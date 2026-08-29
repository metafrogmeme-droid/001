'use strict';
/**
 * The basis and the metrics disagreed about which trades exist.
 *
 * `/api/reputation`, `/api/trades/breakdown` and `/api/guardian/readiness`
 * each derived the starting-equity denominator inline and identically:
 *
 *     const net = rows.reduce((a, r) => a + (parseFloat(r.pnl) || 0), 0);
 *     const startEquity = Math.max(parseFloat(snap[0].equity) - net, 1);
 *
 * That sums over ALL rows. But `computeReputation` and `computePerformance`
 * both open with `.filter(t => t.pnl != null && Number.isFinite(...))` — they
 * score only the PRICED rows. So the denominator counted an unpriced close as
 * moving equity by zero while the account's real equity already included
 * whatever it actually did, leaving `startEquity` wrong by exactly that amount
 * and every percentage derived from it quietly biased.
 *
 * `trades.js` was inconsistent with ITSELF: line 40 already used the honest
 * `realizedTotal()` for the displayed net, and line 201 hand-rolled the banned
 * shape for the basis.
 *
 * NONE OF THAT IS FIXABLE. The unpriced pnl is genuinely unknown and no
 * arithmetic recovers it. It is DECLARABLE, which is the whole difference
 * between a number a reader can weigh and one they cannot — `sync.js` has
 * carried `scored_trades`/`unpriced_trades` for the same reason.
 *
 * The `10000` fallback is declared too: a book with no equity snapshot has no
 * measured basis at all, so every percentage against it is a ratio to a
 * number nobody observed.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { deriveStartEquity } = require('../lib/equity_basis');

const t = (pnl) => ({ pnl });

test('a fully priced book against a real snapshot is not an estimate', () => {
  // equity 10,060 after +100 and -40 => the book started at 10,000.
  const b = deriveStartEquity([t('100'), t('-40')], '10060');
  assert.strictEqual(b.start_equity, 10000);
  assert.strictEqual(b.basis_source, 'equity_snapshot');
  assert.strictEqual(b.scored_trades, 2);
  assert.strictEqual(b.unpriced_trades, 0);
  assert.strictEqual(b.basis_is_estimate, false);
});

test('an unpriced close makes the basis an estimate and says how many', () => {
  const b = deriveStartEquity([t('100'), t(null)], '10060');
  assert.strictEqual(b.unpriced_trades, 1);
  assert.strictEqual(b.scored_trades, 1);
  assert.strictEqual(b.basis_is_estimate, true,
    'a basis derived without a trade it could not read claimed to be measured');
});

test('no equity snapshot is an estimate, not a measured 10,000', () => {
  // The neutral default is a guess. Presenting it as a basis makes every
  // percentage a ratio to a number nobody observed.
  const b = deriveStartEquity([t('100')], null);
  assert.strictEqual(b.start_equity, 10000);
  assert.strictEqual(b.basis_source, 'default');
  assert.strictEqual(b.basis_is_estimate, true);
});

test('the basis agrees with the readers about which rows count', () => {
  // computeReputation/computePerformance filter to readable pnl. The basis
  // must sum the SAME set, or the denominator and numerator describe
  // different books.
  const rows = [t('100'), t(null), t('-40'), t('')];
  const b = deriveStartEquity(rows, '10060');
  assert.strictEqual(b.scored_trades, 2);
  assert.strictEqual(b.unpriced_trades, 2);
  // 10,060 - (100 - 40) = 10,000 — the unreadable pair contributes nothing to
  // the sum AND is reported, rather than silently contributing zero.
  assert.strictEqual(b.start_equity, 10000);
});

test('every flavour of unreadable pnl counts as unpriced', () => {
  for (const bad of [null, undefined, '', 'n/a', NaN]) {
    const b = deriveStartEquity([t(bad)], '10000');
    assert.strictEqual(b.unpriced_trades, 1, `pnl=${JSON.stringify(bad)}`);
    assert.strictEqual(b.scored_trades, 0);
  }
});

test('a measured zero is priced, not unpriced', () => {
  // 0.00 is a real break-even close. Counting it as unreadable would be the
  // mirror defect and would understate coverage.
  const b = deriveStartEquity([t('0')], '10000');
  assert.strictEqual(b.scored_trades, 1);
  assert.strictEqual(b.unpriced_trades, 0);
  assert.strictEqual(b.basis_is_estimate, false);
});

test('the basis never goes below 1 (it is a denominator)', () => {
  // A book that lost more than its current equity would otherwise produce a
  // zero or negative denominator.
  const b = deriveStartEquity([t('100000')], '10');
  assert.ok(b.start_equity >= 1, `start_equity was ${b.start_equity}`);
});

test('an empty book is still an honest estimate against its snapshot', () => {
  const b = deriveStartEquity([], '5000');
  assert.strictEqual(b.start_equity, 5000);
  assert.strictEqual(b.scored_trades, 0);
  assert.strictEqual(b.unpriced_trades, 0);
  assert.strictEqual(b.basis_is_estimate, false,
    'nothing was unreadable and the snapshot was real');
});

test('all three routes derive the basis through the shared helper', () => {
  // The three inline copies are why they drifted from the readers in the
  // first place. Structural, because the defect is duplication.
  const fs = require('node:fs');
  const path = require('node:path');
  for (const f of ['reputation.js', 'trades.js', 'guardian_readiness.js']) {
    const src = fs.readFileSync(path.join(__dirname, '..', 'routes', f), 'utf8');
    const code = src.replace(/\/\/.*$/gm, '');
    assert.ok(code.includes('deriveStartEquity'),
      `${f} no longer uses the shared basis helper`);
    assert.ok(!/reduce\(\(a, r\) => a \+ \(parseFloat\(r\.pnl\) \|\| 0\), 0\)/.test(code),
      `${f} hand-rolls the basis sum again — it will drift from the readers`);
  }
});

// ── The declaration has to REACH somebody ──────────────────────────────────
//
// The first version of this work put `basis` on three JSON payloads and
// rendered it nowhere. That is #999 exactly: present, correct, unreachable,
// and indistinguishable from never having been built. `app/public/js/
// basis-note.js` turns the coverage into the sentence a reader sees, and the
// tests below cover both halves — what it says, and that it is wired.

const { coverageNote, isEstimate } = require('../public/js/basis-note');

test('a clean, fully priced window says nothing', () => {
  // A caveat printed every time is how a real one gets skipped. Same rule as
  // VenueRows.venueFootnote.
  const note = coverageNote({
    basis_source: 'equity_snapshot', scored_trades: 12,
    unpriced_trades: 0, basis_is_estimate: false,
  });
  assert.strictEqual(note, '');
});

test('an unpriced close is named, with the denominator it came from', () => {
  const note = coverageNote({
    basis_source: 'equity_snapshot', scored_trades: 8,
    unpriced_trades: 2, basis_is_estimate: true,
  });
  assert.match(note, /2 of 10 closed trades/);
  assert.match(note, /no recorded P&L/);
  assert.match(note, /estimated/,
    'the reader has to be told the percentages inherit the gap');
});

test('one unpriced close is singular', () => {
  assert.match(coverageNote({ scored_trades: 3, unpriced_trades: 1 }),
    /1 of 4 closed trade had/);
});

test('a defaulted basis says the percentages are ratios to a guess', () => {
  const note = coverageNote({
    basis_source: 'default', scored_trades: 4,
    unpriced_trades: 0, basis_is_estimate: true,
  });
  assert.match(note, /No equity snapshot/);
  assert.match(note, /default starting balance/);
  assert.ok(!/closed trade/.test(note),
    'nothing was unpriced; inventing a coverage gap is the mirror defect');
});

test('both faults at once are both reported', () => {
  const note = coverageNote({
    basis_source: 'default', scored_trades: 1,
    unpriced_trades: 2, basis_is_estimate: true,
  });
  assert.match(note, /No equity snapshot/);
  assert.match(note, /2 of 3 closed trades/);
});

test('an unreadable coverage report invents no caveat and no all-clear', () => {
  for (const bad of [null, undefined, 'nope', 7, []]) {
    assert.strictEqual(coverageNote(bad), '', JSON.stringify(bad));
    assert.strictEqual(isEstimate(bad), false, JSON.stringify(bad));
  }
});

test('isEstimate reads the flag the route computed, not its own re-derivation', () => {
  // The API and the screen must not be able to disagree about whether a
  // number is measured — two copies of one classifier is how the auth path
  // diverged from itself.
  assert.strictEqual(isEstimate({ basis_is_estimate: true, unpriced_trades: 0 }), true);
  assert.strictEqual(isEstimate({ basis_is_estimate: false, unpriced_trades: 5 }), false);
  // Absent flag: fall back to the counts rather than answering "measured".
  assert.strictEqual(isEstimate({ unpriced_trades: 5 }), true);
  assert.strictEqual(isEstimate({ basis_source: 'default' }), true);
  assert.strictEqual(isEstimate({ basis_source: 'equity_snapshot' }), false);
});

test('the reputation card renders the note and the page loads the script', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.ok(dash.includes('BasisNote.coverageNote'),
    'the panel stopped asking for the coverage note');
  assert.ok(/<\/tbody><\/table><\/div>\$\{basisNote\(data\)\}/.test(dash),
    'the note is no longer rendered under the metrics table it qualifies');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.ok(html.includes('/js/basis-note.js'),
    'the page never loads basis-note.js, so BasisNote is undefined at runtime '
    + 'and the note silently renders as nothing — present code, zero renders');
});

test('the reputation payload carries the coverage and no dollar figure', () => {
  // reputation.js opens by saying it is "dollar-free (every metric is a ratio)
  // ... stays shareable without exposing amounts". The route is where a dollar
  // amount would get bolted onto it, so start_equity is destructured off.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'reputation.js'), 'utf8');
  const code = src.replace(/\/\/.*$/gm, '');
  assert.ok(/const \{ start_equity, \.\.\.coverage \} = deriveStartEquity/.test(code),
    'the reputation route no longer separates the basis amount from its coverage');
  assert.ok(/coverage \}\)/.test(code), 'the coverage stopped reaching the payload');
  assert.ok(!/start_equity,?\s*\n?\s*\}\);?\s*$/m.test(code.split('res.json')[1] || ''),
    'a dollar basis is being returned on the dollar-free payload');
});
