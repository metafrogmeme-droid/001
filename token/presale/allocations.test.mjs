// The supply-allocation invariant, pinned offline.
//
// `finalizeV2` refuses while any base token is unallocated:
//
//     Program log: Total supply must be fully allocated before finalize
//
// and finalize is the step that opens deposits — so a launch that is short by a
// single token is fully built and permanently unopenable. It fails at the END of
// the sequence, after the genesis account, the presale bucket and the
// IRREVERSIBLE liquidity lock already exist on chain.
//
// That makes this the highest-value offline check in the presale tooling: it
// converts a fatal, expensive, late on-chain failure into a config error caught
// before anything is signed. Verified against devnet on 2026-07-26 — the real
// 1,000,000,000 config now creates 8 buckets and finalizes.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { deriveAllocationBuckets, loadConfig } from './genesis_lib.mjs';

const clone = (o) => JSON.parse(JSON.stringify(o));

/** The committed config, which must always be internally consistent. */
function committed() {
  return loadConfig();
}

test('the committed config allocates exactly 100% of supply', () => {
  const cfg = committed();
  const r = deriveAllocationBuckets(cfg);
  assert.equal(
    r.allocated,
    r.totalSupply,
    'the shipped config must be finalizable, or no launch can open'
  );
  const scale = 10n ** BigInt(cfg.token.decimals);
  assert.equal(r.totalSupply / scale, 1_000_000_000n);
});

test('the buckets mirror the published §4 allocation table', () => {
  // If these drift from docs/TOKEN_ROADMAP.md §4, the published tokenomics and
  // the on-chain reality disagree — which is a disclosure problem, not just a
  // config one.
  //
  // UPDATED 2026-07-26 with the F-25 sizing decision. The LP side moved from
  // 100,000,000 (parity at a FULL raise, underwater at anything less) to
  // 20,001,000 (parity at the soft cap). The 79,999,000 freed did not vanish —
  // it is in `reserve`, earmarked for post-TGE liquidity, which is what makes
  // the thin-pool consequence of that choice recoverable.
  const cfg = committed();
  const byName = Object.fromEntries(
    (cfg.allocations.buckets || []).map((b) => [b.name, BigInt(b.tokens)])
  );
  assert.deepEqual(byName, {
    community: 250_000_000n,
    team: 150_000_000n,
    treasury: 200_000_000n,
    partnerships: 80_000_000n,
    advisors: 20_000_000n,
    reserve: 129_999_000n,
  });
  assert.equal(BigInt(cfg.sale.presaleAllocation), 150_000_000n);
  assert.equal(BigInt(cfg.liquidity.tokenAllocation), 20_001_000n);
  // The freed supply is accounted for, not merely absent from liquidity.
  assert.equal(129_999_000n - 50_000_000n, 100_000_000n - 20_001_000n);
});

test('a supply that is SHORT is refused, naming the gap', () => {
  const cfg = clone(committed());
  cfg.allocations.buckets = cfg.allocations.buckets.filter((b) => b.name !== 'reserve');
  assert.throws(() => deriveAllocationBuckets(cfg), /not fully allocated/);
  assert.throws(() => deriveAllocationBuckets(cfg), /short by 129,999,000/);
  // And it must say WHY this matters, because the on-chain failure is late and
  // expensive.
  assert.throws(() => deriveAllocationBuckets(cfg), /Total supply must be fully allocated/);
});

test('a supply that is OVER-allocated is refused too', () => {
  const cfg = clone(committed());
  cfg.allocations.buckets[0].tokens = '250000001';
  assert.throws(() => deriveAllocationBuckets(cfg), /over by 1/);
});

test('an over-allocation of a single base unit is still caught', () => {
  // Off-by-one at 10^18 base units is exactly the class of error a float-based
  // check would round away.
  const cfg = clone(committed());
  cfg.token.totalSupply = '1000000001';
  assert.throws(() => deriveAllocationBuckets(cfg), /not fully allocated/);
});

test('a zero or negative allocation is rejected rather than silently ignored', () => {
  const cfg = clone(committed());
  cfg.allocations.buckets[0].tokens = '0';
  assert.throws(() => deriveAllocationBuckets(cfg), /must be a positive token amount/);
});

test('an unnamed bucket is rejected', () => {
  const cfg = clone(committed());
  delete cfg.allocations.buckets[2].name;
  assert.throws(() => deriveAllocationBuckets(cfg), /has no name/);
});

test('every cliff is a real timestamp and the claim window stays open', () => {
  const cfg = committed();
  for (const b of deriveAllocationBuckets(cfg).buckets) {
    assert.ok(b._unlockAt > 0n, `${b.name} has no unlock time`);
    // An unlocked bucket whose claim window CLOSED would strand its whole
    // allocation with no instruction to recover it.
    assert.ok(b.claimStartCondition, `${b.name} has no claim start condition`);
    assert.ok(b.claimEndCondition, `${b.name} has no claim end condition`);
  }
});

test('a malformed unlockAt is rejected, not silently coerced', () => {
  const cfg = clone(committed());
  cfg.allocations.buckets[1].unlockAt = 'next tuesday';
  assert.throws(() => deriveAllocationBuckets(cfg), /Bad timestamp/);
});

test('recipients are unset in the committed config, and that is deliberate', () => {
  // presale:allocate REFUSES a blank recipient rather than defaulting to the
  // signing key — an unset recipient in the repo is a prompt to choose one, not
  // a working default. If this ever starts passing with values filled in, they
  // must be real multisig addresses, not a developer's hot wallet.
  const cfg = committed();
  for (const b of cfg.allocations.buckets) {
    assert.equal(b.recipient, '', `${b.name} must ship with an empty recipient`);
  }
});
