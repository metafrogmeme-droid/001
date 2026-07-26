'use strict';
// "Your Arena week" — the private weekly paper recap, parts-rendered so it
// reads in the reader's language, honest about quiet weeks, and never stored
// (deterministic over append-only closes).

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { composeArenaLetter } = require('../lib/arena_letter.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const WEEK = { key: '2026-W29', start: new Date('2026-07-13'), end: new Date('2026-07-20') };
const t = (reason, pnl, symbol = 'BTCUSDT') => ({ reason, pnl, symbol, closed_at: '2026-07-15T00:00:00Z' });

test('a quiet week says so — no invented activity', () => {
  const L = composeArenaLetter(WEEK, { trades: [], openCount: 0 });
  assert.equal(L.headline, 'A flat paper week');
  assert.equal(L.headline_part.tid, 'aw_h_flat');
  assert.equal(L.sections[0].parts[0].tid, 'aw_flat');
  // No discipline section (nothing to read), no calls section.
  assert.ok(!L.sections.some((s) => s.title_tid === 'discipline'));
  assert.ok(!L.sections.some((s) => s.title_tid === 'calls'));
  assert.equal(L.virtual, true);
});

test('an active week carries counts, discipline and calls — all as parts', () => {
  const trades = [t('tp', 12), t('sl', -5), t('trail', 8), t('manual', 3), t('liquidated', -20, 'SOLUSDT')];
  const L = composeArenaLetter(WEEK, { trades, openCount: 2 });
  assert.equal(L.headline_part.tid, 'aw_h');
  assert.deepEqual(L.headline_part.params, { wr: 60, n: 5 });
  const byTid = Object.fromEntries(L.sections.map((s) => [s.title_tid, s]));
  assert.deepEqual(byTid.week.parts[0].params, { n: 5, wr: 60, net: '-2' });
  assert.equal(byTid.discipline.parts[0].tid, 'aw_disc');
  assert.equal(byTid.discipline.parts[0].params.p, 60);        // tp+sl+trail of 5
  assert.equal(byTid.discipline.parts[1].tid, 'aw_disc_liq');
  assert.equal(byTid.calls.parts[0].params.sym, 'BTCUSDT');
  assert.equal(byTid.calls.parts[1].params.sym, 'SOLUSDT');
  assert.equal(byTid.ahead.parts[0].tid, 'aw_ahead_open');
  // Every section keeps its English html — the fallback a renderer needs.
  for (const s of L.sections) assert.ok(s.html && s.html.length);
});

test('the discipline section declines below its minimum sample', () => {
  const L = composeArenaLetter(WEEK, { trades: [t('tp', 5), t('manual', -2)], openCount: 0 });
  assert.ok(!L.sections.some((s) => s.title_tid === 'discipline'),
    'two closes graded is noise graded — the discipline read must decline');
});

test('the calls section needs two distinct results', () => {
  const one = composeArenaLetter(WEEK, { trades: [t('tp', 5)], openCount: 0 });
  assert.ok(!one.sections.some((s) => s.title_tid === 'calls'));
  const same = composeArenaLetter(WEEK, { trades: [t('tp', 5), t('tp', 5)], openCount: 0 });
  assert.ok(!same.sections.some((s) => s.title_tid === 'calls'),
    'best === worst is one call wearing two labels');
});

test('the route is PRIVATE and composes for the last completed week', () => {
  const src = read('routes', 'arena.js');
  const at = src.indexOf("router.get('/letter'");
  assert.ok(at > -1);
  const slice = src.slice(at, at + 1200);
  assert.match(slice, /authMiddleware/);
  assert.match(slice, /lastCompletedWeek\(\)/);
  assert.match(slice, /WHERE user_id = \?/);
  assert.doesNotMatch(slice, /INSERT/, 'the letter is deterministic — nothing is stored');
});

test('every aw.* string exists in all 14 languages, slots intact', () => {
  const keys = Object.keys(i18n.STRINGS).filter((k) => k.startsWith('aw.'));
  assert.ok(keys.length >= 17, `expected the full aw.* set, found ${keys.length}`);
  for (const k of keys) {
    const e = i18n.STRINGS[k];
    const slots = [...String(e.en).matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    for (const c of codes) {
      assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
      for (const n of slots) {
        assert.ok(String(e[c]).includes('{' + n + '}'), `${k}:${c} dropped the {${n}} slot`);
      }
    }
  }
});

test('the panel renders parts with English fallback, all-or-nothing per section', () => {
  const page = read('public', 'arena.html');
  assert.match(page, /T\('aw\.' \+ part\.tid, ''\)/);
  assert.match(page, /done\.every\(function \(x\) \{ return x != null; \}\)/);
  assert.match(page, /return s2\.html;/);
});
