'use strict';
/**
 * The website's own not-ready answer is not "the bot", and not "not linked".
 *
 * Reported 8 October with a screenshot of the Account page: Profile,
 * Membership, Wallet link, Push notifications and Invite friends each said
 * "This site is not connected to the trading bot — the operator needs to
 * finish that setup", with no Retry, while the bot answered /connect and
 * /exchange in Telegram the same minute. The Telegram Link card meanwhile
 * offered to generate a link token for an account that was already linked.
 *
 * Profile reads /api/auth/me, the website's own database, and nothing else.
 * The one 503 that route can give is server.js's not-ready gate: until the
 * schema is migrated every /api/ route answers 503 {error: 'starting',
 * reason} with Retry-After. The panel model had no row for `starting`, so it
 * fell to the 503 row, which is the bot-not-configured sentence with no
 * button. And the Telegram card read `linked` off that same failed read, where
 * failed is false.
 *
 * `starting` has its own sentence now, with Retry, and the Telegram card
 * guards on the read before it says anything about the link.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const M = require('../public/js/panel-error-model');
const i18n = require('../public/js/i18n');
const { codeOnly } = require('./helpers/code_only');

const SERVER = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
const DASH = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

/** The code the not-ready gate puts on the wire, read from the gate itself. */
function gateCode() {
  const gate = SERVER.slice(SERVER.indexOf('const DB_FREE_API'), SERVER.indexOf("app.use('/api/auth'"));
  const m = gate.match(/res\.status\(503\)\.json\(\{\s*error:\s*'([a-z_]+)'/);
  assert.ok(m, 'the not-ready gate no longer answers 503 with a literal error code');
  return m[1];
}

test('the not-ready gate\'s 503 is the site starting, with Retry', () => {
  const code = M.codeOf({ error: gateCode(), reason: 'db_timeout' });
  assert.equal(code, 'starting');
  const v = M.panelFailure({ status: 503, code });
  assert.equal(v.key, 'dd.err_starting');
  assert.equal(v.action, 'retry', 'the gate sends Retry-After: the condition is not permanent');
  assert.doesNotMatch(v.fallback, /trading bot/, 'it is the website, not the bot');
});

test('a 503 that is the bot not being configured keeps its sentence', () => {
  // The other arm: the row this change routes around still answers its own case.
  for (const err of [{ status: 503 }, { status: 503, code: 'gateway_disabled' }]) {
    const v = M.panelFailure(err);
    assert.equal(v.key, 'dd.err_bot_unlinked', JSON.stringify(err));
    assert.equal(v.action, 'none');
  }
});

test('the sentence is in all fourteen languages', () => {
  const e = i18n.STRINGS['dd.err_starting'];
  assert.ok(e, 'dd.err_starting is not in the dictionary');
  for (const l of i18n.LANGS) assert.ok(String(e[l.code] || '').trim(), `missing ${l.code}`);
});

/** The body of the loader passed to renderPanel(C(id), ...), brace-matched. */
function loaderBody(id) {
  const at = DASH.indexOf(`renderPanel(C('${id}'), async () => {`);
  assert.ok(at > 0, `the ${id} loader moved — re-derive this test`);
  const open = DASH.indexOf('{', DASH.indexOf('=>', at));
  let depth = 0;
  for (let i = open; i < DASH.length; i += 1) {
    if (DASH[i] === '{') depth += 1;
    else if (DASH[i] === '}') { depth -= 1; if (depth === 0) return DASH.slice(open, i + 1); }
  }
  throw new Error(`could not find the end of the ${id} loader`);
}

test('the Telegram card guards on the profile read before it speaks about the link', () => {
  const body = loaderBody('atg');
  const guard = body.indexOf('mustRead(me)');
  const claim = body.indexOf('if (linked)');
  assert.ok(guard > 0, 'the Telegram card reads `linked` from a read nobody checked');
  assert.ok(claim > guard, 'the guard must come before the card says linked or not');
  // And `linked` is still read off that same `me`, so the guard covers it.
  assert.match(DASH, /const linked = !!me\?\.data\?\.telegram_linked;/);
});

test('the profile card was already guarded, which is the shape the Telegram card now has', () => {
  assert.match(loaderBody('aprof'), /^\{\s*mustRead\(me\);/);
});
