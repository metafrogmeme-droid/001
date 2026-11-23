'use strict';
/**
 * A member can turn off the mail that is not a security notice, from the
 * mail itself, with a link that changes that one preference and nothing
 * else.
 *
 * Driven through the real auth and email-preferences routes on the
 * in-memory database. Every refusal is asserted beside the success it
 * guards, so a route that did nothing at all could not pass.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'p'.repeat(48);
process.env.APP_BASE_URL = 'https://app.test';
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');

const mailer = require('../lib/mailer');
mailer.sendMail = async () => ({ skipped: false });
mailer.isConfigured = () => true;

const prefs = require('../lib/email_prefs');
const erasure = require('../lib/account_erasure');
const authModule = require('../auth');
const { pool } = require('../db');

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/email-preferences', require('../routes/email_preferences'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

async function call(method, p, { body, bearer } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const r = await fetch(base + p, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  return { status: r.status, json: text ? JSON.parse(text) : {}, cookie: r.headers.get('set-cookie') };
}

// Members are written straight to the store and handed a real session: the
// registration route is limited per address, and this file needs more
// members than that limit allows. The hash is of the password the deletion
// test confirms with.
const bcrypt = require('bcryptjs');
const HASH = bcrypt.hashSync('longenough1', 4);
async function member(email) {
  const [r] = await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, HASH]);
  const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [r.insertId]);
  return { token: authModule.signToken(rows[0]), id: r.insertId };
}

const optedOutAt = async (id) => {
  const [rows] = await pool.execute('SELECT id, plan, email_opted_out_at FROM users WHERE id = ?', [id]);
  return rows[0].email_opted_out_at;
};

const q = (t) => `?token=${encodeURIComponent(t)}`;

// ── the token ──────────────────────────────────────────────────────────────

test('a link token names one member and verifies only as signed', () => {
  const t = prefs.tokenFor(42);
  assert.match(t, /^42\.[A-Za-z0-9_-]{43}$/);
  assert.equal(prefs.verify(t), 42);
  const [id, mac] = t.split('.');
  const flip = mac.slice(0, -1) + (mac.endsWith('A') ? 'B' : 'A');
  assert.equal(prefs.verify(`${id}.${flip}`), null, 'a tampered signature verified');
  assert.equal(prefs.verify(`43.${mac}`), null, 'one member\'s signature verified for another');
  for (const junk of ['', '42', '42.', `0.${mac}`, `-1.${mac}`, `42.${mac}x`, `${t} `.repeat(2), null, undefined]) {
    assert.equal(prefs.verify(junk), null, JSON.stringify(junk));
  }
  // A different deployment secret signs a different link.
  const other = prefs.tokenFor(42, { BOT_SYNC_SECRET: 'q'.repeat(48) });
  assert.notEqual(other, t);
  assert.equal(prefs.verify(other), null);
});

test('without a usable secret no link is signed or checked, and that is not "invalid"', () => {
  for (const env of [{}, { BOT_SYNC_SECRET: 'short' }]) {
    assert.equal(prefs.available(env), false);
    assert.throws(() => prefs.tokenFor(1, env), (e) => e.code === 'EMAIL_PREFS_NO_KEY');
    assert.throws(() => prefs.verify('1.x', env), (e) => e.code === 'EMAIL_PREFS_NO_KEY');
  }
  assert.equal(prefs.available(), true);
});

test('the links are built from the public origin', () => {
  const t = prefs.tokenFor(7);
  assert.equal(prefs.preferencesUrl('https://app.test/', 7), `https://app.test/email-preferences?token=${t}`);
  assert.equal(prefs.oneClickUrl('https://app.test', 7), `https://app.test/api/email-preferences/unsubscribe?token=${t}`);
});

// ── the routes ─────────────────────────────────────────────────────────────

test('the link reads the preference, and a GET changes nothing', async () => {
  const { id } = await member('prefs-read@example.com');
  const t = prefs.tokenFor(id);
  for (let i = 0; i < 2; i++) {
    const r = await call('GET', `/api/email-preferences${q(t)}`);
    assert.equal(r.status, 200, JSON.stringify(r.json));
    assert.deepEqual(r.json, { subscribed: true, via: 'link' });
  }
  assert.equal(await optedOutAt(id), null, 'a GET opted the member out');
});

test('the link turns the mail off and back on, and only for its own member', async () => {
  const a = await member('prefs-a@example.com');
  const b = await member('prefs-b@example.com');
  const t = prefs.tokenFor(a.id);
  let r = await call('POST', `/api/email-preferences${q(t)}`, { body: { subscribed: false } });
  assert.deepEqual([r.status, r.json], [200, { subscribed: false, via: 'link' }]);
  assert.ok(await optedOutAt(a.id) instanceof Date);
  assert.equal(await optedOutAt(b.id), null, 'the other member was opted out too');
  r = await call('GET', `/api/email-preferences${q(t)}`);
  assert.equal(r.json.subscribed, false);
  r = await call('POST', '/api/email-preferences', { body: { token: t, subscribed: true } });
  assert.deepEqual([r.status, r.json], [200, { subscribed: true, via: 'link' }]);
  assert.equal(await optedOutAt(a.id), null);
});

test('a choice that is not true or false changes nothing', async () => {
  const { id } = await member('prefs-bad-body@example.com');
  const t = prefs.tokenFor(id);
  for (const subscribed of ['false', 0, null, undefined]) {
    const r = await call('POST', `/api/email-preferences${q(t)}`, { body: { subscribed } });
    assert.equal(r.status, 400, JSON.stringify([subscribed, r.json]));
  }
  assert.equal(await optedOutAt(id), null);
});

test('an invalid link is refused, and never falls back to whoever is signed in', async () => {
  const signedIn = await member('prefs-session@example.com');
  const victim = await member('prefs-victim@example.com');
  const good = prefs.tokenFor(victim.id);
  const forged = `${victim.id}.${good.split('.')[1].replace(/.$/, (c) => (c === 'A' ? 'B' : 'A'))}`;
  for (const bad of [forged, 'garbage', `${signedIn.id}.x`]) {
    const r = await call('POST', `/api/email-preferences${q(bad)}`,
      { body: { subscribed: false }, bearer: signedIn.token });
    assert.deepEqual([r.status, r.json], [400, { error: 'invalid_link' }], bad);
  }
  assert.equal(await optedOutAt(signedIn.id), null, 'an invalid link changed the signed-in member');
  assert.equal(await optedOutAt(victim.id), null, 'a forged link changed its target');
});

test('with no link, a signed-in member changes their own row, and a stranger nothing', async () => {
  const m = await member('prefs-own@example.com');
  let r = await call('POST', '/api/email-preferences', { body: { subscribed: false }, bearer: m.token });
  assert.deepEqual([r.status, r.json], [200, { subscribed: false, via: 'session' }]);
  assert.ok(await optedOutAt(m.id) instanceof Date);
  r = await call('GET', '/api/email-preferences', { bearer: m.token });
  assert.deepEqual(r.json, { subscribed: false, via: 'session' });
  r = await call('POST', '/api/email-preferences', { body: { subscribed: true } });
  assert.deepEqual([r.status, r.json], [401, { error: 'sign_in_or_use_the_link' }]);
  r = await call('GET', '/api/email-preferences');
  assert.equal(r.status, 401);
});

test('one-click unsubscribe opts out with the link alone, and needs the link', async () => {
  const m = await member('prefs-oneclick@example.com');
  let r = await call('POST', '/api/email-preferences/unsubscribe', { bearer: m.token });
  assert.deepEqual([r.status, r.json], [400, { error: 'invalid_link' }], 'one-click ran on a session');
  assert.equal(await optedOutAt(m.id), null);
  // RFC 8058 posts List-Unsubscribe=One-Click as a form; the body is ignored.
  const raw = await fetch(`${base}/api/email-preferences/unsubscribe${q(prefs.tokenFor(m.id))}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'List-Unsubscribe=One-Click',
  });
  assert.equal(raw.status, 200);
  assert.deepEqual(await raw.json(), { subscribed: false, via: 'link' });
  assert.ok(await optedOutAt(m.id) instanceof Date);
});

test('the link is not a login: no cookie, no session, and no use as a bearer', async () => {
  const m = await member('prefs-not-login@example.com');
  const t = prefs.tokenFor(m.id);
  for (const r of [
    await call('GET', `/api/email-preferences${q(t)}`),
    await call('POST', `/api/email-preferences${q(t)}`, { body: { subscribed: false } }),
    await call('POST', `/api/email-preferences/unsubscribe${q(t)}`),
  ]) {
    assert.equal(r.status, 200);
    assert.equal(r.cookie, null, 'a preferences response set a cookie');
    assert.deepEqual(Object.keys(r.json).sort(), ['subscribed', 'via'], 'it answered more than the preference');
  }
  const asBearer = await call('GET', '/api/auth/me', { bearer: t });
  assert.equal(asBearer.status, 401, 'the link token opened an authenticated route');
  // The other arm: the member's own session does open it.
  assert.equal((await call('GET', '/api/auth/me', { bearer: m.token })).status, 200);
});

test('an erased account\'s link opens nothing', async () => {
  const m = await member('prefs-erased@example.com');
  const t = prefs.tokenFor(m.id);
  assert.equal((await call('GET', `/api/email-preferences${q(t)}`)).status, 200);
  const r = await call('DELETE', '/api/auth/account',
    { bearer: m.token, body: { password: 'longenough1', confirm: 'DELETE' } });
  assert.equal(r.status, 200, JSON.stringify(r.json));
  const after = await call('POST', `/api/email-preferences${q(t)}`, { body: { subscribed: false } });
  assert.deepEqual([after.status, after.json], [400, { error: 'invalid_link' }]);
});

test('erasure clears both preference columns in the statement that tombstones the row', () => {
  const sql = erasure.erasurePlan(7, { addresses: [] }).at(-1).sql;
  for (const c of ['email_opted_out_at', 'onboarding_email_sent_at']) {
    assert.match(sql, new RegExp(`\\b${c} = NULL\\b`), c);
  }
});

test('with no key, a link answers "unavailable" while the signed-in path still works', async () => {
  const m = await member('prefs-nokey@example.com');
  const t = prefs.tokenFor(m.id);
  const saved = process.env.BOT_SYNC_SECRET;
  delete process.env.BOT_SYNC_SECRET;
  try {
    const r = await call('POST', `/api/email-preferences${q(t)}`, { body: { subscribed: false } });
    assert.deepEqual([r.status, r.json], [503, { error: 'email_preferences_unavailable' }]);
    assert.equal(await optedOutAt(m.id), null);
    const own = await call('POST', '/api/email-preferences', { body: { subscribed: false }, bearer: m.token });
    assert.equal(own.status, 200);
  } finally {
    process.env.BOT_SYNC_SECRET = saved;
  }
});
