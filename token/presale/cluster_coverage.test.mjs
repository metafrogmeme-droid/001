// Every command that opens a connection must verify the cluster it landed on.
//
// This is a STRUCTURAL test — it reads the source of genesis_presale.mjs rather
// than calling anything. That is deliberate. The defect it exists to catch is
// not a wrong result from a function; it is a missing call in a function nobody
// thought to check, and no amount of exercising the guarded paths reveals the
// unguarded ones.
//
// What it caught when written: five of nine commands — `deposit`, `liquidity`,
// `withdraw`, `withdraw-unsold`, `claim` — called `makeUmi()` and never called
// `assertDevnet()`. `deposit` sends buyer SOL. `liquidity` creates the
// never-claim LP lock, which is irreversible by construction. The only thing in
// front of them was `rpcUrl()`'s `url.includes('mainnet')`, and that test is
// case-sensitive while DNS is not, so the literal endpoint
// `https://API.MAINNET-BETA.SOLANA.COM` passed it — along with every rebranded
// provider and any bare IP.
//
// The guard cannot live in `makeUmi` itself: `assertDevnet` is async (it asks
// the chain for its genesis hash) and `makeUmi` is not, so the call has to be
// made by each command. Anything a person has to remember at nine call sites is
// something this test has to remember for them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { PRESALE_DIR, rpcUrl } from './genesis_lib.mjs';

const SOURCE = path.join(PRESALE_DIR, 'genesis_presale.mjs');

/** Split the file into top-level function bodies. */
function functionBodies() {
  const lines = fs.readFileSync(SOURCE, 'utf8').split('\n');
  const starts = [];
  lines.forEach((l, i) => {
    const m = /^(?:async )?function (\w+)/.exec(l);
    if (m) starts.push({ line: i, name: m[1] });
  });
  starts.push({ line: lines.length, name: '<eof>' });
  const out = [];
  for (let i = 0; i < starts.length - 1; i++) {
    out.push({ name: starts[i].name, body: lines.slice(starts[i].line, starts[i + 1].line).join('\n') });
  }
  return out;
}

test('every command that opens a connection asserts the cluster', () => {
  const connecting = functionBodies().filter((f) => f.body.includes('makeUmi('));
  assert.ok(connecting.length >= 9, `only found ${connecting.length} connecting commands — did the file move?`);

  const unguarded = connecting.filter((f) => !f.body.includes('assertDevnet')).map((f) => f.name);
  assert.deepEqual(
    unguarded,
    [],
    `these commands open an RPC connection without verifying the cluster: ${unguarded.join(', ')}.\n` +
      'A URL cannot identify a chain — assertDevnet asks it for its genesis hash. Without that\n' +
      'call, a rebranded mainnet endpoint signs real transactions from draft tooling.'
  );
});

test('the URL pre-check is not the cluster guard, and does not pretend to be', () => {
  // Pinned so nobody deletes assertDevnet on the theory that rpcUrl covers it.
  // Each of these is a real, reachable mainnet endpoint shape.
  for (const url of [
    'https://rpc.helius.xyz/?api-key=x',
    'https://alpha.quiknode.pro/abc/',
    'https://solana-rpc.publicnode.com',
    'http://10.0.0.5:8899',
  ]) {
    assert.equal(
      rpcUrl({ RPC_URL: url }),
      url,
      `rpcUrl refused ${url} — if the URL check has become authoritative, this test's premise ` +
        'is wrong and the comment in genesis_lib.mjs needs revisiting'
    );
  }
});

test('the pre-check is case-insensitive', () => {
  // `.includes` is case-sensitive; DNS is not. The literal mainnet endpoint in
  // capitals used to walk straight through.
  for (const url of [
    'https://api.mainnet-beta.solana.com',
    'https://API.MAINNET-BETA.SOLANA.COM',
    'https://Api.Mainnet-Beta.Solana.Com',
  ]) {
    assert.throws(() => rpcUrl({ RPC_URL: url }), /Refusing mainnet/, `${url} was accepted`);
  }
});
