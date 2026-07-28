'use strict';

/**
 * Two silent gateway traps, both hit in production on 2026-07-28.
 *
 * 1. A NEAR-MISS ENV NAME. The deployment set GATEWAY_URL; this app reads
 *    BOT_GATEWAY_URL. Nothing complained — the app kept its localhost default
 *    and every chat request went nowhere, which on a managed page host means
 *    the bot is not there at all. "Set a variable the app never reads" looked
 *    identical to "set nothing", and the difference cost an afternoon.
 *
 * 2. PLAIN HTTP TO A REMOTE HOST. WEB_GATEWAY_SECRET is presented in that
 *    request, so over http:// to anything but localhost it crosses the network
 *    in cleartext on every message. That secret grants chat and trade access
 *    to the bot — a disclosure, not a degrade.
 *
 * The second is deliberately a WARNING and not a fatal. Refusing to boot would
 * take down a site that is otherwise serving perfectly, over a transport the
 * operator may have chosen while standing the gateway up. An outage is a
 * certain harm today against a possible one; the finding is loud, reaches
 * /diagz, and names the remedy without being destructive.
 */

const test = require('node:test');
const assert = require('node:assert');

const { auditConfig } = require('../lib/config_audit');

const SECRET = 'x'.repeat(40);
const QUIET = { warn() {}, error() {} };

function audit(env) {
  let refused = false;
  const findings = auditConfig({
    env: { WEB_GATEWAY_SECRET: SECRET, ...env },
    log: QUIET,
    onFatal: () => { refused = true; },
  });
  return { gw: findings.filter((f) => f.key === 'BOT_GATEWAY_URL'), refused };
}

// ── the name mismatch ─────────────────────────────────────────────────────

test('a near-miss variable name is reported, not silently ignored', () => {
  const { gw } = audit({ GATEWAY_URL: 'http://1.2.3.4:8080' });
  assert.equal(gw.length, 1);
  assert.match(gw[0].msg, /GATEWAY_URL is set/);
  assert.match(gw[0].msg, /reads BOT_GATEWAY_URL only/);
  assert.match(gw[0].msg, /localhost:8080/, 'say what it falls back to');
});

test('other plausible near-misses are caught too', () => {
  for (const key of ['BOT_GATEWAY', 'BOT_URL', 'GATEWAY_BASE_URL']) {
    const { gw } = audit({ [key]: 'http://1.2.3.4:8080' });
    assert.equal(gw.length, 1, `${key} should be reported`);
    assert.match(gw[0].msg, new RegExp(key));
  }
});

test('nothing set at all is NOT reported as a mismatch', () => {
  // Absent config is already covered by the WEB_GATEWAY_SECRET finding; a
  // second warning saying "you named it wrong" when nothing was named would
  // be noise.
  assert.equal(audit({}).gw.length, 0);
});

test('a correctly named variable produces no mismatch finding', () => {
  assert.equal(audit({ BOT_GATEWAY_URL: 'https://bot.example.com' }).gw.length, 0);
});

// ── the transport ─────────────────────────────────────────────────────────

test('plain HTTP to a REMOTE host warns that the secret is in cleartext', () => {
  const { gw } = audit({ BOT_GATEWAY_URL: 'http://139.0.0.1:8080' });
  assert.equal(gw.length, 1);
  assert.equal(gw[0].level, 'warn');
  assert.match(gw[0].msg, /cleartext/);
  assert.match(gw[0].msg, /139\.0\.0\.1/, 'name the host so it is actionable');
  assert.match(gw[0].msg, /Use https/, 'and the remedy');
});

test('it NEVER refuses to start, even in production', () => {
  // The whole point: an outage is a certain harm, this is a possible one.
  const { refused, gw } = audit({
    NODE_ENV: 'production', BOT_GATEWAY_URL: 'http://139.0.0.1:8080',
  });
  assert.equal(refused, false, 'a warning must not take a serving site down');
  assert.equal(gw[0].level, 'warn');
});

test('localhost over plain HTTP is fine — it leaves no wire', () => {
  for (const host of ['localhost', '127.0.0.1', 'bot.internal', 'box.local']) {
    assert.equal(audit({ BOT_GATEWAY_URL: `http://${host}:8080` }).gw.length, 0,
      `${host} should not warn`);
  }
});

test('https to a remote host is fine', () => {
  assert.equal(audit({ BOT_GATEWAY_URL: 'https://bot.example.com:8080' }).gw.length, 0);
});

test('an unparseable URL is reported rather than ignored', () => {
  const { gw } = audit({ BOT_GATEWAY_URL: 'not a url' });
  assert.equal(gw.length, 1);
  assert.match(gw[0].msg, /not a parseable URL/);
});

test('no finding ever contains the secret', () => {
  for (const env of [{ GATEWAY_URL: 'http://1.2.3.4:8080' },
    { BOT_GATEWAY_URL: 'http://1.2.3.4:8080' },
    { NODE_ENV: 'production', BOT_GATEWAY_URL: 'http://1.2.3.4:8080' }]) {
    for (const f of audit(env).gw) {
      assert.ok(!f.msg.includes(SECRET), 'the secret must never be echoed');
    }
  }
});
