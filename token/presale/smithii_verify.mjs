#!/usr/bin/env node
// Smithii presale — READ the created launch back off mainnet and compare every
// claim in smithii.config.json with what is actually on chain.
//
//   npm run presale:smithii-verify -- --authority <wallet that signed Create>
//   (optional)  --launch <address Smithii showed>   --rpc <url>
//
// Read-only: it holds no key, signs nothing and sends nothing. Run it right
// after Create and BEFORE the first phase starts — Smithii's edit instruction
// only works until then, so this is the moment a wrong value can still be
// fixed, and the only moment it is cheap.
//
// Three outcomes, never two. Exit 0: everything read matched. Exit 1: a row
// FAILED. Exit 3: something could not be read, so nothing is claimed about it.
// A reading that did not happen is never printed as a match.
//
// The RPC comes from --rpc or SMITHII_RPC_URL, deliberately NOT from RPC_URL,
// which the Genesis tooling points at devnet. The first thing checked is the
// genesis hash, so a devnet endpoint cannot return a confident wrong answer,
// and the URL is never printed (provider URLs carry API keys).
import { pathToFileURL } from 'node:url';

import { getAssociatedTokenAddressSync } from '@solana/spl-token';
import { PublicKey } from '@solana/web3.js';

import { MAINNET_GENESIS } from '../scripts/lib.mjs';
import {
  compareLaunch,
  compareMint,
  compareProgram,
  compareVault,
  decodeLaunch,
  deriveSmithiiSale,
  exitCodeFor,
  formatUnits,
  loadSmithiiConfig,
  loadTokenRecord,
} from './smithii_lib.mjs';

const DEFAULT_RPC = 'https://api.mainnet-beta.solana.com';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * A JSON-RPC caller that retries a rate limit and reports failure as a value.
 * Errors name the method and the status, never the URL or the response body.
 */
export function makeRpc(url, { fetchImpl = fetch, sleepImpl = sleep, tries = 5 } = {}) {
  return async function rpcCall(method, params) {
    let delay = 1500;
    for (let attempt = 1; attempt <= tries; attempt += 1) {
      let res;
      try {
        res = await fetchImpl(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
          signal: AbortSignal.timeout(30000),
        });
      } catch (e) {
        if (attempt === tries) return { ok: false, error: `${method}: ${e?.name ?? 'network error'}` };
        await sleepImpl(delay);
        delay = Math.min(delay * 2, 15000);
        continue;
      }
      if (res.status === 429 && attempt < tries) {
        await sleepImpl(delay);
        delay = Math.min(delay * 2, 15000);
        continue;
      }
      if (!res.ok) return { ok: false, error: `${method}: HTTP ${res.status}` };
      let body;
      try {
        body = await res.json();
      } catch {
        return { ok: false, error: `${method}: unreadable response` };
      }
      if (body.error) return { ok: false, error: `${method}: rpc error ${body.error.code}` };
      return { ok: true, value: body.result };
    }
    return { ok: false, error: `${method}: gave up` };
  };
}

/**
 * Run every check and return the rows. `rpcCall(method, params)` resolves to
 * { ok, value } or { ok: false, error }; a test hands in canned answers.
 *
 * Three states per read, never two: a value (compare it), an answer of "no such
 * account" (a FAIL — a working node said so), and no answer at all (UNVERIFIED).
 */
export async function verifySmithii({ cfg, record, authority, launchHint, rpcCall, nowSeconds }) {
  const rows = [];
  const add = (check, status, detail) => rows.push({ check, status, detail });
  const unverified = (check, why) => add(check, 'UNVERIFIED', `could not read: ${why}`);

  const mint = new PublicKey(cfg.token.mint);
  const programId = new PublicKey(cfg.program.id);
  const authorityKey = new PublicKey(authority);
  const [launchKey] = PublicKey.findProgramAddressSync(
    [Buffer.from('launch'), mint.toBuffer(), authorityKey.toBuffer()], programId);
  const vaultKey = getAssociatedTokenAddressSync(mint, launchKey, true);
  const context = { launch: launchKey.toBase58(), vault: vaultKey.toBase58() };

  // 0. The right chain, or nothing below means anything.
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

  // 1. The token.
  const m = await rpcCall('getAccountInfo', [cfg.token.mint, { encoding: 'jsonParsed' }]);
  if (!m.ok) unverified('the mint', m.error);
  else if (!m.value?.value) add('the mint exists', 'FAIL', `no account at ${cfg.token.mint}`);
  else {
    const v = m.value.value;
    const info = v.data?.parsed?.info ?? {};
    rows.push(...compareMint(cfg, record, {
      owner: v.owner,
      mintAuthority: info.mintAuthority ?? null,
      freezeAuthority: info.freezeAuthority ?? null,
      decimals: info.decimals,
      supplyBase: info.supply,
    }));
  }

  // 2. The program: executable, upgrade authority, last deploy.
  const p = await rpcCall('getAccountInfo', [cfg.program.id, { encoding: 'jsonParsed' }]);
  if (!p.ok) unverified('the program', p.error);
  else if (!p.value?.value) add('the program exists', 'FAIL', `no account at ${cfg.program.id}`);
  else {
    const programData = p.value.value.data?.parsed?.info?.programData;
    const pd = programData ? await rpcCall('getAccountInfo', [programData, { encoding: 'jsonParsed' }]) : null;
    if (!pd) add('the program has a ProgramData account', 'FAIL', 'it is not an upgradeable-loader program any more: the disclosures about its upgrade authority no longer describe it');
    else if (!pd.ok) unverified('the program\'s upgrade authority', pd.error);
    else {
      const info = pd.value?.value?.data?.parsed?.info ?? {};
      rows.push(...compareProgram(cfg, {
        executable: p.value.value.executable,
        upgradeAuthority: info.authority ?? null,
        lastDeployedSlot: info.slot,
      }));
    }
  }

  // 3. The launch itself.
  if (launchHint && launchHint !== context.launch) {
    add('the launch address Smithii showed', 'FAIL', `Smithii showed ${launchHint}; this authority and mint derive ${context.launch}`);
  } else if (launchHint) {
    add('the launch address Smithii showed', 'PASS', 'equals the address derived from this authority and mint');
  }
  const l = await rpcCall('getAccountInfo', [context.launch, { encoding: 'base64' }]);
  let started = false;
  if (!l.ok) unverified('the launch account', l.error);
  else if (!l.value?.value) {
    add('the launch exists', 'FAIL', `no Launch account at ${context.launch}: Create has not happened, or the wrong wallet was given`);
  } else {
    const acct = l.value.value;
    add('the launch is owned by the sale program', acct.owner === cfg.program.id ? 'PASS' : 'FAIL', `owner ${acct.owner}`);
    let launch;
    try {
      launch = decodeLaunch(Buffer.from(acct.data[0], 'base64'));
    } catch (e) {
      add('the launch account decodes', 'FAIL', e.message);
    }
    if (launch) {
      started = nowSeconds >= Number(launch.publicPhase.startDate);
      rows.push(...compareLaunch(cfg, launch, { authority, nowSeconds }));
    }
  }

  // 4. The vault: hard cap / the STORED price, on chain (not the form's "Sending" figure, which
  // divides by the typed price and is slightly lower). getAccountInfo rather than
  // getTokenAccountBalance, because a missing token account is an RPC ERROR on
  // the second and a clean `null` on the first — and "no vault" must not be
  // filed under "could not read".
  const v = await rpcCall('getAccountInfo', [context.vault, { encoding: 'jsonParsed' }]);
  if (!v.ok) unverified('the vault balance', v.error);
  else if (!v.value?.value) add('the vault exists', 'FAIL', `no token account at ${context.vault}: Create has not moved the tokens in`);
  else {
    const amount = v.value.value.data?.parsed?.info?.tokenAmount?.amount;
    if (amount === undefined) add('the vault is a token account', 'FAIL', 'the account at the vault address is not a parsed token account');
    else rows.push(...compareVault(cfg, BigInt(amount), { started }));
  }
  return { rows, context };
}

export function renderRows(rows, context, cfg) {
  const d = deriveSmithiiSale(cfg);
  const out = [
    'Smithii presale — read-back from mainnet-beta (read-only)',
    `  launch  ${context.launch}`,
    `  vault   ${context.vault}   (should hold ${formatUnits(d.escrowAtCreateBase, d.decimals)} ${cfg.token.symbol})`,
    '',
  ];
  const width = Math.max(...rows.map((r) => r.check.length));
  for (const r of rows) out.push(`  ${r.status.padEnd(10)} ${r.check.padEnd(width)}  ${r.detail}`);
  const code = exitCodeFor(rows);
  const failed = rows.filter((r) => r.status === 'FAIL').map((r) => r.check);
  // Before Create the launch and its vault do not exist, and everything else can
  // already be checked. That is a state, not a mismatch to fix with `edit`.
  const notCreated = failed.length > 0 && failed.every((c) => c === 'the launch exists' || c === 'the vault exists');
  out.push('');
  out.push(code === 0
    ? 'RESULT: everything that was read matches the config.'
    : notCreated
      ? 'RESULT: NOT CREATED YET — the rows above that passed (mint, program) match the disclosures; run this again right after Create.'
      : code === 1
        ? 'RESULT: MISMATCH — fix it with Smithii\'s edit before the first phase starts, or change the config and the published terms to match.'
        : 'RESULT: COULD NOT BE CHECKED — nothing above that says UNVERIFIED is a statement about the chain.');
  return out.join('\n');
}

function parseArgs(argv) {
  const get = (flag) => {
    const i = argv.indexOf(flag);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  return { authority: get('--authority'), launch: get('--launch'), rpc: get('--rpc') ?? process.env.SMITHII_RPC_URL ?? DEFAULT_RPC };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.authority) {
    console.error('usage: npm run presale:smithii-verify -- --authority <wallet that signed Create> [--launch <address>] [--rpc <url>]');
    process.exitCode = 2;
    return;
  }
  const cfg = loadSmithiiConfig();
  const record = loadTokenRecord();
  let result;
  try {
    result = await verifySmithii({
      cfg, record, authority: args.authority, launchHint: args.launch,
      rpcCall: makeRpc(args.rpc), nowSeconds: Math.floor(Date.now() / 1000),
    });
  } catch (e) {
    // PublicKey() throws on a malformed address: a usage error, not a verdict.
    console.error(`could not start: ${e?.name ?? 'error'} — check the addresses passed in`);
    process.exitCode = 2;
    return;
  }
  console.log(`RPC host: ${new URL(args.rpc).host}\n`);
  console.log(renderRows(result.rows, result.context, cfg));
  process.exitCode = exitCodeFor(result.rows);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
