'use strict';
// The operator screenshotted a modal that blamed a dependency for being
// offline while that same dependency was visibly working three lines below.
//
// SGOV, a stock perp. The card rendered a full candle chart, a VWAP chip, a
// structure chip and a pattern read — every one of them served by the
// analysis bridge — under this sentence:
//
//     "No live decision picture right now (the analysis bridge may be offline)."
//
// The bridge was up. It simply has no confluence score for a stock perp.
//
// Three different situations collapsed into that one message, because the
// guard was a single OR:
//
//     if (!d || d.error || typeof d.confluence !== 'number')
//
//   !d                     the request failed         -> "offline" is fair
//   d.error                the bridge said WHY        -> and we discarded it
//   no confluence          the bridge answered 200    -> it is UP, and empty
//
// And the caller passed `ins && ins.data`, so the function could not have
// told them apart even if it wanted to: a failed fetch and a successful one
// with an empty payload both arrive as undefined.
//
//     AN ABSENCE IS NOT A DIAGNOSIS, AND THE NAMED CAUSE WAS THE WRONG ONE.
//
// Same family as the scan timeout that blamed the exchange for a deadline
// this repo had set itself, and the news radar that promised a refresh at the
// moment one had failed.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

// Lift the guard out and drive it. The tail is replaced with a sentinel: the
// happy path builds a meter from many helpers this file has no business
// pulling in, and the property under test is entirely in the early returns.
function loadBlock() {
  const i = src.indexOf('function _insightBlock');
  const j = src.indexOf('const dir = (d.confluence', i);
  assert.ok(i > 0 && j > i, 'could not locate _insightBlock');
  const body = `${src.slice(i, j)}return 'READ'; }`;
  const esc = (x) => String(x == null ? '' : x)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // eslint-disable-next-line no-new-func
  return new Function('esc', `return ${body}`)(esc);
}

const block = loadBlock();

test('a healthy read still renders the decision picture', () => {
  assert.strictEqual(block({ ok: true, data: { confluence: 0.62 } }), 'READ');
});

test('a failed request is the only case that blames reachability', () => {
  for (const res of [null, undefined, { ok: false }, { ok: false, data: {} }]) {
    assert.match(block(res), /Could not reach the analysis bridge/,
      `case: ${JSON.stringify(res)}`);
  }
});

test('a bridge that answered is never called unreachable', () => {
  // The screenshot case. The bridge served the chart on this very card.
  const out = block({ ok: true, data: { price: 100 } });
  assert.doesNotMatch(out, /Could not reach/);
  assert.doesNotMatch(out, /offline/i,
    'the message must not name a cause the evidence contradicts');
});

test('an empty read says the bridge answered and had nothing', () => {
  const out = block({ ok: true, data: { price: 100 } });
  assert.match(out, /the bridge answered/);
  assert.match(out, /no confluence score here/);
});

test('an empty read names the plausible reason without asserting it', () => {
  // "stock and newly-listed perps often have none" is a hint, not a verdict:
  // it explains the shape without claiming to know why THIS symbol is empty.
  const out = block({ ok: true, data: { price: 100 } });
  assert.match(out, /often have none/,
    'a reader should know this is normal for some symbols');
});

test("the bridge's own reason is repeated, not replaced with a guess", () => {
  const out = block({ ok: true, data: { error: 'symbol not analysed' } });
  assert.match(out, /symbol not analysed/,
    'the route forwards the bridge detail — discarding it to say "offline" '
    + 'throws away the only real diagnosis available');
});

test("the bridge's reason is escaped", () => {
  const out = block({ ok: true, data: { error: '<img src=x onerror=1>' } });
  assert.doesNotMatch(out, /<img/);
  assert.match(out, /&lt;img/);
});

test("the bridge's reason is length-bounded", () => {
  const out = block({ ok: true, data: { error: 'x'.repeat(5000) } });
  assert.ok(out.length < 400, `message ran to ${out.length} chars`);
});

test('every failure mode reassures that the rest of the card is live', () => {
  // The whole defect was a message implying the card was broken when only
  // one section of it was empty.
  for (const res of [null, { ok: true, data: {} }]) {
    assert.match(block(res), /unaffected/, `case: ${JSON.stringify(res)}`);
  }
});

test('the three cases produce three different messages', () => {
  const unreachable = block(null);
  const declined = block({ ok: true, data: { error: 'nope' } });
  const empty = block({ ok: true, data: { price: 1 } });
  assert.strictEqual(new Set([unreachable, declined, empty]).size, 3,
    'one sentence for three causes is how the wrong one got named');
});

test('both call sites pass the whole result, not just .data', () => {
  // `_insightBlock(ins && ins.data)` cannot distinguish a failed fetch from a
  // successful one with an empty payload — both arrive as undefined.
  assert.doesNotMatch(src, /_insightBlock\([a-z]+ && [a-z]+\.data\)/,
    'passing only .data discards the ok flag the attribution depends on');
  assert.match(src, /_insightBlock\(ins\)/);
  assert.match(src, /_insightBlock\(r\)/);
});
