'use strict';
/**
 * A live position whose take-profit is the ladder's BACKSTOP says where it
 * scales out, so the venue's far TP does not read as the trade's target.
 *
 * The bot's row carries `scale_out` ({tp1, tp2, plan_take_profit}) only for
 * a position whose TP the executor moved beyond the ladder
 * (`live_executor.scale_out_reading`); without it the TP is the plan's own
 * and the row prints it as before.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function load() {
  const a = DASH.indexOf('  // ── scale-out text: start');
  const b = DASH.indexOf('  // ── scale-out text: end');
  assert.ok(a > 0 && b > a, 'the scale-out text moved; it sits between its markers in dashboard.js');
  const ctx = { fmtPrice: (v) => '$' + Number(v).toFixed(2), Number, String };
  vm.runInNewContext(DASH.slice(a, b) + '\nglobalThis.scaleOutText = scaleOutText;', ctx);
  return ctx.scaleOutText;
}

const scaleOutText = load();

test('a backstop names itself and the two levels the position scales out at', () => {
  assert.equal(scaleOutText({ take_profit: 108, scale_out: { tp1: 103, tp2: 105, plan_take_profit: 102.5 } }),
    ' (backstop) · scales out at $103.00 / $105.00');
});

test('a plan target, or a reading missing a level, adds nothing', () => {
  for (const p of [{ take_profit: 102.5 }, { scale_out: null }, { scale_out: { tp1: 103 } },
    { scale_out: { tp1: null, tp2: 105 } }, null, undefined]) {
    assert.equal(scaleOutText(p), '', JSON.stringify(p));
  }
});

test('the open-positions row prints it beside the TP', () => {
  assert.match(DASH, /· TP \$\{fmtPrice\(p\.take_profit\)\}\$\{scaleOutText\(p\)\} ·/);
});
