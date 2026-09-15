/**
 * THE EQUITY THEATRE, DRIVEN.
 *
 * The finding this suite exists for is a vocabulary one: SEVEN quantities on
 * this site are called "drawdown" and five were labelled in the same words,
 * from five endpoints, about three different books. `DD_KINDS` is the one
 * table, and `kind()` RAISES on a name it does not hold — a drawdown figure
 * printed with no book named is the defect, so there is no quiet fallback.
 *
 * The rest is the three-state discipline: a failed read, a venue-empty
 * history and a real curve are three facts, and the old code could not tell
 * the first two apart (the underwater chart simply stayed hidden).
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const M = require(path.join(__dirname, '..', 'public', 'js', 'equity-theatre-model.js'));

const snap = (v) => ({ equity: v, snapshot_at: '2026-09-15T00:00:00Z' });
const payload = (vals, ce) => ({ snapshots: vals.map(snap), capital_events: ce });

test('every drawdown on the site has a kind, and each names a different book', () => {
  const names = Object.keys(M.DD_KINDS);
  // One row per quantity the site renders. Five of them were labelled
  // identically before this table existed.
  assert.deepEqual(names.sort(), [
    'agentRecord', 'agentReputation', 'backtestLab', 'copyLeader', 'engineGate',
    'replayWhatIf', 'yourClosed', 'yourEquity',
  ]);
  for (const n of names) {
    const k = M.kind(n);
    assert.ok(k.short.en.length > 0, n + ' has no qualifier');
    assert.ok(k.long.en.length > 20, n + ' has no sentence');
    assert.ok(k.short.key.indexOf('dd.et_k_') === 0);
  }
  // The three books are told apart in words a reader can act on.
  assert.match(M.kind('yourEquity').long.en, /not the limit the engine enforces/i);
  assert.match(M.kind('engineGate').long.en, /different quantity/i);
  assert.match(M.kind('yourClosed').long.en, /does not include open positions/i);
  assert.match(M.kind('replayWhatIf').long.en, /nothing here was traded/i);
  assert.match(M.kind('backtestLab').long.en, /rules you configured/i);
});

test('an unknown kind RAISES — a figure with no book named is the defect', () => {
  assert.throws(() => M.kind('maxDrawdown'), /no such drawdown kind/);
  assert.throws(() => M.kind(''), /no such drawdown kind/);
  assert.throws(() => M.kind(undefined), /no such drawdown kind/);
});

test('a failed read, an empty history and a thin one are three facts', () => {
  const failed = M.theatre(null);
  assert.equal(failed.state, 'unread');
  assert.match(failed.word.en, /could not be read/);

  // An older server that sends no `snapshots` array is ALSO unread — "nothing
  // recorded yet" is a claim about the account, not about the payload shape.
  const older = M.theatre({ capital_events: 0 });
  assert.equal(older.state, 'unread');

  const empty = M.theatre(payload([], 0));
  assert.equal(empty.state, 'none');
  assert.match(empty.word.en, /No equity snapshots/);

  const thin = M.theatre(payload([1000], 0));
  assert.equal(thin.state, 'thin');

  const states = [failed, older, empty, thin].map((r) => r.state);
  assert.equal(new Set(states).size, 3, 'unread / none / thin are distinct');
  for (const r of [failed, older, empty, thin]) assert.equal(r.uw, null);
});

test('a flat record and an unreadable one are NOT the same answer', () => {
  // The old caption said "No meaningful drawdown yet" for both.
  const flat = M.theatre(payload([1000, 1000, 1000], 0));
  assert.equal(flat.state, 'read');
  assert.equal(flat.uw.deepest, 0, 'a real record that never fell is a measurement');

  const unread = M.theatre(null);
  assert.equal(unread.uw, null, 'and nothing measured is not a zero');
});

test('the underwater series is percent below the RUNNING peak', () => {
  const uw = M.underwater([1000, 1200, 900, 1100]);
  assert.deepEqual(uw.series.map((v) => Math.round(v * 100) / 100), [0, 0, -25, -8.33]);
  assert.equal(uw.deepest, -25);
  assert.equal(uw.points.length, 4);
});

test('junk rows are dropped and the count says how many survived', () => {
  const r = M.theatre(payload([1000, 'x', 1200, null, 900], 2));
  assert.equal(r.state, 'read');
  assert.equal(r.sample.answered, 5);
  assert.equal(r.sample.parsed, 3);
  assert.equal(r.sample.dropped, 2);
  assert.equal(r.curve.length, 3);
});

test('ONE predicate decides a reading, so the count and the chart agree', () => {
  // The footnote says "{n} snapshots" and the chart draws `uw.points`. When
  // the row filter and the series filter were two predicates, a non-positive
  // equity was counted as parsed and dropped from the chart: a footnote
  // claiming more than the picture holds.
  const r = M.theatre(payload([1000, 0, 1200, -5, 'x'], 0));
  assert.equal(r.sample.parsed, 2, 'a non-positive equity is not a reading here');
  assert.equal(r.sample.dropped, 3);
  assert.equal(r.uw.points.length, r.sample.parsed,
    'the chart draws exactly what the footnote counted');
  // and the reason it is not a reading: the series divides by the peak
  assert.equal(M.usable(0), false);
  assert.equal(M.usable(-1), false);
  assert.equal(M.usable('1200.5'), true, 'a numeric string is still a reading');
});

test('a series too thin to measure has NO deepest — not a zero', () => {
  // Reachable only through `underwater` directly (theatre returns `thin`
  // first), and the model states the contract for both: 0 is a real series
  // that never fell, null is nothing measured.
  for (const thin of [[], [1000], ['x', 'y'], [0, -1]]) {
    const uw = M.underwater(thin);
    assert.equal(uw.deepest, null, JSON.stringify(thin) + ' measured a depth');
    assert.deepEqual(uw.series, []);
  }
  assert.equal(M.underwater([1000, 1000]).deepest, 0, 'and a real flat pair is 0');
});

test('a string equity is a real reading; a non-numeric one is not', () => {
  assert.equal(M.parseNum('1234.5'), 1234.5);
  assert.equal(M.parseNum(1234.5), 1234.5);
  for (const bad of ['', '  ', 'abc', null, undefined, NaN, Infinity, {}, []]) {
    assert.equal(M.parseNum(bad), null, String(bad) + ' is not a reading');
  }
});

test('the footnote states the sample and prints nothing it did not measure', () => {
  const clean = M.footnote(M.theatre(payload([1000, 1200, 900], 0)));
  assert.deepEqual(clean.map((f) => f.word.key), ['dd.et_snaps', 'dd.et_deepest']);
  assert.equal(clean[0].n, 3);

  // A "0 segments dropped" row trains the reader to stop reading the line.
  const withSegments = M.footnote(M.theatre(payload([1000, 1200, 900], 2)));
  assert.deepEqual(withSegments.map((f) => f.word.key),
    ['dd.et_snaps', 'dd.et_deepest', 'dd.et_segments']);

  const flat = M.footnote(M.theatre(payload([1000, 1000], 0)));
  assert.deepEqual(flat.map((f) => f.word.key), ['dd.et_snaps', 'dd.et_flat']);

  // Nothing to footnote about a read that did not happen — and `none` and
  // `thin` are not reads either. Each already says its own sentence; a
  // "0 snapshots" line under "No equity snapshots recorded yet" is the same
  // fact twice, and "1 snapshots" under "a curve needs at least two" reads
  // as a sample the chart drew from.
  assert.deepEqual(M.footnote(M.theatre(null)), []);
  assert.deepEqual(M.footnote(M.theatre({ capital_events: 0 })), []);
  assert.deepEqual(M.footnote(M.theatre(payload([], 0))), [], 'none is not a read');
  assert.deepEqual(M.footnote(M.theatre(payload([1000], 0))), [], 'thin is not a read');
  assert.deepEqual(M.footnote(null), []);
});

test('the equity read never claims to be the limit the engine enforces', () => {
  const r = M.theatre(payload([1000, 1200, 900], 0));
  assert.equal(r.kind, M.DD_KINDS.yourEquity);
  assert.match(M.W.notTheGate.en, /separate reading/i);
});

test('sample() refuses counts it cannot use, and floors a junk segment count', () => {
  assert.equal(M.sample(null, 3), null);
  assert.equal(M.sample('10', 3), null);
  assert.equal(M.sample(-1, 0), null);
  assert.equal(M.sample(10, 8, -5).segmentsDropped, 0);
  assert.equal(M.sample(10, 8, 2.7).segmentsDropped, 2);
  assert.equal(M.sample(10, 8, null).segmentsDropped, 0);
});

test('num() reads numbers only — a numeric string is junk to it', () => {
  assert.equal(M.num(0), 0, 'a measured zero is a reading');
  assert.equal(M.num(-4.2), -4.2);
  for (const bad of ['4', null, undefined, NaN, Infinity, -Infinity]) {
    assert.equal(M.num(bad), null);
  }
});

test('every word this model emits is a dd.et_ key, and KEYS lists them all', () => {
  const emitted = new Set();
  for (const p of [null, {}, payload([], 0), payload([1000], 0), payload([1000, 900], 0)]) {
    const r = M.theatre(p);
    if (r.word) emitted.add(r.word.key);
    for (const f of M.footnote(r)) emitted.add(f.word.key);
    emitted.add(r.kind.short.key);
    emitted.add(r.kind.long.key);
  }
  emitted.add(M.W.notTheGate.key);
  for (const k of emitted) {
    assert.ok(k.indexOf('dd.et_') === 0, k + ' is not this model\'s to spell');
    assert.ok(M.KEYS.includes(k), k + ' is emitted but missing from KEYS');
  }
  // Every kind's pair is in KEYS too, including the ones only a call site emits.
  for (const n of Object.keys(M.DD_KINDS)) {
    assert.ok(M.KEYS.includes(M.DD_KINDS[n].short.key));
    assert.ok(M.KEYS.includes(M.DD_KINDS[n].long.key));
  }
});

test('every KEYS entry is defined in all fourteen languages', () => {
  // Read the dictionary STRICTLY: translate() falls back to English, so a key
  // present in one language answers English for the other thirteen.
  const src = require('node:fs').readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  const LANGS = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'ar', 'de', 'nl', 'ja', 'ko', 'ru', 'tr'];
  const lines = src.split('\n');
  for (const k of M.KEYS) {
    const line = lines.find((l) => l.trim().startsWith(`'${k}':`));
    assert.ok(line, `${k} is not in the dictionary at all`);
    for (const lang of LANGS) {
      assert.ok(new RegExp(`\\b${lang}: '`).test(line), `${k} has no ${lang}`);
    }
  }
});
