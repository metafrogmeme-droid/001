/**
 * Leaderboard — opt-in, anonymous handles, ranked by return %.
 *
 * Privacy: appearing is OFF by default. A user opts in by choosing a display
 * handle (never their email). The board shows the handle, a return %, a trade
 * count, and win rate — NEVER a dollar amount, so account size never leaks.
 * It reads only realized PnL and does not touch the money path or the
 * live-eligibility gate.
 *
 * RETURN % IS ON EACH ACCOUNT'S OWN BASIS, OR IT IS NOT PUBLISHED. It used to
 * be `net_pnl / 10000` for everybody -- "the standard paper stake" -- which
 * made `return_pct * 100` the member's realized P&L in whole dollars: a
 * member with closes of +412.37, -95.12 and +23.40 was published at 3.41%,
 * and 3.41 x 100 is the 340.65 the panel promised it never shows. A ratio to
 * a constant is a dollar figure with the units moved. The basis now is the
 * account's own starting equity, derived the way the reputation route derives
 * it (`deriveStartEquity`: the latest equity snapshot minus the realized net),
 * and a member with no snapshot, or whose snapshot does not leave a positive
 * basis, is listed by trades and win rate with `return_pct: null` and no rank.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { aggregateStats } = require('../public/js/trade-stats');
const { deriveStartEquity } = require('../lib/equity_basis');
const { pool } = require('../db');

const router = express.Router();
router.use(authMiddleware);

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
  const [snap] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [uid]);
  return {
    // null, never 0: a return of 0.00% is a real flat book, and an unpriceable
    // one is not a flat book. The caller ranks on `scored`, not on `total`.
    return_pct: ownBasisReturn(c.net_pnl, snap.length > 0 ? snap[0].equity : null),
    trades: c.total,
    scored: c.scored,
    unpriced: c.unpriced,
    win_rate: c.win_rate === null ? null : Math.round(c.win_rate * 10) / 10,
  };
}

/**
 * The realized net as a percent of the account's OWN starting equity, or null.
 *
 * `deriveStartEquity` sums the priced rows, and the aggregate's `net_pnl` IS
 * that sum, so one row carrying it is the same input. Two of its answers are
 * not a basis for a published ratio: the `default` source (no snapshot, so the
 * 10,000 it returns is the constant this function exists to stop dividing by)
 * and a basis at its floor of 1 (the snapshot sits at or below the realized
 * net -- a withdrawal, say -- and `max(..., 1)` is a constant too: dividing by
 * it would publish the net P&L x 100).
 */
function ownBasisReturn(net, snapshotEquity) {
  if (net === null) return null;
  const b = deriveStartEquity([{ pnl: net }], snapshotEquity);
  if (b.basis_source !== 'equity_snapshot' || !(b.start_equity > 1)) return null;
  return Math.round((net / b.start_equity) * 10000) / 100;
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
    // Ranked by return only where a return exists. `b - a` over a null reads
    // null as 0 and would rank a member nobody could measure as a flat book;
    // those members are LISTED after the ranked ones, with no rank, by their
    // count and win rate -- which are real readings of their record.
    const ranked = scored.filter((r) => r.return_pct !== null)
      .sort((a, b) => b.return_pct - a.return_pct);
    const unranked = scored.filter((r) => r.return_pct === null)
      .sort((a, b) => (b.scored - a.scored) || String(a.handle).localeCompare(String(b.handle)));
    const listed = [...ranked, ...unranked];
    const rows = listed.slice(0, MAX_ROWS).map((r, i) => ({
      rank: i < ranked.length ? i + 1 : null, handle: r.handle, return_pct: r.return_pct,
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
    const myIdx = ranked.findIndex((r) => r.id === req.user.user_id);
    const my_rank = myIdx >= 0 ? myIdx + 1 : null;
    // Listed but not ranked is a third state, and the caller is told which:
    // "close a trade to get ranked" is false for a member who has closed
    // trades and has no equity reading to measure a return against.
    const my_unranked = unranked.some((r) => r.id === req.user.user_id);
    res.json({ rows, opted_in: !!handle, handle, my_rank, my_unranked,
               ranked_total: ranked.length, unranked_total: unranked.length });
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
