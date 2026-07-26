#!/usr/bin/env node
// Report — and gate on — every on-chain program a presale transaction invokes.
//
// WHY THIS EXISTS
//
// `npm audit` and `cargo audit` answer "what packages do we import". Neither can
// answer "what BYTECODE does a transaction we sign actually execute", because a
// CPI target is not a dependency any package manager knows about. That gap was
// not theoretical here: `presale:deposit`, `presale:trigger` and `presale:claim`
// were all routing through MPL Token Extras (TokExjvjJ…), a third-party
// upgradeable program, because `createTokenIfMissing` from mpl-toolbox invokes
// it internally. It is deployed on every cluster, so nothing ever failed. It
// appeared in no config, no runbook and no dependency list. The only reason it
// was found is that a local validator carrying just the programs the sale needs
// refused the deposit outright.
//
// So: run the lifecycle, then run this. It reads the transaction signatures the
// run recorded, pulls each transaction's logs from the chain, and extracts every
// `Program <id> invoke [depth]` line — including CPIs at depth 2+, which is
// where the surprises live. Anything not in .program-inventory.json is a NEW
// program in the money path and fails the gate.
//
//   RPC_URL=http://127.0.0.1:8899 node scripts/program_inventory.mjs
//   RPC_URL=http://127.0.0.1:8899 node scripts/program_inventory.mjs --update
//
// This is a ratchet, not a scan: --update rewrites the baseline, and doing so
// is the moment to ask who holds the upgrade authority of whatever was added.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Connection, PublicKey } from '@solana/web3.js';

const TOKEN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ARTIFACTS = path.join(TOKEN_ROOT, '.artifacts');
const BASELINE = path.join(TOKEN_ROOT, '.program-inventory.json');
const UPDATE = process.argv.includes('--update');
const RPC = process.env.RPC_URL || 'http://127.0.0.1:8899';

/** Base58 signatures are 87-88 chars; be strict so log prose is not mistaken for one. */
const SIG_RE = /\b[1-9A-HJ-NP-Za-km-z]{86,90}\b/g;

/**
 * Collect (signature -> label) from everything the harness wrote.
 *
 * Deliberately scrapes the report's captured stdout as well as the structured
 * `txs` map: commands that print `tx: <sig>` without recording it in an artifact
 * would otherwise be invisible here, and a step nobody inventories is exactly
 * the kind of gap this tool exists to close.
 */
function collectSignatures() {
  const sigs = new Map();
  if (!fs.existsSync(ARTIFACTS)) return sigs;

  for (const f of fs.readdirSync(ARTIFACTS)) {
    if (!f.endsWith('.json')) continue;
    let doc;
    try {
      doc = JSON.parse(fs.readFileSync(path.join(ARTIFACTS, f), 'utf8'));
    } catch {
      continue;
    }
    for (const [k, v] of Object.entries(doc.txs || {})) {
      if (typeof v === 'string') sigs.set(v, k);
    }
    for (const [k, v] of Object.entries(doc.allocationBuckets || {})) {
      if (v && typeof v.tx === 'string') sigs.set(v.tx, `allocate:${k}`);
    }
    for (const step of doc.steps || []) {
      for (const m of String(step.out || '').matchAll(SIG_RE)) {
        if (!sigs.has(m[0])) sigs.set(m[0], step.name || f);
      }
    }
  }
  return sigs;
}

/**
 * A signature is 64 bytes, a pubkey 32 — and in base58 their lengths overlap
 * enough that the scrape can pick up an address. Anything PublicKey accepts is
 * 32 bytes and therefore not a signature.
 */
function looksLikeSignature(s) {
  try {
    new PublicKey(s);
    return false;
  } catch {
    return true;
  }
}

async function main() {
  const conn = new Connection(RPC, 'confirmed');
  const sigs = collectSignatures();
  if (!sigs.size) {
    console.error(
      `No transaction signatures found under ${ARTIFACTS}.\n` +
      'Run the lifecycle first (see token/e2e/README.md), then re-run this.'
    );
    process.exit(2);
  }

  const invoked = new Map(); // programId -> { steps:Set, maxDepth:number }
  let fetched = 0;
  let missing = 0;
  for (const [sig, label] of sigs) {
    if (!looksLikeSignature(sig)) continue;
    let tx;
    try {
      tx = await conn.getTransaction(sig, { commitment: 'confirmed', maxSupportedTransactionVersion: 0 });
    } catch {
      tx = null;
    }
    if (!tx) { missing += 1; continue; }
    fetched += 1;
    for (const line of tx.meta?.logMessages || []) {
      const m = line.match(/^Program ([1-9A-HJ-NP-Za-km-z]{32,44}) invoke \[(\d+)\]/);
      if (!m) continue;
      const [, pid, depth] = m;
      if (!invoked.has(pid)) invoked.set(pid, { steps: new Set(), maxDepth: 0 });
      const e = invoked.get(pid);
      e.steps.add(label);
      e.maxDepth = Math.max(e.maxDepth, Number(depth));
    }
  }

  const baseline = fs.existsSync(BASELINE) ? JSON.parse(fs.readFileSync(BASELINE, 'utf8')) : { programs: {} };
  const known = baseline.programs || {};

  console.log(`=== Program inventory (${RPC}) ===`);
  console.log(`transactions inspected: ${fetched}${missing ? ` (${missing} not in this ledger — skipped)` : ''}`);
  if (!fetched) {
    console.error(
      '\nNone of the recorded signatures are in this ledger. They are probably from a ' +
      'different cluster or a validator that has since been --reset. Re-run the ' +
      'lifecycle against this endpoint first.'
    );
    process.exit(2);
  }
  console.log('');

  const rows = [...invoked].sort((a, b) => a[0].localeCompare(b[0]));
  const unknown = [];
  for (const [pid, e] of rows) {
    const meta = known[pid];
    const tag = meta ? meta.name : 'UNKNOWN — NOT IN BASELINE';
    console.log(`${pid}`);
    console.log(`    ${tag}`);
    console.log(`    max CPI depth ${e.maxDepth} | steps: ${[...e.steps].sort().join(', ')}`);
    if (meta?.upgradeAuthority) console.log(`    upgrade authority: ${meta.upgradeAuthority}`);
    if (!meta) unknown.push(pid);
  }

  const vanished = Object.keys(known).filter((pid) => !invoked.has(pid));
  if (vanished.length) {
    console.log(`\nIn the baseline but NOT invoked by this run: ${vanished.join(', ')}`);
    console.log('(not a failure — a run that skips a step will not reach every program)');
  }

  if (UPDATE) {
    const programs = {};
    for (const [pid, e] of rows) {
      programs[pid] = known[pid] || {
        name: 'FILL IN: what is this program, and who holds its upgrade authority?',
        upgradeAuthority: 'UNKNOWN',
        firstSeen: new Date().toISOString().slice(0, 10),
      };
      programs[pid].steps = [...e.steps].sort();
      programs[pid].maxDepth = e.maxDepth;
    }
    fs.writeFileSync(BASELINE, `${JSON.stringify({ ...baseline, programs }, null, 2)}\n`);
    console.log(`\nBaseline written to ${BASELINE}.`);
    console.log('Fill in every "FILL IN" entry before committing — an unattributed program in');
    console.log('the money path is the finding, and recording it without naming it hides it.');
    return;
  }

  if (unknown.length) {
    console.error(`\nFAIL: ${unknown.length} program(s) in the money path are not in the baseline:`);
    for (const pid of unknown) console.error(`  ${pid}`);
    console.error(
      '\nThis is a NEW piece of third-party bytecode executing in a transaction this\n' +
      'project signs. Identify it and its upgrade authority, decide whether it belongs\n' +
      'there, then record it with --update.'
    );
    process.exit(1);
  }
  console.log('\nOK — every invoked program is accounted for.');
}

await main();
