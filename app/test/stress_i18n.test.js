'use strict';
// The Stress Lab breaks your portfolio in the reader's language. Fifth and
// last Guardian surface on this architecture: model English (it is also the
// stress_portfolio MCP payload), stable ids, render-side translation with the
// model's English as fallback.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const M = require('../public/js/stress-model.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'stress.html'), 'utf8');

test('every scenario id has a name in all 14 languages', () => {
  const ids = M.SCENARIOS.map((s) => s.id).concat(['custom']);
  assert.ok(ids.length >= 6);
  for (const id of ids) {
    const e = i18n.STRINGS['sx.sc.' + id];
    assert.ok(e, `sx.sc.${id} missing — the scenario renders in English on a translated page`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `sx.sc.${id} is missing ${c}`);
  }
});

test('every JS-built sentence on the page exists everywhere', () => {
  for (const k of ['sx.liq', 'sx.survives', 'sx.n_liq', 'sx.no_liq', 'sx.worst', 'sx.cash',
    'sx.legs_h', 'sx.v_prefix', 'sx.v_takes', 'sx.v_liqn', 'sx.v_noliq', 'sx.v_first',
    'sx.v_resilient', 'sx.over_alloc', 'sx.cash_note', 'sx.sh_addfirst', 'sx.sh_text',
    'sx.sh_text2', 'sx.sh_copied', 'sx.dir_long', 'sx.dir_short', 'sx.d_long', 'sx.d_short',
    'sx.p_fail']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('every slot survives every translation', () => {
  const slots = {
    'sx.n_liq': ['n'], 'sx.worst': ['a'], 'sx.cash': ['p'], 'sx.cash_note': ['p'],
    'sx.v_takes': ['name', 'pct'], 'sx.v_liqn': ['n'], 'sx.v_first': ['asset', 'x', 'p'],
    'sx.sh_text': ['p', 'name'],
  };
  for (const [k, names] of Object.entries(slots)) {
    for (const c of codes) {
      for (const n of names) {
        assert.ok(String(i18n.STRINGS[k][c]).includes('{' + n + '}'),
          `${k}:${c} dropped the {${n}} slot`);
      }
    }
  }
});

test('the cash note carries the LIVE number, not a hard-coded one', () => {
  // The old dictionary entry said "15% in stablecoin cash" — literally, in
  // fourteen languages. Any reader who changed a weight kept being told 15%.
  // A fabricated number on a page about honesty; the slot is the fix.
  for (const c of codes) {
    assert.ok(String(i18n.STRINGS['sx.cash_note'][c]).includes('{p}'),
      `sx.cash_note:${c} hard-codes a percentage that stops being true on the first edit`);
  }
  // And the element is not statically bound — apply() would stomp the
  // computed value with the raw template on every language switch.
  assert.doesNotMatch(page, /id="cashNote"[^>]*data-i18n/,
    'cashNote is JS-owned; a data-i18n binding overwrites the live number');
});

test('the page renders scenarios by id with the model English as fallback', () => {
  assert.match(page, /T\('sx\.sc\.' \+ x\.scenario\.id, x\.scenario\.name\)/);
  assert.match(page, /T\('sx\.sc\.custom', 'Custom'\)/);
  assert.match(page, /T\('sx\.d_' \+ l\.dir, l\.dir\)/,
    'the per-leg row prints the raw dir enum inside a translated line');
});

test('the verdict emphasis survives translation-driven word order', () => {
  // The scenario name and the drawdown are re-wrapped in <b> AFTER the
  // sentence is translated and escaped, so whatever position a language puts
  // them in, they stay bold and the rest stays escaped.
  assert.match(page, /\\u0000N\\u0000/);
  assert.match(page, /\\u0000P\\u0000/);
  assert.match(page, /\\u0000A\\u0000/);
});

test('i18n.js loads BEFORE the page script that renders at parse time', () => {
  const i18nAt = page.indexOf('/js/i18n.js');
  const modelAt = page.indexOf('/js/stress-model.js');
  assert.ok(i18nAt > -1 && modelAt > -1);
  assert.ok(i18nAt < modelAt,
    'i18n.js is loaded after the page script — the only render paints English');
  assert.equal((page.match(/\/js\/i18n\.js\?v=/g) || []).length, 1);
});

test('the model stays English — it is also the MCP payload', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'stress-model.js'), 'utf8');
  assert.doesNotMatch(src, /RCI18N|translate\(/);
  // Scenario ids are load-bearing now: renaming one silently un-translates it.
  assert.deepEqual(M.SCENARIOS.map((s) => s.id),
    ['majors_down', 'alt_crash', 'depeg', 'cascade', 'black_swan']);
});
