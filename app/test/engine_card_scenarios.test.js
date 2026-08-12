'use strict';
/**
 * Plant the payload, read the card. Replaces engine_card_absent.test.js.
 *
 * THE ORIGINAL DEFECT (2026-07-28). The engine card read
 *
 *     ENGINE EQUITY $378.88   NET PNL +0.00   WIN RATE 0.0%   OPEN 0
 *
 * off `{"equity":378.88,"net_pnl":0,"win_rate":0,"total_trades":0}`, while
 * Telegram reported -$10.74 over 95 trades at 57% for the same account.
 * total_trades: 0 — nothing was measured, and the card printed two zeros in
 * the same typeface as the equity beside them, which was real.
 *
 * WHY THIS FILE REPLACES THE ONE THAT PINNED THE FIX. The fix was a
 * `_haveTrades(cb)` guard, and its test asserted the guard's exact spelling
 * inside dashboard.js:
 *
 *     assert.match(row, /_haveTrades\(cb\) \? fmt\(cb\.win_rate, 1\) \+ '%' : '—'/);
 *
 * That passes for a card that is right and for a card that is wrong in the
 * one way the guard cannot see: `_haveTrades` asks "is there a record", and a
 * figure needs "was THIS figure measured". They came apart as soon as the bot
 * learned to send `win_rate: null` for a book of twelve closes none of which
 * carry a recorded P&L — twelve passes _haveTrades, and `fmt(null,1) + '%'`
 * renders **--%** on the operator's dashboard. The regex would still match.
 *
 * So the decision moved into a pure model and the assertions moved to what
 * the card SAYS, the way engine_status_scenarios.test.js already does for the
 * chip beside it. MUST_SAY / MUST_NOT_SAY, with a planted red herring.
 */
const test = require('node:test');
const assert = require('node:assert');

const { engineCardCells } = require('../public/js/engine-card-model.js');

const cells = (cb) => {
  const c = engineCardCells(cb);
  return { c, blob: `${c.equity.text} ${c.netPnl.text} ${c.winRate.text} ${c.open.text} ${c.note}` };
};

// (name, payload, MUST include, MUST NOT include)
const SCENARIOS = [
  ['the original: a real balance with an empty book states no result',
    { equity: 378.88, net_pnl: 0, win_rate: 0, total_trades: 0, open_count: 0 },
    ['$378.88', '—', 'No closed trades yet'], ['+0.00', '0.0%']],

  // THE RED HERRING, and the reason this file exists. Twelve closes is a real
  // record — _haveTrades says yes — and not one of them can be priced. The
  // invited conclusion is "12 trades, so the figures beside it are measured".
  ['twelve closes none of which can be priced is not a 0% win rate',
    { equity: 884.31, net_pnl: null, win_rate: null, total_trades: 12,
      unpriced_trades: 12, open_count: 1 },
    ['$884.31', '—', '12 of 12 closes carry no recorded P&L'],
    ['0.0%', '+0.00', '--%', '--']],

  ['a partially priced book says how far the figures reach',
    { equity: 500, net_pnl: 42.5, win_rate: 60, total_trades: 10,
      unpriced_trades: 4, open_count: 0 },
    ['+42.50', '60.0%', '4 of 10 closes carry no recorded P&L'], ['—']],

  ['an unreadable record says so instead of showing dashes with no reason',
    { equity: 884.31, net_pnl: null, win_rate: null, total_trades: 0,
      record_unreadable: true, open_count: 0 },
    ['could not be read', 'these are not zeros'], ['0.0%', '+0.00']],

  ['a measured break-even is printed, not hidden',
    { equity: 1000, net_pnl: 0, win_rate: 0, total_trades: 8, open_count: 0 },
    ['+0.00', '0.0%'], ['—']],

  ['an unreadable equity does not blank the measured statistics',
    { equity: null, net_pnl: -12.34, win_rate: 44.4, total_trades: 9, open_count: 2 },
    ['—', '-12.34', '44.4%'], ['$0.00', '+0.00']],
];

for (const [name, cb, must, mustNot] of SCENARIOS) {
  test(name, () => {
    const { blob } = cells(cb);
    for (const phrase of must) {
      assert.ok(blob.includes(phrase), `missing ${JSON.stringify(phrase)} in: ${blob}`);
    }
    for (const phrase of mustNot) {
      assert.ok(!blob.includes(phrase), `wrongly claimed ${JSON.stringify(phrase)} in: ${blob}`);
    }
  });
}

test('colour is a claim: an unmeasured net carries none', () => {
  const { c } = cells({ equity: 100, net_pnl: null, win_rate: null,
                        total_trades: 5, open_count: 0 });
  assert.strictEqual(c.netPnl.cls, '',
    'green on a number that does not exist reads as "not down"');
});

test('a measured zero is green, because break-even is not a loss', () => {
  const { c } = cells({ equity: 100, net_pnl: 0, win_rate: 0,
                        total_trades: 5, open_count: 0 });
  assert.strictEqual(c.netPnl.cls, 'pos');
  assert.strictEqual(c.netPnl.text, '+0.00');
});

test('a measured loss is red', () => {
  const { c } = cells({ equity: 100, net_pnl: -5, win_rate: 20,
                        total_trades: 5, open_count: 0 });
  assert.strictEqual(c.netPnl.cls, 'neg');
});

test('equity is a live balance and is never gated on the trade count', () => {
  // The distinction the first fix got right and which must survive: a funded
  // account that has not traded still HAS a balance.
  const { c } = cells({ equity: 250.5, total_trades: 0, open_count: 0 });
  assert.strictEqual(c.equity.text, '$250.50');
  assert.strictEqual(c.netPnl.text, '—');
});

test('a non-finite figure is treated as absent, not rendered', () => {
  // NaN and Infinity reach here through JSON round-trips and bad divisions;
  // `isFinite` is the difference between "—" and "NaN%" on the dashboard.
  const { c } = cells({ equity: 100, net_pnl: NaN, win_rate: Infinity,
                        total_trades: 4, open_count: 0 });
  assert.strictEqual(c.netPnl.text, '—');
  assert.strictEqual(c.winRate.text, '—');
});

test('a missing payload does not throw and claims nothing', () => {
  for (const bad of [null, undefined, {}]) {
    const c = engineCardCells(bad);
    assert.strictEqual(c.netPnl.text, '—');
    assert.strictEqual(c.winRate.text, '—');
    assert.strictEqual(c.equity.text, '—');
    assert.strictEqual(c.open.text, '—');
  }
});

test('the note is silent on a complete, healthy card', () => {
  // A caveat printed on every card is how a real one gets skipped — the same
  // reason win_rate.py's coverage_note returns '' on a full pass.
  const { c } = cells({ equity: 900, net_pnl: 120.4, win_rate: 61.5,
                        total_trades: 26, unpriced_trades: 0, open_count: 3 });
  assert.strictEqual(c.note, '');
});

test('the dashboard actually calls the model', () => {
  // The model can be perfect and unreached — the exact failure the Telegram
  // adoption card shipped. This is wiring, not behaviour, and it is the one
  // thing a scenario test cannot see.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(src, /EngineCardModel \|\| \{\}\)\.engineCardCells/,
    'dashboard.js must render the engine card through the model');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(html, /engine-card-model\.js\?v=/,
    'the model must be loaded by the page, with a cache-busting version');
});
