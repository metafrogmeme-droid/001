/**
 * A key waiting on the bot says how long it has waited.
 *
 * Reported 8 October: the Bybit card read "applying…" with nothing to tell a
 * minute's ordinary wait from a bot that never calls. The bot picks requests
 * up after an engine tick, which is a scan plus SCAN_INTERVAL, so a wait is
 * normal; one that only grows is not.
 *
 * Driven: the real /api/credentials routes on the in-memory database, the
 * age reading on both of its sources, and the card's chip and note cut out of
 * dashboard.js by their markers.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.WEB_CREDS_KEY = Buffer.alloc(32, 7).toString('base64');

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const { pendingAgeSeconds } = require('../routes/credentials');
const { codeOnly } = require('./helpers/code_only');

let server, base, token, uid;

function request(method, p, { token: tk, secret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(tk ? { Authorization: `Bearer ${tk}` } : {}),
        ...(secret ? { 'x-bot-secret': secret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
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

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['wait@test.io', 'x', 'W']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['wait@test.io']);
  uid = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['9002', uid]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
  u[0].telegram_linked = true; // MemoryDB: set the flag directly
  token = jwt.sign({ user_id: uid, email: 'wait@test.io' }, process.env.JWT_SECRET);
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/credentials', require('../routes/credentials'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => server && server.close());

test('the status says how long a request has waited, and nothing once it is answered', async () => {
  const sub = await request('POST', '/api/credentials', {
    token, body: { venue: 'bybit', api_key: 'k'.repeat(18), api_secret: 's'.repeat(36) },
  });
  assert.equal(sub.status, 200);
  let s = await request('GET', '/api/credentials/status', { token });
  assert.equal(s.data.pending_venue, 'bybit');
  assert.ok(Number.isInteger(s.data.pending_age_s) && s.data.pending_age_s >= 0
    && s.data.pending_age_s < 5, `a fresh request read ${s.data.pending_age_s}s`);

  // Twenty minutes later, by the store's own clock.
  const [pend] = await pool.execute('SELECT * FROM pending_credentials WHERE user_id = ?', [uid]);
  pend[0].created_at = new Date(Date.now() - 20 * 60 * 1000);
  s = await request('GET', '/api/credentials/status', { token });
  assert.ok(s.data.pending_age_s >= 1200 && s.data.pending_age_s < 1210, String(s.data.pending_age_s));

  const ack = await request('POST', '/api/bot/sync/credentials/ack', {
    secret: process.env.BOT_SYNC_SECRET,
    body: { acks: [{ user_id: uid, action: 'connect', ok: true }] },
  });
  assert.equal(ack.status, 200);
  s = await request('GET', '/api/credentials/status', { token });
  assert.equal(s.data.pending, null);
  assert.equal(s.data.pending_age_s, null);
});

test('the age is read off one clock, and unreadable is null, never zero', () => {
  // The database's own figure wins: created_at and Date.now() are two hosts.
  assert.equal(pendingAgeSeconds({ pending_age_s: 30, created_at: new Date(Date.now() - 3600e3) }), 30);
  assert.equal(pendingAgeSeconds({ pending_age_s: '75' }), 75);
  assert.equal(pendingAgeSeconds({ pending_age_s: 75.9 }), 75);
  assert.equal(pendingAgeSeconds({ pending_age_s: -3 }), null, 'a negative age is skew, not a wait');
  assert.equal(pendingAgeSeconds({ pending_age_s: 'n/a' }), null);
  // The in-memory store stamped created_at from this process's clock.
  const two = pendingAgeSeconds({ created_at: new Date(Date.now() - 120e3) });
  assert.ok(two >= 120 && two < 123, String(two));
  assert.equal(pendingAgeSeconds({ created_at: '2026-10-08 09:00:00' }), null);
  assert.equal(pendingAgeSeconds({}), null);
  assert.equal(pendingAgeSeconds(null), null);
});

test('on MySQL the database computes the age against its own clock', () => {
  // The in-memory store evaluates no SQL, so this shape is checked where it
  // is written: the status read asks the database for the difference.
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'credentials.js'), 'utf8'));
  assert.match(src, /TIMESTAMPDIFF\(SECOND, created_at, CURRENT_TIMESTAMP\) AS pending_age_s\s+FROM pending_credentials/);
});

// ── the card ─────────────────────────────────────────────────────────────────

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function loadChip() {
  const a = DASH.indexOf('  // ── venue chip: start');
  const b = DASH.indexOf('  // ── venue chip: end');
  assert.ok(a > 0 && b > a, 'the venue chip moved; it sits between its markers in dashboard.js');
  const T = (k, en) => en;
  const TF = (k, en, map) => String(en).replace(/\{(\w+)\}/g,
    (w, key) => (map && map[key] != null ? String(map[key]) : w));
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ctx = { T, TF, esc, String, Number, Math };
  vm.runInNewContext(DASH.slice(a, b)
    + '\nglobalThis.venueChip = venueChip; globalThis.venueStuckNote = venueStuckNote;', ctx);
  return ctx;
}

const { venueChip, venueStuckNote } = loadChip();
const pendingChip = (waitedS) =>
  venueChip({ connected: false, pending: 'connect', rejected: null, label: 'Bybit', waitedS });

test('the chip carries the wait from a minute on, and claims none it cannot read', () => {
  assert.match(pendingChip(7 * 60 + 20), />applying Bybit… · waiting 7 min</);
  assert.match(pendingChip(60), /· waiting 1 min</);
  assert.match(pendingChip(119), /· waiting 1 min</, 'a minute is not rounded up');
  for (const v of [59, 0, null, undefined, '', NaN, 'soon', -120]) {
    assert.match(pendingChip(v), />applying Bybit…</, String(v));
    assert.doesNotMatch(pendingChip(v), /waiting/, String(v));
  }
  // A connected or rejected card has no wait to show.
  assert.doesNotMatch(venueChip({ connected: true, pending: null, rejected: null, label: 'Bybit', waitedS: 900 }),
    /waiting/);
});

test('past fifteen minutes the card says what might be wrong, as a possibility', () => {
  assert.equal(venueStuckNote(15 * 60 - 1), '');
  assert.equal(venueStuckNote(null), '');
  assert.equal(venueStuckNote(''), '');
  const note = venueStuckNote(15 * 60);
  assert.match(note, /Still waiting for the bot/);
  assert.match(note, /may not be reaching the website/);
});

test('the keys card hands the chip and the note the request\'s age', () => {
  const src = codeOnly(DASH);
  const start = src.indexOf('async function renderAccount()');
  assert.ok(start > 0, 'renderAccount moved');
  const body = src.slice(start);
  assert.ok(body.includes('const waitedS = pending ? c.pending_age_s : null;'));
  assert.ok(body.includes('venueChip({ connected, pending, rejected, label: v.label, waitedS })'));
  assert.ok(body.includes(': venueStuckNote(waitedS);'));
});
