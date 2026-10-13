'use strict';
/**
 * Pattern watch — the retention loop: when the engine's synced deep scan
 * reports a NEW chart pattern on a symbol a user is holding, that user gets
 * ONE push. No confidence floor: the figure is the detector's own hand-set
 * score, unmeasured, so a floor on it chose detectors, not patterns. One source of truth (engine's deepscan block
 * only), owner-only delivery, per-(user,symbol,pattern) 12h dedupe, §4: no
 * amounts in the notification path.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { transitions, TTL_MS, MAX_PER_SYMBOL } = require('../lib/pattern_watch');

const HIT = (symbol, patterns) => ({ symbol, chart_patterns: patterns });
const PAT = (name, confidence, signal) => ({ name, confidence, signal: signal || 'bullish' });
const POS = (user_id, symbol, direction) => ({ user_id, symbol, direction: direction || 'LONG' });

test('a new pattern on a held symbol notifies the holder once', () => {
  const hits = [HIT('BTC/USDT', [PAT('Elliott 5-Wave Impulse', 0.78)])];
  const positions = [POS(7, 'BTCUSDT')];
  const r1 = transitions(hits, positions, new Map(), 1000);
  assert.equal(r1.notify.length, 1);
  assert.equal(r1.notify[0].user_id, 7);
  assert.equal(r1.notify[0].name, 'Elliott 5-Wave Impulse');
  // Same sweep result two minutes later: deduped.
  const r2 = transitions(hits, positions, r1.seen, 1000 + 120000);
  assert.equal(r2.notify.length, 0);
  // After the TTL the pattern may announce again (a fresh read, not spam).
  const r3 = transitions(hits, positions, r2.seen, 1000 + TTL_MS + 1);
  assert.equal(r3.notify.length, 1);
});

test('no floor on the detector score; the per-symbol cap holds', () => {
  const hits = [HIT('BTCUSDT', [
    PAT('Low Score', 0.4), PAT('A', 0.9), PAT('B', 0.8), PAT('C', 0.7),
  ])];
  const r = transitions(hits, [POS(1, 'BTCUSDT')], new Map(), 1);
  const names = r.notify.map((n) => n.name);
  assert.deepEqual(names, ['Low Score', 'A'], 'the first patterns, whatever their score');
  assert.equal(names.length, MAX_PER_SYMBOL);
});

test('an unread score travels as null, never as a zero', () => {
  const r = transitions([HIT('BTCUSDT', [{ name: 'No Score', signal: 'bullish' }])],
    [POS(1, 'BTCUSDT')], new Map(), 1);
  assert.equal(r.notify.length, 1);
  assert.strictEqual(r.notify[0].confidence, null);
});

test('the push says the detector\'s score, unmeasured, and never a percent', async () => {
  const pw = require('../lib/pattern_watch');
  const db = require('../db');
  db.pool.arenaPositions = db.pool.arenaPositions || [];
  db.pool.arenaPositions.push({ id: 91, user_id: 41, symbol: 'ADAUSDT', direction: 'LONG',
    entry: 1, margin: 1, leverage: 1 });
  await pw.resetPatternWatch();
  const sent = [];
  const deps = (patterns) => ({
    push: { isConfigured: () => true, notifySubscribers: async (p, ids) => { sent.push({ p, ids }); } },
    getLatestScan: async () => ({ deepscan: { hits: [{ symbol: 'ADA/USDT', chart_patterns: patterns }] } }),
  });
  await pw.runOnce(deps([{ name: 'Elliott 5-Wave Impulse', signal: 'bullish', confidence: 0.78 },
    { name: 'No Score', signal: 'bearish' }]));
  const bodies = sent.filter((s) => s.ids[0] === 41).map((s) => s.p.body);
  assert.equal(bodies.length, 2, bodies);
  assert.match(bodies[0], /Elliott 5-Wave Impulse \(bullish, detector score 78 \(unmeasured\)\)/);
  assert.match(bodies[1], /No Score \(bearish\) — /, 'an unread score is left out, not printed as 0');
  for (const b of bodies) assert.ok(!/\d\s*%/.test(b), b);
  await pw.resetPatternWatch();
});

test('symbol forms normalize: BTC/USDT hit matches a BTCUSDT position', () => {
  const r = transitions([HIT('BTC/USDT', [PAT('Bull Flag', 0.7)])],
    [POS(3, 'BTCUSDT')], new Map(), 1);
  assert.equal(r.notify.length, 1);
});

test('only holders hear about it — no position, no push', () => {
  const r = transitions([HIT('BTCUSDT', [PAT('Bull Flag', 0.9)])],
    [POS(5, 'ETHUSDT')], new Map(), 1);
  assert.equal(r.notify.length, 0);
});

test('§4 + delivery: owner-only push, no amount fields in the event', () => {
  const r = transitions([HIT('SOLUSDT', [PAT('Double Bottom', 0.71)])],
    [POS(9, 'SOLUSDT', 'SHORT')], new Map(), 1);
  const ev = r.notify[0];
  for (const k of ['margin', 'balance', 'pnl', 'entry', 'equity', 'email']) {
    assert.ok(!(k in ev), `event must not carry "${k}"`);
  }
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'pattern_watch.js'), 'utf8');
  assert.match(src, /\[n\.user_id\]/);                    // owner-only delivery
  assert.match(src, /getLatestScan/);                     // engine block only
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /pattern_watch'\)\.startPatternWatch\(\)/);
});
