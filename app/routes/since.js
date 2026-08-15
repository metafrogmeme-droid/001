'use strict';
/**
 * "While you were away" — GET /api/since. The dashboard's welcome-back
 * digest: how long you were gone and what ACTUALLY happened in between —
 * new engine signals, engine events, and your own paper closes. Every number
 * is a real count over the caller's absence window; a first visit returns
 * { first_visit: true } and never a back-filled history (honesty doctrine).
 *
 * Private per-user surface: virtual vUSDT pnl for the caller's own arena
 * closes is allowed here (§4 keeps dollars off PUBLIC surfaces only).
 * Reading the digest advances last_seen_at — but only when every section read
 * cleanly, because advancing is what makes the window unrecoverable.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');

const router = express.Router();

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

router.get('/', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const [rows] = await pool.execute(
      'SELECT id, last_seen_at FROM users WHERE id = ?', [userId]);
    if (!rows[0]) return res.status(404).json({ error: 'User not found' });
    const last = rows[0].last_seen_at ? new Date(rows[0].last_seen_at) : null;
    const now = new Date();
    if (!last || Number.isNaN(last.getTime())) {
      // Nothing to measure yet — stamp the window's start and say so. This
      // advance is unconditional because there are no counts it could hide.
      await pool.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', [now, userId]);
      return res.json({ first_visit: true });
    }
    // Each section starts UNREAD, not at zero. Three catches used to leave the
    // initialized 0 behind and label themselves 'stream quiet → 0' — so a
    // failed query reported "0 new signals" in the same words the engine uses
    // for a genuinely quiet night. This is the composite case from CLAUDE.md's
    // table, so the strategy is OMIT: a dead source leaves itself out and says
    // which one it was, rather than blanking the two that still read.
    const out = {
      away_s: Math.max(0, Math.floor((now.getTime() - last.getTime()) / 1000)),
      since: last.toISOString(),
      signals_new: null,
      events_new: null,
      arena: null,
      unreadable: [],
    };
    try {
      const [sc] = await pool.execute(
        'SELECT COUNT(*) AS n FROM signals WHERE created_at >= ?', [last]);
      out.signals_new = Number(sc[0] && sc[0].n) || 0;
    } catch (e) { out.unreadable.push('signals'); }
    try {
      const [ec] = await pool.execute(
        'SELECT COUNT(*) AS n FROM agent_events WHERE created_at >= ?', [last]);
      out.events_new = Number(ec[0] && ec[0].n) || 0;
    } catch (e) { out.unreadable.push('events'); }
    try {
      const [tr] = await pool.execute(
        'SELECT pnl FROM arena_trades WHERE user_id = ? AND closed_at >= ?',
        [userId, last]);
      // arena_trades.pnl is NOT NULL (db.js) — one of the repo's proven-safe
      // columns — so summing it needs no scored-denominator dance.
      out.arena = {
        closes: tr.length,
        pnl: round2(tr.reduce((a, t) => a + (Number(t.pnl) || 0), 0)),
      };
    } catch (e) { out.unreadable.push('arena'); }
    // Reading the digest CONSUMES the window: last_seen_at used to advance
    // before the counts ran, so a failed read reported zero AND destroyed the
    // only record of what it had failed to read — the next visit measured from
    // now and the missed events were unrecoverable. Advance only on a clean
    // sweep; a partial read leaves the window open so the next visit re-reads
    // it, and a permanently broken section simply keeps showing a longer one.
    if (!out.unreadable.length) {
      await pool.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', [now, userId]);
    }
    res.json(out);
  } catch (err) {
    console.error('Since error:', err.stack || err.message);
    res.status(500).json({ error: 'Digest unavailable' });
  }
});

module.exports = router;
