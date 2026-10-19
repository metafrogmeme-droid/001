'use strict';
/**
 * Daily Duel — the player calls the market before the agent's call is shown.
 *
 * A card a UTC day, in four CATEGORIES of question, every one of them settled
 * by the market and nothing else:
 *
 *   direction   three rounds. A SYMBOL and the agent's stance on it; the
 *               player calls LONG / SHORT / PASS and the stance is shown only
 *               once they have. The classic duel. Beating the agent scores
 *               double.
 *   backfade    one round, when the agent made a second call that day. The
 *               agent's call is the question: "The Claw called SHORT on SOL —
 *               back it or fade it?" Fading it correctly is beating it.
 *   range       one round. "Does BNB move more than ±2% either way?" The
 *               band is the round's own, stored with it, so a later change to
 *               the table cannot rescore a round that was played under it.
 *   mover       one round. "BTC or ETH — which moves more?" Two symbols, two
 *               entries, two settles, compared by the size of the move.
 *   sports      one round, where a football feed is configured. "Arsenal vs
 *               Chelsea — who wins?" Settled by the final score, not the
 *               market; calls close at kick-off. (lib/sports_feed.js)
 *
 * Every entry belongs to the pick, not to the round, and that is a fairness
 * property rather than an implementation detail. A shared entry snapped at
 * midnight would hand every later player a free look at how the day had
 * already gone — and a lock early enough to prevent that would close the game
 * for most of the day. Scoring each call from the price at the moment it was
 * made means nobody can profit by waiting, and the card stays open all day.
 *
 * This module is PURE — no I/O, no clock beyond an injectable `now`, no DB. It
 * is the single implementation of the rules: the web routes, the public board
 * and the Telegram surface all score through here, so the three can never
 * disagree about what a player's record is. The picks a round takes are
 * published on the round (`options`), so no surface keeps its own copy of
 * which words a category accepts.
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
 *   `accuracy().pct` is null — never 0 — when nothing has resolved. A mover
 *   round with ONE readable settle is unresolved: half a comparison is not a
 *   comparison.
 *
 * §4: counts, percent and market prices only. No amount of any currency is
 * computed or carried here.
 */

const { computeStreak, isoWeek, weekStart } = require('./arena_streaks');
const { SEASON_MAJORS } = require('./arena_seasons');
const { exchangeSymbol } = require('./agent_match');

/**
 * The card. Three classic rounds, then one of each other kind; back/fade needs
 * a second agent call that day, so a card holds five rounds or six.
 */
const CARD = Object.freeze({ direction: 3, backfade: 1, range: 1, mover: 1, sports: 1 });
/** The most rounds a day's card holds. */
const ROUNDS_PER_DAY = CARD.direction + CARD.backfade + CARD.range + CARD.mover + CARD.sports;
/** Kick-off to a published result: 90 minutes, half-time, stoppage, and slack. */
const SPORTS_FULL_TIME_MS = 150 * 60000;
/** How long a call runs, measured from the moment it was made. */
const HORIZON_HOURS = 24;
/** |move| under this settles FLAT: below it the "direction" is noise. The
 *  same band decides a TIE between two movers. */
const DEAD_BAND_PCT = 0.05;
const DAY_MS = 86400000;

/**
 * The categories, with the picks each takes and the outcome on which a PASS is
 * the right call: the market "went nowhere" (flat) or the two movers tied. A
 * range round has no such outcome, so a PASS on it is always declined.
 */
const KINDS = Object.freeze({
  direction: Object.freeze({ picks: Object.freeze(['long', 'short', 'pass']), neutral: 'flat' }),
  backfade: Object.freeze({ picks: Object.freeze(['back', 'fade', 'pass']), neutral: 'flat' }),
  range: Object.freeze({ picks: Object.freeze(['break', 'hold', 'pass']), neutral: null }),
  mover: Object.freeze({ picks: Object.freeze(['first', 'second', 'pass']), neutral: 'tie' }),
  sports: Object.freeze({ picks: Object.freeze(['home', 'draw', 'away', 'pass']), neutral: null }),
});
const KIND_NAMES = Object.freeze(Object.keys(KINDS));
/** The classic round's picks, kept under their old name for the callers that
 *  only ever meant those. */
const PICKS = KINDS.direction.picks;
const DIRECTIONS = Object.freeze(['long', 'short']);
/** Every pick any round takes, for a first look at a request before its round
 *  is known. */
const ALL_PICKS = Object.freeze(
  KIND_NAMES.reduce((acc, k) => acc.concat(KINDS[k].picks.filter((p) => acc.indexOf(p) < 0)), []));

/**
 * The band a range round asks about, by the symbol's class. A major moves
 * less in a day than an alt, so the question is kept close to a coin flip for
 * each. The band chosen is STORED on the round: this table is for building a
 * card, never for scoring one.
 */
const RANGE_BAND_PCT = Object.freeze({ major: 2, other: 4 });

/**
 * Symbols a card is topped up from, the season's majors first. Each must be
 * priced by the ticker map before it reaches a card (buildRounds), so an
 * unlisted or delisted one costs a round, never a guessed entry.
 */
const DUEL_POOL = Object.freeze(SEASON_MAJORS.concat([
  'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'LTCUSDT', 'DOTUSDT', 'SUIUSDT', 'ARBUSDT',
]));

/** A round's category. A stored round without one is a classic round (the
 *  rows that predate the categories); a category this code does not know is
 *  null, and a null kind is callable by nobody and scored for nobody. */
function kindOf(round) {
  const r = round || {};
  if (r.kind == null || r.kind === '') return 'direction';
  const k = String(r.kind).trim().toLowerCase();
  return KINDS[k] ? k : null;
}

/** The picks a category takes; none for a category we do not know. */
function picksFor(kind) {
  return KINDS[kind] ? KINDS[kind].picks : Object.freeze([]);
}

/** 'LONG' -> 'long' for the given category; anything else -> null. Never
 *  guesses a default. `kind` null means any category's pick. */
function normPick(v, kind = 'direction') {
  const s = String(v == null ? '' : v).trim().toLowerCase();
  const set = kind === null ? ALL_PICKS : picksFor(kind);
  return set.indexOf(s) >= 0 ? s : null;
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

/** A round's `meta` (a football match's teams and kick-off), stored as JSON
 *  text and read back as an object; null when the round carries none. */
function metaOf(round) {
  const m = (round || {}).meta;
  if (m == null || m === '') return null;
  if (typeof m === 'object') return m;
  try { return JSON.parse(String(m)); } catch (e) { return null; }
}

/** A sports round's kick-off, as ms, or NaN when it carries none. */
function kickoffMs(round) {
  const m = metaOf(round);
  return m && m.kickoff ? Date.parse(m.kickoff) : NaN;
}

/** The band for a symbol, by its class (build time only; see RANGE_BAND_PCT). */
function rangeBandFor(symbol) {
  return SEASON_MAJORS.indexOf(String(symbol || '').toUpperCase()) >= 0
    ? RANGE_BAND_PCT.major : RANGE_BAND_PCT.other;
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

/** 'break' | 'hold' | null. A band that is not a positive number cannot
 *  settle anything, so it answers null rather than treating every move as a
 *  break of nothing. */
function rangeOutcome(entryPrice, settlePrice, bandPct) {
  const move = movePct(entryPrice, settlePrice);
  const band = Number(bandPct);
  if (move === null || !Number.isFinite(band) || band <= 0) return null;
  return Math.abs(move) >= band ? 'break' : 'hold';
}

/** 'home' | 'draw' | 'away' | null from a final score. A score that is not
 *  two non-negative numbers is no score. */
function sportsOutcome(home, away) {
  if (home == null || away == null) return null;
  const h = Number(home);
  const a = Number(away);
  if (!Number.isFinite(h) || !Number.isFinite(a) || h < 0 || a < 0) return null;
  if (h === a) return 'draw';
  return h > a ? 'home' : 'away';
}

/** 'first' | 'second' | 'tie' | null. Half a comparison answers null. */
function moverOutcome(entryA, settleA, entryB, settleB) {
  const a = movePct(entryA, settleA);
  const b = movePct(entryB, settleB);
  if (a === null || b === null) return null;
  const diff = Math.abs(a) - Math.abs(b);
  if (Math.abs(diff) < DEAD_BAND_PCT) return 'tie';
  return diff > 0 ? 'first' : 'second';
}

/**
 * A stored classic call's outcome, over its own window. `settle_state ===
 * 'unresolved'` is the terminal "we never got a price for this" marker and
 * answers null, the same as an unsettled call — both are absences, and
 * neither is a measurement.
 */
function pickOutcome(pick) {
  if (!pick) return null;
  if (pick.settle_state === 'unresolved') return null;
  return outcomeOf(pick.entry_price, pick.settle_price);
}

/** A stored call's outcome under its round's category. */
function callOutcome(round, pick) {
  if (!pick) return null;
  if (pick.settle_state === 'unresolved') return null;
  const r = round || {};
  switch (kindOf(r)) {
    case 'direction':
    case 'backfade':
      return outcomeOf(pick.entry_price, pick.settle_price);
    case 'range':
      return rangeOutcome(pick.entry_price, pick.settle_price, r.threshold_pct);
    case 'mover':
      return moverOutcome(pick.entry_price, pick.settle_price, pick.entry_price_b, pick.settle_price_b);
    case 'sports':
      // The final score rides the two settle columns: home, then away.
      return sportsOutcome(pick.settle_price, pick.settle_price_b);
    default:
      return null;
  }
}

/**
 * Score one call.
 *
 *   unreadable outcome     -> 'unresolved'  0    (excluded from the denominator)
 *   PASS  + neutral        -> 'correct'     1    (declining noise is a call)
 *   PASS  + anything else  -> 'push'        0    (declined — NOT a loss)
 *   a side + neutral       -> 'push'        0    (the market didn't answer)
 *   right                  -> 'correct'     1
 *     ...and the agent was wrong -> 2 marks, beat_agent
 *   wrong                  -> 'wrong'       0    (never negative)
 *
 * `beat_agent` requires the agent to have actually had a view. On a round the
 * agent passed on there is nothing to beat, so it stays false; a range or a
 * mover round carries no view at all. On a back/fade round the agent's view
 * IS the question: backing it correctly scores one, fading it correctly
 * scores two, because the agent was wrong.
 */
function scoreCall(kind, pick, outcome, agentDirection) {
  const p = normPick(pick, kind);
  if (p === null) return { state: 'invalid', marks: 0, beat_agent: false };
  if (outcome == null) return { state: 'unresolved', marks: 0, beat_agent: false };
  const neutral = KINDS[kind].neutral;

  if (p === 'pass') {
    return neutral !== null && outcome === neutral
      ? { state: 'correct', marks: 1, beat_agent: false }
      : { state: 'push', marks: 0, beat_agent: false };
  }
  if (neutral !== null && outcome === neutral) return { state: 'push', marks: 0, beat_agent: false };

  const hit = (d, o) => (d === 'long' && o === 'up') || (d === 'short' && o === 'down');
  const agent = normDirection(agentDirection);

  if (kind === 'direction') {
    if (!hit(p, outcome)) return { state: 'wrong', marks: 0, beat_agent: false };
    const beat = agent !== null && !hit(agent, outcome);
    return { state: 'correct', marks: beat ? 2 : 1, beat_agent: beat };
  }
  if (kind === 'backfade') {
    // A back/fade round without a stance is a round that could not have been
    // asked; nothing can be scored against it.
    if (agent === null) return { state: 'invalid', marks: 0, beat_agent: false };
    const mine = p === 'back' ? agent : (agent === 'long' ? 'short' : 'long');
    if (!hit(mine, outcome)) return { state: 'wrong', marks: 0, beat_agent: false };
    const beat = p === 'fade';
    return { state: 'correct', marks: beat ? 2 : 1, beat_agent: beat };
  }
  // range, mover and sports: the pick names the outcome it predicts.
  return p === outcome
    ? { state: 'correct', marks: 1, beat_agent: false }
    : { state: 'wrong', marks: 0, beat_agent: false };
}

/** Score one classic call: the three-argument form every older caller uses. */
function scorePick(pick, outcome, agentDirection) {
  return scoreCall('direction', pick, outcome, agentDirection);
}

/**
 * Join picks to rounds and score each. Rounds are matched by `id`; a pick
 * whose round is missing is dropped rather than scored against nothing, and
 * so is a pick on a round whose category this code does not know.
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
    const kind = kindOf(round);
    if (kind === null) continue;
    const outcome = callOutcome(round, p);
    const res = scoreCall(kind, p.pick, outcome, round.agent_direction);
    const entry = {
      round_id: round.id,
      day: round.day,
      kind,
      symbol: round.symbol,
      pick: normPick(p.pick, kind),
      agent_direction: normDirection(round.agent_direction),
      outcome,
      // A sports call has no entry: null, never a zero that would read as a price.
      entry_price: p.entry_price == null ? null : Number(p.entry_price),
      move_pct: outcome === null || kind === 'sports' ? null : movePct(p.entry_price, p.settle_price),
      resolves_at: p.resolves_at,
      created_at: p.created_at,
      ...res,
    };
    if (kind === 'sports') {
      const m = metaOf(round);
      entry.match = m ? { home: m.home, away: m.away, competition: m.competition, kickoff: m.kickoff } : null;
      entry.score = outcome === null ? null : { home: Number(p.settle_price), away: Number(p.settle_price_b) };
    }
    if (kind === 'mover') {
      entry.symbol_b = round.symbol_b;
      entry.entry_price_b = Number(p.entry_price_b);
      entry.move_pct_b = outcome === null ? null : movePct(p.entry_price_b, p.settle_price_b);
    }
    if (kind === 'range') entry.threshold_pct = Number(round.threshold_pct);
    out.push(entry);
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
const FULL_CARD_CALLS = CARD.direction + CARD.range + CARD.mover;
const DUEL_QUEST_POOL = [
  { key: 'three_calls', icon: '🎯', name: 'Make 3 calls', target: 3,
    have: (w) => w.length },
  { key: 'beat_the_claw', icon: '⚔️', name: 'Beat the Claw once', target: 1,
    have: (w) => Math.min(1, w.filter((e) => e.beat_agent).length) },
  { key: 'two_correct', icon: '🏹', name: 'Land 2 correct calls', target: 2,
    have: (w) => w.filter((e) => e.state === 'correct').length },
  // Five, not "every round": a day's card holds five rounds or six, and a
  // quest must name a number a player can count to.
  { key: 'full_card', icon: '📅', name: 'Call ' + FULL_CARD_CALLS + ' rounds in one day', target: 1,
    have: (w) => {
      const perDay = new Map();
      for (const e of w) perDay.set(e.day, (perDay.get(e.day) || 0) + 1);
      let best = 0;
      for (const n of perDay.values()) if (n > best) best = n;
      return best >= FULL_CARD_CALLS ? 1 : 0;
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
 * The classic rounds come first: the agent's own calls, strongest conviction
 * leading, then the pool by 24h volume tops them up. The agent's SECOND call
 * of the day is held back from them and becomes the back/fade round, where
 * its stance is the question; held back, because a classic round on the same
 * symbol would have its hidden stance printed one card lower. Then the range
 * round on the next pooled symbol, and the mover on the two after that.
 *
 * A round is created ONLY when a real price backs it: no price, no round —
 * never a round with a guessed entry, which would silently make every pick on
 * it unscoreable later. A symbol appears on a card once.
 *
 * A topped-up classic round carries `agent_direction: null`, which the
 * surfaces render as "the Claw passed". That is an honest absence, not a
 * hidden direction, and it means nobody can be credited with beating an
 * opinion that was never held.
 */
function buildRounds(day, signals, tickers, sports) {
  if (!Number.isFinite(dayStartMs(day))) return [];
  const marks = tickers || {};
  const chosen = [];
  const seen = new Set();

  // Exchange-style, the key the ticker map uses. The agent's calls arrive in
  // the scanner's spelling ('PENDLE/USDT'), so reading them raw priced none
  // of them and every card was topped up with majors the Claw "passed" on,
  // while the calls it had actually made that day went unused.
  const priced = (symbol) => {
    const sym = exchangeSymbol(symbol);
    if (!sym || seen.has(sym)) return null;
    // A symbol we cannot price right now is a symbol nobody could call, so it
    // does not go on the card. The price itself is NOT kept: the entry belongs
    // to whoever calls it, at the moment they do.
    const m = marks[sym];
    const price = m == null ? NaN : Number(m.price);
    if (!Number.isFinite(price) || price <= 0) return null;
    return sym;
  };
  const push = (round) => {
    chosen.push(Object.assign({ day, idx: chosen.length, meta: null }, round));
  };

  // Unknown confidence sorts BELOW every known one — explicitly, rather than
  // via a `|| 0` that would rank it as a measured zero.
  const conf = (s) => (Number.isFinite(Number(s.confidence)) ? Number(s.confidence) : -1);
  const vol = (sym) => {
    // `Number(null)` is 0 AND finite, so a null volume slipped past the
    // isFinite check and ranked as a measured zero — above the -1 this
    // function already reserves for unknown, and against the intent stated
    // for `confidence` three lines up.
    const raw = marks[sym] == null ? null : marks[sym].volume;
    if (raw == null) return -1;
    const v = Number(raw);
    return Number.isFinite(v) ? v : -1;
  };

  // The agent's priced calls, strongest first, one per symbol.
  const calls = [];
  const callSyms = new Set();
  const ranked = (signals || [])
    .filter((s) => s && s.symbol && normDirection(s.direction) !== null)
    .slice()
    .sort((a, b) => conf(b) - conf(a));
  for (const s of ranked) {
    const sym = exchangeSymbol(s.symbol);
    if (!sym || callSyms.has(sym)) continue;
    const m = marks[sym];
    const price = m == null ? NaN : Number(m.price);
    if (!Number.isFinite(price) || price <= 0) continue;
    callSyms.add(sym);
    calls.push({ symbol: sym, agent_direction: normDirection(s.direction),
      signal_key: s.signal_key == null ? null : String(s.signal_key) });
  }
  const held = calls.length >= 2 ? calls.splice(1, 1)[0] : null;

  // Classic rounds: the agent's calls, then the pool by volume.
  const pool = DUEL_POOL.slice().sort((a, b) => vol(b) - vol(a));
  const nextPooled = () => {
    for (const sym of pool) {
      const p = priced(sym);
      if (p) return p;
    }
    return null;
  };
  for (const c of calls) {
    if (chosen.length >= CARD.direction) break;
    const sym = priced(c.symbol);
    if (!sym) continue;
    seen.add(sym);
    push({ kind: 'direction', symbol: sym, symbol_b: null, threshold_pct: null,
      agent_direction: c.agent_direction, signal_key: c.signal_key });
  }
  while (chosen.length < CARD.direction) {
    const sym = nextPooled();
    if (!sym) break;
    seen.add(sym);
    push({ kind: 'direction', symbol: sym, symbol_b: null, threshold_pct: null,
      agent_direction: null, signal_key: null });
  }

  // Back or fade: the agent's second call, its stance on the card.
  if (held) {
    const sym = priced(held.symbol);
    if (sym) {
      seen.add(sym);
      push({ kind: 'backfade', symbol: sym, symbol_b: null, threshold_pct: null,
        agent_direction: held.agent_direction, signal_key: held.signal_key });
    }
  }

  // Range: the next pooled symbol, with the band its class gets.
  const rangeSym = nextPooled();
  if (rangeSym) {
    seen.add(rangeSym);
    push({ kind: 'range', symbol: rangeSym, symbol_b: null, threshold_pct: rangeBandFor(rangeSym),
      agent_direction: null, signal_key: null });
  }

  // Mover: the next two. One alone is no comparison, so it is left off.
  const a = nextPooled();
  if (a) {
    seen.add(a);
    const b = nextPooled();
    if (b) {
      seen.add(b);
      push({ kind: 'mover', symbol: a, symbol_b: b, threshold_pct: null,
        agent_direction: null, signal_key: null });
    } else {
      seen.delete(a);
    }
  }

  // Football: the feed's dealt match, when one was read (lib/sports_feed.js).
  // Its symbol is the provider's event key; the teams and kick-off ride meta.
  const m = sports && typeof sports === 'object' ? sports : null;
  if (m && m.event_id && m.home && m.away && Number.isFinite(Date.parse(m.kickoff || ''))) {
    push({ kind: 'sports', symbol: ('FD:' + String(m.event_id)).slice(0, 20), symbol_b: null,
      threshold_pct: null, agent_direction: null, signal_key: null,
      meta: { provider: m.provider || 'football-data', event_id: String(m.event_id),
        home: String(m.home), away: String(m.away),
        home_short: String(m.home_short || m.home), away_short: String(m.away_short || m.away),
        competition: String(m.competition || ''), kickoff: new Date(m.kickoff).toISOString() } });
  }

  return chosen;
}

/**
 * Is this round still open to calls? A round belongs to its UTC day and closes
 * when the day does. There is no earlier lock and none is needed: because each
 * call is scored from the price at the moment it was made, waiting buys a
 * player nothing, so the card can stay open for the whole day it belongs to.
 * A round of a category this code does not know is open to nobody.
 */
function isCallable(round, now = new Date()) {
  if (!round || !round.day) return false;          // unknown day => not open
  const kind = kindOf(round);
  if (kind === null) return false;
  if (dayKey(now) !== String(round.day)) return false;
  if (kind === 'sports') {
    // Calls close at kick-off; a round whose kick-off cannot be read is open
    // to nobody rather than open all day on a guess.
    const ko = kickoffMs(round);
    return Number.isFinite(ko) && new Date(now).getTime() < ko;
  }
  return true;
}

/** When a sports call resolves: full time after the round's kick-off, or
 *  null when the kick-off cannot be read (then no call is taken). */
function sportsHorizonFor(round) {
  const ko = kickoffMs(round);
  return Number.isFinite(ko) ? new Date(ko + SPORTS_FULL_TIME_MS).toISOString() : null;
}

/** The horizon for a call made now. */
function horizonFor(pickedAt) {
  const t = new Date(pickedAt).getTime();
  if (!Number.isFinite(t)) return null;
  return new Date(t + HORIZON_HOURS * 3600000).toISOString();
}

/** Is this call due to settle? */
function isDue(pick, now = new Date()) {
  if (!pick || !pick.resolves_at) return false;
  const t = Date.parse(pick.resolves_at);
  return Number.isFinite(t) && new Date(now).getTime() >= t;
}

/** Past this, a call we still cannot price is marked terminally unresolved
 *  rather than left pending forever. */
const SETTLE_GIVE_UP_MS = 7 * DAY_MS;
function isAbandoned(pick, now = new Date()) {
  if (!pick || !pick.resolves_at) return false;
  const t = Date.parse(pick.resolves_at);
  return Number.isFinite(t) && new Date(now).getTime() >= t + SETTLE_GIVE_UP_MS;
}

/**
 * The public view of a round: its category, the picks it takes (`options`),
 * and what the question needs — the second symbol of a mover, the band of a
 * range. `agent_direction` is OMITTED — not nulled — until the caller has
 * committed: a null field is indistinguishable from "the agent passed", which
 * is a real answer, so showing null early would leak one fact and lie about
 * another. On a back/fade round the stance is the question, so it is there
 * from the start; a range or a mover round never has it, the agent having
 * held no view on either.
 */
function publicRound(round, { revealAgent, now = new Date() } = {}) {
  const r = round || {};
  const kind = kindOf(r);
  const out = {
    id: r.id,
    day: r.day,
    idx: r.idx,
    kind,
    symbol: r.symbol,
    options: picksFor(kind).slice(),
    callable: isCallable(r, now),
  };
  if (kind === 'mover') out.symbol_b = r.symbol_b;
  if (kind === 'range') out.threshold_pct = Number(r.threshold_pct);
  if (kind === 'sports') {
    const m = metaOf(r);
    out.match = m ? { home: m.home, away: m.away, home_short: m.home_short || m.home,
      away_short: m.away_short || m.away, competition: m.competition || '', kickoff: m.kickoff || null } : null;
  }
  // A range or a mover round never held a view, so the field is never there:
  // a null on it would read as "the Claw passed" on a choice never offered.
  if ((revealAgent && kind === 'direction') || kind === 'backfade') {
    out.agent_direction = normDirection(r.agent_direction);
  }
  return out;
}

/**
 * The public view of one of the caller's OWN calls.
 *
 * The three states are kept distinct on purpose. `pending` is "the horizon has
 * not arrived"; `unresolved` is "it has, and we could not price it". Collapsing
 * either into an outcome — or into each other — is how a waiting call starts
 * reading as a settled loss.
 *
 * `round` decides the category; without one the call is read as a classic.
 */
function publicPick(pick, { now = new Date(), round = null } = {}) {
  const p = pick || {};
  const kind = round ? kindOf(round) : 'direction';
  const out = {
    pick: normPick(p.pick, kind),
    entry_price: p.entry_price == null ? null : Number(p.entry_price),
    resolves_at: p.resolves_at,
    seal: p.seal || null,
    outcome: null,
  };
  if (kind === 'mover') out.entry_price_b = Number(p.entry_price_b);
  const outcome = round ? callOutcome(round, p) : pickOutcome(p);
  if (outcome !== null) {
    out.outcome = outcome;
    if (kind === 'sports') {
      out.score = { home: Number(p.settle_price), away: Number(p.settle_price_b) };
    } else {
      out.move_pct = Math.round(movePct(p.entry_price, p.settle_price) * 100) / 100;
      if (kind === 'mover') out.move_pct_b = Math.round(movePct(p.entry_price_b, p.settle_price_b) * 100) / 100;
    }
  } else if (isDue(p, now)) {
    out.unresolved = true;
  } else {
    out.pending = true;
  }
  return out;
}

module.exports = {
  CARD, ROUNDS_PER_DAY, FULL_CARD_CALLS, HORIZON_HOURS, DEAD_BAND_PCT, SETTLE_GIVE_UP_MS, SPORTS_FULL_TIME_MS,
  KINDS, KIND_NAMES, PICKS, ALL_PICKS, RANGE_BAND_PCT, DUEL_POOL, DUEL_QUEST_POOL,
  kindOf, picksFor, normPick, normDirection, dayKey, dayStartMs, horizonFor, rangeBandFor,
  metaOf, kickoffMs, sportsHorizonFor,
  movePct, outcomeOf, rangeOutcome, moverOutcome, sportsOutcome, pickOutcome, callOutcome,
  scoreCall, scorePick, scoreEntries, accuracy, computeMarks,
  duelStreak, weeklyDuelQuests,
  buildRounds, isCallable, isDue, isAbandoned, publicRound, publicPick,
};
