'use strict';
/**
 * The engine panel's alerting tile. The bot's monitor isolates each of its
 * checks and counts the ones that raise; the scan payload's `features` now
 * carries `monitor_checks_down`, and this is what the panel says about it.
 * Absent means no monitor reported and nothing is claimed; an empty array is
 * the monitor's own "all ran"; names are a degraded alerting system, paged.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
function load() {
  const start = dash.indexOf('function monitorChecksCopy(down)');
  const end = dash.indexOf('// end monitorChecksCopy');
  assert.ok(start > 0 && end > start, 'monitorChecksCopy seam not found');
  const ctx = {}; vm.createContext(ctx);
  vm.runInContext(dash.slice(start, end) + '\nthis.f = monitorChecksCopy;', ctx);
  return ctx.f;
}

test('no report makes no claim', () => {
  const f = load();
  assert.equal(f(undefined), null);
  assert.equal(f(null), null);
  assert.equal(f('nope'), null);
});

test('an empty list is the monitor saying all ran, not silence', () => {
  const out = load()([]);
  assert.equal(out.degraded, false);
  assert.match(out.headline, /all checks running/);
});

test('names are a degraded alerting system, counted and listed', () => {
  const out = load()(['ws_health', 'state_changes']);
  assert.equal(out.degraded, true);
  assert.equal(out.headline, '2 checks down');
  assert.match(out.detail, /state_changes, ws_health/);
  assert.match(out.detail, /paged/);
  assert.equal(load()(['ws_health']).headline, '1 check down');
});

test('the engine panel renders the tile from the seam, red when degraded', () => {
  const i = dash.indexOf("renderPanel(C('emods')");
  assert.ok(i > 0);
  const block = dash.slice(i, i + 4000);
  assert.ok(block.includes('Array.isArray(f.monitor_checks_down)'), 'absent must mean no tile');
  assert.ok(block.includes('monitorChecksCopy(f.monitor_checks_down)'), 'the tile must come from the seam');
  assert.ok(block.includes('Alerting · DEGRADED'), 'a degraded alerting system is named as such');
});
