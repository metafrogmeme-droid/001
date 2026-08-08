'use strict';
/**
 * Daily Duel — /api/duel. Three rounds a UTC day, drawn from the agent's own
 * live signals and topped up from the majors. The player calls LONG / SHORT /
 * PASS before the round locks; the agent's own call stays hidden until the
 * player has committed; the market settles it at the horizon.
 *
 * Zero setup: any registered user can play immediately. No exchange keys, no
 * bot gateway, no paper balance to fund — which is the point, since the Arena's
 * on-ramp still asks for a trading decision with size attached and this asks
 * only for an opinion.
 *
 * Rules are lib/duel.js (pure, tested). Prices come from lib/tickers at
 * creation and lib/candles at settlement. Nothing derived is stored: marks,
 * accuracy, streaks and quests recompute on every read.
 *
 * §4: virtual throughout — there is no stake, no wager and no amount of any
 * currency anywhere in this surface. Only counts, percent and market prices.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { pool } = require('../db');
const { getTickers } = require('../lib/tickers');
const { closeAt } = require('../lib/candles');
const { sealDuelPick } = require('../lib/callseal');
const duel = require('../lib/duel');

const router = express.Router();
router.use(authMiddleware);

const pickLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });
/** How far back a player's record is computed. Stated on the payload rather
 *  than silently truncating: a capped window reported as "your record" would
 *  be a partial total printed as a whole one. */
const RECORD_WINDOW_DAYS = 90;
/** Bound on how many due rounds one request will try to settle, so a long
 *  outage cannot turn the next page load into an unbounded fan-out. */
const SETTLE_BATCH = 6;

const ROUND_COLS = 'id, day, idx, symbol, agent_direction, signal_key';
const PICK_COLS = 'id, round_id, pick, entry_price, resolves_at, settle_price, '
  + 'settle_state, seal, created_at';

// Same sanitiser philosophy as routes/arena.js: say WHY a read failed without
// letting a driver message, URI or credential reach the client.
function safeReason(err) {
  if (!err) return null;
  let m = String(err.code ? `${err.code}: ${err.message}` : err.message || err).slice(0, 200);
  m = m.replace(/\b[a-z+]+:\/\/[^\s'")]+/gi, '<uri>');
  m = m.replace(/(?:\/[\w.-]+){3,}/g, '<path>');
  m = m.replace(/\b[0-9a-f]{32,}\b/gi, '<hex>');
  m = m.replace(/password[=:]\S+/gi, '<redacted>');
  return m || null;
}

function dayFloor(now, days) {
  return new Date(new Date(now).getTime() - days * 86400000).toISOString().slice(0, 10);
}

async function loadRoundsForDay(day) {
  const [rows] = await pool.execute(
    `SELECT ${ROUND_COLS} FROM duel_rounds WHERE day = ? ORDER BY idx`, [day]);
  return rows || [];
}

async function loadRoundsSince(day) {
  const [rows] = await pool.execute(
    `SELECT ${ROUND_COLS} FROM duel_rounds WHERE day >= ? ORDER BY day, idx`, [day]);
  return rows || [];
}

async function loadPicks(userId) {
  const [rows] = await pool.execute(
    `SELECT ${PICK_COLS} FROM duel_picks WHERE user_id = ?`, [userId]);
  return rows || [];
}

/**
 * Today's rounds, created on first read of the day.
 *
 * The unique key on (day, idx) is what makes this race-safe without a lock:
 * concurrent first-readers all INSERT IGNORE and then read back the same three
 * rows. Whoever loses the race simply inserts nothing.
 *
 * If the card cannot be built because prices are unreadable, this THROWS rather
 * than returning an empty list — an empty card would render as "no rounds
 * today", which is a confident statement about the game, made from a failure to
 * read the market.
 */
async function ensureRounds(day, now) {
  const existing = await loadRoundsForDay(day);
  if (existing.length >= duel.ROUNDS_PER_DAY) return existing;

  // Newest signals, unfiltered in SQL and narrowed to the day here: the
  // in-memory shim ignores WHERE on this table, so filtering in JS is what
  // keeps test and production reading the same rows.
  let signals = [];
  try {
    const [rows] = await pool.execute(
      'SELECT signal_key, symbol, direction, confidence, created_at FROM signals'
      + ' ORDER BY created_at DESC LIMIT 60');
    signals = (rows || []).filter(
      (s) => s && s.created_at && String(new Date(s.created_at).toISOString()).slice(0, 10) === day);
  } catch (e) {
    // The agent's own calls are a bonus, not a prerequisite: a signals outage
    // degrades the card to majors rather than cancelling the day.
    signals = [];
  }

  const tickers = await getTickers();
  const built = duel.buildRounds(day, signals, tickers);
  if (!built.length) {
    if (existing.length) return existing;      // keep a partial card over none
    const err = new Error('no market prices available to open the day');
    err.rcReason = 'prices_unreadable';
    throw err;
  }

  for (const r of built) {
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, symbol, agent_direction, signal_key)'
      + ' VALUES (?, ?, ?, ?, ?)',
      [r.day, r.idx, r.symbol, r.agent_direction, r.signal_key]);
  }
  return loadRoundsForDay(day);
}

/**
 * Settle whatever is due among the caller's own calls.
 *
 * Because the settle price is the close of the candle CONTAINING the call's
 * horizon, running this late produces the same number as running it on time —
 * which is why it can be driven by page loads instead of a scheduler.
 *
 * An unreadable candle writes NOTHING and leaves the call pending; a later read
 * tries again. Past the give-up horizon it is marked terminally 'unresolved' so
 * it stops being pending forever, and is excluded from every denominator rather
 * than counted as anyone's loss.
 */
async function settleDue(rounds, picks, now = new Date()) {
  const symbolOf = new Map((rounds || []).map((r) => [String(r.id), r.symbol]));
  const due = (picks || []).filter(
    (p) => p && p.settle_price == null && p.settle_state == null && duel.isDue(p, now));
  let settled = 0;
  for (const p of due.slice(0, SETTLE_BATCH)) {
    const symbol = symbolOf.get(String(p.round_id));
    const at = Date.parse(p.resolves_at);
    const close = (symbol && Number.isFinite(at)) ? await closeAt(symbol, at) : null;
    if (close != null) {
      await pool.execute(
        'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
        [close, 'settled', new Date(now).toISOString(), p.id]);
      p.settle_price = close;
      p.settle_state = 'settled';
      settled++;
    } else if (duel.isAbandoned(p, now)) {
      await pool.execute(
        'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
        [null, 'unresolved', new Date(now).toISOString(), p.id]);
      p.settle_state = 'unresolved';
    }
  }
  return settled;
}

async function handleFor(userId) {
  try {
    const [rows] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE id = ?', [userId]);
    return (rows[0] && rows[0].leaderboard_handle) || null;
  } catch (e) { return null; }
}

/** The player's record over the stated window. */
function recordFor(rounds, picks, now) {
  const entries = duel.scoreEntries(rounds, picks);
  return {
    window_days: RECORD_WINDOW_DAYS,
    accuracy: duel.accuracy(entries),
    marks: duel.computeMarks(entries),
    streak: duel.duelStreak(picks, now),
    quests: duel.weeklyDuelQuests(entries, now),
    entries,
  };
}

// GET /today — the day's card plus whatever the caller has already called.
router.get('/today', async (req, res) => {
  try {
    const now = new Date();
    const day = duel.dayKey(now);
    const rounds = await ensureRounds(day, now);
    const picks = await loadPicks(req.user.user_id);
    await settleDue(rounds, picks, now);

    const mine = new Map(picks.map((p) => [String(p.round_id), p]));

    const cards = rounds.map((r) => {
      const pick = mine.get(String(r.id));
      // The stance is owed only once the caller has committed. Before that it
      // is omitted entirely — see publicRound.
      const view = duel.publicRound(r, { revealAgent: Boolean(pick), now });
      if (pick) {
        view.my_call = duel.publicPick(pick, { now });
        view.my_result = duel.scorePick(pick.pick, duel.pickOutcome(pick), r.agent_direction);
      }
      return view;
    });

    res.json({
      day,
      horizon_hours: duel.HORIZON_HOURS,
      rounds: cards,
      open: cards.filter((c) => c.callable && !c.my_call).length,
      counts_only: true,
    });
  } catch (err) {
    console.error('Duel today error:', err.stack || err.message);
    res.status(503).json({
      error: 'The day\'s rounds are unavailable',
      reason: err.rcReason || null,
      detail: safeReason(err),
    });
  }
});

// POST /pick { round_id, pick } — commit a call. Write-once, before the lock.
router.post('/pick', pickLimit, async (req, res) => {
  try {
    const body = req.body || {};
    const pick = duel.normPick(body.pick);
    const roundId = Number(body.round_id);
    if (pick === null) {
      return res.status(400).json({ error: 'Call LONG, SHORT or PASS.' });
    }
    if (!Number.isInteger(roundId) || roundId <= 0) {
      return res.status(400).json({ error: 'Which round?' });
    }

    const now = new Date();
    const rounds = await loadRoundsForDay(duel.dayKey(now));
    const round = rounds.find((r) => Number(r.id) === roundId);
    if (!round) return res.status(404).json({ error: 'That round is not on today\'s card.' });
    if (!duel.isCallable(round, now)) {
      return res.status(409).json({ error: 'That round has closed — its day is over.' });
    }

    // The entry is snapped NOW, for this player. A stale mark would score the
    // call from a price it was never offered at, so an unreadable ticker
    // refuses the call outright rather than guessing one.
    const tickers = await getTickers();
    const mark = tickers && tickers[round.symbol];
    const entry = mark == null ? NaN : Number(mark.price);
    if (!Number.isFinite(entry) || entry <= 0) {
      return res.status(503).json({
        error: `Cannot read a ${round.symbol} price right now — your call was not recorded.`,
        reason: 'prices_unreadable',
      });
    }

    const handle = await handleFor(req.user.user_id);
    const picked_at = now.toISOString();
    const resolves_at = duel.horizonFor(now);
    const receipt = sealDuelPick({
      round_id: round.id, day: round.day, symbol: round.symbol,
      handle, pick, entry_price: entry, resolves_at, picked_at,
    });

    try {
      await pool.execute(
        'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
        + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [req.user.user_id, round.id, pick, entry, resolves_at,
          receipt.seal, receipt.seal_payload, picked_at]);
    } catch (e) {
      // The unique key IS the anti-cheat, not a convenience: a call cannot be
      // revised once made, so the second write has nowhere to land.
      if (e && (e.code === 'ER_DUP_ENTRY' || e.errno === 1062)) {
        return res.status(409).json({ error: 'You have already called this round.' });
      }
      throw e;
    }

    // Committed — so the agent's stance can now be shown.
    res.json({
      ok: true,
      round_id: round.id,
      symbol: round.symbol,
      pick,
      entry_price: entry,
      resolves_at,
      seal: receipt.seal,
      agent_direction: duel.normDirection(round.agent_direction),
    });
  } catch (err) {
    console.error('Duel pick error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not record your call', detail: safeReason(err) });
  }
});

// GET /me — the caller's record. Counts and percent; no amount of anything.
router.get('/me', async (req, res) => {
  try {
    const now = new Date();
    const floor = dayFloor(now, RECORD_WINDOW_DAYS);
    const [rounds, picks] = await Promise.all([
      loadRoundsSince(floor), loadPicks(req.user.user_id),
    ]);
    await settleDue(rounds, picks, now);

    const rec = recordFor(rounds, picks, now);
    const handle = await handleFor(req.user.user_id);
    res.json({
      handle,
      window_days: rec.window_days,
      accuracy: rec.accuracy,
      marks: rec.marks,
      streak: rec.streak,
      quests: rec.quests,
      history: rec.entries
        .slice()
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        .slice(0, 60),
      counts_only: true,
      private: true,
    });
  } catch (err) {
    console.error('Duel record error:', err.stack || err.message);
    res.status(500).json({ error: 'Your record is unavailable', detail: safeReason(err) });
  }
});

// GET /season — the caller's standing this calendar month (UTC).
router.get('/season', async (req, res) => {
  try {
    const now = new Date();
    const season = now.toISOString().slice(0, 7);
    const [rounds, allPicks] = await Promise.all([
      loadRoundsSince(season + '-01'), loadPicks(req.user.user_id),
    ]);
    const entries = duel.scoreEntries(rounds, allPicks)
      .filter((e) => String(e.day).slice(0, 7) === season);
    res.json({
      season,
      accuracy: duel.accuracy(entries),
      marks: duel.computeMarks(entries),
      counts_only: true,
    });
  } catch (err) {
    console.error('Duel season error:', err.stack || err.message);
    res.status(500).json({ error: 'The season standing is unavailable', detail: safeReason(err) });
  }
});

module.exports = router;
module.exports.ensureRounds = ensureRounds;
module.exports.settleDue = settleDue;
module.exports.recordFor = recordFor;
module.exports.RECORD_WINDOW_DAYS = RECORD_WINDOW_DAYS;
