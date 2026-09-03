'use strict';
/**
 * The Hub strip printed "0 positions carried" and "0 alerts armed" while
 * every API answered 503. Found by the unreadable-API browser pass: with all
 * reads failing, the only honest figures are dashes and error states, and the
 * strip's Mode tile already said "—" for the same failed portfolio read.
 * `hubCounts` is the seam: null means not read, a real empty answer is 0.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
function load() {
  const start = dash.indexOf('function hubCounts(pf, alertsR)');
  const end = dash.indexOf('// end hubCounts');
  assert.ok(start > 0 && end > start, 'hubCounts seam not found');
  const ctx = {}; vm.createContext(ctx);
  vm.runInContext(dash.slice(start, end) + '\nthis.f = hubCounts;', ctx);
  return ctx.f;
}

test('a portfolio that failed to load is not an empty book', () => {
  const { nOpen } = load()(null, { ok: true, data: { alerts: [] } });
  assert.strictEqual(nOpen, null);
});

test('a portfolio that answered with no positions is a real zero', () => {
  assert.strictEqual(load()({ open_positions: [] }, null).nOpen, 0);
  assert.strictEqual(load()({ open_positions: [{}, {}] }, null).nOpen, 2);
});

test('an alerts call that did not answer ok is not zero alerts', () => {
  const f = load();
  assert.strictEqual(f({}, null).armed, null);
  assert.strictEqual(f({}, { ok: false, status: 503, data: { ok: false } }).armed, null);
  assert.strictEqual(f({}, { ok: true, data: {} }).armed, null, 'ok without an alerts array is still unread');
});

test('an alerts call that answered counts only the active ones', () => {
  const r = { ok: true, data: { alerts: [{ active: true }, { active: false }, { active: true }, null] } };
  assert.strictEqual(load()({}, r).armed, 2);
  assert.strictEqual(load()({}, { ok: true, data: { alerts: [] } }).armed, 0);
});

test('the strip renders a dash and says unread, from the seam', () => {
  const i = dash.indexOf("renderPanel(C('hubstat')");
  assert.ok(i > 0);
  const block = dash.slice(i, i + 3500);
  assert.ok(block.includes('hubCounts(pf, alertsR)'), 'the strip must take its counts from the seam');
  assert.ok(block.includes("'positions unread'") && block.includes("'alerts unread'"), 'a null must be labelled unread');
  assert.ok(!block.includes("String(nOpen), nOpen === 1"), 'the unconditional String(nOpen) is back');
});
