// The fallback venue's terms must match the primary venue's, or say why not.
//
// THE DEFECT
//
// `smithii.config.json` opened with "Mirrors the Metaplex Genesis params so the
// two are interchangeable." Nothing checked that, and three values diverged:
//
//   refundIfSoftCapMissed   true   vs  false
//   autoListPercentOfRaise  60     vs  6667 bps (66.67%)
//   lpLockMonths            12     vs  never-claim (permanent)
//
// The refund one is the serious one. `metaplex-genesis.config.json` carries a
// finding proven on devnet — there is no refund instruction for a V2 presale,
// withdrawPresaleV1 and withdrawUnsoldPresaleV1 both reject a V2 genesis
// account with 0x2f — and `derivePresaleParams()` THROWS if the flag is set, so
// the primary venue could not publish that promise even by accident. The
// fallback published it by default, and nothing connected the two.
//
// WHY THE MISMATCH SURVIVED A REVIEW THAT WAS LOOKING FOR IT
//
// docs/TOKEN_SECURITY_AUDIT.md said "Sale economics agree field by field across
// smithii.config.json, metaplex-genesis.config.json and the roadmap" and then
// listed the fields: allocation, caps, contribution bounds, phase lengths,
// vesting. Every one of those does agree. The sentence is true about what it
// enumerates and false about what it claims, because the enumeration is the
// evidence and it stops one field short. A list of things that match cannot
// establish that nothing mismatches — only a comparison over the full key set
// can, which is what this file does.
//
// NEITHER FILE IS READ BY CODE. An operator transcribes the values into
// Smithii's UI (RUNBOOK Path B), so there is no runtime that could reject a
// wrong one. This test is the only thing between a bad value and a published
// sale term.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (f) => JSON.parse(fs.readFileSync(path.join(HERE, f), 'utf8'));

const genesis = read('metaplex-genesis.config.json');
const smithii = read('smithii.config.json');

const HOUR_MS = 3600 * 1000;
const hoursBetween = (a, b) => (Date.parse(b) - Date.parse(a)) / HOUR_MS;

// Every term a buyer could read off either venue: a label, both sides, and the
// smithii key(s) it covers. The fourth element is what makes
// `every_published_term_is_compared` below exact — a field added to
// smithii.config.json and compared nowhere is a term the fallback publishes
// alone, which is precisely how the original three divergences survived.
const SHARED = [
  ['token.decimals', () => genesis.token.decimals, () => smithii.token.decimals, ['token.decimals']],
  ['token.symbol', () => genesis.token.symbol, () => smithii.token.symbol, ['token.symbol']],
  ['sale.presaleAllocation', () => genesis.sale.presaleAllocation, () => smithii.sale.presaleAllocation, ['sale.presaleAllocation']],
  ['sale.softCapSol', () => genesis.sale.softCapSol, () => smithii.sale.softCapSol, ['sale.softCapSol']],
  ['sale.hardCapSol', () => genesis.sale.hardCapSol, () => smithii.sale.hardCapSol, ['sale.hardCapSol']],
  ['sale.minContributionSol', () => genesis.sale.minContributionSol, () => smithii.sale.minContributionSol, ['sale.minContributionSol']],
  ['sale.maxContributionSol', () => genesis.sale.maxContributionSol, () => smithii.sale.maxContributionSol, ['sale.maxContributionSol']],
  ['sale.refundIfSoftCapMissed', () => genesis.sale.refundIfSoftCapMissed, () => smithii.sale.refundIfSoftCapMissed, ['sale.refundIfSoftCapMissed']],
  ['vesting.tgeUnlockPercent', () => genesis.vesting.tgeUnlockPercent, () => smithii.vesting.tgeUnlockPercent, ['vesting.tgeUnlockPercent']],
  ['vesting.linearMonthsAfterTge', () => genesis.vesting.linearMonthsAfterTge, () => smithii.vesting.linearMonthsAfterTge, ['vesting.linearMonthsAfterTge']],
  ['liquidity.dex', () => genesis.liquidity.dex, () => smithii.liquidity.dex, ['liquidity.dex']],
  ['liquidity.lpLock', () => genesis.liquidity.lpLock, () => smithii.liquidity.lpLock, ['liquidity.lpLock']],
  // Genesis states these as an absolute timeline; Smithii's UI takes durations.
  // Same term, different unit — so the durations are DERIVED from the timeline
  // rather than restated, which is what keeps them honest when a date moves.
  ['whitelist phase (h)',
    () => hoursBetween(genesis.timeline.whitelistStart, genesis.timeline.publicStart),
    () => smithii.whitelist.phaseHours, ['whitelist.phaseHours']],
  ['public phase (h)',
    () => hoursBetween(genesis.timeline.publicStart, genesis.timeline.depositEnd),
    () => smithii.publicPhaseHours, ['publicPhaseHours']],
  // Genesis has no `enabled` flag: the whitelist round exists iff the window
  // between whitelistStart and publicStart is non-empty. Same claim, expressed
  // as a schedule on one side and a boolean on the other.
  ['whitelist round exists',
    () => hoursBetween(genesis.timeline.whitelistStart, genesis.timeline.publicStart) > 0,
    () => smithii.whitelist.enabled, ['whitelist.enabled']],
  // bps -> percent. 6667 bps is 66.67%.
  ['liquidity share of raise (%)',
    () => genesis.liquidity.raisedSolToLiquidityBps / 100,
    () => smithii.liquidity.autoListPercentOfRaise, ['liquidity.autoListPercentOfRaise']],
];

test('every shared sale term matches across the two venues', () => {
  const diffs = [];
  for (const [label, g, s] of SHARED) {
    const a = g(), b = s();
    if (a !== b) diffs.push(`${label}: genesis ${JSON.stringify(a)} != smithii ${JSON.stringify(b)}`);
  }
  assert.deepEqual(diffs, [],
    'the fallback venue would publish different sale terms:\n  ' + diffs.join('\n  '));
});

test('the refund promise is off at BOTH venues', () => {
  // Called out separately from the parity check above because equality is not
  // the property that matters here. Two configs agreeing on `true` would pass
  // parity and still publish a refund nobody has implemented at either venue.
  assert.equal(genesis.sale.refundIfSoftCapMissed, false);
  assert.equal(smithii.sale.refundIfSoftCapMissed, false,
    'smithii promises a refund. There is no refund instruction for a V2 presale ' +
    '(proven on devnet, 0x2f) and Smithii\'s own refund path has never been ' +
    'executed or read by anything in this repo. Execute it and publish the ' +
    'transaction first.');
});

test('the LP lock is permanent at both venues, with no month count to expire', () => {
  assert.match(genesis.liquidity.lpLock, /never-claim/);
  assert.match(smithii.liquidity.lpLock, /never-claim/);
  assert.equal(smithii.liquidity.lpLockMonths, null,
    '"the LP unlocks in N months" is a different product from "the LP is never ' +
    'claimable". If Smithii cannot express a permanent lock, that must be ' +
    're-ratified and published — not encoded here as a number.');
});

test('every published term is compared, so this file cannot silently go stale', () => {
  // The failure mode of a hand-written parity list is a field nobody added to
  // it — which is how the original three divergences survived. So the check
  // runs over smithii.config.json's ENTIRE key set, not over the intersection
  // with genesis: an intersection misses the case that matters most, a term the
  // fallback venue publishes and the primary one has no equivalent for. (An
  // earlier draft compared only the intersection and let exactly that through.)
  //
  // Direction is deliberate. Genesis has ~60 leaf keys with no fallback
  // counterpart — buckets, disclosures, treasury, funding mode — and listing
  // them all as exemptions would be noise that nobody reads. Smithii's file is
  // the one an operator transcribes into a UI, so it is the one whose every
  // field becomes a published term.
  const leaves = (obj, prefix = '') => Object.entries(obj).flatMap(([k, v]) =>
    k.startsWith('_') ? []
      : (v && typeof v === 'object' && !Array.isArray(v))
        ? leaves(v, `${prefix}${k}.`)
        : [`${prefix}${k}`]);

  // Not parity terms. Each needs a reason.
  const EXEMPT = new Set([
    'venue',                      // the whole point is that they differ
    'cluster',                    // set per run, not a sale term
    'token.mint',                 // fill-from-artifact placeholder on both sides
    'token.symbol',               // compared, but also trivially fixed
    'platformFee.baseSol',        // the venue's own fee; Genesis has no counterpart
    'platformFee.percentOfSales', // ditto, and still a <CONFIRM_ON_SMITHII> placeholder
    'liquidity.lpLockMonths',     // must be null; asserted by its own test above
  ]);

  const covered = new Set(SHARED.flatMap(([, , , keys]) => keys));
  const published = leaves(smithii);
  const uncompared = published.filter((k) => !covered.has(k) && !EXEMPT.has(k));

  assert.deepEqual(uncompared, [],
    'these fields would be published at the fallback venue but nothing compares ' +
    'them against the primary one — add them to SHARED, or to EXEMPT with a ' +
    'reason:\n  ' + uncompared.join('\n  '));

  // Vacuity guard: if the leaf walker breaks, `published` empties and the
  // assertion above passes while checking nothing.
  assert.ok(published.length >= 15,
    `only ${published.length} fields found in smithii.config.json — the shape ` +
    'changed and this test is no longer reading it');
});
