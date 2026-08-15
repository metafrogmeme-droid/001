/**
 * Signal stream (read).
 * Serves the global signal stream the bot pushes via /api/bot/sync/signals.
 * Public market data (like /api/bot/sync/scan) — no auth required.
 */

const express = require('express');
const { pool } = require('../db');
const { computeAnalytics } = require('../lib/signal_analytics');
const { publicSignal, publicAnalytics } = require('../lib/public_signal');

const router = express.Router();

// GET /api/signals?limit=&status=&symbol=&direction=&min_confidence=
// Recent global signals, newest first. Optional filters.
router.get('/', async (req, res) => {
  try {
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const where = [];
    const params = [];
    if (req.query.status) { where.push('status = ?'); params.push(String(req.query.status).slice(0, 16)); }
    if (req.query.symbol) { where.push('symbol = ?'); params.push(String(req.query.symbol).slice(0, 32)); }
    if (req.query.direction) { where.push('direction = ?'); params.push(String(req.query.direction).slice(0, 8).toUpperCase()); }
    if (req.query.min_confidence) {
      const mc = Number(req.query.min_confidence);
      if (Number.isFinite(mc)) { where.push('confidence >= ?'); params.push(mc); }
    }
    const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';
    const [rows] = await pool.execute(
      `SELECT signal_key, symbol, direction, confidence, score, pattern, regime,
              entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
              created_at, resolved_at, seal
       FROM signals ${clause}
       ORDER BY created_at DESC LIMIT ${limit}`,
      params
    );
    res.json({ signals: rows.map(publicSignal) });
  } catch (err) {
    console.error('Signals fetch error:', err.stack || err.message);
    // "Fail soft — an empty stream is better than a dashboard error" was the
    // comment here, and it is the honesty rule stated backwards: `{signals:[]}`
    // renders as "No signals yet. They stream in as the engine scans the
    // market" — a confident claim that the engine has found nothing,
    // manufactured by a DB outage. /stats one screen down already returns 503
    // for exactly this reason. The dashboard's loader calls mustRead(), so the
    // panel paints an unreadable state the moment we stop lying to it.
    res.status(503).json({ error: 'signal_stream_unavailable' });
  }
});

// GET /api/signals/stats - aggregate signal performance (resolved signals only).
router.get('/stats', async (req, res) => {
  try {
    // `losses: resolved - wins` filed every break-even signal as a defeat:
    // the WHERE already excludes unresolved rows, so the leftover after wins
    // is losses PLUS pnl = 0.00, which is a real outcome and not a loss.
    // Counted here instead, so wins + losses + flat === resolved.
    const [rows] = await pool.execute(
      `SELECT COUNT(*) AS resolved,
              SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses
       FROM signals WHERE pnl IS NOT NULL`
    );
    const r = rows[0] || {};
    const resolved = parseInt(r.resolved || 0);
    const wins = parseInt(r.wins || 0);
    const losses = parseInt(r.losses || 0);
    // No `net_pnl`. This endpoint is unauthenticated (server.js mounts it with
    // no auth), so §4 allows percent, ratio and count here and nothing else —
    // and `SUM(pnl)` is an amount. It was emitted for as long as the column
    // stayed NULL in production, which is the only reason it never leaked.
    // The win rate carries the same information the panel actually shows.
    res.json({
      resolved,
      wins,
      losses,
      flat: Math.max(0, resolved - wins - losses),
      // null over an empty set, never 0. "0% of nothing" and "0% of forty"
      // are different sentences and the dashboard prints them identically.
      win_rate: resolved > 0 ? Math.round((wins / resolved) * 1000) / 10 : null,
    });
  } catch (err) {
    console.error('Signal stats error:', err.stack || err.message);
    // A database failure is not a record of zero signals. This returned
    // HTTP 200 with `{resolved:0, wins:0, losses:0, win_rate:0, net_pnl:0}`,
    // so the panel's `mustRead` passed and it rendered "No resolved signals
    // yet — outcomes appear once signals hit target or stop": a confident
    // claim about the signal record, manufactured by an outage. 503 is the
    // honest answer; the caller paints an error state.
    res.status(503).json({ error: 'signal_stats_unavailable' });
  }
});

// GET /api/signals/analytics - win-rate / net-pnl broken down by pattern,
// symbol, direction and confidence bucket (resolved signals only). Aggregation
// runs in-process over a bounded window so it behaves the same on MySQL and the
// in-memory mock (which ignores WHERE clauses).
router.get('/analytics', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, confidence, pattern, pnl
       FROM signals WHERE pnl IS NOT NULL
       ORDER BY resolved_at DESC LIMIT 2000`
    );
    // Dollar totals stripped at the boundary, not in the aggregator:
    // computeAnalytics keeps computing net_pnl honestly (null over an empty
    // set) and keeps its own tests; this surface is anonymous, so it publishes
    // the ratios and counts only.
    res.json(publicAnalytics(computeAnalytics(rows)));
  } catch (err) {
    console.error('Signal analytics error:', err.stack || err.message);
    // The deleted EMPTY_ANALYTICS was `{resolved:0, wins:0, losses:0,
    // win_rate:0, net_pnl:0}` served as HTTP 200 — an outage rendering as a
    // measured 0% win rate over a measured zero signals. Every group panel
    // downstream read it as data. 503; the caller paints the error.
    res.status(503).json({ error: 'signal_analytics_unavailable' });
  }
});

module.exports = router;
