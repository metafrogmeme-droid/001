'use strict';
// "I cannot reach the bot" and "you have no exchange" are different answers.
//
// Net worth gets exchange EQUITY from the bot gateway, but whether an exchange
// is CONNECTED is the web's own fact — it lives in exchange_status, and the
// venues panel reads it directly. When the gateway could not answer, net worth
// said "🏦 Exchange — none connected — connect keys here" while the panel
// directly above it said "BITGET connected", and the link sent a user off to
// re-enter keys that were already stored.
//
// Reported from production with both panels visible in one screenshot.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'networth.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('a gateway failure does not become "no exchange connected"', () => {
  assert.match(src, /SELECT exchange FROM exchange_status WHERE user_id = \? AND connected = 1/,
    'net worth never consults the web’s own record of connected venues');
  assert.match(src, /connected: true,\s*\n\s*ok: false,/,
    'a connected-but-unreadable exchange must report connected, not absent');
});

test('the two failure causes stay distinguishable', () => {
  // Not configured here vs the bot did not answer are different operator
  // problems and must not collapse into one sentence.
  assert.match(src, /the bot link is not configured here/);
  assert.match(src, /the bot did not answer just now/);
});

test('the gateway answer is never overwritten when it DID answer', () => {
  // Only a not-connected answer is second-guessed. A gateway that reported a
  // real equity must win — this must not fabricate a connection over it.
  assert.match(src, /if \(sections\.cex && !sections\.cex\.connected\)/,
    'the fallback must be gated on the gateway NOT reporting a connection');
});

test('the lookup failing leaves the original answer alone', () => {
  const block = src.slice(src.indexOf('SELECT exchange FROM exchange_status') - 400,
    src.indexOf('SELECT exchange FROM exchange_status') + 900);
  assert.match(block, /catch \(e\) \{ \/\* leave the gateway's answer as-is \*\/ \}/,
    'a failed DB read must not invent a connection state');
});

test('the panel renders the reason instead of a dollar figure', () => {
  // With connected:true, ok:false the existing render already shows detail —
  // this pins that path so the fix is not silently undone at the UI end.
  assert.match(dash, /c\.ok && c\.equity_usd != null \? '\$' \+ fmt\(c\.equity_usd, 2\) : `<span class="muted small">\$\{esc\(c\.detail \|\| 'unreadable'\)\}/,
    'the panel no longer surfaces the unreadable reason');
});

test('"none connected" survives only for a genuinely empty account', () => {
  // The message is still correct when it is true, so it stays — the bug was
  // showing it when it was false, not the wording.
  assert.match(dash, /none connected — <a href="#account\/akeys">connect keys here<\/a>/);
});
