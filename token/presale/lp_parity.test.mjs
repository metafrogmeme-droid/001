// The soft-cap fix, pinned.
//
// F-25: the LP token side is fixed at bucket creation while the SOL side scales
// with the raise, so the pool's opening price scales with the raise and the
// presale price does not. At the 1,000 SOL soft cap the pool opened ~5x below
// what presale buyers paid, against a PERMANENT LP lock.
//
// `updateRaydiumCpmmBucketV2` takes an optional `baseTokenAllocation`, so once
// the raise is final the token side can be scaled to match. These tests pin the
// arithmetic that decides that number. It becomes irreversible the moment the
// pool is created, and it is the number that decides whether every presale buyer
// is above or below water at listing.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  parityLpBaseUnitsForRaise,
  rebalancedLpAllocation,
  fixedPriceSolPerToken,
} from './genesis_lib.mjs';

function baseConfig() {
  return {
    token: { symbol: 'RCLAW', decimals: 9, totalSupply: '1000000000' },
    sale: { presaleAllocation: '150000000', softCapSol: 1000, hardCapSol: 5000 },
    liquidity: { tokenAllocation: '100000000', raisedSolToLiquidityBps: 6667 },
  };
}

const SOL = 1_000_000_000n;
const clone = (o) => JSON.parse(JSON.stringify(o));

/** Opening pool price in SOL per whole token, from the on-chain quantities. */
function poolPrice(cfg, raisedLamports, lpBaseUnits) {
  const quoteToPool =
    (BigInt(raisedLamports) * BigInt(cfg.liquidity.raisedSolToLiquidityBps)) / 10_000n;
  const solToPool = Number(quoteToPool) / 1e9;
  const wholeTokens = Number(lpBaseUnits) / 10 ** cfg.token.decimals;
  return solToPool / wholeTokens;
}

/**
 * Exact `poolPrice >= presalePrice`, with no floating point anywhere.
 *
 *   quoteToPool / lp  >=  hardCapLamports / presaleAllocationBaseUnits
 *   <=> quoteToPool * presaleAllocationBaseUnits >= hardCapLamports * lp
 *
 * The float form of this check reported a failure at a 999-lamport raise where
 * the two prices are exactly equal in rational arithmetic — a one-ULP artifact
 * of the comparison, not of the allocation. Cross-multiplying removes the
 * question entirely, which matters because "is the pool at or above the presale
 * price" is the single property this whole feature exists to guarantee.
 */
function opensAtOrAbovePresale(cfg, raisedLamports, lpBaseUnits) {
  const quoteToPool =
    (BigInt(raisedLamports) * BigInt(cfg.liquidity.raisedSolToLiquidityBps)) / 10_000n;
  const presaleAlloc = BigInt(cfg.sale.presaleAllocation) * 10n ** BigInt(cfg.token.decimals);
  const hardCapLamports = BigInt(cfg.sale.hardCapSol) * 1_000_000_000n;
  return quoteToPool * presaleAlloc >= hardCapLamports * BigInt(lpBaseUnits);
}

test('parity holds at EVERY raise between the soft and hard cap', () => {
  // This is the whole property. A fixed allocation makes the pool price a
  // function of the raise; a scaled one makes it constant.
  const cfg = baseConfig();
  const presale = fixedPriceSolPerToken(cfg);
  for (const sol of [1000, 1500, 2000, 2500, 3000, 4000, 4999, 5000]) {
    const raised = BigInt(sol) * SOL;
    const lp = parityLpBaseUnitsForRaise(cfg, raised);
    const price = poolPrice(cfg, raised, lp);
    const drift = Math.abs(price - presale) / presale;
    assert.ok(
      drift < 1e-9,
      `at ${sol} SOL the pool opens at ${price.toExponential(8)} vs presale ` +
        `${presale.toExponential(8)} (drift ${drift})`
    );
  }
});

test('the soft-cap case that F-25 reported is actually fixed', () => {
  const cfg = baseConfig();
  const raised = 1000n * SOL;
  const presale = fixedPriceSolPerToken(cfg);

  // Before: the fixed 100M allocation.
  const fixedAlloc = BigInt(cfg.liquidity.tokenAllocation) * 10n ** 9n;
  const before = poolPrice(cfg, raised, fixedAlloc);
  assert.ok(before < presale / 4, `the reported defect should reproduce (got ${before})`);

  // After: scaled to the raise.
  const after = poolPrice(cfg, raised, parityLpBaseUnitsForRaise(cfg, raised));
  assert.ok(
    Math.abs(after - presale) / presale < 1e-9,
    `scaled allocation must open at the presale price (got ${after}, want ${presale})`
  );
});

test('scaling is proportional — 20% of the cap gets 20% of the tokens', () => {
  const cfg = baseConfig();
  const atCap = parityLpBaseUnitsForRaise(cfg, 5000n * SOL);
  const atSoft = parityLpBaseUnitsForRaise(cfg, 1000n * SOL);
  // 1000/5000 = 1/5 exactly, so this is an exact integer relationship.
  assert.equal(atSoft * 5n, atCap);
  assert.equal(atSoft / 10n ** 9n, 20_001_000n, '20,001,000 whole tokens at the soft cap');
});

// ── The clamp ───────────────────────────────────────────────────────────────

test('a full raise clamps to the configured allocation rather than exceeding it', () => {
  // Parity at the hard cap wants 100,005,000 tokens — marginally more than the
  // 100,000,000 the bucket was created with, because 6667 bps is the ceiling of
  // the exact parity ratio. Allocating tokens that are not in the bucket is not
  // an option, so it clamps.
  const cfg = baseConfig();
  const r = rebalancedLpAllocation(cfg, 5000n * SOL);
  assert.equal(r.clamped, true);
  assert.equal(r.allocation, r.configured);
  assert.ok(r.parity > r.configured);

  // And clamping DOWN is safe: fewer tokens against the same quote means a
  // higher opening price, never a lower one.
  const presale = fixedPriceSolPerToken(cfg);
  assert.ok(poolPrice(cfg, 5000n * SOL, r.allocation) >= presale);
});

test('a partial raise is never clamped', () => {
  const r = rebalancedLpAllocation(baseConfig(), 2000n * SOL);
  assert.equal(r.clamped, false);
  assert.equal(r.allocation, r.parity);
  assert.ok(r.allocation < r.configured, 'a partial raise must allocate fewer tokens');
});

test('rounding always favours the buyer, never the pool', () => {
  // Floor division must never round UP, because rounding up allocates more
  // tokens against the same quote and opens the pool BELOW the presale price —
  // the exact failure this function exists to prevent.
  const cfg = baseConfig();
  for (const lamports of [1n, 7n, 999n, 250_000_000n, 1_234_567_891n, 3_333_333_333_333n]) {
    const lp = parityLpBaseUnitsForRaise(cfg, lamports);
    assert.ok(
      opensAtOrAbovePresale(cfg, lamports, lp),
      `raise ${lamports} lamports opened the pool below the presale price (lp=${lp})`
    );
  }
});

// ── Refusals ────────────────────────────────────────────────────────────────

test('a zero raise yields a zero allocation, which the command must refuse', () => {
  assert.equal(parityLpBaseUnitsForRaise(baseConfig(), 0n), 0n);
});

test('a negative raise is rejected', () => {
  assert.throws(() => parityLpBaseUnitsForRaise(baseConfig(), -1n), /must not be negative/);
});

test('a zero quote split has no price to reach parity with', () => {
  const cfg = clone(baseConfig());
  cfg.liquidity.raisedSolToLiquidityBps = 0;
  assert.throws(() => parityLpBaseUnitsForRaise(cfg, 1000n * SOL), /no quote is routed/);
});

test('the computation is exact BigInt against a hand-derived value', () => {
  // At 10^17 base units a double carries ~16-17 significant digits, so float
  // arithmetic here is wrong by millions of base units — and the result is an
  // immutable on-chain allocation. Pinned against a value derived by hand:
  //   quoteToPool = floor(3_333_333_333_333 * 6667 / 10000) = 2_222_333_333_332
  //   lp          = floor(2_222_333_333_332 * 150e15 / 5e12)
  const cfg = baseConfig();
  const raised = 3_333_333_333_333n; // deliberately not a round number
  const quoteToPool = (raised * 6667n) / 10_000n;
  const expected = (quoteToPool * 150_000_000n * 10n ** 9n) / (5000n * 10n ** 9n);
  const exact = parityLpBaseUnitsForRaise(cfg, raised);
  assert.equal(typeof exact, 'bigint', 'must be BigInt — a double loses digits at 10^17');
  assert.equal(exact, expected);
  assert.ok(opensAtOrAbovePresale(cfg, raised, exact));

  // The result sits above a double's exact-integer range (2^53 ≈ 9.0e15), which
  // is why the whole path is BigInt: intermediate products here reach ~3.3e29,
  // far past the point where a double silently starts rounding. This particular
  // value does round-trip through Number by luck — it ends in enough zeros —
  // so that is asserted as a fact about the magnitude, not used as proof.
  assert.ok(exact > BigInt(Number.MAX_SAFE_INTEGER), 'the result exceeds a double’s exact range');
});
