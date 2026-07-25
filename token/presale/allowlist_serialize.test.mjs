/**
 * Offline regression: the allowlist args must actually SERIALIZE.
 *
 * The original implementation omitted the required `padding` field of
 * AllowlistInitArgs (a fixed-size u8[6] with no kinobi default), so
 * `presale:create` threw a TypeError before building any transaction whenever a
 * whitelist artifact existed. `presale:plan` never touched that path, so the
 * "offline plan runs green" verification could not catch it.
 *
 * This test serializes the real args with the real SDK serializer — no RPC, no
 * keypair — so the defect can never regress silently.
 *
 * Run: node --test presale/allowlist_serialize.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';

import { getAllowlistInitArgsSerializer } from '@metaplex-foundation/genesis';
import { buildAllowlist, allowlistInitArgsFromArtifact } from './genesis_lib.mjs';

const CFG = {
  timeline: { whitelistStart: '2026-09-01T15:00:00Z', publicStart: '2026-09-03T15:00:00Z' },
  sale: { hardCapSol: 5000 },
};

const ADDRS = [
  'So11111111111111111111111111111111111111112',
  'GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B',
  'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL',
];

test('buildAllowlist().initArgs serializes with the real SDK serializer', () => {
  const wl = buildAllowlist(CFG, ADDRS);
  const ser = getAllowlistInitArgsSerializer();
  const bytes = ser.serialize(wl.initArgs); // threw before the padding fix
  assert.ok(bytes.length > 0);
  assert.deepEqual([...wl.initArgs.padding], [0, 0, 0, 0, 0, 0]);
  assert.equal(wl.initArgs.merkleRoot.length, 32);
});

test('allowlistInitArgsFromArtifact() round-trips and serializes', () => {
  const wl = buildAllowlist(CFG, ADDRS);
  const artifact = { rootHex: wl.rootHex, treeHeight: wl.treeHeight };
  const args = allowlistInitArgsFromArtifact(CFG, artifact);
  const bytes = getAllowlistInitArgsSerializer().serialize(args);
  assert.ok(bytes.length > 0);
  assert.equal(args.merkleTreeHeight, wl.treeHeight);
  // The rebuilt root must equal the originally-built root.
  assert.deepEqual([...args.merkleRoot], [...wl.initArgs.merkleRoot]);
});

test('whitelist window is non-empty: deposits open before the allowlist expires', async () => {
  const { derivePresaleParams } = await import('./genesis_lib.mjs');
  const cfg = {
    ...CFG,
    token: { decimals: 9 },
    sale: { hardCapSol: 5000, softCapSol: 1000, presaleAllocation: '150000000', minContributionSol: 0.25, maxContributionSol: 25 },
    vesting: { cliffAmountBps: 3300, linearMonthsAfterTge: 2, vestingPeriodSeconds: 86400 },
    timeline: { ...CFG.timeline, depositEnd: '2026-09-06T15:00:00Z', tge: '2026-09-08T15:00:00Z', claimEnd: '2027-09-08T15:00:00Z' },
    whitelist: ADDRS,
    liquidity: { tokenAllocation: '100000000', raisedSolToLiquidityBps: 6000, dex: 'raydium' },
  };
  const p = derivePresaleParams(cfg);
  const allowlistEnd = Date.parse(cfg.timeline.publicStart) / 1000;
  assert.ok(
    Number(p._times.depositStart) < allowlistEnd,
    'deposits must open BEFORE the allowlist expires, or the whitelist round is zero-length'
  );
});
