'use strict';
// Arena refusals carry stable codes, and the panel translates by code with
// the server's English as fallback. Also: the tp/sl wording now tells the
// truth — three of the four call paths validate against the LIVE MARK, and
// at open the fill IS the current price, so "the entry" was only ever
// accidentally right.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const arena = require('../lib/arena.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
const routeSrc = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');

test('every validator refusal carries a stable code', () => {
  assert.equal(arena.validateOpen({ symbol: '!!' }).code, 'bad_symbol');
  assert.equal(arena.validateOpen({ symbol: 'BTCUSDT', direction: 'FLAT' }).code, 'bad_direction');
  assert.equal(arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 1 }).code, 'min_margin');
  assert.equal(arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 2 }, 10).code, 'balance');
  assert.equal(arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 99 }, 1000).code, 'leverage');
  assert.equal(arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 2 }, 1000, 5).code, 'max_open');
  assert.equal(arena.validateTpSl('LONG', 100, -1, null).code, 'tp_positive');
  assert.equal(arena.validateTpSl('LONG', 100, null, -1).code, 'sl_positive');
  assert.equal(arena.validateTpSl('LONG', 100, 90, null).code, 'tp_above');
  assert.equal(arena.validateTpSl('SHORT', 100, 110, null).code, 'tp_below');
  assert.equal(arena.validateTpSl('LONG', 100, null, 110).code, 'sl_below');
  assert.equal(arena.validateTpSl('SHORT', 100, null, 90).code, 'sl_above');
  assert.equal(arena.validateTrail(999).code, 'trail_range');
});

test('the tp/sl wording says "the current price", not "the entry"', () => {
  assert.match(arena.validateTpSl('LONG', 100, 90, null).error, /current price/);
  assert.match(arena.validateTpSl('LONG', 100, null, 110).error, /current price/);
  // public/js/arena_engine.js, not lib/arena.js: the rules moved there so the
  // browser sandbox and the server load the same bytes, and lib/arena.js is
  // now a one-line re-export. Scanning the shim would pass VACUOUSLY — the
  // banned string is trivially absent from a file with no wording in it.
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'arena_engine.js'), 'utf8');
  assert.ok(!src.includes('sit above the entry') && !src.includes('sit below the entry'),
    'the misleading "entry" wording is back — three call paths validate against the mark');
});

test('the routes return the code alongside the error', () => {
  assert.match(routeSrc, /\{ error: v\.error, code: v\.code \}/);
  assert.match(routeSrc, /\{ error: ts\.error, code: ts\.code \}/);
  assert.match(routeSrc, /\{ error: tv\.error, code: tv\.code \}/);
});

test('the panel translates refusals by code, English fallback', () => {
  assert.match(page, /T\('arena\.e_' \+ d\.code, d\.error \|\| fallbackEn\)/);
  // The three surfaces that show refusals all use the helper.
  assert.equal((page.match(/refusal\(/g) || []).length >= 4, true,
    'open/close/exits refusals bypass the translator');
});

test('every refusal code has a translation in all 14 languages', () => {
  for (const k of ['bad_symbol', 'bad_direction', 'min_margin', 'balance', 'leverage',
    'max_open', 'tp_positive', 'sl_positive', 'tp_above', 'tp_below', 'sl_below',
    'sl_above', 'trail_range']) {
    const e = i18n.STRINGS['arena.e_' + k];
    assert.ok(e, `arena.e_${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `arena.e_${k} is missing ${c}`);
  }
});
