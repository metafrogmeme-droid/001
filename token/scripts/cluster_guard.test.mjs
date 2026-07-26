// Verifies the cluster-identity guards against the REAL chains.
//
// The audit could not do this: its environment returned 403 for
// api.devnet.solana.com, so every genesis constant here was written from memory
// and asserted rather than observed. A wrong constant fails in one of two ways,
// both silent — it either bricks the tooling against a legitimate cluster, or it
// waves mainnet through. Neither is visible offline.
//
// NETWORK-GATED: skips unless RUN_NETWORK_TESTS=1, so CI and offline runs stay
// hermetic. Run with:  npm run test:network
//
// Do NOT set RUN_NETWORK_TESTS in CI. These hit the public Solana RPC endpoints,
// which rate-limit: during development this file passed 5/5 standalone and then
// reported 2 failures in the same minute when run alongside the other suites,
// purely from throttling — the constants had not changed. A guard that goes red
// because someone else's endpoint was busy teaches people to ignore red. The
// offline case at the bottom always runs and is the one CI enforces.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  getConnection,
  MAINNET_GENESIS,
  DEVNET_GENESIS,
  TESTNET_GENESIS,
  CLUSTER_GENESIS,
} from './lib.mjs';

const ONLINE = process.env.RUN_NETWORK_TESTS === '1';
const opts = { skip: ONLINE ? false : 'set RUN_NETWORK_TESTS=1 to run (needs network)' };

async function genesisOf(url) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getGenesisHash' }),
  });
  return (await res.json()).result;
}

test('the hardcoded genesis constants match the live chains', opts, async () => {
  assert.equal(await genesisOf('https://api.devnet.solana.com'), DEVNET_GENESIS,
    'DEVNET_GENESIS has drifted from the real devnet');
  assert.equal(await genesisOf('https://api.mainnet-beta.solana.com'), MAINNET_GENESIS,
    'MAINNET_GENESIS is wrong — the mainnet refusal would not fire');
  assert.equal(await genesisOf('https://api.testnet.solana.com'), TESTNET_GENESIS,
    'TESTNET_GENESIS has drifted from the real testnet');
});

test('real devnet is accepted', opts, async () => {
  const conn = await getConnection({ RPC_URL: 'https://api.devnet.solana.com' },
    { expectCluster: 'devnet' });
  assert.equal(await conn.getGenesisHash(), DEVNET_GENESIS);
});

test('real mainnet-beta is refused', opts, async () => {
  await assert.rejects(
    () => getConnection({ RPC_URL: 'https://api.mainnet-beta.solana.com' },
      { expectCluster: 'devnet' }),
    /Refusing to run against mainnet-beta/,
    'this is the guard that stops draft tooling touching real value'
  );
});

test('a cluster mismatch is refused', opts, async () => {
  // Config claims testnet, the endpoint is devnet. Both are non-mainnet, so only
  // the explicit expectCluster comparison catches it.
  await assert.rejects(
    () => getConnection({ RPC_URL: 'https://api.devnet.solana.com' },
      { expectCluster: 'testnet' }),
    /Cluster mismatch/
  );
});

test('a config naming mainnet is refused before any network call', () => {
  // Offline — runs always. Guards the config field itself.
  assert.equal(CLUSTER_GENESIS['mainnet-beta'], MAINNET_GENESIS);
  assert.rejects(
    () => getConnection({ RPC_URL: 'https://api.devnet.solana.com' },
      { expectCluster: 'mainnet-beta' }),
    /draft\/devnet tooling/
  );
});
