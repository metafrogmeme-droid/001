'use strict';
/**
 * The exchange-key panel's gate: three states, and the last two are not one.
 *
 * `POST /api/credentials` refuses when nothing can protect a submission, and
 * the form above it used to look perfectly live while it did — a user pasted
 * real exchange API keys, got a 503, and could only read it as their own
 * mistake. The panel now hides the form and says why, which is a claim about a
 * money surface, so the decision lives in a pure model rather than inline in
 * six thousand lines of browser script.
 *
 * The state worth the file is `unknown`. `crypto_ready: false` asserts "this
 * form is off"; a key store that could not be READ has not earned that, and
 * telling an operator the bot never called when the database is down sends
 * them to restart the wrong process.
 *
 * Run: npm test  (node --test test/)
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { gateState } = require('../public/js/creds-gate-model');

test('a ready deployment shows the forms and claims nothing else', () => {
  for (const mode of ['sealed', 'legacy']) {
    const g = gateState({ crypto_ready: true, crypto_mode: mode, crypto_reason: null });
    assert.strictEqual(g.state, 'ready');
    assert.strictEqual(g.showForms, true);
    assert.strictEqual(g.reason, null);
  }
});

test('a known-off deployment hides the forms and carries the reason', () => {
  const g = gateState({
    crypto_ready: false, crypto_mode: 'off', crypto_reason: 'awaiting_bot_key',
    crypto_detail: 'The bot has not published its key yet…',
  });
  assert.strictEqual(g.state, 'off');
  assert.strictEqual(g.showForms, false);
  assert.strictEqual(g.reason, 'awaiting_bot_key');
  assert.match(g.detail, /has not published/);
});

test('an UNREADABLE key store is its own state, not "off"', () => {
  const g = gateState({
    crypto_ready: null, crypto_mode: 'off', crypto_reason: 'sealing_key_unreadable',
    crypto_detail: 'We could not check how to protect your keys…',
  });
  // Hidden for the same reason — never invite secrets into a form that may
  // not work — but NOT reported as a bot that never called.
  assert.strictEqual(g.showForms, false);
  assert.strictEqual(g.state, 'unknown');
  assert.notStrictEqual(g.state, 'off');
  assert.strictEqual(g.reason, 'sealing_key_unreadable');
});

test('an older server that sends no field is treated as ready', () => {
  // Absent is not "off": this is a deployment whose form worked yesterday, and
  // a model that read a missing field as a refusal would break it on upgrade.
  assert.strictEqual(gateState({ linked: true, connected: false }).showForms, true);
  assert.strictEqual(gateState({}).showForms, true);
  assert.strictEqual(gateState(null).showForms, true);
  assert.strictEqual(gateState(undefined).showForms, true);
});

test('null and undefined crypto_ready do not collapse into each other', () => {
  // `== null` is the shape this would most plausibly grow into, and it would
  // silently turn every older server's panel into an outage notice.
  assert.strictEqual(gateState({ crypto_ready: null }).showForms, false);
  assert.strictEqual(gateState({ crypto_ready: undefined }).showForms, true);
});

test('the panel actually uses the model', () => {
  // A pure model nothing calls is a module that cannot be wrong and cannot
  // help either — the exact failure this repo ratchets against.
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /CredsGateModel\.gateState\(/, 'the panel does not use the model');
  const page = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(page, /creds-gate-model\.js\?v=/, 'the page does not load the model');
});
