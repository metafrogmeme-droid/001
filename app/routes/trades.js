const express = require('express');
const { profitFactor, sharpe: calcSharpe, winStats, realizedTotal } =
  require('../public/js/trade-stats');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { computePerformance } = require('../lib/trade_performance');
const { venueBreakdown } = require('../lib/venue_breakdown');
const { segmentByCapitalEvents, deriveStartEquity } = require('../lib/equity_basis');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();

// All routes require auth
router.use(authMiddleware);

const notesLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

// GET /api/trades/stats - Portfolio statistics
router.get('/stats', async (req, res) => {
  try {
    const uid = req.user.user_id;

    // One row set, one reader. The two aggregate queries this replaces had
    // `trades.pnl` — which is NULLABLE — wrong in three ways at once:
    // `COALESCE(SUM(pnl), 0)` printed an unpriceable book as a measured
    // $0.00, `wins / COUNT(*)` put unpriced closes in the denominator only,
    // and `losses: totalTrades - wins` filed every one of them as a defeat.
    // That last line is verbatim off CLAUDE.md's table of banned shapes.
    const [allPnl] = await pool.execute(
      'SELECT pnl, size_usd, fees, venue FROM trades WHERE user_id = ? AND status = ? ORDER BY closed_at',
      [uid, 'CLOSED']
    );

    const [openRows] = await pool.execute(
      'SELECT COUNT(*) as open_count FROM trades WHERE user_id = ? AND status = ?',
      [uid, 'OPEN']
    );

    const ws = winStats(allPnl);
    const netPnl = realizedTotal(allPnl);
    const totalFees = allPnl.reduce((a, r) => {
      const f = parseFloat(r.fees);
      return a + (Number.isFinite(f) ? f : 0);
    }, 0);
    const totalTrades = ws.total;
    const wins = ws.wins;
    const winRate = ws.rate === null ? null : ws.rate * 100;

    // Use latest synced equity snapshot if available
    const [snapRows] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    // No snapshot yet -> equity is genuinely unknown; return null so the UI
    // renders a "no data yet" state instead of an invented starting balance.
    const equity = snapRows.length > 0 ? parseFloat(snapRows[0].equity) : null;

    const sharpe = calcSharpe(allPnl);
    const pf = profitFactor(allPnl);

    res.json({
      // PER-VENUE, and PRIVATE. This route is the user's own dashboard, so
      // dollars are fine here — the public track record and every community
      // payload stay percent/ratio/count, which is why this is added here and
      // not in track.js.
      //
      // Venues that never traded are ABSENT rather than zeroed: a row of
      // zeroes beside a venue that really did trade invites reading one as the
      // other. See app/lib/venue_breakdown.js.
      by_venue: venueBreakdown(allPnl),
      equity: equity != null ? Math.round(equity * 100) / 100 : null,
      net_pnl: netPnl === null ? null : Math.round(netPnl * 100) / 100,
      total_fees: Math.round(totalFees * 100) / 100,
      total_trades: totalTrades,
      open_positions: parseInt(openRows[0].open_count),
      win_rate: winRate === null ? null : Math.round(winRate * 10) / 10,
      sharpe: sharpe !== null ? Math.round(sharpe * 100) / 100 : null,
      profit_factor: pf !== null ? Math.round(pf * 100) / 100 : null,
      wins,
      // COUNTED, never derived by subtraction. A break-even close is neither
      // a win nor a loss, and a close with no recorded P&L is neither either.
      losses: ws.losses,
      breakeven: ws.breakeven,
      unpriced: ws.unscored,
    });
  } catch (err) {
    console.error('Stats error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to compute stats' });
  }
});

// GET /api/trades/history - Closed trades
router.get('/history', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const offset = parseInt(req.query.offset) || 0;

    const [rows] = await pool.execute(
      `SELECT id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at, notes
       FROM trades WHERE user_id = ? AND status = 'CLOSED'
       ORDER BY closed_at DESC LIMIT ${limit} OFFSET ${offset}`,
      [uid]
    );

    const [countRows] = await pool.execute(
      "SELECT COUNT(*) as total FROM trades WHERE user_id = ? AND status = 'CLOSED'",
      [uid]
    );

    res.json({ trades: rows, total: parseInt(countRows[0].total) });
  } catch (err) {
    console.error('History error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch history' });
  }
});

// GET /api/trades/open - Open positions
router.get('/open', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT id, symbol, direction, entry_price, size_usd, fees, pattern, stop_loss, take_profit, opened_at
       FROM trades WHERE user_id = ? AND status = 'OPEN'
       ORDER BY opened_at DESC`,
      [uid]
    );
    res.json({ positions: rows });
  } catch (err) {
    console.error('Open error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch positions' });
  }
});

// PATCH /api/trades/:id/notes - attach a journal note to one of the user's
// OWN closed trades. Turns the trade history from a ledger into something
// worth reviewing (why a setup worked or didn't).
router.patch('/:id/notes', notesLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const id = parseInt(req.params.id);
    if (!Number.isInteger(id)) return res.status(400).json({ error: 'Invalid trade id' });
    const notes = typeof req.body.notes === 'string' ? req.body.notes.slice(0, 2000) : '';
    const [result] = await pool.execute(
      'UPDATE trades SET notes = ? WHERE id = ? AND user_id = ?',
      [notes, id, uid]
    );
    if (!result.affectedRows) return res.status(404).json({ error: 'Trade not found' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Trade notes error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to save note' });
  }
});

// GET /api/trades/activity - Real position open/close activity feed.
// Built only from this user's own trades table -- no synthetic "Telegram
// connected"/"risk settings changed" entries, since those aren't persisted
// with history once the bot acknowledges and applies them (pending_* rows
// are deleted, not archived).
router.get('/activity', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const limit = Math.min(parseInt(req.query.limit) || 30, 100);
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, status, opened_at, closed_at
       FROM trades WHERE user_id = ?
       ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ${limit * 2}`,
      [uid]
    );
    const events = [];
    for (const t of rows) {
      if (t.opened_at) events.push({ type: 'open', symbol: t.symbol, direction: t.direction, size_usd: t.size_usd, timestamp: t.opened_at });
      if (t.status === 'CLOSED' && t.closed_at) events.push({ type: 'close', symbol: t.symbol, direction: t.direction, pnl: t.pnl, timestamp: t.closed_at });
    }
    events.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    res.json({ events: events.slice(0, limit) });
  } catch (err) {
    console.error('Activity error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch activity' });
  }
});

// GET /api/trades/breakdown - realised PnL by symbol + max drawdown + expectancy.
// Computed in-process over the user's closed trades (bounded window) so it
// behaves the same on MySQL and the in-memory mock.
router.get('/breakdown', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, closed_at
       FROM trades WHERE user_id = ? AND status = 'CLOSED'
       ORDER BY closed_at DESC LIMIT 2000`,
      [uid]
    );
    // Seed drawdown % against the latest equity snapshot if we have one.
    const [snap] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    // See deriveStartEquity: the basis was summed over ALL rows while
    // computePerformance scores only the priced ones.
    const basis = deriveStartEquity(rows, snap.length > 0 ? snap[0].equity : null);
    res.json({
      ...computePerformance(rows, { startEquity: basis.start_equity }),
      basis,
    });
  } catch (err) {
    console.error('Breakdown error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to compute breakdown' });
  }
});

// GET /api/trades/equity-curve - Equity snapshots (current capital basis).
// The raw series can contain capital events — a deposit, withdrawal, or the
// paper→live switch — that would draw as a trading cliff. Serve only the
// CURRENT consistent-capital segment, plus how many events were skipped, so
// the chart shows trading performance, never funding changes.
router.get('/equity-curve', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at ASC LIMIT 365',
      [uid]
    );
    const [closed] = await pool.execute(
      "SELECT pnl, closed_at FROM trades WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL ORDER BY closed_at ASC",
      [uid]
    );
    const curve = rows
      .map(r => ({ t: new Date(r.snapshot_at).getTime(), equity: parseFloat(r.equity), snapshot_at: r.snapshot_at }))
      .filter(p => isFinite(p.equity) && p.equity > 0);
    const segments = segmentByCapitalEvents(curve, closed);
    const current = segments.length ? segments[segments.length - 1] : [];
    res.json({
      snapshots: current.map(p => ({ equity: p.equity, snapshot_at: p.snapshot_at })),
      capital_events: Math.max(0, segments.length - 1),
    });
  } catch (err) {
    console.error('Equity curve error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch equity curve' });
  }
});

module.exports = router;
