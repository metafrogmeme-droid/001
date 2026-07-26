'use strict';
// The weekly agent letter in the reader's language — the last English left on
// the site. The letter is GENERATED, so the architecture shifts one step: the
// composers emit, alongside each section's assembled English `html`, the
// unassembled `parts` it was built from (template id + raw params). Renderers
// rebuild sentences per language; anything without parts — letters stored
// before parts existed, or a tid the dictionary doesn't know — renders its
// stored English. Omit, never invent.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
const L = require('../lib/letter.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

const WEEK = { key: '2026-W29', start: new Date('2026-07-13'), end: new Date('2026-07-20') };
const richData = {
  trades: [
    { symbol: 'BTC/USDT:USDT', pnl: '12.5', entry_price: 60000, exit_price: 61000,
      opened_at: '2026-07-14T00:00:00Z', closed_at: '2026-07-15T00:00:00Z', direction: 'LONG' },
    { symbol: 'ETH/USDT:USDT', pnl: '-4', entry_price: 3000, exit_price: 2950,
      opened_at: '2026-07-14T00:00:00Z', closed_at: '2026-07-15T00:00:00Z', direction: 'LONG' },
  ],
  equity: { start: 10000, end: 10008.5 },
  signals: [{ direction: 'LONG', regime: 'trend_up' }, { direction: 'SHORT', regime: 'trend_up' }],
  openCount: 1,
  reports: { parity: { verdict: 'aligned' }, arb: { total_accrued_usd: 3.2 } },
};
const flatData = { trades: [], equity: { start: null, end: null }, signals: [], openCount: 0, reports: null };

test('both composers emit parts alongside unchanged English html', () => {
  for (const fn of ['composeLetter', 'composePublicLetter']) {
    const l = L[fn](WEEK, richData);
    assert.ok(l.headline_part && l.headline_part.tid, `${fn}: headline has no part`);
    assert.ok(l.footer_tid, `${fn}: footer has no tid`);
    for (const s of l.sections) {
      assert.ok(s.title_tid, `${fn}: section "${s.title}" has no title_tid`);
      assert.ok(Array.isArray(s.parts) && s.parts.length, `${fn}: section "${s.title}" has no parts`);
      assert.ok(typeof s.html === 'string' && s.html.length, `${fn}: html gone — it is the stored truth`);
      for (const p of s.parts) assert.ok(p.tid && p.params !== undefined, `${fn}: malformed part in "${s.title}"`);
    }
  }
  // The private English is byte-stable: the exact grinder sentence survives.
  const priv = L.composeLetter(WEEK, richData);
  assert.match(priv.sections[0].html, /A grinder's week: 2 closed trades and only 50% winners/);
});

test('every tid either composer can emit exists in all 14 languages', () => {
  // Reachable-tid inventory: run both composers across the data shapes that
  // select different branches, and require a dictionary row for each.
  const shapes = [richData, flatData,
    { ...richData, trades: richData.trades.map((t) => ({ ...t, pnl: '-4' })) },       // losing week
    { ...richData, trades: [richData.trades[0]] },                                    // clean week, no losers
    { ...richData, signals: [], openCount: 0 },                                       // quiet tape, flat ahead
  ];
  const tids = new Set(['t.week', 't.performance', 't.equity', 't.alpha', 't.tape', 't.side', 't.ahead']);
  for (const fn of ['composeLetter', 'composePublicLetter']) {
    for (const d of shapes) {
      const l = L[fn](WEEK, d);
      tids.add(l.headline_part.tid);
      tids.add(l.footer_tid);
      for (const s of l.sections) for (const p of s.parts || []) tids.add(p.tid);
    }
  }
  assert.ok(tids.size >= 25, `expected a wide tid inventory, found ${tids.size}`);
  for (const tid of tids) {
    const e = i18n.STRINGS['lt.' + tid];
    assert.ok(e, `lt.${tid} missing — that sentence renders English on a translated letter`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `lt.${tid} is missing ${c}`);
  }
});

test('every slot in every letter template survives every translation', () => {
  // Derived from the ENGLISH template, so a new slot is covered automatically.
  for (const [k, e] of Object.entries(i18n.STRINGS)) {
    if (!k.startsWith('lt.')) continue;
    const slots = [...String(e.en).matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    for (const c of codes) {
      for (const n of slots) {
        assert.ok(String(e[c]).includes('{' + n + '}'), `${k}:${c} dropped the {${n}} slot`);
      }
    }
  }
});

test('params are data and get escaped; templates are the only trusted HTML', () => {
  // Both renderers must escape params inside ltFill — a regime name or parity
  // verdict is upstream data and must never ride into innerHTML unescaped.
  const dash = read('public/js/dashboard.js');
  const page = read('public/letter.html');
  assert.match(dash, /const ltFill = \(tpl, params\) => tpl\.replace\(\/\\\{\(\\w\+\)\\\}\/g, \(m, k\) =>\n\s+params && params\[k\] != null \? esc\(String\(params\[k\]\)\) : m\)/);
  assert.match(page, /params && params\[k\] != null \? esc\(String\(params\[k\]\)\) : m/);
});

test('a letter without parts renders its stored English — omit, never invent', () => {
  // Letters stored before parts existed have only title/html. Both renderers
  // must fall back to that html rather than to a blank or a guess.
  const dash = read('public/js/dashboard.js');
  const page = read('public/letter.html');
  assert.match(dash, /return s\.html;\s+\/\/ stored English — the fallback/);
  assert.match(page, /return sane\(s\.html\);/);
  // And an unknown tid inside parts poisons the WHOLE section back to English
  // rather than mixing two languages in one sentence.
  assert.match(dash, /done\.every\(\(x\) => x != null\)/);
  assert.match(page, /done\.every\(function \(x\) \{ return x != null; \}\)/);
});

test('the letter API payload keeps its English html for MCP/API consumers', () => {
  const pub = L.composePublicLetter(WEEK, flatData);
  assert.equal(pub.headline, 'A flat week, by choice');
  assert.match(pub.footer, /account size is never published/);
  assert.match(pub.sections[0].html, /patience is a position too/);
});
