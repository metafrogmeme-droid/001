'use strict';
/**
 * The operator dashboard's LLM panel showed a constant and called it state.
 *
 * `/api/state` built `llm_tiers` by walking DEFAULT_TIER_ROUTING — a module
 * constant — and never called resolve_tier_config. So the panel showed the
 * same four rows on every install, forever: it could not reflect an env pin, a
 * runtime /settier override, the admin premium table, or whether a key exists,
 * and it refreshed every three seconds to prove it was live.
 *
 * TWO MORE DEFECTS SAT IN THE RENDERER, and they are why this file drives the
 * function rather than checking the payload alone:
 *
 *   1. `if (tiers && Object.keys(tiers).length) { ... }` HAD NO `else`. A
 *      failed read left the PREVIOUS poll's routing on screen, unchanged and
 *      unlabelled. That is the stalest form of "unreadable is never zero":
 *      not a zero, but last-known-good presented as current.
 *
 *   2. `cost.llm_calls || 0` and `fmt(cost.llm_cost_usd || 0, 4)`, guarded by
 *      `if (cost)` — and `{}` is truthy, which is what the server sent on a
 *      failed read. The panel printed `0` calls and `$0.0000`: a measurement
 *      saying the brain has run and cost nothing.
 *
 * The page is standalone HTML with no build step, so it cannot require a
 * model module. The seam is a VM slice of the function, which is what
 * CLAUDE.md describes for the inline engine-status chip.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const HTML = path.join(__dirname, '..', '..', 'bot', 'web', 'dashboard.html');
const src = fs.readFileSync(HTML, 'utf8');

// Starts at modeBadgeView rather than updateEngine: RC-2026-023 moved the
// mode-pill decision into that pure helper, which sits immediately above
// updateEngine and is CALLED by it, so a slice beginning lower down throws
// ReferenceError. Widening the slice keeps this test running the real helper
// instead of a stub that could drift away from it.
const START = 'function modeBadgeView';
const END = '// ── Fetch Loop';
assert.ok(src.includes(START) && src.includes('function updateEngine')
          && src.includes(END),
  'the dashboard no longer contains modeBadgeView, updateEngine or the ' +
  'fetch-loop marker — this test is slicing nothing and would pass on an ' +
  'empty page');
const slice = src.slice(src.indexOf(START), src.indexOf(END));

/** Run updateEngine against a stub DOM and return what each element shows. */
function render(eng, tiers, cost) {
  const els = {};
  // `textContent` COERCES TO STRING in a real DOM, so the stub must too — a
  // stub stricter than the browser fails on code that is correct there, and
  // teaches you to "fix" the page to suit the test.
  const make = () => {
    let text = '';
    return {
      className: '', innerHTML: '',
      get textContent() { return text; },
      set textContent(v) { text = String(v); },
    };
  };
  const el = (id) => (els[id] = els[id] || make());
  const ctx = {
    $: el,
    stateClass: () => 'x',
    fmt: (v, dp) => Number(v).toFixed(dp),
    Number, String, Object, console,
  };
  vm.createContext(ctx);
  vm.runInContext(slice, ctx);
  ctx.updateEngine(eng, tiers, cost);
  return els;
}

const TIERS = {
  scan: { provider: 'runeclaw', model: 'v10-8b', source: 'env',
          key_state: 'key', env_var: 'LLM_TIER_SCAN_PROVIDER', env_value: 'runeclaw' },
  chat: { provider: 'anthropic', model: 'claude-sonnet-5', source: 'admin-table',
          key_state: 'key', env_var: 'LLM_TIER_CHAT_PROVIDER', env_value: 'mistral' },
  learning: { provider: 'anthropic', model: 'claude-sonnet-5',
              source: 'admin-table', key_state: 'key',
              env_var: 'LLM_TIER_LEARNING_PROVIDER', env_value: '' },
};

test('a routing table that could not be read does not leave the last one on screen', () => {
  const rows = render({ state: 'RUNNING' }, null, {}).tierRows.innerHTML;
  assert.match(rows, /routing unavailable/);
  assert.match(rows, /not "no tiers"/,
    'an unreadable routing reads as an empty one');
});

test('an empty tier map is treated as unreadable, not as zero tiers', () => {
  // The server sent `{}` on every failed read for as long as this existed.
  assert.match(render({}, {}, {}).tierRows.innerHTML, /routing unavailable/);
});

test('a dropped operator instruction is called out on the row', () => {
  const rows = render({}, TIERS, {}).tierRows.innerHTML;
  assert.match(rows, /LLM_TIER_CHAT_PROVIDER not applied/);
  assert.match(rows, /tier-warn/, 'the dropped instruction is not marked');
});

test('an applied pin and an unpinned tier do not read identically', () => {
  const rows = render({}, TIERS, {}).tierRows.innerHTML;
  assert.match(rows, /pinned by env/);
  assert.match(rows, /admin table/);
});

test('a tier on a default is not painted as a fault', () => {
  // CONTROL. Amber is a claim; an unpinned tier is normal, not a problem.
  const only = { learning: TIERS.learning };
  assert.ok(!render({}, only, {}).tierRows.innerHTML.includes('tier-warn'),
    'an ordinary unpinned tier was flagged');
});

test('a keyless remote endpoint is flagged rather than left blank', () => {
  const t = { scan: { ...TIERS.scan, key_state: 'keyless_remote' } };
  assert.match(render({}, t, {}).tierRows.innerHTML, /no key, remote endpoint/);
});

test('a provider name cannot inject markup', () => {
  const t = { scan: { ...TIERS.scan, provider: '<img onerror=x>' } };
  assert.ok(!render({}, t, {}).tierRows.innerHTML.includes('<img'));
});

// ── the cost row ────────────────────────────────────────────────────────────

test('an unreadable cost is not a measured zero', () => {
  const els = render({}, TIERS, {});          // {} — what the server sent
  assert.strictEqual(els.cCalls.textContent, '--');
  assert.strictEqual(els.cCost.textContent, '--');
  assert.strictEqual(els.cTokens.textContent, '--');
});

test('a null cost is not a measured zero either', () => {
  const els = render({}, TIERS, null);
  assert.strictEqual(els.cCalls.textContent, '--');
  assert.strictEqual(els.cCost.textContent, '--');
});

test('a genuine zero survives', () => {
  // THE OTHER HALF, and the one a blunt fix breaks: a brain that has been up
  // and made no calls really has made 0 calls at $0.0000, and that is a
  // measurement worth printing.
  const els = render({}, TIERS, {
    llm_calls: 0, llm_cost_usd: 0, prompt_tokens: 0, completion_tokens: 0 });
  assert.strictEqual(els.cCalls.textContent, '0');
  assert.strictEqual(els.cCost.textContent, '$0.0000');
  assert.strictEqual(els.cTokens.textContent, '0');
});

test('a cost snapshot missing one field does not zero that field', () => {
  const els = render({}, TIERS, { llm_calls: 12 });
  assert.strictEqual(els.cCalls.textContent, '12');
  assert.strictEqual(els.cCost.textContent, '--',
    'an absent cost printed as $0.0000 beside a real call count');
  assert.strictEqual(els.cTokens.textContent, '--');
});

test('an empty string is not zero', () => {
  // Number('') === 0 AND IS FINITE. This repo has been bitten by it three
  // times; the guard has to reject '' before the numeric check, not by it.
  const els = render({}, TIERS, { llm_calls: '', llm_cost_usd: '' });
  assert.strictEqual(els.cCalls.textContent, '--');
  assert.strictEqual(els.cCost.textContent, '--');
});

test('real values still render', () => {
  const els = render({}, TIERS, {
    llm_calls: 412, llm_cost_usd: 1.2345, prompt_tokens: 900, completion_tokens: 400 });
  assert.strictEqual(els.cCalls.textContent, '412');
  assert.strictEqual(els.cCost.textContent, '$1.2345');
  assert.strictEqual(els.cTokens.textContent, '1.3k');
});
