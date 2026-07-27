'use strict';
/**
 * The Allowance X-ray joins the Guardian constellation everywhere the suite
 * announces itself: the hub's orbit and card grid, the firewall's topnav
 * (its other half), and the approvals page links back to the hub. All
 * translated; the card copy keeps the bounded-honesty line.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');

test('the guardian hub announces the X-ray in orbit and grid', () => {
  const hub = read('guardian.html');
  assert.match(hub, /\{ label: 'Allowance X-ray', emoji: '🩻', href: '\/approvals' \}/,
    'the orbit carries the new module');
  assert.match(hub, /<a class="card" href="\/approvals">/);
  assert.match(hub, /data-i18n="gd\.c7_p"/);
  assert.match(hub, /a clean result is never a guarantee/,
    'the card copy keeps the bounded-honesty line');
});

test('the two firewall halves link each other', () => {
  assert.match(read('firewall.html'), /href="\/approvals" data-i18n="nav\.approvals"/);
  assert.match(read('approvals.html'), /href="\/firewall" data-i18n="nav\.firewall"/);
  assert.match(read('approvals.html'), /href="\/guardian" data-i18n="nav\.guardian"/);
});

test('all three new keys ship all 14 locales', () => {
  const i18n = read('js', 'i18n.js');
  for (const key of ['nav.approvals', 'gd.c7_p', 'gd.c7_go']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const m = read('guardian.html').match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 87, 'buster >= 87');
});
