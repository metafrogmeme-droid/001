'use strict';
/**
 * Chips on the Daily Duel (lib/duel_chips.js, docs/design/VIRTUAL_STAKES_DESIGN.md):
 * a play-money count a season hands every player, put with a call at odds
 * sealed when the round was dealt, paid at settlement from the verdict the
 * rules already give the call.
 *
 * Every rule is driven both ways at its boundary: the least and the most a
 * call may carry, the stack it may not exceed, the pass that takes none, the
 * round dealt without odds, the layer switched off. A season is driven end to
 * end through the service: dealt with odds, called with chips, settled, held
 * while disputed and paid when the window closes, and at every step the stack
 * is the float plus what was paid minus what is on the table. The odds never
 * read the Claw's hidden stance. A stack is never stored: it is recomputed
 * from the calls, the same posture as marks.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.DUEL_CHIPS_ENABLED;
delete process.env.FLOVIA_KEY;
delete process.env.FOOTBALL_DATA_TOKEN;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const chips = require('../lib/duel_chips');
const svc = require('../lib/duel_service');
const duel = require('../lib/duel');
const receipts = require('../lib/duel_receipts');
const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher, hourStart } = require('../lib/candles');
const { pool } = require('../db');

const sha256 = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const H = 3600 * 1000;
const PRICES = {
  BTCUSDT: { price: 60000, volume: 900 },
  ETHUSDT: { price: 3000, volume: 800 },
  SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 },
  XRPUSDT: { price: 0.5, volume: 500 },
  ARBUSDT: { price: 1.2, volume: 50 },
};
// Closes by symbol, for the candle stand-in; nothing settles unless a test says so.
let CLOSES = {};
setTickerFetcher(async () => PRICES);
setCandleFetcher(async (symbol, startMs) => (CLOSES[symbol] == null ? []
  : [[String(hourStart(startMs)), '1', '1', '1', String(CLOSES[symbol]), '1', '1']]));
test.after(() => { setTickerFetcher(null); setCandleFetcher(null); delete process.env.DUEL_CHIPS_ENABLED; });

// The card's day: the 10th of this month, at 09:30 UTC. Its season is this month.
const MONTH = new Date().toISOString().slice(0, 7);
const DAY = `${MONTH}-10`;
const NOON = Date.parse(`${DAY}T09:30:00Z`);
let nextUser = 500;
const newPlayer = () => nextUser++;

// ── the rules, alone ────────────────────────────────────────────────────────

test('a call may carry between the least and the most, never more than the stack, and none on a pass', () => {
  const odds = { long: 1.9, short: 1.9 };
  const at = (c, extra = {}) => chips.checkPut({ on: true, odds, kind: 'direction', pick: 'long', chips: c, available: 1000, ...extra });
  assert.deepStrictEqual(at(null), { ok: true }, 'no chips is always a call');
  assert.deepStrictEqual(at(chips.MIN_PUT), { ok: true });
  assert.strictEqual(at(chips.MIN_PUT - 1).reason, 'chips_below_min');
  assert.strictEqual(at(0).reason, 'chips_below_min', 'zero is not "none": it is refused, never read as no chips');
  assert.deepStrictEqual(at(chips.MAX_PUT), { ok: true });
  assert.strictEqual(at(chips.MAX_PUT + 1).reason, 'chips_over_max');
  assert.strictEqual(at(NaN).reason, 'chips_invalid');
  assert.deepStrictEqual(at(150, { available: 150 }), { ok: true }, 'the whole stack may go');
  const over = at(151, { available: 150 });
  assert.strictEqual(over.reason, 'chips_over_stack');
  assert.strictEqual(over.status, 409);
  assert.match(over.error, /150 chips left/);
  assert.match(at(10, { available: 0 }).error, /empty until the next season/);
  assert.strictEqual(at(10, { available: NaN }).status, 503, 'a stack that cannot be read refuses the put');
  assert.strictEqual(at(50, { pick: 'pass' }).reason, 'chips_on_pass', 'a pass is never wrong, so chips on it would be a free bet');
  assert.strictEqual(at(50, { odds: null }).reason, 'no_odds');
  assert.match(at(50, { odds: null, kind: 'sports' }).error, /match is dealt without chips/);
  assert.strictEqual(at(50, { on: false }).reason, 'chips_off');
  assert.deepStrictEqual(at(null, { on: false }), { ok: true }, 'off, a call without chips is what it always was');
  assert.deepStrictEqual(chips.checkPut({ on: true, odds, kind: 'direction', pick: 'long', chips: 50 }), { ok: true },
    'without a stack, everything else is checked');
  assert.deepStrictEqual([chips.parseChips(undefined), chips.parseChips(''), chips.parseChips('50'), chips.parseChips(50)],
    [null, null, 50, 50]);
  assert.ok(Number.isNaN(chips.parseChips('5.5')) && Number.isNaN(chips.parseChips('fifty')));
});

test('a verdict pays chips × (odds − 1), takes the chips, or gives them back', () => {
  assert.strictEqual(chips.payoutFor(50, 1.9, 'correct'), 45);
  assert.strictEqual(chips.payoutFor(50, 2.2, 'correct'), 60);
  assert.strictEqual(chips.payoutFor(50, 1.7, 'correct'), 35);
  assert.strictEqual(chips.payoutFor(50, 1.9, 'wrong'), -50);
  for (const r of ['push', 'unresolved', 'invalid']) assert.strictEqual(chips.payoutFor(50, 1.9, r), 0, r);
  assert.strictEqual(chips.heldUntil('2026-10-10T12:00:00.000Z', true), '2026-10-10T14:00:00.000Z');
  assert.strictEqual(chips.heldUntil('2026-10-10T12:00:00.000Z', false), null, 'agreement holds nothing');
  assert.strictEqual(chips.heldUntil('2026-10-10T12:00:00.000Z', null), null, 'unchecked holds nothing');
});

test('the odds are the category\'s table and nothing else: a round is never dealt at the Claw\'s side', () => {
  for (const kind of ['direction', 'backfade', 'range', 'mover']) {
    const o = chips.oddsFor(kind);
    assert.deepStrictEqual(o.odds, { ...chips.ODDS_TABLE[kind] });
    assert.strictEqual(o.source, chips.ODDS_SOURCE);
    for (const p of duel.picksFor(kind)) {
      if (p === 'pass') assert.ok(!(p in o.odds), 'no odds on a pass');
      else assert.ok(o.odds[p] > 1, `${kind}/${p} has odds`);
    }
  }
  assert.deepStrictEqual(chips.oddsFor('sports'), { odds: null, source: null }, 'the feed prices no match, so none is made up');
  assert.strictEqual(chips.oddsOf({ odds: '{"long":1.9,"short":0}' }), null, 'odds that cannot pay are no odds');
  assert.strictEqual(chips.oddsOf({ odds: '{"long":1.9,"short":1}' }), null, 'odds of 1 pay nothing on a win: no odds either');
  assert.strictEqual(chips.oddsOf({ odds: '{"long":1.9,"short":0.5}' }), null);
  assert.strictEqual(chips.oddsOf({ odds: 'not json' }), null);
  assert.deepStrictEqual(chips.oddsOf({ odds: '{"long":1.9,"short":1.9}' }), { long: 1.9, short: 1.9 });
});

test('a stack is the float, plus what was paid, minus what is on the table, for its own season only', () => {
  const now = Date.parse(`${MONTH}-20T12:00:00Z`);
  const rd = (id, day) => ({ id, day, kind: 'direction', agent_direction: 'long', odds: '{"long":1.9,"short":1.9}' });
  const rounds = [rd(1, `${MONTH}-02`), rd(2, `${MONTH}-03`), rd(3, `${MONTH}-04`), rd(4, `${MONTH}-05`), rd(5, '2000-01-05'),
    { ...rd(6, `${MONTH}-06`), odds: null }];
  const settled = (round_id, chipsPut, entry, close, extra = {}) => ({ round_id, pick: 'long', chips: chipsPut, entry_price: entry,
    settle_price: close, settle_state: 'settled', settled_at: `${MONTH}-15T00:00:00.000Z`, ...extra });
  const picks = [
    settled(1, 100, 100, 110),                       // won 90
    settled(2, 50, 100, 90),                         // lost 50
    { round_id: 3, pick: 'long', chips: 200, entry_price: 100, settle_state: null },   // open
    settled(4, 30, 100, 110, { settle_disputed: 1, settled_at: new Date(now - 1 * H).toISOString() }),  // held
    settled(5, 200, 100, 50),                        // another season: not this stack
    settled(6, 40, 100, 110),                        // its odds cannot be read
    { round_id: 99, pick: 'long', chips: 10 },       // its round was not read
    settled(1, null, 100, 110),                      // no chips: not in the stack at all
  ];
  const s = chips.stackOf(rounds, picks, MONTH, now);
  assert.deepStrictEqual({ net: s.net, on_table: s.on_table, held: s.held, available: s.available },
    { net: 40, on_table: 270, held: 30, available: 1000 + 40 - 270 });
  assert.strictEqual(s.paid, 2);
  assert.strictEqual(s.unread, 2, 'unreadable odds and an unread round are counted, never folded in');
  assert.strictEqual(s.return_pct, 4);
  assert.strictEqual(chips.stackOf([], [], MONTH, now).return_pct, null, 'nothing paid is no return, not 0%');
  // The window closes: the held call is paid as its verdict stands.
  const later = chips.stackOf(rounds, picks, MONTH, now + 2 * H);
  assert.deepStrictEqual({ net: later.net, held: later.held, available: later.available },
    { net: 40 + 27, held: 0, available: 1000 + 67 - 240 });
});

test('one call\'s chips read open, held, won, lost or returned, and unread when its odds are not there', () => {
  const r = { id: 1, day: DAY, kind: 'direction', agent_direction: 'short', odds: '{"long":1.9,"short":1.9}' };
  const base = { round_id: 1, pick: 'long', chips: 60, entry_price: 100, settled_at: '2026-10-10T12:00:00.000Z' };
  const at = Date.parse('2026-10-10T13:00:00Z');
  assert.strictEqual(chips.pickChips({ ...base, chips: null }, r, at), null);
  assert.deepStrictEqual(chips.pickChips({ ...base, settle_state: null }, r, at), { chips: 60, odds: 1.9, state: 'open', payout: null });
  const won = { ...base, settle_state: 'settled', settle_price: 110 };
  assert.deepStrictEqual(chips.pickChips(won, r, at), { chips: 60, odds: 1.9, state: 'won', payout: 54 });
  assert.deepStrictEqual(chips.pickChips({ ...won, settle_price: 90 }, r, at), { chips: 60, odds: 1.9, state: 'lost', payout: -60 });
  assert.strictEqual(chips.pickChips({ ...won, settle_price: 100 }, r, at).state, 'returned', 'a flat market is a push');
  assert.strictEqual(chips.pickChips({ ...won, settle_state: 'unresolved', settle_price: null }, r, at).state, 'returned');
  const held = chips.pickChips({ ...won, settle_disputed: 1 }, r, at);
  assert.deepStrictEqual(held, { chips: 60, odds: 1.9, state: 'held', payout: null, held_until: '2026-10-10T14:00:00.000Z' });
  assert.strictEqual(chips.pickChips({ ...won, settle_disputed: 1 }, r, Date.parse('2026-10-10T14:00:01Z')).state, 'won');
  assert.strictEqual(chips.pickChips({ ...won, settle_disputed: 0 }, r, at).state, 'won', 'agreement holds nothing');
  assert.strictEqual(chips.pickChips(won, { ...r, odds: null }, at).state, 'unread');
});

// ── the season, through the service ─────────────────────────────────────────

test('off, the duel is what it was: rounds carry no odds, a call without chips is v4, chips are refused', async () => {
  delete process.env.DUEL_CHIPS_ENABLED;
  const day = `${MONTH}-08`;
  const now = Date.parse(`${day}T09:30:00Z`);
  const rounds = await svc.ensureRounds(day, new Date(now));
  for (const r of rounds) {
    assert.strictEqual(r.odds, null);
    assert.strictEqual(JSON.parse(r.seal_payload).v, 6);
  }
  const who = newPlayer();
  const card = await svc.cardFor(who, new Date(now));
  assert.strictEqual(card.chips, null, 'no chips block for a game the player was never offered');
  assert.ok(card.rounds.every((r) => !('odds' in r)));
  const refused = await svc.placePick(who, rounds[0].id, 'long', new Date(now), { chips: 50 });
  assert.deepStrictEqual([refused.ok, refused.reason], [false, 'chips_off']);
  const plain = await svc.placePick(who, rounds[0].id, 'long', new Date(now));
  assert.strictEqual(plain.ok, true);
  assert.strictEqual(plain.chips, undefined);
  const row = pool.duelPicks.find((p) => p.seal === plain.seal);
  assert.strictEqual(row.chips, null);
  assert.strictEqual(JSON.parse(row.seal_payload).v, 4);
});

test('on, a season runs: dealt with odds, called with chips, settled at the odds, held while disputed', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const rounds = await svc.ensureRounds(DAY, new Date(NOON));
  const byKind = (k) => rounds.find((r) => duel.kindOf(r) === k);
  for (const r of rounds) {
    const payload = JSON.parse(r.seal_payload);
    assert.strictEqual(payload.v, 9, 'the round is sealed with its odds');
    assert.deepStrictEqual(payload.odds, chips.oddsFor(duel.kindOf(r)).odds);
    assert.strictEqual(payload.odds_source, chips.ODDS_SOURCE);
    assert.strictEqual(sha256(r.seal_payload), r.seal);
    assert.deepStrictEqual(chips.oddsOf(r), chips.oddsFor(duel.kindOf(r)).odds);
  }
  const who = newPlayer();
  const card = await svc.cardFor(who, new Date(NOON));
  assert.deepStrictEqual(card.chips.stack.available, chips.FLOAT);
  assert.deepStrictEqual([card.chips.min, card.chips.max, card.chips.float, card.chips.enabled], [10, 200, 1000, true]);
  assert.deepStrictEqual(card.rounds[0].odds, { long: 1.9, short: 1.9 });

  // Put 100 on LONG on the first classic round, 50 on HOLDS on the range round.
  const dir = byKind('direction');
  const range = byKind('range');
  const a = await svc.placePick(who, dir.id, 'long', new Date(NOON), { chips: 100 });
  assert.deepStrictEqual([a.ok, a.chips, a.odds], [true, 100, 1.9]);
  const callRow = pool.duelPicks.find((p) => p.seal === a.seal);
  const callPayload = JSON.parse(callRow.seal_payload);
  assert.deepStrictEqual([callPayload.v, callPayload.kind, callPayload.chips, callPayload.odds], [9, 'duel_pick', 100, 1.9]);
  assert.ok(!('result' in callPayload) && !('payout' in callPayload), 'a call receipt carries no outcome (spec §5 rule 2)');
  assert.strictEqual(sha256(callRow.seal_payload), callRow.seal);
  const b = await svc.placePick(who, range.id, 'hold', new Date(NOON + 60000), { chips: 50 });
  assert.deepStrictEqual([b.ok, b.odds], [true, 1.7]);
  let stack = await svc.stackFor(who, new Date(NOON + 120000));
  assert.deepStrictEqual([stack.available, stack.on_table, stack.net], [850, 150, 0]);

  // A pass takes none, and the refusal records nothing.
  const pass = await svc.placePick(who, byKind('mover').id, 'pass', new Date(NOON), { chips: 20 });
  assert.deepStrictEqual([pass.ok, pass.reason, pass.status], [false, 'chips_on_pass', 400]);
  assert.ok(!pool.duelPicks.some((p) => p.user_id === who && Number(p.round_id) === Number(byKind('mover').id)),
    'a refused put records no call');
  const again = await svc.placePick(who, dir.id, 'short', new Date(NOON), { chips: 10 });
  assert.deepStrictEqual([again.ok, again.status], [false, 409], 'a call is still write-once');

  // The card shows each call's chips; the board the player sees is theirs.
  const seen = await svc.cardFor(who, new Date(NOON + 120000));
  const mine = seen.rounds.find((r) => Number(r.id) === Number(dir.id)).my_call.chips;
  assert.deepStrictEqual(mine, { chips: 100, odds: 1.9, state: 'open', payout: null });
  assert.strictEqual(seen.chips.stack.available, 850);

  // Settlement: BTC up 10% (the LONG is right), BNB up 1% (inside ±2%: HOLD is right).
  CLOSES = { [dir.symbol]: PRICES[dir.symbol].price * 1.1, [range.symbol]: PRICES[range.symbol].price * 1.01 };
  const after = NOON + 25 * H;
  const picks = await svc.loadPicks(who);
  await svc.settleDue(rounds, picks, new Date(after));
  const settledDir = pool.duelPicks.find((p) => p.seal === a.seal);
  const settle = JSON.parse(settledDir.settle_payload);
  assert.deepStrictEqual([settle.v, settle.result, settle.chips, settle.odds_used, settle.payout, settle.held_until],
    [9, 'correct', 100, 1.9, 90, null]);
  assert.strictEqual(sha256(settledDir.settle_payload), settledDir.settle_seal);
  stack = await svc.stackFor(who, new Date(after));
  assert.deepStrictEqual([stack.net, stack.on_table, stack.available], [90 + 35, 0, 1000 + 125]);
  assert.strictEqual(stack.return_pct, 12.5);
  CLOSES = {};
});

test('a disputed settlement holds its chips for the window, then pays as the verdict stands', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const day = `${MONTH}-12`;
  const now = Date.parse(`${day}T09:30:00Z`);
  const rounds = await svc.ensureRounds(day, new Date(now));
  const dir = rounds.find((r) => duel.kindOf(r) === 'direction');
  const who = newPlayer();
  const put = await svc.placePick(who, dir.id, 'short', new Date(now), { chips: 80 });
  assert.strictEqual(put.ok, true);
  const p = pool.duelPicks.find((x) => x.seal === put.seal);
  const settledAt = now + 25 * H;
  // The market fell 5% (SHORT is right), and a second source disagreed.
  await svc.writeSettlement(dir, p, 'settled', Number(p.entry_price) * 0.95, null, new Date(settledAt),
    { second: { read: true, provider: 'CoinGecko', price: Number(p.entry_price), source_at: new Date(settledAt).toISOString() },
      second_b: null, disputed: true });
  const settle = JSON.parse(p.settle_payload);
  assert.deepStrictEqual([settle.result, settle.disputed, settle.payout, settle.held_until],
    ['correct', true, 72, new Date(settledAt + chips.HOLD_MS).toISOString()]);
  const during = await svc.stackFor(who, new Date(settledAt + chips.HOLD_MS - 1000));
  assert.deepStrictEqual([during.held, during.net, during.available], [80, 0, 1000 - 80], 'held: neither won nor lost');
  const rec = await svc.recordFor(who, new Date(settledAt + 60000));
  const entry = rec.entries.find((e) => Number(e.round_id) === Number(dir.id));
  assert.deepStrictEqual(entry.chips, { chips: 80, odds: 1.9, state: 'held', payout: null, held_until: settle.held_until });
  assert.strictEqual(entry.state, 'correct', 'the verdict and its marks stand while the chips are held');
  const released = await svc.stackFor(who, new Date(settledAt + chips.HOLD_MS + 1000));
  assert.deepStrictEqual([released.held, released.net, released.available], [0, 72, 1072]);
});

test('the stack holds: a call that would spend more than is left is refused, and the last chips may go', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const day = `${MONTH}-14`;
  const now = Date.parse(`${day}T09:30:00Z`);
  const rounds = (await svc.ensureRounds(day, new Date(now))).filter((r) => chips.oddsOf(r));
  assert.ok(rounds.length >= 5, 'the fixture deals five rounds with odds');
  const who = newPlayer();
  // Four open calls of 200 leave 200.
  const pickOf = (r) => duel.picksFor(duel.kindOf(r))[0];
  for (const r of rounds.slice(0, 4)) {
    const out = await svc.placePick(who, r.id, pickOf(r), new Date(now), { chips: 200 });
    assert.strictEqual(out.ok, true, JSON.stringify(out));
  }
  const over = await svc.placePick(who, rounds[4].id, pickOf(rounds[4]), new Date(now), { chips: 200 + 0 });
  assert.strictEqual(over.ok, true, 'the last 200 may go');
  const empty = await svc.stackFor(who, new Date(now));
  assert.strictEqual(empty.available, 0);
  // A sixth round with odds, planted on the same day, cannot take a chip.
  const sixth = { day, idx: 99, kind: 'direction', symbol: 'BTCUSDT', agent_direction: null, odds: JSON.stringify({ long: 1.9, short: 1.9 }),
    odds_source: chips.ODDS_SOURCE, seal: null, seal_payload: null, created_at: new Date(now).toISOString(), id: 90000 };
  pool.duelRounds.push(sixth);
  const none = await svc.placePick(who, sixth.id, 'long', new Date(now), { chips: 10 });
  assert.deepStrictEqual([none.ok, none.reason, none.status], [false, 'chips_over_stack', 409]);
  assert.match(none.error, /empty until the next season/);
  const plain = await svc.placePick(who, sixth.id, 'long', new Date(now));
  assert.strictEqual(plain.ok, true, 'an empty stack still calls, for marks');
});

test('the receipts carry the chips: the round its odds, the call its chips, the settlement its payout', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const day = `${MONTH}-16`;
  const now = Date.parse(`${day}T09:30:00Z`);
  const rounds = await svc.ensureRounds(day, new Date(now));
  const dir = rounds.find((r) => duel.kindOf(r) === 'direction');
  const who = newPlayer();
  const put = await svc.placePick(who, dir.id, 'long', new Date(now), { chips: 40 });
  const call = await receipts.lookupDuel('duel:' + put.seal, { now: new Date(now) });
  assert.strictEqual(call.code, 200);
  assert.strictEqual(call.body.current.chips, 40);
  assert.strictEqual(call.body.current.odds, 1.9);
  assert.strictEqual(JSON.parse(call.body.seal_payload).chips, 40);
  const round = await receipts.lookupDuel('round:' + dir.seal, { now: new Date(now + 2 * 86400000) });
  assert.deepStrictEqual(round.body.current.odds, { long: 1.9, short: 1.9 });
  const p = pool.duelPicks.find((x) => x.seal === put.seal);
  await svc.writeSettlement(dir, p, 'settled', Number(p.entry_price) * 0.9, null, new Date(now + 25 * H), null);
  const settle = await receipts.lookupDuel('settle:' + p.settle_seal, { now: new Date(now + 26 * H) });
  assert.deepStrictEqual([settle.body.current.chips, settle.body.current.paid, settle.body.current.chips_state], [40, -40, 'lost']);
  assert.strictEqual(JSON.parse(settle.body.seal_payload).payout, -40);
  // A call without chips names none: absent, not zero.
  const other = newPlayer();
  const plain = await svc.placePick(other, dir.id, 'short', new Date(now));
  const plainReceipt = await receipts.lookupDuel('duel:' + plain.seal, { now: new Date(now) });
  assert.ok(!('chips' in plainReceipt.body.current));
});

test('the one column list carries every column the duel tables have, so no reader loses one', () => {
  const db = require('../db');
  const cols = (list) => new Set(list.split(',').map((c) => c.trim()));
  const pickCols = cols(svc.PICK_COLS);
  const roundCols = cols(svc.ROUND_COLS);
  for (const c of Object.keys(db.REQUIRED_DUEL_PICK_COLUMNS)) assert.ok(pickCols.has(c), `PICK_COLS lacks ${c}`);
  for (const c of Object.keys(db.REQUIRED_DUEL_ROUND_COLUMNS)) assert.ok(roundCols.has(c), `ROUND_COLS lacks ${c}`);
  for (const c of ['settle_disputed', 'settle_second_price', 'settle_second_price_b', 'chips']) assert.ok(pickCols.has(c), c);
});

test('the public board carries a percent and a count of calls with chips, and not one chip', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const http = require('node:http');
  const express = require('express');
  const publicDuel = require('../routes/public_duel');
  publicDuel._resetCache();
  const season = '2001-03';
  const id = 7700;
  pool.users.push({ id, email: 'chips-board@example.com', leaderboard_handle: 'chipper', referred_by: null });
  for (let i = 0; i < 6; i++) {
    const rid = 80000 + i;
    pool.duelRounds.push({ id: rid, day: `${season}-0${i + 1}`, idx: 0, kind: 'direction', symbol: 'BTCUSDT',
      agent_direction: null, odds: JSON.stringify({ long: 1.9, short: 1.9 }), odds_source: chips.ODDS_SOURCE,
      seal: null, seal_payload: null, created_at: `${season}-0${i + 1}T00:00:00.000Z` });
    // Five right, one wrong, each with 100 chips: +450 − 100 = +350, a 35% return.
    pool.duelPicks.push({ id: 90100 + i, user_id: id, round_id: rid, pick: 'long', entry_price: 100, entry_price_b: null,
      resolves_at: `${season}-0${i + 2}T00:00:00.000Z`, settle_price: i === 5 ? 90 : 110, settle_price_b: null,
      settle_state: 'settled', settled_at: `${season}-0${i + 2}T00:00:00.000Z`, settle_disputed: null, chips: 100,
      seal: null, seal_payload: null, created_at: `${season}-0${i + 1}T01:00:00.000Z` });
  }
  const app = express();
  app.use('/api/public/duel', publicDuel);
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  try {
    const raw = await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${server.address().port}/api/public/duel/board?season=${season}`, (res) => {
        let b = ''; res.on('data', (c) => { b += c; }); res.on('end', () => resolve(b));
      }).on('error', reject);
    });
    const body = JSON.parse(raw);
    const row = body.rows.find((r) => r.handle === 'chipper');
    assert.ok(row, raw);
    assert.deepStrictEqual(row.chips, { return_pct: 35, calls_with_chips: 6, paid: 6 });
    for (const banned of ['available', 'on_table', 'payout', '"held"', '"net"', '"put"', '"float"', '"stack"', 'chips_state']) {
      assert.ok(!raw.includes(banned), `"${banned}" must not leave on the public board`);
    }
    assert.strictEqual(body.counts_only, true);
  } finally { server.close(); }
});

test('the card offers no more than the stack can put, says when it is empty, and an unread stack is a dash', () => {
  const D = require('../public/js/duel');
  const block = (available) => ({ enabled: true, float: 1000, min: 10, max: 200, stack: { available, on_table: 0, held: 0, unread: 0, float: 1000 } });
  assert.match(D.chipsField(block(60), null), /max="60"/, 'the field offers what is left, not the per-call maximum');
  assert.match(D.chipsField(block(60), null), /10–60, or none/);
  assert.match(D.chipsField(block(900), null), /max="200"/);
  assert.match(D.chipsField(block(5), null), /No chips left this season/, 'below the least a call takes, the field is not offered');
  assert.doesNotMatch(D.chipsField(block(5), null), /<input/);
  assert.deepStrictEqual(D.stackLabel(null, null).text, '—');
  assert.deepStrictEqual(D.stackLabel({ stack: { available: null } }, null).text, '—', 'a stack nobody read is not 0 and not 1,000');
  assert.strictEqual(D.stackLabel(block(850), null).text, '850');
  assert.strictEqual(D.chipsLabel(null, null), null, 'no chips, no line');
  assert.strictEqual(D.chipsLabel({ chips: 50, odds: 1.9, state: 'lost', payout: -50 }, null).tone, 'down');
  for (const state of ['open', 'held', 'returned', 'unread']) {
    assert.strictEqual(D.chipsLabel({ chips: 50, odds: 1.9, state, payout: state === 'returned' ? 0 : null }, null).tone, 'muted', state);
  }
  const held = D.chipsLabel({ chips: 50, odds: 1.9, state: 'held', held_until: '2026-10-10T14:00:00.000Z' }, null, Date.parse('2026-10-10T13:20:00Z'));
  assert.strictEqual(held.text, '50 chips held: disputed, paid in 40m');
  // A layer switched off mid-season: the block still carries the stack, and the card offers nothing.
  const off = D.roundCard({ id: 1, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true,
    odds: { long: 1.9, short: 1.9 } }, null, null, { links: true, chips: { ...block(500), enabled: false } });
  assert.doesNotMatch(off, /d-chips|×1\.9|No chips on this round/);
  // Pi: no chips block passed, so no odds and no field, whatever the round carries.
  const html = D.roundCard({ id: 1, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true, odds: { long: 1.9, short: 1.9 } }, null, null, { verify: true });
  assert.doesNotMatch(html, /d-chips|×1\.9/);
});

test('a layer switched off mid-season still pays and shows what was put', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const day = `${MONTH}-18`;
  const now = Date.parse(`${day}T09:30:00Z`);
  const rounds = await svc.ensureRounds(day, new Date(now));
  const dir = rounds.find((r) => duel.kindOf(r) === 'direction');
  const who = newPlayer();
  assert.strictEqual((await svc.placePick(who, dir.id, 'long', new Date(now), { chips: 30 })).ok, true);
  delete process.env.DUEL_CHIPS_ENABLED;
  const card = await svc.cardFor(who, new Date(now + 60000));
  assert.ok(card.chips, 'the stack is still shown to a player who has chips out');
  assert.strictEqual(card.chips.enabled, false);
  assert.strictEqual(card.chips.stack.on_table, 30);
  assert.ok(!('odds' in card.rounds[0]), 'off, no round offers odds');
  const plain = newPlayer();
  assert.strictEqual((await svc.cardFor(plain, new Date(now + 60000))).chips, null, 'a player who never put any is shown none');
});
