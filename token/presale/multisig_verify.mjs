#!/usr/bin/env node
// Squads v4 — READ the treasury multisig back off mainnet and compare it with what the token record
// publishes (token/config/rclaw.mainnet.json, its multisig block).
//
//   npm run presale:multisig-verify
//   (optional)  --rpc <url>   --snapshot <file>
//
// Read-only: it holds no key, signs nothing and sends nothing. Run it after any change to the
// multisig and after any transfer into or out of its vault; the website and the bot print what the
// record says. --snapshot writes every answer it read to <file>;
// token/presale/fixtures/squads_multisig_live.json is one, and
// the_record_states_the_multisig.test.mjs runs this same check on it offline.
//
// Three outcomes, never two. Exit 0: everything read matched (an open proposal is a WARN, and still
// exit 0: it is a fact to look at, not a mismatch). Exit 1: a row FAILED. Exit 3: something could
// not be read, so nothing is claimed about it.
//
// The RPC comes from --rpc or SMITHII_RPC_URL, as for the other verifiers; the genesis hash is
// checked first, and the URL is never printed (provider URLs carry API keys).
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

import { MAINNET_GENESIS } from '../scripts/lib.mjs';
import { programDataAddress, programDataHead, recording, replaying } from './locks_verify.mjs';
import { compareMultisig, compareProposals, compareVaultHoldings, decodeMultisig, decodeProposal, proposalAddress } from './multisig_lib.mjs';
import { exitCodeFor, loadTokenRecord } from './smithii_lib.mjs';
import { makeRpc } from './smithii_verify.mjs';

const DEFAULT_RPC = 'https://api.mainnet-beta.solana.com';
/** getMultipleAccounts takes at most 100 addresses. */
const BATCH = 100;

const b64 = (value) => Buffer.from(value.data[0], 'base64');

export { replaying };

/**
 * Run every check and return the rows. `rpcCall(method, params)` resolves to { ok, value } or
 * { ok: false, error }; the offline test hands in a recorded snapshot. Three states per read: a
 * value (compare it), an answer of "no such account" (a FAIL: a working node said so), and no
 * answer (UNVERIFIED).
 */
export async function verifyMultisig({ record, rpcCall }) {
  const rows = [];
  const add = (check, status, detail) => rows.push({ check, status, detail });
  const unverified = (check, why) => add(check, 'UNVERIFIED', `could not read: ${why}`);
  const want = record.multisig;
  const context = want ? { account: want.account, vault: want.vault } : null;

  if (!want || typeof want !== 'object') {
    add('the record names a multisig', 'FAIL', 'token/config/rclaw.mainnet.json has no multisig block');
    return { rows, context };
  }

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

  // The program that enforces the approvals and the delay: if it could be upgraded, both would be
  // only as strong as whoever holds that key.
  const p = await rpcCall('getAccountInfo', [want.program, { encoding: 'base64', dataSlice: { offset: 0, length: 36 } }]);
  const immutable = 'the Squads v4 program cannot be upgraded';
  if (!p.ok) unverified(immutable, p.error);
  else if (!p.value?.value) add(immutable, 'FAIL', `no account at ${want.program}`);
  else {
    const pdAddr = programDataAddress(b64(p.value.value));
    if (!p.value.value.executable || !pdAddr) add(immutable, 'FAIL', 'it is not executable, or not an upgradeable-loader program');
    else {
      const pd = await rpcCall('getAccountInfo', [pdAddr, { encoding: 'base64', dataSlice: { offset: 0, length: 45 } }]);
      const head = pd.ok && pd.value?.value ? programDataHead(b64(pd.value.value)) : null;
      if (!pd.ok) unverified(immutable, pd.error);
      else if (!head) add(immutable, 'FAIL', `no ProgramData readable at ${pdAddr}`);
      else {
        add(immutable, head.authority === null ? 'PASS' : 'FAIL', head.authority === null
          ? `no upgrade authority (last deployed in slot ${head.slot})`
          : `upgrade authority ${head.authority}: whoever holds it can replace the code that enforces the approvals and the time lock`);
      }
    }
  }

  let multisig = null;
  const a = await rpcCall('getAccountInfo', [want.account, { encoding: 'base64' }]);
  if (!a.ok) unverified('the multisig account', a.error);
  else if (!a.value?.value) add('the multisig account exists', 'FAIL', `no account at ${want.account}`);
  else {
    try {
      multisig = decodeMultisig(b64(a.value.value));
    } catch (e) {
      add('the multisig account decodes as a Squads v4 Multisig', 'FAIL', e.message);
    }
    if (multisig) rows.push(...compareMultisig(want, { owner: a.value.value.owner, multisig }));
  }

  const t = await rpcCall('getTokenAccountsByOwner', [want.vault, { mint: record.mint }, { encoding: 'jsonParsed' }]);
  if (!t.ok) unverified('the vault\'s RCLAW', t.error);
  else {
    const accounts = [];
    let unparsed = null;
    for (const entry of t.value?.value ?? []) {
      const info = entry.account?.data?.parsed?.info;
      if (!info?.tokenAmount?.amount || info.mint !== record.mint || info.owner !== want.vault) {
        unparsed = entry.pubkey;
        break;
      }
      accounts.push({ address: entry.pubkey, amount: BigInt(info.tokenAmount.amount), delegate: info.delegate ?? null, closeAuthority: info.closeAuthority ?? null });
    }
    if (unparsed) add('the vault\'s token accounts are RCLAW accounts it owns', 'FAIL', `${unparsed} is not a parsed RCLAW token account owned by the vault`);
    else rows.push(...compareVaultHoldings(want, record.decimals, accounts));
  }

  // Every proposal ever created, by index: an approved one stays executable even once stale.
  if (multisig) {
    const count = Number(multisig.transactionIndex);
    const indexes = Array.from({ length: count }, (_, i) => i + 1);
    const proposals = [];
    let readAll = true;
    for (let i = 0; i < indexes.length && readAll; i += BATCH) {
      const chunk = indexes.slice(i, i + BATCH);
      const r = await rpcCall('getMultipleAccounts', [chunk.map((n) => proposalAddress(want.program, want.account, n)), { encoding: 'base64' }]);
      if (!r.ok) {
        unverified('the proposals', r.error);
        readAll = false;
        break;
      }
      chunk.forEach((n, j) => {
        const acct = r.value?.value?.[j];
        if (!acct) {
          proposals.push({ index: n, decoded: null });
          return;
        }
        try {
          proposals.push({ index: n, decoded: decodeProposal(b64(acct)) });
        } catch (e) {
          add(`proposal #${n} decodes`, 'FAIL', e.message);
          readAll = false;
        }
      });
    }
    if (readAll) rows.push(...compareProposals(multisig.staleTransactionIndex, proposals));
  }
  return { rows, context };
}

export function renderRows(rows, context) {
  const out = ['Squads v4 — the treasury multisig, read back from mainnet-beta (read-only)'];
  if (context) out.push(`  multisig ${context.account}   vault ${context.vault}`);
  out.push('');
  const width = Math.max(...rows.map((r) => r.check.length));
  for (const r of rows) out.push(`  ${r.status.padEnd(10)} ${r.check.padEnd(width)}  ${r.detail}`);
  const code = exitCodeFor(rows);
  out.push('');
  out.push(code === 0
    ? (rows.some((r) => r.status === 'WARN')
      ? 'RESULT: the multisig matches the record, AND a proposal is open: read the WARN rows.'
      : 'RESULT: the multisig matches the record.')
    : code === 1
      ? 'RESULT: MISMATCH — the record, and every page that prints it, says something the chain does not: re-take the snapshot and correct the record, or find out what changed.'
      : 'RESULT: COULD NOT BE CHECKED — nothing above that says UNVERIFIED is a statement about the chain.');
  return out.join('\n');
}

const SNAPSHOT_NOTE = [
  'Every answer `npm run presale:multisig-verify -- --snapshot <this file>` read from Solana mainnet-beta, verbatim, at readAt.',
  'the_record_states_the_multisig.test.mjs runs the same check on these answers offline, so the multisig the record publishes is held to bytes read from the chain, not to bytes this repo wrote.',
  'Re-take it with the same command after any change to the multisig or any transfer into or out of its vault; never edit it by hand.',
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
  const result = await verifyMultisig({ record: loadTokenRecord(), rpcCall });
  console.log(`RPC host: ${new URL(args.rpc).host}\n`);
  console.log(renderRows(result.rows, result.context));
  process.exitCode = exitCodeFor(result.rows);
  if (args.snapshot) {
    fs.writeFileSync(args.snapshot, JSON.stringify({ _comment: SNAPSHOT_NOTE, readAt: readAt.toISOString(), answers: log }, null, 2) + '\n');
    console.log(`\nsnapshot: ${log.length} answers written to ${args.snapshot}`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
