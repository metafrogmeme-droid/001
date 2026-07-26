// Offline regression tests for the presale derivation.
//
// `presale:plan` exiting 0 proved almost nothing: a derivation that produced
// wrong numbers, or silently accepted an inverted timeline, would also exit 0.
// These pin the values that become IMMUTABLE on-chain conditions, and the
// invariants that must refuse to derive at all.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { derivePresaleParams, deriveLiquidityParams, fixedPriceSolPerToken } from './genesis_lib.mjs';

/** A minimal, internally consistent config. Tests clone and perturb it. */
function baseConfig() {
  return {
    token: { symbol: 'RCLAW', decimals: 9, totalSupply: '1000000000' },
    sale: {
      presaleAllocation: '150000000',
      softCapSol: 1000,
      hardCapSol: 5000,
      minContributionSol: 0.25,
      maxContributionSol: 25,
      refundIfSoftCapMissed: false,
    },
    timeline: {
      whitelistStart: '2026-07-01T00:00:00Z',
      publicStart: '2026-07-03T00:00:00Z',
      depositEnd: '2026-07-06T00:00:00Z',
      tge: '2026-07-08T00:00:00Z',
      claimEnd: '2027-07-08T00:00:00Z',
    },
    vesting: { cliffAmountBps: 3300, linearMonthsAfterTge: 6, vestingPeriodSeconds: 86400 },
    liquidity: { tokenAllocation: '100000000', raisedSolToLiquidityBps: 6667, dex: 'raydium' },
    whitelist: [],
  };
}

const clone = (o) => JSON.parse(JSON.stringify(o));

test('derives the exact on-chain values from config', () => {
  const p = derivePresaleParams(baseConfig());
  // 150,000,000 tokens at 9 dp.
  assert.equal(p.baseTokenAllocation, 150_000_000n * 10n ** 9n);
  // Caps in lamports — these set the fixed price, so they must be exact.
  assert.equal(p.allocationQuoteTokenCap, 5_000n * 1_000_000_000n);
  assert.equal(p.softCapLamports, 1_000n * 1_000_000_000n);
  // Fractional SOL must not lose precision.
  assert.equal(p.perWalletMinLamports, 250_000_000n);
  assert.equal(p.perWalletMaxLamports, 25n * 1_000_000_000n);
});

test('deposits open at whitelistStart only when a whitelist exists', () => {
  const open = derivePresaleParams(baseConfig());
  const publicStart = BigInt(Date.parse('2026-07-03T00:00:00Z') / 1000);
  assert.equal(open._times.depositStart, publicStart, 'no whitelist => opens at publicStart');

  const gated = clone(baseConfig());
  gated.whitelist = ['So11111111111111111111111111111111111111112'];
  const wl = derivePresaleParams(gated);
  assert.equal(
    wl._times.depositStart,
    BigInt(Date.parse('2026-07-01T00:00:00Z') / 1000),
    'a whitelist must open the window at whitelistStart, or the OG round is zero-length'
  );
});

test('fixed price is allocation-over-cap', () => {
  const price = fixedPriceSolPerToken(baseConfig());
  assert.ok(Math.abs(price - 5000 / 150_000_000) < 1e-18);
});

// ── Invariants: each of these used to derive happily and fail on-chain ──────

test('rejects an inverted deposit window', () => {
  const cfg = clone(baseConfig());
  cfg.timeline.depositEnd = '2026-06-01T00:00:00Z'; // before the start
  assert.throws(() => derivePresaleParams(cfg), /depositStart .* must precede depositEnd/);
});

test('rejects a TGE that precedes the deposit window closing', () => {
  const cfg = clone(baseConfig());
  cfg.timeline.tge = '2026-07-04T00:00:00Z'; // inside the deposit window
  assert.throws(() => derivePresaleParams(cfg), /must not follow tge/);
});

test('rejects a soft cap above the hard cap', () => {
  const cfg = clone(baseConfig());
  cfg.sale.softCapSol = 9000;
  assert.throws(() => derivePresaleParams(cfg), /softCapSol .* must not exceed hardCapSol/);
});

test('rejects per-wallet min above max', () => {
  const cfg = clone(baseConfig());
  cfg.sale.minContributionSol = 50;
  assert.throws(() => derivePresaleParams(cfg), /minContributionSol .* must not exceed/);
});

test('rejects a cliff outside 0..10000 bps', () => {
  const cfg = clone(baseConfig());
  cfg.vesting.cliffAmountBps = 12_000;
  assert.throws(() => derivePresaleParams(cfg), /cliffAmountBps .* within 0\.\.10000/);
});

test('refuses to ship an unenforceable soft-cap refund promise', () => {
  const cfg = clone(baseConfig());
  cfg.sale.refundIfSoftCapMissed = true;
  assert.throws(() => derivePresaleParams(cfg), /unenforceable|no soft-cap or refund instruction/);
});

// ── Liquidity pricing ───────────────────────────────────────────────────────

test('refuses a split that opens the pool below the presale price', () => {
  const cfg = clone(baseConfig());
  cfg.liquidity.raisedSolToLiquidityBps = 6000; // the old value: 6000 < 100M/150M
  assert.throws(() => deriveLiquidityParams(cfg), /below the presale price/);
});

test('parity allocation opens the pool exactly at the presale price', () => {
  const cfg = baseConfig();
  const lp = deriveLiquidityParams(cfg);
  const presale = fixedPriceSolPerToken(cfg);
  assert.ok(
    lp._pricing.bestCasePoolPrice >= presale,
    'bps == tokenAllocation/presaleAllocation must price the pool at or above parity'
  );
  assert.ok(
    (lp._pricing.bestCasePoolPrice - presale) / presale < 0.001,
    'and no more than a rounding step above it'
  );
  assert.ok(lp._pricing.worstCasePoolPrice < presale, 'soft cap still opens below parity');
});
