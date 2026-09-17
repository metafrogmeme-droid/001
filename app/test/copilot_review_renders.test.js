/**
 * THE CO-PILOT BLOCK, RENDERED.
 *
 * `copilot_review_model.test.js` drives the badge and nothing runs the HTML.
 * A renderer covered only by a scan is #999's card exactly — present,
 * correct-looking, and rendered zero times — so the block is sliced out and
 * executed here, and every assertion is about what a person would SEE in the
 * ticket immediately before confirming a real order.
 *
 * In order of what a wrong answer would cost:
 *  * a review that could not look at everything never wears CLEAR;
 *  * what it could not look at is ON the card, with its own reason;
 *  * the score never appears without its span;
 *  * a verdict this build cannot place paints no badge at all;
 *  * a reason from the bot is escaped — it is text, not markup.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const M = require(path.join(__dirname, '..', 'public', 'js', 'copilot-review-model.js'));
const RAW = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

/** The co-pilot renderer, executed in a VM with the two helpers it reads. */
function renderer(model) {
  const a = RAW.indexOf('// ── co-pilot review renderer ─');
  const b = RAW.indexOf('// ── co-pilot review renderer end ─');
  assert.ok(a > 0 && b > a,
    'the co-pilot renderer lost its markers; this harness slices between them');
  const ctx = {
    // The page's own escaper, byte-for-byte (app.js).
    esc: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    window: { CopilotReviewModel: model === undefined ? M : model },
  };
  vm.createContext(ctx);
  vm.runInContext(RAW.slice(a, b) + '\n;globalThis.__render = copilotReviewHtml;', ctx);
  return ctx.__render;
}

const PARTIAL = {
  verdict: 'partial', score: 100, rr: 3, stop_pct: 2, target_pct: 6,
  score_line: 'score 100/100 over 2 of the 4 checks',
  checks: { reward_risk: 'ok', stop_distance: 'ok', size_vs_equity: 'unchecked',
    engine_bias: 'unchecked', existing_exposure: 'unchecked' },
  unchecked: [
    { name: 'size_vs_equity', label: 'size vs equity', reason: 'your live balance was not read recently' },
    { name: 'engine_bias', label: "the engine's bias", reason: 'the engine has no open idea on BTC right now' },
    { name: 'existing_exposure', label: 'your existing exposure', reason: 'no live account on this build is linked to you' },
  ],
  flags: [], notes: ['Strong reward:risk (3).'],
};

const CLEAR = {
  verdict: 'clear', score: 100, rr: 3, stop_pct: 2, target_pct: 6,
  score_line: 'score 100/100 over all 4 checks',
  checks: {}, unchecked: [], flags: [], notes: ['Margin is 5% of equity.'],
};

test('a partly reviewed ticket is never dressed as a cleared one', () => {
  const html = renderer()(PARTIAL);
  assert.ok(html.includes('PARTIAL'), html);
  assert.ok(!html.includes('>CLEAR<'), 'PARTIAL must not wear the cleared word');
  assert.ok(html.includes('cop-badge--partial'));
  // And the muted badge is its own class: green or amber would each assert
  // half the state as the whole of it.
  assert.ok(!html.includes('cop-badge--clear') && !html.includes('cop-badge--caution'));
});

test('what it could not look at is on the card, with its own reason', () => {
  const html = renderer()(PARTIAL);
  for (const u of PARTIAL.unchecked) {
    assert.ok(html.includes(u.label), `missing label ${u.label}`);
    assert.ok(html.includes(u.reason), `missing reason for ${u.label}`);
  }
  assert.equal((html.match(/Not checked/g) || []).length, 3);
});

test('a cleared ticket carries no coverage rows to read past', () => {
  const html = renderer()(CLEAR);
  assert.ok(html.includes('CLEAR') && html.includes('cop-badge--clear'));
  assert.ok(!html.includes('Not checked'),
    'a permanent coverage row on a full review trains the reader to skip it');
});

test('the score never appears without its span', () => {
  assert.ok(renderer()(PARTIAL).includes('over 2 of the 4 checks'));
  assert.ok(renderer()(CLEAR).includes('over all 4 checks'));
  // An older bot build sends no span. A bare "score 100/100" is the defect.
  const noSpan = renderer()({ ...PARTIAL, score_line: undefined });
  assert.ok(!/score\s*\d/.test(noSpan), noSpan);
  assert.ok(noSpan.includes('R:R 3'), 'the rest of the card still renders');
});

test('a verdict this build cannot place paints no badge', () => {
  const html = renderer()({ ...PARTIAL, verdict: 'something_new' });
  assert.ok(!html.includes('cop-badge'), html);
  assert.ok(/cannot read/.test(html), html);
  // And it does not quietly print the numbers under a missing verdict.
  assert.ok(!html.includes('R:R'));
});

test('an invalid ticket shows the block message and no score', () => {
  const html = renderer()({
    verdict: 'invalid', score: null, rr: null, stop_pct: null, target_pct: null,
    unchecked: [], flags: [{ level: 'block', msg: 'Stop/target are on the wrong side of entry for a long.' }],
    notes: [],
  });
  assert.ok(html.includes('wrong side of entry'));
  assert.ok(!/score/.test(html));
  assert.ok(!html.includes('null'), 'a null figure must never reach the card');
});

test('the bot sentence is escaped — a reason is text, not markup', () => {
  const html = renderer()({ ...PARTIAL, unchecked: [
    { name: 'engine_bias', label: '<img src=x onerror=1>', reason: '<b>boom</b>' },
  ] });
  assert.ok(!html.includes('<img'), html);
  assert.ok(html.includes('&lt;img'), html);
  assert.ok(html.includes('&lt;b&gt;boom'), html);
});

test('the page without the model says the PAGE could not render, never a verdict', () => {
  // `defer` ordering, a blocked script, a stale cache: the model can be absent
  // and the block must not fall through to a badge of its own.
  //
  // It is its OWN sentence. A missing script is a fact about this page; a
  // verdict the model cannot place is a fact about what the BOT said; a null
  // review is the bot saying it produced none. Three causes, and the one
  // thing none of them may read as is "nothing was found" — the Confirm
  // button beside the block is live in all three.
  const html = renderer(null)(PARTIAL);
  assert.ok(/could not be rendered on this page/.test(html), html);
  assert.ok(!html.includes('cop-badge'));
  assert.ok(!html.includes('R:R'));
});

test('no review at all is not "nothing was found"', () => {
  // `pending_trade.copilot` is null when the bot could not produce a review —
  // the reading raised, or an older build sent no field at all. Before the
  // review rode on the proposal this state could not arise on this card,
  // because the card carried no review in any state.
  for (const absent of [null, undefined, 'nope', 42]) {
    const html = renderer()(absent);
    assert.ok(/did not review this ticket/.test(html), String(absent) + ': ' + html);
    assert.ok(!html.includes('cop-badge'), String(absent));
    assert.ok(!/\bscore\b/.test(html), String(absent));
  }
});

test('the advisory footer is the one sentence the bot also prints', () => {
  // `trade_copilot.COPILOT_FOOTER` is this string byte for byte and a Python
  // guard pins them equal. Here: the block really carries it, escaped.
  const html = renderer()(PARTIAL);
  assert.ok(html.includes('remain the authority'), html);
  assert.equal(typeof M.FOOTER, 'string');
  assert.ok(html.includes(M.FOOTER.replace(/&/g, '&amp;')), html);
});

test('the renderer refuses to run without an escaper', () => {
  // A renderer that silently stops escaping publishes a producer sentence as
  // markup. There is no degraded mode: the model throws.
  assert.throws(() => M.render(PARTIAL, null), /escaper/);
  assert.throws(() => M.render(PARTIAL, 'not a function'), /escaper/);
});
