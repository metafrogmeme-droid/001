'use strict';
/**
 * The last gate before an irreversible action.
 *
 * `meme_swap.build_swap` decided the terms were obtainable. This decides
 * whether they are still true at the moment a human clicks — a different
 * question, and the one that costs money when it is skipped.
 *
 * It is a separate module for the reason CLAUDE.md gives: the dashboard's
 * engine chip was built inline in six thousand lines of browser script and
 * could only be tested by grepping for the spelling of an expression. A
 * decision that authorises spending must not be reachable only through a
 * click.
 *
 * Every test here is a refusal, because on this path a wrong "no" costs a
 * retry and a wrong "yes" is unrecoverable.
 */

const test = require('node:test');
const assert = require('node:assert');

const { canSign, secondsLeft, reviewCells } =
  require('../public/js/swap-sign-model');

const WALLET = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
const NOW_MS = 1_700_000_000_000;

// The signable build is a MAINNET one, because that is the only kind there is:
// Jupiter v6 quotes mainnet only, so `signable` and `network: 'mainnet'` travel
// together and the fixture would be lying if it split them.
const build = (over = {}) => ({
  buildable: true,
  signed: false,
  broadcast: false,
  network: 'mainnet',
  signable: true,
  not_signable_reason: null,
  intent_id: 'abc123',
  user_public_key: WALLET,
  terms: {
    in_amount: '25000000', out_amount: '1234567',
    other_amount_threshold: '1210000',
    price_impact_pct: 0.0031, slippage_bps: 100,
    expires_at: NOW_MS / 1000 + 30,
  },
  ...over,
});

const ctx = (over = {}) => ({
  nowMs: NOW_MS, connectedWallet: WALLET, sentIntents: new Set(),
  mainnetConfirmed: true, ...over,
});

// ── the happy path exists, and is narrow ──────────────────────────────────

test('a fresh build with the right wallet is signable', () => {
  const r = canSign(build(), ctx());
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.code, 'ready');
});

// ── expiry is checked at CLICK time, not page load ────────────────────────

test('an expired quote is refused', () => {
  // A page open for two minutes holds terms that stopped being true ninety
  // seconds ago, and the wallet shows the stale numbers without complaint.
  const r = canSign(build(), ctx({ nowMs: NOW_MS + 31_000 }));
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.code, 'expired');
});

test('the boundary second is already too late', () => {
  const r = canSign(build(), ctx({ nowMs: NOW_MS + 30_000 }));
  assert.strictEqual(r.code, 'expired');
});

test('terms that cannot be dated are refused, not trusted', () => {
  // The damaging default here is "still valid".
  for (const bad of [null, undefined, 'soon', NaN]) {
    const r = canSign(build({ terms: { expires_at: bad } }), ctx());
    assert.strictEqual(r.ok, false, String(bad));
  }
  assert.strictEqual(canSign(build(), ctx({ nowMs: null })).code, 'expired');
});

test('secondsLeft never goes negative and never returns NaN', () => {
  assert.strictEqual(secondsLeft(build(), NOW_MS), 30);
  assert.strictEqual(secondsLeft(build(), NOW_MS + 99_000), 0);
  assert.strictEqual(secondsLeft(build({ terms: {} }), NOW_MS), 0);
  assert.strictEqual(secondsLeft(null, NOW_MS), 0);
});

// ── a broadcast is not idempotent ─────────────────────────────────────────

test('an intent already sent is refused', () => {
  // A double-click, a retry, or a back-button resubmit is recognised HERE
  // rather than discovered on-chain.
  const r = canSign(build(), ctx({ sentIntents: new Set(['abc123']) }));
  assert.strictEqual(r.code, 'already_sent');
});

test('the sent-set may be an array too', () => {
  assert.strictEqual(canSign(build(), ctx({ sentIntents: ['abc123'] })).code,
    'already_sent');
});

test('a build with no intent id is refused rather than sent unlabelled', () => {
  assert.strictEqual(canSign(build({ intent_id: null }), ctx()).code, 'no_intent');
});

test('a different intent is not blocked by an earlier one', () => {
  const r = canSign(build({ intent_id: 'zzz999' }),
    ctx({ sentIntents: new Set(['abc123']) }));
  assert.strictEqual(r.ok, true, 'a re-quote is a legitimate retry');
});

// ── the wallet must be the one it was built for ───────────────────────────

test('a wallet switched since the build is refused', () => {
  // Trivially easy in Phantom, and invisible to the page unless it looks.
  const r = canSign(build(), ctx({ connectedWallet: 'SomeOtherWallet1111111' }));
  assert.strictEqual(r.code, 'wrong_wallet');
});

test('no connected wallet is refused', () => {
  assert.strictEqual(canSign(build(), ctx({ connectedWallet: null })).code,
    'wrong_wallet');
});

// ── mainnet needs a second, deliberate act ────────────────────────────────

test('a mainnet build is refused until explicitly confirmed', () => {
  const r = canSign(build(), ctx({ mainnetConfirmed: false }));
  assert.strictEqual(r.code, 'mainnet_unconfirmed');
  assert.match(r.reason, /real funds/i);
});

test('an absent confirmation is not a confirmation', () => {
  assert.strictEqual(canSign(build(), ctx({ mainnetConfirmed: undefined })).code,
    'mainnet_unconfirmed');
});

// ── "simulate" is a statement about US, not about the transaction ─────────
//
// Jupiter v6 quotes mainnet only. A build labelled `simulate` still carries a
// real mainnet transaction, so the label cannot be read as a safety claim —
// the server says who may sign, in `signable`, and this page obeys that.

test('a review-only build is refused however fresh it is', () => {
  const r = canSign(build({
    network: 'simulate', signable: false,
    not_signable_reason: 'simulation mode: nothing will be signed or sent.',
  }), ctx());
  assert.strictEqual(r.code, 'not_signable');
  assert.match(r.reason, /nothing will be signed/);
});

test('a missing signable flag refuses — absent is not permission', () => {
  // An older server that has never heard of the field must land on "no".
  for (const bad of [undefined, null, 'true', 1, 0, false]) {
    const b = build(); b.signable = bad;
    assert.strictEqual(canSign(b, ctx()).code, 'not_signable', String(bad));
  }
});

test('the permanent refusal is reported before the transient one', () => {
  // "Expired — request a fresh quote" would send the user round a loop that
  // cannot end, because no fresh quote will be signable either.
  const b = build({ network: 'simulate', signable: false });
  const r = canSign(b, ctx({ nowMs: NOW_MS + 99_000 }));
  assert.strictEqual(r.code, 'not_signable');
  assert.doesNotMatch(r.reason, /fresh/i);
});

test('a review-only build still refuses even with a good wallet and time', () => {
  assert.strictEqual(canSign(build({ signable: false }), ctx()).ok, false);
});

// ── nothing missing ever answers yes ──────────────────────────────────────

test('an absent or unbuildable build is refused', () => {
  for (const bad of [null, undefined, {}, 'nope', 0]) {
    assert.strictEqual(canSign(bad, ctx()).ok, false, String(bad));
  }
  const r = canSign(build({ buildable: false, reason: 'no route' }), ctx());
  assert.strictEqual(r.code, 'not_buildable');
  assert.match(r.reason, /no route/);
});

test('a build already signed or broadcast is refused', () => {
  assert.strictEqual(canSign(build({ signed: true }), ctx()).code, 'already_signed');
  assert.strictEqual(canSign(build({ broadcast: true }), ctx()).code, 'already_signed');
});

test('a build with no terms is refused', () => {
  assert.strictEqual(canSign(build({ terms: null }), ctx()).code, 'no_terms');
});

test('every refusal carries a code and a human reason', () => {
  const cases = [
    [null, ctx()],
    [build({ buildable: false }), ctx()],
    [build(), ctx({ nowMs: NOW_MS + 99_000 })],
    [build(), ctx({ sentIntents: new Set(['abc123']) })],
    [build(), ctx({ connectedWallet: null })],
    [build(), ctx({ mainnetConfirmed: false })],
    [build({ signable: false }), ctx()],
  ];
  for (const [b, c] of cases) {
    const r = canSign(b, c);
    assert.strictEqual(r.ok, false);
    assert.ok(r.code && r.reason, JSON.stringify(r));
  }
});

// ── the card states what is true, and no more ─────────────────────────────

test('an unknown price impact renders as a dash, never as 0.00%', () => {
  // `0.00%` is a measurement. Printing it for a figure the quote never carried
  // is the same defect as a 0% win rate over no trades.
  const cells = reviewCells(build({ terms: { expires_at: 1 } }));
  assert.strictEqual(cells.priceImpact, '—');
  assert.strictEqual(cells.slippage, '—');
  assert.strictEqual(cells.impactClass, 'muted', 'unknown gets no warning colour');
});

test('a known-bad impact is coloured and a benign one is not', () => {
  assert.strictEqual(reviewCells(build()).impactClass, '');
  const bad = reviewCells(build({ terms: { ...build().terms, price_impact_pct: 0.09 } }));
  assert.strictEqual(bad.impactClass, 'neg');
});

test('the card always states who signs', () => {
  const cells = reviewCells(build());
  assert.match(cells.custody, /never signs and never holds your keys/);
  assert.match(cells.custody, /your own wallet/);
});

test('the card names the network and flags mainnet', () => {
  assert.strictEqual(reviewCells(build()).isMainnet, true);
  assert.strictEqual(reviewCells(build({ network: 'simulate' })).isMainnet, false);
});

test('the card marks a review-only build, and defaults to saying so', () => {
  assert.strictEqual(reviewCells(build()).reviewOnly, false);
  const ro = reviewCells(build({ signable: false, not_signable_reason: 'sim mode' }));
  assert.strictEqual(ro.reviewOnly, true);
  assert.strictEqual(ro.reviewOnlyReason, 'sim mode');
  // The dangerous face must not be the one shown on the least certain input.
  for (const junk of [null, undefined, {}, { network: 'simulate' }]) {
    assert.strictEqual(reviewCells(junk).reviewOnly, true, String(junk));
  }
});

test('the card always says the bytes are a mainnet transaction', () => {
  // `simulate` reads as a safety claim to every user who sees it, and is not
  // one. The caveat is unconditional for that reason.
  for (const net of ['simulate', 'devnet', 'mainnet']) {
    assert.match(reviewCells(build({ network: net })).networkCaveat,
      /MAINNET transaction regardless/);
  }
});

test('reviewCells survives junk without inventing figures', () => {
  for (const bad of [null, undefined, {}, { terms: null }]) {
    const c = reviewCells(bad);
    assert.strictEqual(c.inAmount, '—');
    assert.strictEqual(c.priceImpact, '—');
  }
});
