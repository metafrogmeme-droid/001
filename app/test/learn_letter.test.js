'use strict';
/**
 * The weekly Study Letter — the contract under test:
 * - Composed ONLY from recorded facts (diary days, word counts, closes, the
 *   panel's own discipline read); nothing coaching-shaped is invented.
 * - A blank week says so; discipline appears only past MIN_CLOSES; the
 *   pairing line exists only when both halves actually happened.
 * - Parts/tid architecture: every sentence renders per-language with the
 *   composer's English as whole-line fallback.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const { composeStudyLetter } = require('../lib/learn_letter');
const { MIN_CLOSES } = require('../lib/arena_discipline');
const { lastCompletedWeek } = require('../lib/letter');

const WEEK = lastCompletedWeek();
const tids = (L) => L.parts.map((p) => p.tid);

test('a full week: days, words, closes, discipline and the pairing line', () => {
  const entries = [
    { day: '2026-07-20', body: 'Followed the plan on both trades.' },
    { day: '2026-07-21', body: 'Sat out. Choppy.' },
    { day: '2026-07-22', body: 'Late entry, early exit — wrote it down.' },
  ];
  const trades = Array.from({ length: MIN_CLOSES }, (_, i) =>
    ({ symbol: 'BTCUSDT', pnl: i % 2 ? 10 : -5, reason: i % 2 ? 'tp' : 'sl' }));
  const L = composeStudyLetter(WEEK, { entries, trades });
  assert.deepEqual(tids(L),
    ['lw.h', 'lw.j_days', 'lw.j_words', 'lw.t_closes', 'lw.d_rate', 'lw.pair', 'lw.f']);
  assert.equal(L.parts[1].params.n, 3);
  assert.equal(L.parts[2].params.w, 16, 'word counts are counted, not estimated');
  assert.equal(L.parts[3].params.n, MIN_CLOSES);
  assert.equal(L.parts[4].params.p, 100, 'every close was tp/sl — the rate is a fact');
  assert.match(L.text, /You wrote on 3 of 7 days\./, 'assembled English fills its slots');
  assert.equal(L.private, true);
});

test('a blank week says so — no invented encouragement, no pairing line', () => {
  const L = composeStudyLetter(WEEK, { entries: [], trades: [] });
  assert.deepEqual(tids(L), ['lw.h', 'lw.j_none', 'lw.t_none', 'lw.f']);
  assert.match(L.text, /the page was blank, and that is recorded honestly/);
});

test('below MIN_CLOSES the discipline line is the honest thin one', () => {
  const L = composeStudyLetter(WEEK, { entries: [{ day: '2026-07-20', body: 'x' }],
    trades: [{ symbol: 'ETHUSDT', pnl: 4, reason: 'manual' }] });
  assert.ok(tids(L).includes('lw.d_thin'));
  assert.ok(!tids(L).includes('lw.d_rate'));
  assert.equal(L.parts.find((p) => p.tid === 'lw.d_thin').params.m, MIN_CLOSES);
  assert.ok(tids(L).includes('lw.pair'), 'one entry + one trade IS both halves');
});

// ── the route ────────────────────────────────────────────────────────────────
let server, base, tok, uid;

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['letter@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['letter@test.io']);
  uid = u[0].id;
  tok = jwt.sign({ user_id: uid, email: u[0].email }, process.env.JWT_SECRET);
  const app = express();
  app.use(express.json());
  app.use('/api/learn', require('../routes/learn'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function req(method, p, tokIn) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, {
      method, headers: tokIn ? { Authorization: `Bearer ${tokIn}` } : {},
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test('route: authed, filtered to the last completed week only', async () => {
  assert.equal((await req('GET', '/api/learn/letter')).status, 401);

  const inWeekDay = WEEK.start.toISOString().slice(0, 10);
  const afterWeekDay = WEEK.end.toISOString().slice(0, 10); // first day AFTER the week
  await pool.execute(
    'INSERT INTO learn_diary (user_id, day, body, created_at) VALUES (?, ?, ?, ?)',
    [uid, inWeekDay, 'inside the week', new Date()]);
  await pool.execute(
    'INSERT INTO learn_diary (user_id, day, body, created_at) VALUES (?, ?, ?, ?)',
    [uid, afterWeekDay, 'outside the week', new Date()]);
  const inWeekClose = new Date(WEEK.start.getTime() + 3600_000);
  await pool.execute(
    `INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price,
      margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [uid, 'BTCUSDT', 'LONG', 100, 110, 500, 3, 150, 'tp', inWeekClose, inWeekClose]);

  const r = await req('GET', '/api/learn/letter', tok);
  assert.equal(r.status, 200);
  const L = r.data.letter;
  assert.equal(L.week, WEEK.key);
  assert.equal(L.parts.find((p) => p.tid === 'lw.j_days').params.n, 1,
    'the entry after the week must not count');
  assert.equal(L.parts.find((p) => p.tid === 'lw.t_closes').params.n, 1);
});

test('the page renders the letter by tid with English fallback; keys cover 14 languages', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.match(page, /api\('\/letter'\)/);
  assert.match(page, /T\(p\.tid, p\.en\)/, 'whole-line translation, English fallback');
  assert.match(page, /id="lwPanel"/);
  const i18n = require('../public/js/i18n.js');
  for (const k of ['lw.hh', 'lw.h', 'lw.j_none', 'lw.j_days', 'lw.j_words', 'lw.t_none',
    'lw.t_closes', 'lw.d_rate', 'lw.d_thin', 'lw.pair', 'lw.f']) {
    const en = String(i18n.STRINGS[k].en || '');
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
});
