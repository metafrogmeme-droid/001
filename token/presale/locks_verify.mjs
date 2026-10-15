#!/usr/bin/env node
// Smithii Token Vesting — READ the created locks back off mainnet and compare each with the plan
// (locks.plan.json) and with what the token record publishes (token/config/rclaw.mainnet.json).
//
//   npm run presale:locks-verify
//   (optional)  --rpc <url>   --snapshot <file>
//
// Read-only: it holds no key, signs nothing and sends nothing. Run it after each lock is created
// and before its address goes into the record; the website and the bot print what the record says.
// --snapshot writes every answer it read to <file>; token/presale/fixtures/smithii_locks_live.json
// is one, and the_record_states_the_locks.test.mjs runs this same check on it offline.
//
// Three outcomes, never two. Exit 0: everything read matched. Exit 1: a row FAILED. Exit 3:
// something could not be read (or a lock is not created yet), so nothing is claimed about it.
//
// The RPC comes from --rpc or SMITHII_RPC_URL, as for presale:smithii-verify; the genesis hash is
// checked first, and the URL is never printed (provider URLs carry API keys).
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

import { getAssociatedTokenAddressSync } from '@solana/spl-token';
import { PublicKey } from '@solana/web3.js';

import { MAINNET_GENESIS } from '../scripts/lib.mjs';
import { compareLock, compareLockVault, decodeVesting, loadLocksPlan, recordLocks } from './locks_lib.mjs';
import { loadAllocation } from './roadmap_table.mjs';
import { base58Encode, exitCodeFor, loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';
import { makeRpc } from './smithii_verify.mjs';

const DEFAULT_RPC = 'https://api.mainnet-beta.solana.com';

/** An upgradeable program's account: tag 2, then the address of its ProgramData account. */
export function programDataAddress(buf) {
  return buf.length >= 36 && buf.readUInt32LE(0) === 2 ? base58Encode(buf.subarray(4, 36)) : null;
}

/** A ProgramData account's head: tag 3, the slot it was last deployed in, then Option<upgrade authority>. */
export function programDataHead(buf) {
  if (buf.length < 13 || buf.readUInt32LE(0) !== 3) return null;
  const slot = Number(buf.readBigUInt64LE(4));
  const authority = buf.readUInt8(12) === 1 && buf.length >= 45 ? base58Encode(buf.subarray(13, 45)) : null;
  return { slot, authority };
}

const b64 = (value) => Buffer.from(value.data[0], 'base64');

/**
 * Run every check and return the rows. `rpcCall(method, params)` resolves to { ok, value } or
 * { ok: false, error }; the offline test hands in a recorded snapshot.
 *
 * Three states per read: a value (compare it), an answer of "no such account" (a FAIL: a working
 * node said so), and no answer (UNVERIFIED). A lock the record names no account for is not created
 * yet, and is UNVERIFIED too: nothing was read, so nothing is said about it.
 */
export async function verifyLocks({ plan, cfg, table, record, rpcCall, nowS }) {
  const rows = [];
  const add = (check, status, detail) => rows.push({ check, status, detail });
  const unverified = (check, why) => add(check, 'UNVERIFIED', `could not read: ${why}`);
  const context = [];

  const genesis = await rpcCall('getGenesisHash', []);
  if (!genesis.ok) {
    unverified('RPC is mainnet-beta', genesis.error);
    return { rows, context };
  }
  if (genesis.value !== MAINNET_GENESIS) {
    add('RPC is mainnet-beta', 'FAIL', `genesis hash ${genesis.value}; every answer below would be about another chain, so none is given`);
    return { rows, context };
  }
  add('RPC is mainnet-beta', 'PASS', 'genesis hash matches');

  // The program that holds every lock: who can change it, and whether it has changed since it was measured.
  const tool = plan.tool;
  const p = await rpcCall('getAccountInfo', [tool.program, { encoding: 'base64', dataSlice: { offset: 0, length: 36 } }]);
  if (!p.ok) unverified('the vesting program', p.error);
  else if (!p.value?.value) add('the vesting program exists', 'FAIL', `no account at ${tool.program}`);
  else {
    const pdAddr = programDataAddress(b64(p.value.value));
    if (!p.value.value.executable || !pdAddr) {
      add('the vesting program is an upgradeable program', 'FAIL', 'it is not executable, or not an upgradeable-loader program: what the plan says about its upgrade authority no longer describes it');
    } else {
      const pd = await rpcCall('getAccountInfo', [pdAddr, { encoding: 'base64', dataSlice: { offset: 0, length: 45 } }]);
      const head = pd.ok && pd.value?.value ? programDataHead(b64(pd.value.value)) : null;
      if (!pd.ok) unverified('the vesting program\'s upgrade authority', pd.error);
      else if (!head) add('the vesting program has a ProgramData account', 'FAIL', `none readable at ${pdAddr}`);
      else {
        add('the vesting program\'s upgrade authority is the one disclosed', head.authority === tool.programUpgradeAuthority ? 'PASS' : 'FAIL',
          `on chain ${head.authority ?? 'none (immutable)'}; disclosed ${tool.programUpgradeAuthority}`);
        add('the vesting program has not been redeployed since it was measured', head.slot === tool.programLastDeployedSlot ? 'PASS' : 'FAIL',
          `last deployed slot ${head.slot}; the plan pins ${tool.programLastDeployedSlot} (deployed ${tool.programLastDeployed})`);
      }
    }
  }

  const mint = new PublicKey(record.mint);
  const unit = 10n ** BigInt(record.decimals);
  const items = Array.isArray(record.locks?.items) ? record.locks.items : [];
  for (const planned of recordLocks(plan, { cfg, rows: table, record })) {
    const name = planned.bucket;
    const item = items.find((i) => i.bucket === name);
    if (!item) {
      add(`${name}: the record lists the lock`, 'FAIL', 'token/config/rclaw.mainnet.json has no entry for this bucket');
      continue;
    }
    if (item.vesting_account == null) {
      add(`${name}: the lock`, 'UNVERIFIED', 'not created yet: the record names no lock account');
      continue;
    }
    const want = { ...planned, claim_wallet: item.claim_wallet, vesting_account: item.vesting_account };
    const vault = getAssociatedTokenAddressSync(mint, new PublicKey(item.vesting_account), true).toBase58();
    context.push({ bucket: name, lock: item.vesting_account, vault });

    const a = await rpcCall('getAccountInfo', [item.vesting_account, { encoding: 'base64' }]);
    if (!a.ok) unverified(`${name}: the lock account`, a.error);
    else if (!a.value?.value) add(`${name}: the lock account exists`, 'FAIL', `no account at ${item.vesting_account}`);
    else {
      let vesting = null;
      try {
        vesting = decodeVesting(b64(a.value.value));
      } catch (e) {
        add(`${name}: the lock account decodes as a Vesting`, 'FAIL', e.message);
      }
      if (vesting) rows.push(...compareLock(want, unit, { owner: a.value.value.owner, vesting, programId: tool.program, mint: record.mint }));
    }

    // getAccountInfo rather than getTokenAccountBalance: a missing token account is an RPC error on the
    // second and a clean null on the first, and "no vault" must not be filed under "could not read".
    const v = await rpcCall('getAccountInfo', [vault, { encoding: 'jsonParsed' }]);
    if (!v.ok) unverified(`${name}: the vault`, v.error);
    else if (!v.value?.value) add(`${name}: the vault exists`, 'FAIL', `no token account at ${vault}`);
    else {
      const info = v.value.value.data?.parsed?.info;
      if (!info?.tokenAmount?.amount) add(`${name}: the vault is a token account`, 'FAIL', `the account at ${vault} is not a parsed token account`);
      else {
        rows.push(...compareLockVault(want, unit, {
          owner: info.owner, mint: info.mint, expectedMint: record.mint, amount: BigInt(info.tokenAmount.amount),
          delegate: info.delegate ?? null, closeAuthority: info.closeAuthority ?? null,
        }, { nowS }));
      }
    }
  }
  return { rows, context };
}

/** A caller that keeps every answer it got, for --snapshot. */
export function recording(rpcCall, log) {
  return async (method, params) => {
    const r = await rpcCall(method, params);
    if (r.ok) log.push({ method, params, value: r.value });
    return r;
  };
}

/** A caller that answers from a snapshot, and says "not read" for anything the snapshot does not hold. */
export function replaying(answers) {
  return async (method, params) => {
    const key = JSON.stringify(params);
    const hit = answers.find((x) => x.method === method && JSON.stringify(x.params) === key);
    return hit ? { ok: true, value: hit.value } : { ok: false, error: `${method}: not in the snapshot` };
  };
}

export function renderRows(rows, context) {
  const out = ['Smithii Token Vesting — the locks, read back from mainnet-beta (read-only)'];
  for (const c of context) out.push(`  ${c.bucket.padEnd(22)} lock ${c.lock}   vault ${c.vault}`);
  out.push('');
  const width = Math.max(...rows.map((r) => r.check.length));
  for (const r of rows) out.push(`  ${r.status.padEnd(10)} ${r.check.padEnd(width)}  ${r.detail}`);
  const code = exitCodeFor(rows);
  out.push('');
  out.push(code === 0
    ? 'RESULT: every lock that was read matches the plan and the record.'
    : code === 1
      ? 'RESULT: MISMATCH — a lock cannot be edited or cancelled; correct the record, the plan and the published schedule to what the chain holds, and say so.'
      : 'RESULT: COULD NOT BE CHECKED — nothing above that says UNVERIFIED is a statement about the chain.');
  return out.join('\n');
}

const SNAPSHOT_NOTE = [
  'Every answer `npm run presale:locks-verify -- --snapshot <this file>` read from Solana mainnet-beta, verbatim, at readAt.',
  'the_record_states_the_locks.test.mjs runs the same check on these answers offline, so the locks the record publishes are held to bytes read from the chain, not to bytes this repo wrote.',
  'Re-take it with the same command after a lock is created; never edit it by hand.',
];

function parseArgs(argv) {
  const get = (flag) => {
    const i = argv.indexOf(flag);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  return { rpc: get('--rpc') ?? process.env.SMITHII_RPC_URL ?? DEFAULT_RPC, snapshot: get('--snapshot') };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const log = [];
  const live = makeRpc(args.rpc);
  const rpcCall = args.snapshot ? recording(live, log) : live;
  const readAt = new Date();
  const result = await verifyLocks({
    plan: loadLocksPlan(), cfg: loadSmithiiConfig(), table: loadAllocation(), record: loadTokenRecord(),
    rpcCall, nowS: Math.floor(readAt.getTime() / 1000),
  });
  console.log(`RPC host: ${new URL(args.rpc).host}\n`);
  console.log(renderRows(result.rows, result.context));
  process.exitCode = exitCodeFor(result.rows);
  if (args.snapshot) {
    fs.writeFileSync(args.snapshot, JSON.stringify({ _comment: SNAPSHOT_NOTE, readAt: readAt.toISOString(), answers: log }, null, 2) + '\n');
    console.log(`\nsnapshot: ${log.length} answers written to ${args.snapshot}`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
