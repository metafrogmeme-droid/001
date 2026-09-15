/**
 * THE COURT'S RENDERER, DRIVEN.
 *
 * `decision_court_model.test.js` drives the reading and
 * `decision_court_is_reached.test.js` scans the wiring; neither runs the
 * HTML. A renderer covered only by a scan is the failure this repo records
 * at length — #999's card was present, correct-looking and rendered zero
 * times — so the block is sliced out and executed here, and the assertions
 * are about what a reader would SEE.
 *
 * What it pins, in order of what a wrong answer would cost:
 *  * an absent section shows its SENTENCE, never an empty block and never a
 *    row of dashes — a dossier is read to decide whether to trust the engine;
 *  * a confidence sealed as 0 renders the absence, not "0%";
 *  * no verdict colour is painted for a gate nobody could read;
 *  * the chain's three fields are rendered, because they are what makes the
 *    record evidence rather than a claim;
 *  * a 404 renders its own sentence and nothing else.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const M = require(path.join(__dirname, '..', 'public', 'js', 'decision-court-model.js'));
const LOG = require(path.join(__dirname, '..', 'public', 'js', 'decision-log-model.js'));

const RAW = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

/** The Court's renderer block, executed in a VM with the helpers it reads. */
function renderer() {
  const a = RAW.indexOf('// ── the Decision Court: renderers ─');
  const b = RAW.indexOf('// ── the Decision Court: renderers end ─');
  assert.ok(a > 0 && b > a,
    'the Court renderers lost their markers; this harness slices between them');
  const block = RAW.slice(a, b);
  const ctx = {
    self: { DecisionCourtModel: M, DecisionLogModel: LOG },
    Number, String, Array, Object, isFinite, Math, JSON,
    T: (k, en) => `[${k}]`,
    esc: (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
    fmt: (n, d) => Number(n).toFixed(d === undefined ? 2 : d),
    fmtPrice: (n) => '$' + Number(n).toFixed(2),
    fmtAgo: (iso) => `ago(${iso})`,
    signed: (n) => (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(2),
    pnlClass: () => 'up',
    dlChip: (c) => `<span class="${c.cls}">chip</span>`,
    dlFillHtml: (f) => `<span class="dl-fill">${f.state}</span>`,
    dlSay: (W, w) => (w ? (W[w.key] || w.en) : ''),
  };
  vm.runInNewContext(block + '\n;globalThis.__x = { decisionCourtHtml, dcWords };', ctx);
  return ctx.__x;
}

const WORDS = () => {
  const out = {};
  for (const k of Object.keys(M.W)) out[M.W[k].key] = M.W[k].en;
  for (const k of Object.keys(LOG.W)) out[LOG.W[k].key] = LOG.W[k].en;
  return out;
};

const FULL = {
  decision_id: 'D-1', symbol: 'ETH/USDT', timestamp: '2026-09-15T09:56:40Z',
  outcome: 'EXECUTED_LIVE', is_paper: false,
  idea: {
    direction: 'LONG', confidence: 0.82, entry: 2475.93, sl: 2420, tp: 2600, rr: 2.1,
    strategy_type: 'swing', signal_type: 'breakout',
    reasoning: 'R1 range compression into the London open.',
    provenance: { model_provider: 'grok' },
  },
  risk: { verdict: 'APPROVED' },
  chain: { sequence: 4212, entry_hash: 'de'.repeat(32), prev_hash: 'ca'.repeat(32) },
  result: { pnl_usd: 137.42, exit_price: 2560.1, close_reason: 'TP HIT' },
};

const render = (rec, status) =>
  renderer().decisionCourtHtml(M.decisionCourt({ record: rec }, status || 200), WORDS());

test('a full record renders every section with its own heading', () => {
  const html = render(FULL);
  for (const id of ['chain', 'thesis', 'plan', 'setup', 'gate', 'decided', 'execution']) {
    assert.ok(html.includes(`data-dc-sec="${id}"`), `${id} section missing`);
  }
  assert.ok(html.includes('R1 range compression'), 'the thesis is rendered in full');
  assert.ok(html.includes('4212'), 'the chain sequence is rendered');
  assert.ok(html.includes('de'.repeat(32)), 'the entry hash is rendered whole, not truncated');
  assert.ok(html.includes('82%'), 'a read confidence renders as a percent');
});

test('an absent section shows its SENTENCE, never an empty block', () => {
  const html = render({ decision_id: 'D-2' });
  // Every section still appears — a dossier that silently drops a section
  // reads as a record that had nothing to say about it.
  for (const id of ['chain', 'thesis', 'plan', 'gate', 'macro', 'compliance', 'execution']) {
    assert.ok(html.includes(`data-dc-sec="${id}"`), `${id} section missing`);
  }
  assert.ok(html.includes('No thesis on record'), 'the thesis absence is a sentence');
  assert.ok(html.includes('No levels on record'), 'the plan absence is a sentence');
  assert.ok(html.includes('not the same as a broken chain'),
    'a missing chain says what it is and what it is not');
  // and no row markup at all in a section that has no rows
  const thesis = html.slice(html.indexOf('data-dc-sec="thesis"'), html.indexOf('data-dc-sec="plan"'));
  assert.ok(thesis.includes('dc-none'), 'the absence wears the muted class');
  assert.ok(!thesis.includes('dc-row'), 'no empty rows under a section with nothing on record');
});

test('a confidence sealed as 0 renders the absence, not 0%', () => {
  const html = render({ idea: { confidence: 0 } });
  assert.ok(html.includes('not on record'), 'the absence is said');
  assert.ok(!/>0%</.test(html), 'a defaulted confidence must never print as a measurement');
  assert.ok(html.includes('dc-stat--unread'), 'and the cell is marked unread');
});

test('no verdict colour is painted for a gate nobody could read', () => {
  for (const risk of [undefined, { verdict: 'UNKNOWN' }, { verdict: '' }]) {
    const html = render({ risk });
    const gate = html.slice(html.indexOf('data-dc-sec="gate"'),
      html.indexOf('data-dc-sec="macro"'));
    assert.ok(!/chip--(up|down)/.test(gate), `a verdict colour on an unread gate: ${gate}`);
    assert.ok(/not a pass/.test(gate), 'and it says so in words');
  }
});

test('a decision outside the published window renders ONE sentence and no dossier', () => {
  const html = renderer().decisionCourtHtml(M.decisionCourt(null, 404), WORDS());
  assert.ok(html.includes('still exists in the chain'));
  assert.ok(!html.includes('data-dc-sec='), 'no sections — there is no record to show');
  assert.ok(!html.includes('dc-strip'), 'and no strip of empty cells');
});

test('the anonymous note says the dollars are WITHHELD, not missing', () => {
  const html = renderer().decisionCourtHtml(
    M.decisionCourt({ record: FULL, disclosure: 'Anonymous view.' }, 200), WORDS());
  assert.ok(html.includes('withheld here, not missing from the record'));
});

test('the raw record is rendered escaped — a sealed field is never markup', () => {
  const html = render(Object.assign({}, FULL, { symbol: '<img src=x onerror=1>' }));
  assert.ok(!html.includes('<img src=x'), 'a symbol from the record must not become a tag');
  assert.ok(html.includes('&lt;img src=x'), 'it is shown as the text it is');
});
