'use strict';
/**
 * Share-your-trade — the closed-trade "Share" button in the Portfolio view.
 * dashboard.js is browser DOM code, so we compile it (parse-only) and assert
 * the share wiring: percentage-only (never a dollar amount, so account size
 * doesn't leak), carries the invite link, and degrades to a Telegram intent.
 */
const test = require('node:test');
const assert = require('node:assert');
const { blockBetween } = require('./helpers/block');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('dashboard.js compiles (parse-only)', () => {
  assert.doesNotThrow(() => new vm.Script(DASH));
});

test('closed trades expose a Share action', () => {
  assert.match(DASH, /class="btn btn--sm share-trade"/, 'share button rendered per row');
  assert.match(DASH, /\.closest\('\.share-trade'\)/, 'delegated share handler');
});

test('share is percentage-only and recruit-linked', () => {
  // A percentage is computed and shared; no dollar PnL is put in the share text.
  assert.match(DASH, /toFixed\(2\)}%/, 'shares a PnL percentage');
  assert.match(DASH, /traded with RUNECLAW/, 'brand copy in the share text');
  assert.match(DASH, /\/api\/auth\/referrals/, 'share carries the invite link');
  assert.match(DASH, /navigator\.share/, 'native share sheet when available');
  assert.match(DASH, /t\.me\/share\/url/, 'Telegram share fallback');
  // Guard: the share text must not embed a dollar sign (no account-size leak).
  // blockBetween, not slice: `.share-trade` is a CSS class at 266855 and
  // `t.me/share/url` is the share builder at 164307, so slice() returned ""
  // and the assertion below asserted nothing. The markers now bracket the
  // share builder itself, and the helper fails loudly on an empty region.
  // 'a RUNECLAW decision picture' is unique in the bundle and sits INSIDE the
  // share-text template, so the block is guaranteed to contain the `text = \``
  // assignment the regex below looks for. The first attempt at this fix
  // anchored on 'navigator.share' — non-empty, but it started AFTER the
  // assignment, so the guard would have been just as dead with a healthier
  // looking slice. A region that excludes its own subject is the same defect
  // wearing a longer string.
  // The unique anchor sits INSIDE the template, so the block reaches back to
  // pick up the `const text = \`` that opens it. `const text = ` alone appears
  // six times in the bundle and indexOf would only find the right one by luck.
  const shareBlock = blockBetween(DASH, 'a RUNECLAW decision picture',
    't.me/share/url', { before: 120, pad: 400, label: 'share text builder' });
  assert.match(shareBlock, /text = `/,
    'the block no longer contains the share-text assignment this guards');
  assert.ok(!/text = `[^`]*\$\{?\s*(usd|dollar|pnl_usd|amount)/i.test(shareBlock),
    'share text carries no dollar amount');
});

test('share attaches the server-rendered card when the browser can take files', () => {
  assert.match(DASH, /\/api\/share\/card\?/, 'fetches the rendered card');
  assert.match(DASH, /navigator\.canShare/, 'feature-detects file sharing');
  assert.match(DASH, /files:\s*\[file\]/, 'attaches the card as a file');
  assert.match(DASH, /new File\(/, 'wraps the PNG blob as a File');
  // The card request carries the SAME percent-only data — no dollar params.
  const cardBlock = DASH.slice(DASH.indexOf('/api/share/card'), DASH.indexOf('navigator.canShare'));
  assert.ok(!/pnl_usd|size_usd|margin|equity/.test(cardBlock),
    'card request carries no dollar/size parameter');
  // Image failure must not break sharing: text/url share + t.me remain after.
  const after = DASH.slice(DASH.indexOf('navigator.canShare'));
  assert.match(after, /navigator\.share\(\{ text, url \}\)/, 'text share still follows');
  assert.match(after, /t\.me\/share\/url/, 'Telegram fallback still follows');
});

test('the decision picture shares a PLAN card: the target as a price move, the leverage, no return', () => {
  // 2026-10-20: this Share sent text and a link only, so the post's preview
  // was the home page's banner. It now asks for kind=plan with the numbers
  // levelMove returned, attaches the PNG, and keeps the text-only fallbacks.
  const block = blockBetween(DASH, 'a RUNECLAW decision picture', 'Star toggle',
    { before: 2400, pad: 0, label: 'decision picture share' });
  assert.match(block, /kind=plan/, 'asks for the plan card');
  assert.match(block, /target_pct=\$\{encodeURIComponent\(mv\.pct\.toFixed\(2\)\)\}/, 'the PRICE move, not the leveraged text');
  assert.match(block, /mv\.leverage \? `&leverage=/, 'a leverage only when levelMove knew one');
  assert.doesNotMatch(block, /pnl_pct|pnl_usd|size_usd|margin_usd|equity/, 'a plan carries no return and no dollars');
  assert.match(block, /files: \[file\]/, 'attaches the card');
  const after = block.slice(block.indexOf('navigator.canShare'));
  assert.match(after, /navigator\.share\(\{ text, url \}\)/, 'text share still follows');
  assert.match(after, /t\.me\/share\/url/, 'Telegram fallback still follows');
});
