'use strict';
/**
 * `pct(null)` was "+0.00%", in green.
 *
 * strengthmap.js formatted every percentage through
 *
 *     const pct = (v) => (v >= 0 ? '+' : '') + (Number(v) || 0).toFixed(2) + '%';
 *
 * which is most of CLAUDE.md's table in one expression, and it rendered FOUR
 * kinds of absent as TWO different confident answers:
 *
 *     null      ->  "+0.00%"  green        undefined ->  "0.00%"  red
 *     ""        ->  "+0.00%"  green        NaN       ->  "0.00%"  red
 *
 * Same missing data, opposite claims, decided by which flavour of absent
 * happened to arrive. `null >= 0` is TRUE in JavaScript and `undefined >= 0`
 * is FALSE; `Number('')` is 0 AND finite, so it slips an isFinite check.
 *
 * AND THE COLOUR WAS SEPARATELY WRONG IN dashboard.js. Three cells looked like
 *
 *     <td class="${x.delta_bps >= 0 ? 'up' : 'down'}">
 *        ${x.delta_bps != null ? fmt(x.delta_bps) : '—'}</td>
 *
 * The TEXT is null-guarded — the author knew the value goes missing — and the
 * COLOUR is not. So the cell printed an em-dash and painted it green: "no
 * reading" and "up" in the same cell, and green is the half a reader takes in
 * at a glance. Colour is a claim.
 *
 * WHAT THIS FILE ENFORCES is not "no colour ternaries". `moveClass` and
 * `pnlClass` each contain exactly one, and that is where one belongs — inside
 * a function that has already answered "is this readable?". The rule is that
 * CALL SITES go through such a helper, and the ratchet below is the way that
 * is kept true without pretending the whole tree has been audited.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');

const stripJs = (t) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '')
   .split('\n').map((l) => l.replace(/(^|\s)\/\/.*$/, '')).join('\n');

const COLOUR = /(>=\s*0)\s*\?\s*['"](up|pos|chip--up|up-c)['"]\s*:\s*['"](down|neg|chip--down|down-c)['"]/g;

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const full = path.join(dir, d.name);
    if (d.isDirectory()) return walk(full);
    return /\.(js|html)$/.test(d.name) ? [full] : [];
  });
}

function countColourTernaries() {
  const out = {};
  for (const f of walk(PUB)) {
    const n = (stripJs(fs.readFileSync(f, 'utf8')).match(COLOUR) || []).length;
    if (n) out[path.relative(PUB, f).split(path.sep).join('/')] = n;
  }
  return out;
}

/**
 * A ratchet in BOTH directions, like tests/unreachable_baseline.txt.
 *
 * A file appearing here, or a count going UP, means somebody added a colour
 * claim from a bare comparison and has to say why. A count going DOWN means
 * one was fixed and the number must come down in the same commit, so a stale
 * allowance cannot quietly cover the next one.
 *
 * `js/strengthmap.js: 1` and `js/app.js: 1` are the guarded helpers
 * themselves; `js/engine-card-model.js: 1` is gated on `showNet`, which is
 * that file's own readability check. The rest are UNAUDITED — this pass fixed
 * strengthmap's formatter and dashboard's three text-guarded cells, and did
 * not claim to sweep the tree.
 */
const BASELINE = {
  'agents.html': 1,
  'arena.html': 2,
  'compare.html': 1,
  'index.html': 2,
  'js/app.js': 1,
  'js/dashboard.js': 11,
  'js/engine-card-model.js': 1,
  'js/sandbox.js': 2,
  'js/strengthmap.js': 1,
  'js/theater.js': 3,
  'sentinel.html': 2,
  'strategy.html': 1,
};

test('no new colour claim is derived from a bare comparison', () => {
  const now = countColourTernaries();
  const added = Object.entries(now).filter(([f, n]) => n > (BASELINE[f] || 0));
  assert.deepStrictEqual(added, [],
    'a colour class is being decided by `x >= 0` at a new site. `null >= 0` is ' +
    'true and `undefined >= 0` is false, so an absent value paints green or ' +
    'red depending on which flavour of absent arrived. Route it through a ' +
    'helper that returns \'\' for unreadable — see moveClass in ' +
    'strengthmap.js / dashboard.js, or pnlClass in app.js.');
});

test('the baseline shrinks when a site is fixed', () => {
  const now = countColourTernaries();
  const stale = Object.entries(BASELINE).filter(([f, n]) => (now[f] || 0) < n);
  assert.deepStrictEqual(stale, [],
    'a colour ternary was removed without lowering the baseline. A stale ' +
    'allowance silently covers the NEXT one added to that file, which is how ' +
    'a ratchet stops ratcheting.');
});

// ── the formatter, driven ───────────────────────────────────────────────────

const smSrc = fs.readFileSync(path.join(PUB, 'js', 'strengthmap.js'), 'utf8');
const pct = eval('(' + smSrc.slice(smSrc.indexOf('const pct = (v) =>') + 'const pct = '.length,
                                   smSrc.indexOf('// Colour is a claim')).trim().replace(/;$/, '') + ')');
const moveClass = eval('(' + smSrc.slice(smSrc.indexOf('const moveClass = (v) =>') + 'const moveClass = '.length,
                                         smSrc.indexOf('const smooth =')).trim().replace(/;$/, '') + ')');

test('the helpers were actually extracted', () => {
  // The slice above is text-based, so a reformat could yield something that is
  // not a function and every behaviour test below would fail confusingly.
  assert.strictEqual(typeof pct, 'function', 'pct was not extracted');
  assert.strictEqual(typeof moveClass, 'function', 'moveClass was not extracted');
});

for (const absent of [null, undefined, '', '   ', NaN, 'n/a']) {
  test(`an absent percentage (${JSON.stringify(absent)}) is not a measurement`, () => {
    assert.strictEqual(pct(absent), '—',
      'an unreadable value is still being formatted as a number');
    assert.strictEqual(moveClass(absent), '',
      'an unreadable value is still being given a direction colour');
  });
}

test('a genuine zero survives as a measured flat move', () => {
  // THE OTHER HALF. 0 is real, measured and break-even; a blunt fix that
  // muted it would be the opposite error.
  assert.strictEqual(pct(0), '+0.00%');
  assert.strictEqual(moveClass(0), 'up');
});

test('real moves keep their sign and their colour', () => {
  assert.strictEqual(pct(2.5), '+2.50%');
  assert.strictEqual(pct(-2.5), '-2.50%');
  assert.strictEqual(moveClass(2.5), 'up');
  assert.strictEqual(moveClass(-2.5), 'down');
});

test('a numeric string is still a number', () => {
  // CONTROL: the empty-string guard must not reject "2.5" from a JSON payload.
  assert.strictEqual(pct('2.5'), '+2.50%');
  assert.strictEqual(moveClass('-2.5'), 'down');
});

test('the three dashboard cells that painted an em-dash green go through the guard', () => {
  const src = stripJs(fs.readFileSync(path.join(PUB, 'js', 'dashboard.js'), 'utf8'));
  for (const v of ['x.delta_bps', 't.change_24h_pct']) {
    assert.ok(src.includes(`moveClass(${v})`), `${v} no longer uses moveClass`);
  }
  assert.ok(!/class="num r \$\{x\.delta_bps >= 0/.test(src),
    'the delta_bps cell decides its colour from a bare comparison again');
});
