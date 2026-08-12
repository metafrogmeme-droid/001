'use strict';
/**
 * `losses = total - wins`, and the three shapes it travels with.
 *
 * `trades.pnl` is `DECIMAL(14,2)` — NULLABLE — so a CLOSED row with no
 * recorded P&L is a real row, and four surfaces read it as a defeat:
 *
 *   routes/trades.js   losses: totalTrades - wins
 *                      winRate = totalTrades > 0 ? wins / totalTrades : 0
 *                      COALESCE(SUM(pnl), 0) as net_pnl
 *   routes/sync.js     wins / closedCount, and parseFloat(t.pnl) || 0
 *
 * Every one is in CLAUDE.md's table of banned shapes, and `losses = total -
 * wins` is there by name. Together they mean an unpriced close is counted as
 * a loss in the ratio, as a break-even in the total, and as a loss again in
 * the W/L line under it — three different wrong claims about one row.
 *
 * A break-even close hits the same subtraction: `pnl = 0.00` is a real,
 * measured, flat trade, and `total - wins` files it under L.
 *
 * These are pure functions over row sets, so the tests plant rows and read
 * the numbers. The identity at the bottom is the one worth keeping: every
 * closed trade is a win, a loss, a break-even, or unpriced — exactly one.
 */
const test = require('node:test');
const assert = require('node:assert');

const { winStats, realizedTotal, aggregateStats, profitFactor, summaryCells } =
  require('../public/js/trade-stats');

const rows = (...pnls) => pnls.map((p) => ({ pnl: p }));

test('an unpriced close is not a loss', () => {
  const s = winStats(rows(10, -5, null));
  assert.strictEqual(s.wins, 1);
  assert.strictEqual(s.losses, 1, 'the null was counted as a loss');
  assert.strictEqual(s.unscored, 1);
  assert.strictEqual(s.scored, 2);
  assert.strictEqual(s.rate, 0.5, 'the null was left in the denominator');
});

test('a break-even close is not a loss either', () => {
  const s = winStats(rows(10, 0));
  assert.strictEqual(s.wins, 1);
  assert.strictEqual(s.losses, 0, '0.00 is flat, not down');
  assert.strictEqual(s.breakeven, 1);
  assert.strictEqual(s.rate, 0.5, 'break-even is scored, and is not a win');
});

test('nothing priceable is a null rate, never 0%', () => {
  const s = winStats(rows(null, null, undefined));
  assert.strictEqual(s.scored, 0);
  assert.strictEqual(s.unscored, 3);
  assert.strictEqual(s.rate, null, '0% claims everything lost');
});

test('an empty set has no rate and no total', () => {
  assert.strictEqual(winStats([]).rate, null);
  assert.strictEqual(realizedTotal([]), null,
    '[].reduce((a,b)=>a+b,0) is 0, and printing it says the book is flat');
  assert.strictEqual(realizedTotal(null), null);
});

test('the realized total covers only the rows it could read', () => {
  assert.strictEqual(realizedTotal(rows(10, -4, null)), 6);
  assert.strictEqual(realizedTotal(rows(null, null)), null);
});

test('strings from the driver are parsed; junk is absent, not zero', () => {
  // mysql2 hands DECIMAL back as a string, so the reader has to parse — and
  // the parse failing must not land on 0.
  assert.strictEqual(realizedTotal([{ pnl: '10.50' }, { pnl: '-2.25' }]), 8.25);
  const s = winStats([{ pnl: '10.50' }, { pnl: 'n/a' }, { pnl: null }]);
  assert.strictEqual(s.scored, 1);
  assert.strictEqual(s.unscored, 2);
});

test('NaN and Infinity are absent, not extreme', () => {
  const s = winStats(rows(5, NaN, Infinity, -Infinity));
  assert.strictEqual(s.scored, 1);
  assert.strictEqual(s.unscored, 3);
  assert.strictEqual(realizedTotal(rows(5, NaN)), 5);
});

test('every closed trade lands in exactly one bucket', () => {
  // The identity that makes `losses = total - wins` impossible to reintroduce
  // without failing here.
  for (const set of [rows(1, -1, 0, null), rows(), rows(0, 0), rows(null),
                     rows(3, 2, 1), rows(-3, -2, NaN, '0')]) {
    const s = winStats(set);
    assert.strictEqual(s.wins + s.losses + s.breakeven + s.unscored, s.total,
      `buckets do not partition ${JSON.stringify(set)}`);
    assert.strictEqual(s.scored, s.wins + s.losses + s.breakeven);
    assert.strictEqual(s.total, set.length);
  }
});

test('profit factor agrees with winStats about which rows are legible', () => {
  // They run over the same reader on purpose: a PF computed over more rows
  // than the win rate is two windows printed as one.
  const set = rows(10, -5, null, 0);
  assert.strictEqual(winStats(set).scored, 3);
  assert.strictEqual(profitFactor(set), 2);
  assert.strictEqual(profitFactor(rows(10, 20)), null, 'no losses is not a PF of 0');
});

// ── the SQL aggregate path ───────────────────────────────────────────

test('an aggregate with unpriced rows uses the priced denominator', () => {
  // 20 closed, 12 priced, 6 of those won. The old query published 6/20 = 30%.
  const a = aggregateStats({ total: 20, scored: 12, wins: 6, net_pnl: 143.2 });
  assert.strictEqual(a.win_rate, 50);
  assert.strictEqual(a.unpriced, 8);
  assert.strictEqual(a.net_pnl, 143.2);
});

test('an aggregate that could price nothing reports null, not zero', () => {
  // SUM over zero non-null rows is NULL in SQL; COALESCE(...,0) is what made
  // it a measured break-even.
  const a = aggregateStats({ total: 7, scored: 0, wins: 0, net_pnl: null });
  assert.strictEqual(a.net_pnl, null);
  assert.strictEqual(a.win_rate, null);
  assert.strictEqual(a.unpriced, 7);
});

test('an aggregate over an empty table claims nothing', () => {
  const a = aggregateStats({ total: 0, scored: 0, wins: 0, net_pnl: null });
  assert.strictEqual(a.total, 0);
  assert.strictEqual(a.win_rate, null);
  assert.strictEqual(a.net_pnl, null);
});

test('aggregate counts arrive as strings from the driver', () => {
  const a = aggregateStats({ total: '20', scored: '12', wins: '6', net_pnl: '143.20' });
  assert.strictEqual(a.total, 20);
  assert.strictEqual(a.win_rate, 50);
  assert.strictEqual(a.net_pnl, 143.2);
});

test('a genuinely flat priced book still reports its zero', () => {
  // The other half of the rule. Suppressing a measured 0 is the same defect
  // facing the other way.
  const a = aggregateStats({ total: 4, scored: 4, wins: 0, net_pnl: 0 });
  assert.strictEqual(a.net_pnl, 0);
  assert.strictEqual(a.win_rate, 0, 'four priced closes, none won — that IS 0%');
});

test('a missing or malformed aggregate row does not throw', () => {
  for (const bad of [null, undefined, {}]) {
    const a = aggregateStats(bad);
    assert.strictEqual(a.total, 0);
    assert.strictEqual(a.win_rate, null);
    assert.strictEqual(a.net_pnl, null);
  }
});

// ── what a summary card actually says ────────────────────────────────

test('an unmeasured total renders as an absence, not $0.00', () => {
  const c = summaryCells(rows(null, null));
  assert.strictEqual(c.net, null);
  assert.strictEqual(c.netText, '—', '"+$0.00" claims a measured flat day');
});

test('a measured break-even still renders its zero', () => {
  const c = summaryCells(rows(0, 0));
  assert.strictEqual(c.netText, '+$0.00', 'a real result was suppressed');
  assert.strictEqual(c.wins, 0);
});

test('an empty set has nothing to say', () => {
  assert.strictEqual(summaryCells([]).netText, '—');
  assert.strictEqual(summaryCells([]).coverage, '');
});

test('a partial window discloses how far the figures reach', () => {
  const c = summaryCells(rows(40, -10, null, 0));
  assert.strictEqual(c.netText, '+$30.00');
  assert.strictEqual(c.wins, 1, 'the unpriced row was scored as a win');
  assert.strictEqual(c.coverage, '3 priced');
});

test('a complete window says nothing extra', () => {
  // A caveat printed on every card is how a real one gets skipped.
  assert.strictEqual(summaryCells(rows(40, -10)).coverage, '');
});

test('a negative total keeps its sign', () => {
  assert.strictEqual(summaryCells(rows(-4.5, -0.25)).netText, '-$4.75');
});

test('the two paths agree on the same book', () => {
  // The whole reason they share a file: routes/sync.js reaches this by row
  // set on one path and by aggregate on the other, for the same account.
  const set = rows(10, -5, 0, null, 4);
  const s = winStats(set);
  const a = aggregateStats({
    total: s.total, scored: s.scored, wins: s.wins, net_pnl: realizedTotal(set),
  });
  assert.strictEqual(a.win_rate, s.rate * 100);
  assert.strictEqual(a.unpriced, s.unscored);
  assert.strictEqual(a.net_pnl, realizedTotal(set));
});
