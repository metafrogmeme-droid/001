'use strict';
// The Intent Compiler explains WHO stops a bad trade — now in the reader's
// language. Same architecture as the firewall and the escape agent, because it
// has survived three pages: the model stays English (it is also the
// compile_intent MCP payload), every rule variant carries a stable id, and the
// render rebuilds each sentence from the id + value with English as fallback.
//
// `axis` could not key the translation: daily-loss and drawdown share 'loss',
// long-only and short-only share 'direction'. Ids are per-variant.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const M = require('../public/js/intent-model.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'intent.html'), 'utf8');
const modelSrc = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'intent-model.js'), 'utf8');

// One sentence per variant, chosen to trip exactly that recognizer.
const TRIP = {
  size_max: 'max 5% per trade',
  exposure_max: 'total exposure under 60%',
  exposure_cash: 'keep 30% in cash',
  lev_none: 'no leverage',
  lev_max: 'max 3x',
  dir_long: 'no shorts',
  dir_short: 'short only',
  loss_daily: 'stop if down 4% today, daily',
  loss_dd: 'stop if down 8%',
  conf_min: 'min confidence 70%',
  conf_high: 'high-confidence only',
  scope_majors: 'majors only',
  scope_list: 'only btc and eth',
  venue_nobridge: 'no bridges',
  approval_limit: 'approve orders over $500',
  rate_cap: 'max 4 trades per day',
  emg_escape: 'unwind everything on my signal',
  emg_systemic: 'watch for a depeg',
};

test('every rule variant compiles with its stable id', () => {
  for (const [id, phrase] of Object.entries(TRIP)) {
    const out = M.compile(phrase);
    assert.ok(out.rules.some((r) => r.id === id),
      `"${phrase}" did not produce id ${id} — got [${out.rules.map((r) => r.id)}]`);
  }
});

test('every id has a label and a human sentence in all 14 languages', () => {
  for (const id of Object.keys(TRIP)) {
    for (const half of ['in.r.', 'in.h.']) {
      const e = i18n.STRINGS[half + id];
      assert.ok(e, `${half}${id} missing — a half-translated envelope is worse than none`);
      for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${half}${id} is missing ${c}`);
    }
  }
});

test('tiers, axes, warnings, periods and page strings exist everywhere', () => {
  const keys = [
    ...['wallet', 'gate', 'approval', 'monitor'].flatMap((t) => ['in.t.' + t, 'in.tn.' + t]),
    ...M.AXES.map((a) => 'in.ax.' + a),
    'in.w.no_loss', 'in.w.no_size', 'in.w.nothing',
    'in.per.day', 'in.per.hour', 'in.per.week',
    'in.s_compiled', 'in.s_tightest', 'in.s_none', 'in.s_empty', 'in.s_prefix',
    'in.p_empty', 'in.p_fail',
  ];
  for (const k of keys) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('every slot survives every translation', () => {
  const slots = {
    'in.h.size_max': ['p'], 'in.h.exposure_max': ['p'], 'in.h.exposure_cash': ['p', 'keep'],
    'in.h.lev_max': ['x'], 'in.h.loss_daily': ['p'], 'in.h.loss_dd': ['p'],
    'in.h.conf_min': ['p'], 'in.h.scope_majors': ['syms'], 'in.h.scope_list': ['syms'],
    'in.h.rate_cap': ['n', 'per'],
    'in.s_compiled': ['n', 'c', 't'], 'in.s_tightest': ['rule', 'tier'],
  };
  for (const [k, names] of Object.entries(slots)) {
    for (const c of codes) {
      for (const n of names) {
        assert.ok(String(i18n.STRINGS[k][c]).includes('{' + n + '}'),
          `${k}:${c} dropped the {${n}} slot — the sentence renders with a hole in it`);
      }
    }
  }
});

test('the model stays English — it is also the MCP payload', () => {
  assert.doesNotMatch(modelSrc, /RCI18N|translate\(/,
    'the model must not localise — compile_intent returns these strings to agents');
  // And the additions are additive: the English label/human are still there.
  const out = M.compile('max 5% per trade');
  assert.equal(out.rules[0].label, 'Max size per position');
  assert.match(out.rules[0].human, /No single position may exceed 5% of the book\./);
});

test('warning_ids parallels warnings, one id per sentence, same order', () => {
  const noLoss = M.compile('max 5% per trade');
  assert.deepEqual(noLoss.warning_ids, ['no_loss']);
  assert.equal(noLoss.warnings.length, 1);
  const nothing = M.compile('the weather is nice');
  assert.deepEqual(nothing.warning_ids, ['nothing']);
  const covered = M.compile('max 5% per trade, stop if down 8%');
  assert.deepEqual(covered.warning_ids, []);
});

test('summaryParts distinguishes empty, none and plan', () => {
  assert.equal(M.compile('').summaryParts.kind, 'empty');
  assert.equal(M.compile('the weather is nice').summaryParts.kind, 'none');
  const p = M.compile('max 5% per trade, stop if down 8%').summaryParts;
  assert.equal(p.kind, 'plan');
  assert.equal(p.count, 2);
  assert.equal(p.strongestId, 'size_max', 'wallet-tier rules sort first');
  assert.equal(p.strongestTier, 'wallet');
});

test('the page renders by id, with the model’s English as fallback', () => {
  assert.match(page, /T\('in\.r\.' \+ r\.id, r\.label\)/);
  assert.match(page, /T\('in\.t\.' \+ r\.tier, r\.tier_label\)/);
  assert.match(page, /T\('in\.tn\.' \+ r\.tier, r\.tier_note\)/);
  assert.match(page, /function humanFor\(r\)/);
  assert.match(page, /T\('in\.w\.' \+ id, w\)/);
  assert.match(page, /T\('in\.ax\.' \+ a, AXIS_LABEL\[a\] \|\| a\)/);
});

test('i18n.js loads BEFORE the page script that renders at parse time', () => {
  // intent.html calls render() synchronously at the end of its IIFE — the
  // exact boot-order bug the escape page shipped with. Pin the ordering.
  const i18nAt = page.indexOf('/js/i18n-core.js');
  const modelAt = page.indexOf('/js/intent-model.js');
  assert.ok(i18nAt > -1 && modelAt > -1);
  assert.ok(i18nAt < modelAt,
    'i18n.js is loaded after the page script — the only render paints English');
  assert.equal((page.match(/\/js\/i18n(?:-core)?\.js\?v=/g) || []).length, 1,
    'the old trailing i18n tag is back alongside the new one');
});

test('the rate-cap period is translated as a word, not passed through raw', () => {
  assert.match(page, /T\('in\.per\.' \+ \(v && v\.per\), v && v\.per\)/,
    'the per-day/hour/week noun would render in English inside a translated sentence');
});
