// The read-back command, run against a fake chain it cannot tell from a real
// one: every RPC answer is canned, and every address the verifier asks for is
// recorded, so a check that quietly stopped reading something shows up here.
//
// The shape this guards is the one CLAUDE.md names first: unreadable is never
// zero. A node that does not answer must produce UNVERIFIED, never a PASS, and a
// node that answers "no such account" must produce a FAIL, never UNVERIFIED —
// three states, three different sentences.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PublicKey } from '@solana/web3.js';
import { getAssociatedTokenAddressSync } from '@solana/spl-token';

import { MAINNET_GENESIS } from '../scripts/lib.mjs';
import { deriveSmithiiSale, exitCodeFor, loadSmithiiConfig, loadTokenRecord, LAUNCH_DISCRIMINATOR_HEX } from './smithii_lib.mjs';
import { makeRpc, renderRows, verifySmithii } from './smithii_verify.mjs';

const cfg = () => structuredClone(loadSmithiiConfig());
const record = loadTokenRecord();
const AUTHORITY = 'EEoMVamYkEvZEDXe7cyMGCLg5BUGNAuvSXC1w2LWELVy';
const START = 1_800_000_000;
const before = START - 86400;

const programId = new PublicKey(cfg().program.id);
const mintKey = new PublicKey(cfg().token.mint);
const [launchKey] = PublicKey.findProgramAddressSync(
  [Buffer.from('launch'), mintKey.toBuffer(), new PublicKey(AUTHORITY).toBuffer()], programId);
const vaultKey = getAssociatedTokenAddressSync(mintKey, launchKey, true);
const PROGRAM_DATA = 'BvDccYscP3NHWbFV6xSgYzar5wcbYo83J15gSTU7B4dk';

/** The bytes of a Launch account, written by the documented offsets. */
function launchBytes({ authority = AUTHORITY, mint = cfg().token.mint, hardcap = 5000n * 10n ** 9n, softcap = 1000n * 10n ** 9n,
  sold = 0n, price = 33333n, start = BigInt(START), end = BigInt(START + 72 * 3600), min = 250000000n, max = 25n * 10n ** 9n,
  wlPrice = 0n, payment = 0 } = {}) {
  const b = Buffer.alloc(192);
  Buffer.from(LAUNCH_DISCRIMINATOR_HEX, 'hex').copy(b, 0);
  new PublicKey(authority).toBuffer().copy(b, 8);
  new PublicKey(mint).toBuffer().copy(b, 40);
  b.writeBigUInt64LE(hardcap, 72);
  b.writeBigUInt64LE(softcap, 80);
  b.writeBigUInt64LE(sold, 88);
  b.writeBigUInt64LE(wlPrice, 96); // whitelist price; the rest of that phase stays zero
  for (const [i, v] of [price, start, end, min, max].entries()) b.writeBigUInt64LE(v, 144 + 8 * i);
  b[184] = payment;
  return b;
}

/**
 * A chain. `overrides` maps "method:address" to a replacement answer, where an
 * answer is { ok, value } as the real caller returns. `calls` records every ask.
 */
function fakeChain(overrides = {}, { launch = launchBytes() } = {}) {
  const c = cfg();
  const d = deriveSmithiiSale(c);
  const calls = [];
  const table = {
    'getGenesisHash:': { ok: true, value: MAINNET_GENESIS },
    [`getAccountInfo:${c.token.mint}`]: { ok: true, value: { value: {
      owner: record.token_program,
      data: { parsed: { info: { decimals: 9, supply: record.supply_base_units, mintAuthority: null, freezeAuthority: null } } },
    } } },
    [`getAccountInfo:${c.program.id}`]: { ok: true, value: { value: {
      executable: true, owner: 'BPFLoaderUpgradeab1e11111111111111111111111',
      data: { parsed: { info: { programData: PROGRAM_DATA } } },
    } } },
    [`getAccountInfo:${PROGRAM_DATA}`]: { ok: true, value: { value: {
      data: { parsed: { info: { authority: c.program.upgradeAuthority, slot: c.program.lastDeployedSlot } } },
    } } },
    [`getAccountInfo:${launchKey.toBase58()}`]: { ok: true, value: { value: { owner: c.program.id, data: [launch.toString('base64'), 'base64'] } } },
    [`getAccountInfo:${vaultKey.toBase58()}`]: { ok: true, value: { value: {
      data: { parsed: { info: { tokenAmount: { amount: d.escrowAtCreateBase.toString() } } } },
    } } },
    ...overrides,
  };
  const rpcCall = async (method, params) => {
    const key = `${method}:${typeof params[0] === 'string' ? params[0] : ''}`;
    calls.push(key);
    if (!(key in table)) return { ok: false, error: `${method}: not canned (${key})` };
    return table[key];
  };
  return { rpcCall, calls };
}

const run = (chain, over = {}) => verifySmithii({ cfg: cfg(), record, authority: AUTHORITY, rpcCall: chain.rpcCall, nowSeconds: before, ...over });
const statusOf = (rows, check) => rows.find((r) => r.check === check)?.status;
const failing = (rows) => rows.filter((r) => r.status === 'FAIL').map((r) => r.check);

// ── the derivations, against a real launch ──────────────────────────────────
//
// The tests below build their fake chain from the SAME seeds the verifier uses,
// so they cannot tell whether the seeds are right. These two can: they derive
// the launch and its vault from a real third-party sale's mint and authority
// and must land on the addresses mainnet actually holds.

const live = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'smithii_launch_live.json'), 'utf8'));

test('["launch", mint, authority] under the program id is the address a real launch lives at', () => {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from('launch'), new PublicKey(live.expected.mint).toBuffer(), new PublicKey(live.expected.authority).toBuffer()],
    new PublicKey(live.program));
  assert.equal(pda.toBase58(), live.launch);
});

test('the vault is the launch\'s associated token account, allowing the off-curve owner', () => {
  const ata = getAssociatedTokenAddressSync(new PublicKey(live.expected.mint), new PublicKey(live.launch), true);
  assert.equal(ata.toBase58(), live.vault.address);
  assert.throws(() => getAssociatedTokenAddressSync(new PublicKey(live.expected.mint), new PublicKey(live.launch), false),
    'without allowOwnerOffCurve a PDA owner is refused, which is why the verifier passes true');
});

test('a chain that matches the config verifies: exit 0, and every account was actually read', async () => {
  const chain = fakeChain();
  const { rows, context } = await run(chain);
  assert.deepEqual(failing(rows), []);
  assert.equal(exitCodeFor(rows), 0);
  assert.equal(context.launch, launchKey.toBase58());
  // The checks must have asked for the mint, the program, its ProgramData, the launch and the vault.
  for (const want of ['getGenesisHash:', `getAccountInfo:${cfg().token.mint}`, `getAccountInfo:${cfg().program.id}`,
    `getAccountInfo:${PROGRAM_DATA}`, `getAccountInfo:${launchKey.toBase58()}`, `getAccountInfo:${vaultKey.toBase58()}`]) {
    assert.ok(chain.calls.includes(want), `never asked for ${want}`);
  }
  // 1 cluster + 5 mint + 3 program + 1 ownership + 13 launch + 1 vault. An exact
  // count, so a dropped check and a silently ADDED one both stop here and are
  // decided on purpose.
  assert.equal(rows.length, 24, `the rows moved: ${rows.map((r) => r.check).join(' | ')}`);
});

test('the launch address is derived from the program, the mint and the authority — and the hint is checked against it', async () => {
  assert.equal((await run(fakeChain(), { launchHint: launchKey.toBase58() })).rows.find((r) => r.check === 'the launch address Smithii showed').status, 'PASS');
  const wrong = await run(fakeChain(), { launchHint: vaultKey.toBase58() });
  assert.equal(statusOf(wrong.rows, 'the launch address Smithii showed'), 'FAIL');
});

test('a wrong chain stops everything: no row below the cluster check is a statement', async () => {
  const chain = fakeChain({ 'getGenesisHash:': { ok: true, value: 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG' } });
  const { rows } = await run(chain);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'FAIL');
  assert.equal(exitCodeFor(rows), 1);
  assert.deepEqual(chain.calls, ['getGenesisHash:'], 'nothing else was asked of a node that is not mainnet');
});

test('a node that does not answer is UNVERIFIED, never a pass', async () => {
  const dead = { ok: false, error: 'getAccountInfo: HTTP 503' };
  const { rows } = await run(fakeChain({ [`getAccountInfo:${launchKey.toBase58()}`]: dead, [`getAccountInfo:${vaultKey.toBase58()}`]: dead }));
  assert.equal(statusOf(rows, 'the launch account'), 'UNVERIFIED');
  assert.equal(statusOf(rows, 'the vault balance'), 'UNVERIFIED');
  assert.equal(exitCodeFor(rows), 3);
  assert.ok(!rows.some((r) => /launch is owned|hard cap|sale price/.test(r.check)), 'no claim is made about a launch that was never read');

  const noCluster = await run(fakeChain({ 'getGenesisHash:': { ok: false, error: 'getGenesisHash: network error' } }));
  assert.equal(noCluster.rows.length, 1);
  assert.equal(exitCodeFor(noCluster.rows), 3);
});

test('a node that says "no such account" is a FAIL, which is a different sentence from UNVERIFIED', async () => {
  const none = { ok: true, value: { value: null } };
  const missingLaunch = await run(fakeChain({ [`getAccountInfo:${launchKey.toBase58()}`]: none }));
  assert.equal(statusOf(missingLaunch.rows, 'the launch exists'), 'FAIL');
  assert.match(missingLaunch.rows.find((r) => r.check === 'the launch exists').detail, /Create has not happened/);
  assert.equal(exitCodeFor(missingLaunch.rows), 1);

  const missingVault = await run(fakeChain({ [`getAccountInfo:${vaultKey.toBase58()}`]: none }));
  assert.equal(statusOf(missingVault.rows, 'the vault exists'), 'FAIL');

  const missingMint = await run(fakeChain({ [`getAccountInfo:${cfg().token.mint}`]: none }));
  assert.equal(statusOf(missingMint.rows, 'the mint exists'), 'FAIL');
});

test('the program moving is a FAIL: a new upgrade authority, a redeploy, or an immutable program', async () => {
  const c = cfg();
  const pd = (info) => ({ [`getAccountInfo:${PROGRAM_DATA}`]: { ok: true, value: { value: { data: { parsed: { info } } } } } });
  const moved = await run(fakeChain(pd({ authority: AUTHORITY, slot: c.program.lastDeployedSlot })));
  assert.deepEqual(failing(moved.rows), ['upgrade authority is the one disclosed']);
  const redeployed = await run(fakeChain(pd({ authority: c.program.upgradeAuthority, slot: c.program.lastDeployedSlot + 5 })));
  assert.deepEqual(failing(redeployed.rows), ['program has not been redeployed since it was measured']);
  const immutable = await run(fakeChain(pd({ slot: c.program.lastDeployedSlot })));
  assert.deepEqual(failing(immutable.rows), ['upgrade authority is the one disclosed']);
  const notUpgradeable = await run(fakeChain({ [`getAccountInfo:${c.program.id}`]: { ok: true, value: { value: { executable: true, data: { parsed: { info: {} } } } } } }));
  assert.ok(failing(notUpgradeable.rows).includes('the program has a ProgramData account'));
});

test('a launch on chain that differs from the config fails the row that differs, and the vault catches a short escrow', async () => {
  const c = cfg();
  const launchOver = (bytes) => ({ [`getAccountInfo:${launchKey.toBase58()}`]: { ok: true, value: { value: { owner: c.program.id, data: [bytes.toString('base64'), 'base64'] } } } });
  const price = await run(fakeChain(launchOver(launchBytes({ price: 33334n }))));
  assert.deepEqual(failing(price.rows), ['sale price']);
  const hard = await run(fakeChain(launchOver(launchBytes({ hardcap: 4000n * 10n ** 9n }))));
  assert.deepEqual(failing(hard.rows), ['hard cap']);
  const wl = await run(fakeChain(launchOver(launchBytes({ wlPrice: 1n }))));
  assert.deepEqual(failing(wl.rows), ['whitelist phase is off']);
  // The payment byte, read through the real byte layout rather than a decoded object.
  const usdc = await run(fakeChain(launchOver(launchBytes({ payment: 2 }))));
  assert.deepEqual(failing(usdc.rows), ['paid in SOL']);
  const notOwned = await run(fakeChain({ [`getAccountInfo:${launchKey.toBase58()}`]: { ok: true, value: { value: { owner: AUTHORITY, data: [launchBytes().toString('base64'), 'base64'] } } } }));
  assert.deepEqual(failing(notOwned.rows), ['the launch is owned by the sale program']);

  const short = await run(fakeChain({ [`getAccountInfo:${vaultKey.toBase58()}`]: { ok: true, value: { value: { data: { parsed: { info: { tokenAmount: { amount: '1' } } } } } } } }));
  assert.deepEqual(failing(short.rows), ['vault holds hard cap / price tokens']);
});

test('after the start the edit window is a WARN, so a FAIL stands but is not hidden', async () => {
  const late = await run(fakeChain(), { nowSeconds: START + 3600 });
  assert.equal(statusOf(late.rows, 'edit window'), 'WARN');
  assert.deepEqual(failing(late.rows), []);

  // The verifier hands "has it started" to the vault check, which then reads a
  // smaller balance as claims or a withdraw rather than as a short escrow.
  const lessInVault = { [`getAccountInfo:${vaultKey.toBase58()}`]: { ok: true, value: { value: { data: { parsed: { info: { tokenAmount: { amount: '1' } } } } } } } };
  const started = await run(fakeChain(lessInVault), { nowSeconds: START + 3600 });
  assert.equal(statusOf(started.rows, 'vault holds hard cap / price tokens'), 'WARN');
  assert.equal(exitCodeFor(started.rows), 0);
  const notStarted = await run(fakeChain(lessInVault), { nowSeconds: before });
  assert.equal(statusOf(notStarted.rows, 'vault holds hard cap / price tokens'), 'FAIL');
});

test('the result line says which of the three things happened', async () => {
  const ok = await run(fakeChain());
  assert.match(renderRows(ok.rows, ok.context, cfg()), /RESULT: everything that was read matches/);
  // Before Create the launch and its vault are absent and nothing else is wrong:
  // that is "not created yet", not a mismatch to repair with Smithii's edit.
  const none = { ok: true, value: { value: null } };
  const early = await run(fakeChain({ [`getAccountInfo:${launchKey.toBase58()}`]: none, [`getAccountInfo:${vaultKey.toBase58()}`]: none }));
  assert.match(renderRows(early.rows, early.context, cfg()), /RESULT: NOT CREATED YET/);
  assert.equal(exitCodeFor(early.rows), 1, 'still a non-zero exit: nothing has been verified');
  // …but an absent launch alongside a real mismatch elsewhere is a MISMATCH.
  const wrongProgram = await run(fakeChain({
    [`getAccountInfo:${launchKey.toBase58()}`]: none, [`getAccountInfo:${vaultKey.toBase58()}`]: none,
    [`getAccountInfo:${PROGRAM_DATA}`]: { ok: true, value: { value: { data: { parsed: { info: { authority: AUTHORITY, slot: cfg().program.lastDeployedSlot } } } } } },
  }));
  assert.match(renderRows(wrongProgram.rows, wrongProgram.context, cfg()), /RESULT: MISMATCH/);
  const bad = await run(fakeChain({ [`getAccountInfo:${vaultKey.toBase58()}`]: none }));
  assert.match(renderRows(bad.rows, bad.context, cfg()), /RESULT: NOT CREATED YET/, 'a vault alone absent is the same state');
  const mismatch = await run(fakeChain({ [`getAccountInfo:${launchKey.toBase58()}`]: { ok: true, value: { value: { owner: AUTHORITY, data: [launchBytes().toString('base64'), 'base64'] } } } }));
  assert.match(renderRows(mismatch.rows, mismatch.context, cfg()), /RESULT: MISMATCH/);
  const dead = await run(fakeChain({ 'getGenesisHash:': { ok: false, error: 'x' } }));
  assert.match(renderRows(dead.rows, dead.context, cfg()), /RESULT: COULD NOT BE CHECKED/);
});

// ── the RPC caller ──────────────────────────────────────────────────────────

const respond = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

test('a rate limit is retried, a result is returned, and the URL is never in an error', async () => {
  const url = 'https://rpc.example.invalid/?api-key=SECRET-KEY-123';
  const seen = [];
  const sleeps = [];
  let n = 0;
  const call = makeRpc(url, {
    fetchImpl: async (u) => { seen.push(u); n += 1; return n < 3 ? respond(429, {}) : respond(200, { result: 'ok' }); },
    sleepImpl: async (ms) => { sleeps.push(ms); },
  });
  assert.deepEqual(await call('getSlot', []), { ok: true, value: 'ok' });
  assert.equal(n, 3, 'two 429s, then the answer');
  assert.deepEqual(sleeps, [1500, 3000], 'backs off');

  const always429 = makeRpc(url, { fetchImpl: async () => respond(429, {}), sleepImpl: async () => {}, tries: 3 });
  const r = await always429('getSlot', []);
  assert.equal(r.ok, false);
  assert.match(r.error, /HTTP 429/);
  assert.ok(!r.error.includes('SECRET-KEY-123') && !r.error.includes('example.invalid'), 'the URL (and its key) must not reach a message');

  const boom = makeRpc(url, { fetchImpl: async () => { throw new TypeError('connect ECONNREFUSED rpc.example.invalid/?api-key=SECRET-KEY-123'); }, sleepImpl: async () => {}, tries: 2 });
  const b = await boom('getSlot', []);
  assert.deepEqual(b, { ok: false, error: 'getSlot: TypeError' }, 'names the class, never the message');

  const rpcErr = await makeRpc(url, { fetchImpl: async () => respond(200, { error: { code: -32005, message: 'node is behind by 99999 slots' } }) })('getSlot', []);
  assert.deepEqual(rpcErr, { ok: false, error: 'getSlot: rpc error -32005' });
  const html = await makeRpc(url, { fetchImpl: async () => ({ status: 200, ok: true, json: async () => { throw new SyntaxError('<html>'); } }) })('getSlot', []);
  assert.deepEqual(html, { ok: false, error: 'getSlot: unreadable response' });
});
