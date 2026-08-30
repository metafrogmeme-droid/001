'use strict';
/**
 * An unpriced close could fake a deposit and hide a real loss.
 *
 * `segmentByCapitalEvents` decides whether an equity step is a CAPITAL EVENT
 * (deposit, withdrawal, paper→live switch) or a step trading explains. It read
 * the closes in the window with:
 *
 *     pnl: parseFloat(t.pnl) || 0
 *
 * `trades.pnl` is `DECIMAL(14,2)` and NULLABLE — routes/sync.js reports
 * `unpriced_trades` for precisely this state — so an unpriced close
 * contributed nothing, the equity it really moved read as UNEXPLAINED, and a
 * step past the 30%/$25 threshold was classified a capital event and SPLIT
 * OUT of the series.
 *
 * `segmentedMaxDrawdownPct` measures only WITHIN segments. So the sequence
 * was: a real trading loss, its close unpriced, reported as a deposit, and
 * excluded from max drawdown entirely.
 *
 *     10,000 -> 6,000 with the close unpriced
 *     pnlBetween 0, unexplained 4,000 > max(3,000, 25) -> split
 *     max drawdown: 0%          (the truth is 40%)
 *
 * On a RISK metric the reassuring direction is the dangerous one, so an
 * unreadable close now means "cannot classify" rather than "capital event":
 * the step stays in the segment and the drawdown keeps it.
 *
 * THE RED HERRINGS, both asserted below: a genuine capital event with no
 * closes in the window, and one whose closes are all priced. Both must still
 * split. A fix that stopped splitting would replace a hidden loss with a
 * fabricated one — the same defect pointed the other way, and the reason the
 * existing tests in track_drawdown.test.js still pass unchanged.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const {
  segmentByCapitalEvents, segmentedMaxDrawdownPct,
} = require('../lib/equity_basis');

const pt = (t, equity) => ({ t, equity });
const close = (t, pnl) => ({ closed_at: new Date(t).toISOString(), pnl });

// A 40% drop across one step: 10,000 -> 6,000.
const DROP = [pt(1000, 10000), pt(2000, 6000)];

test('an unpriced close does not turn a trading loss into a deposit', () => {
  const segs = segmentByCapitalEvents(DROP, [close(1500, null)]);
  assert.equal(segs.length, 1,
    'the step was split out as a capital event on a close nobody could price');
  assert.equal(segmentedMaxDrawdownPct(DROP, [close(1500, null)]), 40,
    'a real 40% drawdown was reported as 0% because its close was unpriced');
});

test('the same step with the close PRICED behaves exactly as before', () => {
  const segs = segmentByCapitalEvents(DROP, [close(1500, -4000)]);
  assert.equal(segs.length, 1, 'a step explained by trade PnL must not split');
  assert.equal(segmentedMaxDrawdownPct(DROP, [close(1500, -4000)]), 40);
});

test('RED HERRING: a real capital event with no closes still splits', () => {
  // Nothing traded, and the equity moved anyway — that IS a deposit.
  const segs = segmentByCapitalEvents(DROP, []);
  assert.equal(segs.length, 2,
    'capital-event detection was disabled rather than made honest');
  assert.equal(segmentedMaxDrawdownPct(DROP, []), 0,
    'a genuine capital event is being counted as drawdown');
});

test('RED HERRING: a real capital event with a priced close still splits', () => {
  // A small priced trade cannot explain a 4,000 step.
  const trades = [close(1500, -50)];
  assert.equal(segmentByCapitalEvents(DROP, trades).length, 2,
    'a priced close must not suppress capital-event detection');
  assert.equal(segmentedMaxDrawdownPct(DROP, trades), 0);
});

test('an unreadable pnl of any flavour counts as unreadable', () => {
  // undefined, empty string and a non-numeric string are all "not a number",
  // and `parseFloat` answers NaN for each — which `|| 0` then made a zero.
  for (const bad of [undefined, '', 'n/a', NaN]) {
    assert.equal(segmentByCapitalEvents(DROP, [close(1500, bad)]).length, 1,
      `pnl=${JSON.stringify(bad)} was treated as a measured zero`);
  }
});

test('a measured zero is a reading and does not suppress detection', () => {
  // 0.00 is a real, break-even close. It explains nothing about a 4,000 step,
  // so the step is still a capital event. Muting every zero would be the
  // mirror defect.
  assert.equal(segmentByCapitalEvents(DROP, [close(1500, 0)]).length, 2,
    'a genuine break-even close was treated as unreadable');
});

test('one unreadable close among priced ones is enough to stop the claim', () => {
  // The window cannot be reconciled if any part of it is unknown — a partial
  // reconciliation is the "partial total printed as whole" shape.
  const trades = [close(1400, -50), close(1500, null), close(1600, -25)];
  assert.equal(segmentByCapitalEvents(DROP, trades).length, 1,
    'the window was reconciled from the priced subset alone');
});
