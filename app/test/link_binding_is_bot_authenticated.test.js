'use strict';
/**
 * POST /api/auth/validate-token WRITES, so it cannot stay anonymous.
 *
 * The route was reachable by anyone. It does not merely answer "is this token
 * valid" — it consumes the link token AND sets `telegram_id` on the account,
 * then returns the account's email. guard_lint's exemption argued the read
 * case ("the token IS the credential being checked"), which is true of a
 * lookup and false of a bind; that exemption is removed in the same commit as
 * this test.
 *
 * Two doors down, `POST /wallet/link` already carries the rule this route was
 * missing — "A wallet identifies at most one account" (auth.js:1110-1115),
 * a 409 when the address is on another row. `telegram_id` had no equivalent
 * AND no unique index, so two rows could hold the same chat_id while every
 * `WHERE telegram_id = ?` lookup takes the first match. Which account a user's
 * tier, trades and exchange credentials attach to would depend on row order.
 *
 * What is asserted here, and why each needs a running route rather than a grep:
 *
 *   1. an anonymous caller cannot bind, and cannot read the email
 *   2. a wrong secret cannot either, and the comparison is constant-time
 *   3. the bot still can — the fix must not break /link
 *   4. a chat_id on another account is refused, and the VICTIM's row is
 *      unchanged (the interesting half: a 409 that still wrote would be worse
 *      than no check)
 *   5. a refused bind does NOT burn the token — the legitimate owner retries
 *   6. re-linking the same chat_id to the same account still works
 *   7. an unset BOT_SYNC_SECRET answers 503, not 403: the server cannot tell
 *      who is calling, and "invalid secret" would send an operator to rotate a
 *      credential that was never the problem. server.js refuses to BOOT without
 *      the secret, so this is not reachable through a normal start — it is
 *      reachable when the router is mounted by something else, which is exactly
 *      what this file does, and it costs one branch. Stated rather than dressed
 *      up as production protection.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const SECRET = process.env.BOT_SYNC_SECRET;

let server, base, pool;

test.before(async () => {
  const authModule = require('../auth');
  ({ pool } = require('../db'));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function post(path, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(`${base}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        ...headers,
      },
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

let seq = 0;
/** Seed a user holding a live link token. Straight to the DB: /register and
 *  /login share a per-IP limiter and this file makes several accounts. */
async function userWithToken({ telegram = null } = {}) {
  const email = `link${seq++}@example.test`;
  const token = `tok-${seq}-${'a'.repeat(24)}`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    [email, 'x']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
  const id = rows[0].id;
  // Two statements, not one. app/db.js's in-memory shim pattern-matches SQL
  // and reads parameters POSITIONALLY, so a combined
  // `SET link_token = ?, link_token_expires = ?, telegram_id = ? WHERE id = ?`
  // matches its `UPDATE USERS SET LINK_TOKEN` branch, misreads params[2] as the
  // user id, finds nobody, and silently seeds nothing. The first draft of this
  // file did exactly that and four tests failed with a 404 that had nothing to
  // do with the route.
  await pool.execute(
    'UPDATE users SET link_token = ?, link_token_expires = ? WHERE id = ?',
    [token, new Date(Date.now() + 600000), id]);
  if (telegram !== null) {
    await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', [telegram, id]);
  }
  return { id, email, token };
}

async function rowOf(id) {
  const [r] = await pool.execute(
    'SELECT id, telegram_id, link_token FROM users WHERE id = ?', [id]);
  return r[0];
}

// ── 1. anonymous ───────────────────────────────────────────────────────────
test('an anonymous caller cannot bind a Telegram account, and gets no email', async () => {
  const u = await userWithToken();
  const res = await post('/api/auth/validate-token', { token: u.token, chat_id: '111' });

  assert.strictEqual(res.status, 403,
    'the route accepted an unauthenticated bind — anyone holding a link token '
    + 'could attach their own Telegram to that account');
  assert.ok(!('email' in res.data), `the refusal leaked the account email: ${JSON.stringify(res.data)}`);

  const row = await rowOf(u.id);
  assert.strictEqual(row.telegram_id, null, 'the refused request still wrote telegram_id');
  assert.strictEqual(row.link_token, u.token, 'the refused request still consumed the token');
});

// ── 2. wrong secret ────────────────────────────────────────────────────────
test('a wrong bot secret is refused, at any length', async () => {
  for (const bad of ['', 'x', 'b'.repeat(47), 'b'.repeat(49), 'c'.repeat(48)]) {
    const u = await userWithToken();
    const res = await post('/api/auth/validate-token',
      { token: u.token, chat_id: '222' }, { 'X-Bot-Secret': bad });
    assert.strictEqual(res.status, 403,
      `a secret of length ${bad.length} was accepted`);
    // A wrong-LENGTH secret must not crash to a 500: timingSafeEqual throws on
    // unequal buffers, which is why botAuth length-checks first.
    assert.notStrictEqual(res.status, 500, 'wrong-length secret crashed the route');
  }
});

// ── 3. the bot still works ─────────────────────────────────────────────────
test('the bot, holding the secret, still links', async () => {
  const u = await userWithToken();
  const res = await post('/api/auth/validate-token',
    { token: u.token, chat_id: '333' }, { 'X-Bot-Secret': SECRET });

  assert.strictEqual(res.status, 200, JSON.stringify(res.data));
  assert.strictEqual(res.data.user_id, u.id);
  assert.strictEqual(res.data.email, u.email);

  const row = await rowOf(u.id);
  assert.strictEqual(String(row.telegram_id), '333');
  assert.ok(!row.link_token, 'the token was not consumed');
});

// ── 4 & 5. a chat_id already on another account ────────────────────────────
test('a chat_id bound elsewhere is refused, the victim is untouched, and the token survives', async () => {
  const victim = await userWithToken({ telegram: '444' });
  const attacker = await userWithToken();

  const res = await post('/api/auth/validate-token',
    { token: attacker.token, chat_id: '444' }, { 'X-Bot-Secret': SECRET });

  assert.strictEqual(res.status, 409,
    'a chat_id already linked to another account was bound a second time — two '
    + 'rows now share one telegram_id and every lookup takes whichever comes first');

  const v = await rowOf(victim.id);
  assert.strictEqual(String(v.telegram_id), '444', "the victim's binding was moved");

  const a = await rowOf(attacker.id);
  assert.strictEqual(a.telegram_id, null);
  assert.strictEqual(a.link_token, attacker.token,
    'the refusal burned the token, so the legitimate owner cannot retry');
});

// ── 6. idempotent re-link ──────────────────────────────────────────────────
test('re-linking the same chat_id to the same account still works', async () => {
  const u = await userWithToken({ telegram: '555' });
  const res = await post('/api/auth/validate-token',
    { token: u.token, chat_id: '555' }, { 'X-Bot-Secret': SECRET });
  assert.strictEqual(res.status, 200,
    'a user re-running /link on the account it is already on was refused');
});

// ── 7. unconfigured is not rejected ────────────────────────────────────────
test('an unset BOT_SYNC_SECRET answers 503, not 403', async () => {
  const saved = process.env.BOT_SYNC_SECRET;
  delete process.env.BOT_SYNC_SECRET;
  try {
    const u = await userWithToken();
    const res = await post('/api/auth/validate-token',
      { token: u.token, chat_id: '666' }, { 'X-Bot-Secret': saved });
    assert.strictEqual(res.status, 503,
      'a server with no secret configured reported "invalid bot secret", which '
      + 'sends an operator to rotate a credential that was never the problem');
  } finally {
    process.env.BOT_SYNC_SECRET = saved;
  }
});
