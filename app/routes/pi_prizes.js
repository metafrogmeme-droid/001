'use strict';
/**
 * Season prizes in Pi (lib/pi_prizes.js): the table a player sees, and the
 * payout plan the operator reads. Off unless the operator turns it on.
 *
 *   GET /api/pi/prizes        signed in. { enabled: false } or the table:
 *                             { enabled: true, places, min_trades, total, season }
 *   GET /api/pi/prizes/plan   the operator only. Who is owed what for the
 *                             current season, read from its closed trades.
 *
 * NOTHING HERE PAYS ANYONE. No route moves Pi and no key is read; the plan is
 * a list for the operator (docs/hackathon/SEASON_PRIZES_PLAN.md).
 *
 * THE TABLE IS SIGNED-IN ONLY. Prize amounts are Pi, not dollars, but the
 * public board stays percent, ratio and count only, and the table is shown
 * where the person is signed in (the Pi app's Leaders tab).
 */
const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { isOperator } = require('../lib/operator_view');
const prizes = require('../lib/pi_prizes');
const seasons = require('../lib/arena_seasons');

const router = express.Router();
const limit = rateLimit({ name: 'pi_prizes', windowMs: 60000, max: 60, key: userKey });

/** Test seam: { env, now }. */
function deps() { return router._deps || {}; }
function env() { return deps().env || process.env; }
function now() { return deps().now ? deps().now() : new Date(); }

async function currentSeason() {
  const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at, rules FROM arena_seasons');
  const t = now();
  const season = seasons.pickCurrentSeason(rows, t);
  if (!season) return null;
  return { season, status: seasons.seasonStatus(season, t) };
}

router.get('/', authMiddleware, limit, async (req, res) => {
  const sched = prizes.schedule(env());
  if (!sched.on) return res.json({ enabled: false });
  try {
    const cur = await currentSeason();
    return res.json({
      enabled: true,
      places: sched.places,
      min_trades: sched.minTrades,
      total: sched.total,
      season: cur ? { name: cur.season.name, ends_at: cur.season.ends_at, status: cur.status } : null,
    });
  } catch (err) {
    // The table is the environment's; only the season could not be read, and
    // a table with no season would be a prize for nothing. Unreadable is not off.
    console.error('Pi prizes season read failed:', err && err.constructor ? err.constructor.name : 'Error');
    return res.status(503).json({ error: 'season_unreadable' });
  }
});

router.get('/plan', authMiddleware, limit, async (req, res) => {
  if (!(await isOperator(req))) return res.status(403).json({ error: 'operator_only' });
  const sched = prizes.schedule(env());
  if (!sched.on) return res.json({ enabled: false, reason: sched.reason });
  try {
    const cur = await currentSeason();
    if (!cur || cur.status === 'upcoming') return res.json({ enabled: true, season: null, places: [] });
    const [trades] = await pool.execute(
      // The season board's own reads (routes/arena.js GET /season), so the
      // plan and the board are read from the same rows.
      'SELECT user_id, pnl, seal FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
      [cur.season.starts_at, cur.season.ends_at]);
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const p = prizes.plan(trades, handleOf, sched);
    return res.json({
      ...p,
      enabled: true,
      season: { name: cur.season.name, starts_at: cur.season.starts_at, ends_at: cur.season.ends_at, status: cur.status },
      // A running season's plan is a preview: the board can still move. Set
      // after the plan's own fields, which know nothing of the season.
      final: p.final && cur.status === 'ended',
      running: cur.status === 'live',
      paid_by: 'operator',
    });
  } catch (err) {
    console.error('Pi prizes plan failed:', err && err.constructor ? err.constructor.name : 'Error');
    return res.status(503).json({ error: 'plan_unreadable' });
  }
});

module.exports = router;
