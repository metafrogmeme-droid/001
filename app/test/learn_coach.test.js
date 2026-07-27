'use strict';
/**
 * The diary's coach — deterministic nudges from the day's own record, never
 * a model. Pins: facts-only rules with priority order and the max-2 cap,
 * silence on empty days, praise only when EVERY close was planned, route
 * wiring, and the page rendering with lesson pointers.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { coachNudges } = require('../lib/learn_coach');

const T = (reason) => ({ symbol: 'X', direction: 'LONG', pnl: 0, reason });

test('no trades — silence, not invented encouragement', () => {
  assert.deepEqual(coachNudges([]), []);
  assert.deepEqual(coachNudges(null), []);
});

test('a liquidation leads, and points at lesson 04', () => {
  const n = coachNudges([T('liquidated'), T('tp')]);
  assert.strictEqual(n[0].id, 'liq');
  assert.strictEqual(n[0].lesson, 'leverage-and-liquidation');
  assert.strictEqual(n[0].params.n, 1);
});

test('improvised-majority points at exit discipline', () => {
  const n = coachNudges([T('manual'), T('manual'), T('tp')]);
  assert.strictEqual(n[0].id, 'improv');
  assert.strictEqual(n[0].lesson, 'exit-discipline');
  assert.deepEqual(n[0].params, { i: 2, t: 3 });
});

test('planned-majority does NOT nag about improvisation', () => {
  const n = coachNudges([T('tp'), T('sl'), T('manual')]);
  assert.ok(!n.some((x) => x.id === 'improv'));
});

test('six closes flags the day, capped at two nudges total', () => {
  const trades = [T('liquidated'), T('manual'), T('manual'), T('manual'), T('manual'), T('manual')];
  const n = coachNudges(trades);
  assert.strictEqual(n.length, 2, 'a bad day is not a lecture');
  assert.deepEqual(n.map((x) => x.id), ['liq', 'improv'], 'priority order holds');
});

test('praise fires only when every close was the planned one', () => {
  const clean = coachNudges([T('tp'), T('trail'), T('sl')]);
  assert.strictEqual(clean.length, 1);
  assert.strictEqual(clean[0].id, 'clean');
  assert.strictEqual(clean[0].lesson, null);
  assert.match(clean[0].en, /One day proves nothing/);
  const notClean = coachNudges([T('tp'), T('manual')]);
  assert.ok(!notClean.some((x) => x.id === 'clean'));
});

test('the route serves nudges and the page renders them with lesson links', () => {
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'learn.js'), 'utf8');
  assert.match(route, /coachNudges\(trades\)/);
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.match(page, /function renderCoach\(nudges\)/);
  assert.match(page, /T\('lc\.' \+ n\.id, n\.en\)/, 'whole-line translation by stable id');
  assert.match(page, /data-lesson=/, 'lesson pointers reuse the existing opener');
  // the streak ember: grows with the streak, hot from 5, reduced-motion safe
  assert.match(page, /streak >= 5 \? ' hot' : ''/);
  assert.match(page, /prefers-reduced-motion: reduce\) \{ \.ember\.hot \{ animation: none; \} \}/);
});

test('all five lc keys ship all 14 locales', () => {
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const key of ['lc.liq', 'lc.improv', 'lc.many', 'lc.clean', 'lc.open']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
});
