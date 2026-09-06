/**
 * Boot-time config audit (lib/config_audit.js) — the fail-loud guard.
 *
 * Pins: a fully-unset env warns (never fatal) in dev; a malformed WEB_CREDS_KEY
 * and a missing APP_BASE_URL while email/OAuth is on are fatal; production
 * invokes onFatal (→ process.exit in real boot) while dev never does; a
 * fully-configured env is clean.
 *
 * WEB_CREDS_KEY no longer gates the connect form — submissions are sealed to
 * the bot's published key (lib/sealing_key.js) with nothing to configure — so
 * what its findings are ABOUT changed even though their levels did not. Unset
 * now means new 2FA secrets are stored unencrypted (lib/totp.js), which is
 * still worth a warning; set-but-malformed is still fatal in production
 * because it looks configured and throws at use time. These tests check the
 * key and the level, deliberately, and not the wording — the wording is
 * checked where it is claimed.
 *
 * Run: npm test  (node --test test/)
 */

const test = require('node:test');
const assert = require('node:assert');
const { auditConfig, credsKeyState } = require('../lib/config_audit');

const silentLog = { warn() {}, error() {} };

function run(env, extra = {}) {
  return auditConfig({ env, log: silentLog, onFatal: () => {}, ...extra });
}
const keys = (f) => f.map((x) => x.key);
const fatals = (f) => f.filter((x) => x.level === 'fatal');

test('credsKeyState classifies unset / invalid / ok', () => {
  assert.strictEqual(credsKeyState(''), 'unset');
  assert.strictEqual(credsKeyState('not base64 %%%'), 'invalid'); // decodes to != 32 bytes
  assert.strictEqual(credsKeyState('c2hvcnQ='), 'invalid');        // "short" → 5 bytes
  assert.strictEqual(credsKeyState(Buffer.alloc(32).toString('base64')), 'ok');
  // url-safe base64 is accepted too
  assert.strictEqual(credsKeyState(Buffer.alloc(32).toString('base64url')), 'ok');
});

test('a bare dev env warns about every degraded flow but is never fatal', () => {
  const f = run({});
  assert.strictEqual(fatals(f).length, 0);
  for (const k of ['WEB_CREDS_KEY', 'WEB_GATEWAY_SECRET', 'SMTP', 'BOT_API_URL']) {
    assert.ok(keys(f).includes(k), `expected a warning for ${k}`);
  }
  // APP_BASE_URL only warns (not fatal) when nothing that needs it is configured
  const abu = f.find((x) => x.key === 'APP_BASE_URL');
  assert.strictEqual(abu.level, 'warn');
});

test('a set-but-malformed WEB_CREDS_KEY is fatal (configured, and throws at use)', () => {
  const f = run({ WEB_CREDS_KEY: 'c2hvcnQ=' });
  const cf = fatals(f).find((x) => x.key === 'WEB_CREDS_KEY');
  assert.ok(cf, 'malformed creds key must be fatal');
});

test('missing APP_BASE_URL is fatal once email OR OAuth is configured', () => {
  const withMail = run({ SMTP_HOST: 'smtp.x.io', MAIL_FROM: 'a@x.io' });
  assert.ok(fatals(withMail).some((x) => x.key === 'APP_BASE_URL'));
  const withOauth = run({ GOOGLE_CLIENT_ID: 'gid.apps.googleusercontent.com' });
  assert.ok(fatals(withOauth).some((x) => x.key === 'APP_BASE_URL'));
});

test('production invokes onFatal; dev does not', () => {
  let called = 0;
  // dev (no NODE_ENV): fatal finding exists but onFatal NOT called
  run({ WEB_CREDS_KEY: 'c2hvcnQ=' }, { onFatal: () => { called++; } });
  assert.strictEqual(called, 0, 'dev must not exit');
  // production: onFatal called
  run({ WEB_CREDS_KEY: 'c2hvcnQ=', NODE_ENV: 'production' }, { onFatal: () => { called++; } });
  assert.strictEqual(called, 1, 'production must invoke onFatal');
});

test('a fully-configured env produces no findings', () => {
  const f = run({
    WEB_CREDS_KEY: Buffer.alloc(32).toString('base64'),
    WEB_GATEWAY_SECRET: 'g'.repeat(32),
    APP_BASE_URL: 'https://app.runeclaw.io',
    SMTP_HOST: 'smtp.x.io', MAIL_FROM: 'noreply@runeclaw.io',
    BOT_API_URL: 'http://bot:8000',
    NODE_ENV: 'production',
  });
  assert.strictEqual(f.length, 0, `expected clean audit, got ${JSON.stringify(f)}`);
});
