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
const { sealDuelPick, sealDuelRound, sealDuelSettle, newRoundNonce } = require('./callseal');
const duel = require('./duel');
const sportsFeed = require('./sports_feed');

/** How far back a player's record is computed, stated on every payload that
 *  carries it rather than truncating in silence. */
const RECORD_WINDOW_DAYS = 90;
/** Bound on how many due calls one request will settle, so a long outage
 *  cannot turn the next page load into an unbounded fan-out. */
const SETTLE_BATCH = 6;

// The ONE list of what a round and a pick carry, imported by every other
// reader of the two tables (the public board, the share card, the command
// deck). A reader with its own list reads the categories' columns as absent,
// and an absent `kind` is a classic round, so every mover and range round
// would be scored as a LONG/SHORT call there.
const ROUND_COLS = 'id, day, idx, kind, symbol, symbol_b, threshold_pct, agent_direction, signal_key, meta,'
  + ' seal, seal_payload, created_at';
const PICK_COLS = 'id, round_id, pick, entry_price, entry_price_b, resolves_at, settle_price, '
  + 'settle_price_b, settle_state, settled_at, seal, seal_payload, settle_seal, settle_payload, created_at';

function dayFloor(now, days) {
  return new Date(new Date(now).getTime() - days * 86400000).toISOString().slice(0, 10);
}

/** A row as read: `meta` is JSON text in the table and an object here. */
function parseRound(r) {
  if (!r) return r;
  return Object.assign({}, r, { meta: duel.metaOf(r) });
}

async function loadRoundsForDay(day) {
  const [rows] = await pool.execute(
    `SELECT ${ROUND_COLS} FROM duel_rounds WHERE day = ? ORDER BY idx`, [day]);
  return (rows || []).map(parseRound);
}

async function loadRoundsSince(day) {
  const [rows] = await pool.execute(
    `SELECT ${ROUND_COLS} FROM duel_rounds WHERE day >= ? ORDER BY day, idx`, [day]);
  return (rows || []).map(parseRound);
}

/**
 * What the football feed said when a day's card was dealt BY THIS PROCESS:
 * 'dealt', 'none' (a readable feed with no qualifying match) or 'unreadable'.
 * Three sentences; a card built elsewhere answers from its rounds alone.
 */
const sportsStateByDay = new Map();

function sportsStateFor(day, rounds) {
  if ((rounds || []).some((r) => duel.kindOf(r) === 'sports')) return 'dealt';
  if (!sportsFeed.isConfigured()) return 'off';
  return sportsStateByDay.get(day) || 'unknown';
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
async function ensureRounds(day, now = new Date()) {
  const existing = await loadRoundsForDay(day);
  // A day that has rounds has its card. It is not topped up later: the
  // builder is deterministic over the prices and calls it is given, and those
  // move through the day, so a round added at noon could repeat a symbol the
  // morning's card already carries.
  if (existing.length) return existing;

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

  // The football match, where a feed is configured. A feed that cannot be
  // read costs the card its match and is SAID to have failed; a readable feed
  // with no match today is the other sentence. Neither cancels the day.
  let sports = null;
  if (sportsFeed.isConfigured()) {
    try {
      sports = await sportsFeed.candidateFor(day, now);
      sportsStateByDay.set(day, sports ? 'dealt' : 'none');
    } catch (e) {
      sportsStateByDay.set(day, 'unreadable');
      console.error('Sports feed unreadable:', (e && e.constructor && e.constructor.name) || 'Error',
        e && e.status ? `HTTP ${e.status}` : '');
    }
  }

  const tickers = await getTickers();
  const built = duel.buildRounds(day, signals, tickers, sports);
  if (!built.length) {
    const err = new Error('no market prices available to open the day');
    err.rcReason = 'prices_unreadable';
    throw err;
  }

  const dealt_at = new Date(now).toISOString();
  for (const r of built) {
    // Provable Calls v6: the round is sealed as dealt, stance included, and
    // the hash is on the card before anyone can call (lib/callseal.js).
    const m = r.meta && r.kind === 'sports' ? r.meta : null;
    const receipt = sealDuelRound({
      day: r.day, idx: r.idx, kind: r.kind, symbol: r.symbol, symbol_b: r.symbol_b,
      threshold_pct: r.threshold_pct, agent_direction: r.agent_direction,
      match: m ? { event_id: m.event_id, home: m.home, away: m.away, kickoff: m.kickoff } : null,
      nonce: newRoundNonce(), dealt_at,
    });
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, kind, symbol, symbol_b, threshold_pct,'
      + ' agent_direction, signal_key, meta, seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [r.day, r.idx, r.kind, r.symbol, r.symbol_b, r.threshold_pct, r.agent_direction, r.signal_key,
        r.meta == null ? null : JSON.stringify(r.meta), receipt.seal, receipt.seal_payload, dealt_at]);
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
  const roundOf = new Map((rounds || []).map((r) => [String(r.id), r]));
  const due = (picks || []).filter(
    (p) => p && p.settle_price == null && p.settle_state == null && duel.isDue(p, now));
  let settled = 0;
  for (const p of due.slice(0, SETTLE_BATCH)) {
    const round = roundOf.get(String(p.round_id));
    if (round && duel.kindOf(round) === 'sports') {
      // The final score, from the feed, when the match has FINISHED. Not yet
      // finished: nothing is written and the next settle asks again. Past the
      // give-up window (a postponed or abandoned match): unresolved, never a
      // loss. An unreadable feed is logged by class and leaves the call pending.
      const m = duel.metaOf(round);
      let score = null;
      try {
        score = m && m.event_id ? await sportsFeed.finalScore(m.event_id) : null;
      } catch (e) {
        console.error('Sports feed unreadable at settle:', (e && e.constructor && e.constructor.name) || 'Error');
        score = null;
      }
      if (score) {
        await writeSettlement(round, p, 'settled', score.home, score.away, now);
        settled++;
      } else if (duel.isAbandoned(p, now)) {
        await writeSettlement(round, p, 'unresolved', null, null, now);
      }
      continue;
    }
    const symbol = round && round.symbol;
    const at = Date.parse(p.resolves_at);
    const close = (symbol && Number.isFinite(at)) ? await closeAt(symbol, at) : null;
    // A mover needs BOTH closes. One alone is written nowhere: half a
    // comparison recorded as a settle would score the round from one side.
    const mover = round && duel.kindOf(round) === 'mover';
    const closeB = (mover && close != null && round.symbol_b && Number.isFinite(at))
      ? await closeAt(round.symbol_b, at) : null;
    if (close != null && (!mover || closeB != null)) {
      await writeSettlement(round, p, 'settled', close, mover ? closeB : null, now);
      settled++;
    } else if (duel.isAbandoned(p, now)) {
      await writeSettlement(round, p, 'unresolved', null, null, now);
    }
  }
  return settled;
}

/**
 * One settlement, written with its own receipt (Provable Calls v7). The
 * receipt names the call seal it settles and the round seal the call was
 * made under, carries the prices (or the score) and the verdict the rules
 * give them, and is folded into its day's root like every other seal. An
 * unresolved settlement is sealed too: null prices and 'unresolved' are the
 * facts, and a receipt for "we could not price it" is the one a later
 * rewrite would most like to lose. A pick whose round is not loaded cannot
 * be scored and is written without a receipt rather than with a wrong one.
 */
async function writeSettlement(round, p, state, close, closeB, now) {
  const settled_at = new Date(now).toISOString();
  let seal = null;
  let payload = null;
  if (round) {
    const kind = duel.kindOf(round);
    const outcome = state === 'settled'
      ? duel.callOutcome(round, { ...p, settle_price: close, settle_price_b: closeB }) : null;
    const res = duel.scoreCall(kind, p.pick, outcome, round.agent_direction);
    const receipt = sealDuelSettle({
      round_id: round.id, call_seal: p.seal, round_seal: round.seal, kind, pick: p.pick,
      settle_state: state, settle_price: close, settle_price_b: closeB,
      outcome, result: res.state, marks: res.marks, settled_at,
    });
    seal = receipt.seal;
    payload = receipt.seal_payload;
  }
  await pool.execute(
    'UPDATE duel_picks SET settle_price = ?, settle_price_b = ?, settle_state = ?, settled_at = ?,'
    + ' settle_seal = ?, settle_payload = ? WHERE id = ?',
    [close, closeB, state, settled_at, seal, payload, p.id]);
  p.settle_price = close;
  p.settle_price_b = closeB;
  p.settle_state = state;
  p.settled_at = settled_at;
  p.settle_seal = seal;
  p.settle_payload = payload;
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
  // A first look, before the round is known: a word no round takes is
  // refused here; a word the WRONG round takes is refused below, naming the
  // picks that round does take.
  if (duel.normPick(rawPick, null) === null) {
    return { ok: false, status: 400, error: 'That is not a call any round takes.' };
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
  const kind = duel.kindOf(round);
  const pick = duel.normPick(rawPick, kind);
  if (pick === null) {
    const words = duel.picksFor(kind).map((w) => w.toUpperCase());
    return { ok: false, status: 400,
      error: `Call ${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}.` };
  }

  // The entry is snapped NOW, for this player. A stale mark would score the
  // call from a price it was never offered at, so an unreadable ticker refuses
  // the call outright rather than guessing one. A mover needs both of its
  // symbols priced in the same read. A sports call has no entry at all: it
  // is settled by a score, and resolves at full time after the kick-off.
  let entry = null;
  let entryB = null;
  let resolves_at;
  if (kind === 'sports') {
    resolves_at = duel.sportsHorizonFor(round);
    if (!resolves_at) {
      return { ok: false, status: 503, reason: 'kickoff_unreadable',
        error: 'This match has no readable kick-off — your call was not recorded.' };
    }
  } else {
    const tickers = await getTickers();
    const priceOf = (sym) => {
      const mark = tickers && tickers[sym];
      const v = mark == null ? NaN : Number(mark.price);
      return Number.isFinite(v) && v > 0 ? v : null;
    };
    entry = priceOf(round.symbol);
    entryB = kind === 'mover' ? priceOf(round.symbol_b) : null;
    const unpriced = entry === null ? round.symbol : (kind === 'mover' && entryB === null ? round.symbol_b : null);
    if (unpriced) {
      return {
        ok: false,
        status: 503,
        reason: 'prices_unreadable',
        error: `Cannot read a ${unpriced} price right now — your call was not recorded.`,
      };
    }
    resolves_at = duel.horizonFor(now);
  }

  const handle = await handleFor(userId);
  const picked_at = new Date(now).toISOString();
  const receipt = sealDuelPick({
    round_id: round.id, day: round.day, kind, symbol: round.symbol,
    symbol_b: kind === 'mover' ? round.symbol_b : null,
    threshold_pct: kind === 'range' ? Number(round.threshold_pct) : null,
    handle, pick, entry_price: entry, entry_price_b: entryB, resolves_at, picked_at,
  });

  try {
    await pool.execute(
      'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, entry_price_b, resolves_at,'
      + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, round.id, pick, entry, entryB, resolves_at, receipt.seal, receipt.seal_payload, picked_at]);
  } catch (e) {
    // The unique key IS the anti-cheat, not a convenience: a call cannot be
    // revised once made, so the second write has nowhere to land.
    if (e && (e.code === 'ER_DUP_ENTRY' || e.errno === 1062)) {
      return { ok: false, status: 409, error: 'You have already called this round.' };
    }
    throw e;
  }

  const out = {
    ok: true,
    round_id: round.id,
    kind,
    symbol: round.symbol,
    pick,
    entry_price: entry,
    resolves_at,
    seal: receipt.seal,
  };
  if (kind === 'mover') { out.symbol_b = round.symbol_b; out.entry_price_b = entryB; }
  if (kind === 'range') out.threshold_pct = Number(round.threshold_pct);
  if (kind === 'sports') {
    const m = duel.metaOf(round);
    out.match = m ? { home: m.home, away: m.away, home_short: m.home_short || m.home,
      away_short: m.away_short || m.away, competition: m.competition || '', kickoff: m.kickoff || null } : null;
  }
  // The stance is owed on the rounds that have one to reveal. A range or a
  // mover round never held a view, and saying "the Claw passed" on it would
  // describe a choice that was never on offer.
  if (kind === 'direction' || kind === 'backfade') {
    out.agent_direction = duel.normDirection(round.agent_direction);
  }
  return out;
}

/** Today's card as the caller sees it, with their own calls attached. */
async function cardFor(userId, now = new Date()) {
  const day = duel.dayKey(now);
  const rounds = await ensureRounds(day, now);
  const picks = await loadPicks(userId);
  await settleDue(rounds, picks, now);
  const mine = new Map(picks.map((p) => [String(p.round_id), p]));

  const cards = rounds.map((r) => {
    const pick = mine.get(String(r.id));
    // The stance is owed only once the caller has committed. Before that it is
    // omitted entirely — see duel.publicRound.
    const view = duel.publicRound(r, { revealAgent: Boolean(pick), now });
    if (pick) {
      view.my_call = duel.publicPick(pick, { now, round: r });
      view.my_result = duel.scoreCall(duel.kindOf(r), pick.pick, duel.callOutcome(r, pick), r.agent_direction);
    }
    return view;
  });

  return {
    day,
    horizon_hours: duel.HORIZON_HOURS,
    rounds: cards,
    open: cards.filter((c) => c.callable && !c.my_call).length,
    // Why there is, or is not, a football round: 'dealt', 'none' (a readable
    // feed with no match today), 'unreadable' (the feed failed when the card
    // was dealt), 'off' (no feed configured), 'unknown' (the card was dealt
    // by another process). A surface says something only for 'unreadable'.
    sources: { sports: sportsStateFor(day, rounds) },
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
  dayFloor, parseRound, loadRoundsForDay, loadRoundsSince, loadPicks, sportsStateFor, writeSettlement,
  ensureRounds, settleDue, handleFor, placePick, cardFor, recordFor,
  _resetSportsState: () => sportsStateByDay.clear(),
};
