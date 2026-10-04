'use strict';
/**
 * The word "survives" on a setup cell.
 *
 * It appears only when the cell was pre-registered and that registration
 * was replicated on prospective data. A q-value under 0.05 and an interval
 * clear of zero do not say it. A planted word does not say it. The records
 * in the tree (eligibility grants, the POC-retest replay, the absent
 * hypothesis registry) name no such cell, so none reads survives.
 *
 * A missing cell is omitted. An unreadable prospective bound stays null.
 * A measured 0, on the book or on the prospective bound, stays 0 and does
 * not clear the gate.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { computeAnalytics } = require('../lib/signal_analytics');
const { loadCellRegistrations } = require('../lib/cell_registrations');
const { publicAnalytics, dollarKeys } = require('../lib/public_signal');
const { codeOnly } = require('./helpers/code_only');
const WR = require('../public/js/winrate-bar');

const REPO = path.join(__dirname, '..', '..');

function cell(over = {}) {
  return {
    pnl: 1,
    signal_type: 'vwap_reversion',
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
    pattern: 'breakout',
    symbol: 'BTC/USDT',
    confidence: 0.7,
    ...over,
  };
}

/** n rows whose mean and standard error realise a two-sided z. */
function rowsAtZ(n, mean, z) {
  const se = Math.abs(mean) / z;
  const variance = se * se * n;
  const d = Math.sqrt(variance * (n - 1) / n);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const pnl = i < n / 2 ? mean + d : mean - d;
    rows.push(cell({ pnl }));
  }
  return rows;
}

function registration(over = {}) {
  return {
    setup: 'vwap_reversion',
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
    preregistered: true,
    prospective_lo: 0.4,
    prospective_hi: 1.2,
    ...over,
  };
}

test('the records on disk register no cell, so a strong one does not read survives', () => {
  const regs = loadCellRegistrations(REPO);
  assert.deepEqual(regs, []);

  const book = computeAnalytics(rowsAtZ(40, 1, 2.58), { registrations: regs });
  assert.equal(book.by_setup.length, 1);
  const g = book.by_setup[0];
  assert.equal(g.setup, 'vwap_reversion');
  assert.ok(g.q_value != null && g.q_value < 0.05, `q was ${g.q_value}`);
  assert.ok(g.mean_r_lo > 0, `interval lo was ${g.mean_r_lo}`);
  assert.equal(g.reading, 'exploratory');
  assert.equal('registrations' in g, false);

  const pub = publicAnalytics(book);
  assert.equal(JSON.stringify(pub).includes('survives'), false);
  assert.deepEqual(dollarKeys(pub), []);

  const html = WR.setupScoreboard(pub.by_setup);
  assert.match(html, /exploratory/);
  assert.equal(html.includes('survives'), false);
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-neg'), false);
  assert.equal(html.includes('wr-fill'), false);

  // A word planted on the signal row is not a registration.
  const planted = computeAnalytics([
    cell({ preregistered: true, prospective_lo: 1, prospective_hi: 2, reading: 'survives' }),
  ]);
  assert.equal(planted.by_setup[0].reading, 'exploratory');
  assert.equal('registrations' in planted.by_setup[0], false);
});

test('a pre-registered cell that was not replicated prospectively does not read survives', () => {
  const book = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ prospective_lo: -0.2, prospective_hi: 0.3 })],
  });
  const g = book.by_setup[0];
  assert.ok(g.mean_r_lo > 0);
  assert.ok(g.q_value < 0.05);
  assert.equal(g.reading, 'exploratory');
  assert.equal(g.registrations.length, 1);
  assert.equal(g.registrations[0].prospective_lo, -0.2);
  assert.equal(g.registrations[0].prospective_hi, 0.3);
  const html = WR.setupScoreboard([g]);
  assert.match(html, /exploratory/);
  assert.equal(html.includes('survives'), false);
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-fill'), false);

  // The prospective bound touching zero is a measurement, and it does not clear.
  const touches = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ prospective_lo: 0, prospective_hi: 1.2 })],
  });
  assert.equal(touches.by_setup[0].registrations[0].prospective_lo, 0);
  assert.equal(touches.by_setup[0].reading, 'exploratory');
  assert.equal(WR.setupScoreboard(touches.by_setup).includes('survives'), false);

  // No prospective window. Null is not a zero, and it is not a replication.
  const unmeasured = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ prospective_lo: null, prospective_hi: null })],
  });
  assert.equal(unmeasured.by_setup[0].registrations[0].prospective_lo, null);
  assert.equal(unmeasured.by_setup[0].registrations[0].prospective_hi, null);
  assert.equal(unmeasured.by_setup[0].reading, 'exploratory');

  // An unreadable bound is not published as 0.
  const unread = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ prospective_lo: 'n/a', prospective_hi: 'n/a' })],
  });
  assert.equal(unread.by_setup[0].registrations[0].prospective_lo, null);
  assert.equal(unread.by_setup[0].reading, 'exploratory');
  assert.equal(JSON.stringify(unread.by_setup[0].registrations).includes('n/a'), false);

  // Registered for a different cell. This one was not.
  const other = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ timeframe: '4h' })],
  });
  assert.equal(other.by_setup[0].reading, 'exploratory');
  assert.equal('registrations' in other.by_setup[0], false);

  // The flag is exactly true. A string, and a registration that was not
  // pre-registered, do not promote a clear prospective interval.
  const flag = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ preregistered: 'true' })],
  });
  assert.equal(flag.by_setup[0].reading, 'exploratory');
  const notRegistered = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ preregistered: false })],
  });
  assert.equal(notRegistered.by_setup[0].reading, 'exploratory');

  // One replicated record and one that did not are two answers.
  const split = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [
      registration(),
      registration({ prospective_lo: -0.2, prospective_hi: 0.3 }),
    ],
  });
  assert.equal(split.by_setup[0].reading, 'exploratory');
  assert.equal(WR.setupScoreboard(split.by_setup).includes('survives'), false);

  // A measured flat book stays 0. The failed registration does not relabel it.
  const flat = computeAnalytics([
    cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }),
  ], {
    registrations: [registration({ prospective_lo: -0.2, prospective_hi: 0.3 })],
  });
  assert.equal(flat.by_setup[0].mean_r, 0);
  assert.equal(flat.by_setup[0].reading, 'exploratory');
  const flatHtml = WR.setupScoreboard(flat.by_setup);
  assert.match(flatHtml, /0R/);
  assert.match(flatHtml, /exploratory/);
  assert.equal(flatHtml.includes('survives'), false);
});

test('a cell that was pre-registered and replicated prospectively reads survives', () => {
  const book = computeAnalytics(rowsAtZ(40, 1, 2.58), {
    registrations: [registration({ fee_usd: 12, net_pnl: 99 })],
  });
  const g = book.by_setup[0];
  assert.equal(g.reading, 'survives');
  assert.equal(g.registrations.length, 1);
  assert.equal(g.registrations[0].prospective_lo, 0.4);
  assert.equal(g.registrations[0].prospective_hi, 1.2);
  assert.equal('fee_usd' in g.registrations[0], false);
  assert.equal('net_pnl' in g.registrations[0], false);
  const pub = publicAnalytics(book);
  assert.equal(pub.by_setup[0].reading, 'survives');
  assert.deepEqual(dollarKeys(pub), []);

  const html = WR.setupScoreboard(pub.by_setup);
  assert.match(html, /survives/);
  assert.equal(html.includes('exploratory'), false);
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-neg'), false);
  assert.equal(html.includes('wr-fill'), false);
  assert.equal(/<button/i.test(html), false);
  assert.equal(/\bfollow\b/.test(html), false);
  assert.match(html, /interval /);

  // The same numbers, with the word planted and no registration, do not.
  const planted = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG',
    n: 40, win_rate: 80, mean_r: 1.5,
    hit_rate: 0.8, wilson_lo: 0.65, wilson_hi: 0.9,
    mean_r_lo: 0.4, mean_r_hi: 2.6, q_value: 0.01,
    reading: 'survives',
  }]);
  assert.match(planted, /exploratory/);
  assert.equal(planted.includes('survives'), false);
  assert.equal(planted.includes('wr-pos'), false);
});

test('an eligibility grant and a replay verdict are not a setup cell', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cell-reg-'));
  const elig = path.join(root, 'benchmark', 'eligibility');
  const pocDir = path.join(root, 'benchmark', 'poc_retest');
  const hyp = path.join(root, 'benchmark', 'hypotheses');
  fs.mkdirSync(elig, { recursive: true });
  fs.mkdirSync(pocDir, { recursive: true });
  fs.mkdirSync(hyp, { recursive: true });
  fs.writeFileSync(path.join(elig, 'abc.json'), JSON.stringify({
    schema: 1, strategy_hash: 'abc', verdict: 'survives', stage: 'minimum',
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG', preregistered: true,
    prospective_lo: 0.4, prospective_hi: 1.2,
  }));
  fs.writeFileSync(path.join(pocDir, 'result.json'), JSON.stringify({
    kind: 'poc_retest_replay',
    windows: [{ label: 'later', verdict: 'survives', mean_r: 2.04, interval: [1.59, 2.5] }],
  }));
  fs.writeFileSync(path.join(hyp, 'vwap.yaml'), 'verdict: survives\npreregistered: true\n');
  assert.deepEqual(loadCellRegistrations(root), []);

  // Unreadable is no registration, and it does not throw.
  fs.writeFileSync(path.join(pocDir, 'result.json'), '{');
  fs.writeFileSync(path.join(elig, 'abc.json'), '{');
  assert.deepEqual(loadCellRegistrations(root), []);
  assert.deepEqual(loadCellRegistrations(path.join(root, 'missing')), []);
});

test('a missing cell is omitted and not labelled survives', () => {
  assert.equal(WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: null,
    source: 'rules', direction: 'LONG',
    n: 40, win_rate: 80, mean_r: 1,
    registrations: [registration()],
  }]), '');
  const empty = computeAnalytics([cell({ pnl: null })]);
  assert.deepEqual(empty.by_setup, []);
});

test('the analytics route asks the registration loader', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'signals.js'), 'utf8'));
  const a = src.indexOf("router.get('/analytics'");
  const b = src.indexOf('signal_analytics_unavailable', a);
  assert.ok(a !== -1 && b > a, 'the analytics handler moved');
  const q = src.slice(a, b);
  assert.ok(q.includes('loadCellRegistrations('), 'the route does not read registrations');
  assert.ok(q.includes('registrations:'), 'computeAnalytics is not given them');
});
