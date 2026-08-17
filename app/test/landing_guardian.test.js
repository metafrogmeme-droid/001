'use strict';
/**
 * Landing page tells the whole story: a Guardian showcase section (the
 * differentiated safety suite) and hero "explore" links to the flagship
 * experiences, so a first-time visitor sees the breadth — not just a signup box.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

// Reads explore.html: this content moved off the landing page when
// /explore was split out. The assertions below are about the CONTENT
// existing and being correct, not about which document holds it.
const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'explore.html'), 'utf8');
// The hero stayed on the landing page when /explore was split out, so
// hero assertions read index.html while section assertions read explore.
const landing = require('node:fs').readFileSync(
  require('node:path').join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the landing page links the flagship experiences', () => {
  // Moved out of the hero — see the note in landing_arena.test.js. The
  // requirement is that these are reachable from the landing page, which is
  // what is asserted; the hero was only where they used to sit.
  // `landing`, not `index` — in THIS file `index` is bound to explore.html
  // (see the header comment). Slicing the wrong document here would have made
  // the test look for a landing-page row inside /explore and fail on correct
  // markup.
  const row = landing.slice(landing.indexOf('class="how-explore"'));
  const cut = row.slice(0, row.indexOf('</section>'));
  assert.match(cut, /href="\/strengthmap"/);
  assert.match(cut, /href="\/guardian"/);
  assert.match(cut, /href="\/agents"/);
});

test('a Guardian section showcases every module, each linking to its live tool', () => {
  const sec = index.slice(index.indexOf('id="guardianTease"'));
  const cut = sec.slice(0, sec.indexOf('</section>') + 10);
  for (const href of ['/flight', '/stress', '/sentinel', '/firewall', '/escape', '/intent']) {
    assert.ok(cut.includes(`href="${href}"`), `Guardian section links ${href}`);
  }
  assert.match(cut, /Flight Recorder/);
  assert.match(cut, /Universal Escape Agent/);
  assert.match(cut, /data-i18n="sec\.guardian_h"/);
  assert.match(cut, /data-i18n="sec\.guardian_cta"/);
  assert.match(cut, /href="\/guardian"/);              // "Explore Guardian" CTA
});

test('the new landing copy is translated (all six locales) + cache-buster bumped', () => {
  for (const key of ['sec.guardian_h', 'sec.guardian_p', 'sec.guardian_cta', 'hero.explore_map']) {
    assert.match(i18n, new RegExp("'" + key.replace('.', '\\.') + "'"));
  }
  // sec.guardian_p carries all six locale codes
  const line = i18n.split('\n').find(l => l.includes("'sec.guardian_p'"));
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(line.includes(loc), `guardian_p has ${loc}`);
  assert.match(index, /i18n\.js\?v=\d+/);
});
