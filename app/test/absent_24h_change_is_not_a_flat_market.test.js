'use strict';
/**
 * The shared ticker map handed twelve modules a measured zero.
 *
 *     lib/tickers.js
 *     const price = parseFloat(t.lastPr);
 *     if (!t.symbol || !isFinite(price)) continue;      // ← price IS guarded
 *     map[t.symbol] = {
 *       price,
 *       change: (parseFloat(t.change24h) || 0) * 100,   // ← this is not
 *       ...
 *
 * CLAUDE.md's banned shape at the root of a map that `lib/rwa.js`,
 * `lib/alerts.js`, `lib/research.js`, `lib/token_safety.js` and eight others
 * read — with the inconsistency visible in place, two lines apart.
 *
 * "Flat" is a real reading, so nothing downstream could tell a token that did
 * not move from one the venue said nothing about. Three consequences, each
 * worse than the last:
 *
 * 1. THE RWA SECTOR AVERAGE. A manufactured 0 entered a VOLUME-WEIGHTED mean
 *    as a real flat token. Five tokens up 5%, one unreadable, and the sector
 *    printed 4% — a 20% understatement from a token nobody read. The same
 *    dilution the systemic sentinel was cured of, in a different radar.
 *
 * 2. THE SORT. `round2(undefined)` is NaN, and a comparator that returns NaN
 *    leaves `Array.prototype.sort` in an implementation-defined order — one
 *    unrankable token scrambled the ranking of every other token in its
 *    category.
 *
 * 3. A PRICE ALERT FIRING ON DATA THAT DOES NOT EXIST. `evaluateAlert` guards
 *    with `isFinite(v)` — the GLOBAL one, which coerces, so `isFinite(null)`
 *    is TRUE. With the `|| 0` the value was already 0, so
 *    "notify me when 24h change < 5%" reported a threshold crossing for every
 *    token whose change could not be read. Not a silent miss: a false fire.
 *
 * THE RED HERRING, planted throughout: a token that genuinely did not move.
 * 0.0% is a measurement and must keep its colour, its place in the average and
 * its ability to satisfy an alert. A fix that muted every zero would be the
 * mirror defect.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { buildRadar } = require('../lib/rwa');

const BASES = ['ONDO', 'POLYX', 'RIO', 'CFG', 'TRU', 'MPL', 'GFI', 'MKR',
  'LINK', 'PENDLE', 'BTC'];

/** A ticker map where `unread` names bases whose 24h change is absent. */
function tickers({ change = 5, unread = [], volume = 1e6 } = {}) {
  const map = {};
  for (const b of BASES) {
    map[`${b}USDT`] = {
      price: 10,
      change: unread.includes(b) ? null : change,
      volume,
    };
  }
  return map;
}

const listed = (r) => r.categories.filter((c) => c.listed);

// ── 1. the sector average ───────────────────────────────────────────────────

test('an unreadable token does not enter the sector average as a flat one', () => {
  const all = buildRadar(tickers());
  const one = buildRadar(tickers({ unread: ['ONDO'] }));
  const a = listed(all)[0], b = listed(one)[0];
  assert.ok(a && b, 'setup: a category must be listed');
  assert.strictEqual(a.change_24h_pct, b.change_24h_pct,
    'dropping one token from the READ set changed the average of the rest — '
    + 'the unreadable one is still being counted as 0%');
});

test('a genuinely flat token DOES move the average', () => {
  // THE RED HERRING. 0.0% is a measurement.
  const up = listed(buildRadar(tickers({ change: 5 })))[0];
  const mixed = { ...tickers({ change: 5 }) };
  mixed.ONDOUSDT = { price: 10, change: 0, volume: 1e6 };
  const withFlat = listed(buildRadar(mixed))[0];
  assert.notStrictEqual(up.change_24h_pct, withFlat.change_24h_pct,
    'a real 0% is being treated as unreadable — the mirror defect');
});

test('a category where nothing is readable has no average, not a zero', () => {
  const r = buildRadar(tickers({ unread: BASES }));
  for (const c of listed(r)) {
    assert.strictEqual(c.change_24h_pct, null,
      `${c.key} published a number from no readings`);
  }
});

test('the token cell itself is null, never 0', () => {
  const c = listed(buildRadar(tickers({ unread: ['ONDO'] })))[0];
  const t = c.tokens.find((x) => x.base === 'ONDO');
  if (t) assert.strictEqual(t.change_24h_pct, null);
});

// ── 2. the sort ─────────────────────────────────────────────────────────────

test('an unrankable token sorts last instead of scrambling the ranking', () => {
  // NEGATIVE movers are what make this test bite. `b.change - a.change` on a
  // null coerces to 0, so an unread token lands last ANYWAY when every other
  // move is positive — the first draft of this test passed against the old
  // comparator for that reason. With real decliners present, a null ranked as
  // 0% sorts ABOVE them, in the middle of the table, presented as having
  // outperformed tokens it was never compared to.
  const map = tickers({ unread: ['ONDO'] });
  map.POLYXUSDT = { price: 10, change: -8, volume: 1e6 };
  map.RIOUSDT = { price: 10, change: -3, volume: 1e6 };
  const c = listed(buildRadar(map))[0];
  const idx = c.tokens.findIndex((t) => t.change_24h_pct == null);
  if (idx >= 0) {
    assert.strictEqual(idx, c.tokens.length - 1,
      `a row that cannot be ranked by change is at ${idx} of `
      + `${c.tokens.length} — it is being ranked as 0%, above the decliners`);
  }
});

test('the readable rows are still ranked by change, descending', () => {
  const map = tickers({ unread: ['ONDO'] });
  map.POLYXUSDT = { price: 10, change: 12, volume: 1e6 };
  map.RIOUSDT = { price: 10, change: -3, volume: 1e6 };
  const c = listed(buildRadar(map))[0];
  const read = c.tokens.filter((t) => t.change_24h_pct != null)
    .map((t) => t.change_24h_pct);
  assert.deepStrictEqual(read, [...read].sort((a, b) => b - a),
    `readable rows are out of order: ${read}`);
});

test('the order is deterministic when several rows are unrankable', () => {
  const a = buildRadar(tickers({ unread: ['ONDO', 'POLYX', 'RIO'] }));
  const b = buildRadar(tickers({ unread: ['ONDO', 'POLYX', 'RIO'] }));
  assert.deepStrictEqual(listed(a)[0].tokens.map((t) => t.base),
    listed(b)[0].tokens.map((t) => t.base));
});

// ── 3. the alert ────────────────────────────────────────────────────────────

test('an alert cannot fire on a change that arrived as an empty string', () => {
  // THE GUARD THAT IS ACTUALLY LOAD-BEARING. For `null` the function returns
  // `hit ? v : null` and `v` IS null, so it answers null either way and the
  // guard cannot be observed. `''` is different: the global `isFinite('')` is
  // TRUE (Number('') is 0 AND finite), `'' < 5` is TRUE, and the function
  // returned the empty string as a threshold crossing. An empty string is
  // what several serialisers emit for SQL NULL — CLAUDE.md records this exact
  // trap, and it is why the guard has to be `Number.isFinite`.
  const { evaluateAlert } = require('../lib/alerts');
  for (const bad of ['', '   ', null, undefined, NaN]) {
    const tk = { price: 10, change: bad, volume: 1e6 };
    assert.strictEqual(
      evaluateAlert({ metric: 'change_24h', op: '<', threshold: 5 }, tk), null,
      `change=${JSON.stringify(bad)} reported a threshold crossing`);
  }
});

test('an alert cannot fire on a 24h change that was never read', () => {
  const { evaluateAlert } = require('../lib/alerts');
  const unread = { price: 10, change: null, volume: 1e6 };
  for (const metric of ['change_24h', 'change_abs_24h']) {
    assert.strictEqual(
      evaluateAlert({ metric, op: '<', threshold: 5 }, unread), null,
      `${metric} reported a threshold crossing from an unreadable value`);
    assert.strictEqual(
      evaluateAlert({ metric, op: '>', threshold: -5 }, unread), null,
      `${metric} reported a threshold crossing from an unreadable value`);
  }
});

test('an alert still fires on a genuinely flat market', () => {
  // THE RED HERRING again: 0% is a real reading and can cross a threshold.
  const { evaluateAlert } = require('../lib/alerts');
  const flat = { price: 10, change: 0, volume: 1e6 };
  assert.strictEqual(
    evaluateAlert({ metric: 'change_24h', op: '<', threshold: 5 }, flat), 0);
  assert.strictEqual(
    evaluateAlert({ metric: 'change_abs_24h', op: '<', threshold: 5 }, flat), 0);
});

test('a real move still fires', () => {
  const { evaluateAlert } = require('../lib/alerts');
  const up = { price: 10, change: 7.5, volume: 1e6 };
  assert.strictEqual(
    evaluateAlert({ metric: 'change_24h', op: '>', threshold: 5 }, up), 7.5);
  assert.strictEqual(
    evaluateAlert({ metric: 'change_24h', op: '>', threshold: 9 }, up), null);
});

test('a price alert is untouched by any of this', () => {
  const { evaluateAlert } = require('../lib/alerts');
  const tk = { price: 10, change: null, volume: 1e6 };
  assert.strictEqual(
    evaluateAlert({ metric: 'price', op: '>', threshold: 5 }, tk), 10,
    'the price path was collateral damage — price is separately guarded');
});

// ── the root, and the surfaces it feeds ─────────────────────────────────────

/** Strip comments before scanning — the rule this file's author broke first. */
const codeOnly = (t) => t
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n').map((l) => l.replace(/(^|\s)\/\/.*$/, '')).join('\n');

test('the ticker map no longer manufactures a zero change', () => {
  // STRIPPED, because the replacement comment QUOTES the expression it
  // replaced and the first draft of this test failed on its own prose. That
  // is CLAUDE.md's "strip comments first" firing for the sixth time in this
  // sweep — the note recording it was written hours before this.
  const src = codeOnly(
    fs.readFileSync(path.join(__dirname, '..', 'lib', 'tickers.js'), 'utf8'));
  assert.ok(!/change:\s*\(parseFloat\([^)]*\)\s*\|\|\s*0\)/.test(src),
    'the `|| 0` is back at the root of the shared ticker map');
  assert.match(src, /Number\.isFinite\(chg\)/);
});

test('vs-BTC is not computed against a BTC nobody read', () => {
  // `sectorChange - null` is `sectorChange` — a sector would have been
  // reported as beating a BTC that was never read.
  const map = tickers();
  map.BTCUSDT = { price: 10, change: null, volume: 1e6 };
  const r = buildRadar(map);
  assert.strictEqual(r.sector.vs_btc_pct, null);
});

test('the research card does not paint an unread change green', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'research.js'), 'utf8');
  const i = src.indexOf("title: 'Market read'");
  assert.ok(i > 0, 'the Market read section moved');
  const window = src.slice(i, i + 600);
  assert.match(window, /tk\.change == null/,
    'the research card still colours a change it may not have');
});

test('the dashboard RWA cell routes through the guarded helper', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.ok(!/\$\{t\.change_24h_pct >= 0 \? 'up' : 'down'\}/.test(src),
    'the cell is colouring a value it prints as an em-dash again');
  assert.match(src, /moveClass\(t\.change_24h_pct\)/);
});
