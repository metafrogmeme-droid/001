'use strict';
/**
 * Confidence Duel. A self-rating sealed inside the existing duel_pick
 * payload, only while DUEL_CONFIDENCE_ENABLED is on.
 *
 * Off, the sealed string is the string a call without the field has always
 * been, including when a client sends a rating anyway. On, low, medium or
 * high is the last field. An older receipt — the string without the field —
 * still hashes to its seal, which is all rcverify asks. A high-confidence
 * call that has not settled has no rate, and that rate is not 0%.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.DUEL_CONFIDENCE_ENABLED;
delete process.env.DUEL_CHIPS_ENABLED;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const seal = require('../lib/callseal');
const conf = require('../lib/duel_confidence');
const gate = require('../lib/daily_duel_gate');
const rcverify = require('../../tools/rcverify');
const svc = require('../lib/duel_service');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

const AT = '2026-10-23T12:00:00.000Z';
const sha = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const CALL = {
  round_id: 7, day: '2026-10-23', kind: 'direction', symbol: 'ETHUSDT', symbol_b: null,
  threshold_pct: null, handle: null, pick: 'long', entry_price: 50, entry_price_b: null,
  resolves_at: '2026-10-24T05:00:00.000Z', picked_at: AT,
};

function bundle(receipt) {
  return { format: 'rcproof/1', seal: receipt.seal, seal_payload: receipt.seal_payload };
}

test('a call with no rating seals the historical payload, and a rating is the last field', () => {
  const plain = seal.canonicalDuelPayload(CALL);
  const parsed = JSON.parse(plain);
  assert.ok(!('confidence' in parsed));
  assert.deepStrictEqual(Object.keys(parsed), [
    'v', 'kind', 'round_id', 'day', 'category', 'symbol', 'symbol_b', 'threshold_pct',
    'handle', 'pick', 'entry_price', 'entry_price_b', 'resolves_at', 'picked_at',
  ]);
  // The same facts, spelled out, so a later edit that moves a key fails here.
  assert.equal(plain, JSON.stringify({
    v: 4, kind: 'duel_pick', round_id: 7, day: '2026-10-23', category: 'direction',
    symbol: 'ETHUSDT', symbol_b: null, threshold_pct: null, handle: null, pick: 'long',
    entry_price: 50, entry_price_b: null, resolves_at: '2026-10-24T05:00:00.000Z', picked_at: AT,
  }));
  for (const stray of [undefined, null, '', 'sure', 2, 'certain']) {
    const again = JSON.parse(seal.canonicalDuelPayload({ ...CALL, confidence: stray }));
    assert.ok(!('confidence' in again), String(stray));
  }
  // Case and surrounding space fold into the three words. Anything else is omitted.
  assert.equal(JSON.parse(seal.canonicalDuelPayload({ ...CALL, confidence: 'HIGH' })).confidence, 'high');
  const rated = JSON.parse(seal.sealDuelPick({ ...CALL, confidence: 'high' }).seal_payload);
  assert.deepStrictEqual(Object.keys(rated), Object.keys(parsed).concat(['confidence']));
  assert.equal(rated.confidence, 'high');
  assert.equal(seal.sealDuelPick({ ...CALL, confidence: '  Medium ' }).seal_payload.includes('"confidence":"medium"'), true);
});

test('rcverify accepts a receipt without the field and one with it, and a changed rating fails', async () => {
  const old = seal.sealDuelPick(CALL);
  const neu = seal.sealDuelPick({ ...CALL, confidence: 'low' });
  const oldOut = await rcverify.verifyBundle(bundle(old), { offline: true });
  const newOut = await rcverify.verifyBundle(bundle(neu), { offline: true });
  assert.equal(oldOut.checks.find((c) => c.name === 'hash').status, 'pass');
  assert.equal(newOut.checks.find((c) => c.name === 'hash').status, 'pass');
  assert.equal(oldOut.exit, 0);
  assert.equal(newOut.exit, 0);
  const tampered = neu.seal_payload.replace('"low"', '"high"');
  const bad = await rcverify.verifyBundle(
    { format: 'rcproof/1', seal: neu.seal, seal_payload: tampered }, { offline: true });
  assert.equal(bad.checks.find((c) => c.name === 'hash').status, 'fail');
  assert.equal(bad.exit, 1);
  assert.equal(sha(old.seal_payload), old.seal);
});

test('the flag off drops a rating; on, it is sealed, and a word that is not one of the three is refused', async () => {
  delete process.env.DUEL_CONFIDENCE_ENABLED;
  const marks = { ETHUSDT: { price: 50, volume: 10, change: 0 } };
  const dropped = await gate.sealCall({ now: AT, pick: 'up', symbol: 'ETHUSDT', confidence: 'high', marks });
  assert.equal(dropped.ok, true);
  assert.ok(!('confidence' in JSON.parse(dropped.seal_payload)));
  assert.equal(gate.rules().confidence, false);
  process.env.DUEL_CONFIDENCE_ENABLED = '1';
  try {
    assert.equal(gate.rules().confidence, true);
    const sealed = await gate.sealCall({ now: AT, pick: 'down', symbol: 'ETHUSDT', confidence: 'high', marks });
    assert.equal(sealed.ok, true);
    assert.equal(JSON.parse(sealed.seal_payload).confidence, 'high');
    assert.equal(JSON.parse(sealed.seal_payload).pick, 'short');
    const missing = await gate.sealCall({ now: AT, pick: 'up', symbol: 'ETHUSDT', marks });
    assert.equal(missing.ok, true);
    assert.ok(!('confidence' in JSON.parse(missing.seal_payload)), 'a caller that sends none still gets a call');
    const refused = await gate.sealCall({ now: AT, pick: 'up', symbol: 'ETHUSDT', confidence: 'certain', marks });
    assert.equal(refused.ok, false);
    assert.equal(refused.error, 'confidence');
  } finally {
    delete process.env.DUEL_CONFIDENCE_ENABLED;
  }
});

test('high-confidence calibration counts only settled hits, and none settled is not 0%', () => {
  const cal = conf.calibration([
    { confidence: 'high', state: 'correct' },
    { confidence: 'high', state: 'correct' },
    { confidence: 'high', state: 'wrong' },
    { confidence: 'high', state: 'push' },
    { confidence: 'high', state: null },
    { confidence: 'medium', state: 'correct' },
    { confidence: 'low', state: 'wrong' },
  ]);
  assert.deepStrictEqual(
    { correct: cal.correct, scored: cal.scored, unscored: cal.unscored, pct: cal.pct },
    { correct: 2, scored: 3, unscored: 2, pct: 66.7 },
  );
  const none = conf.calibration([{ confidence: 'high', state: null }, { confidence: 'high', state: 'unresolved' }]);
  assert.equal(none.pct, null);
  assert.equal(none.scored, 0);
  assert.notEqual(none.pct, 0);
  const empty = conf.fromPicks([{ round_id: 1, seal_payload: '{"confidence":"nope"}' }], []);
  assert.equal(empty.scored, 0);
  assert.equal(empty.unscored, 0);
  const hit = conf.fromPicks(
    [{ round_id: 4, seal_payload: JSON.stringify({ confidence: 'high' }) }],
    [{ round_id: 4, state: 'correct' }],
  );
  assert.deepStrictEqual([hit.correct, hit.scored, hit.pct], [1, 1, 100]);
});

test('the anonymous gate scores a due high call from the price it was given, and leaves an unread price out', async () => {
  process.env.DUEL_CONFIDENCE_ENABLED = '1';
  try {
    const payload = seal.sealDuelPick({ ...CALL, confidence: 'high' }).seal_payload;
    const pending = await gate.calibrationOf({
      now: AT, payloads: [payload], marks: { ETHUSDT: { price: 80, volume: 1 } },
    });
    assert.equal(pending.high.pct, null, 'the window has not ended');
    assert.equal(pending.high.unscored, 1);
    const right = await gate.calibrationOf({
      now: '2026-10-24T06:00:00.000Z', payloads: [payload],
      marks: { ETHUSDT: { price: 80, volume: 1 } },
    });
    assert.deepStrictEqual([right.high.correct, right.high.scored, right.high.pct], [1, 1, 100]);
    const unread = await gate.calibrationOf({
      now: '2026-10-24T06:00:00.000Z', payloads: [payload], marks: {},
    });
    assert.equal(unread.high.pct, null);
    assert.equal(unread.high.scored, 0);
    assert.equal(unread.high.unscored, 1);
    const old = seal.sealDuelPick(CALL).seal_payload;
    const ignored = await gate.calibrationOf({
      now: '2026-10-24T06:00:00.000Z', payloads: [old],
      marks: { ETHUSDT: { price: 10, volume: 1 } },
    });
    assert.equal(ignored.high.scored, 0);
    assert.equal(ignored.high.unscored, 0);
  } finally {
    delete process.env.DUEL_CONFIDENCE_ENABLED;
  }
  const off = await gate.calibrationOf({ now: AT, payloads: ['{"confidence":"high"}'] });
  assert.equal(off.enabled, false);
  assert.ok(!('high' in off));
});

test('the arena seals a rating only while the flag is on, and the record counts it', async () => {
  delete process.env.DUEL_CONFIDENCE_ENABLED;
  const prices = {
    BTCUSDT: { price: 60000, volume: 900 }, ETHUSDT: { price: 3000, volume: 800 },
    SOLUSDT: { price: 150, volume: 700 }, BNBUSDT: { price: 600, volume: 600 },
    XRPUSDT: { price: 0.5, volume: 500 }, ARBUSDT: { price: 1.2, volume: 50 },
  };
  setTickerFetcher(async () => prices);
  const now = new Date('2026-10-23T09:30:00.000Z');
  const offWho = 880221;
  const onWho = 880222;
  try {
    const card = await svc.cardFor(offWho, now);
    assert.ok(!('confidence' in card));
    const round = card.rounds.find((r) => r.kind === 'direction' && r.callable);
    const ignored = await svc.placePick(offWho, round.id, 'long', now, { confidence: 'high' });
    assert.equal(ignored.ok, true);
    assert.ok(!('confidence' in ignored));
    const [rows] = await pool.execute('SELECT seal_payload FROM duel_picks WHERE user_id = ?', [offWho]);
    assert.ok(!('confidence' in JSON.parse(rows[0].seal_payload)));

    process.env.DUEL_CONFIDENCE_ENABLED = '1';
    const on = await svc.cardFor(onWho, now);
    assert.deepStrictEqual(on.confidence_levels, ['low', 'medium', 'high']);
    const bad = await svc.placePick(onWho, round.id, 'long', now, { confidence: 'certain' });
    assert.equal(bad.ok, false);
    assert.match(bad.error, /low, medium or high/);
    const good = await svc.placePick(onWho, round.id, 'short', now, { confidence: 'high' });
    assert.equal(good.ok, true);
    assert.equal(good.confidence, 'high');
    const again = await svc.cardFor(onWho, now);
    const mine = again.rounds.find((r) => r.id === round.id);
    assert.equal(mine.my_call.confidence, 'high');
    const [stored] = await pool.execute('SELECT id, seal_payload FROM duel_picks WHERE user_id = ?', [onWho]);
    assert.equal(JSON.parse(stored[0].seal_payload).confidence, 'high');
    assert.equal(sha(stored[0].seal_payload), good.seal);

    const rec = await svc.recordFor(onWho, now);
    assert.equal(rec.calibration.pct, null);
    assert.equal(rec.calibration.unscored, 1);
    // Short, and the settle is below the entry the ticker handed the call
    // (ETH at 3000). A lower close is the direction that call named.
    await pool.execute(
      'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
      [2000, 'settled', '2026-10-24T09:30:00.000Z', stored[0].id]);
    const later = await svc.recordFor(onWho, new Date('2026-10-24T12:00:00.000Z'));
    assert.equal(later.calibration.pct, 100);
    assert.equal(later.calibration.correct, 1);
  } finally {
    delete process.env.DUEL_CONFIDENCE_ENABLED;
    setTickerFetcher(null);
  }
});

test('the card asks for a rating only when it was given the three words, and a null rate is a dash', () => {
  const D = require('../public/js/duel');
  const open = {
    id: 11, kind: 'direction', symbol: 'ETHUSDT', callable: true,
    horizon_hours: 24, options: ['long', 'short', 'pass'],
  };
  const off = D.roundCard(open);
  assert.equal(off.includes('data-confidence'), false);
  assert.equal(off.includes('data-ask-confidence'), false);
  assert.equal(off.includes('Your rating'), false);
  const on = D.roundCard(open, undefined, undefined, { confidenceLevels: ['low', 'medium', 'high', 'certain'] });
  assert.match(on, /data-ask-confidence="yes"/);
  assert.match(on, /data-confidence="low"[^>]*hidden/);
  assert.match(on, /data-confidence="high"[^>]*hidden/);
  assert.equal(on.includes('data-confidence="certain"'), false);
  const called = D.roundCard({
    ...open, my_call: { pick: 'long', pending: true, confidence: 'high' },
  });
  assert.match(called, /Your rating/);
  assert.match(called, />High</);
  const old = D.roundCard({ ...open, my_call: { pick: 'long', pending: true } });
  assert.equal(old.includes('Your rating'), false);
  const none = D.calibrationLabel({ pct: null, scored: 0, unscored: 0 });
  assert.equal(none.text, '—');
  assert.equal(none.tone, 'muted');
  assert.match(none.sub, /No high-confidence call yet/);
  const waiting = D.calibrationLabel({ pct: null, scored: 0, unscored: 2 });
  assert.equal(waiting.text, '—');
  assert.match(waiting.sub, /has settled yet/);
  const miss = D.calibrationLabel({ pct: 0, scored: 2, unscored: 0 });
  assert.equal(miss.text, '0%');
  assert.equal(miss.tone, 'down');
  const hit = D.calibrationLabel({ pct: 66.7, scored: 3, unscored: 1 });
  assert.equal(hit.text, '66.7%');
  assert.equal(hit.tone, 'up');
  assert.match(hit.sub, /of 3 high-confidence calls/);
});
