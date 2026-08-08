'use strict';
/**
 * Daily Duel — the PUBLIC board, at /api/public/duel. No session required.
 *
 * Two views: players ranked by how often they call the market correctly, and
 * squads — a player plus everyone who signed up on their referral code —
 * ranked the same way.
 *
 * Privacy is the same model as the paper leaderboard: appearing is opt-in and
 * off by default, and the only identity shown is the anonymous handle the
 * player chose. No email, no user id, and no amount of any currency: §4 holds
 * here by construction, since a duel has no stake to report in the first place.
 *
 * Honesty: a player or squad whose record is too short to read is COUNTED and
 * reported as warming up, never ranked at 0% and never silently dropped — a
 * board that omitted them would misreport how many people are playing.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { pool } = require('../db');
const duel = require('../lib/duel');
const squads = require('../lib/duel_squads');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 30000;
const ROUND_COLS = 'id, day, idx, symbol, agent_direction, signal_key';
const PICK_COLS = 'id, user_id, round_id, pick, entry_price, resolves_at, '
  + 'settle_price, settle_state, created_at';

let cache = { at: 0, key: null, data: null };

/** The month a board covers. Anything else is refused rather than silently
 *  serving the current one under a label the caller did not ask for. */
const SEASON_RE = /^\d{4}-\d{2}$/;

function seasonOf(q) {
  const raw = String(q == null ? '' : q).trim();
  if (!raw) return new Date().toISOString().slice(0, 7);
  return SEASON_RE.test(raw) ? raw : null;
}

/**
 * Everything the board needs for one season, scored per player.
 * Returns { standings: Map(user_id -> standing), members: [...] }.
 */
async function loadSeason(season) {
  const start = season + '-01';
  const [rounds] = await pool.execute(
    `SELECT ${ROUND_COLS} FROM duel_rounds WHERE day >= ? ORDER BY day, idx`, [start]);
  const [picks] = await pool.execute(
    `SELECT ${PICK_COLS} FROM duel_picks WHERE created_at >= ?`,
    [start + 'T00:00:00.000Z']);
  const [users] = await pool.execute(
    'SELECT id, leaderboard_handle, referred_by FROM users WHERE leaderboard_handle IS NOT NULL');

  const inSeason = (rounds || []).filter((r) => String(r.day).slice(0, 7) === season);
  const byUser = new Map();
  for (const p of picks || []) {
    const k = String(p.user_id);
    if (!byUser.has(k)) byUser.set(k, []);
    byUser.get(k).push(p);
  }

  const members = (users || []).map((u) => ({
    id: u.id, handle: u.leaderboard_handle, referred_by: u.referred_by,
  }));
  const standings = new Map();
  for (const m of members) {
    const entries = duel.scoreEntries(inSeason, byUser.get(String(m.id)) || []);
    standings.set(String(m.id), squads.playerStanding(m.handle, entries));
  }
  return { standings, members };
}

async function seasonData(season) {
  const now = Date.now();
  if (cache.data && cache.key === season && now - cache.at < CACHE_MS) return cache.data;
  const data = await loadSeason(season);
  cache = { at: now, key: season, data };
  return data;
}

// GET /board?season=YYYY-MM — opted-in players, ranked.
router.get('/board', async (req, res) => {
  const season = seasonOf(req.query.season);
  if (season === null) return res.status(400).json({ error: 'Season must look like 2026-08.' });
  try {
    const { standings } = await seasonData(season);
    const board = squads.buildBoard([...standings.values()]);
    res.json({ season, ...board, counts_only: true });
  } catch (err) {
    console.error('Duel board error:', err.stack || err.message);
    res.status(503).json({ error: 'The duel board is unavailable' });
  }
});

// GET /squads?season=YYYY-MM — squads built from the referral graph.
router.get('/squads', async (req, res) => {
  const season = seasonOf(req.query.season);
  if (season === null) return res.status(400).json({ error: 'Season must look like 2026-08.' });
  try {
    const { standings, members } = await seasonData(season);
    const table = squads.buildSquads(members, (id) => standings.get(String(id)));
    res.json({ season, ...table, counts_only: true });
  } catch (err) {
    console.error('Duel squads error:', err.stack || err.message);
    res.status(503).json({ error: 'Squad standings are unavailable' });
  }
});

module.exports = router;
module.exports._resetCache = () => { cache = { at: 0, key: null, data: null }; };
