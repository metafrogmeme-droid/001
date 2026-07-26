#!/usr/bin/env node
// End-to-end $RCLAW presale dry-run on Solana DEVNET. DRAFT / DEVNET-ONLY.
//
// Orchestrates the existing token/ commands through the full lifecycle with a
// GENERATED near-now timeline (deposit/claim windows open in seconds, not
// months), so create → deposit → claim can actually be exercised on devnet —
// the paths CI can't reach. Reuses genesis_presale.mjs verbatim via a
// GENESIS_CONFIG override; no presale logic is duplicated here.
//
// Modes:
//   --dry   Offline: generate the near-now config and run `presale:plan`
//           against it, print the planned sequence. No RPC, no keypair, no sends.
//   (live)  Runs keygen → create → liquidity → deposit → (wait) → claim on devnet,
//           writing a step-by-step report to token/.artifacts/e2e-report.json.
//
// Never targets mainnet (genesis_lib refuses mainnet RPCs). If the devnet faucet
// is rate-limited it logs the address to fund and stops gracefully.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOKEN_ROOT = path.resolve(__dirname, '..');
const ARTIFACTS = path.join(TOKEN_ROOT, '.artifacts');
const DERIVED_CONFIG = path.join(ARTIFACTS, 'dryrun.genesis.config.json');

const DRY = process.argv.includes('--dry');

// Devnet-only stand-in recipient for allocation buckets whose recipient is blank
// in the committed config. A real launch must set every one of them explicitly —
// `presale:allocate` refuses a blank recipient rather than defaulting.
const DRY_RUN_RECIPIENT_OVERRIDE = process.env.E2E_ALLOCATION_RECIPIENT || null;

/**
 * Who the six allocation buckets pay out to during a dry run.
 *
 * This used to be the env var alone, defaulting to `null` — which meant the
 * derived config carried blank recipients, `presale:allocate` refused them (as
 * it must), and the run died at step five unless the operator happened to know
 * about an undocumented variable. Worse, the report the harness wrote asserted
 * in `harnessSimplifications` that "recipients defaulted to the devnet payer",
 * which was simply not true of the code. A harness that describes a behaviour
 * it does not have is the same class of problem as a test that passes without
 * asserting: it reads as covered.
 *
 * So the default is now real, and it is the signing payer — the only key a dry
 * run has. `presale:allocate`'s refusal is untouched and still fires for a real
 * launch, because a real launch uses the committed config, where the recipients
 * are blank on purpose.
 */
async function dryRunRecipient() {
  if (DRY_RUN_RECIPIENT_OVERRIDE) return DRY_RUN_RECIPIENT_OVERRIDE;
  const { Keypair } = await import('@solana/web3.js');
  const kp = Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(path.join(TOKEN_ROOT, '.keys', 'mint-payer.json'), 'utf8')))
  );
  return kp.publicKey.toBase58();
}

// Window sizing (seconds from now). Short so the run finishes in ~a few minutes.
const OPEN_IN = 20;      // deposit window opens
const DEPOSIT_FOR = 240; // deposit window length. Must comfortably exceed the
// time the SETUP takes, because the window is baked into the config before
// create runs and the clock starts then. Adding the six allocation buckets
// pushed setup past a 90s window, so the deposit arrived after depositEnd and
// the program correctly refused with "The Presale has ended".
const CLAIM_GAP = 45;    // gap between deposit end and TGE/claim open

function nowSec() {
  return Math.floor(Date.now() / 1000);
}
function iso(sec) {
  return new Date(sec * 1000).toISOString();
}

function log(msg) {
  console.log(`[e2e] ${msg}`);
}

// Build a near-now Genesis config from the committed one. Funding mode = mint so
// the run is self-contained (Genesis mints the supply; no separate token/ mint).
async function writeDerivedConfig() {
  const recipient = await dryRunRecipient();
  const base = JSON.parse(
    fs.readFileSync(path.join(TOKEN_ROOT, 'presale', 'metaplex-genesis.config.json'), 'utf8')
  );
  const t = nowSec();
  const publicStart = t + OPEN_IN;
  const depositEnd = publicStart + DEPOSIT_FOR;
  const tge = depositEnd + CLAIM_GAP;
  // The full 1,000,000,000 supply is now allocatable: presale (15%) + liquidity
  // (10%) + the six allocation buckets in config.allocations (75%). The harness
  // used to narrow totalSupply to what its two buckets held, because the other
  // buckets did not exist and finalizeV2 refuses on an unallocated supply. That
  // narrowing hid the real gap, so it is gone — this now exercises the config a
  // launch would actually use.
  //
  // Recipients must be set for `presale:allocate` to run. Devnet uses the payer
  // for all six; a real launch must not (see the refusal in cmdAllocate).
  // Shrink the per-wallet contribution floor for the dry run. This is the
  // single largest recurring cost of a live devnet run and it is unrecoverable:
  // a V2 presale has NO refund instruction (see #857), so every lamport a test
  // deposits is destroyed. The deposit path is exercised identically at 0.01 SOL
  // — same wSOL wrap, same instruction, same fee deduction, same claim. Caps and
  // totalSupply are untouched, so the derived price and the allocation
  // arithmetic remain the real ones.
  //
  // (An earlier edit removing the totalSupply narrowing deleted this block by
  // accident, which is how a run ended up depositing 0.5 SOL again.)
  const sale = { ...base.sale, minContributionSol: 0.005 };
  const cfg = {
    ...base,
    sale,
    _comment: 'GENERATED near-now dry-run config — do not commit. See token/e2e/.',
    allocations: base.allocations && {
      ...base.allocations,
      buckets: (base.allocations.buckets || []).map((b) => ({
        ...b,
        recipient: b.recipient || recipient,
        // Near-now cliffs so the dry run does not have to wait until 2027.
        unlockAt: iso(nowSec() + 5),
      })),
    },
    fundingMode: { ...base.fundingMode, mode: 'mint' },
    timeline: {
      _comment: 'near-now, generated by token/e2e/devnet_dryrun.mjs',
      whitelistStart: iso(t),
      publicStart: iso(publicStart),
      depositEnd: iso(depositEnd),
      tge: iso(tge),
      claimEnd: iso(tge + 365 * 86400),
    },
  };
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  fs.writeFileSync(DERIVED_CONFIG, JSON.stringify(cfg, null, 2));
  return { cfg, publicStart, depositEnd, tge };
}

// Run a token/ command with the derived config active. Returns {ok, code, out}.
// Per-run artifact name. Without this the harness reads and overwrites the same
// presale.devnet.json a real run uses, so a dry run could adopt a real presale's
// identity — or destroy it.
const RUN_ID = process.env.E2E_RUN_ID || String(process.pid);
const RUN_ARTIFACT = `presale.dryrun.${RUN_ID}.json`;

function run(name, args, { capture = true } = {}) {
  const env = {
    ...process.env,
    GENESIS_CONFIG: DERIVED_CONFIG,
    PRESALE_ARTIFACT: RUN_ARTIFACT,
  };
  log(`→ ${name}: ${args.join(' ')}`);
  const res = spawnSync('node', args, {
    cwd: TOKEN_ROOT,
    env,
    encoding: 'utf8',
    stdio: capture ? 'pipe' : 'inherit',
  });
  const out = capture ? (res.stdout || '') + (res.stderr || '') : '';
  if (capture && out.trim()) console.log(out.trim());
  return { name, ok: res.status === 0, code: res.status, out: out.slice(-4000) };
}

async function sleepUntil(sec) {
  const ms = Math.max(0, sec * 1000 - Date.now()) + 15000; // +15s cushion: the on-chain
  // clock (slot blocktime) lags wall-clock, so a small cushion lands before the condition.
  if (ms > 0) {
    log(`waiting ${Math.round(ms / 1000)}s until ${iso(Math.floor((Date.now() + ms) / 1000))}…`);
    await new Promise((r) => setTimeout(r, ms));
  }
}

function saveReport(steps) {
  const report = {
    kind: 'rclaw-presale-e2e-devnet',
    mode: DRY ? 'dry' : 'live',
    harnessSimplifications: [
      'allocation-bucket recipients defaulted to the devnet payer and their cliffs moved to ' +
      'near-now; a real launch sets each recipient explicitly (presale:allocate refuses a ' +
      'blank one) and uses the real unlock dates.',
    ],
    generatedConfig: DERIVED_CONFIG,
    steps,
    ok: steps.every((s) => s.ok),
    note: 'DRAFT / DEVNET dry-run — see token/e2e/README.md',
  };
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const out = path.join(ARTIFACTS, 'e2e-report.json');
  fs.writeFileSync(out, JSON.stringify(report, null, 2));
  return out;
}

async function main() {
  log(`mode: ${DRY ? 'DRY (offline)' : 'LIVE (devnet)'}`);
  const { cfg: cfgBase, publicStart, depositEnd, tge } = await writeDerivedConfig();
  log(`derived config: ${DERIVED_CONFIG}`);
  log(`windows → deposit ${iso(publicStart)}..${iso(depositEnd)}, claim/TGE ${iso(tge)}`);

  const steps = [];

  if (DRY) {
    // Offline: just prove the config drives the plan derivation, print sequence.
    const planned = run('plan', ['presale/genesis_presale.mjs', 'plan']);
    steps.push(planned);
    // Exit 0 alone proves nothing — a plan that printed an empty derivation
    // would also exit 0. Assert the numbers that matter actually appear.
    const required = [
      /Hard cap \(=QTC\)\s*:\s*\d/,
      /Fixed price\s*:\s*[\d.]+/,
      /Deposit window\s*:\s*\d+n?\s*→\s*\d+n?/,
      /Claim \/ TGE\s*:\s*\d+n?\s*→\s*\d+n?/,
      /Listing price\s*:/,
    ];
    const missing = required.filter((re) => !re.test(planned.out));
    if (missing.length) {
      log(`plan output is missing ${missing.length} expected derivation line(s) — treating as failure.`);
      planned.ok = false;
    }
    log('planned live sequence: keygen → create → liquidity → deposit → (wait) → claim');
    const out = saveReport(steps);
    log(`report: ${out}`);
    process.exit(steps.every((s) => s.ok) ? 0 : 1);
  }

  // Abort on the first failure: every step after this one signs and sends real
  // transactions, and continuing past a failure means doing that against a state
  // the harness has already proven it does not understand.
  const step = (name, args) => {
    const r = run(name, args);
    steps.push(r);
    if (!r.ok) {
      log(`step "${name}" failed (exit ${r.code}) — aborting before any further signed transactions.`);
      const out = saveReport(steps);
      log(`report: ${out}`);
      process.exit(1);
    }
    return r;
  };

  // Live devnet run.
  const keygen = run('keygen', ['scripts/keygen.mjs']);
  steps.push(keygen);

  // Gate on the ACTUAL balance, not on whether keygen's airdrop happened to
  // succeed. keygen tries to top up whenever the balance is under 1 SOL and
  // exits 0 even when the faucet refuses — so the previous gate, which
  // string-matched "airdrop skipped", stopped a run that had plenty of SOL and
  // then reported ok:true because keygen itself succeeded. That is a false
  // green: the lifecycle did not run, yet the report claimed success. A run
  // needs roughly 0.3 SOL for rent + fees (most rent is reclaimable), so gate
  // on a real floor and, crucially, exit NON-ZERO when we stop without running.
  const { Keypair, LAMPORTS_PER_SOL } = await import('@solana/web3.js');
  const { getConnection, loadEnv: loadTokenEnv } = await import('../scripts/lib.mjs');
  const kp = Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(path.join(TOKEN_ROOT, '.keys', 'mint-payer.json'), 'utf8')))
  );
  const conn = await getConnection(loadTokenEnv());
  const MIN_RUN_SOL = 0.3;

  // Read at FINALIZED, not the connection default, because that is the
  // commitment the presale commands are preflighted at — and the two disagree
  // for longer than you would expect. On a freshly started local validator the
  // finalized slot trails by ~32 slots, so `solana balance` and this gate both
  // showed 100 SOL while the very first `initializeV2` died with
  //
  //   Transaction simulation failed: Attempt to debit an account but found no
  //   record of a prior credit.
  //
  // — an error that reads like an empty wallet and is really a commitment
  // mismatch. Gating on the optimistic number is the same false-green shape as
  // gating on keygen's exit code: the check passes, the run does not.
  const finalizedBalance = async () =>
    (await conn.getBalance(kp.publicKey, 'finalized')) / LAMPORTS_PER_SOL;
  let balanceSol = await finalizedBalance();
  if (balanceSol < MIN_RUN_SOL) {
    const optimistic = (await conn.getBalance(kp.publicKey, 'confirmed')) / LAMPORTS_PER_SOL;
    if (optimistic >= MIN_RUN_SOL) {
      log(`balance ${optimistic.toFixed(4)} SOL is confirmed but not yet finalized — waiting…`);
      for (let i = 0; i < 60 && balanceSol < MIN_RUN_SOL; i += 1) {
        await new Promise((r) => setTimeout(r, 1000));
        balanceSol = await finalizedBalance();
      }
    }
  }
  log(`payer ${kp.publicKey.toBase58()} balance ${balanceSol.toFixed(4)} SOL (finalized)`);
  if (!keygen.ok || balanceSol < MIN_RUN_SOL) {
    log(
      `Insufficient balance (${balanceSol.toFixed(4)} < ${MIN_RUN_SOL} SOL) or keygen failed — ` +
      'fund the address via https://faucet.solana.com and re-run. NOT a successful run.'
    );
    saveReport(steps.concat({ name: 'balance-gate', ok: false, code: 1, out: `balance ${balanceSol} SOL` }));
    process.exit(1);
  }

  step('plan', ['presale/genesis_presale.mjs', 'plan']);
  step('create', ['presale/genesis_presale.mjs', 'create']);
  step('liquidity', ['presale/genesis_presale.mjs', 'liquidity']);
  // REQUIRED: finalizeV2 refuses while any supply is unallocated.
  step('allocate', ['presale/genesis_presale.mjs', 'allocate']);
  // REQUIRED before any deposit: depositPresaleV2 fails with "The Genesis
  // Account is not finalized" (0x2c) until this runs. Found by running it.
  step('finalize', ['presale/genesis_presale.mjs', 'finalize']);

  await sleepUntil(publicStart);
  // Deposit 2x the configured minimum. Depositing EXACTLY the minimum fails on
  // devnet with "The deposit amount is below the minimum allowed": Genesis takes
  // its protocol fee out of the deposited quote amount, so the net contribution
  // lands under the floor. An operator-facing quirk worth knowing — the
  // effective minimum a depositor must send is min + fee, not min.
  const depositSol = String(2 * (cfgBase.sale?.minContributionSol ?? 0.25));
  step('deposit', ['presale/genesis_presale.mjs', 'deposit', '--amount', depositSol]);

  // The deposit window is closed by now (depositEnd < tge), so the presale
  // bucket's end behaviors are executable. This is the step that ACTUALLY moves
  // the raise-to-liquidity share on chain — the whole F-11 remediation rests on
  // it, and until now it had never been executed against any cluster. It is
  // permissionless: triggerBehaviorsV2 takes a payer and no authority.
  await sleepUntil(depositEnd);
  step('trigger', ['presale/genesis_presale.mjs', 'trigger']);

  await sleepUntil(tge);
  step('claim', ['presale/genesis_presale.mjs', 'claim']);

  const out = saveReport(steps);
  const ok = steps.every((s) => s.ok);
  log(`${ok ? 'ALL STEPS OK ✓' : 'SOME STEPS FAILED ✗'} — report: ${out}`);
  process.exit(ok ? 0 : 1);
}

await main();
