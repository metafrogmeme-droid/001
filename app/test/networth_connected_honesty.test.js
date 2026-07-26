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

// ── The same correction, the other panel ─────────────────────────────────────
// "FUNDS BY VENUE & WALLET" reads /api/holdings, a different endpoint from the
// net-worth card, and it carried the identical bug: an empty (or absent)
// gateway venue list rendered as "none connected — connect keys here" while
// exchange_status held a working Bitget connection.

const holdings = fs.readFileSync(path.join(__dirname, '..', 'lib', 'holdings.js'), 'utf8');

test('holdings consults exchange_status before claiming none connected', () => {
  assert.match(holdings, /SELECT exchange FROM exchange_status WHERE user_id = \? AND connected = 1/,
    'the funds-by-venue panel still trusts the gateway alone');
  assert.match(holdings, /if \(!venues\.length\)/,
    'the fallback must only run when the gateway listed no venue');
});

test('holdings distinguishes "no balance" from "no answer"', () => {
  assert.match(holdings, /the bot reported no balance for it/);
  assert.match(holdings, /the bot did not answer/);
});

test('a failed lookup leaves the gateway answer untouched', () => {
  const tail = holdings.slice(holdings.indexOf('SELECT exchange FROM exchange_status'));
  assert.match(tail, /catch \(e\) \{ \/\* leave the gateway's answer as-is \*\/ \}/);
});

// ── RPC failures must be diagnosable ────────────────────────────────────────
// Three chains all reported "rpc unreadable" in production with no way to tell
// blocked VPC egress from a 429 from a bad URL — the catch blocks discarded the
// reason entirely.

const walletLib = fs.readFileSync(path.join(__dirname, '..', 'lib', 'wallet.js'), 'utf8');

test('the RPC failure reason is captured, not swallowed', () => {
  assert.match(walletLib, /error_detail: errorDetail/,
    'the chain reports THAT it failed but never WHY');
  assert.match(walletLib, /catch \(e\) \{ sawError = true; note\(e\); \}/,
    'a catch still discards the error');
  assert.match(holdings, /c\.error_detail \? `\$\{c\.error\} — \$\{c\.error_detail\}`/,
    'the reason never reaches the page');
});

test('a chain can be given several RPC endpoints', () => {
  // One hardcoded public RPC is fragile: datacentre IPs get rate-limited, and
  // an operator had no way to supply a working alternative without a code change.
  assert.match(walletLib, /configured\.split\(','\)/, 'no comma-separated endpoint list');
  assert.match(walletLib, /p\.rotate = function \(\)/, 'no rotation to the next endpoint');
  assert.match(walletLib, /if \(provider && typeof provider\.rotate === 'function' && provider\.rotate\(\)\)/,
    'a failed read never tries the next endpoint');
});

test('an injected test provider still works unchanged', () => {
  // setProviderFactory hands back a plain provider, not the rotating wrapper.
  assert.match(walletLib, /const active = \(\) => \(provider && provider\.current\) \? provider\.current : provider;/,
    'the plain provider shape used by tests would break');
});
