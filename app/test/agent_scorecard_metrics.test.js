'use strict';
/**
 * The Agents-tab scorecard is six readings in one grid.
 *
 * The phone card used a flex row. The drawdown's book caption sat inside the
 * Max DD cell, so that cell grew and the second row wrapped: Sharpe and
 * Trades landed beside the drawdown value. A negative Sharpe carried no
 * colour while a profit factor below 1 was painted as a loss, and a measured
 * profit factor of 0 was painted as break-even because `|| 1` treated it as
 * missing. Max drawdown was hardcoded `neg` — a magnitude below peak, not a
 * signed return.
 *
 * Driven: the renderer runs, and the frozen Dip Sniper / Momentum Hunter
 * cards are the fixture. A scan of the source would stay green if the grid
 * class were never reached.
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
const STRAT = fs.readFileSync(path.join(APP, 'public', 'strategy.html'), 'utf8');
const Score = require(path.join(APP, 'public', 'js', 'agent-scorecard.js'));
const Theatre = require(path.join(APP, 'public', 'js', 'equity-theatre-model.js'));

function loadPnlClass() {
  const src = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
  const start = src.indexOf('  function pnlClass(n) {');
  const end = src.indexOf('  function fmtAgo(iso) {', start);
  assert.ok(start > 0 && end > start, 'pnlClass moved; the colour helper is app.js');
  const ctx = { Number, isFinite, String };
  vm.runInNewContext(src.slice(start, end) + '\nglobalThis.pnlClass = pnlClass;', ctx);
  return ctx.pnlClass;
}

const pnlClass = loadPnlClass();

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[c]));
}

function ddLabel(kind) {
  const k = Theatre.kind(kind);
  return `<span class="muted small">${esc(k.short.en)}</span>`;
}

function dashboardRenderer() {
  const a = DASH.indexOf('// ── agent scorecard: six readings, one grid');
  const b = DASH.indexOf('// ── agent scorecard: renderer end');
  assert.ok(a > 0 && b > a, 'scoreBlock lost its markers');
  const ctx = {
    window: { AgentScorecard: Score },
    pnlClass, esc, ddLabel,
    String, Number, Error, Array, Object, isFinite, Math,
  };
  vm.runInNewContext(DASH.slice(a, b) + '\nglobalThis.scoreBlock = scoreBlock;', ctx);
  return ctx.scoreBlock;
}

const scoreBlock = dashboardRenderer();

function strategyCls() {
  const src = STRAT;
  const start = src.indexOf('  var cls = function (v) {');
  const end = src.indexOf('  var root = document.getElementById', start);
  assert.ok(start > 0 && end > start, 'strategy cls moved');
  const ctx = { String, Number, isFinite };
  vm.runInNewContext(src.slice(start, end) + '\nglobalThis.cls = cls;', ctx);
  return ctx.cls;
}

function strategyRenderer() {
  const a = STRAT.indexOf('  // ── strategy scorecard renderer ──');
  const b = STRAT.indexOf('  // ── strategy scorecard renderer end ──');
  assert.ok(a > 0 && b > a, 'strategy scoreBlock lost its markers');
  const ctx = {
    window: { AgentScorecard: Score },
    esc, cls: strategyCls(),
    String, Number, isFinite, Math,
  };
  vm.runInNewContext(STRAT.slice(a, b) + '\nglobalThis.scoreBlock = scoreBlock;', ctx);
  return ctx.scoreBlock;
}

const strategyBlock = strategyRenderer();

function sliceDiv(html, start) {
  let i = start;
  let depth = 0;
  while (i < html.length) {
    if (html.startsWith('<div', i)) { depth += 1; i += 4; continue; }
    if (html.startsWith('</div>', i)) {
      depth -= 1;
      i += 6;
      if (depth === 0) return html.slice(start, i);
      continue;
    }
    i += 1;
  }
  assert.fail('unclosed div');
}

function gridOf(html) {
  const at = html.indexOf('<div class="agent-metrics"');
  assert.ok(at >= 0, 'no metrics grid: ' + html);
  const grid = sliceDiv(html, at);
  const open = grid.slice(0, grid.indexOf('>') + 1);
  assert.match(open, /display:grid/);
  assert.match(open, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.doesNotMatch(open, /flex-wrap/);
  assert.doesNotMatch(grid, /min-width:70px/);
  return grid;
}

function cells(html) {
  const grid = gridOf(html);
  const out = [];
  const re = /<div class="agent-metric" data-metric="([^"]+)"[^>]*>([\s\S]*?)<\/div>/g;
  let m;
  while ((m = re.exec(grid))) {
    const cls = (m[2].match(/class="num ([^"]*)"/) || ['', ''])[1];
    const text = (m[2].match(/data-metric-value[^>]*>([^<]*)</) || [])[1];
    out.push({ label: m[1], cls: cls.trim(), text, inner: m[2] });
  }
  assert.equal(out.length, 6, 'expected six metric cells, got ' + out.length);
  return { grid, cells: out, html };
}

function cell(parsed, label) {
  const c = parsed.cells.find((x) => x.label === label);
  assert.ok(c, 'missing ' + label + ' in ' + parsed.cells.map((x) => x.label).join(','));
  return c;
}

function cardFrom(file) {
  // HEAD, not the worktree: a parallel edit can rewrite the frozen cards
  // while this test is about the record this branch actually ships.
  const raw = JSON.parse(execFileSync('git', ['show', `HEAD:benchmark/scorecards/${file}`], {
    cwd: REPO, encoding: 'utf8',
  }));
  // The catalogue truncates the hash to 12 before a card sees it
  // (bot/core/strategy_catalog.py). The renderer prints what it is given.
  return {
    dataset: raw.dataset,
    dataset_hash: String(raw.dataset_hash).slice(0, 12),
    bars: raw.bars,
    unmodeled: raw.unmodeled,
    metrics: raw.metrics,
  };
}

function loss(cls) {
  assert.match(cls, /\bneg\b/, 'expected a loss colour, got "' + cls + '"');
}
function neutral(cls) {
  assert.doesNotMatch(cls, /\bneg\b/);
  assert.doesNotMatch(cls, /\bpos\b/);
}

test('Dip Sniper keeps the frozen figures, in a 3-column grid, caption outside', () => {
  const html = scoreBlock(cardFrom('dip-sniper.json'));
  const parsed = cells(html);
  assert.deepEqual(parsed.cells.map((c) => c.label),
    ['Return', 'Profit factor', 'Win rate', 'Max DD', 'Sharpe', 'Trades']);

  const ret = cell(parsed, 'Return');
  const pf = cell(parsed, 'Profit factor');
  const wr = cell(parsed, 'Win rate');
  const dd = cell(parsed, 'Max DD');
  const sh = cell(parsed, 'Sharpe');
  const tr = cell(parsed, 'Trades');

  assert.equal(ret.text, '-0.11%');
  assert.equal(pf.text, '0.91');
  assert.equal(wr.text, '47%');
  assert.equal(dd.text, '0.87%');
  assert.equal(sh.text, '-1.59');
  assert.equal(tr.text, '19');

  loss(ret.cls);
  loss(pf.cls);
  neutral(wr.cls);
  neutral(dd.cls);
  loss(sh.cls);
  neutral(tr.cls);
  // A negative Sharpe must carry the same loss class as a sub-1 profit factor.
  assert.equal(sh.cls, pf.cls);
  // Label above the number. Inline spans let a value run into the next column
  // once a card is narrower than the phone screenshot.
  assert.match(ret.inner, /agent-metric-k" style="display:block/);
  assert.match(ret.inner, /data-metric-value style="display:block/);

  assert.doesNotMatch(parsed.grid, /leader/, 'caption sat inside the metric grid');
  for (const c of parsed.cells) {
    assert.doesNotMatch(c.inner, /leader/, c.label + ' contains the book caption');
  }
  const after = html.slice(html.indexOf(parsed.grid) + parsed.grid.length);
  assert.match(after, /agent-metrics-caption/);
  assert.match(after, /this leader/);
  assert.match(html, /Frozen backtest · majors_1h · 1500 bars · #8dbe73514ce8/);
  assert.doesNotMatch(html, /low sample/);
  assert.ok(!html.includes('$'), 'public card rendered a dollar');
});

test('Momentum Hunter: a profit factor of 0 is a loss, and the row still grids', () => {
  const html = scoreBlock(cardFrom('momentum-hunter.json'));
  const parsed = cells(html);
  const pf = cell(parsed, 'Profit factor');
  const sh = cell(parsed, 'Sharpe');
  const dd = cell(parsed, 'Max DD');
  assert.equal(cell(parsed, 'Return').text, '-0.22%');
  assert.equal(pf.text, '0.00');
  loss(pf.cls);
  assert.equal(cell(parsed, 'Win rate').text, '0%');
  neutral(cell(parsed, 'Win rate').cls);
  assert.equal(dd.text, '0.22%');
  neutral(dd.cls);
  assert.equal(sh.text, '-14.96');
  loss(sh.cls);
  assert.equal(sh.cls, pf.cls);
  assert.equal(cell(parsed, 'Trades').text, '3');
  assert.match(html, /low sample · 3 trades/);
  assert.doesNotMatch(parsed.grid, /leader/);
});

test('a gain is green, break-even profit factor follows pnlClass, drawdown stays uncoloured', () => {
  const html = scoreBlock({
    dataset: 'majors_1h', bars: 1500, dataset_hash: 'abc',
    metrics: {
      total_return_pct: 1.25, profit_factor: 1.4, win_rate: 0.6,
      max_drawdown_pct: 2, sharpe_ratio: 0.8, total_trades: 40,
    },
  });
  const parsed = cells(html);
  assert.equal(cell(parsed, 'Return').text, '+1.25%');
  assert.match(cell(parsed, 'Return').cls, /\bpos\b/);
  assert.equal(cell(parsed, 'Profit factor').text, '1.40');
  assert.match(cell(parsed, 'Profit factor').cls, /\bpos\b/);
  assert.equal(cell(parsed, 'Sharpe').text, '0.80');
  assert.match(cell(parsed, 'Sharpe').cls, /\bpos\b/);
  assert.equal(cell(parsed, 'Max DD').text, '2.00%');
  neutral(cell(parsed, 'Max DD').cls);
  assert.equal(cell(parsed, 'Win rate').text, '60%');
  assert.doesNotMatch(html, /low sample/);

  const even = scoreBlock({
    metrics: { total_return_pct: 0, profit_factor: 1, win_rate: 0.5,
      max_drawdown_pct: 0, sharpe_ratio: 0, total_trades: 12 },
  });
  const ep = cells(even);
  // pnlClass paints a measured 0 as pos. Profit factor 1 is that zero.
  assert.equal(pnlClass(0), 'pos');
  assert.match(cell(ep, 'Profit factor').cls, /\bpos\b/);
  assert.equal(cell(ep, 'Profit factor').text, '1.00');
  assert.equal(cell(ep, 'Max DD').text, '0.00%');
  neutral(cell(ep, 'Max DD').cls);
  assert.equal(cell(ep, 'Return').text, '+0.00%');
});

test('unreadable metrics are dashes, never a zero and never a colour', () => {
  const html = scoreBlock({
    metrics: {
      total_return_pct: null,
      profit_factor: '',
      win_rate: undefined,
      max_drawdown_pct: NaN,
      sharpe_ratio: ' -1.59 ',
      total_trades: false,
    },
  });
  const parsed = cells(html);
  for (const c of parsed.cells) {
    assert.equal(c.text, '—', c.label + ' painted ' + c.text);
    neutral(c.cls);
  }
  assert.doesNotMatch(html, /0\.00/);
  assert.doesNotMatch(html, /low sample/);
  assert.match(scoreBlock(null), /Verified backtest pending/);
  assert.match(scoreBlock({}), /Verified backtest pending/);
  const bare = scoreBlock({ metrics: { profit_factor: 0.91, sharpe_ratio: -1.59 } });
  const ctx = { window: {}, pnlClass, esc, ddLabel, String, Number };
  const a = DASH.indexOf('  function scoreBlock(sc) {');
  const b = DASH.indexOf('  // ── agent scorecard: renderer end');
  vm.runInNewContext(DASH.slice(a, b) + '\nglobalThis.scoreBlock = scoreBlock;', ctx);
  const missing = ctx.scoreBlock({ metrics: { profit_factor: 0.91 } });
  assert.match(missing, /could not be painted/);
  assert.doesNotMatch(missing, /0\.91/);
  assert.match(bare, /0\.91/);
});

function stratCell(html, key) {
  const re = new RegExp('data-metric="' + key + '"[\\s\\S]*?class="v ([^"]*)"[^>]*>([^<]*)<');
  const m = re.exec(html);
  assert.ok(m, key + ' missing in ' + html);
  return { cls: m[1].trim(), text: m[2] };
}

test('the public strategy page uses the same reading and a 3-column grid', () => {
  const html = strategyBlock(cardFrom('dip-sniper.json'));
  assert.equal(stratCell(html, 'return').text, '-0.11%');
  assert.equal(stratCell(html, 'return').cls, 'down');
  assert.equal(stratCell(html, 'profit-factor').text, '0.91');
  assert.equal(stratCell(html, 'profit-factor').cls, 'down');
  assert.equal(stratCell(html, 'win-rate').text, '47%');
  assert.equal(stratCell(html, 'win-rate').cls, '');
  assert.equal(stratCell(html, 'max-dd').text, '0.87%');
  assert.equal(stratCell(html, 'max-dd').cls, '');
  assert.equal(stratCell(html, 'sharpe').text, '-1.59');
  assert.equal(stratCell(html, 'sharpe').cls, 'down');
  assert.equal(stratCell(html, 'sharpe').cls, stratCell(html, 'profit-factor').cls);
  assert.equal(stratCell(html, 'trades').text, '19');

  const mom = strategyBlock(cardFrom('momentum-hunter.json'));
  assert.equal(stratCell(mom, 'profit-factor').text, '0.00');
  assert.equal(stratCell(mom, 'profit-factor').cls, 'down');
  assert.equal(stratCell(mom, 'sharpe').cls, 'down');
  assert.equal(stratCell(mom, 'max-dd').cls, '');
  assert.match(mom, /low sample · 3 trades/);

  const unread = strategyBlock({
    metrics: { profit_factor: '', sharpe_ratio: null, max_drawdown_pct: NaN,
      total_return_pct: null, win_rate: null, total_trades: null },
  });
  assert.doesNotMatch(unread, /class="v down"|class="v up"/);
  assert.match(unread, /—/);

  assert.match(STRAT, /\.sc-grid \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(STRAT, /\.sc-grid \{[^}]*flex-wrap/);
  const dashHtml = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const model = dashHtml.indexOf('/js/agent-scorecard.js');
  const dash = dashHtml.indexOf('/js/dashboard.js');
  assert.ok(model > 0 && model < dash, 'dashboard.js reads AgentScorecard at paint time');
});
