'use strict';
// Screen-reader users get a language too.
//
// 87 aria-labels shipped, none translated — so a blind user reading in any of
// the eleven non-English languages heard "Main", "Open menu", "Send message",
// "Confirm trade" in English on every control. The i18n engine has supported
// `data-i18n-attr="aria-label:key"` since it was written (it is the example in
// its own header comment) and it had never once been used for aria-label.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const i18n = require('../public/js/i18n');
const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));

// Static markup only. Labels built inside <script> string concatenation carry
// runtime values and are localized in JS, not by the attribute path — scanning
// them here would flag code this mechanism cannot reach.
function markup(f) {
  return fs.readFileSync(path.join(PUB, f), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '<script></script>');
}

test('every static aria-label in markup is wired to the dictionary', () => {
  const bare = [];
  for (const f of pages) {
    const src = markup(f);
    for (const m of src.matchAll(/<[^>]*?aria-label="([^"$]+)"[^>]*?>/g)) {
      if (!/data-i18n-attr="[^"]*aria-label:/.test(m[0])) {
        bare.push(`${f}: aria-label="${m[1]}"`);
      }
    }
  }
  assert.deepStrictEqual(bare, [],
    'these labels are announced in English regardless of the reader\'s language:\n  '
      + bare.join('\n  '));
});

test('every aria-label key resolves in all twelve languages', () => {
  const keys = new Set();
  for (const f of pages) {
    const src = markup(f);
    for (const m of src.matchAll(/aria-label:([\w.]+)/g)) keys.add(m[1]);
  }
  assert.ok(keys.size >= 26, `expected the full a11y sweep, found ${keys.size} keys`);
  for (const k of keys) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const l of i18n.LANGS) {
      const v = i18n.STRINGS[k][l.code];
      assert.ok(typeof v === 'string' && v.length, `${k} missing ${l.code}`);
    }
  }
});

test('wiring an aria-label never drops a sibling attribute mapping', () => {
  // One element already mapped its placeholder; merging must keep both. A lost
  // placeholder would be a silent regression for sighted users.
  const idx = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
  const m = idx.match(/id="hero-email"[^>]*data-i18n-attr="([^"]+)"/);
  assert.ok(m, 'the hero email input still declares its attribute mappings');
  assert.match(m[1], /aria-label:a11y\.email/, 'the new aria-label mapping is present');
  assert.match(m[1], /placeholder:auth\.email_ph/, 'the original placeholder mapping survived');
});

test('only wirings that actually reach the engine are shipped', () => {
  // apply() runs once at init with no MutationObserver, so a data-i18n-attr on
  // markup a script builds LATER never gets translated — and a page that does
  // not load i18n.js cannot translate anything at all. Wiring either one looks
  // correct in the diff and does nothing in the browser, which is worse than
  // leaving it plainly English. Both were verified in a real page before this
  // test was written; agents.html and arena's timeframe row are follow-ups
  // tied to the remaining page sweep.
  const agents = fs.readFileSync(path.join(PUB, 'agents.html'), 'utf8');
  assert.ok(!/i18n\.js/.test(agents), 'agents.html still has no engine (tracked)');
  assert.ok(!/aria-label:/.test(agents),
    'agents.html must not carry wiring its page cannot apply');

  for (const f of ['escape.html', 'stress.html']) {
    const src = fs.readFileSync(path.join(PUB, f), 'utf8');
    assert.match(src, /i18n\.js/, `${f} loads the engine`);
    assert.match(src, /aria-label:a11y\.remove/, `${f} keeps its verified wiring`);
  }
});

test('the English label stays in the markup as the never-blank fallback', () => {
  // data-i18n-attr overwrites the attribute at apply() time; if the key were
  // ever missing, the inline English is what a screen reader still announces.
  for (const f of pages) {
    const src = markup(f);
    for (const m of src.matchAll(/<[^>]*data-i18n-attr="[^"]*aria-label:[^"]*"[^>]*>/g)) {
      assert.match(m[0], /aria-label="[^"]+"/,
        `${f}: an element maps aria-label but ships no English fallback`);
    }
  }
});
