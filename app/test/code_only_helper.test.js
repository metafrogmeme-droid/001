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

// ── the two desyncs found on 2026-08-23 ──────────────────────────────────
//
// Both were found the same way: a new file was written, a guard scanned it,
// and the guard failed on a string that only appeared in a COMMENT. Chasing
// that produced a measurement — SIXTEEN of the files the guards scan were
// desynchronising, `public/js/dashboard.js` (40 comment lines surviving) and
// `public/js/app.js` (28) among them. Those two are what
// `panel_failure_honesty.test.js` reads as the structural enforcer of this
// repo's central rule, and its own docstring records a comment-induced false
// negative that blanking was installed to prevent. Blanking was running; on
// those files it stopped a third of the way in.
//
// After the fix every guard still passes, which is the part worth saying out
// loud: the lost coverage was not concealing a defect. It was concealing
// whether there was one.

test('a quote inside a regex character class does not open a string', () => {
  // The trigger. `/[&<>"']/g` is the most ordinary line in any HTML escaper,
  // and the `"` inside it put the scanner into string mode, from where it ran
  // to whatever quote came next and desynchronised everything after.
  //
  // The comment is on the SAME LINE deliberately. String contents survive
  // `codeOnly` verbatim, so a regex swallowed into a string still MATCHES a
  // test looking for its text — the first draft of this assertion passed with
  // regex tracking disabled for exactly that reason. What differs is whether a
  // trailing comment gets blanked, because the runaway string consumes it.
  const src = 'const esc = s => s.replace(/[&<>"\']/g, m => m); // blank me\nkeep();\n';
  const out = codeOnly(src);
  assert.match(out, /\/\[&<>"'\]\/g/, 'the regex literal itself must survive verbatim');
  assert.doesNotMatch(out, /blank me/, 'the comment after the regex survived blanking');
  assert.match(out, /keep\(\);/);
});

test('a division is not mistaken for a regex', () => {
  // The opposite error, and the more destructive one: reading `a / b` as a
  // regex opener swallows code up to the next slash.
  const src = 'const r = total / count;\nconst q = (a) / (b);\n// blank me\nkeep();\n';
  const out = codeOnly(src);
  assert.match(out, /total \/ count/);
  assert.match(out, /\(a\) \/ \(b\)/);
  assert.doesNotMatch(out, /blank me/);
  assert.match(out, /keep\(\);/);
});

test('a regex after `return` is a regex, not a division', () => {
  const src = 'function f() { return /ab/.test(x); }\n// blank me\n';
  const out = codeOnly(src);
  assert.match(out, /return \/ab\/\.test/);
  assert.doesNotMatch(out, /blank me/);
});

test('an unterminated slash is treated as division, not as a file-eating regex', () => {
  // Failing SAFE: a lone `/` that never closes must cost one character, not
  // the remainder of the file.
  const src = 'const a = b /\nconst c = 1;\n// blank me\nkeep();\n';
  const out = codeOnly(src);
  assert.match(out, /const c = 1;/);
  assert.doesNotMatch(out, /blank me/);
  assert.match(out, /keep\(\);/);
});

test('a NESTED template literal closes at the right backtick', () => {
  // The older half of the bug, and the one that cost dashboard.js its
  // coverage. Treating a backtick like any other quote closes the outer
  // template on the INNER one, after which template text is scanned as code.
  const src = 'const h = `<div>${c ? `<span>${x}</span>` : \'\'}</div>`;\n// blank me\nkeep();\n';
  const out = codeOnly(src);
  assert.match(out, /<span>/, 'template text must survive verbatim');
  assert.doesNotMatch(out, /blank me/, 'the comment after a nested template survived');
  assert.match(out, /keep\(\);/);
});

test('a comment INSIDE a ${} hole is blanked, because that hole is code', () => {
  const src = 'const h = `x${ /* gone */ y }z`;\nkeep();\n';
  const out = codeOnly(src);
  assert.doesNotMatch(out, /gone/, 'a ${} expression is code and its comments must blank');
  assert.match(out, /keep\(\);/);
});

test('braces inside a ${} hole do not close it early', () => {
  const src = 'const h = `a${ f({ k: 1 }) }b`;\n// blank me\nkeep();\n';
  const out = codeOnly(src);
  assert.match(out, /`a\$\{ f\(\{ k: 1 \}\) \}b`/);
  assert.doesNotMatch(out, /blank me/);
});

test('a single-quoted string cannot span a line', () => {
  // One unbalanced apostrophe used to consume the rest of the file looking for
  // its partner. A real string literal cannot contain a raw newline, so the
  // scan stops at one and the damage is bounded to that line.
  const src = "const a = 'it's broken;\nkeep();\n// blank me\n";
  const out = codeOnly(src);
  assert.match(out, /keep\(\);/);
  assert.doesNotMatch(out, /blank me/);
});

test('every file the guards scan round-trips without desynchronising', () => {
  // The property, measured over the real corpus rather than over fixtures.
  // A `//` comment line whose output still holds text means the scanner lost
  // its place somewhere upstream.
  const fs2 = require('node:fs');
  const path2 = require('node:path');
  const offenders = [];
  for (const dir of ['public/js', 'routes', 'lib']) {
    const abs = path2.join(__dirname, '..', dir);
    for (const f of fs2.readdirSync(abs)) {
      if (!f.endsWith('.js')) continue;
      const src = fs2.readFileSync(path2.join(abs, f), 'utf8');
      const out = codeOnly(src);
      if (out.length !== src.length) { offenders.push(`${dir}/${f}: length changed`); continue; }
      const s = src.split('\n');
      const o = out.split('\n');
      const survived = s.filter((line, k) => line.trim().startsWith('//') && o[k].trim().length > 0);
      if (survived.length) offenders.push(`${dir}/${f}: ${survived.length} comment line(s) survived`);
    }
  }
  assert.deepEqual(offenders, [],
    'the scanner lost its place in these files, so every guard reading them is '
    + 'inspecting less than it reports:\n  ' + offenders.join('\n  '));
});
