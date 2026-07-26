'use strict';
/**
 * Arena risk model — the number a stop actually caps, and portfolio heat.
 * The load-bearing test is the CROSS-PIN: loss-at-stop must equal the
 * engine's own fill math (lib/arena.js posPnl at the stop price), so the
 * risk chip and the settlement can never drift apart silently.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const R = require('../public/js/arena-risk-model.js');
const arena = require('../lib/arena.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

test('cross-pin: loss-at-stop IS the engine fill at the stop price', () => {
  const book = [
    { entry: 100, margin: 500, leverage: 3, sl: 95, direction: 'LONG' },
    { entry: 100, margin: 500, leverage: 3, sl: 104, direction: 'SHORT' },
    { entry: 64000, margin: 120, leverage: 20, sl: 63100, direction: 'LONG' },
    { entry: 2.5, margin: 50, leverage: 10, sl: 2.9, direction: 'SHORT' },
    { entry: 100, margin: 500, leverage: 10, sl: 1, direction: 'LONG' },   // stop far beyond liq
  ];
  for (const pos of book) {
    const engine = arena.posPnl(pos, pos.sl);
    assert.equal(R.lossAtStop(pos), -Math.min(0, engine),
      `model and engine disagree for ${JSON.stringify(pos)}`);
  }
});

test('the clamp holds: a stop beyond liquidation cannot lose more than margin', () => {
  const pos = { entry: 100, margin: 500, leverage: 10, sl: 1, direction: 'LONG' };
  assert.equal(R.lossAtStop(pos), 500, 'isolated margin is the ceiling');
});

test('a stop on the profit side caps nothing to lose — 0, not negative', () => {
  assert.equal(R.lossAtStop({ entry: 100, margin: 500, leverage: 3, sl: 110, direction: 'LONG' }), 0);
});

test('no stop is null, never zero — an absent cap is not a zero risk', () => {
  assert.equal(R.lossAtStop({ entry: 100, margin: 500, leverage: 3, direction: 'LONG' }), null);
  assert.equal(R.riskPct({ entry: 100, margin: 500, leverage: 3, direction: 'LONG' }, 10000), null);
});

test('riskPct: percent of equity, one decimal', () => {
  // 500 × 3 × 5% = 75 loss on 10000 equity = 0.75% → 0.8 rounded to one decimal.
  const pos = { entry: 100, margin: 500, leverage: 3, sl: 95, direction: 'LONG' };
  assert.equal(R.riskPct(pos, 10000), 0.8);
  assert.equal(R.riskPct(pos, 0), null, 'no equity, no percent');
});

test('heat: capped losses summed, stopless positions COUNTED not folded in', () => {
  const positions = [
    { entry: 100, margin: 500, leverage: 3, sl: 95, direction: 'LONG' },   // 75
    { entry: 100, margin: 200, leverage: 5, sl: 90, direction: 'LONG' },   // 100
    { entry: 100, margin: 300, leverage: 2, direction: 'SHORT' },          // no stop
  ];
  const h = R.heat(positions, 10000);
  assert.equal(h.stopped, 2);
  assert.equal(h.unbounded, 1);
  assert.equal(h.loss, 175);
  assert.equal(h.pct, 1.8);
  // An all-stopless book must never show a reassuring 0% total.
  const cold = R.heat([{ entry: 100, margin: 300, leverage: 2, direction: 'LONG' }], 10000);
  assert.equal(cold.pct, null);
  assert.equal(cold.unbounded, 1);
});

test('the arena page wires the model: chip, heat line, ticket cap — all through T()', () => {
  const page = read('public', 'arena.html');
  assert.match(page, /arena-risk-model\.js/);
  assert.match(page, /RCArenaRisk\.riskPct\(p, d\.equity\)/, 'per-position chip');
  assert.match(page, /\.heat\(d\.positions, d\.equity\)/, 'portfolio heat');
  assert.match(page, /arena\.rp_stop/, 'the ticket says what the stop caps BEFORE the click');
  assert.match(page, /arena\.heat_unb/, 'stopless positions are named, not hidden');
  assert.match(page, /id="heatLine"/);
  const i18n = require('../public/js/i18n.js');
  for (const k of ['arena.heat', 'arena.heat_unb', 'arena.heat_t', 'arena.risk_chip_t', 'arena.rp_stop']) {
    const en = String(i18n.STRINGS[k].en || '');
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
});
