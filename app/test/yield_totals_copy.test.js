'use strict';
/**
 * The yield panel's totals line must say when it is a floor.
 *
 * The bot's `incomplete` note means "free futures margin could not be read,
 * so it is not counted". Printing "Total idle $40.00" beside a silently
 * missing futures row presents a spot-only figure as the whole picture.
 * `yieldTotalsCopy` is the pure seam: the label changes and the note is
 * carried; with no note, nothing is added.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function load() {
  const start = dash.indexOf('function yieldTotalsCopy(y)');
  const end = dash.indexOf('// end yieldTotalsCopy');
  assert.ok(start > 0 && end > start, 'yieldTotalsCopy seam not found');
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(dash.slice(start, end) + '\nthis.f = yieldTotalsCopy;', ctx);
  return ctx.f;
}

test('a complete report gets the plain label and no note', () => {
  const f = load();
  // Field by field: the object comes from another vm realm, and
  // deepStrictEqual compares prototypes across realms.
  for (const y of [{ total_idle_usd: 40, incomplete: '' }, { total_idle_usd: 40 }]) {
    const out = f(y);
    assert.equal(out.label, 'Total idle');
    assert.equal(out.note, '');
  }
});

test('an incomplete report is labelled partial and carries the reason', () => {
  const f = load();
  const out = f({ total_idle_usd: 40, incomplete: 'Free futures margin could not be read, so it is not counted below.' });
  assert.match(out.label, /partial/);
  assert.match(out.label, /futures margin unread/);
  assert.match(out.note, /could not be read/);
});

test('the panel renders the note and the label from the seam', () => {
  const i = dash.indexOf("renderPanel(C('ayield')");
  assert.ok(i > 0);
  const block = dash.slice(i, i + 9000);
  assert.ok(block.includes('yieldTotalsCopy(y).note'), 'the note must be rendered');
  assert.ok(block.includes('yieldTotalsCopy(y).label'), 'the label must come from the seam');
  assert.ok(!/<p class="small muted mt-2">Total idle </.test(block), 'the hard-coded "Total idle" label is back');
});
