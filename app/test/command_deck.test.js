'use strict';
/**
 * The Command Deck — the contract under test:
 * - Achievements are granted by ARITHMETIC: each predicate is exercised in
 *   both directions, and there is no storage anywhere to grant from.
 * - The deck payload is COUNTS AND FACTS only — no balance, equity, usd or
 *   pnl figure rides it (scanned, not assumed).
 * - The diary-streak helper is single-source: the Study Room route imports
 *   the same function the deck uses.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const { computeAchievements, ACHIEVEMENTS, diaryStreak } = require('../lib/achievements');
const { MIN_CLOSES } = require('../lib/arena_discipline');

test('every achievement is a two-way predicate — unlockable and not-yet', () => {
  const none = computeAchievements({});
  assert.equal(none.length, ACHIEVEMENTS.length);
  assert.ok(none.every((a) => !a.unlocked), 'an empty record unlocks nothing');

  const everything = computeAchievements({
    closes: MIN_CLOSES, sealed: 1, disciplineLevel: 'planned',
    diaryTotal: 10, diaryStreak: 5, lessonsDone: 3, lessonsTotal: 3,
    runeMinted: true, questsDone: 3, pairedWeek: true,
    duelCalls: 1, duelBeats: 1, duelScored: 10, duelCorrect: 6,
  });
  assert.ok(everything.every((a) => a.unlocked), 'the full record unlocks all');

  // Edges that must NOT unlock.
  const edges = computeAchievements({
    closes: MIN_CLOSES - 1, sealed: 0, disciplineLevel: 'mixed',
    diaryTotal: 1, diaryStreak: 4, lessonsDone: 2, lessonsTotal: 3,
    runeMinted: false, questsDone: 2, pairedWeek: false,
    duelCalls: 1, duelBeats: 0, duelScored: 10, duelCorrect: 5,
  });
  const by = Object.fromEntries(edges.map((a) => [a.id, a.unlocked]));
  assert.equal(by.readable, false, 'one short of MIN_CLOSES stays locked');
  assert.equal(by.planned, false, 'mixed is not planned');
  assert.equal(by.week_of_words, false, 'a 4-day streak is not a 5-day streak');
  assert.equal(by.scholar, false, '2 of 3 lessons is not every lesson');
  assert.equal(by.quest_week, false);
  assert.equal(by.first_close, true);
  assert.equal(by.first_words, true);
  assert.equal(by.first_call, true, 'one call made is one call made');
  assert.equal(by.beat_the_claw, false, 'no beats means no glyph');
  assert.equal(by.read_the_tape, false, 'exactly half right is not more than half');
});

test('the duel accuracy glyph counts SETTLED calls, never calls made', () => {
  // A week of unreadable settles is not a week of being right. Counting calls
  // made would hand out an accuracy glyph to somebody with no measured
  // accuracy at all — the exact shape CLAUDE.md warns about, wearing a trophy.
  const unsettled = computeAchievements({
    duelCalls: 40, duelBeats: 0, duelScored: 0, duelCorrect: 0,
  });
  const by = Object.fromEntries(unsettled.map((a) => [a.id, a.unlocked]));
  assert.equal(by.read_the_tape, false, 'forty unresolved calls prove nothing');
  assert.equal(by.first_call, true, 'but they did show up, and that is a fact');

  const settled = computeAchievements({
    duelCalls: 10, duelBeats: 0, duelScored: 10, duelCorrect: 6,
  });
  assert.equal(settled.find((a) => a.id === 'read_the_tape').unlocked, true);

  // Nine settled is not ten, however good the rate.
  const nearly = computeAchievements({
    duelCalls: 9, duelBeats: 0, duelScored: 9, duelCorrect: 9,
  });
  assert.equal(nearly.find((a) => a.id === 'read_the_tape').unlocked, false);
});

test('scholar never unlocks on an empty shelf', () => {
  const a = computeAchievements({ lessonsDone: 0, lessonsTotal: 0 });
  assert.equal(a.find((x) => x.id === 'scholar').unlocked, false,
    'zero of zero must not read as "every lesson read"');
});

test('single-source streak: the Study Room imports the deck’s helper', () => {
  const learnSrc = fs.readFileSync(path.join(__dirname, '..', 'routes', 'learn.js'), 'utf8');
  assert.match(learnSrc, /require\('\.\.\/lib\/achievements'\)/,
    'two streak implementations would drift; there must be one');
  assert.equal(diaryStreak([]), 0);
  const today = new Date().toISOString().slice(0, 10);
  assert.equal(diaryStreak([today]), 1);
});

// ── the route ────────────────────────────────────────────────────────────────
let server, base, tok, uid;

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['deck@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['deck@test.io']);
  uid = u[0].id;
  tok = jwt.sign({ user_id: uid, email: u[0].email }, process.env.JWT_SECRET);
  const now = new Date();
  const ins = `INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price,
    margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`;
  await pool.execute(ins, [uid, 'BTCUSDT', 'LONG', 100, 110, 500, 3, 150, 'tp', now, now]);
  await pool.execute(
    'INSERT INTO learn_diary (user_id, day, body, created_at) VALUES (?, ?, ?, ?)',
    [uid, now.toISOString().slice(0, 10), 'wrote about it', now]);
  const app = express();
  app.use(express.json());
  app.use('/api/command', require('../routes/command'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function req(p, tokIn) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, {
      headers: tokIn ? { Authorization: `Bearer ${tokIn}` } : {},
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test('the deck: authed, counts-only, facts assembled from records', async () => {
  assert.equal((await req('/api/command')).status, 401);
  const r = await req('/api/command', tok);
  assert.equal(r.status, 200);
  const d = r.data;
  assert.equal(d.arena.closes, 1);
  assert.equal(d.study.diary_total, 1);
  assert.equal(d.study.diary_streak, 1);
  assert.ok(d.study.lessons_total >= 3);
  assert.equal(d.arena.quests.length, 3);
  const by = Object.fromEntries(d.achievements.map((a) => [a.id, a.unlocked]));
  assert.equal(by.first_close, true);
  assert.equal(by.first_words, true);
  assert.equal(by.both_halves, true, 'a close and an entry in the same week');
  assert.equal(by.readable, false);
  // §4 posture, scanned not assumed: no money word anywhere in the payload.
  const raw = JSON.stringify(d).toLowerCase();
  for (const word of ['balance', 'equity', 'usd', '"pnl"', 'margin']) {
    assert.ok(!raw.includes(word), `the deck must not carry ${word}`);
  }
  assert.equal(d.counts_only, true);
});

test('the page: hall + rune ceremony reused, everything through T()', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'command.html'), 'utf8');
  assert.match(page, /learn-hall\.js/, 'the deck breathes the same hall');
  assert.match(page, /rune-card\.js/, 'a minted rune gets its ceremony here too');
  assert.match(page, /granted by arithmetic/i);
  assert.match(page, /T\('qn\.' \+ q\.key, q\.name\)/, 'quest names translate with server English fallback');
  assert.match(page, /T\('ach\.' \+ a\.id, a\.en\)/, 'achievement titles translate by stable id');
  const i18n = require('../public/js/i18n.js');
  const keys = ['nav.command', 'cd.h', 'cd.lede', 'cd.signed_out', 'cd.fail', 'cd.s_streak',
    'cd.s_best', 'cd.s_closes', 'cd.s_diary', 'cd.s_entries', 'cd.s_lessons', 'cd.q_h',
    'cd.r_h', 'cd.r_none', 'cd.a_h', 'cd.disc',
    ...require('../lib/arena_streaks').QUEST_POOL.map((q) => 'qn.' + q.key),
    ...ACHIEVEMENTS.map((a) => 'ach.' + a.id)];
  for (const k of keys) {
    const en = String((i18n.STRINGS[k] || {}).en || '');
    assert.ok(en, `${k} missing from the dictionary`);
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
});
