'use strict';
// The last English on a Japanese dashboard — found by walking the rendered
// DOM in a real browser, not by grepping. Nineteen nodes survived every
// previous sweep; after this pass the only English left is the weekly letter,
// which is a server-side document generator and a separate piece of work.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('every residue string exists in all 14 languages', () => {
  for (const k of ['dd.h_nopf', 'dd.m_empty', 'dd.w_hint', 'dd.ac_today', 'dd.ac_closed',
    'dd.ac_none', 'dd.ac_carrying', 'dd.ac_open', 'dd.ac_riskpref', 'dd.rp_conservative',
    'dd.rp_balanced', 'dd.rp_aggressive', 'dd.ac_watching', 'dd.b_ask', 'dd.b_hub',
    'dd.b_signals', 'dd.b_portfolio', 'dd.chk_verify_l', 'dd.chk_verify_h', 'dd.chk_paper_h',
    'dd.chk_tg_h', 'dd.chk_keys_l', 'dd.chk_keys_h', 'dd.cta_keys', 'dd.cta_finish_keys',
    'dd.chk_live_l', 'dd.chk_live_on', 'dd.chk_live_off']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('every slot survives every translation', () => {
  for (const [k, names] of Object.entries({ 'dd.ac_closed': ['n', 'w'], 'dd.ac_open': ['n'] })) {
    for (const c of codes) {
      for (const n of names) {
        assert.ok(String(i18n.STRINGS[k][c]).includes('{' + n + '}'),
          `${k}:${c} dropped the {${n}} slot`);
      }
    }
  }
});

test('the checklist steps carry stable keys, and the tracker uses them', () => {
  // The "just done" pop was tracked by LABEL in localStorage. Labels are
  // language-dependent now: a language switch would have marked every
  // completed step newly-done and replayed the celebration. Keys are stable;
  // the old label values are still accepted so existing storage doesn't
  // re-pop once either.
  for (const key of ['verify', 'paper', 'tg', 'keys', 'live']) {
    assert.match(dash, new RegExp(`\\{ key: '${key}',`), `checklist step '${key}' lost its stable key`);
  }
  assert.match(dash, /!prevDone\.has\(s\.key\) && !prevDone\.has\(s\.label\)/);
  assert.match(dash, /steps\.filter\(\(s\) => s\.done\)\.map\(\(s\) => s\.key\)/,
    'completion is stored by label again — language switches will re-pop every step');
});

test('the risk-preference buttons translate their labels, not their values', () => {
  // data-riskpref carries the API value and must stay English; only the
  // visible label localises.
  assert.match(dash, /data-riskpref="\$\{m\}"/);
  assert.match(dash, /T\('dd\.rp_' \+ m,/);
});

test('the residue strings are gone from the source as literals', () => {
  // Each now flows through T(); the English survives only as the fallback
  // argument. A bare re-introduction (no T) is what this guards against.
  for (const lit of ['<span>Today for you</span>', '<span>Carrying</span>',
    '<span>Your risk preference</span>', '<span>Watching</span>',
    '>💬 Ask your agent<', "label: 'Verify your email'", "label: 'Connect an exchange'",
    "label: 'Go live'"]) {
    assert.ok(!dash.includes(lit), `untranslated literal is back: ${lit}`);
  }
});
