/**
 * The keys card shows what the bot holds, not this site's last ack.
 *
 * Reported 8 October, twice in one day. The card showed Bitget connected while
 * the bot held no key file at all, then showed it not connected after the key
 * was re-linked with /connect in Telegram. The card was a copy of this site's
 * own last ack: it learned a venue's state only by answering a key submitted
 * here, so nothing done in Telegram, and nothing lost on the bot, reached it.
 *
 * The bot now sends a complete held-venues report (bot/utils/credential_pull.py
 * report_held_venues), and /api/bot/sync/credentials/state reconciles
 * exchange_venue_status with it. Driven: the real route on the in-memory store.
 *
 * Run: npm test  (node --test test/)
 */
'use strict';

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const { pool } = require('../db');

let server, base;
const ids = {};

function post(p, body, secret = process.env.BOT_SYNC_SECRET) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(`${base}${p}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(secret ? { 'x-bot-secret': secret } : {}) },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.write(payload);
    r.end();
  });
}

async function user(name, tg, linked = true) {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    [`${name}@held.test`, 'x', name]);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', [`${name}@held.test`]);
  const id = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', [tg, id]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [id]);
  u[0].telegram_linked = linked; // MemoryDB: set the flag directly
  ids[name] = id;
  return id;
}

async function row(uid, venue, connected, why = null) {
  if (why) {
    await pool.execute(`INSERT INTO exchange_venue_status (user_id, exchange, connected, last_error)
      VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE connected = VALUES(connected), last_error = VALUES(last_error)`,
    [uid, venue, connected, why]);
  } else {
    await pool.execute(`INSERT INTO exchange_venue_status (user_id, exchange, connected, last_error)
      VALUES (?, ?, ?, NULL) ON DUPLICATE KEY UPDATE connected = VALUES(connected), last_error = NULL`,
    [uid, venue, connected]);
  }
}

async function card(uid) {
  const [rows] = await pool.execute(
    'SELECT connected, exchange, last_error FROM exchange_venue_status WHERE user_id = ?', [uid]);
  return Object.fromEntries(rows.map((r) => [r.exchange, [!!r.connected, r.last_error ?? null]]));
}

const REJECTED = 'Bybit mainnet does not know this API key (code 10003).';

test.before(async () => {
  // A: re-linked Bitget in Telegram; still has a stale Bybit "connected" row.
  const a = await user('a', '9101');
  await row(a, 'bybit', true);
  // B: the bot holds nothing for B any more (keys lost), the card says connected.
  const b = await user('b', '9102');
  await row(b, 'bitget', true);
  // C: an account that unlinked Telegram: left alone.
  const c = await user('c', '9103', false);
  await row(c, 'bitget', true);
  // D: a Bybit key submitted here and still in flight; a stale Bitget row.
  const d = await user('d', '9104');
  await row(d, 'bybit', true);
  await row(d, 'bitget', true);
  await pool.execute(`INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
    VALUES (?, ?, ?, 'connect', ?)`, [d, '9104', 'bybit', '{}']);
  // E: a key this site's form submitted and the bot refused: the reason stays.
  const e = await user('e', '9105');
  await row(e, 'bybit', false, REJECTED);
  // G: a new Bybit key in flight while the bot still holds the old one: the
  // report's "held" waits for the ack rather than overwrite the refusal shown.
  const g = await user('g', '9107');
  await row(g, 'bybit', false, REJECTED);
  await pool.execute(`INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
    VALUES (?, ?, ?, 'connect', ?)`, [g, '9107', 'bybit', '{}']);
  // F: not in the report at all, with a Bybit key in flight: the sweep leaves
  // that venue to its ack and turns the other one off.
  const f = await user('f', '9106');
  await row(f, 'bybit', true);
  await row(f, 'bitget', true);
  await pool.execute(`INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
    VALUES (?, ?, ?, 'connect', ?)`, [f, '9106', 'bybit', '{}']);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => server && server.close());

const REPORT = {
  complete: true,
  users: [
    { telegram_id: '9101', venues: { bitget: 'held', okx: 'unreadable', ftx: 'held' } },
    { telegram_id: '9104', venues: {} },
    { telegram_id: '9105', venues: { bitget: 'held' } },
    { telegram_id: '9107', venues: { bybit: 'held' } },
    { telegram_id: '9999', venues: { bitget: 'held' } },  // a Telegram user with no account here
  ],
};

test('a refused report changes nothing', async () => {
  const before = await card(ids.b);
  for (const bad of [
    { users: REPORT.users },                                         // not said to be complete
    { complete: true },                                               // no users
    { complete: true, users: [{ telegram_id: 'abc', venues: {} }] },  // not a Telegram id
    { complete: true, users: [{ telegram_id: '9101', venues: { bitget: 'maybe' } }] },
    { complete: true, users: [{ telegram_id: '9101', venues: [] }] },
    { complete: true, users: [{ telegram_id: '9101', venues: {} }, { telegram_id: '9101', venues: {} }] },
  ]) {
    const r = await post('/api/bot/sync/credentials/state', bad);
    assert.equal(r.status, 400, JSON.stringify(bad));
    assert.equal(r.data.error, 'incomplete_report');
  }
  assert.deepEqual(await card(ids.b), before, 'a refused report turned a card off');
  const noAuth = await post('/api/bot/sync/credentials/state', REPORT, 'wrong');
  assert.notEqual(noAuth.status, 200);
  assert.deepEqual(await card(ids.b), before);
});

test('the card follows the report', async () => {
  const r = await post('/api/bot/sync/credentials/state', REPORT);
  assert.equal(r.status, 200, JSON.stringify(r.data));
  // A: linked in Telegram -> connected; the stale Bybit row -> not connected;
  // an undecryptable OKX key -> not connected, and why; an unknown venue -> nothing.
  const a = await card(ids.a);
  assert.deepEqual(a.bitget, [true, null]);
  assert.deepEqual(a.bybit, [false, null]);
  assert.equal(a.okx[0], false);
  assert.match(a.okx[1], /stored but cannot read them/);
  assert.ok(!('ftx' in a));
  // B: not in the report at all -> the bot holds nothing -> not connected.
  assert.deepEqual((await card(ids.b)).bitget, [false, null]);
  // C: unlinked from Telegram -> untouched.
  assert.deepEqual((await card(ids.c)).bitget, [true, null]);
  // D: the in-flight venue is left to its ack; the other one follows the report.
  const d = await card(ids.d);
  assert.deepEqual(d.bybit, [true, null], 'a venue with a submission in flight was touched');
  assert.deepEqual(d.bitget, [false, null]);
  // E: a refusal the bot sent stays, beside the venue the bot holds.
  const e = await card(ids.e);
  assert.deepEqual(e.bybit, [false, REJECTED]);
  assert.deepEqual(e.bitget, [true, null]);
  // G: the in-flight venue the report names is still left to its ack.
  assert.deepEqual((await card(ids.g)).bybit, [false, REJECTED]);
  // F: swept, except the venue in flight.
  const f = await card(ids.f);
  assert.deepEqual(f.bybit, [true, null], 'the sweep touched a venue with a submission in flight');
  assert.deepEqual(f.bitget, [false, null]);
  assert.deepEqual({ connected: r.data.connected, disconnected: r.data.disconnected, unreadable: r.data.unreadable },
    { connected: 2, disconnected: 4, unreadable: 1 });
});

test('the same report again writes nothing', async () => {
  const r = await post('/api/bot/sync/credentials/state', REPORT);
  assert.equal(r.status, 200);
  assert.deepEqual([r.data.connected, r.data.disconnected, r.data.unreadable], [0, 0, 0]);
});

test('a key removed in Telegram turns the card off; linked again, it turns back on', async () => {
  let r = await post('/api/bot/sync/credentials/state', {
    complete: true, users: REPORT.users.filter((u) => u.telegram_id !== '9101') });
  assert.equal(r.status, 200);
  assert.deepEqual((await card(ids.a)).bitget, [false, null]);
  r = await post('/api/bot/sync/credentials/state', REPORT);
  assert.deepEqual((await card(ids.a)).bitget, [true, null]);
});
