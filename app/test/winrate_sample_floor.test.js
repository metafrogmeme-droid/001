'use strict';
/**
 * A win rate is a claim about edge, and a sample size decides how much of one.
 *
 * The "What works" panel already got the harder half right — a group with
 * nothing resolved shows a muted dash, not a red 0% — and its own comment
 * records why. What it did not do was tell a RATE from a SAMPLE:
 *
 *     const cls = wr === null ? 'muted' : (wr >= 50 ? 'pos' : 'neg');
 *
 * One resolved trade that won renders `100%`, in green, above a pattern with
 * 47 trades at 61%. `null` was handled; `n = 1` was not. Colour asserts an
 * edge, a bar's LENGTH asserts it again, and neither can be carried by a
 * single sample — so adding bars without a floor would have made the panel
 * less honest than the text rows it replaced.
 *
 * THE FLOOR IS NOT INVENTED. `MIN_RATED = 10` is what
 * `bot/learning/setup_expectancy.py` already uses to decide a setup has been
 * learned at all. Reusing it keeps one number meaning one thing across the
 * learner and the dashboard.
 */

const test = require('node:test');
const assert = require('node:assert');

const WR = require('../public/js/winrate-bar');

const g = (pattern, win_rate, n) => ({ pattern, win_rate, n });

// ── the gap this file exists for ─────────────────────────────────────────

test('a perfect rate on ONE trade is not coloured and gets no bar', () => {
  const c = WR.classify(g('breakout', 100, 1), 'pattern');
  assert.equal(c.rated, false, 'a single sample was ranked');
  assert.match(c.reason, /1 resolved, under 10/);

  const html = WR.buildRows([g('breakout', 100, 1)], 'pattern');
  assert.doesNotMatch(html, /wr-pos/, '100% on n=1 was painted as a win');
  assert.doesNotMatch(html, /wr-fill/, 'a full-width bar was drawn from one trade');
  assert.match(html, /100%/, 'the measurement itself must still be shown');
});

test('the rate is SHOWN below the floor, just never ranked', () => {
  // Hiding a real measurement is its own dishonesty. The number appears; the
  // colour, the bar and the ranking do not.
  const html = WR.buildRows([g('wedge', 66, 3)], 'pattern');
  assert.match(html, /66%/);
  assert.match(html, /×3/);
  assert.match(html, /wr-unrated/);
  assert.match(html, /3 resolved, under 10/);
});

test('at the floor it becomes rated', () => {
  assert.equal(WR.classify(g('x', 60, WR.MIN_RATED - 1), 'pattern').rated, false);
  assert.equal(WR.classify(g('x', 60, WR.MIN_RATED), 'pattern').rated, true);
});

test('a rated group gets colour and a bar proportional to the rate', () => {
  const html = WR.buildRows([g('flag', 61, 47)], 'pattern');
  assert.match(html, /wr-pos/);
  assert.match(html, /wr-fill/);
  assert.match(html, /width:61%/);
});

test('a rated group BELOW 50 is coloured as a loss, not hidden', () => {
  const html = WR.buildRows([g('flag', 32, 47)], 'pattern');
  assert.match(html, /wr-neg/);
  assert.match(html, /32%/);
});

// ── unmeasured is not zero ───────────────────────────────────────────────

test('nothing resolved is a dash, never 0%', () => {
  const c = WR.classify(g('new-pattern', null, 0), 'pattern');
  assert.equal(c.rate, null);
  assert.equal(c.rated, false);
  assert.match(c.reason, /nothing resolved/);

  const html = WR.buildRows([g('new-pattern', null, 0)], 'pattern');
  assert.match(html, /—/);
  assert.doesNotMatch(html, /0%/, 'an unmeasured group was rendered as zero percent');
  assert.doesNotMatch(html, /wr-neg/, 'an unmeasured group was painted as losing');
});

test('a MEASURED zero is a real result and keeps its colour', () => {
  // The other half of the rule. A pattern that lost all 22 times is the most
  // useful row on the board and must not be suppressed for looking like absent.
  const c = WR.classify(g('bad', 0, 22), 'pattern');
  assert.equal(c.rate, 0);
  assert.equal(c.rated, true);
  const html = WR.buildRows([g('bad', 0, 22)], 'pattern');
  assert.match(html, /0%/);
  assert.match(html, /wr-neg/);
});

test('a rate with NO sample count cannot be ranked', () => {
  // The number is real; what it rests on is unknown, and unknown is not ten.
  const c = WR.classify({ pattern: 'x', win_rate: 80 }, 'pattern');
  assert.equal(c.rated, false);
  assert.match(c.reason, /sample size unknown/);
});

test('a non-numeric rate or count is treated as absent, not as a number', () => {
  assert.equal(WR.classify({ pattern: 'x', win_rate: 'high', n: 40 }, 'pattern').rate, null);
  assert.equal(WR.classify({ pattern: 'x', win_rate: 60, n: 'many' }, 'pattern').rated, false);
  assert.equal(WR.classify({ pattern: 'x', win_rate: NaN, n: 40 }, 'pattern').rate, null);
});

// ── ordering is a claim too ──────────────────────────────────────────────

test('rated groups sort ABOVE unrated ones, whatever the percentages', () => {
  // A list ordered by percentage alone puts `100% ×1` at the top — exactly the
  // ranking the floor exists to refuse.
  const html = WR.buildRows([
    g('lucky', 100, 2),
    g('solid', 61, 47),
  ], 'pattern');
  assert.ok(html.indexOf('solid') < html.indexOf('lucky'),
    'a two-sample 100% outranked a 47-sample 61%');
});

test('within the rated band, higher rates come first', () => {
  const html = WR.buildRows([g('lo', 40, 30), g('hi', 70, 30)], 'pattern');
  assert.ok(html.indexOf('hi') < html.indexOf('lo'));
});

test('ratedCount reports how many may honestly be ranked', () => {
  const rows = [g('a', 100, 1), g('b', 61, 47), g('c', null, 0), g('d', 55, 12)];
  assert.equal(WR.ratedCount(rows, 'pattern'), 2);
});

// ── shape and safety ─────────────────────────────────────────────────────

test('an empty or missing group list renders nothing', () => {
  assert.equal(WR.buildRows([], 'pattern'), '');
  assert.equal(WR.buildRows(null, 'pattern'), '');
  assert.equal(WR.buildRows(undefined, 'pattern'), '');
});

test('a missing label renders as (none) rather than blank', () => {
  const html = WR.buildRows([g('', 61, 47)], 'pattern');
  assert.match(html, /\(none\)/);
});

test('labels are escaped', () => {
  const html = WR.buildRows([g('<img src=x onerror=alert(1)>', 61, 47)], 'pattern');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

test('the row count is capped', () => {
  const many = Array.from({ length: 20 }, (_, i) => g('p' + i, 60, 40));
  const html = WR.buildRows(many, 'pattern');
  assert.equal((html.match(/class="wr-row/g) || []).length, WR.MAX_ROWS);
});

test('the bar width is clamped, so a bad rate cannot overflow the track', () => {
  assert.match(WR.buildRows([g('x', 140, 40)], 'pattern'), /width:100%/);
  assert.match(WR.buildRows([g('x', -20, 40)], 'pattern'), /width:0%/);
});

// ── the floor agrees with the learner ────────────────────────────────────

test('MIN_RATED is the same number setup_expectancy already ratified', () => {
  // One number meaning one thing. If the learner's threshold moves, this fails
  // rather than letting the dashboard and the learner disagree about what
  // counts as evidence.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', '..', 'bot', 'learning', 'setup_expectancy.py'), 'utf8');
  // Read the CONSTANT, not the parameter default that references it. The first
  // draft matched `min_samples: int = ...` and found only
  // `min_samples: int = _DEFAULT_MIN_SAMPLES` — a name, not a number — so the
  // test failed while the two values already agreed.
  const m = src.match(/_DEFAULT_MIN_SAMPLES\s*=\s*(\d+)/);
  assert.ok(m, 'could not find _DEFAULT_MIN_SAMPLES in setup_expectancy.py');
  const learner = Number(m[1]);
  assert.equal(WR.MIN_RATED, learner,
    `the panel ranks at ${WR.MIN_RATED} resolved trades and the learner counts a `
    + `setup learned at ${learner} — one of them is wrong about what evidence is`);
});
