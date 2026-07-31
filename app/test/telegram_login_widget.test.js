'use strict';
// On 2026-07-31 the operator registered www.humanoid-traders.com with
// BotFather's /setdomain — the one server-side step Telegram's Login Widget
// requires. The button still would not have appeared, and nothing anywhere
// would have said why.
//
// /auth/config gated `telegram_bot` on process.env.TELEGRAM_BOT_USERNAME. The
// landing page reads that field and only injects telegram-widget.js when it
// is truthy, so an unset env var produced an empty <div> — no error, no log,
// no failed request. Meanwhile the very same username is printed verbatim on
// the landing page, in all 14 i18n locales, and three times in the dashboard.
// It was never a secret; it was a required config value for a public constant.
//
// The line this file defends:
//
//   THE SECRET IS THE TOKEN, NOT THE USERNAME.
//
// The token gate stays — without it no Telegram login can be VERIFIED, and a
// button that cannot complete is worse than no button. What goes away is a
// second gate on a value the site already publishes.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const authSrc = fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8');
const indexSrc = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('the public bot username has a default, not a required env var', () => {
  const i = authSrc.indexOf('telegram_bot:');
  assert.ok(i > 0, 'telegram_bot must be advertised in /auth/config');
  const line = authSrc.slice(i, i + 260);
  assert.match(line, /HTRUNECLAW_bot/,
    'an unset TELEGRAM_BOT_USERNAME silently hid the login button');
  assert.match(line, /TELEGRAM_BOT_USERNAME/,
    'forks must still be able to override the default');
});

test('the verifying token still gates the button', () => {
  const i = authSrc.indexOf('telegram_bot:');
  const line = authSrc.slice(i, i + 260);
  assert.match(line, /TELEGRAM_BOT_TOKEN/,
    'advertising a login that cannot be verified is worse than hiding it');
});

test('the bot TOKEN is never sent to the browser', () => {
  // The whole point of the distinction above. /auth/config is public.
  // Comments are stripped first: prose ABOUT the token is not a leak of it,
  // and scanning raw source made this assertion fail on its own rationale.
  const start = authSrc.indexOf("router.get('/config'");
  const body = authSrc.slice(start, start + 1800)
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  assert.doesNotMatch(body, /TELEGRAM_BOT_TOKEN\s*[,}]/,
    'the token may only appear inside a boolean guard, never as a value');
  // Positive form: every code mention is a guard, followed by && .
  const guards = body.match(/TELEGRAM_BOT_TOKEN/g) || [];
  const ands = body.match(/TELEGRAM_BOT_TOKEN\s*&&/g) || [];
  assert.strictEqual(ands.length, guards.length,
    'every TELEGRAM_BOT_TOKEN in the public config must be a boolean guard');
});

test('the page injects the widget from the config field it is given', () => {
  assert.match(indexSrc, /cfg\.telegram_bot/);
  assert.match(indexSrc, /data-telegram-login/);
  // A hardcoded username in the page would drift from the one the server
  // advertises, and only one of the two is the one BotFather knows.
  const i = indexSrc.indexOf('data-telegram-login');
  assert.match(indexSrc.slice(i, i + 120), /cfg\.telegram_bot/,
    'the widget must use the server-advertised username, not its own copy');
});
