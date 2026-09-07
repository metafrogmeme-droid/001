'use strict';
/**
 * A tax report must not invent a realized gain it could not read.
 *
 * `trades.pnl` is `DECIMAL(14,2)` and NULLABLE. `routes/tax.js` selects CLOSED
 * rows unfiltered, so a close the engine never managed to price is an ordinary
 * input here — and `num()` turned it into a realized gain of exactly `0.00`,
 * which `summarize` then folded into `net_gain_loss` and `toCsv` published on a
 * Form-8949-friendly export. Of every place in this repo that renders an absent
 * field as a confident number, this is the one somebody files a return from.
 *
 * The rule is CLAUDE.md's, and the shape is the first row of its table:
 * `float(x or 0)` — "unreadable is break-even".
 */

const test = require('node:test');
const assert = require('node:assert');

const { classifyDisposal, summarize, buildReport, toCsv, numOrNull, UNPRICED_CELL }
  = require('../lib/tax');

const OPENED = '2026-01-10T00:00:00.000Z';
const CLOSED = '2026-02-10T00:00:00.000Z';

const trade = (over = {}) => ({
  symbol: 'BTC/USDT', direction: 'LONG', size_usd: 1000, pnl: 50, fees: 2,
  opened_at: OPENED, closed_at: CLOSED, ...over,
});

// ── the coercion itself ──────────────────────────────────────────────────

test('numOrNull keeps unreadable apart from zero', () => {
  assert.strictEqual(numOrNull(0), 0, '0 is a real, measured break-even');
  assert.strictEqual(numOrNull('0.00'), 0, 'the driver returns DECIMAL as a string');
  assert.strictEqual(numOrNull(-12.5), -12.5);
  assert.strictEqual(numOrNull(null), null);
  assert.strictEqual(numOrNull(undefined), null);
  // `Number('')` is 0 AND is finite, so an empty string — what several
  // serialisers emit for SQL NULL — slips past a bare isFinite check and
  // scores as break-even. Same trap `pnlClass` records on the client.
  assert.strictEqual(numOrNull(''), null);
  assert.strictEqual(numOrNull('   '), null);
  assert.strictEqual(numOrNull('abc'), null);
  assert.strictEqual(numOrNull(NaN), null);
  assert.strictEqual(numOrNull(Infinity), null);
});

// ── one row ──────────────────────────────────────────────────────────────

test('an unpriced close is not a $0.00 disposal', () => {
  const d = classifyDisposal(trade({ pnl: null }));
  assert.strictEqual(d.gain_loss, null,
    'a trade with no booked P&L was reported as an exactly break-even disposal');
  assert.strictEqual(d.priced, false);
});

test('proceeds are not derived from a gain we do not have', () => {
  // proceeds = basis + gain. With gain unreadable, publishing `basis` as
  // proceeds would put the same number on the form under a second heading and
  // make the row look complete.
  const d = classifyDisposal(trade({ pnl: null }));
  assert.strictEqual(d.proceeds, null);
  assert.strictEqual(d.cost_basis, 1000, 'size_usd is NOT NULL and stays readable');
});

test('a genuine break-even keeps its zero', () => {
  // THE CHECK THAT STOPS THE FIX FROM OVERREACHING. 0.0 is falsy and 0.0 is a
  // real, measured, break-even close (CLAUDE.md). Rendering it as "unpriced"
  // would be the same defect pointed the other way.
  const d = classifyDisposal(trade({ pnl: 0 }));
  assert.strictEqual(d.gain_loss, 0);
  assert.strictEqual(d.priced, true);
  assert.strictEqual(d.proceeds, 1000);
});

test('a loss is still a loss', () => {
  const d = classifyDisposal(trade({ pnl: -250 }));
  assert.strictEqual(d.gain_loss, -250);
  assert.strictEqual(d.proceeds, 750);
  assert.strictEqual(d.priced, true);
});

// ── the totals ───────────────────────────────────────────────────────────

test('an unpriced row is excluded from every money total', () => {
  const rows = [
    classifyDisposal(trade({ pnl: 100 })),
    classifyDisposal(trade({ pnl: null })),
    classifyDisposal(trade({ pnl: -40 })),
  ];
  const { totals } = summarize(rows);
  assert.strictEqual(totals.disposals, 3, 'the disposal happened and must be counted');
  assert.strictEqual(totals.unpriced, 1);
  assert.strictEqual(totals.net_gain_loss, 60, '100 + (-40); the null must add nothing');
  // `+ null` is `+ 0` in JavaScript, so leaving the accumulators to run would
  // have produced this same 60 — and a proceeds/basis total silently covering
  // a row with no gain behind it. Assert the money totals cover ONLY the
  // priced rows, which is the claim the UI makes about them.
  assert.strictEqual(totals.proceeds, 1100 + 960);
  assert.strictEqual(totals.cost_basis, 2000);
  assert.strictEqual(totals.fees, 4, 'the unpriced row contributes no fees either');
});

test('an unpriced row is scored neither a win nor a loss', () => {
  // `losses = len(all) - wins` is on CLAUDE.md's table: it makes every
  // unscorable row a loss. Both counters must simply skip it.
  const { totals } = summarize([
    classifyDisposal(trade({ pnl: 100 })),
    classifyDisposal(trade({ pnl: null })),
  ]);
  assert.strictEqual(totals.gains, 1);
  assert.strictEqual(totals.losses, 0);
  assert.strictEqual(totals.gains + totals.losses + totals.unpriced, totals.disposals);
});

test('a book that could not be priced at all reports zero disposals scored', () => {
  const { totals } = summarize([
    classifyDisposal(trade({ pnl: null })),
    classifyDisposal(trade({ pnl: undefined })),
  ]);
  assert.strictEqual(totals.disposals, 2);
  assert.strictEqual(totals.unpriced, 2);
  assert.strictEqual(totals.net_gain_loss, 0,
    'the accumulator is untouched — and `unpriced === disposals` is what tells '
    + 'a reader this 0 is an empty sum rather than a measured flat year');
  assert.strictEqual(totals.gains, 0);
  assert.strictEqual(totals.losses, 0);
});

test('per-year buckets carry the count too', () => {
  const { years } = summarize([
    classifyDisposal(trade({ pnl: 10 })),
    classifyDisposal(trade({ pnl: null })),
  ]);
  assert.strictEqual(years.length, 1);
  assert.strictEqual(years[0].unpriced, 1);
  assert.strictEqual(years[0].net_gain_loss, 10);
});

// ── the export, which is where a spreadsheet gets to re-zero it ──────────

test('the CSV says UNPRICED rather than leaving a summable blank', () => {
  const csv = toCsv([classifyDisposal(trade({ pnl: null }))]);
  const line = csv.trim().split('\n')[1];
  const cells = line.split(',');
  // Header: Symbol, Direction, Acquired, Sold, Proceeds, Basis, Fees, Gain/Loss, Days, Term
  assert.strictEqual(cells[4], UNPRICED_CELL, 'proceeds');
  assert.strictEqual(cells[7], UNPRICED_CELL, 'gain/loss');
  assert.strictEqual(cells[5], '1000', 'the basis IS readable and stays a number');
  // A blank is what SUM() treats as nothing, which would rebuild the $0.00 in
  // the reader's spreadsheet after the server stopped sending it.
  assert.ok(!/,,/.test(line.replace(/^[^,]*,/, '')),
    `an empty money cell survived in the export: ${line}`);
});

test('a priced row still exports plain numbers', () => {
  const csv = toCsv([classifyDisposal(trade({ pnl: -40 }))]);
  const cells = csv.trim().split('\n')[1].split(',');
  assert.strictEqual(cells[4], '960');
  assert.strictEqual(cells[7], '-40');
});

// ── end to end, the shape the route actually returns ─────────────────────

test('buildReport surfaces the unpriced count on totals', () => {
  const r = buildReport([
    trade({ pnl: 100 }),
    trade({ pnl: null }),
  ]);
  assert.strictEqual(r.totals.disposals, 2);
  assert.strictEqual(r.totals.unpriced, 1);
  assert.strictEqual(r.totals.net_gain_loss, 100);
  assert.strictEqual(r.disposals.find((d) => !d.priced).gain_loss, null);
});

test('the disclaimer still travels with the report', () => {
  // The unpriced rows are a caveat ON TOP of the standing one, never instead.
  const r = buildReport([trade({ pnl: null })]);
  assert.match(r.disclaimer, /not tax advice/i);
});

test('an unparseable close date still drops the row, as before', () => {
  // Unchanged behaviour, pinned because this fix touched the same function:
  // a disposal with no sale date has no tax year to sit in.
  const r = buildReport([trade({ closed_at: null })]);
  assert.strictEqual(r.disposals.length, 0);
});
