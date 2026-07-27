'use strict';
/**
 * The command palette — the whole universe from anywhere. Pins:
 * - room labels ride existing nav.* keys (all 14 languages on day one);
 * - smart input: address/ENS/base58 → the X-ray, receipt-shaped keys → the
 *   sealed receipt — and the palette NAVIGATES only, never acts;
 * - `/` never steals focus from a form field; Ctrl+K toggles; Esc closes;
 * - the fade respects reduced motion; the dialog is aria-labelled;
 * - every inhabited room ships the script.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const pal = read('js', 'palette.js');
const i18n = read('js', 'i18n.js');

test('rooms speak the dictionary; the palette only navigates', () => {
  for (const key of ['nav.dashboard', 'nav.arena', 'nav.learn', 'nav.command',
    'nav.rune', 'nav.guardian', 'nav.firewall', 'nav.approvals', 'nav.escape',
    'nav.stress', 'nav.sentinel', 'nav.flight', 'nav.gas', 'nav.proof']) {
    assert.ok(pal.includes(`'${key}'`), `palette lists ${key}`);
  }
  assert.match(pal, /location\.href = it\[3\]/, 'navigation is the only act');
  assert.ok(!/fetch\(|XMLHttpRequest/.test(pal), 'the palette itself calls nothing');
});

test('smart input recognizes the three shapes honestly', () => {
  assert.match(pal, /ADDR_RE = \/\^0x\[0-9a-fA-F\]\{40\}\$\//);
  assert.match(pal, /SOL_RE = \/\^\[1-9A-HJ-NP-Za-km-z\]\{32,44\}\$\//);
  assert.match(pal, /\/approvals\?a=/);
  assert.match(pal, /\/call\/'/, 'receipt keys open the receipt page');
});

test('keyboard contract: toggle, slash guard, escape', () => {
  assert.match(pal, /\(e\.ctrlKey \|\| e\.metaKey\)/);
  assert.match(pal, /INPUT\|TEXTAREA\|SELECT/, 'slash never steals a form field');
  assert.match(pal, /isContentEditable/);
  assert.match(pal, /e\.key === 'Escape'/);
  assert.match(pal, /role', 'dialog'/);
  assert.match(pal, /reduce \? '' : 'animation:rcpFade/, 'fade off under reduced motion');
});

test('all five pal keys ship all 14 locales', () => {
  for (const key of ['pal.ph', 'pal.addr', 'pal.call', 'pal.empty', 'pal.aria']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
});

test('every inhabited room ships the palette', () => {
  const must = ['index.html', 'dashboard.html', 'arena.html', 'learn.html',
    'command.html', 'rune.html', 'guardian.html', 'firewall.html',
    'approvals.html', 'escape.html', 'stress.html', 'sentinel.html',
    'flight.html', 'gas.html', 'proof.html', 'provable.html', 'call.html',
    'trader.html', 'leaderboard.html'];
  for (const f of must) {
    assert.ok(read(f).includes('/js/palette.js?v='), `${f} carries the palette`);
  }
});
