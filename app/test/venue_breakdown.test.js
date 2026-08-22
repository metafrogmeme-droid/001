'use strict';
/**
 * Phase 1: per-venue results, and the three ways this arithmetic goes wrong.
 *
 * Phase 0 taught the bot's records where a trade happened. The attribution then
 * DIED AT THE WIRE — the MySQL `trades` table had no venue column and the sync
 * sent none — so the dashboard, the surface anyone actually looks at, could
 * never show it. This phase carries it across and groups by it.
 *
 * The grouping lives in `app/lib/venue_breakdown.js` rather than in a SQL
 * GROUP BY because this is exactly the arithmetic this repository has got wrong
 * most often, and a SQL expression cannot be unit-tested against the rows that
 * break it. `track.js` already carries a comment about three public surfaces
 * answering "did this trade win?" three different ways. This is the fourth
 * surface; it is not going to be a fourth answer.
 */

const test = require('node:test');
const assert = require('node:assert');

const { venueBreakdown } = require('../lib/venue_breakdown');

const at = (list, venue) => list.find((e) => e.venue === venue);

test('it groups closed trades by the venue they happened on', () => {
  const out = venueBreakdown([
    { venue: 'bitget', pnl: 10 }, { venue: 'bitget', pnl: -4 },
    { venue: 'bybit', pnl: 7 },
  ]);
  assert.equal(out.length, 2);
  assert.equal(at(out, 'bitget').trades, 2);
  assert.equal(at(out, 'bitget').pnl, 6);
  assert.equal(at(out, 'bybit').trades, 1);
});


// ── The three rules, each of which is a real defect if broken ────────────

test('an unpriced close is not break-even and not a loss', () => {
  // `trades.pnl` is DECIMAL(14,2) with no NOT NULL, so a closed row can carry
  // no recorded P&L. `parseFloat(x) || 0` would make it a flat trade nobody
  // measured; `total - wins` would file it as a defeat. Both are on CLAUDE.md's
  // table of banned shapes.
  const out = venueBreakdown([
    { venue: 'okx', pnl: 5 }, { venue: 'okx', pnl: null }, { venue: 'okx', pnl: 'x' },
  ]);
  const e = at(out, 'okx');
  assert.equal(e.trades, 3, 'every close is still counted');
  assert.equal(e.unscored, 2);
  assert.equal(e.flat, 0, 'an unpriced close was recorded as break-even');
  assert.equal(e.losses, 0, 'an unpriced close was recorded as a loss');
  assert.equal(e.pnl, 5, 'an unpriced close moved the total');
  assert.equal(e.win_rate_pct, 100, 'the rate is over what was SCORED, not over all');
});

test('a win rate over nothing is null, not zero', () => {
  // 0% reads as "this venue loses everything" — a claim nobody measured.
  const e = at(venueBreakdown([{ venue: 'gate', pnl: null }]), 'gate');
  assert.equal(e.scored, 0);
  assert.strictEqual(e.win_rate_pct, null,
    `a venue with no scorable close reported ${e.win_rate_pct}% — that is a `
    + 'rate over zero measurements');
});

test('a venue that never traded is absent, not a row of zeroes', () => {
  // The natural implementation seeds every CONNECTED venue at zero and adds.
  // That produces a table where "never traded here" and "traded here and broke
  // even" look identical, which is the whole reason this is its own module.
  const out = venueBreakdown([{ venue: 'bitget', pnl: 1 }]);
  assert.deepStrictEqual(out.map((e) => e.venue), ['bitget']);
  assert.equal(at(out, 'bybit'), undefined);
});

test('a genuine break-even is kept apart from an unpriced one', () => {
  // 0.00 is a real, measured result: somebody priced the close and it came out
  // flat. Folding it in with "we could not read this" loses the distinction in
  // the direction that inflates the sample.
  const e = at(venueBreakdown([{ venue: 'bitget', pnl: 0 }, { venue: 'bitget', pnl: null }]),
    'bitget');
  assert.equal(e.flat, 1);
  assert.equal(e.unscored, 1);
  assert.equal(e.scored, 1);
  assert.strictEqual(e.win_rate_pct, 0, 'a measured flat IS scored, and is not a win');
});


// ── Labels ───────────────────────────────────────────────────────────────

test('an unlabelled row is bitget, not "unknown"', () => {
  // Every trade recorded before venues existed is a Bitget trade, and the
  // column is NOT NULL DEFAULT 'bitget' for the same reason. An "unknown"
  // bucket would manufacture a venue nobody traded on.
  const out = venueBreakdown([{ pnl: 1 }, { venue: '', pnl: 2 }, { venue: null, pnl: 3 }]);
  assert.deepStrictEqual(out.map((e) => e.venue), ['bitget']);
  assert.equal(at(out, 'bitget').trades, 3);
});

test('venue labels are normalised so one venue is one row', () => {
  const out = venueBreakdown([
    { venue: 'Bybit', pnl: 1 }, { venue: ' bybit ', pnl: 2 }, { venue: 'BYBIT', pnl: 3 },
  ]);
  assert.equal(out.length, 1, 'case or whitespace split one venue into several rows');
  assert.equal(at(out, 'bybit').trades, 3);
});


// ── Shape ────────────────────────────────────────────────────────────────

test('the order does not depend on the order rows arrive in', () => {
  const rows = [{ venue: 'bybit', pnl: 1 }, { venue: 'bitget', pnl: 1 },
    { venue: 'bitget', pnl: 1 }];
  const a = venueBreakdown(rows).map((e) => e.venue);
  const b = venueBreakdown([...rows].reverse()).map((e) => e.venue);
  assert.deepStrictEqual(a, b);
  assert.deepStrictEqual(a, ['bitget', 'bybit'], 'most trades first');
});

test('no rows is an empty list, not a crash and not a fabricated venue', () => {
  for (const empty of [[], null, undefined]) {
    assert.deepStrictEqual(venueBreakdown(empty), []);
  }
});

test('the wins and losses reconcile against the trade count', () => {
  // The complaint audit M9 made about the public record: a reader adding the
  // parts must arrive at the whole, or there are trades on the page that are
  // none of the categories shown.
  const e = at(venueBreakdown([
    { venue: 'okx', pnl: 3 }, { venue: 'okx', pnl: -1 },
    { venue: 'okx', pnl: 0 }, { venue: 'okx', pnl: undefined },
  ]), 'okx');
  assert.equal(e.wins + e.losses + e.flat + e.unscored, e.trades);
  assert.equal(e.scored, e.wins + e.losses + e.flat);
});


// ── It is actually reachable ─────────────────────────────────────────────

test('the private portfolio route serves it, and the public one does not', () => {
  const fs = require('fs');
  const path = require('path');
  const priv = fs.readFileSync(path.join(__dirname, '..', 'routes', 'trades.js'), 'utf8');
  const pub = fs.readFileSync(path.join(__dirname, '..', 'routes', 'track.js'), 'utf8');

  assert.match(priv, /by_venue:\s*venueBreakdown\(/,
    'the breakdown is computed and never served — #58, the module nothing calls');
  assert.match(priv, /SELECT pnl, size_usd, fees, venue FROM trades/,
    'the route groups by a column it does not select');

  // The public record is percent / ratio / count only. A per-venue dollar
  // breakdown there would be the §4 rule broken by a new feature.
  assert.ok(!/venueBreakdown/.test(pub),
    'the PUBLIC track record serves a per-venue dollar breakdown');
});

test('the wire can carry what the bot now sends', () => {
  const fs = require('fs');
  const path = require('path');
  const sync = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  const db = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');

  assert.match(db, /ALTER TABLE trades ADD COLUMN venue/,
    'existing deployments never gain the column');
  assert.ok(/venue VARCHAR\(20\) NOT NULL DEFAULT 'bitget'/.test(db),
    'the column is nullable, so history reads as "venue unknown" when it is '
    + 'known perfectly well, and NULLs go through every group-by');
  // Both halves: a closed trade and an open position.
  assert.equal((sync.match(/venueOf\(/g) || []).length, 3,
    'one of the two INSERTs does not carry the venue (or the helper is gone)');
});

test('a venue the app does not recognise is refused, not stored', () => {
  // The bot-secret channel is authenticated, but the value lands in a NOT NULL
  // column that group-bys and a payload read. An unrecognised string would show
  // on the dashboard as a venue that does not exist.
  const fs = require('fs');
  const path = require('path');
  const sync = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  assert.match(sync, /isVenue\(v\)\s*\?\s*v\s*:\s*'bitget'/,
    'venueOf does not validate against app/lib/venues.js, so the sync and the '
    + 'credential route can disagree about what a venue is');
});
