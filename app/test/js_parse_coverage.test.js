'use strict';
/**
 * The parse gate must reach every JS file this app ships — and go on reaching
 * it after the next directory is added.
 *
 * CI's "Parse — every script must at least compile" step listed
 * `*.js lib/*.js routes/*.js public/js/*.js scripts/*.js`. `public/js/*.js` is
 * not the browser's JS; it is the browser's TOP-LEVEL js. The i18n split put
 * thirteen language chunks under `public/js/i18n/`, loaded at runtime as
 * `'/js/i18n/' + lang + '.js'`, so a syntax error in any one broke the site
 * for THAT LANGUAGE ONLY — the quietest failure available, because nobody
 * testing in English would ever see it. `public/sw.js`, registered by both
 * index.html and dashboard.html, was unchecked for the same reason.
 *
 * That is the third time a gate here came up one directory short: the same
 * step once compiled `lib/` and `routes/` but not `scripts/`, and
 * `totp_secret_at_rest.test.js` swept two readers of a column while skipping
 * the one caller that WRITES it. Every one of them was a correct list that a
 * later directory outgrew — so the step is a `find` now, and this file pins
 * the property rather than the list.
 *
 * TWO THINGS THIS TEST CAUGHT IN ITS OWN FIRST DRAFT, both worth keeping:
 *
 *  - It located the step with `indexOf(name)` and there are TWO steps with
 *    that name (token/ and app/). It was reading token's file list and
 *    reporting on the wrong job entirely.
 *  - It asserted `public/vendor` was rightly excluded "because node --check
 *    rejects the ES module", and node --check accepts it. The reason was
 *    invented, so the exclusion went away and vendor is gated like everything
 *    else — those bundles ship to users too.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const APP = path.join(__dirname, '..');
const CI = path.join(APP, '..', '.github', 'workflows', 'ci.yml');

/** Every .js file under app/ that ships or runs, node_modules aside. */
function shippedJsFiles() {
  const out = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name.startsWith('.')) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === 'node_modules') continue;
        walk(full);
      } else if (e.name.endsWith('.js')) {
        out.push(path.relative(APP, full));
      }
    }
  })(APP);
  return out;
}

/**
 * The body of the app's parse step, read from ci.yml rather than restated.
 *
 * Anchored on `working-directory: app` because TWO steps carry this name —
 * the first draft of this helper matched token/'s and reported on the wrong
 * job while looking entirely convincing.
 */
function appParseStep() {
  const yml = fs.readFileSync(CI, 'utf8');
  const NAME = 'Parse — every script must at least compile';
  const hits = [];
  let i = yml.indexOf(NAME);
  while (i !== -1) { hits.push(i); i = yml.indexOf(NAME, i + 1); }
  assert.ok(hits.length > 0, 'the parse step was renamed or removed');

  // LINE-BASED, not a character window. The first version sliced 700 chars
  // from the name and went red the moment a comment was added between the
  // name and its `run:` — reporting "the step went back to a glob list" about
  // a step it simply could not see. A gate that fails for a reason unrelated
  // to its rule is the failure this file is about, one level up.
  const lines = yml.split('\n');
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].includes(NAME)) continue;
    const indent = lines[i].search(/\S/);
    const block = [lines[i]];
    for (let j = i + 1; j < lines.length; j++) {
      const l = lines[j];
      // The step ends at the next sibling list item at the same indent.
      if (l.trim() && l.search(/\S/) <= indent && /^\s*- /.test(l)) break;
      block.push(l);
    }
    blocks.push(block.join('\n'));
  }
  const mine = blocks.filter(b => /working-directory:\s*app\b/.test(b));
  assert.strictEqual(mine.length, 1,
    `expected exactly one app-scoped parse step, found ${mine.length}`);
  return mine[0];
}

test('the app parse step enumerates rather than lists', () => {
  const step = appParseStep();
  assert.match(step, /find \. -name '\*\.js'/,
    'the parse step went back to a hand-written glob list. A list is true '
    + 'when it is written; that is exactly how public/js/i18n/ and sw.js '
    + 'shipped unchecked.');
  assert.match(step, /node --check/, 'the step no longer compiles anything');
});

test('node_modules is the only thing the parse step excludes', () => {
  const step = appParseStep();
  const excluded = [...step.matchAll(/-not -path '([^']+)'/g)].map(m => m[1]);
  assert.deepStrictEqual([...new Set(excluded)], ["./node_modules/*"],
    'a new exclusion appeared. Every one of these needs a reason that is '
    + 'actually true — the first draft excluded public/vendor on the grounds '
    + 'that node --check rejects its ES module, and it does not.');
});

test('the step enumerates once, so the count and the check cannot disagree', () => {
  /**
   * An earlier draft ran `find` twice — once to count, once to compile — and
   * the two expressions could drift into checking a different set than the
   * one the floor was measured against. A floor over a different population
   * is the `hold_rate` defect in CLAUDE.md, one gate over.
   */
  const step = appParseStep();
  const finds = [...step.matchAll(/find \. -name/g)].length;
  assert.strictEqual(finds, 1,
    `the step runs find ${finds} times; the count and the compile must walk `
    + 'the same enumeration');
});

test('the parse step cannot pass having compiled nothing', () => {
  /**
   * `find ... | xargs node --check` reports XARGS's exit status, and GitHub's
   * default shell is `bash -e`, not pipefail. A find that fails outright hands
   * xargs an empty stream, xargs exits 0, and the step goes green having
   * compiled nothing — `xargs -r` guarantees it. That is a gate that cannot
   * tell "everything compiled" from "nobody looked", which is the whole
   * subject of this repo's guard tests.
   *
   * Driven, not assumed: the mangled form below is what the line looked like
   * when it carried a backslash continuation and something joined it onto one
   * line, and `find` really does reject it while the pipeline really does
   * report success.
   */
  const { spawnSync } = require('node:child_process');
  const broken = spawnSync('bash', ['-e', '-c',
    "find . -name '*.js' -not -path './node_modules/*' -print0 \\ "
    + '| xargs -0 -r -n1 true'], { cwd: APP, encoding: 'utf8' });
  assert.strictEqual(broken.status, 0,
    'the premise changed: a broken find no longer passes through the pipe');
  assert.match(broken.stderr, /paths must precede expression|unknown predicate/,
    'expected find itself to have failed in the mangled form');

  const step = appParseStep();
  assert.match(step, /set -o pipefail/,
    'without pipefail a failed find passes this step silently');
  assert.match(step, /-ge \d+ \]/,
    'the step needs a floor on the file count: pipefail alone does not catch '
    + 'a find that legitimately matches nothing');
  assert.doesNotMatch(step, /xargs -0 -r/,
    '`xargs -r` suppresses the empty case, which is the case worth failing on');
});

test('the counted floor is below the real count and above nothing', () => {
  const floor = Number(appParseStep().match(/-ge (\d+) \]/)[1]);
  const actual = shippedJsFiles().length;
  assert.ok(floor > 0, 'a floor of zero is not a floor');
  assert.ok(floor <= actual,
    `the floor (${floor}) is above the real count (${actual}) — CI is red`);
  assert.ok(floor >= actual / 8,
    `the floor (${floor}) is so far below the real count (${actual}) that a `
    + 'mostly-broken enumeration would still clear it');
});

test('the i18n language chunks are covered, and there are still chunks', () => {
  // Named explicitly because they were the files that shipped unchecked, and
  // because a `find`-based assertion alone would also pass if the directory
  // were deleted rather than covered.
  const dir = path.join(APP, 'public', 'js', 'i18n');
  assert.ok(fs.existsSync(dir), 'the split i18n chunk directory is gone');
  const chunks = fs.readdirSync(dir).filter(f => f.endsWith('.js'));
  assert.ok(chunks.length >= 10,
    `expected the split language chunks, found ${chunks.length}`);
  const shipped = new Set(shippedJsFiles());
  for (const c of chunks) {
    assert.ok(shipped.has(path.join('public', 'js', 'i18n', c)),
      `public/js/i18n/${c} is outside what the gate walks`);
  }
});

test('the service worker is covered', () => {
  assert.ok(shippedJsFiles().includes(path.join('public', 'sw.js')),
    'sw.js is outside the gate — index.html and dashboard.html register it');
});

test('every JS file the app ships compiles', () => {
  // The property itself, and what stops the widened gate landing already red.
  // Runs what CI runs, over the same enumeration.
  const failures = [];
  for (const rel of shippedJsFiles()) {
    try {
      execFileSync(process.execPath, ['--check', path.join(APP, rel)],
        { stdio: 'pipe' });
    } catch (e) {
      failures.push(rel);
    }
  }
  assert.deepStrictEqual(failures, [],
    'these ship to users and do not parse:\n  ' + failures.join('\n  '));
});
