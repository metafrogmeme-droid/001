#!/usr/bin/env node
// The allowlist path, executed. DRAFT / DEVNET-ONLY.
//
// WHY THIS EXISTS SEPARATELY FROM devnet_dryrun.mjs
//
// Every run of the main harness has printed "Whitelist: none (open sale)".
// `config.whitelist` is empty, no allowlist artifact has ever been written, and
// so the entire gated-round code path — Merkle tree construction, the on-chain
// Allowlist extension, proof presentation at deposit, and the program's
// verification of that proof — has NEVER been exercised against a chain.
//
// In this integration that has been a reliable predictor of defects. The quote
// split had no working execution path. `finalizeV2` needed accounts nothing
// mentioned. The deposit routed through a program nobody had chosen. Each was
// invisible until something ran it.
//
// WHAT THIS ASSERTS, and the order matters
//
//   1. a whitelisted wallet CAN deposit during the gated window
//   2. a NON-whitelisted wallet CANNOT                          <- the point
//   3. the same non-whitelisted wallet CAN once the round opens  <- expiry
//
// (2) is the whole reason an allowlist exists. A test that only proves the
// invited wallet gets in proves nothing about exclusion — it passes just as
// happily against an ungated sale.
//
// (3) is not padding either. An earlier version of the deposit command gated on
// "does the artifact exist on disk" rather than on the window, so once any run
// produced that file every uninvited wallet was refused FOREVER, including
// throughout the public round the allowlist was supposed to have expired before.
// The bug is fixed; this is what keeps it fixed.
//
// Local validator only — it needs two funded wallets and several presales, which
// is free locally and would cost real devnet SOL. Usage:
//
//   solana-test-validator --reset --quiet --ledger /tmp/ledger \
//     --url https://api.devnet.solana.com \
//     --clone-upgradeable-program GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B \
//     --clone-upgradeable-program metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s
//   RPC_URL=http://127.0.0.1:8899 node token/e2e/whitelist_e2e.mjs
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOKEN_ROOT = path.resolve(__dirname, '..');
const ARTIFACTS = path.join(TOKEN_ROOT, '.artifacts');
const RPC = process.env.RPC_URL || 'http://127.0.0.1:8899';

const RUN_ID = process.env.E2E_RUN_ID || `wl${process.pid}`;
const DERIVED_CONFIG = path.join(ARTIFACTS, `whitelist.${RUN_ID}.config.json`);
const RUN_ARTIFACT = `presale.whitelist.${RUN_ID}.json`;
const ALLOWLIST_ARTIFACT = path.join(ARTIFACTS, 'allowlist.devnet.json');

// Window sizing. The gated round has to be long enough for setup to finish
// inside it, or case (1) fails for a reason that has nothing to do with the
// allowlist — the same trap that made an earlier deposit land after depositEnd.
const OPEN_IN = 200;      // gated round opens ~now, runs this long
const PUBLIC_FOR = 150;   // public round length after the gate lifts
const CLAIM_GAP = 30;

const log = (m) => console.log(`[wl] ${m}`);
const iso = (s) => new Date(s * 1000).toISOString();
const nowSec = () => Math.floor(Date.now() / 1000);

function run(name, args, { keypair, expect = 'ok' } = {}) {
  const env = {
    ...process.env,
    RPC_URL: RPC,
    GENESIS_CONFIG: DERIVED_CONFIG,
    PRESALE_ARTIFACT: RUN_ARTIFACT,
    ...(keypair ? { KEYPAIR_PATH: keypair } : {}),
  };
  const res = spawnSync('node', args, { cwd: TOKEN_ROOT, env, encoding: 'utf8' });
  const out = (res.stdout || '') + (res.stderr || '');
  const ok = res.status === 0;
  if (expect === 'ok' && !ok) {
    console.log(out);
    throw new Error(`step "${name}" failed (exit ${res.status}) but was expected to succeed`);
  }
  if (expect === 'fail' && ok) {
    console.log(out);
    throw new Error(
      `step "${name}" SUCCEEDED but was expected to be refused. ` +
      'An allowlist that does not exclude is not an allowlist.'
    );
  }
  log(`${name}: ${ok ? 'ok' : 'refused'} (expected ${expect})`);
  return { ok, out };
}

/** A second funded wallet — the uninvited one. Free on a local validator. */
async function makeFundedWallet(name) {
  const { Keypair, Connection, LAMPORTS_PER_SOL } = await import('@solana/web3.js');
  const kp = Keypair.generate();
  const file = path.join(TOKEN_ROOT, '.keys', `e2e-${name}-${RUN_ID}.json`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });

  const conn = new Connection(RPC, 'confirmed');
  await conn.confirmTransaction(await conn.requestAirdrop(kp.publicKey, 5 * LAMPORTS_PER_SOL), 'confirmed');
  // Wait for FINALIZED: umi preflights there, and a confirmed-but-not-finalized
  // airdrop makes the next transaction fail as if the wallet were empty.
  for (let i = 0; i < 120; i += 1) {
    if (await conn.getBalance(kp.publicKey, 'finalized') > 0) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  log(`wallet ${name}: ${kp.publicKey.toBase58()}`);
  return { pubkey: kp.publicKey.toBase58(), file };
}

function writeConfig({ whitelisted, payerPubkey }) {
  const base = JSON.parse(
    fs.readFileSync(path.join(TOKEN_ROOT, 'presale', 'metaplex-genesis.config.json'), 'utf8')
  );
  const t = nowSec();
  const publicStart = t + OPEN_IN;
  const depositEnd = publicStart + PUBLIC_FOR;
  const tge = depositEnd + CLAIM_GAP;

  const cfg = {
    ...base,
    _comment: 'GENERATED whitelist e2e config — do not commit.',
    // The gated round is [whitelistStart, publicStart); the public round runs to
    // depositEnd. This is the split the whole test turns on.
    timeline: {
      whitelistStart: iso(t),
      publicStart: iso(publicStart),
      depositEnd: iso(depositEnd),
      tge: iso(tge),
      claimEnd: iso(tge + 365 * 86400),
    },
    whitelist: [whitelisted],
    sale: { ...base.sale, minContributionSol: 0.005 },
    fundingMode: { ...base.fundingMode, mode: 'mint' },
    allocations: base.allocations && {
      ...base.allocations,
      buckets: (base.allocations.buckets || []).map((b) => ({
        ...b,
        recipient: b.recipient || payerPubkey,
        unlockAt: iso(nowSec() + 5),
      })),
    },
  };
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  fs.writeFileSync(DERIVED_CONFIG, JSON.stringify(cfg, null, 2));
  return { publicStart, depositEnd };
}

/**
 * Wait until the CLOCK SYSVAR passes `sec`.
 *
 * Three different clocks are in play and picking the wrong one produces the same
 * failure each time, which is why this ended up with such a specific comment:
 *
 *   wall clock          - what Date.now() says. Ahead of everything.
 *   getBlockTime(slot)  - the block's timestamp. Observed 12s AHEAD of the
 *                         sysvar on this validator.
 *   Clock sysvar        - what `Clock::get()` returns INSIDE a program, and
 *                         therefore the only one that decides whether a deposit
 *                         is accepted.
 *
 * The first version of this function slept on wall clock plus a fixed cushion —
 * the same defect this file exists to catch, one level up. The second polled
 * getBlockTime, which passed the deadline while the sysvar was still 11 seconds
 * short, so the deposit was still refused. A cushion is a guess about the lag;
 * polling the wrong clock is a guess about which lag.
 */
async function sleepUntilChain(sec, why) {
  const { Connection, PublicKey } = await import('@solana/web3.js');
  const conn = new Connection(RPC, 'confirmed');
  const CLOCK = new PublicKey('SysvarC1ock11111111111111111111111111111111');
  log(`waiting for the CLOCK SYSVAR (finalized — the most conservative read) to pass ${iso(sec)} — ${why}`);
  for (let i = 0; i < 600; i += 1) {
    const acct = await conn.getAccountInfo(CLOCK, 'finalized');
    const t = acct ? Number(acct.data.readBigInt64LE(32)) : null;
    if (t !== null && t > sec) {
      log(`clock sysvar reached ${iso(t)} (${Math.round(Date.now() / 1000 - t)}s behind wall clock)`);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`the clock sysvar never passed ${iso(sec)}`);
}

async function main() {
  log(`rpc ${RPC}`);
  if (!/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|$)/.test(RPC)) {
    throw new Error(
      `Refusing to run against ${RPC}. This creates presales and burns deposits that a V2 ` +
      'presale cannot refund; it is for a local validator only.'
    );
  }

  // The invited wallet is the default payer (it also pays for setup). The
  // uninvited one is generated fresh, so it cannot have been whitelisted by
  // some leftover artifact.
  const { Keypair } = await import('@solana/web3.js');
  const payer = Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(path.join(TOKEN_ROOT, '.keys', 'mint-payer.json'), 'utf8')))
  );
  const invited = payer.publicKey.toBase58();
  const uninvited = await makeFundedWallet('uninvited');

  const { publicStart } = writeConfig({ whitelisted: invited, payerPubkey: invited });
  log(`invited   : ${invited}`);
  log(`uninvited : ${uninvited.pubkey}`);
  log(`gated round ends / public opens: ${iso(publicStart)}`);

  // A stale artifact from another run would silently change what is tested.
  fs.rmSync(ALLOWLIST_ARTIFACT, { force: true });

  run('whitelist', ['presale/genesis_presale.mjs', 'whitelist']);
  if (!fs.existsSync(ALLOWLIST_ARTIFACT)) throw new Error('presale:whitelist wrote no artifact');
  const wl = JSON.parse(fs.readFileSync(ALLOWLIST_ARTIFACT, 'utf8'));
  if (!wl.proofByAddress?.[invited]) throw new Error('no proof for the invited wallet');
  if (wl.proofByAddress?.[uninvited.pubkey]) throw new Error('the uninvited wallet has a proof');
  log(`merkle root ${wl.rootHex.slice(0, 20)}… height ${wl.treeHeight}, ${wl.count} member(s)`);

  run('create', ['presale/genesis_presale.mjs', 'create']);
  run('liquidity', ['presale/genesis_presale.mjs', 'liquidity']);
  run('allocate', ['presale/genesis_presale.mjs', 'allocate']);
  run('finalize', ['presale/genesis_presale.mjs', 'finalize']);

  // ── 1. the invited wallet gets in during the gated round ──────────────────
  const gated = run('deposit(invited, gated round)',
    ['presale/genesis_presale.mjs', 'deposit', '--amount', '0.01']);
  if (!/presenting whitelist proof/.test(gated.out)) {
    throw new Error(
      'The invited deposit succeeded WITHOUT presenting a proof. Either the allowlist was not ' +
      'attached to the bucket, or the window logic thinks the gated round is over — in both cases ' +
      'the sale is open to everyone and this test would not have noticed.'
    );
  }

  // ── 2. the uninvited wallet is refused — the actual assertion ─────────────
  const refused = run('deposit(uninvited, gated round)',
    ['presale/genesis_presale.mjs', 'deposit', '--amount', '0.01'],
    { keypair: uninvited.file, expect: 'fail' });
  // Refused for the RIGHT reason. "Insufficient funds" would also exit non-zero.
  if (!/not on the whitelist|allowlist|proof/i.test(refused.out)) {
    throw new Error(
      'The uninvited wallet was refused, but not for an allowlist reason. The refusal must come ' +
      `from the gate, not from something incidental. Output:\n${refused.out.slice(-1500)}`
    );
  }

  // ── 3. and gets in once the gate lifts ────────────────────────────────────
  await sleepUntilChain(publicStart, 'the public round to open');
  const open = run('deposit(uninvited, public round)',
    ['presale/genesis_presale.mjs', 'deposit', '--amount', '0.01'],
    { keypair: uninvited.file });
  if (!/no whitelist proof for this wallet/i.test(open.out)) {
    log('NOTE: the public deposit succeeded but did not report its allowlist state — check the wording.');
  }

  log('');
  log('ALLOWLIST PROVEN ON CHAIN:');
  log('  invited wallet admitted during the gated round (proof presented)');
  log('  uninvited wallet REFUSED during the gated round');
  log('  uninvited wallet admitted once the round opened');
  fs.rmSync(uninvited.file, { force: true });
}

await main();
