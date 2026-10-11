/**
 * "Run on my bot" is offered only on a card whose rule the live bot runs.
 *
 * Every agent card carried the button, so ETH Moving-Average Trend, Daily
 * Volatility Rotation and ALT Sweep could be armed from the dashboard. The
 * live bot does not run their rules; arming one vetoed confirms against its
 * symbol list alone. The bot now refuses the arming (409 not_run_live) and the
 * catalogue carries `live_runs` per card, so the button is left off a card
 * that says false. A card from a bot that does not send the field keeps the
 * button: the bot refuses it with a sentence the toast shows.
 *
 * The card is built inside dashboard.js's agents panel renderer, which no
 * test can call, so the condition is read from the source, comments blanked.
 *
 * Run: npm test  (node --test test/)
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const SRC = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

test('the agent card offers Run on my bot only where live_runs is not false', () => {
  const at = SRC.indexOf('data-botstrat="${esc(a.id)}"');
  assert.ok(at > 0, 'the agent card button is where this test reads it');
  assert.equal(SRC.indexOf('data-botstrat="${esc(a.id)}"', at + 1), -1,
    'one agent-card button, read once');
  const head = SRC.slice(SRC.lastIndexOf('${', at), at);
  assert.match(head, /\$\{LOGGED_IN && a\.live_runs !== false \? `<button/);
});

test('both arms of the condition: false hides it, true and absent keep it', () => {
  const show = (a, LOGGED_IN = true) => Boolean(LOGGED_IN && a.live_runs !== false);
  assert.equal(show({ live_runs: false }), false);
  assert.equal(show({ live_runs: true }), true);
  assert.equal(show({}), true);
  assert.equal(show({ live_runs: true }, false), false);
});

test('the page loads the dashboard bundle this changed', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(html, /dashboard\.js\?v=233/);
});
