'use strict';
/**
 * Provable Calls receipts for the Daily Duel: the three kinds a reader can
 * ask /api/call/:key for, by the seal itself.
 *
 *   round:<seal>   the round as dealt (v6). The seal is public from the
 *                  moment the card is; the payload, which carries the Claw's
 *                  stance on a classic round, opens once the round has closed
 *                  for everyone (a player sees their own round's payload on
 *                  the card the moment they have called).
 *   duel:<seal>    one call (v4), with the round seal it was made under and
 *                  the settlement receipt once there is one.
 *   settle:<seal>  one settlement (v7): the prices or the score, the verdict,
 *                  and the call seal it settles.
 *
 * With chips (v9, lib/duel_chips.js) each carries its chips fact beside the
 * rest: the round its odds, the call its chips and the odds they were put at,
 * the settlement the chips it moved. A call without chips names none: the
 * field is absent, never a zero.
 *
 *   review:<seal>  the operator's decision on a flagged or disputed
 *                  settlement (v9 duel_review), naming the settlement, the
 *                  call and the round; the settlement's own receipt is never
 *                  rewritten, and names its review once there is one.
 *
 * A seal is its own address: sixty-four hex characters nobody can guess
 * from public data, as §3 asks. §4: prices, a score, states and counts;
 * the handle a player opted into; never an amount.
 *
 * Honesty: a key that matches nothing is a 404 with a sentence, never an
 * empty receipt; a round whose payload is still closed says so and when it
 * opens, and sends no payload at all rather than a blank one.
 */
const { pool } = require('../db');
const duel = require('./duel');
const { ROUND_COLS, PICK_COLS } = require('./duel_service');
const { anchorFor } = require('./seal_roots');
const chips = require('./duel_chips');

const SEAL_RE = /^[0-9a-f]{64}$/;

async function anchorOf(seal, sealedAt) {
  try { return await anchorFor(seal, sealedAt); } catch (e) { return null; }
}

async function roundBySeal(seal) {
  const [rows] = await pool.execute(`SELECT ${ROUND_COLS} FROM duel_rounds WHERE seal = ?`, [seal]);
  return rows[0] || null;
}
async function roundById(id) {
  const [rows] = await pool.execute(`SELECT ${ROUND_COLS} FROM duel_rounds WHERE day >= ?`, ['0000-00-00']);
  return rows.find((r) => Number(r.id) === Number(id)) || null;
}
async function pickBySeal(seal) {
  const [rows] = await pool.execute(`SELECT ${PICK_COLS} FROM duel_picks WHERE seal = ?`, [seal]);
  return rows[0] || null;
}
async function pickByReviewSeal(seal) {
  const [rows] = await pool.execute(`SELECT ${PICK_COLS} FROM duel_picks WHERE review_seal = ?`, [seal]);
  return rows[0] || null;
}
async function pickBySettleSeal(seal) {
  const [rows] = await pool.execute(`SELECT ${PICK_COLS} FROM duel_picks WHERE settle_seal = ?`, [seal]);
  return rows[0] || null;
}

/** The end of a round's UTC day, when a closed classic round opens its payload to everyone. */
function opensAt(round) {
  return new Date(duel.dayStartMs(round.day) + 24 * 3600000).toISOString();
}

/** Whether this viewer has called this round: their own card opens its payload now. */
async function viewerCalled(userId, roundId) {
  if (!userId) return false;
  const [rows] = await pool.execute('SELECT id, round_id FROM duel_picks WHERE user_id = ?', [userId]);
  return (rows || []).some((p) => Number(p.round_id) === Number(roundId));
}

/** The v6 receipt of a dealt round, opened for the viewer who has called it. */
async function roundReceipt(round, now, viewerHasCalled = false) {
  const kind = duel.kindOf(round);
  const revealed = duel.roundSealRevealed(round, { revealAgent: viewerHasCalled, now });
  const current = {
    day: round.day, idx: Number(round.idx), category: kind, symbol: round.symbol,
    symbol_b: round.symbol_b == null ? null : round.symbol_b,
    threshold_pct: round.threshold_pct == null ? null : Number(round.threshold_pct),
  };
  if (revealed) current.agent_direction = round.agent_direction == null ? null : String(round.agent_direction);
  // A round dealt under the chips layer names its odds, or null for one
  // dealt without; a round from before the layer carries no such field.
  if (round.odds_source != null || round.odds != null) current.odds = chips.oddsOf(round);
  const body = {
    kind: 'duel_round',
    seal: round.seal,
    sealed_at: round.created_at,
    anchor: await anchorOf(round.seal, round.created_at),
    revealed,
    current,
    proves: 'that this round, the Claw’s own call included, was fixed when the card was dealt and before anyone called it: sha256 over the opened payload is the seal that was on the card.',
  };
  if (revealed) body.seal_payload = round.seal_payload;
  else {
    body.locked = true;
    body.opens_at = opensAt(round);
    body.note = 'The payload carries the Claw’s call on this round and opens for everyone when the round’s day ends. A player who has called this round, signed in, sees it now.';
  }
  return body;
}

/** A settlement's chips as they stand now: what was put, what moved, and whether it is held. */
function settleChips(pick, round) {
  const c = round ? chips.pickChips(pick, round) : null;
  if (!c) return {};
  // `paid`, not `payout`: the sealed payout is what the verdict gives; what
  // has been paid is null while the chips are held, and naming both alike
  // would read a held settlement as a receipt that drifted.
  return { chips: c.chips, chips_state: c.state, paid: c.payout, ...(c.held_until ? { held_until: c.held_until } : {}) };
}

/** The v7 receipt of a settlement (v9 with chips). */
async function settleReceipt(pick, round) {
  return {
    kind: 'duel_settle',
    seal: pick.settle_seal,
    seal_payload: pick.settle_payload,
    sealed_at: pick.settled_at,
    anchor: await anchorOf(pick.settle_seal, pick.settled_at),
    call: { key: 'duel:' + pick.seal, seal: pick.seal },
    round: round && round.seal ? { key: 'round:' + round.seal, seal: round.seal } : null,
    // Attached, never merged: the review is a receipt of its own.
    review: pick.review_seal ? { key: 'review:' + pick.review_seal, seal: pick.review_seal, decision: pick.review_state } : null,
    flagged: Boolean(pick.flagged_at),
    current: {
      settle_state: pick.settle_state,
      settle_price: pick.settle_price == null ? null : Number(pick.settle_price),
      settle_price_b: pick.settle_price_b == null ? null : Number(pick.settle_price_b),
      settled_at: pick.settled_at,
      second_price: pick.settle_second_price == null ? null : Number(pick.settle_second_price),
      second_price_b: pick.settle_second_price_b == null ? null : Number(pick.settle_second_price_b),
      disputed: pick.settle_disputed == null ? null : Boolean(Number(pick.settle_disputed)),
      ...settleChips(pick, round),
    },
    proves: 'that this settlement, prices (or score) and verdict, was written at this time over the call whose seal it names.',
  };
}

/** The v9 receipt of a review. */
async function reviewReceipt(pick, round) {
  return {
    kind: 'duel_review',
    seal: pick.review_seal,
    seal_payload: pick.review_payload,
    sealed_at: pick.reviewed_at,
    anchor: await anchorOf(pick.review_seal, pick.reviewed_at),
    settlement: pick.settle_seal ? { key: 'settle:' + pick.settle_seal, seal: pick.settle_seal } : null,
    call: { key: 'duel:' + pick.seal, seal: pick.seal },
    round: round && round.seal ? { key: 'round:' + round.seal, seal: round.seal } : null,
    current: { decision: pick.review_state, outcome: pick.review_outcome || null, reviewed_at: pick.reviewed_at },
    proves: 'that this decision on the settlement it names was written at this time, and that the settlement itself was left as it was sealed.',
  };
}

/** The v4 receipt of a call, with its round and its settlement attached. */
async function callReceipt(pick, round) {
  const kind = round ? duel.kindOf(round) : 'direction';
  const current = {
    round_id: Number(pick.round_id),
    category: kind,
    pick: pick.pick,
    entry_price: pick.entry_price == null ? null : Number(pick.entry_price),
    entry_price_b: pick.entry_price_b == null ? null : Number(pick.entry_price_b),
    resolves_at: pick.resolves_at,
    picked_at: pick.created_at,
  };
  if (round) {
    current.day = round.day;
    current.symbol = round.symbol;
    current.symbol_b = round.symbol_b == null ? null : round.symbol_b;
    current.threshold_pct = round.threshold_pct == null ? null : Number(round.threshold_pct);
  }
  if (pick.chips != null) {
    const table = round ? chips.oddsOf(round) : null;
    current.chips = Number(pick.chips);
    current.odds = table && pick.pick in table ? table[pick.pick] : null;
  }
  return {
    kind: 'duel_pick',
    seal: pick.seal,
    seal_payload: pick.seal_payload,
    sealed_at: pick.created_at,
    anchor: await anchorOf(pick.seal, pick.created_at),
    current,
    round: round && round.seal ? { key: 'round:' + round.seal, seal: round.seal } : null,
    // Outcomes are attached, never merged into the payload (spec §5.6): the
    // settlement is its own receipt, named here once it exists.
    settlement: pick.settle_seal ? { key: 'settle:' + pick.settle_seal, seal: pick.settle_seal, state: pick.settle_state } : null,
    proves: 'that this call, at this price and for this horizon, was made at this time and before the market answered.',
  };
}

/**
 * → { code: 200, body } | { code: 404, body: { error } } for a duel key, or
 * null when the key is not a duel key at all.
 */
async function lookupDuel(key, { now = new Date(), userId = null } = {}) {
  const m = /^(round|duel|settle|review):([0-9a-f]{64})$/.exec(String(key || ''));
  if (!m) return null;
  const [, form, seal] = m;
  if (!SEAL_RE.test(seal)) return { code: 400, body: { error: 'Invalid call id' } };
  if (form === 'round') {
    const round = await roundBySeal(seal);
    if (!round) return { code: 404, body: { error: 'No sealed round with that hash — rounds carry a seal since Provable Calls v6 (2026-10-19).' } };
    return { code: 200, body: await roundReceipt(round, now, await viewerCalled(userId, round.id)) };
  }
  if (form === 'duel') {
    const pick = await pickBySeal(seal);
    if (!pick || !pick.seal_payload) return { code: 404, body: { error: 'No sealed duel call with that hash.' } };
    const round = await roundById(pick.round_id);
    return { code: 200, body: await callReceipt(pick, round) };
  }
  if (form === 'review') {
    const rp = await pickByReviewSeal(seal);
    if (!rp || !rp.review_payload) return { code: 404, body: { error: 'No sealed review with that hash.' } };
    return { code: 200, body: await reviewReceipt(rp, await roundById(rp.round_id)) };
  }
  const pick = await pickBySettleSeal(seal);
  if (!pick || !pick.settle_payload) return { code: 404, body: { error: 'No sealed settlement with that hash — settlements carry a seal since Provable Calls v7 (2026-10-19).' } };
  const round = await roundById(pick.round_id);
  return { code: 200, body: await settleReceipt(pick, round) };
}

module.exports = { lookupDuel, roundReceipt, callReceipt, settleReceipt, reviewReceipt, SEAL_RE };
