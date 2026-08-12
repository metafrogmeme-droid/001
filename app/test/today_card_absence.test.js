'use strict';
/**
 * The fourth panel, and why it was the wrong one.
 *
 * The dashboard's "Today for you" card computed its own totals:
 *
 *     const net  = today.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
 *     const wins = today.filter(t => parseFloat(t.pnl) > 0).length;
 *     ... class="num ${pnlClass(net)}"
 *
 * over `trades.pnl`, which is nullable. So an unpriced close was summed as a
 * break-even, counted as a non-win, and — because `pnlClass` treats `>= 0` as
 * green — the fabricated total was painted as a profit.
 *
 * Three sibling panels had already been cured of exactly this. This one was
 * not, and the reason is worth recording: it lives in a browser script that
 * had **no way to reach `trade_stats.js`**, which is a Node module under
 * `app/lib/`. So the rule had four spellings and the fourth was the wrong one,
 * not through oversight but through reach.
 *
 * The fix is the module, not the line. `trade_stats.js` moved to
 * `app/public/js/trade-stats.js` behind the same UMD wrapper
 * `engine-status-model.js` uses, so `require()` is unchanged for the six
 * server-side callers and the browser gets `window.TradeStats`. A fifth panel
 * written tomorrow reaches the same answer instead of writing a fifth one.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const PUBLIC = path.join(__dirname, '..', 'public');

test('the module loads in a browser-shaped context', () => {
  // No `module`, no `require` — just a global. If the UMD wrapper regresses to
  // CommonJS-only the card throws at runtime and the panel renders nothing,
  // which is a silent failure the server-side tests cannot see.
  const ctx = { self: {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(PUBLIC, 'js', 'trade-stats.js'), 'utf8'), ctx);
  const TS = ctx.self.TradeStats;
  assert.ok(TS, 'window.TradeStats was not defined');
  for (const fn of ['winStats', 'realizedTotal', 'profitFactor', 'aggregateStats']) {
    assert.strictEqual(typeof TS[fn], 'function', `${fn} missing from the global`);
  }
});

test('require() still returns the same functions', () => {
  // The six server-side callers must be unaffected by the move.
  const TS = require('../public/js/trade-stats');
  assert.strictEqual(TS.winStats([{ pnl: 10 }, { pnl: null }]).rate, 1);
  assert.strictEqual(TS.realizedTotal([{ pnl: null }]), null);
});

test('the browser and the server compute the same answer', () => {
  // The whole reason for one module. Same rows, two load paths, one result.
  const rows = [{ pnl: 40 }, { pnl: -10 }, { pnl: null }, { pnl: 0 }];
  const node = require('../public/js/trade-stats');
  const ctx = { self: {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(PUBLIC, 'js', 'trade-stats.js'), 'utf8'), ctx);
  // Compared as JSON: objects from a VM realm have a different Object
  // prototype, so deepStrictEqual fails on identity for values that ARE equal.
  assert.strictEqual(JSON.stringify(ctx.self.TradeStats.winStats(rows)),
                     JSON.stringify(node.winStats(rows)));
  assert.strictEqual(ctx.self.TradeStats.realizedTotal(rows), node.realizedTotal(rows));
});

/**
 * Source with `//` comments removed.
 *
 * CLAUDE.md: "Strip comments first. A comment that quotes the string it
 * forbids is indistinguishable from the code doing it, and this has produced
 * four false failures." It produced a fifth while this file was being
 * written — the comment explaining the fix quotes `parseFloat(t.pnl) || 0`,
 * and the scan failed on the very change that cured the defect.
 */
function codeOnly(src) {
  return src.split('\n')
    .map((line) => {
      // Naive but sufficient here: no `//` appears inside a string literal in
      // the region scanned, and a URL would need `://`.
      const i = line.indexOf('//');
      return i >= 0 && line[i - 1] !== ':' ? line.slice(0, i) : line;
    })
    .join('\n');
}

test('the card reaches the shared reader instead of its own arithmetic', () => {
  // Wiring, which no unit test can see: the model can be perfect and unreached.
  const src = codeOnly(
    fs.readFileSync(path.join(PUBLIC, 'js', 'dashboard.js'), 'utf8'));
  const i = src.indexOf("dd.ac_today");
  assert.ok(i > 0, 'the "Today for you" card must still exist');
  const card = src.slice(Math.max(0, i - 1200), i + 400);
  assert.ok(!/parseFloat\(t\.pnl\)\s*\|\|\s*0/.test(card),
    'the card is back to summing an unpriced close as a break-even');
  assert.match(card, /TradeStats/, 'the card computes its own totals again');
});

test('an unmeasured total carries no colour, via the shared formatter', () => {
  // The card passes a possibly-null net straight to `pnlClass`, relying on its
  // "unknown -> muted, never red" contract rather than repeating the check.
  // A mutation pass proved the repeat was redundant — so this pins the thing
  // actually load-bearing, which is that contract.
  const src = fs.readFileSync(path.join(PUBLIC, 'js', 'app.js'), 'utf8');
  const i = src.indexOf('function pnlClass');
  assert.ok(i > 0, 'pnlClass must still exist');
  const fn = new Function(`${src.slice(i, src.indexOf('function fmtAgo'))}; return pnlClass;`)();
  assert.strictEqual(fn(null), '', 'green on a number that does not exist');
  assert.strictEqual(fn(undefined), '');
  assert.strictEqual(fn(NaN), '');
  assert.strictEqual(fn(0), 'pos', 'a measured break-even is not "unknown"');
  assert.strictEqual(fn(-1), 'neg');
});

test('the page actually loads the module', () => {
  // A module nobody loads is a ReferenceError on the card, not a fallback.
  const html = fs.readFileSync(path.join(PUBLIC, 'dashboard.html'), 'utf8');
  const ts = html.indexOf('trade-stats.js?v=');
  const dash = html.indexOf('dashboard.js?v=');
  assert.ok(ts > 0, 'dashboard.html does not load trade-stats.js');
  assert.ok(ts < dash,
    'trade-stats.js must be tagged before dashboard.js — both are `defer`, '
    + 'which executes in document order');
});
