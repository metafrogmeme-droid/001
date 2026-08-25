'use strict';
/**
 * Green means the CHAIN said so — not that a column in our table is non-empty.
 *
 * WHAT WAS WRONG, AND WHY THE TIMING MATTERED
 *
 * /roots rendered its anchor state from the database:
 *
 *     r.anchor_tx ? green("⛓ anchored on Base") : "not yet anchored on-chain"
 *
 * The unanchored half was honest. The other half painted `var(--up)` — the
 * profit green — on the strength of our own field, asserting a fact about Base
 * that nothing had re-checked. `GET /api/roots/verify/:day` exists to
 * substantiate exactly that claim and was called by nothing in the UI.
 *
 * On the one page whose entire purpose is "do not take our word for it",
 * taking our word for it was the implementation. A reorg, a replaced
 * transaction, or a corrupted column would all keep showing green.
 *
 * It was found and fixed BEFORE the first anchor was ever sent. While every
 * day is unanchored the defect is invisible; it becomes a false claim the
 * moment the feature starts being used. That is the cheapest moment to catch
 * this shape and the only one where no user has yet been misled.
 *
 * FOUR STATES. `unknown` is not a synonym for either verdict — an anchored row
 * whose verification could not run is "recorded, not confirmed", and rendering
 * that as green would reintroduce the whole bug through the failure path.
 */

const test = require('node:test');
const assert = require('node:assert');

const { anchorState, anchorCell } = require('../public/js/anchor_cell.js');

const T = (k, en) => en;
const TX = '0xabc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd';

// ── the state machine ──────────────────────────────────────────────────────

test('no transaction recorded is unanchored, whatever the verdict says', () => {
  assert.equal(anchorState({ day: '2026-08-24' }, null), 'unanchored');
  assert.equal(anchorState({ anchor_tx: null }, { status: 'verified' }), 'unanchored');
  assert.equal(anchorState({ anchor_tx: '' }, { status: 'verified' }), 'unanchored');
});

test('an anchored row with NO verdict is unknown, never verified', () => {
  // This is the state every anchored row is in for the first paint, before
  // its live check comes back. If it rendered as verified, the page would
  // flash a green unverified claim on every load — and would stay there
  // permanently for anyone whose verify request fails.
  assert.equal(anchorState({ anchor_tx: TX }, null), 'unknown');
  assert.equal(anchorState({ anchor_tx: TX }, undefined), 'unknown');
  assert.equal(anchorState({ anchor_tx: TX }, 'nonsense'), 'unknown');
});

test('the chain confirming it is the only route to verified', () => {
  assert.equal(anchorState({ anchor_tx: TX }, { status: 'verified' }), 'verified');
});

test('a chain that could not be read is unknown, not a verdict', () => {
  // The server answers `unknown` when Base is unreachable. Folding that into
  // either verdict is the defect this codebase names most often: a failed read
  // rendered as a measurement.
  assert.equal(anchorState({ anchor_tx: TX }, { status: 'unknown', reason: 'rpc_down' }), 'unknown');
  assert.equal(anchorState({ anchor_tx: TX }, { status: null }), 'unknown');
});

test('a NEGATIVE resolution is a mismatch — the alarm state', () => {
  // The recorded transaction and the root disagree. That is corruption or
  // tampering, and it is the single thing this page exists to make impossible
  // to hide. Matched on the exact word because root_anchor.js's vocabulary is
  // closed: verified / mismatch / unknown, with the REASON carrying the detail.
  assert.equal(anchorState({ anchor_tx: TX }, { status: 'mismatch', reason: 'no such transaction on Base' }), 'mismatch');
  assert.equal(anchorState({ anchor_tx: TX }, { status: 'mismatch', reason: 'calldata does not equal the tagged root payload' }), 'mismatch');
});

test('an UNFAMILIAR status is unknown — neither agreement nor alarm', () => {
  // The first draft of this test asserted 'mismatch' while its own name and
  // comment argued for 'unknown'. The comment was right and the assertion was
  // wrong: a word this build has never heard of is not evidence the chain
  // agreed, and it is not evidence of tampering either. Raising the alarm over
  // a vocabulary gap is a false alarm, and false alarms are how a real one
  // comes to be ignored.
  assert.equal(anchorState({ anchor_tx: TX }, { status: 'pending_reorg_check' }), 'unknown');
  assert.equal(anchorState({ anchor_tx: TX }, { status: '' }), 'unknown');
});

// ── what actually reaches the page ─────────────────────────────────────────

test('green appears ONLY when the chain verified it', () => {
  const verified = anchorCell({ anchor_tx: TX }, { status: 'verified', block_time: '2026-08-24T12:00:00Z' }, T);
  assert.match(verified, /var\(--up\)/, 'a verified anchor is not painted as verified');

  for (const [label, verdict] of [
    ['no verdict yet', null],
    ['chain unreadable', { status: 'unknown', reason: 'rpc_down' }],
    ['mismatch', { status: 'mismatch' }],
  ]) {
    const html = anchorCell({ anchor_tx: TX }, verdict, T);
    assert.ok(!html.includes('var(--up)'),
      `"${label}" is painted with the profit green — colour is a claim, and this one is unearned:\n  ${html}`);
  }
});

test('an unanchored day says so plainly and is not coloured at all', () => {
  const html = anchorCell({ day: '2026-08-24' }, null, T);
  assert.match(html, /not yet anchored/);
  assert.ok(!html.includes('var(--up)'));
  assert.ok(!html.includes('var(--down)'));
});

test('a mismatch is loud, and links to the transaction so it can be checked', () => {
  const html = anchorCell({ anchor_tx: TX }, { status: 'mismatch' }, T);
  assert.match(html, /var\(--down\)/, 'the alarm state is not painted as an alarm');
  assert.match(html, /does NOT match/);
  assert.match(html, new RegExp(`basescan\\.org/tx/${TX}`));
});

test('an unconfirmed anchor says which of the two it is', () => {
  // "anchor recorded — chain not reachable to confirm" distinguishes it from
  // both "verified" and "there is no anchor". A bare dash here would collapse
  // three states into one.
  const html = anchorCell({ anchor_tx: TX }, { status: 'unknown' }, T);
  assert.match(html, /not reachable to confirm/);
  assert.match(html, new RegExp(`basescan\\.org/tx/${TX}`));
});

test('the block date is shown on a verified anchor', () => {
  // The block timestamp is the whole point: it is the independent upper bound
  // on when every seal in that day was minted, and it is the one number here
  // that nobody at RUNECLAW controls.
  const html = anchorCell({ anchor_tx: TX }, { status: 'verified', block_time: '2026-08-24T12:00:00Z' }, T);
  assert.match(html, /2026-08-24/);
});

test('a verified anchor with no block time still renders', () => {
  const html = anchorCell({ anchor_tx: TX }, { status: 'verified' }, T);
  assert.match(html, /var\(--up\)/);
  assert.ok(!html.includes('undefined'), 'a missing block time leaked into the page');
});

// ── injection ──────────────────────────────────────────────────────────────

test('a hostile transaction hash cannot break out of the link', () => {
  const evil = '"><script>alert(1)</script>';
  const html = anchorCell({ anchor_tx: evil }, { status: 'verified' }, T);
  assert.ok(!html.includes('<script>'), `unescaped tx hash reached the DOM:\n  ${html}`);
  assert.match(html, /&lt;script&gt;/);
});

test('a hostile block_time cannot inject either', () => {
  const html = anchorCell({ anchor_tx: TX }, { status: 'verified', block_time: '"><img src=x onerror=alert(1)>' }, T);
  assert.ok(!html.includes('<img'), `unescaped block_time reached the DOM:\n  ${html}`);
});

// ── the page uses the seam rather than reimplementing it ───────────────────

test('roots.html renders its anchor cell through this module', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'roots.html'), 'utf8');

  assert.match(html, /RCAnchorCell\.anchorCell\(/,
    'the page no longer calls the tested renderer');
  assert.match(html, /js\/anchor_cell\.js\?v=/,
    'the module is not loaded by the page, so RCAnchorCell will be undefined');
  assert.match(html, /api\/roots\/verify\//,
    'nothing asks the chain — the cell would be painted from our own column again');

  // The exact expression that was the bug. If it comes back, the page is
  // asserting an on-chain fact from a database field again.
  assert.ok(!/anchor_tx\s*\n?\s*\?\s*'<a[^']*var\(--up\)/.test(html),
    'the database-sourced green claim has been reintroduced');
});

// ── the receipt page makes the same claim, so it uses the same decider ─────

/** call.html's anchorLeg, lifted out of the page so it can be driven. */
function loadAnchorLeg() {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  const start = src.indexOf('function anchorLeg(');
  assert.ok(start > 0, 'anchorLeg is gone from call.html');
  const body = src.slice(start, src.indexOf('\n  }', start) + 4);
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  // eslint-disable-next-line no-new-func
  return new Function('esc', 'window', 'RCAnchorCell', `${body}; return anchorLeg;`)(
    esc, { RCAnchorCell: require('../public/js/anchor_cell.js') },
    require('../public/js/anchor_cell.js'));
}

const anchorLeg = loadAnchorLeg();
const A = { day: '2026-08-24', root: 'e'.repeat(64), anchor_tx: TX };

test('the receipt does not call a day anchored until the chain says so', () => {
  // It used to print "<b>This root is anchored on Base.</b>" whenever our own
  // column was non-empty — the milder instance of the /roots bug, and still
  // our database asserting a fact about a chain.
  const unconfirmed = anchorLeg(A, null);
  assert.ok(!/is anchored on Base<\/b>, confirmed/.test(unconfirmed),
    'an unconfirmed anchor is stated as settled fact');
  assert.match(unconfirmed, /could\s+not be reached to confirm/,
    'the unconfirmed state does not say what it is');
});

test('the receipt states it plainly once the chain confirms', () => {
  const html = anchorLeg(A, { status: 'verified', block_time: '2026-08-24T12:00:00Z' });
  assert.match(html, /anchored on Base<\/b>, confirmed against the/);
  assert.match(html, /2026-08-24 12:00:00 UTC/);
});

test('the receipt raises the alarm on a mismatch', () => {
  const html = anchorLeg(A, { status: 'mismatch' });
  assert.match(html, /does NOT match this root/);
  assert.match(html, /var\(--down\)/);
  assert.match(html, /unproven/);
});

test('an unanchored day on the receipt is unchanged and honest', () => {
  const html = anchorLeg({ day: A.day, root: A.root, anchor_tx: null }, null);
  assert.match(html, /not yet anchored on-chain/);
  assert.ok(!html.includes('var(--down)'));
});

test('every receipt state still hands the reader the payload to compare', () => {
  // The page's real strength: it shows the exact RCROOT1 string so a human can
  // check the calldata themselves rather than trusting either of us.
  for (const verdict of [null, { status: 'verified' }, { status: 'mismatch' }, { status: 'unknown' }]) {
    const html = anchorLeg(A, verdict);
    assert.match(html, /RCROOT1:2026-08-24:/, `the compare payload is missing for ${JSON.stringify(verdict)}`);
  }
});
