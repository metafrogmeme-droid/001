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

const { pool, withTransaction } = require('../db');
const { getTickers } = require('./tickers');
const { closeAt } = require('./candles');
const { sealDuelPick, sealDuelRound, sealDuelSettle, newRoundNonce,
  sealDuelRoundChips, sealDuelPickChips, sealDuelSettleChips, sealDuelReview } = require('./callseal');
const confidence = require('./duel_confidence');
const duel = require('./duel');
const chips = require('./duel_chips');
const { flag_on_for } = require('./admin_preview');
const review = require('./duel_review');
const sportsFeed = require('./sports_feed');
const duelContext = require('./duel_context');
const secondPrice = require('./second_price');

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
//
// Every column the tables carry but the owner, held to the tables by
// test/the_duel_reads_every_column_it_writes.test.js. The second reading and
// the dispute flag were written at settlement and never selected, so MySQL
// handed every later read a call with no `settle_disputed`, and a disputed
// settlement read as unchecked after its first page load. The in-memory
// database returns whole rows whatever the projection, which hid it.
const ROUND_COLS = 'id, day, idx, kind, symbol, symbol_b, threshold_pct, agent_direction, signal_key, meta,'
  + ' seal, seal_payload, odds, odds_source, created_at';
const PICK_COLS = 'id, round_id, pick, entry_price, entry_price_b, resolves_at, settle_price, '
  + 'settle_price_b, settle_state, settled_at, seal, seal_payload, settle_seal, settle_payload, '
  + 'settle_second_price, settle_second_price_b, settle_disputed, chips, '
  + 'flagged_at, review_state, review_outcome, review_seal, review_payload, reviewed_at, created_at';

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

async function loadRoundsSince(day, exec = pool) {
  const [rows] = await exec.execute(
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

async function loadPicks(userId, exec = pool) {
  const [rows] = await exec.execute(
    `SELECT ${PICK_COLS} FROM duel_picks WHERE user_id = ?`, [userId]);
  return rows || [];
}

/** The season a moment falls in: the duel's calendar month, UTC. */
function seasonAt(now) {
  return new Date(now).toISOString().slice(0, 7);
}

/** A player's chips this season (lib/duel_chips.js stackOf), read through `exec`. */
async function stackFor(userId, now = new Date(), exec = pool, picks = null) {
  const season = seasonAt(now);
  const [rounds, mine] = await Promise.all([
    loadRoundsSince(season + '-01', exec), picks ? Promise.resolve(picks) : loadPicks(userId, exec),
  ]);
  return chips.stackOf(rounds, mine, season, now);
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
  // Chips (lib/duel_chips.js): with the layer on, each round is dealt at the
  // table's odds for its category, sealed with it (v9). Off, the round is the
  // v6 round it always was, with no odds column written.
  // Public flag only. An admin preview must not write odds onto the shared
  // row: every other caller would then see chips the flag still withholds.
  const withChips = chips.enabled();
  for (const r of built) {
    // Provable Calls v6: the round is sealed as dealt, stance included, and
    // the hash is on the card before anyone can call (lib/callseal.js).
    const m = r.meta && r.kind === 'sports' ? r.meta : null;
    const facts = {
      day: r.day, idx: r.idx, kind: r.kind, symbol: r.symbol, symbol_b: r.symbol_b,
      threshold_pct: r.threshold_pct, agent_direction: r.agent_direction,
      match: m ? { event_id: m.event_id, home: m.home, away: m.away, kickoff: m.kickoff } : null,
      nonce: newRoundNonce(), dealt_at,
    };
    const dealtOdds = withChips ? chips.oddsFor(r.kind) : null;
    const receipt = dealtOdds
      ? sealDuelRoundChips({ ...facts, odds: dealtOdds.odds, odds_source: dealtOdds.source })
      : sealDuelRound(facts);
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, kind, symbol, symbol_b, threshold_pct,'
      + ' agent_direction, signal_key, meta, seal, seal_payload, odds, odds_source, created_at)'
      + ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [r.day, r.idx, r.kind, r.symbol, r.symbol_b, r.threshold_pct, r.agent_direction, r.signal_key,
        r.meta == null ? null : JSON.stringify(r.meta), receipt.seal, receipt.seal_payload,
        dealtOdds && dealtOdds.odds ? JSON.stringify(dealtOdds.odds) : null,
        dealtOdds ? dealtOdds.source : null, dealt_at]);
  }
  // The day's context lines, read once now and remembered; not awaited, so
  // the deal never waits on a rented source (lib/duel_context.js).
  duelContext.primeSmartMoney(built);
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
      // A second reading of the same instant (lib/second_price.js), for the
      // receipt to say whether another source agreed. The venue's close
      // above is the settle price whatever it says; a reading that could
      // not be made leaves the settlement unchecked, never unsettled.
      const check = await secondReadings(symbol, mover ? round.symbol_b : null, at, close, closeB);
      await writeSettlement(round, p, 'settled', close, mover ? closeB : null, now, check);
      settled++;
    } else if (duel.isAbandoned(p, now)) {
      await writeSettlement(round, p, 'unresolved', null, null, now);
    }
  }
  return settled;
}

/**
 * The second readings for a settlement and the dispute they make:
 * { second, second_b, disputed } with disputed true (a read leg disagreed
 * past the tolerance), false (every leg read, all agreed) or null (no leg
 * read, or a leg unread while the read ones agreed: not fully checked).
 */
async function secondReadings(symbol, symbolB, at, close, closeB) {
  const read = async (sym) => {
    try { return await secondPrice.secondPriceAt(sym, at); } catch (e) { return { read: false, reason: 'unreachable', detail: (e && e.name) || 'Error' }; }
  };
  const second = await read(symbol);
  const second_b = symbolB ? await read(symbolB) : null;
  const tol = secondPrice.tolerancePct();
  const verdicts = [secondPrice.disputed(close, second, tol)];
  if (symbolB) verdicts.push(secondPrice.disputed(closeB, second_b, tol));
  let disputed = null;
  if (verdicts.some((v) => v === true)) disputed = true;
  else if (verdicts.every((v) => v === false)) disputed = false;
  return { second, second_b, disputed };
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
async function writeSettlement(round, p, state, close, closeB, now, check = null) {
  const settled_at = new Date(now).toISOString();
  const second = check && check.second && check.second.read === true ? check.second : null;
  const secondB = check && check.second_b && check.second_b.read === true ? check.second_b : null;
  const disputed = check && check.disputed != null ? Boolean(check.disputed) : null;
  let seal = null;
  let payload = null;
  if (round) {
    const kind = duel.kindOf(round);
    const outcome = state === 'settled'
      ? duel.callOutcome(round, { ...p, settle_price: close, settle_price_b: closeB }) : null;
    const res = duel.scoreCall(kind, p.pick, outcome, round.agent_direction);
    const facts = {
      round_id: round.id, call_seal: p.seal, round_seal: round.seal, kind, pick: p.pick,
      settle_state: state, settle_price: close, settle_price_b: closeB,
      outcome, result: res.state, marks: res.marks, settled_at,
      second_source: second, second_source_b: secondB, disputed,
    };
    // A call that carried chips gets the v9 settlement: the chips, the odds
    // they were put at, the movement the verdict gives them, and when a
    // disputed settlement's chips are released (lib/duel_chips.js).
    let receipt;
    if (p.chips != null) {
      const table = chips.oddsOf(round);
      const odds = table && p.pick in table ? table[p.pick] : null;
      receipt = sealDuelSettleChips({
        ...facts, chips: Number(p.chips), odds_used: odds,
        payout: odds == null ? null : chips.payoutFor(Number(p.chips), odds, res.state),
        held_until: chips.heldUntil(settled_at, disputed),
      });
    } else {
      receipt = sealDuelSettle(facts);
    }
    seal = receipt.seal;
    payload = receipt.seal_payload;
  }
  await pool.execute(
    'UPDATE duel_picks SET settle_price = ?, settle_price_b = ?, settle_state = ?, settled_at = ?,'
    + ' settle_seal = ?, settle_payload = ?, settle_second_price = ?, settle_second_price_b = ?,'
    + ' settle_disputed = ? WHERE id = ?',
    [close, closeB, state, settled_at, seal, payload,
      second ? second.price : null, secondB ? secondB.price : null,
      disputed == null ? null : (disputed ? 1 : 0), p.id]);
  p.settle_price = close;
  p.settle_price_b = closeB;
  p.settle_state = state;
  p.settled_at = settled_at;
  p.settle_seal = seal;
  p.settle_payload = payload;
  p.settle_second_price = second ? second.price : null;
  p.settle_second_price_b = secondB ? secondB.price : null;
  p.settle_disputed = disputed == null ? null : (disputed ? 1 : 0);
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
async function placePick(userId, roundId, rawPick, now = new Date(), opts = {}) {
  // A first look, before the round is known: a word no round takes is
  // refused here; a word the WRONG round takes is refused below, naming the
  // picks that round does take.
  if (duel.normPick(rawPick, null) === null) {
    return { ok: false, status: 400, error: 'That is not a call any round takes.' };
  }
  // Chips ride with the call or not at all (lib/duel_chips.js). Absent is
  // "no chips"; anything present is checked, and a refusal says why rather
  // than recording the call without them.
  const put = chips.parseChips(opts && opts.chips);
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
  const viewer = { user: { user_id: userId } };
  const dealtOdds = chips.oddsOf(round);
  const chipsPublic = chips.enabled();
  const chipsOn = chipsPublic || await flag_on_for(viewer, 'DUEL_CHIPS_ENABLED');
  // Preview odds live on this call only. The round row stays as it was dealt.
  const odds = dealtOdds || (chipsOn && !chipsPublic ? chips.oddsFor(kind).odds : dealtOdds);
  // Everything but the stack is checked before a price is read; the stack is
  // checked under the player's row lock, below.
  const early = chips.checkPut({ on: chipsOn, odds, kind, pick, chips: put });
  if (!early.ok) {
    const { ok, ...refusal } = early;
    return { ok: false, ...refusal };
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
  const facts = {
    round_id: round.id, day: round.day, kind, symbol: round.symbol,
    symbol_b: kind === 'mover' ? round.symbol_b : null,
    threshold_pct: kind === 'range' ? Number(round.threshold_pct) : null,
    handle, pick, entry_price: entry, entry_price_b: entryB, resolves_at, picked_at,
  };
  // The rating is sealed only while the flag is on, and only when it is one
  // of the three words. Off, a client that sends one is ignored, so the
  // receipt is the receipt it has always been. A word that is not one of the
  // three is refused rather than stored as something else.
  const confidenceOn = confidence.enabled() || await flag_on_for(viewer, 'DUEL_CONFIDENCE_ENABLED');
  if (confidenceOn && opts && opts.confidence != null && opts.confidence !== '') {
    const level = confidence.levelOf(opts.confidence);
    if (!level) {
      return { ok: false, status: 400, error: 'Choose low, medium or high. Your call was not recorded.' };
    }
    facts.confidence = level;
  }
  // A call with chips is the v9 receipt (the chips and their odds sealed in
  // it); one without is the v4 receipt it always was.
  const receipt = put === null ? sealDuelPick(facts) : sealDuelPickChips({ ...facts, chips: put, odds: odds[pick] });

  try {
    const insert = (exec) => exec.execute(
      'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, entry_price_b, resolves_at,'
      + ' seal, seal_payload, chips, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, round.id, pick, entry, entryB, resolves_at, receipt.seal, receipt.seal_payload, put, picked_at]);
    if (put === null) {
      await insert(pool);
    } else {
      // The stack is read and the call written under the player's own row
      // lock, so two calls sent at once cannot both spend the same chips.
      const refused = await withTransaction(async (conn) => {
        await conn.execute('SELECT id FROM users WHERE id = ? FOR UPDATE', [userId]);
        const stack = await stackFor(userId, now, conn);
        const check = chips.checkPut({ on: chipsOn, odds, kind, pick, chips: put, available: stack.available });
        if (!check.ok) return check;
        await insert(conn);
        return null;
      });
      if (refused) {
        const { ok, ...refusal } = refused;
        return { ok: false, ...refusal };
      }
    }
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
  if (put !== null) { out.chips = put; out.odds = odds[pick]; }
  if (facts.confidence) out.confidence = facts.confidence;
  if ((facts.confidence && !confidence.enabled()) || (put !== null && !chipsPublic)) {
    out.admin_preview = true;
  }
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
/**
 * The chips block a payload carries: the rules and the caller's stack, while
 * the layer is on or while they still have chips calls this season (a layer
 * turned off mid-season still pays what was put). Null otherwise: no block,
 * rather than a stack of 1,000 for a game the player was never offered.
 */
async function chipsBlock(userId, picks, now) {
  const on = chips.enabled() || await flag_on_for({ user: { user_id: userId } }, 'DUEL_CHIPS_ENABLED');
  if (!on && !(picks || []).some((p) => p && p.chips != null)) return null;
  const stack = await stackFor(userId, now, pool, picks);
  if (!on && stack.calls_with_chips === 0) return null;
  return { enabled: on, ...chips.rules(), stack };
}

async function cardFor(userId, now = new Date()) {
  const day = duel.dayKey(now);
  const rounds = await ensureRounds(day, now);
  const picks = await loadPicks(userId);
  await settleDue(rounds, picks, now);
  const mine = new Map(picks.map((p) => [String(p.round_id), p]));
  const viewer = { user: { user_id: userId } };
  const chipsPublic = chips.enabled();
  const chipsOn = chipsPublic || await flag_on_for(viewer, 'DUEL_CHIPS_ENABLED');
  const confidencePublic = confidence.enabled();
  const confidenceOn = confidencePublic || await flag_on_for(viewer, 'DUEL_CONFIDENCE_ENABLED');

  const cards = [];
  for (const r of rounds) {
    const pick = mine.get(String(r.id));
    // The stance is owed only once the caller has committed. Before that it is
    // omitted entirely — see duel.publicRound.
    const view = duel.publicRound(r, { revealAgent: Boolean(pick), now });
    // The odds this round was dealt at, offered while the layer is on. A
    // round dealt without chips carries `odds: null`, which a surface says.
    if (chipsOn) {
      const dealt = chips.oddsOf(r);
      if (dealt) view.odds = dealt;
      else if (!chipsPublic) view.odds = chips.oddsFor(duel.kindOf(r)).odds;
      else view.odds = null;
    }
    if (pick) {
      view.my_call = duel.publicPick(pick, { now, round: r });
      const rated = confidence.fromPayload(pick.seal_payload);
      if (rated) view.my_call.confidence = rated;
      view.my_result = duel.verdictOf(r, pick);
      const c = chips.pickChips(pick, r, now);
      if (c) view.my_call.chips = c;
      // May the player still flag this settlement (lib/duel_review.js)?
      view.my_call.flaggable = review.canFlag(pick, now).ok;
    }
    // A remembered context line, when there is one; never a lookup here.
    await duelContext.attachSmartMoney(view, r);
    cards.push(view);
  }

  const out = {
    day,
    horizon_hours: duel.HORIZON_HOURS,
    rounds: cards,
    open: cards.filter((c) => c.callable && !c.my_call).length,
    // Why there is, or is not, a football round: 'dealt', 'none' (a readable
    // feed with no match today), 'unreadable' (the feed failed when the card
    // was dealt), 'off' (no feed configured), 'unknown' (the card was dealt
    // by another process). A surface says something only for 'unreadable'.
    sources: { sports: sportsStateFor(day, rounds) },
    chips: await chipsBlock(userId, picks, now),
    counts_only: true,
  };
  // Off: the keys are absent, so a card is the card it was.
  if (confidenceOn) {
    out.confidence = true;
    out.confidence_levels = confidence.LEVELS.slice();
  }
  if ((!confidencePublic && confidenceOn) || (!chipsPublic && chipsOn)) out.admin_preview = true;
  return out;
}

/**
 * Flag one of the caller's own settlements, within the check window. Written
 * once: the `flagged_at IS NULL` guard makes a second flag land nowhere.
 * → { ok: true, flagged_at } or { ok: false, status, reason, error }.
 */
async function flagSettlement(userId, roundId, now = new Date()) {
  if (!Number.isInteger(Number(roundId)) || Number(roundId) <= 0) {
    return { ok: false, status: 400, reason: 'round', error: 'Which round?' };
  }
  const picks = await loadPicks(userId);
  const pick = picks.find((p) => Number(p.round_id) === Number(roundId));
  const allowed = review.canFlag(pick, now);
  if (!allowed.ok) return allowed;
  const at = new Date(now).toISOString();
  const [res] = await pool.execute('UPDATE duel_picks SET flagged_at = ? WHERE id = ? AND flagged_at IS NULL', [at, pick.id]);
  if (!res || !res.affectedRows) return { ok: false, status: 409, reason: 'flagged', error: 'You have flagged that settlement already.' };
  return { ok: true, round_id: Number(roundId), flagged_at: at, review_by: new Date(Date.parse(pick.settled_at) + review.WINDOW_MS).toISOString() };
}

/**
 * The operator's queue: settlements flagged or disputed and not yet reviewed,
 * newest first, over the review horizon. No player's identity: the call, the
 * prices and what is being asked of the reviewer.
 */
async function reviewQueue(now = new Date()) {
  const since = new Date(new Date(now).getTime() - review.REVIEW_MAX_MS).toISOString();
  const [rows] = await pool.execute(`SELECT ${PICK_COLS} FROM duel_picks WHERE settled_at >= ?`, [since]);
  const waiting = (rows || []).filter((p) => review.needsReview(p)
    && new Date(now).getTime() - Date.parse(p.settled_at) <= review.REVIEW_MAX_MS);
  if (!waiting.length) return [];
  const rounds = await loadRoundsSince(dayFloor(now, Math.ceil(review.REVIEW_MAX_MS / 86400000) + 2));
  const roundOf = new Map(rounds.map((r) => [String(r.id), r]));
  return waiting.map((p) => {
    const r = roundOf.get(String(p.round_id)) || null;
    const kind = r ? duel.kindOf(r) : null;
    return {
      pick_id: p.id,
      round_id: Number(p.round_id),
      day: r ? r.day : null,
      kind,
      symbol: r ? r.symbol : null,
      symbol_b: r && kind === 'mover' ? r.symbol_b : null,
      pick: p.pick,
      entry_price: p.entry_price == null ? null : Number(p.entry_price),
      settle_price: p.settle_price == null ? null : Number(p.settle_price),
      settle_price_b: p.settle_price_b == null ? null : Number(p.settle_price_b),
      second_price: p.settle_second_price == null ? null : Number(p.settle_second_price),
      outcome: r ? duel.callOutcome(r, p) : null,
      result: r ? duel.verdictOf(r, p).state : null,
      outcomes: kind ? duel.KINDS[kind].outcomes.slice() : [],
      disputed: p.settle_disputed == null ? null : Boolean(Number(p.settle_disputed)),
      flagged: Boolean(p.flagged_at),
      chips: p.chips == null ? null : Number(p.chips),
      settled_at: p.settled_at,
      settle_seal: p.settle_seal || null,
    };
  }).sort((a, b) => Date.parse(b.settled_at) - Date.parse(a.settled_at));
}

/**
 * The operator's decision on one queued settlement, sealed as a review
 * receipt (v9 duel_review) and written once.
 * → { ok: true, seal, decision, outcome } or { ok: false, status, reason, error }.
 */
async function writeReview(pickId, decision, rawOutcome, rawEvidence, now = new Date()) {
  if (!Number.isInteger(Number(pickId)) || Number(pickId) <= 0) return { ok: false, status: 400, reason: 'pick', error: 'Which call?' };
  const [rows] = await pool.execute(`SELECT ${PICK_COLS} FROM duel_picks WHERE id = ?`, [Number(pickId)]);
  const pick = (rows || [])[0] || null;
  let round = null;
  if (pick) {
    const rounds = await loadRoundsSince(dayFloor(Date.parse(pick.settled_at) || now, 3));
    round = rounds.find((r) => Number(r.id) === Number(pick.round_id)) || null;
  }
  const evidence = review.evidenceOf(rawEvidence);
  const check = review.checkDecision(round, pick, decision, rawOutcome, evidence, now);
  if (!check.ok) return check;
  const reviewed_at = new Date(now).toISOString();
  const receipt = sealDuelReview({
    round_id: round.id, call_seal: pick.seal, settle_seal: pick.settle_seal, round_seal: round.seal,
    kind: duel.kindOf(round), pick: pick.pick, decision, outcome: check.outcome, evidence, reviewed_at,
  });
  const [res] = await pool.execute(
    'UPDATE duel_picks SET review_state = ?, review_outcome = ?, review_seal = ?, review_payload = ?, reviewed_at = ?'
    + ' WHERE id = ? AND review_state IS NULL',
    [decision, check.outcome, receipt.seal, receipt.seal_payload, reviewed_at, pick.id]);
  if (!res || !res.affectedRows) return { ok: false, status: 409, reason: 'reviewed', error: 'That settlement has been reviewed already.' };
  return { ok: true, pick_id: pick.id, decision, outcome: check.outcome, seal: receipt.seal };
}

/** The player's record over the stated window. */
async function recordFor(userId, now = new Date()) {
  const floor = dayFloor(now, RECORD_WINDOW_DAYS);
  const [rounds, picks] = await Promise.all([loadRoundsSince(floor), loadPicks(userId)]);
  await settleDue(rounds, picks, now);
  const entries = duel.scoreEntries(rounds, picks);
  // Each entry's chips, read beside its verdict; an entry without chips has none.
  const roundOf = new Map(rounds.map((r) => [String(r.id), r]));
  const pickOf = new Map(picks.map((p) => [String(p.round_id), p]));
  for (const e of entries) {
    const c = chips.pickChips(pickOf.get(String(e.round_id)), roundOf.get(String(e.round_id)), now);
    if (c) e.chips = c;
  }
  const calibration = confidence.fromPicks(picks, entries);
  const record = {
    chips: await chipsBlock(userId, picks, now),
    window_days: RECORD_WINDOW_DAYS,
    accuracy: duel.accuracy(entries),
    marks: duel.computeMarks(entries),
    streak: duel.duelStreak(picks, now),
    quests: duel.weeklyDuelQuests(entries, now),
    entries,
  };
  // Off, and no sealed rating in the window: the record is the record it was.
  // A rating sealed while the flag was on is still counted after it is off.
  const confidencePublic = confidence.enabled();
  const confidenceOn = confidencePublic || await flag_on_for(
    { user: { user_id: userId } }, 'DUEL_CONFIDENCE_ENABLED');
  if (confidenceOn || calibration.scored > 0 || calibration.unscored > 0) {
    record.calibration = calibration;
  }
  const chipsPublic = chips.enabled();
  if ((!confidencePublic && confidenceOn) || (!chipsPublic && record.chips && record.chips.enabled)) {
    record.admin_preview = true;
  }
  return record;
}

module.exports = {
  secondReadings,
  RECORD_WINDOW_DAYS, SETTLE_BATCH, ROUND_COLS, PICK_COLS,
  dayFloor, parseRound, loadRoundsForDay, loadRoundsSince, loadPicks, sportsStateFor, writeSettlement,
  seasonAt, stackFor, chipsBlock, flagSettlement, reviewQueue, writeReview,
  ensureRounds, settleDue, handleFor, placePick, cardFor, recordFor,
  _resetSportsState: () => sportsStateByDay.clear(),
};
