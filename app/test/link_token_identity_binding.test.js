'use strict';
/**
 * RC-2026-001 — `/api/auth/validate-token` let the caller choose whose
 * Telegram identity got bound to their account.
 *
 * The route had ZERO tests before this file. That is the interesting part: it
 * is the endpoint that decides which human a bot account belongs to, it is
 * reachable without a session, and nothing in 532 test files exercised it.
 *
 * THE DEFECT, in the shape CLAUDE.md keeps naming. `link_token` authenticates
 * one thing — which WEB ACCOUNT is being linked — and `/link-token` mints one
 * for the caller's own row on demand. `chat_id` sits beside it in the same
 * body, names the identity to bind, and was authenticated by nothing at all.
 * A guard that covers one parameter reads, at a glance, as a guard that covers
 * the request. `scripts/guard_lint.py` had written the reasoning down and
 * exempted the route on it.
 *
 * WHAT THIS FILE HOLDS, in two layers, because they fail differently:
 *
 *   1. The ladder, driven directly. Three rungs x three verdicts is nine
 *      outcomes, and routing all nine through HTTP would mostly test express.
 *   2. The attack itself, through the real router against a real database,
 *      reading the VICTIM's row afterwards. A 403 is not the claim; the claim
 *      is that the victim's identity did not move.
 *
 * Layer 2 is what stops the #999 failure — a gate that is present, correct,
 * and not actually mounted on the route. Only a request can tell you that.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const SECRET = 's'.repeat(48);
const OTHER = 'x'.repeat(48);

const authModule = require('../auth');
const { linkBotSecretVerdict, linkBotAuth } = authModule;
const { pool } = require('../db');

// ── layer 1: the decision, with no HTTP in the way ────────────────────────

test('the verdict separates a bad secret from no secret to compare against', () => {
  assert.equal(linkBotSecretVerdict(SECRET, SECRET), 'ok');
  assert.equal(linkBotSecretVerdict(OTHER, SECRET), 'bad', 'same length, wrong value');
  assert.equal(linkBotSecretVerdict('short', SECRET), 'bad', 'wrong length is wrong, not a crash');
  assert.equal(linkBotSecretVerdict(undefined, SECRET), 'bad', 'no header at all');
  assert.equal(linkBotSecretVerdict(SECRET, ''), 'unconfigured', 'server has nothing to compare');
  assert.equal(linkBotSecretVerdict('abc', 'abc'), 'unconfigured', 'a too-short secret is not a secret');
});

test('a wrong-LENGTH secret refuses cleanly instead of throwing', () => {
  // crypto.timingSafeEqual throws on unequal-length buffers. Without the length
  // pre-check this is a 500 — and a 500 on an auth gate is a gate whose failure
  // mode nobody has looked at. sync.js:280 documents the same trap.
  assert.doesNotThrow(() => linkBotSecretVerdict('a', SECRET));
  assert.doesNotThrow(() => linkBotSecretVerdict('a'.repeat(4096), SECRET));
});

/** Drive the middleware with a fake req/res and report what it did. */
function drive({ gate, header, secret }) {
  const saved = [process.env.LINK_BOT_SECRET_GATE, process.env.BOT_SYNC_SECRET];
  try {
    if (gate === undefined) delete process.env.LINK_BOT_SECRET_GATE;
    else process.env.LINK_BOT_SECRET_GATE = gate;
    if (secret === undefined) delete process.env.BOT_SYNC_SECRET;
    else process.env.BOT_SYNC_SECRET = secret;

    const out = { passed: false, status: null, body: null };
    const req = { headers: header === undefined ? {} : { 'x-bot-secret': header } };
    const res = {
      status(c) { out.status = c; return this; },
      json(b) { out.body = b; return this; },
    };
    linkBotAuth(req, res, () => { out.passed = true; });
    return out;
  } finally {
    if (saved[0] === undefined) delete process.env.LINK_BOT_SECRET_GATE;
    else process.env.LINK_BOT_SECRET_GATE = saved[0];
    if (saved[1] === undefined) delete process.env.BOT_SYNC_SECRET;
    else process.env.BOT_SYNC_SECRET = saved[1];
  }
}

test('the DEFAULT rung refuses — an unset gate is a closed gate', () => {
  // The whole finding turns on this. A ladder defaulting to `warn` would leave
  // the CRITICAL open on every deployment that never set the variable, which
  // is every deployment that exists today.
  const r = drive({ gate: undefined, header: undefined, secret: SECRET });
  assert.equal(r.passed, false);
  assert.equal(r.status, 403);
  assert.equal(r.body.error, 'invalid_bot_secret');
});

test('block: an unconfigured server answers 503, NOT a pass and NOT 403', () => {
  // Three outcomes, not two. A server with no secret has not checked anything;
  // passing would be "absent is a measurement", and 403 would send an operator
  // hunting a secret mismatch that does not exist.
  const r = drive({ gate: 'block', header: SECRET, secret: undefined });
  assert.equal(r.passed, false);
  assert.equal(r.status, 503);
  assert.equal(r.body.error, 'link_not_configured');
});

test('block admits the real bot and nobody else', () => {
  assert.equal(drive({ gate: 'block', header: SECRET, secret: SECRET }).passed, true);
  assert.equal(drive({ gate: 'block', header: OTHER, secret: SECRET }).passed, false);
  assert.equal(drive({ gate: 'block', header: undefined, secret: SECRET }).passed, false);
});

test('warn passes everything through — that is the point, and the risk', () => {
  for (const header of [SECRET, OTHER, undefined]) {
    assert.equal(drive({ gate: 'warn', header, secret: SECRET }).passed, true);
  }
  // Including when the server has no secret configured at all.
  assert.equal(drive({ gate: 'warn', header: undefined, secret: undefined }).passed, true);
});

test('off does not even look', () => {
  assert.equal(drive({ gate: 'off', header: undefined, secret: undefined }).passed, true);
});

test('an unrecognised rung falls to block, not to open', () => {
  // A typo in an env var must not be a way to disable an auth gate.
  for (const gate of ['', 'blok', 'BLOCK ', 'true', 'yes', 'disabled']) {
    const r = drive({ gate, header: undefined, secret: SECRET });
    assert.equal(r.passed, false, `rung ${JSON.stringify(gate)} let the request through`);
  }
  // ...but the real rung names are case-insensitive, so an operator writing
  // OFF in an .env file gets what they asked for rather than a silent block.
  assert.equal(drive({ gate: 'OFF', header: undefined, secret: SECRET }).passed, true);
  assert.equal(drive({ gate: 'Warn', header: undefined, secret: SECRET }).passed, true);
});

// ── layer 2: the attack, end to end ───────────────────────────────────────

let server, base;

test.before(async () => {
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
async function newUser({ telegram = null } = {}) {
  const bcrypt = require('bcryptjs');
  const email = `link${Date.now()}_${seq++}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    [email, await bcrypt.hash('longenoughpassword1', 4)]);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
  const id = rows[0].id;
  if (telegram) {
    await pool.execute(
      'UPDATE users SET telegram_id = ?, telegram_linked = TRUE WHERE id = ?', [telegram, id]);
  }
  return id;
}

/** Mint a link token straight into the row — /link-token needs a session. */
async function mintToken(userId) {
  const token = `tok${Date.now()}_${seq++}`;
  await pool.execute(
    'UPDATE users SET link_token = ?, link_token_expires = ? WHERE id = ?',
    [token, new Date(Date.now() + 600000), userId]);
  return token;
}

async function telegramIdOf(userId) {
  const [rows] = await pool.execute('SELECT telegram_id FROM users WHERE id = ?', [userId]);
  return rows[0] ? rows[0].telegram_id : undefined;
}

function withEnv(env, fn) {
  const saved = {};
  for (const k of Object.keys(env)) saved[k] = process.env[k];
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) delete process.env[k]; else process.env[k] = v;
  }
  return Promise.resolve().then(fn).finally(() => {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  });
}

test('the audit reproduction: an unauthenticated caller cannot bind a victim', async () => {
  // Steps 1-4 of the finding, verbatim.
  const victimTg = `victim${seq++}`;
  const victim = await newUser({ telegram: victimTg });
  const attacker = await newUser();
  const token = await mintToken(attacker);

  await withEnv({ LINK_BOT_SECRET_GATE: undefined, BOT_SYNC_SECRET: SECRET }, async () => {
    const res = await post('/api/auth/validate-token', { token, chat_id: victimTg });
    assert.equal(res.status, 403);
  });

  // The status code is not the claim. THIS is the claim.
  assert.equal(await telegramIdOf(attacker), null,
    "the attacker's row must not have acquired an identity");
  assert.equal(await telegramIdOf(victim), victimTg,
    "the victim's identity must not have moved");
});

test('the bot, correctly authenticated, still cannot steal a linked identity', async () => {
  // The ordering-free half. Even a caller holding the real bot secret — a
  // compromised bot, or the operator themselves — must not be able to move an
  // identity that already belongs to somebody. This is the outcome the victim
  // cannot undo.
  const victimTg = `victim${seq++}`;
  const victim = await newUser({ telegram: victimTg });
  const attacker = await newUser();
  const token = await mintToken(attacker);

  await withEnv({ LINK_BOT_SECRET_GATE: 'block', BOT_SYNC_SECRET: SECRET }, async () => {
    const res = await post('/api/auth/validate-token',
      { token, chat_id: victimTg }, { 'X-Bot-Secret': SECRET });
    assert.equal(res.status, 409);
    assert.equal(res.data.error, 'telegram_already_linked');
  });

  assert.equal(await telegramIdOf(victim), victimTg);
  assert.equal(await telegramIdOf(attacker), null);
});

test('the claimed-identity refusal holds at EVERY rung, including off', async () => {
  // It depends on no secret and no bot-side change, so a deployment mid-way
  // through the two-sided rollout is still protected from the takeover.
  for (const gate of ['block', 'warn', 'off']) {
    const victimTg = `victim${seq++}`;
    const victim = await newUser({ telegram: victimTg });
    const attacker = await newUser();
    const token = await mintToken(attacker);

    // Send the REAL secret, so the ladder passes at every rung and the only
    // thing that can refuse is the claimed-identity check. Omitting it would
    // let `block` answer 403 from the gate and the test would pass without
    // ever reaching the code it is named after.
    await withEnv({ LINK_BOT_SECRET_GATE: gate, BOT_SYNC_SECRET: SECRET }, async () => {
      const res = await post('/api/auth/validate-token',
        { token, chat_id: victimTg }, { 'X-Bot-Secret': SECRET });
      assert.equal(res.status, 409, `rung ${gate} allowed the takeover`);
      assert.equal(res.data.error, 'telegram_already_linked',
        `rung ${gate} refused for some other reason than the claim`);
    });
    assert.equal(await telegramIdOf(victim), victimTg, `rung ${gate} moved the victim's id`);
    assert.equal(await telegramIdOf(attacker), null, `rung ${gate} bound the attacker`);
  }
});

test('a genuine link still works — the gate is not a wall', async () => {
  // The failure mode of every fix in this area: refusing everybody. If this
  // test is not here, "nothing can link at all" passes as a security fix.
  const user = await newUser();
  const token = await mintToken(user);
  const mine = `fresh${seq++}`;

  await withEnv({ LINK_BOT_SECRET_GATE: 'block', BOT_SYNC_SECRET: SECRET }, async () => {
    const res = await post('/api/auth/validate-token',
      { token, chat_id: mine }, { 'X-Bot-Secret': SECRET });
    assert.equal(res.status, 200);
    assert.equal(res.data.user_id, user);
  });
  assert.equal(await telegramIdOf(user), mine);
});

test('re-linking the same identity to the same account is not a takeover', async () => {
  // `id != ?` and not a bare match. A user who runs /link twice, or relinks
  // after a token refresh, must not be told their own identity is taken.
  const mine = `again${seq++}`;
  const user = await newUser({ telegram: mine });

  await withEnv({ LINK_BOT_SECRET_GATE: 'block', BOT_SYNC_SECRET: SECRET }, async () => {
    const token = await mintToken(user);
    const res = await post('/api/auth/validate-token',
      { token, chat_id: mine }, { 'X-Bot-Secret': SECRET });
    assert.equal(res.status, 200);
  });
  assert.equal(await telegramIdOf(user), mine);
});

test('an unconfigured server refuses the route rather than serving it open', async () => {
  const user = await newUser();
  const token = await mintToken(user);
  const mine = `noconf${seq++}`;

  await withEnv({ LINK_BOT_SECRET_GATE: 'block', BOT_SYNC_SECRET: undefined }, async () => {
    const res = await post('/api/auth/validate-token',
      { token, chat_id: mine }, { 'X-Bot-Secret': SECRET });
    assert.equal(res.status, 503);
  });
  assert.equal(await telegramIdOf(user), null, 'nothing was bound');
});

test('a wrong-length secret is a 403 over HTTP, not a 500', async () => {
  const user = await newUser();
  const token = await mintToken(user);

  await withEnv({ LINK_BOT_SECRET_GATE: 'block', BOT_SYNC_SECRET: SECRET }, async () => {
    const res = await post('/api/auth/validate-token',
      { token, chat_id: `wl${seq++}` }, { 'X-Bot-Secret': 'tiny' });
    assert.equal(res.status, 403);
  });
  assert.equal(await telegramIdOf(user), null);
});
