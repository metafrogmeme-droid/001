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
function gain(cls) {
  assert.match(cls, /\bpos\b/, 'expected a gain colour, got "' + cls + '"');
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

  assert.equal(ret.text, '+3.24%');
  assert.equal(pf.text, '1.62');
  assert.equal(wr.text, '62%');
  assert.equal(dd.text, '2.75%');
  assert.equal(sh.text, '1.24');
  assert.equal(tr.text, '13');

  gain(ret.cls);
  gain(pf.cls);
  neutral(wr.cls);
  neutral(dd.cls);
  gain(sh.cls);
  neutral(tr.cls);
  // On this card both readings are gains, so they share a class. A negative
  // Sharpe sharing the loss class of a sub-1 profit factor is the synthetic
  // case below — these frozen figures no longer show that pair.
  assert.equal(sh.cls, pf.cls);
  // Label above the number. Inline spans let a value run into the next column
  // once a card is narrower than the phone screenshot.
  assert.match(ret.inner, /agent-metric-k" style="display:block/);
  assert.match(ret.inner, /data-metric-value style="display:block/);

  assert.doesNotMatch(parsed.grid, /backtest</, 'caption sat inside the metric grid');
  for (const c of parsed.cells) {
    assert.doesNotMatch(c.inner, /backtest</, c.label + ' contains the book caption');
  }
  const after = html.slice(html.indexOf(parsed.grid) + parsed.grid.length);
  assert.match(after, /agent-metrics-caption/);
  // A frozen backtest, not a leader's record, and the caption names its figure.
  assert.match(after, /Max DD: <\/span><span class="muted small">the agent’s backtest/);
  assert.doesNotMatch(html, /leader/);
  assert.match(html, /Frozen backtest · majors_1h · 1500 bars · #8dbe73514ce8/);
  assert.doesNotMatch(html, /low sample/);
  assert.ok(!html.includes('$'), 'public card rendered a dollar');
});

test('Momentum Hunter grids the frozen figures, and a thin sample stays marked', () => {
  const html = scoreBlock(cardFrom('momentum-hunter.json'));
  const parsed = cells(html);
  const pf = cell(parsed, 'Profit factor');
  const sh = cell(parsed, 'Sharpe');
  const dd = cell(parsed, 'Max DD');
  assert.equal(cell(parsed, 'Return').text, '+0.45%');
  assert.equal(pf.text, '1.80');
  gain(pf.cls);
  assert.equal(cell(parsed, 'Win rate').text, '33%');
  neutral(cell(parsed, 'Win rate').cls);
  assert.equal(dd.text, '0.57%');
  neutral(dd.cls);
  assert.equal(sh.text, '-0.49');
  loss(sh.cls);
  // Positions, not the ladder's fills (B4-04): it closed 3 positions in 5 fills.
  assert.equal(cell(parsed, 'Trades').text, '3');
  assert.match(html, /low sample · 3 trades/);
  assert.doesNotMatch(parsed.grid, /backtest</);
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

  // A measured profit factor of 0 is a loss. `|| 1` used to treat it as
  // missing and paint break-even. A negative Sharpe takes that same class.
  // Drawdown stays a magnitude.
  const zeroPf = scoreBlock({
    metrics: { total_return_pct: -0.22, profit_factor: 0, win_rate: 0,
      max_drawdown_pct: 0.22, sharpe_ratio: -1.59, total_trades: 3 },
  });
  const zp = cells(zeroPf);
  assert.equal(cell(zp, 'Profit factor').text, '0.00');
  loss(cell(zp, 'Profit factor').cls);
  loss(cell(zp, 'Sharpe').cls);
  assert.equal(cell(zp, 'Sharpe').cls, cell(zp, 'Profit factor').cls);
  neutral(cell(zp, 'Max DD').cls);
  assert.match(zeroPf, /low sample · 3 trades/);
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
  assert.match(scoreBlock(null), /No track record published/);
  assert.match(scoreBlock({}), /No track record published/);
  assert.doesNotMatch(scoreBlock(null), /pending/);
  assert.doesNotMatch(scoreBlock(null), /Lab/);
  const omitted = scoreBlock({ omitted: 'The 8% trailing stop is recorded and not applied.' });
  assert.match(omitted, /recorded and not applied/);
  assert.doesNotMatch(omitted, /Verified backtest pending/);
  assert.doesNotMatch(omitted, /No track record published/);
  assert.doesNotMatch(omitted, /0\.00/);
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
  assert.equal(stratCell(html, 'return').text, '+3.24%');
  assert.equal(stratCell(html, 'return').cls, 'up');
  assert.equal(stratCell(html, 'profit-factor').text, '1.62');
  assert.equal(stratCell(html, 'profit-factor').cls, 'up');
  assert.equal(stratCell(html, 'win-rate').text, '62%');
  assert.equal(stratCell(html, 'win-rate').cls, '');
  assert.equal(stratCell(html, 'max-dd').text, '2.75%');
  assert.equal(stratCell(html, 'max-dd').cls, '');
  assert.equal(stratCell(html, 'sharpe').text, '1.24');
  assert.equal(stratCell(html, 'sharpe').cls, 'up');
  assert.equal(stratCell(html, 'sharpe').cls, stratCell(html, 'profit-factor').cls);
  assert.equal(stratCell(html, 'trades').text, '13');

  const mom = strategyBlock(cardFrom('momentum-hunter.json'));
  assert.equal(stratCell(mom, 'profit-factor').text, '1.80');
  assert.equal(stratCell(mom, 'profit-factor').cls, 'up');
  assert.equal(stratCell(mom, 'sharpe').cls, 'down');
  assert.equal(stratCell(mom, 'max-dd').cls, '');
  assert.match(mom, /low sample · 3 trades/);

  const unread = strategyBlock({
    metrics: { profit_factor: '', sharpe_ratio: null, max_drawdown_pct: NaN,
      total_return_pct: null, win_rate: null, total_trades: null },
  });
  assert.doesNotMatch(unread, /class="v down"|class="v up"/);
  assert.match(unread, /—/);

  assert.match(html, /data-folds="unmeasured"/);
  assert.match(html, /Folds unmeasured/);
  assert.doesNotMatch(html, /discovery data/);
  assert.doesNotMatch(html, /0 of /);

  const marked = strategyBlock({
    dataset: 'majors_1h', bars: 1500, dataset_hash: 'abc',
    data_mark: 'discovery',
    folds: { requested: 6, run: 6, profitable: 0 },
    metrics: {
      total_return_pct: 1, profit_factor: 1.1, win_rate: 0.5,
      max_drawdown_pct: 1, sharpe_ratio: 0.2, total_trades: 12,
    },
  });
  assert.match(marked, /data-mark="discovery"/);
  assert.match(marked, /discovery data/);
  assert.match(marked, /data-folds="measured"/);
  assert.match(marked, /Folds 0 of 6 profitable/);

  const dashMarked = scoreBlock({
    dataset: 'majors_1h', bars: 1500, dataset_hash: 'abc',
    data_mark: 'discovery',
    folds: { run: 6, profitable: 1 },
    metrics: {
      total_return_pct: -1, profit_factor: 0.19, win_rate: 0.4,
      max_drawdown_pct: 2, sharpe_ratio: -0.2, total_trades: 14,
    },
  });
  assert.match(dashMarked, /discovery data/);
  assert.match(dashMarked, /Folds 1 of 6 profitable/);
  const bareFolds = scoreBlock({
    metrics: {
      total_return_pct: 1, profit_factor: 1.1, win_rate: 0.5,
      max_drawdown_pct: 1, sharpe_ratio: 0.2, total_trades: 12,
    },
  });
  assert.match(bareFolds, /Folds unmeasured/);
  assert.doesNotMatch(bareFolds, /discovery data/);
  assert.doesNotMatch(bareFolds, /0 of /);

  assert.equal(Score.foldReading(null).text, 'unmeasured');
  assert.equal(Score.foldReading({ run: '6', profitable: 1 }).measured, false);
  assert.equal(Score.foldReading({ run: 6, profitable: 0 }).text, '0 of 6 profitable');
  assert.equal(Score.tradesText({ metrics: { total_trades: 0 } }), '0');
  assert.equal(Score.tradesText({ metrics: { total_trades: null } }), '—');
  assert.equal(Score.tradesText({ metrics: {} }), '—');

  const below = { id: 'full-scan', copy_follow: false, copy_follow_reason: 'below_one' };
  const offered = { id: 'dip-sniper', copy_follow: true, copy_follow_reason: 'offered' };
  const absent = { id: 'alt-sweep' };
  assert.equal(Score.followOffer(below), 'withheld');
  assert.equal(Score.followOffer(offered), 'offered');
  assert.equal(Score.followOffer(absent), 'absent');
  assert.match(Score.followButtonHtml(below, true, false), /data-follow="withheld"/);
  assert.doesNotMatch(Score.followButtonHtml(below, true, false), /data-agentfollow=/);
  assert.match(Score.followButtonHtml(offered, true, false), /data-agentfollow="dip-sniper"/);
  assert.match(Score.followButtonHtml(offered, true, true), /Following/);
  assert.equal(Score.followButtonHtml(offered, false, false), '');
  assert.equal(Score.followButtonHtml(absent, true, false), '');
  // Followed before the withholding: the card says so and offers only the
  // unfollow (a follow is refused by the route). Not followed: chip only.
  const kept = Score.followButtonHtml(below, true, true);
  assert.match(kept, /data-follow="withheld"/);
  assert.match(kept, /data-agentunfollow="full-scan"/);
  assert.match(kept, /data-withheld-following="full-scan"/);
  assert.doesNotMatch(kept, /data-agentfollow=/);
  assert.doesNotMatch(Score.followButtonHtml(below, true, false), /data-agentunfollow=/);
  assert.doesNotMatch(Score.followButtonHtml(below, false, true), /data-agentunfollow=/);
  assert.match(Score.withheldPicksText('below_one'), /Profit factor is below 1.*no longer shown or pushed/);
  assert.match(Score.withheldPicksText(undefined), /^Not offered for follow\. Its picks/);
  assert.match(Score.followLinkHtml(offered), /Follow in the app/);
  assert.match(Score.followLinkHtml(below), /data-follow="withheld"/);
  assert.equal(Score.followLinkHtml(absent), '');
  assert.equal(Score.discoveryHtml('prospective'), '');
  assert.match(Score.discoveryHtml('discovery'), /discovery data/);

  assert.match(STRAT, /\.sc-grid \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(STRAT, /\.sc-grid \{[^}]*flex-wrap/);
  const dashHtml = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const model = dashHtml.indexOf('/js/agent-scorecard.js');
  const dash = dashHtml.indexOf('/js/dashboard.js');
  assert.ok(model > 0 && model < dash, 'dashboard.js reads AgentScorecard at paint time');
});

test('the landing page loads the painter before the script that fetches the catalogue', () => {
  // The fetch can answer from a warm cache before the parser reaches a later
  // tag: the cards then painted "Trades —" and "Folds unmeasured" and were
  // never painted again. A synchronous tag before the inline script is
  // loaded and run before that script starts.
  const index = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  const painter = index.indexOf('<script src="/js/agent-scorecard.js');
  const fetchAt = index.indexOf("fetch('/api/public/strategies'");
  assert.ok(painter > 0 && fetchAt > painter, 'the painter tag must precede the fetch');
  const tag = index.slice(painter, index.indexOf('>', painter) + 1);
  assert.doesNotMatch(tag, /\b(defer|async)\b/, 'a deferred painter runs after the inline script');
});

test('a refused eligibility record is said as refused, not as none', () => {
  const text = Score.withheldText('eligibility_refused');
  assert.match(text, /record filed for this preset was refused/);
  assert.doesNotMatch(text, /no eligibility record/i);
  assert.match(Score.withheldText('below_one'), /no eligibility record says this preset survives/);
});

test('a card says how often its breaker tripped and the reset it modelled', () => {
  // The cards model an operator's reset after 24 bars
  // (scripts/gen_agent_scorecards.py::CARD_BREAKER_RESET_BARS). A figure
  // measured with that assumption says so beside itself, with the trip count.
  const m = {
    total_return_pct: -1, profit_factor: 0.9, win_rate: 0.4,
    max_drawdown_pct: 2, sharpe_ratio: -0.2, total_trades: 40,
  };
  const card = (breaker) => ({ dataset: 'majors_1h', bars: 1500, dataset_hash: 'abc', metrics: m, breaker });
  for (const [name, render] of [['dashboard', scoreBlock], ['strategy page', strategyBlock]]) {
    const three = render(card({ reset_bars: 24, trips: 3 }));
    assert.match(three, /data-breaker-trips="3"/, name);
    assert.match(three, /Breaker tripped 3 times, reset after 24 bars as an operator would/, name);
    assert.match(render(card({ reset_bars: 24, trips: 1 })), /Breaker tripped once, reset after 24 bars/, name);
    // A measured 0 is a count.
    assert.match(render(card({ reset_bars: 24, trips: 0 })), /data-breaker-trips="0">Breaker never tripped</, name);
    assert.match(render(card({ reset_bars: 0, trips: 1 })), /Breaker tripped once, no reset modelled/, name);
    // A card recorded before the block, or with a block that is not two
    // counts, says nothing about the breaker: not "never tripped".
    for (const absent of [undefined, null, { reset_bars: 24 }, { reset_bars: 24, trips: '3' }, { reset_bars: true, trips: 3 }]) {
      const html = render(card(absent));
      assert.doesNotMatch(html, /data-breaker-trips|Breaker/, name + ' ' + JSON.stringify(absent));
    }
  }
});

test('the compact cards carry the same reading as a chip', () => {
  assert.equal(Score.breakerHtml({ reset_bars: 24, trips: 3 }, true),
    '<span class="chip" data-breaker-trips="3">Breaker 3× · reset 24 bars</span>');
  assert.equal(Score.breakerHtml({ reset_bars: 0, trips: 1 }, true),
    '<span class="chip" data-breaker-trips="1">Breaker 1× · no reset</span>');
  assert.equal(Score.breakerHtml({ reset_bars: 24, trips: 0 }, true),
    '<span class="chip" data-breaker-trips="0">Breaker never tripped</span>');
  assert.equal(Score.breakerHtml(undefined, true), '');
  // The Agents grid, the landing page and Compare render inline in their
  // pages; the shape a drive does not reach is that each hands the card's own
  // block to the shared reading.
  const { codeOnly } = require('./helpers/code_only');
  for (const [file, call] of [
    ['agents.html', 'AgentScorecard.breakerHtml(cardSc.breaker, true)'],
    ['index.html', 'AgentScorecard.breakerHtml(a.scorecard && a.scorecard.breaker, true)'],
    ['compare.html', 'AgentScorecard.breakerHtml(sc.breaker, true)'],
  ]) {
    const src = codeOnly(fs.readFileSync(path.join(APP, 'public', file), 'utf8'));
    assert.equal(src.split(call).length - 1, 1, file);
  }
});
