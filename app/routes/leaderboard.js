/**
 * Leaderboard — opt-in, anonymous handles, ranked by return %.
 *
 * Privacy: appearing is OFF by default. A user opts in by choosing a display
 * handle (never their email). The board shows the handle, a return %, a trade
 * count, and win rate — NEVER a dollar amount, so account size never leaks.
 * Return % is computed on the standard paper stake so ranks are comparable
 * across accounts and reveal no balance. It reads only realized PnL and does
 * not touch the money path or the live-eligibility gate.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { aggregateStats } = require('../public/js/trade-stats');
const { pool } = require('../db');

const router = express.Router();
router.use(authMiddleware);

const PAPER_BASE = 10000;                 // the standard paper starting stake
const HANDLE_RE = /^[A-Za-z0-9_]{3,20}$/;
const MAX_ROWS = 50;
const optLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

// Per-user realized stats from CLOSED trades — the same query and the same
// reader (aggregateStats) as routes/portfolio.js and routes/sync.js, so a
// user's rank here and their own dashboard cannot disagree.
//
// This function held the two banned shapes from CLAUDE.md's table, and being a
// RANKING made both worse than they are on a private panel: `trades.pnl` is
// nullable, so `COALESCE(SUM(pnl), 0)` + `parseFloat(...) || 0` scored an
// unpriceable book as a measured 0.00% return, `wins / COUNT(*)` put every
// unpriced close in the denominator alone, and `s.trades > 0` then admitted
// that member to a PUBLIC board — ranked, by handle, at a flat 0.00% with a
// depressed win rate. A record of failure published for rows nobody could
// price, and it reorders everyone else against it.
async function userStats(uid) {
  const [agg] = await pool.execute(
    "SELECT COUNT(*) AS total, " +
    "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS scored, " +
    "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, " +
    "SUM(pnl) AS net_pnl " +
    "FROM trades WHERE user_id = ? AND status = ?",
    [uid, 'CLOSED']);
  const c = aggregateStats(agg[0]);
  return {
    // null, never 0: a return of 0.00% is a real flat book, and an unpriceable
    // one is not a flat book. The caller ranks on `scored`, not on `total`.
    return_pct: c.net_pnl === null
      ? null : Math.round((c.net_pnl / PAPER_BASE) * 10000) / 100,
    trades: c.total,
    scored: c.scored,
    unpriced: c.unpriced,
    win_rate: c.win_rate === null ? null : Math.round(c.win_rate * 10) / 10,
  };
}

// GET /  → ranked board (opted-in members with >=1 closed trade) + caller state.
router.get('/', async (req, res) => {
  try {
    const [members] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const scored = [];
    for (const m of members) {
      const s = await userStats(m.id);
      // Ranked on what could actually be PRICED. `s.trades > 0` admitted a
      // member whose every close was unpriceable and printed them at 0.00%;
      // having closed trades and having a scorable record are different facts.
      if (s.scored > 0) scored.push({ id: m.id, handle: m.leaderboard_handle, ...s });
    }
    scored.sort((a, b) => b.return_pct - a.return_pct);
    const rows = scored.slice(0, MAX_ROWS).map((r, i) => ({
      rank: i + 1, handle: r.handle, return_pct: r.return_pct,
      trades: r.trades, win_rate: r.win_rate,
      // Counts, not amounts — §4-safe, and they keep a rate computed over 4 of
      // 11 closes from reading like one computed over all 11.
      scored: r.scored, unpriced: r.unpriced,
      is_me: r.id === req.user.user_id,
    }));
    const [me] = await pool.execute('SELECT leaderboard_handle FROM users WHERE id = ?', [req.user.user_id]);
    const handle = (me[0] && me[0].leaderboard_handle) || null;
    // UX-6: the caller's REAL rank — even when they're outside the top MAX_ROWS
    // window (the board itself is capped, so >50th place used to be invisible
    // and unmotivating). Rank + total are position-only, no dollar figures.
    const myIdx = scored.findIndex((r) => r.id === req.user.user_id);
    const my_rank = myIdx >= 0 ? myIdx + 1 : null;
    res.json({ rows, opted_in: !!handle, handle, my_rank, ranked_total: scored.length });
  } catch (err) {
    console.error('Leaderboard error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to load leaderboard' });
  }
});

// POST /opt-in { handle } — join (or rename) with an anonymous handle.
router.post('/opt-in', optLimit, async (req, res) => {
  try {
    const handle = String((req.body || {}).handle || '').trim();
    if (!HANDLE_RE.test(handle)) {
      return res.status(400).json({ error: 'Handle must be 3–20 letters, numbers, or underscores.' });
    }
    // Case-insensitive uniqueness, ignoring the caller's own current handle.
    const [taken] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
    if (taken.length && taken[0].id !== req.user.user_id) {
      return res.status(409).json({ error: 'That handle is taken — try another.' });
    }
    await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', [handle, req.user.user_id]);
    res.json({ ok: true, handle });
  } catch (err) {
    console.error('Leaderboard opt-in error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not join the leaderboard' });
  }
});

// POST /opt-out — leave the board (clears the handle).
router.post('/opt-out', optLimit, async (req, res) => {
  try {
    await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', [null, req.user.user_id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Leaderboard opt-out error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not leave the leaderboard' });
  }
});

module.exports = router;
