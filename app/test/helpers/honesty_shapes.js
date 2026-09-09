'use strict';
/*
 * The JS half of "unreadable is never zero, and absent is never a measurement."
 *
 * `scripts/honesty_gate.py` says, in its own coverage section:
 *
 *     **Python only.** The JS half of every shape above is NOT checked here.
 *
 * That was a stated hole, and on 2026-09-09 something walked through it. PR
 * #314 fixed a nightly-audit card in three places, and the third was
 * `app/public/js/dashboard.js`:
 *
 *     <b class="num ${g.net_r > 0 ? 'neg' : 'pos'}">
 *
 * It is not an `|| 0`, so no vocabulary of or-zero spellings would ever have
 * found it — and in JavaScript it is worse than the shapes that table lists,
 * because the coercions disagree with each other:
 *
 *     undefined >= 0   false   -> the else branch  (rendered as a LOSS, red)
 *     null      >= 0   TRUE    -> the then branch  (rendered as a WIN, green)
 *     ''        >= 0   TRUE    -> a WIN
 *     NaN       >= 0   false   -> a LOSS
 *
 * One expression, two ways of being absent, two OPPOSITE false verdicts. The
 * Python gate cannot host this rule even in principle: `None > 0` raises a
 * TypeError there, so the same mistake is loud in Python and silent here.
 *
 * WHAT THIS CLAIMS, AND WHAT IT DOES NOT
 * --------------------------------------
 *   * It claims one thing: **these shapes did not increase.** A hit is a place
 *     to LOOK, not a defect. Most are not defects — `theater.js` guards its
 *     `pnl >= 0` behind `Number.isFinite(pnl) ? ... : null` and says so in its
 *     own comment; `lib/replay.js` computes the `net_pnl_usd` it then compares.
 *     Both look exactly like the bug. Neither is one. That is why the backlog
 *     is recorded wholesale rather than swept — the same argument
 *     `honesty_gate.py` makes about `patterns.py` and `arena_trades.pnl`.
 *   * **It is a SCANNER, not a parser.** No JS parser is vendored here (no
 *     acorn, no espree), so this blanks comments with `code_only.js` and then
 *     matches line-wise. It cannot see across lines, it cannot resolve a
 *     receiver, and a shape written inside a string literal counts. Those are
 *     limits, not surprises: stated here rather than left to be discovered,
 *     which is the failure mode this repo spends most of its guard tests on.
 *   * **The vocabulary is not defined here.** It is read from
 *     `tests/honesty_vocabulary.json`, the same file `honesty_gate.py` reads,
 *     because a second copy of a threshold is a second answer — the rule
 *     `winrate-bar.js` states about `MIN_RATED`.
 *
 * `rulesFingerprint()` hashes the vocabulary AND the shape list into the
 * baseline, so widening either makes the counts incomparable and the ratchet
 * reports CANNOT CHECK instead of a verdict.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const { codeOnly } = require('./code_only');

const REPO = path.join(__dirname, '..', '..', '..');
const VOCABULARY_FILE = path.join(REPO, 'tests', 'honesty_vocabulary.json');

const VOCAB = JSON.parse(fs.readFileSync(VOCABULARY_FILE, 'utf8'));
const MEASUREMENT_WORDS = new Set(VOCAB.measurement_words);
const MEASUREMENT_SUBSTRINGS = VOCAB.measurement_substrings;

/** Directories scanned, relative to app/. `test/` is deliberately absent: a
 *  test PLANTS these shapes to prove the code rejects them. Same exclusion
 *  `honesty_gate.py` makes about `tests/`, and the same admitted hole. */
const ROOTS = ['public/js', 'lib', 'routes'];

const SHAPES = [
  'or-zero',              // pnl || 0        ·  pnl ?? 0
  'or-zero-compare',      // (pnl || 0) >= 0
  'coerce-or-zero',       // Number(pnl) || 0
  'bare-compare-verdict', // pnl >= 0 ? 'up' : 'down'   <- the one that bit
];

/**
 * Split an identifier into words, EXACTLY as `honesty_gate._words` does.
 *
 * Mirrored character by character rather than approximated: the whole point of
 * the shared vocabulary is that both gates answer "is this a measurement?" the
 * same way, and a splitter that disagrees defeats that as thoroughly as a
 * second word list would. `tests/test_honesty_vocabulary_is_shared.py` drives
 * both implementations over the same names and fails if they diverge.
 */
function words(name) {
  const out = [];
  let cur = '';
  for (const ch of String(name || '')) {
    const isDigit = ch >= '0' && ch <= '9';
    if (ch === '_' || isDigit) {
      if (cur) out.push(cur.toLowerCase());
      cur = '';
    } else if (ch >= 'A' && ch <= 'Z' && cur
               && !(cur[cur.length - 1] >= 'A' && cur[cur.length - 1] <= 'Z')) {
      out.push(cur.toLowerCase());
      cur = ch;
    } else {
      cur += ch;
    }
  }
  if (cur) out.push(cur.toLowerCase());
  return out;
}

/** Does this identifier name something whose zero is a claim? */
function isMeasurement(name) {
  if (!name) return false;
  const low = String(name).toLowerCase().replace(/_/g, '');
  if (MEASUREMENT_SUBSTRINGS.some((s) => low.includes(s))) return true;
  return words(name).some((w) => MEASUREMENT_WORDS.has(w));
}

/** The last segment of a dotted path — `f.fixed.net_pnl_usd` -> `net_pnl_usd`.
 *  The tail is what names the value; the receiver names where it came from. */
function tail(expr) {
  const parts = String(expr || '').split('.');
  return parts[parts.length - 1];
}

//: An identifier or dotted path, optionally with a bracket index. Deliberately
//: NOT matching calls: `getCount() || 0` is a function's own default, and a
//: function is free to promise it never returns null. A FIELD cannot.
const PATH = String.raw`[A-Za-z_$][\w$]*(?:(?:\.[A-Za-z_$][\w$]*)|(?:\[[^\]\[]{0,40}\]))*`;

const PATTERNS = [
  // (pnl || 0) >= 0 — unreadable WON, because 0 >= 0 is true. Listed BEFORE
  // the bare or-zero so the more specific shape claims the line.
  { shape: 'or-zero-compare',
    re: new RegExp(String.raw`\(\s*(${PATH})\s*(?:\|\||\?\?)\s*0\s*\)\s*(?:>=|>|<=|<)`, 'g') },

  // Number(pnl) || 0 · parseFloat(x.pnl) || 0
  { shape: 'coerce-or-zero',
    re: new RegExp(String.raw`\b(?:Number|parseFloat|parseInt)\s*\(\s*(${PATH})[^()]{0,40}\)\s*(?:\|\||\?\?)\s*0\b`, 'g') },

  // pnl || 0 · pnl ?? 0
  { shape: 'or-zero',
    re: new RegExp(String.raw`(${PATH})\s*(?:\|\||\?\?)\s*0(?![\w.$])`, 'g') },

  // pnl >= 0 ? 'up' : 'down' — THE ONE THAT BIT. A verdict taken from a bare
  // comparison against a literal, on a field that may be absent. Both branches
  // are reachable from an absent value and they disagree, so there is no
  // "safe" side to fall to.
  //
  // The branches are captured because they decide whether this is the defect
  // or its CURE. `size > 0 ? (pnl / size) * 100 : null` is the honest guard —
  // the shape we want written — and the first draft of this pattern flagged
  // 104 lines, most of them exactly that. A gate that scolds the correct
  // idiom teaches the wrong thing, so `isVerdictPair` below decides.
  { shape: 'bare-compare-verdict',
    re: new RegExp(
      String.raw`(${PATH})\s*(?:>=|<=|>|<)\s*-?\d[\d.]*\s*\?([^?:]{0,60}):([^?:;,)\]}]{0,60})`, 'g'),
    branches: true },
];

//: Branch values that mean "I could not say" rather than a verdict.
const ABSTAINS = /^\s*(null|undefined|''|""|``|'—'|"—"|'-'|"-")\s*$/;

//: A bare number on both sides is a scale or a precision, not a claim:
//: `price >= 100 ? 2 : 4` chooses decimal places.
const NUMERIC = /^\s*-?\d[\d.]*\s*$/;

/**
 * Do these two ternary branches assert OPPOSING VERDICTS about the value?
 *
 * The hazard is a reader being told "profit" or "loss", "calm" or "critical",
 * green or red, from a comparison whose subject may be absent — where JS hands
 * `undefined` to one branch and `null` to the other. It is NOT a hazard when
 * one branch abstains: `x > 0 ? x / y : null` is the fix this whole gate
 * exists to encourage, and `pnl >= 0 ? '+' : ''` prints its own subject
 * beside the sign, so a missing value is visible rather than laundered.
 */
function isVerdictPair(a, b) {
  if (ABSTAINS.test(a) || ABSTAINS.test(b)) return false;
  if (NUMERIC.test(a) && NUMERIC.test(b)) return false;
  return true;
}

/**
 * Is this value already established readable ON THIS LINE?
 *
 * `pnlPct === null ? 'muted' : (pnlPct >= 0 ? 'up' : 'down')` and
 * `!Number.isFinite(pnl) ? '' : (pnl > 0 ? 'pos' : ...)` are the fix, written
 * inline. Counting them would put the cure in the same list as the disease,
 * and a backlog that cannot tell them apart is a backlog nobody reads.
 *
 * Deliberately LINE-scoped and therefore incomplete: a guard an `if` above
 * establishes is invisible here, which is why `theater.js` still shows up
 * (its `Number.isFinite` check is a function away) and stays in the baseline
 * as a place to look rather than a defect.
 */
function guardedOnSameLine(line, name) {
  const n = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(
    String.raw`(?:${n}\s*(?:===|!==|==|!=)\s*(?:null|undefined)`
    + String.raw`|(?:null|undefined)\s*(?:===|!==|==|!=)\s*[\w.$]*${n}`
    + String.raw`|isFinite\s*\(\s*[^)]*${n}`
    + String.raw`|isInteger\s*\(\s*[^)]*${n})`).test(line);
}

/**
 * Every hit in one file's source. `{shape, line, name, text}` per hit.
 *
 * A line is charged to at most ONE shape — the first pattern that claims it,
 * in the order above. Without that, `(pnl || 0) >= 0` counts twice (once as
 * itself and once as the bare or-zero inside it) and the baseline moves for a
 * single edit in two directions at once.
 */
function scanSource(src, { file = '' } = {}) {
  const hits = [];
  const lines = codeOnly(String(src || '')).split('\n');
  lines.forEach((line, i) => {
    for (const { shape, re, branches } of PATTERNS) {
      re.lastIndex = 0;
      let m;
      let claimed = false;
      while ((m = re.exec(line)) !== null) {
        const name = tail(m[1]);
        if (!isMeasurement(name)) continue;
        if (branches && !isVerdictPair(m[2] || '', m[3] || '')) continue;
        if (branches && guardedOnSameLine(line, name)) continue;
        hits.push({ shape, file, line: i + 1, name, text: line.trim().slice(0, 160) });
        claimed = true;
      }
      if (claimed) break;            // one shape per line, most specific first
    }
  });
  return hits;
}

/** Walk one root under app/ and scan every .js file in it (not recursive into
 *  node_modules; these directories are flat or shallow by convention). */
function scanRoot(appDir, root) {
  const dir = path.join(appDir, root);
  let names = [];
  try {
    names = fs.readdirSync(dir).filter((f) => f.endsWith('.js')).sort();
  } catch {
    return [];
  }
  return names.flatMap((f) => scanSource(
    fs.readFileSync(path.join(dir, f), 'utf8'), { file: `${root}/${f}` }));
}

/** Every hit across every scanned root, sorted for a stable baseline. */
function scanAll(appDir = path.join(__dirname, '..', '..')) {
  return ROOTS.flatMap((r) => scanRoot(appDir, r));
}

/** Per-shape, per-file counts — the baseline's shape. */
function countsFrom(hits) {
  const out = {};
  for (const shape of SHAPES) out[shape] = {};
  for (const h of hits) {
    out[h.shape][h.file] = (out[h.shape][h.file] || 0) + 1;
  }
  return out;
}

/** A hash of the analyser: vocabulary + shapes + roots + the patterns
 *  themselves. Counts recorded under one rule set are never compared against
 *  another — the trap `ruff_gate.check_version` documents, where a baseline
 *  taken under mypy 1.19.1 and checked under 1.15.0 named eleven grown classes
 *  and not one was a code change. */
function rulesFingerprint() {
  const blob = [
    VOCAB.measurement_words.slice().sort().join(','),
    VOCAB.measurement_substrings.join(','),
    SHAPES.join(','),
    ROOTS.join(','),
    PATTERNS.map((p) => `${p.shape}:${p.re.source}`).join('|'),
  ].join('||');
  return crypto.createHash('sha256').update(blob).digest('hex').slice(0, 16);
}

module.exports = {
  scanSource, scanAll, countsFrom, rulesFingerprint,
  isMeasurement, words, tail,
  SHAPES, ROOTS, VOCABULARY_FILE,
};
