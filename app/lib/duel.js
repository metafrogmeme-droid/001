'use strict';
/**
 * Daily Duel — the player calls the market before the agent's call is shown.
 *
 * Three rounds a UTC day. Each round is a symbol, an entry price snapped at
 * creation, and a 24h horizon. The player calls LONG / SHORT / PASS before the
 * lock; the market settles it. Beating the agent scores double.
 *
 * This module is PURE — no I/O, no clock beyond an injectable `now`, no DB. It
 * is the single implementation of the rules: the web routes, the public board
 * and the Telegram surface all score through here, so the three can never
 * disagree about what a player's record is.
 *
 * Nothing here is stored. Marks, accuracy, streaks and quests are recomputed
 * from the picks and the settled rounds on every read, the same posture as
 * lib/achievements.js — a score that cannot be granted wrongly or drift from
 * the record.
 *
 * HONESTY (the rule this file exists to keep):
 *   A round whose settle price could not be read is UNRESOLVED. It is not a
 *   loss, not a flat, and not a zero — it is excluded from the accuracy
 *   denominator entirely and reported separately, because a total computed
 *   over rows we could not read is a partial total printed as a whole one.
 *   `accuracy().pct` is null — never 0 — when nothing has resolved.
 *
 * §4: counts, percent and market prices only. No amount of any currency is
 * computed or carried here.
 */

const { computeStreak, isoWeek, weekStart } = require('./arena_streaks');
const { SEASON_MAJORS } = require('./arena_seasons');

const ROUNDS_PER_DAY = 3;
const LOCK_HOURS = 12;      // picks close halfway through the day
const HORIZON_HOURS = 24;   // ...and settle at the end of it
/** |move| under this settles FLAT: below it the "direction" is noise. */
const DEAD_BAND_PCT = 0.05;
const DAY_MS = 86400000;

const PICKS = Object.freeze(['long', 'short', 'pass']);
const DIRECTIONS = Object.freeze(['long', 'short']);

/** 'LONG' -> 'long'; anything else -> null. Never guesses a default. */
function normPick(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase();
  return PICKS.indexOf(s) >= 0 ? s : null;
}

/** The agent's stance. null means the agent had NO VIEW — a real state that
 *  must never be rendered as a direction, and on which nobody can beat it. */
function normDirection(v) {
  if (v == null) return null;
  let s = String(v).trim().toLowerCase();
  if (s === 'buy') s = 'long';
  if (s === 'sell') s = 'short';
  return DIRECTIONS.indexOf(s) >= 0 ? s : null;
}

/** UTC day key ('2026-08-08') for a timestamp. */
function dayKey(now = new Date()) {
  return new Date(now).toISOString().slice(0, 10);
}

/** Midnight UTC of a day key, or NaN if the key is not a day. */
function dayStartMs(day) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(day))
    ? Date.parse(day + 'T00:00:00.000Z') : NaN;
}

/**
 * The move a round made, as a percent — or null when either price is
 * unreadable. A price of zero or below is not a price; it is a failed read
 * wearing a number, so it answers null too.
 */
function movePct(entryPrice, settlePrice) {
  const e = Number(entryPrice);
  const s = Number(settlePrice);
  if (!Number.isFinite(e) || !Number.isFinite(s) || e <= 0 || s <= 0) return null;
  return ((s - e) / e) * 100;
}

/** 'up' | 'down' | 'flat' | null. null = unreadable, NEVER folded into flat. */
function outcomeOf(entryPrice, settlePrice) {
  const move = movePct(entryPrice, settlePrice);
  if (move === null) return null;
  if (Math.abs(move) < DEAD_BAND_PCT) return 'flat';
  return move > 0 ? 'up' : 'down';
}

/**
 * A stored round's outcome. `settle_state === 'unresolved'` is the terminal
 * "we never got a price for this" marker and answers null, same as an
 * unsettled round — both are absences, and neither is a measurement.
 */
function roundOutcome(round) {
  if (!round) return null;
  if (round.settle_state === 'unresolved') return null;
  return outcomeOf(round.entry_price, round.settle_price);
}

/**
 * Score one call.
 *
 *   unreadable outcome     -> 'unresolved'  0    (excluded from the denominator)
 *   PASS  + flat           -> 'correct'     1    (declining noise is a call)
 *   PASS  + up/down        -> 'push'        0    (declined — NOT a loss)
 *   LONG/SHORT + flat      -> 'push'        0    (the market didn't answer)
 *   right                  -> 'correct'     1
 *     ...and the agent was wrong -> 2 marks, beat_agent
 *   wrong                  -> 'wrong'       0    (never negative)
 *
 * `beat_agent` requires the agent to have actually had a view. On a round the
 * agent passed on there is nothing to beat, so it stays false.
 */
function scorePick(pick, outcome, agentDirection) {
  const p = normPick(pick);
  if (p === null) return { state: 'invalid', marks: 0, beat_agent: false };
  if (outcome == null) return { state: 'unresolved', marks: 0, beat_agent: false };

  if (p === 'pass') {
    return outcome === 'flat'
      ? { state: 'correct', marks: 1, beat_agent: false }
      : { state: 'push', marks: 0, beat_agent: false };
  }
  if (outcome === 'flat') return { state: 'push', marks: 0, beat_agent: false };

  const hit = (d, o) => (d === 'long' && o === 'up') || (d === 'short' && o === 'down');
  if (!hit(p, outcome)) return { state: 'wrong', marks: 0, beat_agent: false };

  const agent = normDirection(agentDirection);
  const beat = agent !== null && !hit(agent, outcome);
  return { state: 'correct', marks: beat ? 2 : 1, beat_agent: beat };
}

/**
 * Join picks to rounds and score each. Rounds are matched by `id`; a pick
 * whose round is missing is dropped rather than scored against nothing.
 * Returns one entry per scored pick, newest-agnostic (caller sorts).
 */
function scoreEntries(rounds, picks) {
  const byId = new Map();
  for (const r of rounds || []) if (r && r.id != null) byId.set(String(r.id), r);
  const out = [];
  for (const p of picks || []) {
    if (!p) continue;
    const round = byId.get(String(p.round_id));
    if (!round) continue;
    const outcome = roundOutcome(round);
    const res = scorePick(p.pick, outcome, round.agent_direction);
    out.push({
      round_id: round.id,
      day: round.day,
      symbol: round.symbol,
      pick: normPick(p.pick),
      agent_direction: normDirection(round.agent_direction),
      outcome,
      move_pct: outcome === null ? null : movePct(round.entry_price, round.settle_price),
      created_at: p.created_at,
      ...res,
    });
  }
  return out;
}

/**
 * The record. `pct` is null — not 0 — when nothing has been scored, because a
 * player who has not resolved a call has no accuracy, and printing 0% would
 * report a measured failure where there is only an absence.
 *
 * `scored` deliberately excludes pushes: a declined round is neither a hit nor
 * a miss, so counting it either way would misreport discipline.
 */
function accuracy(entries) {
  let correct = 0, scored = 0, push = 0, wrong = 0, unresolved = 0;
  for (const e of entries || []) {
    if (!e) continue;
    if (e.state === 'unresolved') { unresolved++; continue; }
    if (e.state === 'invalid') continue;
    if (e.state === 'push') { push++; continue; }
    scored++;
    if (e.state === 'correct') correct++; else wrong++;
  }
  return {
    pct: scored > 0 ? Math.round((correct / scored) * 1000) / 10 : null,
    correct, wrong, push, scored, unresolved,
    resolved: scored + push,
  };
}

/**
 * Marks, with the part we cannot know yet reported beside it rather than
 * folded in. `pending` is the number of calls still unresolved: the total is
 * honest only while it travels with the count of rows it could not include.
 */
function computeMarks(entries) {
  let marks = 0, pending = 0, beat = 0;
  for (const e of entries || []) {
    if (!e) continue;
    if (e.state === 'unresolved') { pending++; continue; }
    if (e.state === 'invalid') continue;
    marks += e.marks;
    if (e.beat_agent) beat++;
  }
  return { marks, pending, beat_agent: beat };
}

/** Consecutive UTC days on which the player made a call. Reuses the arena's
 *  streak arithmetic (including its "today isn't over yet" grace) rather than
 *  growing a second definition of the same word. */
function duelStreak(picks, now = new Date()) {
  const days = (picks || [])
    .filter((p) => p && p.created_at)
    .map((p) => ({ closed_at: p.created_at }));
  return computeStreak(days, now);
}

/**
 * Weekly duel quests — two per ISO week, rotated deterministically so the whole
 * player base shares the same pair with no server state. Separate pool from the
 * arena's (those are trade-shaped) but the same rotation, so the two surfaces
 * turn over together.
 */
const DUEL_QUEST_POOL = [
  { key: 'three_calls', icon: '🎯', name: 'Make 3 calls', target: 3,
    have: (w) => w.length },
  { key: 'beat_the_claw', icon: '⚔️', name: 'Beat the Claw once', target: 1,
    have: (w) => Math.min(1, w.filter((e) => e.beat_agent).length) },
  { key: 'two_correct', icon: '🏹', name: 'Land 2 correct calls', target: 2,
    have: (w) => w.filter((e) => e.state === 'correct').length },
  { key: 'full_card', icon: '📅', name: 'Call all 3 rounds in one day', target: 1,
    have: (w) => {
      const perDay = new Map();
      for (const e of w) perDay.set(e.day, (perDay.get(e.day) || 0) + 1);
      let best = 0;
      for (const n of perDay.values()) if (n > best) best = n;
      return best >= ROUNDS_PER_DAY ? 1 : 0;
    } },
  { key: 'read_the_noise', icon: '📐', name: 'Pass on a round that settles flat', target: 1,
    have: (w) => Math.min(1, w.filter((e) => e.pick === 'pass' && e.state === 'correct').length) },
];

function weeklyDuelQuests(entries, now = new Date()) {
  const start = weekStart(now).getTime();
  const week = (entries || []).filter(
    (e) => e && e.created_at && new Date(e.created_at).getTime() >= start);
  const rot = isoWeek(now) + new Date(now).getUTCFullYear();
  const picks = [];
  for (let i = 0; i < 2; i++) picks.push(DUEL_QUEST_POOL[(rot + i * 3) % DUEL_QUEST_POOL.length]);
  return picks.map((q) => {
    const have = Math.min(q.target, q.have(week));
    return { key: q.key, icon: q.icon, name: q.name, target: q.target, have, done: have >= q.target };
  });
}

/**
 * The day's rounds, deterministic given the same inputs.
 *
 * The agent's own calls come first, strongest conviction leading, then the
 * majors by 24h volume top the card up. A round is created ONLY when a real
 * price backs it: no price, no round — never a round with a guessed entry,
 * which would silently make every pick on it unscoreable later.
 *
 * A topped-up round carries `agent_direction: null`, which the surfaces render
 * as "the Claw passed". That is an honest absence, not a hidden direction, and
 * it means nobody can be credited with beating an opinion that was never held.
 */
function buildRounds(day, signals, tickers) {
  const start = dayStartMs(day);
  if (!Number.isFinite(start)) return [];
  const marks = tickers || {};
  const chosen = [];
  const seen = new Set();

  const add = (symbol, agentDirection, signalKey) => {
    if (chosen.length >= ROUNDS_PER_DAY) return;
    const sym = String(symbol == null ? '' : symbol).trim().toUpperCase();
    if (!sym || seen.has(sym)) return;
    const m = marks[sym];
    const price = m == null ? NaN : Number(m.price);
    if (!Number.isFinite(price) || price <= 0) return;   // unreadable -> no round
    seen.add(sym);
    chosen.push({
      day,
      idx: chosen.length,
      symbol: sym,
      entry_price: price,
      agent_direction: agentDirection,
      signal_key: signalKey,
      locks_at: new Date(start + LOCK_HOURS * 3600000).toISOString(),
      resolves_at: new Date(start + HORIZON_HOURS * 3600000).toISOString(),
    });
  };

  // Unknown confidence sorts BELOW every known one — explicitly, rather than
  // via a `|| 0` that would rank it as a measured zero.
  const conf = (s) => (Number.isFinite(Number(s.confidence)) ? Number(s.confidence) : -1);
  const vol = (sym) => {
    const v = marks[sym] == null ? NaN : Number(marks[sym].volume);
    return Number.isFinite(v) ? v : -1;
  };

  const ranked = (signals || [])
    .filter((s) => s && s.symbol && normDirection(s.direction) !== null)
    .slice()
    .sort((a, b) => conf(b) - conf(a));
  for (const s of ranked) {
    add(s.symbol, normDirection(s.direction), s.signal_key == null ? null : String(s.signal_key));
  }

  const majors = SEASON_MAJORS.slice().sort((a, b) => vol(b) - vol(a));
  for (const sym of majors) add(sym, null, null);

  return chosen;
}

/** Has this round locked? Picks are refused from this moment. */
function isLocked(round, now = new Date()) {
  if (!round || !round.locks_at) return true;      // unknown lock => closed
  const t = Date.parse(round.locks_at);
  return !Number.isFinite(t) || new Date(now).getTime() >= t;
}

/** Is this round due to settle? */
function isDue(round, now = new Date()) {
  if (!round || !round.resolves_at) return false;
  const t = Date.parse(round.resolves_at);
  return Number.isFinite(t) && new Date(now).getTime() >= t;
}

/** Past this, a round we still cannot price is marked terminally unresolved
 *  rather than left pending forever. */
const SETTLE_GIVE_UP_MS = 7 * DAY_MS;
function isAbandoned(round, now = new Date()) {
  if (!round || !round.resolves_at) return false;
  const t = Date.parse(round.resolves_at);
  return Number.isFinite(t) && new Date(now).getTime() >= t + SETTLE_GIVE_UP_MS;
}

/**
 * The public view of a round. `agent_direction` is OMITTED — not nulled —
 * until the caller has picked or the round has locked: a null field is
 * indistinguishable from "the agent passed", which is a real answer, so
 * showing null early would leak a fact and lie about a different one.
 */
function publicRound(round, { revealAgent, now = new Date() } = {}) {
  const r = round || {};
  const out = {
    id: r.id,
    day: r.day,
    idx: r.idx,
    symbol: r.symbol,
    entry_price: Number(r.entry_price),
    locks_at: r.locks_at,
    resolves_at: r.resolves_at,
  };
  const outcome = roundOutcome(r);
  if (outcome !== null) {
    out.outcome = outcome;
    out.move_pct = Math.round(movePct(r.entry_price, r.settle_price) * 100) / 100;
  } else if (isDue(r, now)) {
    out.outcome = null;
    out.unresolved = true;
  }
  if (revealAgent) out.agent_direction = normDirection(r.agent_direction);
  return out;
}

module.exports = {
  ROUNDS_PER_DAY, LOCK_HOURS, HORIZON_HOURS, DEAD_BAND_PCT, SETTLE_GIVE_UP_MS,
  PICKS, DUEL_QUEST_POOL,
  normPick, normDirection, dayKey, dayStartMs,
  movePct, outcomeOf, roundOutcome,
  scorePick, scoreEntries, accuracy, computeMarks,
  duelStreak, weeklyDuelQuests,
  buildRounds, isLocked, isDue, isAbandoned, publicRound,
};
