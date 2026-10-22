'use strict';
/**
 * Daily Duel chips — the play-money layer on the same rounds
 * (docs/design/VIRTUAL_STAKES_DESIGN.md).
 *
 * A player holds a season float of CHIPS: a count with no exchange rate,
 * never bought, never sold, never transferred, never withdrawn, reset at the
 * start of every season (the duel's calendar month, UTC). With a call they may
 * PUT some chips on it, at odds fixed and sealed when the round was dealt. A
 * correct call pays chips × (odds − 1); a wrong one loses them; a push or an
 * unresolved round returns them. Off unless DUEL_CHIPS_ENABLED is set, and
 * off, nothing anywhere changes.
 *
 * This module is PURE, like lib/duel.js: no I/O, an injectable `now`. Nothing
 * here is stored either. A stack is recomputed on every read from the calls
 * themselves (the chips each carries, the odds its round was dealt at, the
 * verdict lib/duel.js gives it), the same posture as marks and accuracy. A
 * stored balance would be a second reading of the same quantity, and the two
 * would drift.
 *
 * THE ODDS NEVER READ THE STANCE. A classic round hides the Claw's call behind
 * a sealed nonce so nobody can read it before they call; odds that leaned
 * toward the Claw's side would be that stance in another font. The table below
 * is indexed by category and pick and by nothing else.
 *
 * NO CHIPS ON A PASS. A pass is never wrong (lib/duel.js scoreCall: it is
 * correct on a flat market and a push otherwise), so chips on it could only
 * win or come back: a free bet. A pass is still a call, for marks.
 *
 * HELD, NOT LOST. A settlement a second price source disputed (spec §2.6)
 * keeps its verdict and its marks; its chips are held for HOLD_MS, the
 * challenge window, then paid as the verdict stands. A held payout is shown
 * as held, never as won or lost.
 *
 * Counts only: no chip is a dollar or a token, and nothing on the trading path
 * reads one.
 */
const duel = require('./duel');

/** Chips every player starts a season with. */
const FLOAT = 1000;
/** The least and the most one call may carry. */
const MIN_PUT = 10;
const MAX_PUT = 200;
/** How long a disputed settlement's chips are held before they are paid. */
const HOLD_MS = 2 * 3600 * 1000;

/**
 * Decimal odds by category and pick, fixed for a season and tuned only between
 * seasons, from recorded results. A football round has none: the feed carries
 * no prices, and odds made up for a match would be a claim nobody measured, so
 * a match is dealt without chips (`odds: null`) and still called for marks.
 */
const ODDS_TABLE = Object.freeze({
  direction: Object.freeze({ long: 1.9, short: 1.9 }),
  backfade: Object.freeze({ back: 1.9, fade: 1.9 }),
  range: Object.freeze({ break: 2.2, hold: 1.7 }),
  mover: Object.freeze({ first: 1.9, second: 1.9 }),
});
/** Which table a round was dealt from, sealed with it. */
const ODDS_SOURCE = 'table-2026-10';

/** Whether the chips layer is on. */
function enabled(env = process.env) {
  return /^(1|true|yes|on)$/i.test(String(env.DUEL_CHIPS_ENABLED || '').trim());
}

/** The odds a round of `kind` is dealt at: `{ odds, source }`, or both null for none. */
function oddsFor(kind) {
  const t = ODDS_TABLE[kind];
  return t ? { odds: { ...t }, source: ODDS_SOURCE } : { odds: null, source: null };
}

/**
 * A stored round's odds, read: the map, or null when the round was dealt
 * without chips. A value that does not parse to positive odds is null too: a
 * round whose odds cannot be read takes no chips, rather than chips at a
 * number nobody can show.
 */
function oddsOf(round) {
  const raw = round && round.odds;
  if (raw == null || raw === '') return null;
  let o = raw;
  if (typeof raw === 'string') {
    try { o = JSON.parse(raw); } catch (e) { return null; }
  }
  if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
  const out = {};
  for (const [k, v] of Object.entries(o)) {
    const n = Number(v);
    if (!(Number.isFinite(n) && n > 1)) return null;
    out[k] = n;
  }
  return Object.keys(out).length ? out : null;
}

/**
 * Chips as a request carries them: null for none, an integer, or NaN for a
 * value that is not one. "No chips" and "zero chips" are different requests:
 * zero is refused as below the minimum, never read as none.
 */
function parseChips(raw) {
  if (raw === undefined || raw === null || raw === '') return null;
  const n = typeof raw === 'number' ? raw : Number(String(raw).trim());
  return Number.isInteger(n) ? n : NaN;
}

/**
 * May `chips` ride on this call? → { ok: true } or { ok: false, status, reason, error }.
 * `available` is what the stack can still put this season; leave it out to
 * check everything but the stack (the service checks that under a lock).
 */
function checkPut({ on, odds, kind, pick, chips, available }) {
  const no = (status, reason, error) => ({ ok: false, status, reason, error });
  if (chips === null) return { ok: true };
  if (!on) return no(400, 'chips_off', 'Chips are not part of the duel right now. Make the call without them.');
  if (Number.isNaN(chips)) return no(400, 'chips_invalid', 'Chips are a whole number.');
  if (!odds) {
    return no(400, 'no_odds', kind === 'sports'
      ? 'This match is dealt without chips. Make the call without them.'
      : 'This round was dealt without chips. Make the call without them.');
  }
  if (pick === 'pass') return no(400, 'chips_on_pass', 'A pass takes no chips. Pass without them, or pick a side.');
  if (!(pick in odds)) return no(400, 'no_odds', 'This pick has no odds. Make the call without chips.');
  if (chips < MIN_PUT) return no(400, 'chips_below_min', `Put at least ${MIN_PUT} chips, or none.`);
  if (chips > MAX_PUT) return no(400, 'chips_over_max', `A call takes at most ${MAX_PUT} chips.`);
  if (available === undefined) return { ok: true };
  if (!Number.isFinite(available)) return no(503, 'stack_unreadable', 'Your stack could not be read — your call was not recorded.');
  if (chips > available) {
    return no(409, 'chips_over_stack', available > 0
      ? `Your stack has ${available} chips left this season.`
      : 'Your stack is empty until the next season. Make the call without chips.');
  }
  return { ok: true };
}

/** The chips a verdict moves: + the winnings, − the chips, 0 when they come back. */
function payoutFor(chips, odds, result) {
  const c = Number(chips);
  if (!Number.isInteger(c) || c <= 0) return 0;
  if (result === 'correct') {
    const o = Number(odds);
    return Number.isFinite(o) && o > 1 ? Math.round(c * (o - 1)) : 0;
  }
  if (result === 'wrong') return -c;
  return 0;
}

/** When a settlement's chips are released: settled_at + HOLD_MS when disputed, else null. */
function heldUntil(settledAt, disputed) {
  if (disputed !== true) return null;
  const t = Date.parse(settledAt);
  return Number.isFinite(t) ? new Date(t + HOLD_MS).toISOString() : null;
}

function disputedOf(pick) {
  const d = pick && pick.settle_disputed;
  if (d == null) return null;
  return Boolean(Number(d));
}

/**
 * One call's chips, read:
 *   null                          no chips were put on it
 *   { state: 'unread' }           its round's odds cannot be read: no payout
 *   { state: 'open' }             not settled yet: the chips are on the table
 *   { state: 'held', held_until } disputed, inside the window
 *   { state: 'won' | 'lost' | 'returned', payout }
 * with `chips` and `odds` (the decimal the call was put at) on every one.
 */
function pickChips(pick, round, now = new Date()) {
  if (!pick || pick.chips == null) return null;
  const chips = Number(pick.chips);
  if (!Number.isInteger(chips) || chips <= 0) return null;
  const table = oddsOf(round);
  const odds = table && pick.pick in table ? table[pick.pick] : null;
  const base = { chips, odds };
  // Chips at odds that cannot be read have no payout anyone can compute:
  // unread, never a win paid at nothing or a loss taken at full.
  if (odds === null) return { ...base, state: 'unread', payout: null };
  if (pick.settle_state == null) return { ...base, state: 'open', payout: null };
  // The verdict as a review left it: a review releases a hold at once.
  const res = duel.verdictOf(round, pick);
  const payout = payoutFor(chips, odds, res.state);
  if (!pick.review_state) {
    // Held while disputed, or while flagged and not yet reviewed, until the
    // check window closes; then paid as the verdict stands.
    const flagged = pick.flagged_at ? true : null;
    const until = heldUntil(pick.settled_at, disputedOf(pick) === true || flagged === true ? true : null);
    if (until && Date.parse(until) > new Date(now).getTime()) {
      return { ...base, state: 'held', payout: null, held_until: until };
    }
  }
  return { ...base, state: payout > 0 ? 'won' : payout < 0 ? 'lost' : 'returned', payout };
}

/** The season ('YYYY-MM') a round belongs to: its own day's month. */
function seasonOfRound(round) {
  return String((round && round.day) || '').slice(0, 7);
}

/**
 * A player's stack for one season, from their calls and the rounds those were
 * made on. Calls whose round is not among `rounds`, or whose odds cannot be
 * read, are counted in `unread`: a stack computed over calls it could not see
 * is not the stack, so a surface says so beside it.
 *
 *   available   what may still be put: FLOAT + net − on_table
 *   on_table    chips on calls not yet paid (open and held)
 *   held        of those, chips held on a disputed settlement
 *   net         chips won minus chips lost, over paid calls only
 *   return_pct  net / FLOAT, a percent to one decimal; null with nothing paid
 */
function stackOf(rounds, picks, season, now = new Date()) {
  const byId = new Map((rounds || []).map((r) => [String(r.id), r]));
  let net = 0; let onTable = 0; let held = 0; let put = 0;
  let withChips = 0; let paid = 0; let open = 0; let unread = 0;
  for (const p of picks || []) {
    if (!p || p.chips == null) continue;
    const round = byId.get(String(p.round_id));
    if (!round) { unread++; continue; }
    if (seasonOfRound(round) !== season) continue;
    const c = pickChips(p, round, now);
    if (!c) continue;
    withChips++;
    put += c.chips;
    // Unread chips stay on the table: not available to put again, and in no total.
    if (c.state === 'unread') { onTable += c.chips; unread++; continue; }
    if (c.state === 'open') { onTable += c.chips; open++; continue; }
    if (c.state === 'held') { onTable += c.chips; held += c.chips; open++; continue; }
    net += c.payout;
    paid++;
  }
  return {
    season, float: FLOAT,
    available: FLOAT + net - onTable,
    on_table: onTable, held, net, put,
    calls_with_chips: withChips, paid, open, unread,
    return_pct: paid > 0 ? Math.round((net / FLOAT) * 1000) / 10 : null,
  };
}

/** The limits and the table, for a surface to show before a player commits. */
function rules() {
  return { float: FLOAT, min: MIN_PUT, max: MAX_PUT, hold_hours: HOLD_MS / 3600000, odds_source: ODDS_SOURCE };
}

module.exports = {
  FLOAT, MIN_PUT, MAX_PUT, HOLD_MS, ODDS_TABLE, ODDS_SOURCE,
  enabled, oddsFor, oddsOf, parseChips, checkPut, payoutFor, heldUntil, pickChips,
  seasonOfRound, stackOf, rules,
};
