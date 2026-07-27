'use strict';
/**
 * Escape plan — whole-plan network floor. The plan already shows live gas
 * per step; now the summary carries the sum: one typical transaction per
 * on-chain step at live gas, added up. The contract:
 * - "≥" is literal — it is a FLOOR (bridge fees, relayers, approvals and
 *   destination gas all come on top), and the copy says so;
 * - steps on chains without readable gas are NOT counted and the line says
 *   how many were skipped — omitted, never invented;
 * - the figure derives from public market facts only (gas × typical tx ×
 *   native price) — never from any user balance;
 * - no readable steps → no line at all (never a fabricated $0.00).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const escape = pub('escape.html');
const i18n = pub('js', 'i18n.js');

function floorBlock() {
  const start = escape.indexOf('Whole-plan network floor');
  assert.ok(start > 0, 'the floor computation exists');
  return escape.slice(start, escape.indexOf('var planKey', start));
}

test('the floor counts only steps with readable gas, and says what it skipped', () => {
  const block = floorBlock();
  assert.match(block, /hit\.xfer_cost_usd > 0/, 'a chain counts only with a real positive floor cost');
  assert.match(block, /skipped\+\+/, 'unreadable chains are tallied, not silently dropped');
  assert.match(block, /es\.floor_skip/, 'the skip count is said out loud');
  assert.match(block, /if \(counted > 0\)/, 'no readable steps -> no line');
  assert.match(block, /fl\.style\.display = 'none'/, 'the hidden state is explicit');
});

test('the floor is a floor: literal ≥, honest copy, market facts only', () => {
  const block = floorBlock();
  assert.match(block, /\\u2265 \$\{usd\}/, 'the ≥ rides in the string itself');
  assert.match(block, /come on top/, 'what the floor excludes is stated');
  // the computation never reads a user balance — only GAS (public market facts)
  assert.ok(!block.includes('p.usd'), 'no user balance enters the floor');
  assert.ok(!block.includes('positions['), 'no position value enters the floor');
});

test('both floor keys ship all 14 locales with both slots', () => {
  for (const key of ['es.floor', 'es.floor_skip']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:', 'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
    const slots = key === 'es.floor' ? ['{usd}', '{n}'] : ['{n}'];
    for (const s of slots) {
      const count = line.split(s).length - 1;
      assert.ok(count >= 14, `${key}: slot ${s} in every locale (found ${count})`);
    }
  }
  const m = escape.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 80, 'i18n cache-buster >= 80');
});
