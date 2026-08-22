'use strict';
/**
 * The bot is purged first, and a failure there must change NOTHING here.
 *
 * A user's exchange API keys live in the bot's encrypted vault, not in the web
 * database. So the tempting ordering — delete the rows, report success, then
 * ask the bot to clean up — has an unrecoverable failure mode: the bot keeps
 * holding the credentials that move real money, underneath a message telling
 * the person their account is gone. The inverse failure is visible and
 * retryable: an account whose bot state cleared and whose rows did not is
 * still an account, still logged in, and the user can press delete again.
 *
 * That ordering is the load-bearing property of the route and it has no seam.
 * A source scan can confirm the `502` is written; it cannot confirm the rows
 * survive it — which is the entire claim. This file runs the route against a
 * stub gateway and reads the database afterwards.
 *
 * IT FOUND A DEFECT ON ITS FIRST RUN. `postGateway` resolves an ENVELOPE,
 * `{status, data}`, and does not reject on a non-2xx. The route read
 * `purge.purged` straight off the envelope — always `undefined` — so it
 * refused every deletion that reached a working gateway with the message
 * saying the bot could not clear everything. Present, reached, and wrong: the
 * three-way distinction #999 is named for in CLAUDE.md.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(48);      // isConfigured() needs >= 32
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

// ── a stub bot gateway, on a real socket ──────────────────────────────────
//
// Started BEFORE app/lib/gateway.js is required: it reads BOT_GATEWAY_URL once
// at module scope, so the port has to exist in the environment first.
let botCalls = [];
let botReply = { status: 200, body: { purged: true, stores: { exchange_credentials: 'deleted' } } };
//: Runs while the web request is blocked on this call — the only moment from
//: which the database can be observed MID-DELETION.
let onPurge = null;
const bot = http.createServer((req, res) => {
  let d = '';
  req.on('data', (c) => { d += c; });
  req.on('end', async () => {
    botCalls.push({ url: req.url, body: d ? JSON.parse(d) : null });
    if (onPurge) await onPurge();
    if (botReply === 'hang-up') { req.socket.destroy(); return; }
    res.writeHead(botReply.status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(botReply.body));
  });
});

let server, base, authModule, pool;

test.before(async () => {
  await new Promise((r) => bot.listen(0, '127.0.0.1', r));
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${bot.address().port}`;

  authModule = require('../auth');
  ({ pool } = require('../db'));

  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.get('/api/protected', authModule.authMiddleware, (req, res) => res.json({ ok: true }));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); bot.close(); });

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

/**
 * Seed a user straight into the database, linked to Telegram.
 *
 * Not through POST /register: registration and login share a per-IP rate
 * limiter, and a file that makes half a dozen accounts from one address starts
 * failing on 429s that have nothing to do with deletion.
 */
let seq = 0;
async function newUser({ telegram = true } = {}) {
  const bcrypt = require('bcryptjs');
  const email = `del${Date.now()}_${seq++}@example.com`;
  const password = 'longenoughpassword1';
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    [email, await bcrypt.hash(password, 4)]);
  const [rows] = await pool.execute(
    'SELECT id, token_epoch FROM users WHERE email = ?', [email]);
  const id = rows[0].id;
  if (telegram) {
    await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?',
      [`tg${id}`, id]);
  }
  await pool.execute(
    'INSERT INTO user_watchlist (user_id, symbol, created_at) VALUES (?, ?, ?)',
    [id, 'BTCUSDT', new Date()]);
  botCalls = [];
  return {
    id, email, password,
    token: authModule.signToken({ id, email, token_epoch: rows[0].token_epoch }),
  };
}

/**
 * A SNAPSHOT of what the database holds for this account, right now.
 *
 * The spread is load-bearing, not tidiness. The in-memory shim hands back the
 * live row object for a `SELECT … FROM users`, so holding onto it and reading
 * a field later reads the row's CURRENT value — and the one test that captures
 * state mid-request then "observed" a tombstone that had not happened yet,
 * failing on correct code. A snapshot that mutates is not a snapshot.
 */
async function stillThere(id) {
  const [u] = await pool.execute('SELECT email, telegram_id FROM users WHERE id = ?', [id]);
  const [w] = await pool.execute('SELECT symbol FROM user_watchlist WHERE user_id = ?', [id]);
  return { user: u[0] ? { ...u[0] } : null, watchlist: w.length };
}

const CONFIRM = { password: 'longenoughpassword1', confirm: 'DELETE' };

// ── the ordering property ─────────────────────────────────────────────────

test('an unreachable bot aborts the deletion and changes nothing', async () => {
  const u = await newUser();
  botReply = 'hang-up';                    // socket destroyed mid-request

  const res = await req('DELETE', '/api/auth/account', { token: u.token, body: CONFIRM });
  assert.strictEqual(res.status, 502, JSON.stringify(res.data));

  const after = await stillThere(u.id);
  assert.strictEqual(after.user.email, u.email,
    'the account was erased even though the bot never confirmed — it is still '
    + 'holding the exchange credentials');
  assert.strictEqual(after.watchlist, 1, 'rows were deleted before the abort');
  assert.strictEqual((await req('GET', '/api/protected', { token: u.token })).status, 200,
    'the session was cleared for an account that still exists');
});

test('a partial bot purge aborts too, and says which store did not clear', async () => {
  const u = await newUser();
  botReply = {
    status: 409,
    body: { purged: false, stores: { exchange_credentials: 'error', agent_profile: 'deleted' } },
  };

  const res = await req('DELETE', '/api/auth/account', { token: u.token, body: CONFIRM });
  assert.strictEqual(res.status, 502);
  assert.strictEqual(res.data.bot_stores.exchange_credentials, 'error',
    'the caller cannot tell WHICH store failed, so the operator has nothing to '
    + 'act on and the user has no idea what survived');
  assert.strictEqual((await stillThere(u.id)).watchlist, 1);
});

test('the bot is asked before a single row is touched', async () => {
  // The ordering, observed rather than inferred: the gateway sees the request
  // while the account is still whole.
  const u = await newUser();
  let seen = null;
  botReply = { status: 200, body: { purged: true, stores: { user_record: 'deleted' } } };
  onPurge = async () => { seen = await stillThere(u.id); };
  try {
    const res = await req('DELETE', '/api/auth/account', { token: u.token, body: CONFIRM });
    assert.strictEqual(res.status, 200, JSON.stringify(res.data));
  } finally {
    onPurge = null;
  }
  assert.ok(seen, 'the bot was never called at all');
  assert.strictEqual(seen.watchlist, 1,
    'rows were already gone when the bot was asked — if it had failed, they '
    + 'would have been deleted for nothing');
  assert.strictEqual(seen.user.email, u.email);
});

// ── the successful path ───────────────────────────────────────────────────

test('a confirmed purge erases the account and ends the session', async () => {
  const u = await newUser();
  botReply = { status: 200, body: { purged: true, stores: { exchange_credentials: 'none' } } };

  const res = await req('DELETE', '/api/auth/account', { token: u.token, body: CONFIRM });
  assert.strictEqual(res.status, 200, JSON.stringify(res.data));
  assert.strictEqual(res.data.deleted, true);
  assert.strictEqual(res.data.bot_stores.exchange_credentials, 'none',
    'per-store outcomes are reported, so "there was nothing to delete" is not '
    + 'dressed up as "deleted"');

  const after = await stillThere(u.id);
  assert.match(after.user.email, /@account\.invalid$/);
  assert.strictEqual(after.user.telegram_id, null);
  assert.strictEqual(after.watchlist, 0);
  assert.strictEqual((await req('GET', '/api/protected', { token: u.token })).status, 401,
    'the token still works after the account was erased');
});

test('an account with no telegram link needs no bot round trip', async () => {
  const u = await newUser({ telegram: false });
  const res = await req('DELETE', '/api/auth/account', { token: u.token, body: CONFIRM });
  assert.strictEqual(res.status, 200, JSON.stringify(res.data));
  assert.deepStrictEqual(botCalls, [],
    'the gateway was called for an account the bot has never heard of');
  assert.strictEqual(res.data.bot_stores, null,
    'bot_stores must be null, not {} — "we did not ask" is not "nothing was '
    + 'there"');
});

// ── the gates in front of it ──────────────────────────────────────────────

test('a valid session is not enough on its own', async () => {
  const u = await newUser();
  botReply = { status: 200, body: { purged: true, stores: {} } };

  const noPhrase = await req('DELETE', '/api/auth/account',
    { token: u.token, body: { password: u.password } });
  assert.strictEqual(noPhrase.status, 400);
  assert.strictEqual(noPhrase.data.confirm_required, true);

  const badPw = await req('DELETE', '/api/auth/account',
    { token: u.token, body: { password: 'wrong-but-long-enough', confirm: 'DELETE' } });
  assert.strictEqual(badPw.status, 401);

  assert.strictEqual((await stillThere(u.id)).watchlist, 1);
  assert.deepStrictEqual(botCalls, [],
    'the bot was purged for a request that was then refused — the refusal came '
    + 'after an irreversible step');
});

test('an anonymous request cannot delete anything', async () => {
  const u = await newUser();
  const res = await req('DELETE', '/api/auth/account', { body: CONFIRM });
  assert.strictEqual(res.status, 401);
  assert.strictEqual((await stillThere(u.id)).watchlist, 1);
});

test('the confirmation phrase is matched loosely enough to be usable', async () => {
  // Case and surrounding whitespace are not the point; typing something else
  // entirely is. A phrase gate that rejects "delete " teaches people to
  // paste, which defeats it.
  const u = await newUser({ telegram: false });
  const res = await req('DELETE', '/api/auth/account',
    { token: u.token, body: { password: u.password, confirm: '  delete ' } });
  assert.strictEqual(res.status, 200, JSON.stringify(res.data));
});
