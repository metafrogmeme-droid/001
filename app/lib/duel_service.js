'use strict';
/**
 * Daily Duel — the stateful half: reading the card, opening the day, recording
 * a call, settling what is due.
 *
 * This exists so the web route and the Telegram channel share ONE
 * implementation of the rules. The alternative — a second copy of "may this
 * call be recorded, and at what price" living in the bot — is exactly the
 * drift this repo spends most of its guard tests preventing, and it would be
 * invisible until the two surfaces disagreed about somebody's record.
 *
 * lib/duel.js decides what things MEAN; this decides what is READ and WRITTEN.
 */

const { pool } = require('../db');
const { getTickers } = require('./tickers');
const { closeAt } = require('./candles');
const { sealDuelPick } = require('./callseal');
const duel = require('./duel');

/** How far back a player's record is computed, stated on every payload that
 *  carries it rather than truncating in silence. */
const RECORD_WINDOW_DAYS = 90;
/** Bound on how many due calls one request will settle, so a long outage
 *  cannot turn the next page load into an unbounded fan-out. */
const SETTLE_BATCH = 6;

const ROUND_COLS = 'id, day, idx, symbol, agent_direction, signal_key';
const PICK_COLS = 'id, round_id, pick, entry_price, resolves_at, settle_price, '
  + 'settle_state, seal, created_at';

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
 * than returning an empty list — an empty card renders as "no rounds today",
 * which is a confident statement about the game made from a failure to read the
 * market.
 */
async function ensureRounds(day) {
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
      (s) => s && s.created_at
        && new Date(s.created_at).toISOString().slice(0, 10) === day);
  } catch (e) {
    // The agent's own calls are a bonus, not a prerequisite: a signals outage
    // degrades the card to majors rather than cancelling the day.
    signals = [];
  }

  const tickers = await getTickers();
  const built = duel.buildRounds(day, signals, tickers);
  if (!built.length) {
    if (existing.length) return existing;      // a partial card beats none
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
 * Settle whatever is due among these calls.
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

/**
 * Record one call.
 *
 * Returns { ok: true, ... } or { ok: false, status, error } — a result object
 * rather than a thrown error, because every refusal here is an ordinary
 * outcome the caller must render differently: a closed round, a price we could
 * not read, and a call already made are three different sentences to a player.
 */
async function placePick(userId, roundId, rawPick, now = new Date()) {
  const pick = duel.normPick(rawPick);
  if (pick === null) {
    return { ok: false, status: 400, error: 'Call LONG, SHORT or PASS.' };
  }
  if (!Number.isInteger(Number(roundId)) || Number(roundId) <= 0) {
    return { ok: false, status: 400, error: 'Which round?' };
  }

  const rounds = await loadRoundsForDay(duel.dayKey(now));
  const round = rounds.find((r) => Number(r.id) === Number(roundId));
  if (!round) {
    return { ok: false, status: 404, error: 'That round is not on today\'s card.' };
  }
  if (!duel.isCallable(round, now)) {
    return { ok: false, status: 409, error: 'That round has closed — its day is over.' };
  }

  // The entry is snapped NOW, for this player. A stale mark would score the
  // call from a price it was never offered at, so an unreadable ticker refuses
  // the call outright rather than guessing one.
  const tickers = await getTickers();
  const mark = tickers && tickers[round.symbol];
  const entry = mark == null ? NaN : Number(mark.price);
  if (!Number.isFinite(entry) || entry <= 0) {
    return {
      ok: false,
      status: 503,
      reason: 'prices_unreadable',
      error: `Cannot read a ${round.symbol} price right now — your call was not recorded.`,
    };
  }

  const handle = await handleFor(userId);
  const picked_at = new Date(now).toISOString();
  const resolves_at = duel.horizonFor(now);
  const receipt = sealDuelPick({
    round_id: round.id, day: round.day, symbol: round.symbol,
    handle, pick, entry_price: entry, resolves_at, picked_at,
  });

  try {
    await pool.execute(
      'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
      + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, round.id, pick, entry, resolves_at, receipt.seal, receipt.seal_payload, picked_at]);
  } catch (e) {
    // The unique key IS the anti-cheat, not a convenience: a call cannot be
    // revised once made, so the second write has nowhere to land.
    if (e && (e.code === 'ER_DUP_ENTRY' || e.errno === 1062)) {
      return { ok: false, status: 409, error: 'You have already called this round.' };
    }
    throw e;
  }

  return {
    ok: true,
    round_id: round.id,
    symbol: round.symbol,
    pick,
    entry_price: entry,
    resolves_at,
    seal: receipt.seal,
    agent_direction: duel.normDirection(round.agent_direction),
  };
}

/** Today's card as the caller sees it, with their own calls attached. */
async function cardFor(userId, now = new Date()) {
  const day = duel.dayKey(now);
  const rounds = await ensureRounds(day);
  const picks = await loadPicks(userId);
  await settleDue(rounds, picks, now);
  const mine = new Map(picks.map((p) => [String(p.round_id), p]));

  const cards = rounds.map((r) => {
    const pick = mine.get(String(r.id));
    // The stance is owed only once the caller has committed. Before that it is
    // omitted entirely — see duel.publicRound.
    const view = duel.publicRound(r, { revealAgent: Boolean(pick), now });
    if (pick) {
      view.my_call = duel.publicPick(pick, { now });
      view.my_result = duel.scorePick(pick.pick, duel.pickOutcome(pick), r.agent_direction);
    }
    return view;
  });

  return {
    day,
    horizon_hours: duel.HORIZON_HOURS,
    rounds: cards,
    open: cards.filter((c) => c.callable && !c.my_call).length,
    counts_only: true,
  };
}

/** The player's record over the stated window. */
async function recordFor(userId, now = new Date()) {
  const floor = dayFloor(now, RECORD_WINDOW_DAYS);
  const [rounds, picks] = await Promise.all([loadRoundsSince(floor), loadPicks(userId)]);
  await settleDue(rounds, picks, now);
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

module.exports = {
  RECORD_WINDOW_DAYS, SETTLE_BATCH, ROUND_COLS, PICK_COLS,
  dayFloor, loadRoundsForDay, loadRoundsSince, loadPicks,
  ensureRounds, settleDue, handleFor, placePick, cardFor, recordFor,
};
