'use strict';
/**
 * A setup cell shows n, a Wilson hit rate, mean R with the interval the
 * Python tree already uses, and a Benjamini-Hochberg q-value over the cells
 * that were actually published.
 *
 * The interval functions are twins of `wilson_lower_bound` / `parity._wilson`
 * and `shadow_book.mean_r_interval`. An unreadable input is null, not a
 * zero-width interval at 0. A measured flat book stays 0. A cell left out
 * because a dimension was missing, or because it fell past the cap, is not
 * in the q-value family.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const { computeAnalytics } = require('../lib/signal_analytics');
const inf = require('../lib/inference');
const { publicAnalytics, dollarKeys } = require('../lib/public_signal');
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

function dims(setup) {
  return {
    signal_type: setup,
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
  };
}

/** n rows whose mean and standard error realise a two-sided z. */
function rowsAtZ(setup, n, mean, z) {
  const se = Math.abs(mean) / z;
  const variance = se * se * n;
  const d = Math.sqrt(variance * (n - 1) / n);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const pnl = i < n / 2 ? mean + d : mean - d;
    rows.push(cell({ pnl, ...dims(setup) }));
  }
  return rows;
}

function moments(rows) {
  let sum = 0;
  let sum2 = 0;
  let wins = 0;
  for (const row of rows) {
    sum += row.pnl;
    sum2 += row.pnl * row.pnl;
    if (row.pnl > 0) wins += 1;
  }
  return { n: rows.length, sum, sum2, wins };
}

function rowHtml(html, setup) {
  const parts = html.split('class="wr-row');
  return parts.find((part) => part.includes(setup)) || '';
}

test('the JS interval matches the Python functions on the same inputs', () => {
  // The web-app job installs Node only. ubuntu-latest has `python3` and not
  // `python3.11`, and it does not install the bot's dependencies. Importing
  // `bot.backtest.parity` loads `bot.config`, which imports dotenv, so that
  // import cannot run there. `wilson_lower_bound` and `mean_r_interval` use
  // the standard library. `_wilson` is compiled from its own source in
  // parity.py, with that lower bound in scope: still that function, not a
  // second formula written beside it.
  const script = `
import ast, json, math
from pathlib import Path
from typing import Optional
from bot.core.shadow_book import mean_r_interval
from bot.learning.readiness import wilson_lower_bound

tree = ast.parse(Path("bot/backtest/parity.py").read_text(encoding="utf-8"))
picked = []
for node in tree.body:
    if isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if names == ["_Z"]:
            picked.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name == "_wilson":
        picked.append(node)
if len(picked) != 2 or not isinstance(picked[-1], ast.FunctionDef):
    raise SystemExit("parity.py did not yield module-level _Z and _wilson")
ns = {"wilson_lower_bound": wilson_lower_bound, "Optional": Optional}
exec(compile(ast.Module(body=picked, type_ignores=[]), "bot/backtest/parity.py", "exec"), ns)
_wilson = ns["_wilson"]
wilson = []
for succ, n in [(21, 34), (27, 34), (0, 11), (1, 2), (20, 40), (0, 4), (3, 3), (10, 10), (5, 8)]:
    lo, hi = _wilson(succ, n)
    wilson.append({"succ": succ, "n": n, "lo": lo, "hi": hi,
                   "lower": wilson_lower_bound(succ, n)})
def two_sided(n, s, s2):
    if n < 2:
        return None
    mean = s / n
    var = max(0.0, (s2 - n * mean * mean) / (n - 1))
    se = math.sqrt(var / n)
    if se == 0:
        return 1.0 if mean == 0 else 0.0
    z = abs(mean / se)
    return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
intervals = []
for n, s, s2 in [
    (2, 1.0, 5.0), (3, 0.0, 0.0), (10, 5.0, 15.0), (4, -4.0, 4.0),
    (30, 7.728, 12.5), (8, 3.5, 9.25), (15, -2.0, 40.0), (6, 1.25, 8.0),
    (1, 0.0, 0.0), (0, 0.0, 0.0),
]:
    iv = mean_r_interval(n, s, s2)
    intervals.append({"n": n, "sum": s, "sum2": s2,
                      "lo": None if iv is None else iv[0],
                      "hi": None if iv is None else iv[1],
                      "p": two_sided(n, s, s2)})
print(json.dumps({"wilson": wilson, "intervals": intervals}))
`;
  // Prefer the interpreter this repo pins. The web-app runner only has
  // `python3` (3.12 on ubuntu-latest). Either can run the script above.
  const bin = ['python3.11', 'python3'].find((name) => {
    const probe = spawnSync(name, ['-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'], {
      encoding: 'utf8',
    });
    return probe.status === 0;
  });
  assert.ok(bin, 'no Python >= 3.11 on PATH (tried python3.11 and python3)');
  const run = spawnSync(bin, ['-c', script], { cwd: REPO, encoding: 'utf8' });
  const detail = [run.stderr, run.error && run.error.message].filter(Boolean).join('\n');
  assert.equal(run.status, 0, detail || 'python exited without a status');
  const py = JSON.parse(run.stdout);
  for (const c of py.wilson) {
    const got = inf.wilsonInterval(c.succ, c.n);
    assert.ok(got, `Wilson refused a readable ${c.succ}/${c.n}`);
    assert.ok(Math.abs(got[0] - c.lo) < 1e-12, `lo ${got[0]} != ${c.lo}`);
    assert.ok(Math.abs(got[1] - c.hi) < 1e-12, `hi ${got[1]} != ${c.hi}`);
    assert.ok(Math.abs(got[0] - c.lower) < 1e-12);
    assert.ok(got[1] > got[0] || c.succ === c.n);
  }
  for (const c of py.intervals) {
    const got = inf.meanRInterval(c.n, c.sum, c.sum2);
    if (c.lo === null) {
      assert.equal(got, null, `n=${c.n} invented an interval`);
      assert.equal(inf.meanRPValue(c.n, c.sum, c.sum2), null);
    } else {
      assert.deepEqual(got, [c.lo, c.hi]);
      const p = inf.meanRPValue(c.n, c.sum, c.sum2);
      assert.ok(Math.abs(p - c.p) < 1e-6, `p ${p} != ${c.p}`);
    }
  }
});

test('an unreadable input is not a zero-width interval at 0', () => {
  assert.equal(inf.wilsonInterval(0, 0), null);
  assert.equal(inf.wilsonInterval(null, 5), null);
  assert.equal(inf.wilsonInterval(undefined, 5), null);
  assert.equal(inf.wilsonInterval(Number.NaN, 5), null);
  assert.equal(inf.wilsonInterval(-1, 5), null);
  assert.equal(inf.wilsonInterval(6, 5), null);
  assert.equal(inf.meanRInterval(1, 0, 0), null);
  assert.equal(inf.meanRInterval(2, Number.NaN, 1), null);
  assert.equal(inf.meanRInterval(2, null, 1), null);
  assert.equal(inf.meanRInterval(2, 1, Number.NaN), null);
  assert.equal(inf.meanRPValue(1, 4, 16), null);
  // Zero successes is a real bound of 0, and the interval still has width.
  const none = inf.wilsonInterval(0, 11);
  assert.equal(none[0], 0);
  assert.ok(none[1] > 0);
});

test('Benjamini-Hochberg changes which of the displayed tests clear 0.05', () => {
  // Four tests. Three have p < 0.05. Only the smallest survives the family.
  // A null is a cell whose interval could not be computed: it stays null and
  // it is not in m. Both arms: one q stays under 0.05, two that were under
  // on the raw p move over.
  const qs = inf.bhQValues([0.01, null, 0.03, 0.04, 0.20]);
  assert.equal(qs[1], null);
  assert.ok(qs[0] < 0.05, `the smallest q was ${qs[0]}`);
  assert.ok(qs[2] >= 0.05 && qs[3] >= 0.05, `FDR left ${qs[2]}, ${qs[3]} significant`);
  assert.ok(0.03 < 0.05 && 0.04 < 0.05);
  const withHidden = inf.bhQValues([0.001, 0.01, 0.03, 0.04, 0.20]);
  assert.notEqual(withHidden[1], qs[0], 'a test outside the list did not move q');
});

test('a measured zero stays zero, and a one-row cell has no interval', () => {
  const flat = computeAnalytics([
    cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }),
  ]);
  assert.equal(flat.by_setup.length, 1);
  const g = flat.by_setup[0];
  assert.equal(g.n, 4);
  assert.equal(g.hit_rate, 0);
  assert.equal(g.mean_r, 0);
  assert.equal(g.net_r, 0);
  assert.equal(g.wilson_lo, 0);
  assert.ok(g.wilson_hi > 0);
  assert.deepEqual([g.mean_r_lo, g.mean_r_hi], [0, 0]);
  assert.equal(g.q_value, 1);
  assert.equal(g.flat, 4);

  const thin = computeAnalytics([cell({ pnl: 0, signal_type: 'sweep' })]);
  const one = thin.by_setup[0];
  assert.equal(one.n, 1);
  assert.equal(one.hit_rate, 0);
  assert.equal(one.mean_r, 0);
  assert.equal(one.mean_r_lo, null);
  assert.equal(one.mean_r_hi, null);
  assert.equal(one.q_value, null);
  assert.ok(one.wilson_lo != null && one.wilson_hi != null);

  const html = WR.setupScoreboard(thin.by_setup);
  assert.match(html, /hit 0/);
  assert.match(html, /mean 0R/);
  assert.match(html, /interval unavailable/);
  assert.match(html, /q unavailable/);
  assert.doesNotMatch(html, /interval 0 to 0/);
  assert.doesNotMatch(html, /q 0(?!\.)/);
});

test('q-values are over the published cells only', () => {
  const specs = [
    ['alpha', 40, 1, 2.575829303548903],
    ['beta', 40, 1, 2.170090377584561],
    ['gamma', 40, 1, 2.0537489106318247],
    ['delta', 40, 1, 1.2815515655446004],
  ];
  const groups = Object.fromEntries(specs.map(([name, n, mean, z]) => [name, rowsAtZ(name, n, mean, z)]));
  const shown = specs.flatMap(([name]) => groups[name]);
  // A strong book with no timeframe is not a cell, and an unreadable R is
  // not a row. Neither may move the family.
  const omitted = [
    cell({ pnl: 9, signal_type: 'alpha', timeframe: null }),
    cell({ pnl: 9, signal_type: 'secret', timeframe: null }),
    cell({ pnl: 'nope', signal_type: 'alpha' }),
  ];
  // Smaller n, so the cap drops it. Its p is far smaller than the others;
  // leaving it in the family would move their q.
  const hidden = rowsAtZ('hidden', 20, 1, 4.5);

  const published = computeAnalytics(shown.concat(omitted), { top: 4 });
  assert.equal(published.by_setup.length, 4);
  assert.ok(!published.by_setup.some((g) => g.setup === 'hidden' || g.setup === 'secret'));
  assert.ok(published.by_setup.every((g) => g.timeframe && g.setup));
  assert.ok(!('q_value' in published.by_pattern[0]));

  const ps = published.by_setup.map((g) => {
    const m = moments(groups[g.setup]);
    const p = inf.meanRPValue(m.n, m.sum, m.sum2);
    assert.equal(g.n, m.n);
    assert.equal(g.hit_rate, m.wins / m.n);
    const wilson = inf.wilsonInterval(m.wins, m.n);
    assert.ok(Math.abs(g.wilson_lo - wilson[0]) < 1e-12);
    assert.ok(Math.abs(g.wilson_hi - wilson[1]) < 1e-12);
    const interval = inf.meanRInterval(m.n, m.sum, m.sum2);
    assert.deepEqual([g.mean_r_lo, g.mean_r_hi], interval);
    return p;
  });
  const expectQ = inf.bhQValues(ps);
  published.by_setup.forEach((g, i) => {
    assert.ok(Math.abs(g.q_value - expectQ[i]) < 1e-12, `${g.setup} q drifted`);
  });

  const rawSig = published.by_setup.filter((g, i) => ps[i] < 0.05).map((g) => g.setup);
  const fdrSig = published.by_setup.filter((g) => g.q_value < WR.Q_DISPLAY).map((g) => g.setup);
  assert.ok(rawSig.length > fdrSig.length, `raw ${rawSig} FDR ${fdrSig}`);
  assert.deepEqual(fdrSig, ['alpha']);
  assert.ok(rawSig.includes('beta') && rawSig.includes('gamma'));
  assert.ok(!fdrSig.includes('beta') && !fdrSig.includes('gamma'));

  // Colour does not follow the q-value. alpha's interval clears zero and
  // its q is under 0.05; it still reads exploratory, and the row stays muted.
  const board = WR.setupScoreboard(published.by_setup);
  assert.match(rowHtml(board, 'alpha'), /exploratory/);
  assert.equal(rowHtml(board, 'alpha').includes('wr-pos'), false);
  assert.match(rowHtml(board, 'beta'), /exploratory/);
  assert.equal(rowHtml(board, 'beta').includes('wr-pos'), false);
  assert.match(rowHtml(board, 'beta'), /interval /);
  assert.match(rowHtml(board, 'delta'), /q /);

  const crowded = computeAnalytics(shown.concat(hidden), { top: 5 });
  const alphaShown = published.by_setup.find((g) => g.setup === 'alpha');
  const alphaAll = crowded.by_setup.find((g) => g.setup === 'alpha');
  assert.notEqual(alphaAll.q_value, alphaShown.q_value);

  const scrubbed = publicAnalytics(published);
  assert.deepEqual(dollarKeys(scrubbed), []);
  const kept = scrubbed.by_setup.find((g) => g.setup === 'alpha');
  assert.equal(kept.hit_rate, alphaShown.hit_rate);
  assert.equal(kept.q_value, alphaShown.q_value);
  assert.equal(kept.mean_r_lo, alphaShown.mean_r_lo);
  assert.ok(!('p_value' in kept) && !('_p' in kept));
});

test('the board shows the payload numbers and does not paint an unestablished cell', () => {
  const base = {
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG', n: 20, win_rate: 70, mean_r: 1.2,
    hit_rate: 0.7, wilson_lo: 0.481, wilson_hi: 0.854,
  };
  // Positive point estimate, interval reaches both sides of zero, q does not
  // clear. The percentage is still shown. The colour is not.
  const open = WR.setupScoreboard([{
    ...base, mean_r_lo: -0.2, mean_r_hi: 2.4, q_value: 0.2,
  }]);
  assert.match(open, /70%/);
  assert.match(open, /hit 0\.7/);
  assert.match(open, /Wilson 0\.481 to 0\.854/);
  assert.match(open, /mean 1\.2R/);
  assert.match(open, /interval -0\.2 to 2\.4/);
  assert.match(open, /q 0\.2/);
  assert.equal(rowHtml(open, 'vwap_reversion').includes('wr-pos'), false);
  assert.match(open, /wr-unrated/);
  assert.doesNotMatch(open, /wr-fill/);

  // Same point estimate, interval clear of zero, q under the display level.
  // The numbers stay. The established colour does not: the cell is exploratory.
  const held = WR.setupScoreboard([{
    ...base, mean_r_lo: 0.2, mean_r_hi: 2.4, q_value: 0.01,
  }]);
  assert.match(held, /exploratory/);
  assert.equal(held.includes('wr-pos'), false);
  assert.equal(held.includes('wr-fill'), false);

  // q exactly at the level does not clear. Touching zero does not either.
  const edgeQ = WR.setupScoreboard([{
    ...base, mean_r_lo: 0.2, mean_r_hi: 2.4, q_value: WR.Q_DISPLAY,
  }]);
  assert.equal(rowHtml(edgeQ, 'vwap_reversion').includes('wr-pos'), false);
  const touch = WR.setupScoreboard([{
    ...base, mean_r_lo: 0, mean_r_hi: 2.4, q_value: 0.01,
  }]);
  assert.equal(rowHtml(touch, 'vwap_reversion').includes('wr-pos'), false);

  // A losing rate whose interval is entirely below zero is still exploratory.
  const loss = WR.setupScoreboard([{
    ...base, win_rate: 30, hit_rate: 0.3, mean_r: -1,
    mean_r_lo: -2, mean_r_hi: -0.1, q_value: 0.01,
  }]);
  assert.match(loss, /exploratory/);
  assert.equal(loss.includes('wr-neg'), false);
  const lossOpen = WR.setupScoreboard([{
    ...base, win_rate: 30, hit_rate: 0.3, mean_r: -1,
    mean_r_lo: -2, mean_r_hi: 0.1, q_value: 0.01,
  }]);
  assert.equal(rowHtml(lossOpen, 'vwap_reversion').includes('wr-neg'), false);

  // The numbers are the payload's, not a second formula. 0.1234 is not the
  // Wilson bound of 14/20.
  const planted = WR.setupScoreboard([{
    ...base, wilson_lo: 0.1234, wilson_hi: 0.4321,
    mean_r_lo: 0.2, mean_r_hi: 2.4, q_value: 0.01,
  }]);
  assert.match(planted, /Wilson 0\.1234 to 0\.4321/);

  // A null mean is not 0R. A missing interval is not 0 to 0.
  const absent = WR.setupDetail({
    n: 4, hit_rate: null, mean_r: null, mean_r_lo: null, mean_r_hi: null, q_value: null,
  });
  assert.match(absent, /hit unavailable/);
  assert.match(absent, /mean unavailable/);
  assert.match(absent, /interval unavailable/);
  assert.match(absent, /q unavailable/);
  assert.equal(absent.includes('0R'), false);
  assert.equal(absent.includes('interval 0'), false);

  // A pattern column has no interval fields, so the sample floor is still
  // the whole colour rule.
  const pattern = WR.buildRows([{ pattern: 'flag', win_rate: 61, n: 47 }], 'pattern');
  assert.match(pattern, /wr-pos/);
});
