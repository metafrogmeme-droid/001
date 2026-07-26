// The soft-cap fix, pinned.
//
// F-25: the LP token side is fixed at bucket creation while the SOL side scales
// with the raise, so the pool's opening price scales with the raise and the
// presale price does not. At the 1,000 SOL soft cap the pool opened ~5x below
// what presale buyers paid, against a PERMANENT LP lock.
//
// CORRECTION (2026-07-26, proven on devnet). An earlier version of this file
// backed `presale:rebalance-lp`, which scaled the allocation to the realised
// raise via updateRaydiumCpmmBucketV2 after the deposit window closed. That
// command was removed because it cannot run:
//
//   depositPresaleV2 requires the genesis account FINALIZED       (error 0x2c)
//   finalizeV2 permanently locks bucket configuration             (error 0x2b)
//   the realised raise is only known AFTER deposits
//
// so the allocation is immutable before the number needed to compute it exists.
// The arithmetic below is still exactly right and still load-bearing — it just
// applies at CONFIGURATION time instead. Sized to the SOFT cap it guarantees the
// pool opens at or above the presale price at every raise in range; sized to the
// hard cap (the committed 100M) it does not.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import fs from 'node:fs';
import path from 'node:path';

import {
  parityLpBaseUnitsForRaise,
  rebalancedLpAllocation,
  fixedPriceSolPerToken,
  opensAtOrAbovePresale,
  PRESALE_DIR,
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
 * `opensAtOrAbovePresale` was a local copy here. It is now imported from
 * genesis_lib, because the same comparison also drives the config-time warning
 * in deriveLiquidityParams and the trigger-time refusal in genesis_presale —
 * and three copies of one comparison is three chances to disagree about the
 * boundary case, which is the only case that matters.
 *
 * Why it is exact and not floating point, preserved from the original note: the
 * float form reported a failure at a 999-lamport raise where the two prices are
 * exactly equal in rational arithmetic, a one-ULP artifact of the comparison
 * rather than of the allocation. It later did the same thing to the plan output
 * at exactly the allocation chosen FOR parity.
 */

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

test('sizing to the SOFT cap is safe at every raise in range', () => {
  // The config-time fix, and the property that makes it the right one: one
  // allocation, chosen before any deposit, that is never below parity.
  const cfg = baseConfig();
  const softAlloc = parityLpBaseUnitsForRaise(cfg, 1000n * SOL);
  for (const sol of [1000, 1500, 2500, 4000, 5000]) {
    assert.ok(
      opensAtOrAbovePresale(cfg, BigInt(sol) * SOL, softAlloc),
      `soft-cap-sized allocation opened below presale price at ${sol} SOL`
    );
  }
  // And the committed 100M allocation does NOT have that property.
  const configured = BigInt(cfg.liquidity.tokenAllocation) * 10n ** 9n;
  assert.ok(
    !opensAtOrAbovePresale(cfg, 1000n * SOL, configured),
    'the committed allocation must still reproduce the F-25 defect at the soft cap'
  );
});

test('the soft-cap case that F-25 reported still reproduces un-fixed', () => {
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

// ── The clamp (still used by the config-time sizing helper) ─────────────────

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


// ── the committed config, not a fixture ─────────────────────────────────────
//
// Everything above runs against baseConfig(), which is right for exercising the
// arithmetic across raises but means NOTHING here was asserting anything about
// the number this project actually ships. The sizing decision could have been
// reverted in the config and this file would still have been green. These read
// the committed config directly.

function committedConfig() {
  return JSON.parse(
    fs.readFileSync(path.join(PRESALE_DIR, 'metaplex-genesis.config.json'), 'utf8')
  );
}

test('DECISION: the committed LP allocation never opens below the presale price', () => {
  const cfg = committedConfig();
  const lp = BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(cfg.token.decimals);
  // Every raise from the sized-for floor to the hard cap, inclusive of both.
  const floor = Number(cfg.liquidity.sizedForRaiseSol);
  for (const sol of [floor, floor + 1, 1500, 2500, 3500, 4999, cfg.sale.hardCapSol]) {
    assert.ok(
      opensAtOrAbovePresale(cfg, BigInt(sol) * SOL, lp),
      `committed allocation opens BELOW the presale price at a ${sol} SOL raise`
    );
  }
});

test('DECISION: sizedForRaiseSol actually describes tokenAllocation', () => {
  // The trigger-time guard refuses below sizedForRaiseSol. If that number does
  // not match what tokenAllocation was priced for, the guard lies in whichever
  // direction the mismatch runs — waving through an underwater pool, or blocking
  // a sound one. Pin them to each other.
  const cfg = committedConfig();
  const lp = BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(cfg.token.decimals);
  const floorLamports = BigInt(cfg.liquidity.sizedForRaiseSol) * SOL;

  assert.ok(opensAtOrAbovePresale(cfg, floorLamports, lp), 'at the floor it must be at/above parity');
  // And it must be the TIGHTEST such floor: one lamport less must fail. That is
  // what makes it a description rather than a conservative guess.
  assert.ok(
    !opensAtOrAbovePresale(cfg, floorLamports - 1n, lp),
    'sizedForRaiseSol is higher than tokenAllocation requires — the guard would block sound raises'
  );
});

test('DECISION: below the sized-for floor it DOES open under presale, as disclosed', () => {
  // Not a defect — the disclosed limit of the choice. sale.softCapSol reaches no
  // on-chain account, so a raise under the floor is permitted and prices the
  // pool below presale. If this ever passes, the disclosure has become false and
  // config.disclosures.softCapNotEnforced needs rewriting.
  const cfg = committedConfig();
  const lp = BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(cfg.token.decimals);
  const half = BigInt(Math.floor(cfg.liquidity.sizedForRaiseSol / 2)) * SOL;
  assert.ok(!opensAtOrAbovePresale(cfg, half, lp));
});

test('DECISION: the freed supply is re-homed, not deleted', () => {
  const cfg = committedConfig();
  const buckets = cfg.allocations.buckets.reduce((n, b) => n + BigInt(b.tokens), 0n);
  const total = buckets + BigInt(cfg.sale.presaleAllocation) + BigInt(cfg.liquidity.tokenAllocation);
  assert.equal(total, BigInt(cfg.token.totalSupply), 'finalizeV2 refuses anything but an exact sum');
});

test('DECISION: the disclosures a buyer is owed are present and non-empty', () => {
  // printDisclosures() renders whatever is here, so an empty or missing block
  // degrades silently to printing nothing at all.
  const cfg = committedConfig();
  assert.ok(cfg.disclosures, 'config.disclosures is missing');
  for (const key of ['noRefund', 'programIsUpgradeable', 'softCapNotEnforced']) {
    assert.equal(typeof cfg.disclosures[key], 'string', `disclosures.${key} must be present`);
    assert.ok(cfg.disclosures[key].length > 120, `disclosures.${key} is too short to be a disclosure`);
  }
  // The upgrade authority is the fact that makes that disclosure worth anything.
  assert.match(cfg.disclosures.programIsUpgradeable, /bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb/);
});
