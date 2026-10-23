'use strict';
/**
 * The anonymous Daily Duel bot asks this gate, and the gate asks the duel
 * that already exists. Two properties are held here as behaviour:
 *
 * 1. A Telegram id handed to the sealer is not in the receipt. The receipt
 *    is sealDuelPick's payload, and the entry is the ticker's, not a number
 *    the caller supplied.
 * 2. The cutoff is the instant isCallable stops taking a direction call, and
 *    the horizon is duel.horizonFor. Neither is a second clock.
 */

const test = require('node:test');
const assert = require('node:assert');
const duel = require('../lib/duel');
const { sealDuelPick } = require('../lib/callseal');
const tickers = require('../lib/tickers');
const gate = require('../lib/daily_duel_gate');

const NOW = '2026-10-23T15:00:00.000Z';
const DAY = '2026-10-23';
const TG = '4242424242';

function book(symbol, price, volume) {
  return { [symbol]: { price, volume, change: 0 } };
}

test('the cutoff is the instant a direction call stops being callable', () => {
  const cutoff = gate.cutoffFor(DAY);
  const at = new Date(cutoff);
  assert.equal(duel.isCallable({ day: DAY, kind: 'direction' }, new Date(at.getTime() - 1)), true);
  assert.equal(duel.isCallable({ day: DAY, kind: 'direction' }, at), false);
  assert.equal(duel.dayKey(at), '2026-10-24');
  assert.equal(gate.rules().horizon_hours, duel.HORIZON_HOURS);
});

test('the market and the entry are the duel builder and the ticker', async () => {
  const marks = {
    BTCUSDT: { price: 100, volume: 1, change: 0 },
    ETHUSDT: { price: 50, volume: 9, change: 0 },
  };
  const offer = await gate.offer({ now: NOW, marks });
  assert.equal(offer.ok, true);
  assert.equal(offer.day, DAY);
  assert.equal(offer.callable, true);
  assert.equal(offer.symbol, 'ETHUSDT', 'highest volume priced symbol, the builder\'s first direction round');
  assert.equal(offer.entry_price, 50);
  assert.equal(offer.horizon_hours, duel.HORIZON_HOURS);
  assert.equal(offer.cutoff_at, gate.cutoffFor(DAY));
});

test('a zero price is unreadable, not an entry of zero', async () => {
  const offer = await gate.offer({ now: NOW, marks: book('BTCUSDT', 0, 10) });
  assert.equal(offer.unreadable, true);
  assert.equal(offer.entry_price, null);
  assert.equal(offer.symbol, null);
});

test('the seal is sealDuelPick and the telegram id is not in it', async () => {
  const marks = book('ETHUSDT', 50, 10);
  const out = await gate.sealCall({
    now: NOW,
    pick: 'up',
    symbol: 'ETHUSDT',
    entry_price: 1,
    telegram_id: TG,
    chat_id: '111',
    username: 'ada',
    player_key: 'ab'.repeat(32),
    handle: null,
    marks,
  });
  assert.equal(out.ok, true);
  assert.equal(out.pick, 'long');
  assert.equal(out.entry_price, 50, 'the caller\'s entry is ignored; the ticker is the entry');
  const body = JSON.parse(out.seal_payload);
  assert.equal(body.kind, 'duel_pick');
  assert.equal(body.category, 'direction');
  assert.equal(body.symbol, 'ETHUSDT');
  assert.equal(body.pick, 'long');
  assert.equal(body.handle, null);
  assert.equal(body.round_id, null);
  assert.equal(body.entry_price, 50);
  assert.equal(body.resolves_at, duel.horizonFor(out.picked_at));
  assert.equal(out.seal_payload.includes(TG), false);
  assert.equal(out.seal_payload.includes('telegram'), false);
  assert.equal(out.seal_payload.includes('ada'), false);
  assert.equal(out.seal_payload.includes('player_key'), false);
  const again = sealDuelPick({
    day: body.day, kind: 'direction', symbol: 'ETHUSDT', symbol_b: null, threshold_pct: null,
    handle: null, pick: 'long', entry_price: 50, entry_price_b: null,
    resolves_at: body.resolves_at, picked_at: body.picked_at,
  });
  assert.equal(out.seal_payload, again.seal_payload);
  assert.equal(out.seal, again.seal);
});

test('a handle that is only digits is refused, and a moved market is not sealed', async () => {
  const marks = book('ETHUSDT', 50, 10);
  const digits = await gate.sealCall({ now: NOW, pick: 'down', handle: TG, marks });
  assert.equal(digits.ok, false);
  assert.equal(digits.error, 'handle');
  const moved = await gate.sealCall({ now: NOW, pick: 'up', symbol: 'BTCUSDT', marks });
  assert.equal(moved.ok, false);
  assert.equal(moved.error, 'moved');
  assert.equal(moved.symbol, 'ETHUSDT');
});

test('the streak is the duel\'s streak', () => {
  const none = gate.streak({ now: NOW, picked_at: [] });
  assert.equal(none.current, 0);
  assert.equal(none.best, 0);
  const two = gate.streak({
    now: NOW,
    picked_at: ['2026-10-22T10:00:00.000Z', '2026-10-23T10:00:00.000Z'],
  });
  const via = duel.duelStreak([
    { created_at: '2026-10-22T10:00:00.000Z' },
    { created_at: '2026-10-23T10:00:00.000Z' },
  ], new Date(NOW));
  assert.equal(two.current, via.current);
  assert.equal(two.best, via.best);
  assert.ok(two.current >= 2);
});

test('offer reads getTickers when no marks are handed in', async () => {
  tickers.setTickerFetcher(async () => book('SOLUSDT', 42, 3));
  try {
    const offer = await gate.offer({ now: NOW });
    assert.equal(offer.symbol, 'SOLUSDT');
    assert.equal(offer.entry_price, 42);
  } finally {
    tickers.setTickerFetcher(null);
  }
});
