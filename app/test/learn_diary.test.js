'use strict';
/**
 * Study Room diary — the contract under test:
 * - Private: every route authed; users are fully isolated from each other.
 * - One entry per UTC day; a second write UPDATES and wears edited_at
 *   forever (the ✎ honesty marker).
 * - A diary describes days that happened: future days are refused.
 * - Attached trades are DERIVED at read time from the day's arena_trades
 *   window — the shim's window branch must honor BOTH bounds.
 * - The streak counts consecutive days and tolerates only "today not
 *   written yet".
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');

let server, base, tokA, tokB, uidA;

function req(method, p, body, tok) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
        ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

test.before(async () => {
  for (const email of ['diary-a@test.io', 'diary-b@test.io']) {
    await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
      [email, 'x'.repeat(60)]);
  }
  const [a] = await pool.execute('SELECT * FROM users WHERE email = ?', ['diary-a@test.io']);
  const [b] = await pool.execute('SELECT * FROM users WHERE email = ?', ['diary-b@test.io']);
  tokA = jwt.sign({ user_id: a[0].id, email: a[0].email }, process.env.JWT_SECRET);
  tokB = jwt.sign({ user_id: b[0].id, email: b[0].email }, process.env.JWT_SECRET);
  uidA = a[0].id;

  const app = express();
  app.use(express.json());
  app.use('/api/learn', require('../routes/learn'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('private: no token, no diary', async () => {
  const r = await req('GET', '/api/learn/diary');
  assert.equal(r.status, 401);
});

test('write → read → edit: the second save wears the ✎ mark forever', async () => {
  const w1 = await req('PUT', `/api/learn/diary/${today()}`, { body: 'Followed the plan today.' }, tokA);
  assert.equal(w1.status, 200);
  assert.deepEqual({ saved: w1.data.saved, edited: w1.data.edited }, { saved: true, edited: false });

  const r1 = await req('GET', `/api/learn/diary/${today()}`, null, tokA);
  assert.equal(r1.data.entry.body, 'Followed the plan today.');
  assert.equal(r1.data.entry.edited, false);

  const w2 = await req('PUT', `/api/learn/diary/${today()}`, { body: 'Followed the plan. Mostly.' }, tokA);
  assert.equal(w2.data.edited, true, 'a rewrite is an EDIT, said so');
  const r2 = await req('GET', `/api/learn/diary/${today()}`, null, tokA);
  assert.equal(r2.data.entry.edited, true);
});

test('a diary describes days that happened — future days are refused', async () => {
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
  const r = await req('PUT', `/api/learn/diary/${tomorrow}`, { body: 'time traveller' }, tokA);
  assert.equal(r.status, 400);
  assert.match(r.data.error, /has not/);
  const bad = await req('PUT', '/api/learn/diary/not-a-day', { body: 'x' }, tokA);
  assert.equal(bad.status, 400);
  const empty = await req('PUT', `/api/learn/diary/${today()}`, { body: '   ' }, tokA);
  assert.equal(empty.status, 400);
  const long = await req('PUT', `/api/learn/diary/${today()}`, { body: 'x'.repeat(4001) }, tokA);
  assert.equal(long.status, 400);
  assert.match(long.data.error, /4000/);
});

test('attached trades: only the day WINDOW — both bounds honored', async () => {
  const day = daysAgo(2);
  const inWindow = new Date(day + 'T12:00:00.000Z');
  const outside = new Date(day + 'T12:00:00.000Z');
  outside.setUTCDate(outside.getUTCDate() + 1); // next day noon — beyond the upper bound
  const ins = `INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price,
    margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`;
  await pool.execute(ins, [uidA, 'BTCUSDT', 'LONG', 100, 110, 500, 3, 150, 'tp', inWindow, inWindow]);
  await pool.execute(ins, [uidA, 'ETHUSDT', 'SHORT', 2500, 2400, 300, 2, 24, 'manual', outside, outside]);

  const r = await req('GET', `/api/learn/diary/${day}`, null, tokA);
  assert.equal(r.status, 200);
  assert.equal(r.data.trades.length, 1, 'the next-day close must NOT leak into this day');
  assert.equal(r.data.trades[0].symbol, 'BTCUSDT');
  assert.equal(r.data.trades[0].pnl, 150);
  assert.ok(!('margin' in r.data.trades[0]), 'attached trades carry the reflective fields only');
  assert.equal(r.data.entry, null, 'no entry for that day yet — null, honestly');
});

test('streak: consecutive days count; a gap breaks; missing-today does not', async () => {
  // A already has today. Add yesterday and 3 days ago (gap at day 2).
  await req('PUT', `/api/learn/diary/${daysAgo(1)}`, { body: 'yesterday' }, tokA);
  await req('PUT', `/api/learn/diary/${daysAgo(3)}`, { body: 'older' }, tokA);
  const r = await req('GET', '/api/learn/diary', null, tokA);
  assert.equal(r.status, 200);
  assert.equal(r.data.streak, 2, 'today + yesterday; the gap at −2 ends it');
  assert.ok(r.data.total >= 3);
  // B journals only yesterday: today-not-written-yet keeps the streak alive.
  await req('PUT', `/api/learn/diary/${daysAgo(1)}`, { body: 'b yesterday' }, tokB);
  const rb = await req('GET', '/api/learn/diary', null, tokB);
  assert.equal(rb.data.streak, 1);
});

test('isolation: user B never sees a byte of user A', async () => {
  const rb = await req('GET', '/api/learn/diary', null, tokB);
  for (const e of rb.data.entries) {
    assert.ok(!/plan/i.test(e.body), 'A’s words must never surface for B');
  }
  const day = daysAgo(2);
  const rbd = await req('GET', `/api/learn/diary/${day}`, null, tokB);
  assert.equal(rbd.data.trades.length, 0, 'A’s trades must never attach to B’s diary');
});

test('the page: private framing, ✎ marker, live-derived trades — through T()', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.match(page, /\/api\/learn/);
  assert.match(page, /'\/diary\/'/);
  assert.match(page, /ln\.edited_t/, 'edits are marked, with the why in a tooltip');
  assert.match(page, /ln\.no_trades/, 'a no-trade day gets an honest prompt, not silence');
  assert.match(page, /ln\.signed_out/, 'signed-out is stated, never a blank room');
  const i18nSrc = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.ok(i18nSrc.indexOf('/js/i18n-core.js') < i18nSrc.indexOf('function renderTrades'),
    'i18n loads before the page script');
  const i18n = require('../public/js/i18n.js');
  for (const k of ['nav.learn', 'nav.arena', 'ln.h', 'ln.lede', 'ln.today_h', 'ln.ph', 'ln.save',
    'ln.saved', 'ln.saved_edit', 'ln.save_fail', 'ln.load_fail', 'ln.empty', 'ln.signed_out',
    'ln.streak', 'ln.trades_h', 'ln.hist_h', 'ln.edited', 'ln.edited_t', 'ln.disc', 'ln.no_trades']) {
    const en = String(i18n.STRINGS[k].en || '');
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
});
