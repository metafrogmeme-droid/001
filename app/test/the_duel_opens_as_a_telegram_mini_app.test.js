'use strict';
/**
 * The Daily Duel as a Telegram Mini App (lib/tg_webapp.js, routes/tg_auth.js,
 * routes/tgapp.js, public/js/tg-app.js).
 *
 *   the signature   Telegram's third-party check, as its docs give it: the
 *                   bot id line, the sorted fields without hash and
 *                   signature, an Ed25519 signature under Telegram's key.
 *                   Driven with a key this test holds; Telegram's own keys
 *                   are pinned and must parse as Ed25519 keys.
 *   the refusals    another bot, an edited field, a launch over an hour old,
 *                   a field twice, no signature, no bot id configured (ours,
 *                   503, never "you are not who you say")
 *   the account     the same one "Log in with Telegram" finds or creates,
 *                   and a working session for the Duel
 *   the page        Telegram's script and ours only, nothing sent anywhere
 *                   but our own origin, framable by Telegram's web client,
 *                   no cookie, the Pi app's scripts at the Pi app's versions,
 *                   nothing about a token or a presale
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.DUEL_BOT_ID;
delete process.env.TELEGRAM_WEBAPP_ENV;
delete process.env.TG_FRAME_ANCESTORS;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const tg = require('../lib/tg_webapp');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

// Telegram's key reader as shipped; the route test below stands this test's key in for it.
const realKeyOf = tg.keyOf;
const BOT_ID = '7123456789';
const NOW = Date.parse('2026-10-22T12:00:00Z');
const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
const KEY_HEX = publicKey.export({ format: 'der', type: 'spki' }).subarray(12).toString('hex');

/** A launch as Telegram would sign it for `botId`, with `fields` (auth_date in seconds). */
function launch(fields, { botId = BOT_ID, key = privateKey, tamper = null } = {}) {
  const params = new URLSearchParams(fields);
  const sig = crypto.sign(null, Buffer.from(tg.dataCheckString(botId, params), 'utf8'), key).toString('base64url');
  if (tamper) tamper(params);
  params.set('hash', 'f'.repeat(64));
  params.set('signature', sig);
  return params.toString();
}
const USER = JSON.stringify({ id: 555000111, first_name: 'Ana', username: 'ana_calls', language_code: 'pt' });
const FRESH = { auth_date: String(NOW / 1000 - 60), query_id: 'AAHdF6IQAAAAAN0XohDhrOrc', user: USER };
const opts = { botId: BOT_ID, keyHex: KEY_HEX, now: NOW };

test('the check string is the one Telegram documents, and Telegram\'s own keys are Ed25519 keys', () => {
  // core.telegram.org/bots/webapps, "Validating data for Third-Party Use":
  // '12345678:WebAppData\nauth_date=<auth_date>\nquery_id=<query_id>\nuser=<user>'
  const p = new URLSearchParams({ user: 'U', hash: 'h', auth_date: 'A', signature: 's', query_id: 'Q' });
  assert.strictEqual(tg.dataCheckString('12345678', p), '12345678:WebAppData\nauth_date=A\nquery_id=Q\nuser=U');
  assert.deepStrictEqual(tg.TELEGRAM_KEYS, {
    production: 'e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d',
    test: '40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec',
  });
  for (const hex of Object.values(tg.TELEGRAM_KEYS)) {
    const k = crypto.createPublicKey({ key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), Buffer.from(hex, 'hex')]), format: 'der', type: 'spki' });
    assert.strictEqual(k.asymmetricKeyType, 'ed25519');
  }
  assert.strictEqual(realKeyOf({}), tg.TELEGRAM_KEYS.production);
  assert.strictEqual(realKeyOf({ TELEGRAM_WEBAPP_ENV: 'test' }), tg.TELEGRAM_KEYS.test);
  assert.strictEqual(realKeyOf({ TELEGRAM_WEBAPP_ENV: KEY_HEX }), tg.TELEGRAM_KEYS.production, 'the environment names an environment, never a key');
  assert.deepStrictEqual([tg.botIdOf({ DUEL_BOT_ID: ` ${BOT_ID} ` }), tg.botIdOf({ DUEL_BOT_ID: '123:ABC' }), tg.botIdOf({})], [BOT_ID, null, null]);
});

test('a launch Telegram signed for this bot, lately, is a sign-in; the person is the user field\'s id', () => {
  const v = tg.verifyInitData(launch(FRESH), opts);
  assert.deepStrictEqual(v, { ok: true, auth_date: NOW / 1000 - 60, user: { id: '555000111', username: 'ana_calls', language_code: 'pt' } });
  // The bot-token HMAC (`hash`) is not ours to check, and does not matter.
  const other = launch(FRESH).replace(/hash=f+/, 'hash=0');
  assert.strictEqual(tg.verifyInitData(other, opts).ok, true);
});

test('another bot, an edited field, an old launch, a field twice, no signature: each refused, and named', () => {
  const R = (data, o = opts) => tg.verifyInitData(data, o).reason;
  assert.strictEqual(R(launch(FRESH, { botId: '7000000001' })), 'bad_signature', 'signed for another bot');
  assert.strictEqual(R(launch(FRESH, { tamper: (p) => p.set('user', USER.replace('555000111', '555000112')) })), 'bad_signature');
  assert.strictEqual(R(launch(FRESH, { key: crypto.generateKeyPairSync('ed25519').privateKey })), 'bad_signature', 'another key');
  assert.strictEqual(R(launch({ ...FRESH, auth_date: String(NOW / 1000 - tg.MAX_AGE_S - 1) })), 'expired');
  assert.strictEqual(tg.verifyInitData(launch({ ...FRESH, auth_date: String(NOW / 1000 - tg.MAX_AGE_S + 5) }), opts).ok, true, 'inside the hour');
  assert.strictEqual(R(launch({ ...FRESH, auth_date: String(NOW / 1000 + 3600) })), 'malformed', 'a launch from the future');
  assert.strictEqual(R(launch(FRESH) + '&user=' + encodeURIComponent(USER)), 'malformed', 'the same field twice');
  assert.strictEqual(R(launch(FRESH).replace(/&signature=[^&]+/, '')), 'unsigned');
  assert.strictEqual(R(launch(FRESH).replace(/signature=([^&]+)/, (m, s) => `signature=${s.replace(/-/g, '+').replace(/_/g, '/')}x`)), 'unsigned');
  assert.strictEqual(R(launch({ auth_date: FRESH.auth_date, query_id: 'q' })), 'no_user');
  assert.strictEqual(R(launch({ ...FRESH, user: JSON.stringify({ id: 'not-a-number' }) })), 'no_user');
  assert.strictEqual(R('', opts), 'malformed');
  assert.strictEqual(R('a=' + 'x'.repeat(5000)), 'malformed');
  assert.strictEqual(R(launch(FRESH), { ...opts, botId: null }), 'unconfigured');
  // With no options it reads the environment: no bot id, so ours to fix.
  assert.strictEqual(tg.verifyInitData(launch(FRESH)).reason, 'unconfigured');
});

// ── the route ────────────────────────────────────────────────────────────────

let server; let base;
const post = (p, body, headers = {}) => new Promise((resolve, reject) => {
  const data = JSON.stringify(body);
  const req = http.request(`${base}${p}`, { method: 'POST', headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data), ...headers } }, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: d ? JSON.parse(d) : null }));
  });
  req.on('error', reject); req.end(data);
});
const get = (p, headers = {}) => new Promise((resolve, reject) => {
  http.get(`${base}${p}`, { headers }, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: d }));
  }).on('error', reject);
});

test.before(async () => {
  tg.keyOf = () => KEY_HEX;   // the route asks through the module, so this test's key stands in for Telegram's
  const app = express();
  app.use(express.json());
  app.use('/api/tgapp', require('../routes/tg_auth'));
  app.use('/api/duel', require('../routes/duel'));
  app.use('/tg', require('../routes/tgapp'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { tg.keyOf = realKeyOf; delete process.env.DUEL_BOT_ID; if (server) server.close(); });

const now = () => ({ ...FRESH, auth_date: String(Math.floor(Date.now() / 1000) - 30) });

test('the sign-in route: ours when unconfigured, a refusal with its reason, else the Telegram login\'s account', async () => {
  delete process.env.DUEL_BOT_ID;
  let r = await post('/api/tgapp/signin', { init_data: launch(now()) });
  assert.deepStrictEqual([r.status, r.body.error], [503, 'tgapp_unconfigured'], 'no bot id is ours, not a refusal');
  process.env.DUEL_BOT_ID = BOT_ID;
  r = await post('/api/tgapp/signin', { init_data: launch(now(), { botId: '7000000001' }) });
  assert.deepStrictEqual([r.status, r.body.reason], [401, 'bad_signature']);
  r = await post('/api/tgapp/signin', { init_data: launch({ ...FRESH, auth_date: String(Math.floor(Date.now() / 1000) - 7200) }) });
  assert.deepStrictEqual([r.status, r.body.reason], [401, 'expired']);
  assert.strictEqual(r.headers['cache-control'], 'no-store');

  // The account "Log in with Telegram" made for this id (auth.js, the same call) is the one signed into.
  const made = await require('../auth').findOrCreateOAuthUser({ provider: 'telegram', providerId: '555000111', email: null });
  r = await post('/api/tgapp/signin', { init_data: launch(now()) });
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  assert.ok(r.body.token);
  const who = require('jsonwebtoken').verify(r.body.token, process.env.JWT_SECRET);
  assert.strictEqual(Number(who.user_id), Number(made.id), 'the same player whichever door they used');
  assert.ok(!('telegram_id' in r.body) && !('user' in r.body), 'the answer is a session, not the launch read back');
  // The session plays the Duel.
  const today = await get('/api/duel/today', { authorization: `Bearer ${r.body.token}` });
  assert.notStrictEqual(today.status, 401, today.text.slice(0, 200));
  // A Telegram id with no account gets one, once.
  const fresh = { ...now(), user: JSON.stringify({ id: 555000222, first_name: 'Bo' }) };
  const a = await post('/api/tgapp/signin', { init_data: launch(fresh) });
  const b = await post('/api/tgapp/signin', { init_data: launch(fresh) });
  const ida = require('jsonwebtoken').verify(a.body.token, process.env.JWT_SECRET).user_id;
  const idb = require('jsonwebtoken').verify(b.body.token, process.env.JWT_SECRET).user_id;
  assert.strictEqual(ida, idb);
  const [rows] = await pool.execute('SELECT id FROM users WHERE telegram_id = ?', ['555000222']);
  assert.strictEqual(rows.length, 1);
});

test('the page: Telegram\'s script and ours, our origin only, framable by Telegram\'s web client, no cookie, nothing to sell', async () => {
  const r = await get('/tg', { 'accept-language': 'de-DE,de;q=0.9' });
  assert.strictEqual(r.status, 200);
  const csp = r.headers['content-security-policy'];
  assert.match(csp, /script-src 'self' https:\/\/telegram\.org(;|$)/);
  assert.match(csp, /connect-src 'self'(;|$)/, 'a session token on this page can be sent nowhere else');
  assert.match(csp, /frame-ancestors 'self' https:\/\/web\.telegram\.org(;|$)/);
  assert.ok(!('x-frame-options' in r.headers) && !('set-cookie' in r.headers));
  assert.strictEqual(r.headers['cache-control'], 'no-store');
  const scripts = [...r.text.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(scripts.includes(require('../routes/tgapp').TELEGRAM_JS));
  assert.ok(scripts.every((s) => s.startsWith('/') || s === require('../routes/tgapp').TELEGRAM_JS), scripts.join(' '));
  const pi = require('../routes/piapp').SCRIPTS;
  for (const s of scripts.filter((x) => /^\/js\/(pi-|duel|miniapp|arena-session|embed-arena)/.test(x) && !/pi-i18n\/de/.test(x))) {
    assert.ok(pi.includes(s), `${s} is served at the Pi app's version`);
  }
  assert.ok(scripts.some((s) => /^\/js\/pi-i18n\/de\.js\?v=/.test(s)), 'the reader\'s language');
  assert.strictEqual(scripts[scripts.length - 1], '/js/tg-app.js?v=1', 'the host last');
  assert.ok(!/RCLAW|presale|wallet|ethers|solana/i.test(r.text), 'no token, presale or wallet on the page');
  const host = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'tg-app.js'), 'utf8'));
  assert.match(host, /arena: false/);
  assert.match(host, /autoSignIn: true/);
  assert.match(host, /init_data: data/);
  assert.ok(!/initDataUnsafe/.test(host), 'the page\'s own reading of who this is is never sent');
  process.env.TG_FRAME_ANCESTORS = 'https://web.telegram.org https://example.test';
  assert.match((await get('/tg')).headers['content-security-policy'], /frame-ancestors https:\/\/web\.telegram\.org https:\/\/example\.test/);
  delete process.env.TG_FRAME_ANCESTORS;
});
