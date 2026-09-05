'use strict';
/**
 * The seventh module reaches the front door, and the teaching loop closes:
 * landing guardian band card → /approvals → lesson deep link → /learn opens
 * the exact lesson. Pins the hash-open guard (only known slugs open).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');

test('the landing guardian band carries the seventh module', () => {
  // explore.html: this section moved off the landing page with /explore.
  const index = read('explore.html');
  const band = index.slice(index.indexOf('id="guardianTease"'), index.indexOf('</section>', index.indexOf('id="guardianTease"')));
  assert.match(band, /<a class="feature" href="\/approvals">/);
  assert.match(band, /data-i18n="lp\.g7_p"/);
  assert.match(band, /A clean result is never a guarantee/);
});

test('the approvals page links the lesson, the lesson opens from the hash', () => {
  assert.match(read('approvals.html'), /href="\/learn#lesson=approvals-and-delegates"/);
  const learn = read('learn.html');
  assert.match(learn, /\[#&\]lesson=\(\[a-z0-9-\]\+\)/, 'the hash is pattern-validated');
  assert.match(learn, /LESSONS\.some\(function \(l\) \{ return l\.slug === m\[1\]; \}\)/,
    'only slugs that exist on the shelf open — a crafted hash opens nothing');
  assert.match(learn, /lessonList\(\); openFromHash\(\);/, 'runs after the shelf loads');
});

test('both keys ship all 14 locales', () => {
  const i18n = read('js', 'i18n.js');
  for (const key of ['lp.g7_p', 'av.learn_l']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const m = read('index.html').match(/i18n(?:-core)?\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 92, 'buster >= 92');
});
