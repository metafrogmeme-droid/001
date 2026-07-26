'use strict';
// Moving an open paper position's exits. Managing the exit is half of
// trading, and the Arena teaches trading — but the edit has to be honest
// twice over: levels validate against the LIVE mark (an exit the market has
// already passed would fire the instant it lands), and the open-time seal
// recorded the ORIGINAL levels, so an edited position must say so or the
// receipt overstates the trader's discipline.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const routeSrc = read('routes', 'arena.js');
const page = read('public', 'arena.html');
const db = read('db.js');

test('the route validates against the LIVE mark with the shared validator', () => {
  const exits = routeSrc.slice(routeSrc.indexOf("router.post('/exits'"), routeSrc.indexOf("router.post('/close'"));
  assert.match(exits, /await getTickers\(\)/, 'an exit move is an order-shaped decision — strict fetch');
  assert.match(exits, /arena\.validateTpSl\(p\.direction, mark,/,
    'levels must validate against the MARK, not the entry — a passed level fires instantly');
  assert.doesNotMatch(exits, /validateTpSl\(p\.direction, p\.entry/,
    'validating against the entry would accept a stop the market already passed');
});

test('an edit marks the position, and the payload carries the marker', () => {
  assert.match(routeSrc, /SET tp = \?, sl = \?, trail_pct = \?, exits_edited = 1 WHERE id = \? AND user_id = \?/);
  assert.match(routeSrc, /exits_edited: !!Number\(p\.exits_edited \|\| 0\)/,
    'the account payload hides the edit — the receipt can overstate discipline');
  assert.match(db, /ADD COLUMN exits_edited TINYINT\(1\) NOT NULL DEFAULT 0/,
    'pre-existing deployments never get the column');
});

test('the shim honours the exits UPDATE — both the write and the marker', () => {
  // The same divergence class that has now cost four production bugs: a new
  // statement shape the shim silently no-ops keeps every test green while
  // production behaves differently.
  assert.match(db, /UPDATE ARENA_POSITIONS/);
  assert.match(db, /pos\.tp = params\[0\]; pos\.sl = params\[1\]; pos\.exits_edited = 1;/);
});

test('ownership is enforced in the same statement, not checked separately', () => {
  const exits = routeSrc.slice(routeSrc.indexOf("router.post('/exits'"), routeSrc.indexOf("router.post('/close'"));
  assert.match(exits, /WHERE id = \? AND user_id = \?/,
    'another user’s position id must not be editable');
});

test('the panel shows the moved marker with the receipt-honesty tooltip', () => {
  assert.match(page, /p\.exits_edited \?/);
  assert.match(page, /arena\.x_edited_t/, 'the tooltip explaining WHY the marker exists is gone');
  assert.match(page, /data-exits="' \+ p\.id \+ '"/);
  // Empty input clears a level — stopless is allowed, but visibly.
  assert.match(page, /ntp\.trim\(\) === '' \? null : Number\(ntp\)/);
});

test('every exit-edit string exists in all 14 languages', () => {
  for (const k of ['arena.x_edit_t', 'arena.x_edited', 'arena.x_edited_t', 'arena.x_tp_q',
    'arena.x_sl_q', 'arena.x_saved', 'arena.x_failed']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the ORIGINAL-levels claim survives translation everywhere', () => {
  // This tooltip is the honesty surface: it is what stops an edited position
  // reading as if the sealed receipt covered the new levels.
  const e = i18n.STRINGS['arena.x_edited_t'];
  const orig = {
    en: /ORIGINAL/, hi: /मूल स्तर/, it: /ORIGINALI/, es: /ORIGINALES/, zh: /原始價位/,
    pt: /ORIGINAIS/, fr: /D’ORIGINE/, de: /URSPRÜNGLICHEN/, nl: /OORSPRONKELIJKE/,
    ja: /当初の水準/, ko: /원래 수준/, ru: /ИСХОДНЫЕ/, tr: /ORİJİNAL/, ar: /الأصلية/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), orig[c],
      `arena.x_edited_t:${c} lost the "receipt records the original levels" claim`);
  }
});
