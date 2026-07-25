'use strict';
/**
 * The public trader card had to earn its page. With one open trade and no
 * closes it showed a handle, a percentage and a shrug — while the platform
 * already served that trader's board rank, season standing and receipt
 * coverage. This pins the richer card AND the honesty rules that keep it
 * from inventing anything.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'trader.html'), 'utf8');

test('standings chips come from public feeds and omit what is not true', () => {
  assert.match(page, /function loadStandings\(handle\)/);
  // Both feeds are public and §4-safe (rank / percent / counts).
  assert.match(page, /fetch\('\/api\/arena\/leaderboard'/);
  assert.match(page, /fetch\('\/api\/arena\/season'/);
  // A chip renders ONLY when this handle is genuinely on that board.
  assert.match(page, /var me = d\.rows\.filter\(function \(x\) \{ return x\.handle === handle; \}\)\[0\];\s*\n\s*if \(!me\) return;/);
  // The season chip additionally requires a LIVE season.
  assert.match(page, /d\.season\.status !== 'live'/);
  // Receipt coverage is only claimed when something is actually sealed.
  assert.match(page, /if \(me\.sealed > 0\)/);
});

test('the empty card teaches instead of shrugging — and never invents a record', () => {
  assert.match(page, /position' \+ \(d\.open_positions === 1 \? '' : 's'\)/);
  assert.match(page, /The record starts the moment one closes/);
  assert.match(page, /No trades yet/);
  // It describes what WILL appear; it must not print fake stats.
  assert.match(page, /sealed at the moment it was opened/);
});

test('the animated headline degrades for reduced motion and keeps the real number', () => {
  assert.match(page, /function countUp\(\)/);
  assert.match(page, /\(prefers-reduced-motion: reduce\)'\)\.matches\) return;/);
  // The final frame is the true value, and the pre-animation markup already
  // contains it — so a visitor with JS disabled still reads the right number.
  assert.match(page, /id="retNum" data-to="/);
  assert.match(page, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*\.rise \{ opacity: 1/);
});

test('the page title keeps the information the server rendered', () => {
  // The old JS overwrote the SSR title (which carries the return) with a
  // blander string on every load.
  assert.match(page, /document\.title = d\.handle \+ ' — ' \+ _sign \+ f2\(d\.return_pct\) \+ '% on the RUNECLAW Arena'/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /on the RUNECLAW Arena/, 'SSR title shape must match');
});

test('receipt coverage is stated once, not twice', () => {
  assert.equal((page.match(/receipt-backed/g) || []).length, 1,
    'the chip row owns this claim — the meta row duplicated it');
});
