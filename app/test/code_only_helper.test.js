'use strict';
/**
 * The scanner two guards depend on, tested once.
 *
 * §4 (public_no_dollars) and F-15 (f15_error_responses) each carried their own
 * copy, both written with `/\/\*[\s\S]*?\*\//g`. That treats a `/*` inside a
 * STRING as a comment start; when it has no matching close in the same string,
 * the fake comment runs to the next `*\/` anywhere later and blanks every line
 * between. On server.js that cost 68% of the file — the F-15 guard inspected a
 * third of it and called the whole thing clean.
 *
 * The §4 copy was measured at 0.4% worst case across every route, so it was
 * not losing coverage — it simply carried the same trap, waiting for a public
 * route to gain a string containing `/*`.
 *
 *     THE SAME BUG IN TWO COPIES IS ONE BUG WITH TWO PLACES TO RECUR.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

test('an unclosed comment marker in a string does not blank what follows', () => {
  // THE BUG. Note it must be UNMATCHED: a first attempt at this test used
  // '/**/*.js', which is self-closing, so the broken scanner handled it and
  // the test passed against the very code it was meant to indict.
  const src = [
    "const open = '/*';",
    'const kept = 1;',
    "const close = '*/';",
  ].join('\n');
  assert.ok(codeOnly(src).includes('const kept = 1;'));
});

test('a // inside a string is not treated as a comment', () => {
  const src = "const u = 'a//b'; const kept = 2;";
  const out = codeOnly(src);
  assert.ok(out.includes('a//b'), 'string contents must survive verbatim');
  assert.ok(out.includes('const kept = 2;'));
});

test('a real block comment is blanked', () => {
  const out = codeOnly('/* secret */ const x = 1;');
  assert.ok(!out.includes('secret'));
  assert.ok(out.includes('const x = 1;'));
});

test('a real line comment is blanked', () => {
  const out = codeOnly('const x = 1; // secret\nconst y = 2;');
  assert.ok(!out.includes('secret'));
  assert.ok(out.includes('const y = 2;'));
});

test('line numbers survive', () => {
  // A plain-delete version once reported tool8257.js:45 for a leak at :57.
  const src = 'a\n/* one\n   two\n   three */\nb\n// four\nc';
  assert.equal(codeOnly(src).split('\n').length, src.split('\n').length);
});

test('an escaped quote does not end the string early', () => {
  const src = "const s = 'it\\'s /* not a comment */ fine'; const kept = 3;";
  assert.ok(codeOnly(src).includes('const kept = 3;'));
});

test('an unterminated block comment does not throw', () => {
  assert.doesNotThrow(() => codeOnly('const x = 1; /* never closed'));
});

test('an unterminated string does not throw', () => {
  assert.doesNotThrow(() => codeOnly("const x = 'never closed"));
});

test('a template literal survives', () => {
  const src = 'const t = `a /* b */ c`; const kept = 4;';
  const out = codeOnly(src);
  assert.ok(out.includes('/* b */'), 'template contents are string contents');
  assert.ok(out.includes('const kept = 4;'));
});

test('both guards use this helper and keep no copy of their own', () => {
  // The point of extracting it. A third copy is how the trap comes back.
  for (const f of ['public_no_dollars.test.js', 'f15_error_responses.test.js']) {
    const src = fs.readFileSync(path.join(__dirname, f), 'utf8');
    assert.ok(src.includes("require('./helpers/code_only')"), `${f} must share it`);
    assert.ok(!/function codeOnly\s*\(/.test(src),
      `${f} still defines its own codeOnly — that is how the two diverged`);
  }
});

test('it keeps essentially all of a comment-free file', () => {
  // A blunt sanity floor on the helper itself, where it IS meaningful:
  // with no comments present, nothing may be removed.
  const src = 'const a = 1;\nres.json({ ok: true });\nconst b = "x";\n';
  assert.equal(codeOnly(src), src);
});
