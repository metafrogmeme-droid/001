/*
 * The JS half of the honesty ratchet — recorded, and failing only on growth.
 *
 * `scripts/honesty_gate.py` has covered the Python half since it was written,
 * and states its own hole in capitals: **Python only. The JS half of every
 * shape above is NOT checked here.** On 2026-09-09 something went through it.
 *
 * PR #314 fixed one nightly-audit card in three places. Two were Python. The
 * third was `app/public/js/dashboard.js`:
 *
 *     <b class="num ${g.net_r > 0 ? 'neg' : 'pos'}">
 *
 * It was found by a corollary sweep — a human deciding to look — and nothing
 * would have found the next one. That is the argument `honesty_gate.py` makes
 * for its own existence, one language over: *reading every diff and auditing
 * the previous PR both work and neither scales.*
 *
 * The shape is JS-specific and it is worse than the table's version, because
 * the two ways of being absent disagree:
 *
 *     undefined >= 0   ->  false  ->  the else branch   (a LOSS, red)
 *     null      >= 0   ->  TRUE   ->  the then branch   (a WIN, green)
 *
 * Python cannot host this rule: `None > 0` raises there, so the same mistake
 * is loud in one language and silent in the other.
 *
 * THE BASELINE IS A LIST OF PLACES TO LOOK, not a list of defects — the same
 * claim `honesty_gate.py` makes, for the same reason. Most of these are fine:
 * `theater.js` guards its `pnl >= 0` behind `Number.isFinite` a function
 * away, `lib/replay.js` computes the `net_pnl_usd` it compares, `sentinel.js`
 * returns 'unknown' on a null score in its first line. All three look exactly
 * like the bug. None is one. Lowering a number means somebody read one and
 * decided.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const H = require('./helpers/honesty_shapes');

const BASELINE = path.join(__dirname, 'js_honesty_baseline.json');

function readBaseline() {
  return JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
}

test('the rule set matches the one the baseline was recorded under', () => {
  const base = readBaseline();
  assert.strictEqual(
    H.rulesFingerprint(), base.rules_fingerprint,
    'CANNOT CHECK: the baseline was recorded by a different rule set. Different '
    + 'rules count different things on an identical tree, so any growth reported '
    + 'here would be an artefact of the vocabulary rather than a fact about the '
    + 'code. Re-record deliberately: node app/test/update_js_honesty_baseline.js');
});

test('no new honesty shapes in the JS surfaces', () => {
  const base = readBaseline();
  const now = H.countsFrom(H.scanAll());
  const grown = [];
  for (const shape of H.SHAPES) {
    const files = new Set([
      ...Object.keys(now[shape] || {}),
      ...Object.keys(base.counts[shape] || {}),
    ]);
    for (const f of [...files].sort()) {
      const was = (base.counts[shape] || {})[f] || 0;
      const is = (now[shape] || {})[f] || 0;
      if (is > was) grown.push(`  ${shape.padEnd(22)} ${f}: ${was} -> ${is}  (+${is - was})`);
    }
  }
  assert.deepStrictEqual(grown, [],
    'new honesty shapes — this gate fails on growth, not on the backlog:\n'
    + grown.join('\n')
    + '\n\nRead each one. If the zero really is a measurement (the field cannot '
    + 'be null, the value is computed here, the branch is guarded upstream), '
    + 're-record deliberately: node app/test/update_js_honesty_baseline.js');
});

test('an improvement is re-recorded in the same commit', () => {
  // The other direction, and the `known_failures.txt` rule for the same
  // reason: a baseline left sitting above reality stops being a floor.
  const base = readBaseline();
  const now = H.countsFrom(H.scanAll());
  const stale = [];
  for (const shape of H.SHAPES) {
    for (const [f, was] of Object.entries(base.counts[shape] || {})) {
      const is = (now[shape] || {})[f] || 0;
      if (is < was) stale.push(`  ${shape.padEnd(22)} ${f}: ${was} -> ${is}`);
    }
  }
  assert.deepStrictEqual(stale, [],
    'these improved and the baseline still records the old number — re-record '
    + 'in the same commit:\n' + stale.join('\n'));
});

/*
 * PLANTED TREES. A real-tree assertion can pass for a reason unrelated to the
 * rule — CLAUDE.md records a guard that survived two mutations because a
 * different receiver poisoned the name anyway. These are synthetic sources
 * where the rule is the only thing in play.
 */

test('it catches the exact line that got through', () => {
  const bit = "<b class=\"num ${g.net_r > 0 ? 'neg' : 'pos'}\">";
  const hits = H.scanSource(bit, { file: 'planted.js' });
  assert.strictEqual(hits.length, 1, 'the #314 defect is invisible to this gate');
  assert.strictEqual(hits[0].shape, 'bare-compare-verdict');
  assert.strictEqual(hits[0].name, 'net_r');
});

test('net_r is a measurement to the shared vocabulary', () => {
  // The reason the line above was invisible when this gate was first written:
  // `net_r` split to ['net','r'] and neither was in the word list, on a repo
  // whose entire shadow book is denominated in R. The word `net` was added to
  // tests/honesty_vocabulary.json, which BOTH gates read.
  assert.ok(H.isMeasurement('net_r'));
  assert.ok(H.isMeasurement('r_multiple'));
  assert.ok(H.isMeasurement('avg_r_multiple'));
  assert.ok(H.isMeasurement('net_pnl_usd'));
  // And the obvious false positives it must not create.
  assert.ok(!H.isMeasurement('network'));
  assert.ok(!H.isMeasurement('netting'));
  assert.ok(!H.isMeasurement('retries'));
});

test('it does not catch the fix that replaced it', () => {
  assert.deepStrictEqual(
    H.scanSource("const tone = TONE[g.verdict] || 'muted';", { file: 'p.js' }), []);
});

test('the honest guard is not a hit', () => {
  // `x > 0 ? value : null` is the shape this gate exists to encourage. A gate
  // that scolds the cure teaches the wrong thing.
  for (const src of [
    'const r = size > 0 ? pnl / size : null;',
    "const cls = pnlPct === null ? 'muted' : (pnlPct >= 0 ? 'up' : 'down');",
    "cls: !Number.isFinite(pnl) ? '' : (pnl > 0 ? 'pos' : 'neg'),",
    'profit_factor: grossLoss > 0 ? grossWin / grossLoss : null,',
  ]) {
    assert.deepStrictEqual(H.scanSource(src, { file: 'p.js' }), [], src);
  }
});

test('a sign prefix and a precision choice are not verdicts', () => {
  assert.deepStrictEqual(H.scanSource("const s = pnl >= 0 ? '+' : '';", { file: 'p.js' }), []);
  assert.deepStrictEqual(H.scanSource('const dp = price >= 100 ? 2 : 4;', { file: 'p.js' }), []);
});

test('the or-zero shapes are caught, and only on measurements', () => {
  const hit = (src) => H.scanSource(src, { file: 'p.js' }).map((h) => h.shape);
  assert.deepStrictEqual(hit('const v = t.pnl || 0;'), ['or-zero']);
  assert.deepStrictEqual(hit('const v = t.equity ?? 0;'), ['or-zero']);
  assert.deepStrictEqual(hit('if ((t.pnl || 0) >= 0) wins++;'), ['or-zero-compare']);
  assert.deepStrictEqual(hit('const e = Number(row.equity) || 0;'), ['coerce-or-zero']);
  assert.deepStrictEqual(hit('const e = parseFloat(row.drawdown) || 0;'), ['coerce-or-zero']);
  // A count, an index and a retry budget may default to zero honestly.
  for (const ok of ['const n = rows.length || 0;', 'const i = idx || 0;',
                    'const t = opts.retries || 0;']) {
    assert.deepStrictEqual(hit(ok), [], ok);
  }
});

test('comments are stripped before matching', () => {
  // A comment that quotes the string it forbids is indistinguishable from the
  // code doing it — the trap CLAUDE.md counts five instances of, two of them
  // in the session that wrote this file.
  assert.deepStrictEqual(
    H.scanSource('// this used to be `const v = t.pnl || 0;`\nconst v = mustRead(t);',
      { file: 'p.js' }), []);
});

test('one line is charged to one shape', () => {
  // `(pnl || 0) >= 0` contains a bare or-zero. Counting both moves the
  // baseline in two directions for one edit.
  const hits = H.scanSource('if ((t.pnl || 0) >= 0) wins++;', { file: 'p.js' });
  assert.strictEqual(hits.length, 1);
});

test('the baseline has the shape the ratchet depends on', () => {
  const base = readBaseline();
  assert.match(base.rules_fingerprint, /^[0-9a-f]{16}$/);
  assert.ok(base.counts && typeof base.counts === 'object');
  for (const shape of H.SHAPES) {
    assert.ok(shape in base.counts, `${shape} missing from the baseline`);
  }
  assert.strictEqual(typeof base.total, 'number');
});

test('the vocabulary really is the one the Python gate reads', () => {
  // Not "a copy that matches today" — the same file. A second copy of a
  // threshold is a second answer, and then the two gates disagree about what
  // a measurement is.
  assert.ok(H.VOCABULARY_FILE.endsWith(path.join('tests', 'honesty_vocabulary.json')));
  assert.ok(fs.existsSync(H.VOCABULARY_FILE));
  const py = fs.readFileSync(
    path.join(__dirname, '..', '..', 'scripts', 'honesty_gate.py'), 'utf8');
  assert.ok(py.includes('honesty_vocabulary.json'),
    'honesty_gate.py no longer reads the shared vocabulary — the two gates have drifted');
});
