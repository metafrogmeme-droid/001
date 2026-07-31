'use strict';
/**
 * Scored surface scenarios for the dashboard's most prominent claim.
 *
 * Same discipline as tests/test_surface_scenarios.py on the bot side: plant a
 * known state, assert what the surface SAYS — and plant the red herring that
 * invites the wrong conclusion.
 *
 * The red herring here is real and it cost real time. During this week's
 * database outages the site stayed up and every tab announced ENGINE OFFLINE,
 * because /api/bot/sync/scan was refused with a 503 by the not-ready gate.
 * The engine was running the whole time. "I cannot see the engine" and "the
 * engine is down" are different sentences, and collapsing scanOk to a boolean
 * is what made them the same one.
 *
 * The verdict was extracted from dashboard.js to make this testable at all —
 * six thousand lines of browser script had no seam at which to assert "this
 * state renders that verdict", which is exactly how the Telegram adoption
 * card shipped a detail that never rendered once.
 */
const test = require('node:test');
const assert = require('node:assert');

const { engineChipState } = require('../public/js/engine-status-model.js');

const NOW = Date.parse('2026-07-30T19:00:00Z');
const at = (minsAgo) => new Date(NOW - minsAgo * 60_000).toISOString();

// (name, syncTime, scanOk, MUST include, MUST NOT include)
const SCENARIOS = [
  ['a failed read says it cannot SEE the engine, not that it is down',
    null, false, ['ENGINE STATUS UNKNOWN', 'website problem'], ['OFFLINE']],

  ['never attempted reads as connecting, not as absence',
    null, null, ['CONNECTING'], ['OFFLINE', 'UNKNOWN']],

  ['a successful read with no scan is not "down"',
    null, true, ['NO ENGINE DATA', 'read successfully'], ['OFFLINE', 'UNKNOWN']],

  ['a fresh scan is LIVE',
    at(2), true, ['ENGINE LIVE', '2m ago'], ['OFFLINE', 'STALE']],

  ['a 20-minute-old scan is STALE, not OFFLINE',
    at(20), true, ['ENGINE STALE'], ['OFFLINE', 'LIVE']],

  ['a genuinely old scan is OFFLINE',
    at(90), true, ['ENGINE OFFLINE', '90m ago'], ['LIVE', 'STALE']],

  // THE TRAP: a payload exists AND the latest refresh failed. Age is real
  // evidence and the verdict must stand on it — the failure is NOTED, never
  // allowed to overwrite what we already know.
  ['a failed refresh notes itself without overwriting a fresh verdict',
    at(2), false, ['ENGINE LIVE', 'may be older than it looks'],
    ['UNKNOWN', 'OFFLINE']],
];

for (const [name, syncTime, scanOk, must, mustNot] of SCENARIOS) {
  test(name, () => {
    const s = engineChipState(syncTime, scanOk, NOW);
    const blob = `${s.text} ${s.title}`;
    for (const phrase of must) {
      assert.ok(blob.includes(phrase), `missing ${phrase!==undefined?phrase:''} in: ${blob}`);
    }
    for (const phrase of mustNot) {
      assert.ok(!blob.includes(phrase), `wrongly claimed ${phrase} in: ${blob}`);
    }
  });
}

test('scanOk is tri-state — the three no-scan cases give three verdicts', () => {
  // Collapsing this to a boolean is the defect itself.
  const seen = new Set([null, false, true].map(
    (ok) => engineChipState(null, ok, NOW).text));
  assert.equal(seen.size, 3, `three states must not share a verdict: ${[...seen]}`);
});

test('the boundaries are exact, not approximate', () => {
  assert.equal(engineChipState(at(14.9), true, NOW).text, '● ENGINE LIVE');
  assert.equal(engineChipState(at(15.1), true, NOW).text, '● ENGINE STALE');
  assert.equal(engineChipState(at(29.9), true, NOW).text, '● ENGINE STALE');
  assert.equal(engineChipState(at(30.1), true, NOW).text, '● ENGINE OFFLINE');
});

test('the dashboard routes through the model rather than re-deriving it', () => {
  const fs = require('fs');
  const path = require('path');
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /EngineStatusModel/);
  // The inline copy must be gone — two implementations of one verdict is how
  // they drift apart.
  assert.ok(!dash.includes("'● ENGINE STATUS UNKNOWN'"),
    'the inline verdict survived alongside the model');
});

test('the model is loaded before the dashboard that uses it', () => {
  const fs = require('fs');
  const path = require('path');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  // Match the SCRIPT TAGS, not any mention: a comment on line 35 says
  // "Views are rendered by js/dashboard.js", and a bare indexOf finds that
  // first and reports a load-order failure that does not exist. (It did.)
  const model = html.indexOf('<script src="/js/engine-status-model.js');
  const dash = html.indexOf('<script src="/js/dashboard.js');
  assert.ok(model !== -1, 'the model is never loaded');
  assert.ok(dash !== -1, 'dashboard.js is never loaded');
  assert.ok(model < dash, 'the model must load before dashboard.js');
});
