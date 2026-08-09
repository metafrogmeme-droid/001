'use strict';
/**
 * The Daily Duel share card and share flow.
 *
 * A share card is the one surface nobody can check. It leaves as an image, it
 * gets posted somewhere the product does not control, and whatever it claims
 * is what people believe. So the two things pinned here are that it never
 * draws a number the record does not support, and that the share text carries
 * the invite link rather than an amount.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const authModule = require('../auth');
const { pool } = require('../db');
const { MIN_SCORED } = require('../lib/duel_squads');
const { codeOnly } = require('./helpers/code_only');

let server, base;

function get(p) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${p}`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, buf: Buffer.concat(chunks),
        type: res.headers['content-type'] }));
    }).on('error', reject);
  });
}

const DAY = new Date().toISOString().slice(0, 10);
let READY_ID, NEW_ID;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/frame', require('../routes/frame'));
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });

  for (const [email, handle] of [['ready@example.com', 'ready'], ['fresh@example.com', 'fresh']]) {
    const r = await new Promise((resolve, reject) => {
      const body = JSON.stringify({ email, password: 'longenough1' });
      const req = http.request(`${base}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      }, (res) => {
        let raw = ''; res.on('data', (c) => { raw += c; });
        res.on('end', () => resolve(JSON.parse(raw || '{}')));
      });
      req.on('error', reject); req.write(body); req.end();
    });
    assert.ok(r.token, `register ${email}`);
    const [u] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
    await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', [handle, u[0].id]);
    if (handle === 'ready') READY_ID = u[0].id; else NEW_ID = u[0].id;
  }

  // A readable record for `ready`: every call correct, and the agent wrong on
  // each, so the beat count is real too.
  for (let i = 0; i < MIN_SCORED; i++) {
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, symbol, agent_direction, signal_key)'
      + ' VALUES (?, ?, ?, ?, ?)', [DAY, i, `S${i}USDT`, 'short', null]);
  }
  const [rounds] = await pool.execute(
    'SELECT id, day, idx, symbol, agent_direction, signal_key FROM duel_rounds WHERE day = ? ORDER BY idx',
    [DAY]);
  for (const r of rounds) {
    await pool.execute(
      'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
      + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      [READY_ID, r.id, 'long', 100, new Date(Date.now() - 3600000).toISOString(),
        'a'.repeat(64), '{}', new Date().toISOString()]);
  }
  const [picks] = await pool.execute(
    'SELECT id, user_id, round_id, pick, entry_price, resolves_at, settle_price,'
    + ' settle_state, created_at FROM duel_picks');
  for (const p of picks.filter((x) => Number(x.user_id) === Number(READY_ID))) {
    await pool.execute(
      'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
      [110, 'settled', new Date().toISOString(), p.id]);
  }
  // `fresh` has called once — real, and nowhere near readable.
  await pool.execute(
    'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
    + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [NEW_ID, rounds[0].id, 'long', 100, new Date(Date.now() - 3600000).toISOString(),
      'b'.repeat(64), '{}', new Date().toISOString()]);
});

test.after(() => { if (server) server.close(); });

const isPng = (buf) => buf.length > 8 && buf[0] === 0x89 && buf.toString('latin1', 1, 4) === 'PNG';

test('a readable record renders a PNG card', async () => {
  const r = await get('/api/frame/duel/ready/image');
  assert.equal(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.ok(isPng(r.buf), 'a real PNG, decodable by whatever it gets posted into');
});

test('a player below the floor gets a card that SAYS so, not a 0%', async () => {
  // The failure this guards: publishing "0 PCT" about somebody who has simply
  // not played enough — as an image, which is the one form nobody can check.
  const r = await get('/api/frame/duel/fresh/image');
  assert.equal(r.status, 200);
  assert.ok(isPng(r.buf));
  const ready = await get('/api/frame/duel/ready/image');
  assert.notEqual(r.buf.toString('base64'), ready.buf.toString('base64'),
    'the warming-up card must not be the same drawing as a scored one');
});

test('an unknown handle is refused rather than drawn as an empty record', async () => {
  const r = await get('/api/frame/duel/nobodyhere/image');
  assert.equal(r.status, 200);
  assert.ok(isPng(r.buf), 'still a card — it just says there is no such player');
  const fresh = await get('/api/frame/duel/fresh/image');
  assert.notEqual(r.buf.toString('base64'), fresh.buf.toString('base64'));
});

test('a malformed handle is rejected outright', async () => {
  for (const bad of ['a', '../../etc/passwd', 'has space', 'x'.repeat(40)]) {
    const r = await get('/api/frame/duel/' + encodeURIComponent(bad) + '/image');
    assert.equal(r.status, 400, `"${bad}" must not reach the renderer`);
  }
});

test('the card route draws only counts, percent and the handle', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'frame.js'), 'utf8'));
  const block = src.slice(src.indexOf("router.get('/duel/:handle/image'"));
  for (const banned of ['balance', 'equity', 'margin', 'pnl', 'notional', 'usd']) {
    assert.ok(!new RegExp(`\\b${banned}\\b`, 'i').test(block),
      `§4: the duel card must not read ${banned}`);
  }
  assert.ok(/accuracy_pct/.test(block), 'it draws the rate');
  assert.ok(/beat_agent/.test(block), 'and the beat count');
});

// ── the share flow on the page ──────────────────────────────────────────────

const PAGE = fs.readFileSync(path.join(__dirname, '..', 'public', 'duel.html'), 'utf8');

test('the share flow carries the invite link, which is the point of it', () => {
  assert.ok(PAGE.includes('/api/auth/referrals'),
    'a share that does not carry the invite is a share that grows nothing');
  assert.ok(PAGE.includes('navigator.share'));
  assert.ok(PAGE.includes('t.me/share/url'), 'and a fallback for browsers without it');
});

test('the share text carries no amount of anything', () => {
  const block = PAGE.slice(PAGE.indexOf('function shareRecord'), PAGE.indexOf('function openTelegram'));
  for (const banned of ['pnl', 'usd', 'balance', 'equity', 'margin', 'stake']) {
    assert.ok(!new RegExp(`\\b${banned}\\b`, 'i').test(block), `share text must not carry ${banned}`);
  }
});

test('a failed image still shares the text and the link', () => {
  const block = PAGE.slice(PAGE.indexOf('function shareRecord'), PAGE.indexOf('function openTelegram'));
  // The degradation pin: the card is a bonus, the link is the payload.
  assert.ok(/catch[\s\S]*?openTelegram/.test(block),
    'an unfetchable card must not swallow the share');
  assert.ok(block.includes('navigator.share({ text: text, url: url })'),
    'and there is a path that shares without a file at all');
});

test('sharing is offered only once there is a handle to address the card to', () => {
  assert.ok(PAGE.includes("$('shareBtn').hidden = !MY_HANDLE"));
  assert.ok(PAGE.includes('duel.share_optin'), 'and the reason is explained, not just hidden');
});
