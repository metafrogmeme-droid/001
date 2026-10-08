'use strict';
/**
 * Every surface that prints a scorecard's figures reads them the way the
 * agent card does (`AgentScorecard.readings`).
 *
 * Three surfaces kept their own reading after #482:
 * - The compare page painted max drawdown with the return's rule, so ETH MA
 *   trend's 15.86% drawdown showed as a green "+15.86%".
 * - Reproduce in Lab painted a profit factor with `(pf || 1) - 1`, so a
 *   measured 0 read as missing and showed break-even green. It painted every
 *   drawdown red, and it escaped the drawdown caption's markup a second time,
 *   so the tag itself was printed under the figure.
 * - The public track-record tile classed `(pf || 1) >= 1` as a gain, with
 *   `up`/`down` classes nothing on the dashboard styles.
 *
 * The agent card's caption named "this leader's record" over a frozen
 * backtest, under all six figures without saying which one it qualified. The
 * kind is `agentBacktest` now; `copyLeader` had no other reader.
 *
 * Driven: each renderer is cut out by its own markers and run, and the
 * frozen ETH MA trend and Safe Scalper cards (profit factor 0) are the
 * fixture.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');

const APP = path.join(__dirname, '..');
const REPO = path.join(APP, '..');
const DASH = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
const CMP = fs.readFileSync(path.join(APP, 'public', 'compare.html'), 'utf8');
const Score = require(path.join(APP, 'public', 'js', 'agent-scorecard.js'));
const Theatre = require(path.join(APP, 'public', 'js', 'equity-theatre-model.js'));

function between(src, a, b) {
  const i = src.indexOf(a);
  const j = src.indexOf(b, i + 1);
  assert.ok(i > 0 && j > i, `markers moved: ${a}`);
  assert.equal(src.indexOf(a, i + 1), -1, `marker twice: ${a}`);
  return src.slice(i, j);
}

function loadPnlClass() {
  const src = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
  const ctx = { Number, isFinite, String };
  vm.runInNewContext(between(src, '  function pnlClass(n) {', '  function fmtAgo(iso) {')
    + '\nglobalThis.pnlClass = pnlClass;', ctx);
  return ctx.pnlClass;
}
const pnlClass = loadPnlClass();

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[c]));
}

// The shape dashboard.js's ddLabel returns: the kind's words, escaped, in a
// muted span. A caption printed as text would show this tag.
function ddLabel(kind) {
  return `<span class="muted small">${esc(Theatre.kind(kind).short.en)}</span>`;
}

function card(file) {
  return JSON.parse(execFileSync('git', ['show', `HEAD:benchmark/scorecards/${file}`], {
    cwd: REPO, encoding: 'utf8',
  }));
}

const ETH_MA = card('eth-ma-trend.json');
const SCALPER = card('safe-scalper.json');

function neutral(cls, what) {
  assert.doesNotMatch(cls, /\b(pos|neg|up|down)\b/, `${what} carries a verdict colour: "${cls}"`);
}

// ── the compare page ────────────────────────────────────────────────────────

function compareMetric(withPainter = true) {
  const cellNum = between(CMP, '  var cellNum = function', '  // The two catalogues');
  const body = between(CMP, '      // ── compare metric cell ──', '      // ── compare metric cell end ──');
  const ctx = { window: withPainter ? { AgentScorecard: Score } : {}, esc, String, Number, isFinite };
  vm.runInNewContext(cellNum + body + '\nglobalThis.metric = metric;', ctx);
  return (metrics, key) => {
    const html = ctx.metric({ engine: { scorecard: { metrics } } }, key);
    const m = /<span class="(?:num|dim) ?([^"]*)">([^<]*)<\/span>/.exec(html);
    assert.ok(m, html);
    return { cls: m[1].trim(), text: m[2], dim: html.includes('class="dim"') };
  };
}

test('compare: a drawdown is a magnitude, never a green "+"', () => {
  const metric = compareMetric();
  const dd = metric(ETH_MA.metrics, 'max_drawdown_pct');
  assert.equal(dd.text, '15.86%');
  neutral(dd.cls, 'compare drawdown');
  const ret = metric(ETH_MA.metrics, 'total_return_pct');
  assert.equal(ret.text, '-2.50%');
  assert.equal(ret.cls, 'neg');
  // The other arm: a gain is still a gain.
  const up = metric({ total_return_pct: 3.24 }, 'total_return_pct');
  assert.equal(up.text, '+3.24%');
  assert.equal(up.cls, 'pos');
});

test('compare: a measured profit factor of 0 is a loss, 1.62 a gain', () => {
  const metric = compareMetric();
  const zero = metric(SCALPER.metrics, 'profit_factor');
  assert.equal(zero.text, '0.00');
  assert.equal(zero.cls, 'neg');
  const gain = metric({ profit_factor: 1.62 }, 'profit_factor');
  assert.equal(gain.cls, 'pos');
  assert.equal(metric(SCALPER.metrics, 'win_rate').text, '0%');
});

test('compare: an unread figure is the page dash, and so is a missing painter', () => {
  const metric = compareMetric();
  for (const key of ['total_return_pct', 'profit_factor', 'win_rate', 'max_drawdown_pct']) {
    const c = metric({ total_return_pct: null, profit_factor: '', max_drawdown_pct: NaN }, key);
    assert.equal(c.text, '—', key);
    assert.ok(c.dim, key + ' is not the page dash');
  }
  const bare = compareMetric(false)(ETH_MA.metrics, 'max_drawdown_pct');
  assert.equal(bare.text, '—');
});

// ── Reproduce in Lab ────────────────────────────────────────────────────────

function labTiles(withPainter = true) {
  const body = between(DASH, '  // ── lab result tiles ──', '  // ── lab result tiles end ──');
  const ctx = { window: withPainter ? { AgentScorecard: Score } : {}, pnlClass, esc, ddLabel, String };
  vm.runInNewContext(body + '\nglobalThis.labTiles = labTiles;', ctx);
  const usd = (v) => (v == null ? '—' : `$${v}`);
  return (res) => ctx.labTiles(res, usd(res.net_pnl));
}

function labTile(html, label) {
  const re = new RegExp(`<div class="v num ([^"]*)" data-lab-metric="${label}">([^<]*)</div>`);
  const m = re.exec(html);
  assert.ok(m, `${label} missing in ${html}`);
  return { cls: m[1].trim(), text: m[2] };
}

test('Lab: profit factor 0 is a loss and a drawdown takes no colour', () => {
  const html = labTiles()({ ...ETH_MA.metrics, net_pnl: -25 });
  const pf = labTile(html, 'Profit factor');
  assert.equal(pf.text, '0.00');
  assert.equal(pf.cls, 'neg');
  const dd = labTile(html, 'Max drawdown');
  assert.equal(dd.text, '15.86%');
  neutral(dd.cls, 'Lab drawdown');
  assert.equal(labTile(html, 'Return').cls, 'neg');
  assert.equal(labTile(html, 'Net PnL').text, '$-25');
  // The other arm.
  const good = labTiles()({ profit_factor: 1.62, total_return_pct: 3.24, max_drawdown_pct: 2.75 });
  assert.equal(labTile(good, 'Profit factor').cls, 'pos');
  neutral(labTile(good, 'Max drawdown').cls, 'Lab drawdown');
});

test('Lab: the drawdown caption is markup, not the text of a tag', () => {
  const html = labTiles()(ETH_MA.metrics);
  assert.match(html, /<div class="d"><span class="muted small">this backtest<\/span><\/div>/);
  assert.doesNotMatch(html, /&lt;span/);
});

test('Lab: the card and the Lab paint the same run the same way', () => {
  const html = labTiles()(SCALPER.metrics);
  const read = {};
  Score.readings(SCALPER.metrics, pnlClass).cells.forEach((c) => { read[c.key] = c; });
  for (const [label, key] of [['Return', 'return'], ['Profit factor', 'profit-factor'],
    ['Win rate', 'win-rate'], ['Max drawdown', 'max-dd'], ['Sharpe', 'sharpe'], ['Trades', 'trades']]) {
    const t = labTile(html, label);
    assert.equal(t.text, read[key].text, label);
    assert.equal(t.cls, read[key].cls, label);
  }
});

test('Lab: a missing painter is said, never a row of dashes', () => {
  assert.equal(labTiles(false)(ETH_MA.metrics), null);
  assert.match(DASH, /The result could not be painted\. The run finished; this is not an empty result\./);
});

// ── the public track-record tile ────────────────────────────────────────────

function trackTiles(withPainter = true) {
  const body = between(DASH, '  // ── public record tiles ──', '  // ── public record tiles end ──');
  const ctx = { window: withPainter ? { AgentScorecard: Score } : {}, pnlClass, esc, ddLabel, String };
  vm.runInNewContext(body + '\nglobalThis.prevTrackTiles = prevTrackTiles;', ctx);
  return ctx.prevTrackTiles;
}

function trackTile(html, label) {
  const re = new RegExp(`<div class="k">${label}</div><div class="v num ([^"]*)">([^<]*)</div>`);
  const m = re.exec(html);
  assert.ok(m, `${label} missing in ${html}`);
  return { cls: m[1].trim(), text: m[2] };
}

test('track record: a measured profit factor of 0 is a loss; the drawdown is uncoloured', () => {
  const tiles = trackTiles();
  const zero = tiles({ profit_factor: 0, win_rate_pct: 0, trades: 3, max_drawdown_pct: 4.2 });
  assert.equal(trackTile(zero, 'Profit factor').text, '0.00');
  assert.equal(trackTile(zero, 'Profit factor').cls, 'neg');
  neutral(trackTile(zero, 'Max drawdown').cls, 'track drawdown');
  assert.equal(trackTile(zero, 'Max drawdown').text, '4.2%');
  const gain = tiles({ profit_factor: 1.4, win_rate_pct: 55, trades: 40, max_drawdown_pct: 3 });
  assert.equal(trackTile(gain, 'Profit factor').cls, 'pos');
  // No class the dashboard does not style.
  assert.doesNotMatch(zero + gain, /class="v num (?:up|down)"/);
});

test('track record: an unread profit factor is a dash with no colour, painter or not', () => {
  for (const painter of [true, false]) {
    const html = trackTiles(painter)({ profit_factor: null, trades: 0 });
    assert.equal(trackTile(html, 'Profit factor').text, '—');
    neutral(trackTile(html, 'Profit factor').cls, 'unread profit factor');
  }
  assert.equal(trackTile(trackTiles(false)({ profit_factor: 0.5 }), 'Profit factor').text, '0.50');
});

// ── the agent card's caption ────────────────────────────────────────────────

test("agent card: the drawdown caption names the backtest and the figure it qualifies", () => {
  const body = between(DASH, '  // ── agent scorecard: six readings, one grid', '  // ── agent scorecard: renderer end');
  const ctx = { window: { AgentScorecard: Score }, pnlClass, esc, ddLabel, String, Number, isFinite };
  vm.runInNewContext(body + '\nglobalThis.scoreBlock = scoreBlock;', ctx);
  const html = ctx.scoreBlock({ dataset: 'majors_1h', bars: 1500, metrics: ETH_MA.metrics });
  const cap = between(html, '<div class="agent-metrics-caption"', '</div>');
  assert.match(cap, /Max DD: <\/span><span class="muted small">the agent’s backtest<\/span>/);
  assert.doesNotMatch(html, /leader/);
});
