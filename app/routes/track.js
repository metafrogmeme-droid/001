/**
 * Public track record — the operator engine's verifiable performance.
 *
 * GET /api/public/track-record (no auth). Everything is aggregated from data
 * the bot already syncs: closed trades + equity snapshots of the operator
 * account (user 1) and the scan cache's live_mode flag. Nothing here is
 * hand-entered — if the bot didn't trade it, it isn't on the page. Numbers
 * the data can't support (no snapshots yet, no closes yet) are returned as
 * null and the page says "no data", never a made-up figure.
 *
 * Cached in-memory for 5 minutes: the page is public and this keeps a
 * traffic spike from hammering MySQL.
 */

const express = require('express');
const { pool } = require('../db');

const router = express.Router();

const OPERATOR_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;
const CACHE_MS = 5 * 60 * 1000;
let cache = null;          // { at: ms, payload }

function round2(v) { return Math.round(v * 100) / 100; }

// Capital-basis-aware drawdown/segmentation — shared with the per-user
// portfolio equity curve (routes/trades.js). See lib/equity_basis.js for
// the capital-event rationale (the "98.7% drawdown" bug class).
const { maxDrawdownPct, segmentByCapitalEvents, segmentedMaxDrawdownPct } =
  require('../lib/equity_basis');

// Downsample to at most n points, always keeping the first and last.
function downsample(rows, n) {
  if (rows.length <= n) return rows;
  const step = (rows.length - 1) / (n - 1);
  const out = [];
  for (let i = 0; i < n; i++) out.push(rows[Math.round(i * step)]);
  return out;
}

router.get('/track-record', async (req, res) => {
  try {
    if (cache && Date.now() - cache.at < CACHE_MS) {
      res.setHeader('Cache-Control', 'public, max-age=120');
      return res.json(cache.payload);
    }

    const [trades] = await pool.execute(
      `SELECT symbol, direction, pnl, fees, opened_at, closed_at
         FROM trades
        WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
    const [snaps] = await pool.execute(
      `SELECT equity, snapshot_at FROM equity_snapshots
        WHERE user_id = ? ORDER BY snapshot_at ASC`, [OPERATOR_USER_ID]);

    // Current mode from the bot's scan cache — shown as a badge, and honest:
    // absent cache means "unknown", not a guess.
    let mode = null;
    try {
      const [rows] = await pool.execute('SELECT scan_json FROM scan_cache WHERE id = 1');
      if (rows.length && rows[0].scan_json) {
        const cb = (JSON.parse(rows[0].scan_json) || {}).circuit_breaker || {};
        if (typeof cb.live_mode === 'boolean') mode = cb.live_mode ? 'LIVE' : 'PAPER';
      }
    } catch (e) { /* badge stays unknown */ }

    const pnls = trades.map(t => parseFloat(t.pnl) || 0);
    const wins = pnls.filter(p => p > 0);
    const losses = pnls.filter(p => p < 0);
    const grossWin = wins.reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
    const netPnl = round2(pnls.reduce((a, b) => a + b, 0));
    const fees = round2(trades.reduce((a, b) => a + (parseFloat(b.fees) || 0), 0));

    // Monthly net PnL from closed trades (calendar months, UTC).
    const monthly = {};
    for (const t of trades) {
      const d = new Date(t.closed_at);
      const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      // Skip, never add 0: an unreadable row must not drag the month's sign
      // toward flat. The `|| 0` here is the accumulator's seed, reached only
      // once a finite value exists.
      const v = parseFloat(t.pnl);
      if (!Number.isFinite(v)) continue;
      monthly[key] = round2((monthly[key] || 0) + v);
    }

    const curve = snaps.map(s => ({
      t: new Date(s.snapshot_at).getTime(),
      equity: parseFloat(s.equity),
    })).filter(p => isFinite(p.equity) && p.equity > 0);

    // Capital-aware view of the snapshot series: drawdown within consistent
    // segments only, and the displayed curve is the CURRENT capital basis —
    // a deposit or paper→live switch must never render as a trading cliff.
    const segments = segmentByCapitalEvents(curve, trades);
    const currentSegment = segments.length ? segments[segments.length - 1] : [];

    // §4, decided: the PUBLIC track record is percent / ratio / count only —
    // the same privacy model the public letter shipped with. Dollar PnL
    // without a capital basis is spin anyway; percent on the recorded equity
    // basis is checkable. The operator's own dashboard keeps every dollar.
    //
    // Return % is measured over the CURRENT capital segment (same methodology
    // as drawdown): a deposit or paper→live switch never reads as a gain.
    const segStart = currentSegment.length ? currentSegment[0].equity : null;
    const segEnd = currentSegment.length ? currentSegment[currentSegment.length - 1].equity : null;
    const returnPct = segStart > 0 && currentSegment.length >= 2
      ? round2((segEnd / segStart - 1) * 100) : null;

    // Monthly: counts and the sign of the month, never an amount.
    //
    // `trades.pnl` is DECIMAL(14,2) with no NOT NULL, so a closed row can
    // genuinely carry no recorded P&L. Two claims used to come out of that:
    //
    //   `m.trades += 1` counted every row while wins/losses only incremented
    //   on a readable one, so the public record could render "12 (7W/4L)" --
    //   a visitor doing the arithmetic finds a twelfth trade that is neither,
    //   with nothing on the page to explain it; and
    //
    //   `result` is the SIGN of the dollar sum, and an unreadable row adds 0
    //   to that sum, so a month whose trades were ALL unpriced summed to 0
    //   and published itself as "flat" -- break-even, asserted about a month
    //   nobody could price.
    //
    // Both are the same rule this codebase now applies everywhere: absent is
    // not zero. Count it separately, publish the count so the figures
    // reconcile, and refuse the verdict when nothing was scorable.
    const monthlyOut = {};
    for (const t of trades) {
      const d = new Date(t.closed_at);
      const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      const m = monthlyOut[key] || (monthlyOut[key] = {
        trades: 0, wins: 0, losses: 0, unpriced: 0 });
      m.trades += 1;
      const p = parseFloat(t.pnl);
      if (!Number.isFinite(p)) { m.unpriced += 1; continue; }
      if (p > 0) m.wins += 1; else if (p < 0) m.losses += 1;
    }
    for (const key of Object.keys(monthlyOut)) {
      const m = monthlyOut[key];
      const net = monthly[key] || 0;
      // 'unknown' is its own outcome. It is not 'flat': one is a measured
      // break-even and the other is the absence of a measurement.
      m.result = m.unpriced === m.trades ? 'unknown'
        : net > 0 ? 'green' : net < 0 ? 'red' : 'flat';
    }

    // The curve keeps its SHAPE but not the account size: indexed to 100 at
    // the segment start.
    const curveIdx = segStart > 0
      ? downsample(currentSegment, 400).map(p => ({ t: p.t, idx: round2(p.equity / segStart * 100) }))
      : [];

    const payload = {
      generated_at: new Date().toISOString(),
      mode,                                       // 'LIVE' | 'PAPER' | null
      venue: 'Bitget USDT-M perpetuals',
      stats: {
        trades: trades.length,
        wins: wins.length,
        losses: losses.length,
        win_rate_pct: trades.length ? round2(wins.length / trades.length * 100) : null,
        return_pct: returnPct,
        profit_factor: grossLoss > 0 ? round2(grossWin / grossLoss) : null,
        // avg win over avg loss — the payoff shape of the system, as a ratio.
        payoff_ratio: wins.length && losses.length
          ? round2((grossWin / wins.length) / (grossLoss / losses.length)) : null,
        max_drawdown_pct: curve.length >= 2 ? segmentedMaxDrawdownPct(curve, trades) : null,
        first_trade_at: trades.length ? trades[0].closed_at : null,
        last_trade_at: trades.length ? trades[trades.length - 1].closed_at : null,
      },
      monthly: monthlyOut,
      equity_curve_idx: curveIdx,
      capital_events: Math.max(0, segments.length - 1),
      recent_trades: trades.slice(-20).reverse().map(t => {
        const p = parseFloat(t.pnl) || 0;
        return { symbol: t.symbol, direction: t.direction,
          result: p > 0 ? 'win' : p < 0 ? 'loss' : 'flat', closed_at: t.closed_at };
      }),
    };
    cache = { at: Date.now(), payload };
    res.setHeader('Cache-Control', 'public, max-age=120');
    res.json(payload);
  } catch (err) {
    console.error('Track record error:', err.stack || err.message);
    res.status(500).json({ error: 'Track record unavailable' });
  }
});

// ── Trade replay theater (landing page) ──────────────────────────────────────
// GET /api/public/replay-trade — ONE recorded closed trade to animate on the
// landing page. Showcase pick: the largest |PnL| close of the last 14 days
// (win or loss — the theater shows what actually happened), falling back to
// the most recent close. No trades → { trade: null }, and the landing section
// stays hidden — never a fabricated story.
let replayCache = null;
router.get('/replay-trade', async (req, res) => {
  try {
    if (replayCache && Date.now() - replayCache.at < CACHE_MS) {
      res.setHeader('Cache-Control', 'public, max-age=120');
      return res.json(replayCache.payload);
    }
    const [rows] = await pool.execute(
      `SELECT symbol, direction, entry_price, exit_price, size_usd, pnl, fees,
              opened_at, closed_at
         FROM trades
        WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
    const usable = rows.filter(t =>
      isFinite(parseFloat(t.entry_price)) && isFinite(parseFloat(t.exit_price))
      && isFinite(parseFloat(t.pnl)) && t.opened_at);
    let pick = null;
    if (usable.length) {
      const cutoff = Date.now() - 14 * 86_400_000;
      const recent = usable.filter(t => new Date(t.closed_at).getTime() >= cutoff);
      const pool_ = recent.length ? recent : [usable[usable.length - 1]];
      pick = pool_.reduce((a, b) =>
        Math.abs(parseFloat(b.pnl)) > Math.abs(parseFloat(a.pnl)) ? b : a);
    }
    const payload = {
      // §4: prices are public market facts; the sizes and dollar PnL are not.
      // pnl_pct is return-on-size — checkable against entry/exit for anyone
      // who cares to verify the arithmetic.
      trade: pick ? {
        symbol: String(pick.symbol || ''),
        direction: String(pick.direction || '').toUpperCase(),
        entry_price: parseFloat(pick.entry_price),
        exit_price: parseFloat(pick.exit_price),
        pnl_pct: (parseFloat(pick.size_usd) || 0) > 0
          ? round2(parseFloat(pick.pnl) / parseFloat(pick.size_usd) * 100) : null,
        result: (parseFloat(pick.pnl) || 0) >= 0 ? 'win' : 'loss',
        opened_at: pick.opened_at,
        closed_at: pick.closed_at,
      } : null,
    };
    replayCache = { at: Date.now(), payload };
    res.setHeader('Cache-Control', 'public, max-age=120');
    res.json(payload);
  } catch (err) {
    console.error('Replay trade error:', err.stack || err.message);
    res.status(500).json({ error: 'Replay trade unavailable' });
  }
});

module.exports = router;
// Pure helpers, exported for tests.
module.exports.maxDrawdownPct = maxDrawdownPct;
module.exports.segmentByCapitalEvents = segmentByCapitalEvents;
module.exports.segmentedMaxDrawdownPct = segmentedMaxDrawdownPct;
