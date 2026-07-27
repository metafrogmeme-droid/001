#!/usr/bin/env node
// Metaplex Genesis presale — real @metaplex-foundation/genesis integration.
// DRAFT / DEVNET-ONLY. Refuses mainnet (see genesis_lib.mjs / roadmap §10-11).
//
// Commands:
//   plan               Offline: derive + print every on-chain param from config.
//   create             initializeV2 (create genesis account) + addPresaleBucketV2.
//   deposit --amount N Contribute N SOL (depositPresaleV2).
//   claim              Claim vested tokens (claimPresaleV2).
//
// The fixed price is (presaleAllocation / hardCap): Genesis presale is
// "buy at a fixed price until the SOL cap is hit". Per-wallet min/max map to
// minimumDepositAmount / depositLimit; 33%-TGE-then-linear maps to a claim
// schedule. All values come from metaplex-genesis.config.json.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { generateSigner, publicKey, lamports, transactionBuilder } from '@metaplex-foundation/umi';
import { transferSol, syncNative } from '@metaplex-foundation/mpl-toolbox';
import {
  initializeV2,
  finalizeV2,
  WRAPPED_SOL_MINT,
  addUnlockedBucketV2,
  findUnlockedBucketV2Pda,
  addStreamflowBucketV2,
  findStreamflowBucketV2Pda,
  addPresaleBucketV2Base,
  addPresaleBucketV2Extensions,
  setPresaleBucketV2Behaviors,
  presaleV2Extension,
  addRaydiumCpmmBucketV2,
  depositPresaleV2,
  claimPresaleV2,
  withdrawPresaleV1,
  withdrawUnsoldPresaleV1,
  triggerBehaviorsV2,
  behavior,
  findGenesisAccountV2Pda,
  findPresaleBucketV2Pda,
  findRaydiumCpmmBucketV2Pda,
} from '@metaplex-foundation/genesis';

import {
  loadConfig,
  loadEnv,
  makeUmi,
  assertDevnet,
  sendChecked,
  awaitAccount,
  derivePresaleParams,
  fixedPriceSolPerToken,
  fundingModeValue,
  buildAllowlist,
  proofToPublicKeys,
  deriveLiquidityParams,
  deriveAllocationBuckets,
  deriveUnsoldRollover,
  REQUIRED_STREAM_FLAGS,
  findAssociatedTokenPda,
  findPresaleDepositV2Pda,
  createAtaIdempotent,
  hotKeyAuthorityRefusal,
  strandedUnsoldRefusal,
  TOKEN_ROOT,
} from './genesis_lib.mjs';

const GENESIS_PROGRAM_ID = 'GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B';
const BUCKET_INDEX = 0;
const LIQUIDITY_BUCKET_INDEX = 1;
const ALLOWLIST_ARTIFACT = 'allowlist.devnet.json';
// Overridable so the e2e dry-run harness cannot read or clobber a real presale
// artifact — threaded exactly the way GENESIS_CONFIG already is.
const PRESALE_ARTIFACT = process.env.PRESALE_ARTIFACT || 'presale.devnet.json';

function saveArtifact(name, data) {
  const dir = path.join(TOKEN_ROOT, '.artifacts');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, JSON.stringify(data, (_k, v) => (typeof v === 'bigint' ? v.toString() : v), 2));
  return file;
}

function readArtifact(name) {
  const file = path.join(TOKEN_ROOT, '.artifacts', name);
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
}

/**
 * Persist an ephemeral base-mint keypair before it is used to sign anything.
 *
 * In `mint` mode the base mint is a signer generated in memory. It signs
 * initializeV2 and is then never needed again — but its PUBLIC key is what every
 * PDA in this presale derives from, so losing the process before the artifact is
 * written loses the address of a genesis account that now exists on-chain. This
 * writes the secret to .keys/ (0600, the same handling as the payer key) so a
 * crashed run is recoverable rather than a permanent orphan.
 */
function persistBaseMintKeypair(signer) {
  const dir = path.join(TOKEN_ROOT, '.keys');
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try {
    fs.chmodSync(dir, 0o700);
  } catch {
    /* best effort on filesystems without POSIX modes */
  }
  const file = path.join(dir, `basemint-${signer.publicKey.toString()}.json`);
  fs.writeFileSync(file, JSON.stringify(Array.from(signer.secretKey)), { mode: 0o600 });
  // writeFileSync's mode applies only on CREATE — an existing file keeps its
  // old mode. Repair rather than assume, the same way keygen.mjs does.
  if (process.platform !== 'win32') fs.chmodSync(file, 0o600);
  try {
    fs.chmodSync(file, 0o600);
  } catch {
    /* best effort */
  }
  return file;
}

function arg(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

/**
 * Presence of a valueless flag.
 *
 * Not the same thing as `arg()`, and the difference is a real bug rather than a
 * style point: `arg()` returns argv[i+1], so for a boolean flag with nothing
 * after it — the normal way one is passed — it returns `undefined`, which is
 * indistinguishable from the flag being absent. `--accept-below-presale` was
 * checked with `arg(...) !== undefined` and could therefore never fire; the
 * override was dead on arrival and only running it showed that.
 *
 * Named rather than inlined so the distinction has somewhere to live.
 */
function hasFlag(flag) {
  return process.argv.includes(flag);
}

/**
 * Load the presale artifact for a command that acts on a LIVE sale.
 *
 * `presale:create` writes this file incrementally, so its mere existence no
 * longer means the sale was configured — a run that created the genesis account
 * and then failed leaves a populated record with `status: "pending"`. Depositing
 * into, triggering or claiming from that presale would be acting on a bucket
 * that does not exist. Recovery needs those addresses; transacting must not use
 * them.
 */
function requirePresale() {
  const a = readArtifact(PRESALE_ARTIFACT);
  if (!a) throw new Error(`No ${PRESALE_ARTIFACT} — run \`npm run presale:create\` first.`);
  if (a.status === undefined) {
    // Written by a build that predates incremental checkpointing. It was only
    // ever written after both sends confirmed, so it is complete by construction.
    console.warn(`NOTE: ${PRESALE_ARTIFACT} predates status tracking; treating it as complete.`);
    return a;
  }
  if (a.status !== 'complete') {
    throw new Error(
      `${PRESALE_ARTIFACT} records an INCOMPLETE presale (status: ${a.status}; steps: ` +
      `${(a.completedSteps || []).join(', ') || 'none'}).\n` +
      `  genesis account: ${a.genesisAccount}\n  base mint      : ${a.baseMint}\n` +
      'The presale bucket was never configured, so there is nothing to act on. Those ' +
      'addresses are kept for recovery — do not delete the artifact.'
    );
  }
  return a;
}

// ── plan: fully offline preview (pure SDK derivation, no RPC / keypair) ──────
function cmdPlan() {
  const cfg = loadConfig();
  const p = derivePresaleParams(cfg);
  const price = fixedPriceSolPerToken(cfg);
  console.log('=== Metaplex Genesis presale plan (DEVNET / DRAFT) ===');
  console.log('Venue           :', cfg.venue, '| program', GENESIS_PROGRAM_ID);
  console.log('Token           :', `${cfg.token.symbol} (${cfg.token.decimals} dp)`);
  console.log('Funding mode    :', cfg.fundingMode.mode, '=>', fundingModeValue(cfg));
  console.log('Presale alloc   :', cfg.sale.presaleAllocation, `(${p.baseTokenAllocation} base units)`);
  console.log('Hard cap (=QTC) :', cfg.sale.hardCapSol, 'SOL', `(${p.allocationQuoteTokenCap} lamports)`);
  console.log('Soft cap        :', cfg.sale.softCapSol, 'SOL', `(${p.softCapLamports} lamports)`);
  console.log('Fixed price     :', price.toFixed(12), 'SOL/token', `(~${Math.round(1 / price).toLocaleString()} tokens/SOL)`);
  console.log('Per-wallet min  :', cfg.sale.minContributionSol, 'SOL', `(${p.perWalletMinLamports} lamports)`);
  console.log('Per-wallet max  :', cfg.sale.maxContributionSol, 'SOL', `(${p.perWalletMaxLamports} lamports)`);
  console.log('Deposit window  :', p._times.depositStart, '→', p._times.depositEnd, '(unix s)');
  console.log('Claim / TGE     :', p._times.tge, '→', p._times.claimEnd, '(unix s)');
  console.log('Vesting         :', `${cfg.vesting.cliffAmountBps} bps at TGE, linear to`, p._times.vestingEnd);
  // Whitelist (Merkle allowlist) summary — built offline from config.whitelist.
  const wlAddrs = Array.isArray(cfg.whitelist) ? cfg.whitelist : [];
  if (wlAddrs.length) {
    const wl = buildAllowlist(cfg, wlAddrs);
    console.log('Whitelist       :', `${wlAddrs.length} members, tree height ${wl.treeHeight}, root ${wl.rootHex.slice(0, 16)}… (ends at publicStart)`);
  } else {
    console.log('Whitelist       : none (open sale) — add addresses to config.whitelist + run presale:whitelist');
  }
  // Liquidity bucket summary.
  const lp = deriveLiquidityParams(cfg);
  console.log('Liquidity       :', `${cfg.liquidity.tokenAllocation} ${cfg.token.symbol} to ${cfg.liquidity.dex}, LP locked forever (never-claim)`);
  const pr = lp._pricing;
  console.log('Listing price   :',
    `presale ${pr.presalePrice.toExponential(4)} | pool at hard cap ${pr.bestCasePoolPrice.toExponential(4)} ` +
    `| pool at soft cap ${pr.worstCasePoolPrice.toExponential(4)} SOL/token`);
  console.log('                 ',
    `the LP token side is FIXED at creation and CANNOT be changed afterwards (proven on devnet: ` +
    `updateRaydiumCpmmBucketV2 is rejected once the genesis account is finalized, and deposits ` +
    `require finalize). Size it for the SOFT cap or a weak raise opens the pool below the ` +
    `presale price, permanently.`);
  console.log('                 ',
    `to be safe at EVERY raise, set liquidity.tokenAllocation <= ` +
    `${lp._pricing.softCapParityTokens.toLocaleString()} (currently ${Number(cfg.liquidity.tokenAllocation).toLocaleString()}).`);
  console.log('                 ',
    `quote split: ${lp.raisedSolToLiquidityBps / 100}% of the raise is encoded on chain as a ` +
    `SendQuoteTokenPercentage end behavior on the presale bucket (${lp.raisedSolToLiquidityBps} bps), ` +
    'executed by the permissionless presale:trigger after the deposit window closes.');
  console.log('                 ',
    'the encoded split is publicly VERIFIABLE, not immutable: setPresaleBucketV2Behaviors lets the ' +
    'genesis authority replace it until it is triggered, so presale:liquidity and presale:trigger ' +
    're-read the live bucket and refuse on a mismatch. A multisig authority is the real mitigation.');

  // Supply allocation. This runs in the PREVIEW, not only in tests, because it
  // is the check that decides whether the launch can ever be opened —
  // finalizeV2 refuses on an unallocated supply, and it refuses at the END of
  // the sequence, after the irreversible LP lock exists. Catching it here costs
  // nothing; catching it on chain costs the whole launch.
  const alloc = deriveAllocationBuckets(cfg);
  const scale = 10n ** BigInt(cfg.token.decimals);
  console.log('Allocation      :', `${alloc.buckets.length} bucket(s) + presale + liquidity =`,
    `${(alloc.allocated / scale).toLocaleString()} / ${(alloc.totalSupply / scale).toLocaleString()}`,
    cfg.token.symbol, '(fully allocated ✓ — finalizeV2 requires this)');
  for (const b of alloc.buckets) {
    // Say which SHAPE each bucket is. Printing "cliff <date>" for a bucket that
    // is actually a 24-month stream describes the wrong thing entirely, and this
    // line is the summary an operator reads before committing.
    const day = (t) => new Date(Number(t) * 1000).toISOString().slice(0, 10);
    const shape = b.kind === 'linear'
      ? `stream ${b._schedule.cliffMonths}mo cliff + ${b._schedule.linearMonths}mo -> ${day(b._schedule.endTime)}`
      : `cliff ${day(b._unlockAt)} (FULL amount)`;
    console.log('                 ',
      `${b.name.padEnd(13)} ${(b._tokens).toLocaleString().padStart(13)}  ${shape.padEnd(44)}` +
      `${b.recipient ? '' : ' RECIPIENT UNSET — presale:allocate will refuse'}`);
  }

  console.log('\nConditions/schedules via createTimeAbsoluteCondition / createClaimSchedule / createNeverClaimSchedule.');
  console.log('Flow: presale:whitelist → presale:create → presale:liquidity → presale:finalize → presale:deposit → (window closes) → presale:trigger → presale:claim.');
  console.log('       presale:finalize is REQUIRED before any deposit and PERMANENTLY locks bucket configuration.');

  printDisclosures(cfg);
}

/**
 * Print the things a buyer has to be told, every time the plan is run.
 *
 * These are not warnings about the config — they are properties of the sale
 * that hold however it is configured, each one established by executing the
 * thing rather than reading about it. They live in the config so there is one
 * copy, and they print here so the sale cannot be planned without them being
 * seen.
 *
 * It is deliberately noisy and deliberately not suppressible. The failure mode
 * this guards against is not someone disagreeing with a disclosure; it is
 * everyone forgetting it exists by the time the terms get written.
 */
function printDisclosures(cfg) {
  const d = cfg.disclosures;
  if (!d) {
    console.log('\nNOTE: config has no `disclosures` block. It should — see metaplex-genesis.config.json.');
    return;
  }
  const items = Object.entries(d).filter(([k]) => !k.startsWith('_'));
  if (!items.length) return;

  console.log(`\n${'='.repeat(78)}`);
  console.log('REQUIRED DISCLOSURES — these must appear in the published sale terms');
  console.log('='.repeat(78));
  for (const [key, text] of items) {
    console.log(`\n[${key}]`);
    // Wrap to something readable in a terminal without mangling the source text.
    for (const line of String(text).split(/(?<=\.) (?=[A-Z])/)) {
      console.log(`  ${line.trim()}`);
    }
  }
  console.log(`\n${'='.repeat(78)}`);
}

// ── create: initializeV2 + addPresaleBucketV2 ───────────────────────────────
async function cmdCreate() {
  const cfg = loadConfig();
  const env = loadEnv();
  const umi = makeUmi(env);
  const p = derivePresaleParams(cfg);

  // Base mint: mint-mode creates a fresh mint; transfer-mode reuses the one
  // produced by the token/ tooling.
  const transferMode = cfg.fundingMode.mode === 'transfer';
  if (!['mint', 'transfer'].includes(cfg.fundingMode.mode)) {
    throw new Error(
      `Unknown fundingMode.mode ${JSON.stringify(cfg.fundingMode.mode)}. ` +
      'Expected "mint" or "transfer".'
    );
  }
  if (transferMode) {
    // In transfer mode token.mint is load-bearing; the committed default is a
    // placeholder string that would otherwise reach publicKey() as a real value.
    const m = String(cfg.token.mint || '');
    if (!m || m.includes('<') || m.includes('FILL')) {
      throw new Error(
        `fundingMode.mode is "transfer" but token.mint is unset (${JSON.stringify(m)}). ` +
        'Copy the mint from token/.artifacts/token.devnet.json.'
      );
    }
  } else if (cfg.token.mint && !String(cfg.token.mint).includes('<')) {
    // Loud rather than silent: a configured mint that mint-mode ignores means
    // the operator believes they are selling a token they are not selling.
    throw new Error(
      `fundingMode.mode is "mint" but token.mint is set to ${cfg.token.mint}. ` +
      'Mint mode creates a BRAND NEW mint and ignores that value — the presale would ' +
      'sell a different token than the one you configured. Set mode to "transfer" to ' +
      'sell the existing mint, or clear token.mint.'
    );
  }
  // Refuse to clobber a presale that already exists. `presale:create` mints a
  // BRAND NEW base mint in mint mode, so a second run does not "retry" the first
  // one — it creates an unrelated presale and overwrites the only on-disk record
  // of the original, whose genesis account, bucket and escrowed tokens then
  // become unreachable by every other command here.
  const existing = readArtifact(PRESALE_ARTIFACT);
  if (existing && existing.status !== 'pending' && !hasFlag('--force')) {
    throw new Error(
      `${PRESALE_ARTIFACT} already records a presale (genesis ${existing.genesisAccount}). ` +
      'Creating another would overwrite the only record of it and strand its tokens. ' +
      'Move the artifact aside deliberately, or pass --force if you are certain.'
    );
  }
  if (existing && existing.status === 'pending') {
    console.warn(
      `NOTE: ${PRESALE_ARTIFACT} holds an INCOMPLETE run (steps: ` +
      `${(existing.completedSteps || []).join(', ') || 'none'}, base mint ${existing.baseMint}).\n` +
      '      See the recovery note printed by that run. Continuing creates a NEW presale;\n' +
      '      the incomplete one is not resumed and its genesis account stays on-chain.'
    );
  }

  const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi);
  const baseMintPk = transferMode ? baseMint : baseMint.publicKey;

  console.log('=== Genesis presale create (DEVNET / DRAFT) ===');
  console.log('Authority/payer :', umi.identity.publicKey);
  console.log('Base mint       :', baseMintPk, transferMode ? '(existing)' : '(new)');
  if (!transferMode) {
    console.log(
      '  WARNING: mint mode creates a NEW mint whose authorities are set by the Genesis\n' +
      '           program, not by token/scripts/create_token.mjs. verify_token.mjs does\n' +
      '           NOT check this mint. For a real sale use fundingMode.mode="transfer"\n' +
      '           with the audited, authority-revoked mint.'
    );
  }

  await assertDevnet(umi);

  // Decide the allowlist BEFORE anything is sent. Whether the OG round is gated
  // must not depend on whether a gitignored artifact happens to exist on this
  // machine: deposits open 48h before the public round, so a missing artifact
  // silently turns a whitelisted sale into an open one for two days.
  const wlAddrs = Array.isArray(cfg.whitelist) ? cfg.whitelist : [];
  const wl = readArtifact(ALLOWLIST_ARTIFACT);
  if (wlAddrs.length && !wl) {
    throw new Error(
      `config.whitelist has ${wlAddrs.length} members and deposits open at ` +
      `${cfg.timeline.whitelistStart} (before publicStart), but ` +
      `token/.artifacts/${ALLOWLIST_ARTIFACT} is missing on this machine. ` +
      'Sending now would open an UNGATED presale early. Run `npm run presale:whitelist` first.'
    );
  }
  if (wl && !wlAddrs.length) {
    throw new Error(
      'An allowlist artifact exists but config.whitelist is empty — refusing to ' +
      'publish a Merkle root with no config backing it.'
    );
  }
  // Re-derive from config rather than trusting the artifact: the artifact is a
  // cache, and a stale one would gate the sale on the wrong member set.
  let allowlistArgs = null;
  if (wl) {
    const fresh = buildAllowlist(cfg, wlAddrs);
    if (fresh.rootHex !== wl.rootHex) {
      throw new Error(
        `Stale allowlist artifact: config derives root ${fresh.rootHex.slice(0, 16)}… ` +
        `but the artifact holds ${wl.rootHex.slice(0, 16)}…. Re-run \`npm run presale:whitelist\`.`
      );
    }
    allowlistArgs = fresh.initArgs;
  }

  console.log('\n[1/2] initializeV2 — creating the genesis account…');
  // The authority controls the sale, the proceeds, and the unsold-token
  // withdrawal. A single hot key holding all of that is what the roadmap's
  // multisig requirement exists to prevent, and it cannot be changed after
  // initializeV2 — so it has to be right here, not later.
  //
  // This USED TO BE A console.warn, and the comment directly above it said the
  // authority "has to be right here, not later" — then the code printed a
  // warning and carried on. A warning scrolls past in a log; initializeV2 does
  // not come round again.
  //
  // The inconsistency is what makes it a defect rather than a preference. In
  // this same file, `presale:allocate` THROWS when a bucket recipient is unset
  // for exactly this reason ("it would default to the signing key"), and
  // `presale:trigger` THROWS rather than open a pool below the presale price.
  // The refusing guards covered the recoverable decision and a bad-but-known
  // price; the merely-warning one covered total, permanent control of the
  // raise. That is backwards.
  //
  // So it refuses, in the shape `presale:trigger` already established: an
  // explicit, logged override exists for a deliberate rehearsal, and the
  // default is to stop.
  const authority = cfg.treasury?.authority
    ? publicKey(cfg.treasury.authority)
    : umi.identity;
  const hotKeyRefusal = hotKeyAuthorityRefusal(
    cfg, umi.identity.publicKey, { override: hasFlag('--accept-hot-key-authority') });
  if (hotKeyRefusal) throw new Error(hotKeyRefusal);
  if (!cfg.treasury?.authority) {
    console.warn(
      'PROCEEDING WITH A SINGLE HOT-KEY AUTHORITY ON AN EXPLICIT OVERRIDE.\n' +
      `  authority: ${umi.identity.publicKey}\n` +
      '  This key controls the sale, the proceeds and the unsold-token withdrawal, and ' +
      'it is immutable once the genesis account exists. Valid for a throwaway devnet ' +
      'rehearsal; never for a sale that takes real money.'
    );
  }

  // Every address this presale will ever have is derived from the base mint, so
  // all of it is known BEFORE the first signature. Deriving here rather than
  // after tx1 is what makes the checkpoint below possible.
  const genesisAccount = findGenesisAccountV2Pda(umi, { baseMint: baseMintPk, genesisIndex: 0 });
  const bucket = findPresaleBucketV2Pda(umi, { genesisAccount: genesisAccount[0], bucketIndex: BUCKET_INDEX });
  const liquidityBucket = findRaydiumCpmmBucketV2Pda(umi, {
    genesisAccount: genesisAccount[0],
    bucketIndex: LIQUIDITY_BUCKET_INDEX,
  });

  // This is TWO transactions and nothing makes them atomic. The failure that
  // matters is the second one: initializeV2 lands, addPresaleBucketV2 fails, and
  // the process exits. On-chain there is now a genesis account. In memory there
  // WAS the only copy of the base-mint identity every one of its PDAs derives
  // from — and in mint mode that identity is an ephemeral signer generated
  // seconds earlier. Nothing else on disk records it (.artifacts/ is gitignored
  // and used to be written only after both sends), so the presale became
  // unreachable by withdraw-unsold, withdraw and claim alike.
  //
  // So the identity is persisted before anything is signed, and the artifact is
  // written incrementally with an explicit status. A crashed run leaves a record
  // naming exactly which step it reached.
  let keyFile = null;
  if (!transferMode) {
    keyFile = persistBaseMintKeypair(baseMint);
    console.log('  base-mint keypair saved (0600):', path.relative(TOKEN_ROOT, keyFile));
  }
  const progress = {
    status: 'pending',
    note: 'INCOMPLETE RUN — see status/completedSteps. DRAFT / DEVNET artifact.',
    cluster: env.CLUSTER || 'devnet',
    program: GENESIS_PROGRAM_ID,
    baseMint: baseMintPk.toString(),
    baseMintKeyfile: keyFile ? path.relative(TOKEN_ROOT, keyFile) : null,
    genesisAccount: genesisAccount[0].toString(),
    bucket: bucket[0].toString(),
    liquidityBucket: liquidityBucket[0].toString(),
    completedSteps: [],
    txs: {},
  };
  const checkpoint = (step, sig) => {
    progress.completedSteps.push(step);
    if (sig) progress.txs[step] = sig;
    saveArtifact(PRESALE_ARTIFACT, progress);
  };
  saveArtifact(PRESALE_ARTIFACT, progress);

  let sigInit;
  try {
    sigInit = await sendChecked(
      initializeV2(umi, {
        baseMint,
        authority,
        fundingMode: fundingModeValue(cfg),
        decimals: cfg.token.decimals,
        totalSupplyBaseToken: BigInt(cfg.token.totalSupply) * 10n ** BigInt(cfg.token.decimals),
        name: cfg.token.name,
        symbol: cfg.token.symbol,
        uri: cfg.token.metadataUri,
      }),
      umi,
      'initializeV2'
    );
  } catch (err) {
    console.error(
      `\ninitializeV2 FAILED. No genesis account was created for base mint ${baseMintPk}.` +
      (keyFile ? `\nThe unused base-mint keypair is at ${path.relative(TOKEN_ROOT, keyFile)} and can be deleted.` : '')
    );
    throw err;
  }
  console.log('  tx:', sigInit);
  checkpoint('initializeV2', sigInit);
  // Do not proceed until the account is actually readable — see awaitAccount.
  await awaitAccount(umi, genesisAccount[0], 'genesis account');

  // Encode the raise->liquidity split ON CHAIN rather than promising it.
  //
  // Previously `raisedSolToLiquidityBps` was config text that no instruction
  // read: the token side of the pool was locked irrevocably while the SOL side
  // stayed entirely at the operator's discretion. As an `endBehaviors` entry the
  // program itself moves the quote tokens, and `triggerBehaviorsV2` is
  // permissionless, so any participant can execute it.
  //
  // CORRECTION (2026-07-26). An earlier revision of this comment, and PR #836,
  // said the percentage is "fixed at bucket creation" and that the operator
  // "can neither change the share nor decline to send it". That is FALSE.
  // `setPresaleBucketV2Behaviors` takes the genesis authority and replaces
  // `endBehaviors` wholesale, so the share can be lowered, repointed or deleted
  // right up until someone triggers it. The permissionless trigger only means a
  // participant can win that race.
  //
  // What this actually buys is PUBLIC VERIFIABILITY, not immutability: the value
  // lives in an account anyone can read, and assertEncodedSplitOnChain() checks
  // it before the irreversible steps. Making it truly immutable needs the
  // genesis authority to be a multisig — which is the treasury.authority
  // requirement already flagged below.
  //
  // destinationBucket is a PDA derived from (genesisAccount, bucketIndex), so it
  // is addressable before the liquidity bucket exists (derived above, alongside
  // the other PDAs). That creates a real ordering requirement, enforced in
  // cmdLiquidity: the liquidity bucket must be created before the deposit window
  // closes, or the behavior points at nothing.
  const bps = Number(cfg.liquidity.raisedSolToLiquidityBps ?? 0);
  const endBehaviors = bps > 0
    ? [
        behavior('SendQuoteTokenPercentage', {
          processed: false,
          percentageBps: bps,
          padding: [0, 0, 0, 0],
          destinationBucket: liquidityBucket[0],
        }),
      ]
    : [];

  // Where presale tokens NOBODY BOUGHT go. Attached here because end behaviors
  // are set at creation and cannot be added after finalize.
  //
  // Without this the unsold remainder is stranded permanently: a partial raise
  // leaves `presaleAllocation - sold` in the presale bucket, and the only
  // instructions that could move it out — withdrawUnsoldPresaleV1 and
  // withdrawPresaleV1 — are V1-only and reject a V2 genesis account (0x2f).
  // At a soft-cap raise that is ~120,000,000 tokens frozen in an account no key
  // can reach.
  const rollover = deriveUnsoldRollover(cfg);
  if (rollover) {
    const destination = findUnlockedBucketV2Pda(umi, {
      genesisAccount: genesisAccount[0],
      bucketIndex: rollover.bucketIndex,
    });
    endBehaviors.push(
      behavior('BaseTokenRollover', {
        processed: false,
        percentageBps: rollover.percentageBps,
        padding: [0, 0, 0, 0],
        destinationBucket: destination[0],
      })
    );
    console.log(
      `    unsold rollover: ${rollover.percentageBps / 100}% of unsold presale tokens -> ` +
      `"${rollover.name}" bucket [${rollover.bucketIndex}] ${destination[0]}`
    );
  } else {
    const strandedRefusal = strandedUnsoldRefusal(
      cfg, { override: hasFlag('--accept-stranded-unsold') });
    if (strandedRefusal) throw new Error(strandedRefusal);
    console.warn(
      'PROCEEDING WITH NO UNSOLD ROLLOVER ON AN EXPLICIT OVERRIDE.\n' +
      '  Any presale token nobody buys is stranded permanently and cannot be recovered ' +
      'by any instruction. This is irreversible from the moment the bucket is created.'
    );
  }

  // A single addPresaleBucketV2 does not fit in a Solana transaction.
  //
  // Observed on devnet, not reasoned about: the combined instruction serialised
  // to 1652 bytes against the 1232-byte limit and the RPC rejected it at
  // simulation. Every parameter it carries is load-bearing — four time
  // conditions, the claim schedule, per-wallet floor and ceiling, the quote
  // split, optionally a Merkle allowlist — so nothing could simply be dropped.
  //
  // The SDK's answer is the split form, which is why those instructions exist:
  //   addPresaleBucketV2Base       core allocation, cap and the four conditions
  //   addPresaleBucketV2Extensions min/max deposit, claim schedule, allowlist
  //   setPresaleBucketV2Behaviors  the quote split
  //
  // That makes create a FOUR-transaction sequence with no atomicity, so each
  // step checkpoints and the artifact only flips to complete at the end. A
  // bucket that exists with no extensions is a real, reachable state: it has no
  // per-wallet cap and no vesting, so it must never be treated as a live sale.
  const step = async (label, build, describe) => {
    try {
      // build() is invoked INSIDE the try on purpose. Passing an already-built
      // builder evaluates it as an argument, so a serialization error (a wrong
      // fixed-array length, say) is thrown before this function is even
      // entered — skipping the recovery message at the exact moment a bucket
      // exists half-configured on chain. Observed on devnet, then fixed.
      const sig = await sendChecked(build(), umi, label);
      console.log('  tx:', sig);
      checkpoint(label, sig);
      return sig;
    } catch (err) {
      console.error(
        `\n=== INCOMPLETE PRESALE — RECOVERY INFORMATION ===\n` +
        `${label} FAILED. ${describe}\n` +
        `  genesis account : ${genesisAccount[0]}\n` +
        `  presale bucket  : ${bucket[0]}\n` +
        `  base mint       : ${baseMintPk}\n` +
        (keyFile ? `  base-mint key   : ${path.relative(TOKEN_ROOT, keyFile)} (0600)\n` : '') +
        `  steps completed : ${progress.completedSteps.join(', ') || 'none'}\n` +
        `  artifact        : .artifacts/${PRESALE_ARTIFACT} (status: pending)\n` +
        'These addresses are NOT recoverable from anything else — .artifacts/ is\n' +
        'gitignored and the base mint was generated in this process. Do not delete\n' +
        'them. Re-running `presale:create` creates a DIFFERENT presale; it does not\n' +
        'retry this one.'
      );
      throw err;
    }
  };

  console.log('[2/4] addPresaleBucketV2Base — allocation, cap and the time conditions…');
  await step(
    'addPresaleBucketV2Base',
    () => addPresaleBucketV2Base(umi, {
      genesisAccount: genesisAccount[0],
      baseMint: baseMintPk,
      bucketIndex: BUCKET_INDEX,
      baseTokenAllocation: p.baseTokenAllocation,
      allocationQuoteTokenCap: p.allocationQuoteTokenCap,
      depositStartCondition: p.depositStartCondition,
      depositEndCondition: p.depositEndCondition,
      claimStartCondition: p.claimStartCondition,
      claimEndCondition: p.claimEndCondition,
    }),
    'A genesis account exists with NO presale bucket.'
  );
  await awaitAccount(umi, bucket[0], 'presale bucket');

  // Order matters within the extensions array only in that all of them must
  // land: without ClaimSchedule there is no vesting, and without the deposit
  // bounds the per-wallet cap the sale terms promise does not exist.
  const extensions = [
    presaleV2Extension('MinimumDepositAmount', {
      minimumDepositAmount: { amount: p.perWalletMinLamports },
    }),
    presaleV2Extension('DepositLimit', {
      depositLimit: { limit: p.perWalletMaxLamports },
    }),
    presaleV2Extension('ClaimSchedule', { claimSchedule: p.claimSchedule }),
  ];
  if (allowlistArgs) {
    extensions.push(presaleV2Extension('Allowlist', { allowlist: allowlistArgs }));
    console.log('    whitelist: applying Merkle root', wl.rootHex.slice(0, 16) + '…');
  } else {
    console.log('    whitelist: NONE — this round is open to any wallet.');
  }

  console.log(`[3/4] addPresaleBucketV2Extensions — ${extensions.length} extension(s)…`);
  await step(
    'addPresaleBucketV2Extensions',
    () => addPresaleBucketV2Extensions(umi, {
      genesisAccount: genesisAccount[0],
      bucket: bucket[0],
      padding: [0, 0, 0], // size 3 — fixed-size arrays throw on any other length
      extensions,
    }),
    'The bucket exists but has NO per-wallet limits and NO claim schedule — it must not be sold into.'
  );

  if (bps > 0) {
    console.log(`[4/4] setPresaleBucketV2Behaviors — quote split ${bps} bps -> ${liquidityBucket[0]}…`);
    await step(
      'setPresaleBucketV2Behaviors',
      () => setPresaleBucketV2Behaviors(umi, {
        genesisAccount: genesisAccount[0],
        bucket: bucket[0],
        padding: [0, 0, 0], // size 3
        endBehaviors,
      }),
      'The bucket exists but routes NO share of the raise to liquidity.'
    );
  } else {
    console.log('[4/4] No quote split configured (raisedSolToLiquidityBps is 0).');
  }

  // Do not mark the sale complete until the split is READABLE, not merely sent.
  // Downstream commands verify it against the live account, so a "complete"
  // artifact whose behavior is not yet visible just moves the failure one step
  // later, onto the command that creates the irreversible LP lock.
  if (bps > 0) {
    await assertEncodedSplitOnChain(umi, { ...progress, liquidityBucket: liquidityBucket[0].toString() }, cfg);
  }

  // Only now is the sale real. Every downstream command treats a "complete"
  // artifact as proof of that, and sendChecked throws on a non-null
  // result.value.err, so a transaction that landed and failed never reaches here.
  const artifact = {
    ...progress,
    status: 'complete',
    note: 'DRAFT / DEVNET artifact — see docs/TOKEN_ROADMAP.md',
    hardCapSol: cfg.sale.hardCapSol,
    allowlisted: Boolean(allowlistArgs),
    quoteSplitBps: bps,
  };
  const out = saveArtifact(PRESALE_ARTIFACT, artifact);
  console.log('\n=== DONE ===');
  console.log('Genesis account :', artifact.genesisAccount);
  console.log('Presale bucket  :', artifact.bucket);
  console.log('Artifact written:', out);
  console.log('Next: `npm run presale:deposit -- --amount 1` during the deposit window.');
}

// ── deposit: depositPresaleV2 ───────────────────────────────────────────────
async function cmdDeposit() {
  const cfg = loadConfig();
  const env = loadEnv();
  const umi = makeUmi(env);
  // Every command that opens a connection verifies the cluster by GENESIS HASH.
  // This one did not, and `rpcUrl()`'s `url.includes('mainnet')` was the only
  // thing standing in front of it — a case-sensitive substring test that lets
  // through `https://API.MAINNET-BETA.SOLANA.COM` (DNS is case-insensitive; the
  // test is not), every rebranded endpoint, and any bare IP.
  await assertDevnet(umi);
  const a = requirePresale();
  const amountSol = Number(arg('--amount') || '0');
  if (!(amountSol > 0)) throw new Error('Provide --amount <SOL> greater than 0.');
  const amountQuoteToken = BigInt(Math.round(amountSol * 1e9));

  const input = {
    genesisAccount: publicKey(a.genesisAccount),
    bucket: publicKey(a.bucket),
    baseMint: publicKey(a.baseMint),
    amountQuoteToken,
  };
  // Whitelist proofs. Two decisions here, and the first one used to be made by
  // the wrong clock.
  //
  // ALWAYS PRESENT A PROOF IF WE HAVE ONE. The client used to present it only
  // when `Date.now()` said the gated round was still open, while the PROGRAM
  // decides whether to require one from the on-chain Clock. Those disagree by
  // however far the cluster lags — 14.8 seconds on the validator this was found
  // on. Inside that window a wallet that IS whitelisted sends no proof, the
  // program still wants one, and the deposit dies with
  //
  //     Program log: The merkle proof is invalid   (custom program error 0x4a)
  //
  // which reads like a corrupted allowlist and is really two clocks disagreeing.
  // The wallets it hits are exactly the ones that were invited.
  //
  // A proof presented AFTER the allowlist expires is harmless — verified on a
  // validator, deposit lands normally — so there is no reason to withhold it and
  // every reason not to guess. This removes the client's dependence on clock
  // agreement entirely.
  const wl = readArtifact(ALLOWLIST_ARTIFACT);
  let allowlistContext = null;
  if (wl) {
    const me = umi.identity.publicKey.toString();
    const hexProof = wl.proofByAddress?.[me];
    if (hexProof) {
      input.proof = proofToPublicKeys(hexProof);
      console.log(`  presenting whitelist proof (${hexProof.length} nodes) — sent regardless of round`);
    } else {
      allowlistContext = { me, publicStart: cfg.timeline.publicStart };
      console.log('  no whitelist proof for this wallet — the program decides whether the gated round is still open.');
    }
  }
  // The quote token is WRAPPED SOL, and depositPresaleV2 pulls it from the
  // depositor's wSOL token account — it does not take lamports directly. A
  // wallet that has never wrapped SOL has no such account, and the program
  // rejects the empty system-owned address it finds there with
  //   "The token account is not owned by the SPL Token program"
  // which reads like a config bug and is really a missing wrap. Found on
  // devnet, on the first deposit ever attempted. So: create the ATA if
  // missing, move the lamports in, sync, and deposit — one atomic transaction.
  const wsolAta = findAssociatedTokenPda(umi, {
    mint: WRAPPED_SOL_MINT,
    owner: umi.identity.publicKey,
  });
  console.log(`Depositing ${amountSol} SOL into presale bucket ${a.bucket}…`);
  console.log(`  wrapping ${amountSol} SOL into ${wsolAta[0]} in the same transaction`);
  const builder = createAtaIdempotent(umi, {
    mint: WRAPPED_SOL_MINT,
    owner: umi.identity.publicKey,
  })
    .add(transferSol(umi, { destination: wsolAta[0], amount: lamports(amountQuoteToken) }))
    .add(syncNative(umi, { account: wsolAta[0] }))
    .add(depositPresaleV2(umi, input));
  let sig;
  try {
    sig = await sendChecked(builder, umi, 'depositPresaleV2');
  } catch (err) {
    // 0x4a is "The merkle proof is invalid", which is what the program returns
    // when the gated round is still open and no valid proof was supplied. On its
    // own it reads like a corrupted allowlist. Say what it actually means.
    //
    // This is where the boundary is arbitrated, and deliberately so. The client
    // CANNOT predict it: the clock a transaction executes against is a moving
    // target, and reads at finalized, confirmed and getBlockTime were measured
    // up to 14.8 seconds apart on one validator. Every attempt to pre-judge it
    // here produced either a false refusal or a confusing failure. The program
    // is the authority; the client's job is to explain its answer.
    if (allowlistContext && /merkle proof is invalid|0x4a/i.test(String(err?.message ?? err))) {
      const chainNow = await onChainUnixTime(umi);
      throw new Error(
        `Deposit refused: wallet ${allowlistContext.me} is not on the whitelist, and the gated ` +
        `round is still open on chain.\n` +
        `  public round opens : ${allowlistContext.publicStart}\n` +
        `  chain time now     : ${chainNow ? new Date(chainNow * 1000).toISOString() : 'unknown'}\n` +
        `Add the wallet to config.whitelist and re-run presale:whitelist, or wait for the public ` +
        `round. Note the chain's clock can trail wall-clock by tens of seconds, so "it is past the ` +
        `time on my watch" is not the same as the round being open.\n` +
        `(underlying: ${String(err?.message ?? err).split('\n')[0]})`
      );
    }
    throw err;
  }
  console.log('Deposit confirmed. tx:', sig);
}

/**
 * The cluster's own clock, as the program sees it.
 *
 * Read from the Clock sysvar rather than `Date.now()`, because those are not the
 * same number: a local validator was observed 14.8 seconds behind wall clock,
 * and any comparison against a config timestamp has to use the one the program
 * will use. Returns null if the sysvar cannot be read, so callers fall through
 * to letting the program decide rather than refusing on a failed lookup.
 *
 * Clock layout: slot(8) epoch_start_timestamp(8) epoch(8) leader_schedule_epoch(8)
 * unix_timestamp(8) — so the i64 we want starts at byte 32.
 */
async function onChainUnixTime(umi) {
  try {
    // CONFIRMED, not umi's default. Umi reads at `finalized`, which trails by
    // ~32 slots — measured at 12 seconds against the same sysvar read at
    // confirmed. A transaction executes against the current bank, so finalized
    // is not the clock the program will apply, and using it here made this guard
    // refuse deposits the program would have accepted. A false refusal is the
    // same defect as a false acceptance, pointed the other way.
    const acct = await umi.rpc.getAccount(
      publicKey('SysvarC1ock11111111111111111111111111111111'),
      { commitment: 'confirmed' }
    );
    if (!acct.exists || acct.data.length < 40) return null;
    const view = new DataView(acct.data.buffer, acct.data.byteOffset, acct.data.byteLength);
    return Number(view.getBigInt64(32, true));
  } catch {
    return null;
  }
}

// ── whitelist: build a Merkle allowlist from config + persist proofs ─────────
function cmdWhitelist() {
  const cfg = loadConfig();
  const addresses = Array.isArray(cfg.whitelist) ? cfg.whitelist : [];
  if (!addresses.length) {
    throw new Error('config.whitelist is empty. Add base58 addresses to metaplex-genesis.config.json.');
  }
  const wl = buildAllowlist(cfg, addresses);
  const out = saveArtifact(ALLOWLIST_ARTIFACT, {
    rootHex: wl.rootHex,
    treeHeight: wl.treeHeight,
    count: addresses.length,
    proofByAddress: wl.proofByAddress,
    note: 'DRAFT / DEVNET Merkle allowlist — see docs/TOKEN_ROADMAP.md',
  });
  console.log('=== Whitelist built ===');
  console.log('Members    :', addresses.length);
  console.log('Tree height:', wl.treeHeight);
  console.log('Merkle root:', wl.rootHex);
  console.log('Artifact   :', out);
  console.log('The root is applied to the presale bucket on `presale:create`; deposits during');
  console.log('the whitelist window automatically present each wallet\'s proof.');
}

// ── liquidity: add a Raydium CPMM bucket with a permanent LP lock ────────────
async function cmdLiquidity() {
  const cfg = loadConfig();
  const env = loadEnv();
  const umi = makeUmi(env);
  // Every command that opens a connection verifies the cluster by GENESIS HASH.
  // This one did not, and `rpcUrl()`'s `url.includes('mainnet')` was the only
  // thing standing in front of it — a case-sensitive substring test that lets
  // through `https://API.MAINNET-BETA.SOLANA.COM` (DNS is case-insensitive; the
  // test is not), every rebranded endpoint, and any bare IP.
  await assertDevnet(umi);
  const a = requirePresale();
  const lp = deriveLiquidityParams(cfg);

  // The split is now encoded on-chain by presale:create, so this no longer
  // refuses outright — it checks that what was encoded actually points HERE.
  //
  // The token side of this bucket is irrevocable (never-claim LP lock), so it
  // must not be created against a presale whose quote-side routing is missing or
  // aimed somewhere else. Both are silent failures otherwise: the lock would
  // exist and the SOL would never arrive.
  const expectedBucket = findRaydiumCpmmBucketV2Pda(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    bucketIndex: LIQUIDITY_BUCKET_INDEX,
  })[0].toString();

  if (lp.raisedSolToLiquidityBps > 0) {
    if (!a.quoteSplitBps) {
      throw new Error(
        `config.liquidity.raisedSolToLiquidityBps is ${lp.raisedSolToLiquidityBps}, but the ` +
        'presale bucket was created WITHOUT a SendQuoteTokenPercentage end behavior ' +
        `(artifact ${PRESALE_ARTIFACT} records no quoteSplitBps). The SOL side would never ` +
        'be routed. Re-create the presale with the split configured.'
      );
    }
    if (Number(a.quoteSplitBps) !== Number(lp.raisedSolToLiquidityBps)) {
      throw new Error(
        `Split mismatch: the presale bucket encodes ${a.quoteSplitBps} bps on-chain but the ` +
        `config now says ${lp.raisedSolToLiquidityBps} bps. The on-chain value is fixed at ` +
        'bucket creation and wins — reconcile the config or re-create the presale.'
      );
    }
    if (a.liquidityBucket && a.liquidityBucket !== expectedBucket) {
      throw new Error(
        `Destination mismatch: the encoded behavior sends the quote share to ` +
        `${a.liquidityBucket}, but this command would create the bucket at ${expectedBucket}.`
      );
    }
  }

  // The artifact records what was encoded at creation; the chain records what is
  // encoded NOW. Only the second one executes, and the authority can change it.
  await assertEncodedSplitOnChain(umi, a, cfg);

  console.log(`Adding Raydium CPMM liquidity bucket (${cfg.liquidity.tokenAllocation} ${cfg.token.symbol}, permanent LP lock)…`);
  if (lp.raisedSolToLiquidityBps > 0) {
    console.log(
      `  quote side: ${lp.raisedSolToLiquidityBps / 100}% of the raise is routed ON CHAIN to ` +
      `${expectedBucket} by the presale bucket's SendQuoteTokenPercentage behavior.`
    );
    console.log('  Run `npm run presale:trigger` after the deposit window closes to execute it.');
  }
  const sig = await sendChecked(
    addRaydiumCpmmBucketV2(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      baseMint: publicKey(a.baseMint),
      bucketIndex: LIQUIDITY_BUCKET_INDEX,
      baseTokenAllocation: lp.baseTokenAllocation,
      lpLockSchedule: lp.lpLockSchedule, // never-claim => LP locked forever
      startCondition: lp.startCondition, // pool created at deposit-window close
    }),
    umi,
    'addRaydiumCpmmBucketV2'
  );
  console.log('Liquidity bucket added; LP is permanently locked (never-claim schedule). tx:', sig);
  // finalizeV2 sums every bucket's allocation. If this one is not readable yet
  // the sum comes up short and the program reports "Total supply must be fully
  // allocated before finalize" — an allocation error that is really RPC lag.
  await awaitAccount(umi, expectedBucket, 'liquidity bucket');
}

/**
 * Both withdraw paths are V1-only and CANNOT run against the V2 presale this
 * tooling creates. Refuse up front instead of failing on chain.
 *
 * Proven on devnet, 2026-07-26. `presale:create` builds a V2 launch
 * (initializeV2 + addPresaleBucketV2*), and both withdraw instructions reject
 * it:
 *
 *   Program log: WithdrawPresaleV1
 *   Program log: The Genesis Account is invalid      (custom program error 0x2f)
 *
 *   Program log: WithdrawUnsoldPresaleV1
 *   Program log: The Genesis Account is invalid      (custom program error 0x2f)
 *
 * `withdrawPresaleV1` is explicitly bound to V1 — its generated code references
 * GenesisAccountV1 and findPresaleBucketV1Pda — and the SDK ships no
 * `withdrawPresaleV2` or `withdrawUnsoldPresaleV2` at all.
 *
 * The consequences are worth stating plainly rather than burying:
 *
 *   - There is NO depositor refund for a V2 presale. Once a deposit lands it
 *     cannot be returned by any instruction in this program. This is strictly
 *     worse than audit finding F-12, which observed only that the soft cap
 *     reaches no instruction; the refund does not exist at any cap.
 *   - There is NO unsold-token recovery. On a partial raise the unsold share of
 *     the presale allocation stays in the bucket. The plausible V2 mechanism is
 *     a `BaseTokenRollover` end behavior routing the remainder to another
 *     bucket — the behavior exists in the SDK — but it is NOT implemented here
 *     and NOT tested, so it must not be assumed to work.
 *
 * Failing loudly here matters more than usual: the moment either of these is
 * reached is the moment somebody is trying to get value back.
 */
function refuseV1WithdrawPath(command, consequence) {
  throw new Error(
    `${command} cannot run against this presale.\n\n` +
    'It uses a V1-only instruction, and `presale:create` builds a V2 launch. The program ' +
    'rejects the combination with "The Genesis Account is invalid" (0x2f), and the SDK ships ' +
    'no V2 equivalent — verified against devnet on 2026-07-26.\n\n' +
    `${consequence}\n\n` +
    'Do not work around this by re-running or by editing the artifact: the instruction does not ' +
    'exist for V2, so there is nothing to point it at. See docs/TOKEN_SECURITY_AUDIT.md.'
  );
}

// ── withdraw: depositor cancels their deposit (refund) ──────────────────────
async function cmdWithdraw() {
  const env = loadEnv();
  const umi = makeUmi(env);
  // Every command that opens a connection verifies the cluster by GENESIS HASH.
  // This one did not, and `rpcUrl()`'s `url.includes('mainnet')` was the only
  // thing standing in front of it — a case-sensitive substring test that lets
  // through `https://API.MAINNET-BETA.SOLANA.COM` (DNS is case-insensitive; the
  // test is not), every rebranded endpoint, and any bare IP.
  await assertDevnet(umi);
  const a = requirePresale();
  const bucket = publicKey(a.bucket);
  const mint = publicKey(a.baseMint);
  const me = umi.identity.publicKey;
  const depositPda = findPresaleDepositV2Pda(umi, { bucket, recipient: me });
  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
  refuseV1WithdrawPath(
    'presale:withdraw',
    'CONSEQUENCE: there is no depositor refund for a V2 presale. A deposit, once made, cannot ' +
    'be returned by any instruction. Published sale terms must not promise one.'
  );
  console.log(`Withdrawing (refunding) this wallet's deposit from bucket ${a.bucket}…`);
  const sig = await sendChecked(
    withdrawPresaleV1(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      bucket,
      mint,
      depositPda: depositPda[0],
      recipientTokenAccount: recipientTokenAccount[0],
    }),
    umi,
    'withdrawPresaleV1'
  );
  console.log('Withdraw/refund confirmed. tx:', sig);
  console.log('(Soft-cap/refund is enforced operationally — see RUNBOOK.)');
}

// ── withdraw-unsold: operator recovers unsold presale tokens ─────────────────
async function cmdWithdrawUnsold() {
  const env = loadEnv();
  const umi = makeUmi(env);
  // Every command that opens a connection verifies the cluster by GENESIS HASH.
  // This one did not, and `rpcUrl()`'s `url.includes('mainnet')` was the only
  // thing standing in front of it — a case-sensitive substring test that lets
  // through `https://API.MAINNET-BETA.SOLANA.COM` (DNS is case-insensitive; the
  // test is not), every rebranded endpoint, and any bare IP.
  await assertDevnet(umi);
  const a = requirePresale();
  const bucket = publicKey(a.bucket);
  const mint = publicKey(a.baseMint);
  // Destination is explicit and reviewable rather than implicitly "whoever
  // signed". Unsold supply is a large, silent transfer; --recipient makes it a
  // decision, and treasury.recipient makes it a committed one.
  const cfg = loadConfig();
  const flagRecipient = arg('--recipient');
  const configured = cfg.treasury?.recipient;
  const me = flagRecipient
    ? publicKey(flagRecipient)
    : (configured ? publicKey(configured) : umi.identity.publicKey);
  if (!flagRecipient && !configured) {
    console.warn(
      'WARNING: no --recipient and no treasury.recipient — sending unsold supply to the\n' +
      '         signing key by default. Pass --recipient <address> or set treasury.recipient.'
    );
  }
  const bucketTokenAccount = findAssociatedTokenPda(umi, { mint, owner: bucket });
  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
  refuseV1WithdrawPath(
    'presale:withdraw-unsold',
    'CONSEQUENCE: unsold presale tokens cannot be recovered this way. On a partial raise the ' +
    'unsold share stays in the bucket. The likely V2 mechanism is a BaseTokenRollover end ' +
    'behavior routing the remainder elsewhere — present in the SDK, NOT implemented or tested ' +
    'here. Treat unsold supply as stranded until that is built and proven.'
  );
  console.log(`Withdrawing unsold presale tokens from bucket ${a.bucket} to ${me}…`);
  const builder = withdrawUnsoldPresaleV1(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    bucket,
    mint,
    bucketTokenAccount: bucketTokenAccount[0],
    recipient: me,
    recipientTokenAccount: recipientTokenAccount[0],
    associatedTokenProgram: publicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'),
    // `index` and `padding` are REQUIRED (non-optional) on this instruction —
    // omitting them throws before a transaction is built.
    index: BUCKET_INDEX,
    padding: [0, 0, 0, 0, 0, 0],
  });
  const sig = await sendChecked(builder, umi, 'withdrawUnsoldPresaleV1');
  console.log('Unsold-token withdrawal confirmed. tx:', sig);
}

// ── trigger: execute the presale bucket's end behaviors ─────────────────────
//
// This is what actually moves the quote share to the liquidity bucket. It is
// PERMISSIONLESS — triggerBehaviorsV2 takes a payer but no authority — which is
// the whole point: the percentage is fixed on-chain at bucket creation, and any
// participant can execute it, so the operator can neither change the share nor
// quietly decline to send it. That is the difference between an enforced split
// and a promise.
async function cmdTrigger() {
  const env = loadEnv();
  const umi = makeUmi(env);
  await assertDevnet(umi);
  const a = requirePresale();

  if (!a.quoteSplitBps) {
    console.log('This presale encodes no end behaviors — nothing to trigger.');
    return;
  }
  console.log(
    `Triggering end behaviors on bucket ${a.bucket}: ` +
    `${a.quoteSplitBps} bps (${a.quoteSplitBps / 100}%) of the raise -> ${a.liquidityBucket}`
  );
  console.log('Note: this only succeeds once the deposit window has closed.');
  const triggerCfg = loadConfig();
  await assertEncodedSplitOnChain(umi, a, triggerCfg);
  await assertPoolOpensAtOrAbovePresale(umi, a, triggerCfg);

  // The DESTINATION bucket must be passed as a remaining account, and its quote
  // token account with it. Neither appears in the instruction's declared
  // accounts — the SDK lists only genesisAccount, primaryBucket and baseMint —
  // so this is invisible from the type surface. Without them the program cannot
  // resolve where the behavior points and rejects with:
  //
  //   Program log: TriggerBehaviorsV2
  //   Program log: The destination bucket was not found
  //
  // Same shape as finalizeV2's bucket accounts. Found on devnet: this command
  // could never have executed, which means the raise-to-liquidity split — the
  // entire F-11 remediation — had no working execution path.
  const destination = publicKey(a.liquidityBucket);
  // The quote tokens land in the bucket's SIGNER PDA, not in the bucket account
  // itself — RaydiumCpmmBucketV2.bucketSigner is documented as "holds quote
  // tokens from transition, LP tokens, lamports". Deriving the ATA against the
  // bucket address instead produces an account the program rejects with "The
  // Token Account's owner does not match the recipient". Read the signer from
  // chain rather than re-deriving it, so a seed change upstream cannot silently
  // point this at the wrong account.
  const { fetchRaydiumCpmmBucketV2 } = await import('@metaplex-foundation/genesis');
  const lpBucket = await fetchRaydiumCpmmBucketV2(umi, destination);
  const destinationOwner = lpBucket.bucketSigner;
  const destinationQuoteAta = findAssociatedTokenPda(umi, {
    mint: WRAPPED_SOL_MINT,
    owner: destinationOwner,
  });
  console.log(`  destination bucket signer: ${destinationOwner}`);
  console.log(`  passing destination bucket ${destination} as a remaining account`);

  // Unsold presale tokens. An unlocked bucket tracks base tokens in its own
  // `bucket.baseTokenBalance` field rather than a separate ATA, so the bucket
  // account itself is what has to be writable here.
  const rollover = deriveUnsoldRollover(triggerCfg);
  let rolloverAccounts = [];
  let rolloverDestination = null;
  let rolloverQuoteAta = null;
  if (rollover) {
    rolloverDestination = findUnlockedBucketV2Pda(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      bucketIndex: rollover.bucketIndex,
    })[0];
    // PAIRS, not single accounts. The program rejects an odd count with
    //
    //   InvalidRemainingAccountsLength: Remaining accounts must be provided in
    //   pairs (bucket, quote_token_account)
    //
    // — so every behavior destination contributes two entries, even a
    // BaseTokenRollover that moves no quote tokens at all. The pairing is the
    // program's calling convention, not a statement about what each behavior
    // uses. Nothing in the SDK's type surface says this; the error did.
    rolloverQuoteAta = findAssociatedTokenPda(umi, {
      mint: WRAPPED_SOL_MINT,
      owner: rolloverDestination,
    })[0];
    rolloverAccounts = [
      { pubkey: rolloverDestination, isSigner: false, isWritable: true },
      { pubkey: rolloverQuoteAta, isSigner: false, isWritable: true },
    ];
    console.log(
      `  unsold rollover: ${rollover.percentageBps / 100}% -> "${rollover.name}" ${rolloverDestination}`
    );
  }

  // Read the balances BEFORE, so what actually moved can be reported rather
  // than assumed. A behavior that silently does nothing looks identical to one
  // that worked, and this is the only chance to tell the difference.
  const before = await readBucketBalances(umi, a.bucket, rolloverDestination);
  // The destination bucket's wSOL account has to exist before the quote share
  // can be moved into it, and nothing creates it earlier: the liquidity bucket
  // is created with a base-token allocation and no quote side. The bucket is a
  // PDA, so this is an off-curve ATA — legal, but it will not appear by itself.
  const sig = await sendChecked(
    createAtaIdempotent(umi, {
      mint: WRAPPED_SOL_MINT,
      owner: destinationOwner,
    }).add(
    // The rollover destination's quote account has to exist too — it is passed
    // as the second half of its remaining-accounts pair whether or not the
    // behavior writes to it.
    rolloverQuoteAta
      ? createAtaIdempotent(umi, { mint: WRAPPED_SOL_MINT, owner: rolloverDestination })
      : transactionBuilder()
    ).add(
    triggerBehaviorsV2(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      primaryBucket: publicKey(a.bucket),
      baseMint: publicKey(a.baseMint),
    }).addRemainingAccounts([
      { pubkey: destination, isSigner: false, isWritable: true },
      { pubkey: destinationQuoteAta[0], isSigner: false, isWritable: true },
      // The BaseTokenRollover behavior's destination, if one is configured.
      // Same invisible-requirement shape as the quote side: the instruction's
      // declared accounts name only genesisAccount, primaryBucket and baseMint,
      // so every behavior's destination has to be appended by hand or the
      // program cannot resolve where it points.
      ...rolloverAccounts,
    ])),
    umi,
    'triggerBehaviorsV2'
  );
  console.log('Behaviors executed. tx:', sig);
  await reportRollover(umi, a.bucket, rolloverDestination, before, rollover);
  console.log('Verify the liquidity bucket received the quote share before announcing the pool.');
}

/**
 * Read the presale bucket's end behaviors FROM CHAIN and check the quote split
 * is still what the config says.
 *
 * This exists because a claim shipped in #836 was wrong. That PR said the split
 * was "fixed at bucket creation" and that "the operator can neither change the
 * share nor decline to send it". The SDK has `setPresaleBucketV2Behaviors`
 * (discriminator 80), which takes the genesis authority and REPLACES
 * `endBehaviors` wholesale. So the authority can lower the percentage, repoint
 * the destination, or delete the behavior entirely, at any time before someone
 * triggers it. `triggerBehaviorsV2` being permissionless means a participant can
 * win the race — it does not mean the authority cannot move first.
 *
 * What is actually true is weaker and worth stating plainly: the split is
 * PUBLICLY VERIFIABLE rather than immutable. This function is what makes that
 * real — it reads the live account instead of trusting the creation-time
 * artifact, so a silent change is detectable by anyone before the irreversible
 * steps run.
 */
/**
 * Base-token balances of the presale bucket and the rollover destination.
 *
 * `BucketBase.baseTokenBalance` is the bucket's own accounting of what it holds,
 * so this is the number a rollover moves — read it either side of the trigger
 * rather than trusting that the behavior ran. A behavior that silently does
 * nothing produces the same transaction result as one that worked, which is
 * exactly the failure mode that let the quote split ship with no execution path.
 */
async function readBucketBalances(umi, presaleBucket, rolloverDestination) {
  const { fetchPresaleBucketV2, fetchUnlockedBucketV2 } = await import('@metaplex-foundation/genesis');
  const presale = await fetchPresaleBucketV2(umi, publicKey(presaleBucket));
  const out = { presale: BigInt(presale.bucket.baseTokenBalance) };
  if (rolloverDestination) {
    const dest = await fetchUnlockedBucketV2(umi, rolloverDestination);
    out.destination = BigInt(dest.bucket.baseTokenBalance);
  }
  return out;
}

/** Report what the rollover actually moved, and fail loudly if it moved nothing. */
async function reportRollover(umi, presaleBucket, rolloverDestination, before, rollover) {
  if (!rollover || !rolloverDestination) {
    console.log('  no unsold rollover configured — any unsold presale tokens stay in the bucket,');
    console.log('  and no V2 instruction can move them out. See sale._unsoldRolloverNote.');
    return;
  }
  const after = await readBucketBalances(umi, presaleBucket, rolloverDestination);
  const movedOut = before.presale - after.presale;
  const movedIn = after.destination - before.destination;

  console.log(`  unsold rollover: presale bucket ${before.presale} -> ${after.presale}`);
  console.log(`                   "${rollover.name}" ${before.destination} -> ${after.destination}`);

  if (movedIn !== movedOut) {
    throw new Error(
      `Rollover accounting does not balance: ${movedOut} left the presale bucket but ${movedIn} ` +
      `arrived in "${rollover.name}". The difference is unaccounted supply.`
    );
  }
  if (movedOut === 0n && before.presale > 0n) {
    throw new Error(
      `The BaseTokenRollover behavior moved NOTHING while ${before.presale} base units of unsold ` +
      'presale tokens remain in the bucket. Those tokens have no other exit — there is no V2 ' +
      'withdraw-unsold instruction. Do not announce the sale as complete; investigate first.'
    );
  }
  console.log(`  ${movedOut} base units of unsold supply rolled over and are now claimable from "${rollover.name}".`);
}

/**
 * Refuse to open the pool below the presale price without a deliberate override.
 *
 * `liquidity.tokenAllocation` is fixed at bucket creation and prices the pool
 * for a raise it cannot know: pool price = raise x split / tokenAllocation, so
 * it is LINEAR in the realised raise. Sizing for the soft cap (the decision
 * recorded in the config) means the pool opens at or above the presale price
 * from `sizedForRaiseSol` upward — and below it under that.
 *
 * Nothing on chain prevents that. `sale.softCapSol` reaches no account, so the
 * program happily ends a sale at any raise, and `triggerBehaviorsV2` is
 * PERMISSIONLESS — any participant can fire it. No allocation can close the gap
 * either, because the value required for parity falls to zero with the raise.
 *
 * So this is the last point at which a human is still in the loop, and it reads
 * the realised raise from the bucket rather than assuming the soft cap held. It
 * REFUSES rather than warns, because the pool it would create is permanent (the
 * LP lock is never-claim) and no refund instruction exists for a V2 presale —
 * the buyer would be underwater with no way out and no way back.
 *
 * It is an override, not a lock. Declining to trigger leaves the raise sitting
 * in the presale bucket, and with no V2 withdraw path that is its own kind of
 * stuck — so refusing outright would be trading one irreversible outcome for
 * another. `--accept-below-presale` makes the choice explicit and logged
 * instead of silent.
 */
/**
 * Read a freshly created stream back off the chain and check it says what we
 * asked for.
 *
 * Everything else about vesting is asserted client-side, against the object this
 * repo builds. That proves the derivation and nothing about what was STORED — a
 * serializer that drops a field, a program that defaults one, or an argument
 * that silently does not reach the account would all leave those tests green.
 * The distinction is not hypothetical here: the same "verified the input, never
 * the result" gap is what let a non-idempotent ATA instruction and a dead
 * command-line flag both ship.
 *
 * Two things are worth the round trip, and only these two:
 *
 *   CONSERVATION - cliffAmount + amountPerPeriod * periods must equal the
 *     allocation exactly. Anything the schedule fails to release is stranded in
 *     the stream with no instruction to recover it.
 *   THE FLAGS - cancelable / pausable / transferable / rate-editable must all be
 *     false. Any one of them true means the schedule can be revoked or rewritten
 *     later, which is the difference between vesting and a promise.
 *
 * This runs at bucket creation, which is the last moment anything can be done
 * about it: finalizeV2 locks bucket configuration permanently.
 */
async function assertStreamOnChain(umi, address, b) {
  const { fetchStreamflowBucketV2 } = await import('@metaplex-foundation/genesis');
  const acct = await fetchStreamflowBucketV2(umi, address);
  const c = acct.config;

  const allocation = BigInt(acct.bucket.baseTokenAllocation);
  const cliffAmount = BigInt(c.cliffAmount);
  const perPeriod = BigInt(c.amountPerPeriod);
  if (perPeriod <= 0n) {
    throw new Error(`${b.name}: on-chain amountPerPeriod is ${perPeriod} — the stream would never pay out.`);
  }
  const periods = (allocation - cliffAmount) / perPeriod;
  const released = cliffAmount + perPeriod * periods;
  if (released !== allocation) {
    throw new Error(
      `${b.name}: the stream ON CHAIN does not release its whole allocation — ` +
      `${released} of ${allocation}, stranding ${allocation - released} base units permanently. ` +
      'finalizeV2 would lock this in.'
    );
  }

  const wrong = Object.entries(REQUIRED_STREAM_FLAGS).filter(([f, want]) => c[f] !== want);
  if (wrong.length) {
    throw new Error(
      `${b.name}: stream flags ON CHAIN are not what was configured — ` +
      wrong.map(([f, want]) => `${f}=${c[f]} (expected ${want})`).join(', ') + '.\n' +
      'A cancelable, pausable, transferable or rate-editable stream is not a vesting commitment. ' +
      'Do NOT finalize; the configuration is locked permanently once you do.'
    );
  }
  if (acct.recipient.toString() !== b.recipient) {
    throw new Error(`${b.name}: on-chain recipient ${acct.recipient} != configured ${b.recipient}.`);
  }
  console.log(`     verified on chain: releases exactly ${allocation} over ${periods} periods; all permissions off`);
}

async function assertPoolOpensAtOrAbovePresale(umi, artifact, cfg) {
  const floorSol = Number(cfg.liquidity?.sizedForRaiseSol ?? 0);
  if (!(floorSol > 0)) return null; // not configured — nothing claimed, nothing to check

  const { fetchPresaleBucketV2 } = await import('@metaplex-foundation/genesis');
  const bucket = await fetchPresaleBucketV2(umi, publicKey(artifact.bucket));
  const raisedLamports = BigInt(bucket.quoteTokenDepositTotal);
  const raisedSol = Number(raisedLamports) / 1e9;

  const decimals = BigInt(cfg.token.decimals);
  const lpBaseUnits = BigInt(cfg.liquidity.tokenAllocation) * 10n ** decimals;
  const bps = BigInt(cfg.liquidity.raisedSolToLiquidityBps ?? 0);
  const quoteToPool = (raisedLamports * bps) / 10_000n;

  // Exact BigInt cross-multiplication rather than floats: at the boundary the
  // two prices differ by a lamport, and a float comparison decides it wrongly.
  //   poolPrice   = quoteToPool / lpBaseUnits
  //   presalePrice = hardCapLamports / presaleBaseUnits
  const hardCapLamports = BigInt(Math.round(cfg.sale.hardCapSol * 1e9));
  const presaleBaseUnits = BigInt(cfg.sale.presaleAllocation) * 10n ** decimals;
  const atOrAbove = quoteToPool * presaleBaseUnits >= hardCapLamports * lpBaseUnits;

  const ratio = lpBaseUnits === 0n ? 0
    : (Number(quoteToPool) / Number(lpBaseUnits)) / (Number(hardCapLamports) / Number(presaleBaseUnits));

  console.log(`  realised raise: ${raisedSol} SOL (read from the bucket, not assumed)`);
  console.log(`  pool opens at ${ratio.toFixed(4)}x the presale price`);

  if (atOrAbove) return { raisedSol, ratio, ok: true };

  if (hasFlag('--accept-below-presale')) {
    console.warn(
      `PROCEEDING BELOW PRESALE PRICE ON AN EXPLICIT OVERRIDE.\n` +
      `  The raise is ${raisedSol} SOL against a pool sized for ${floorSol} SOL, so the pool will ` +
      `open at ${ratio.toFixed(4)}x the presale price.\n` +
      `  Every presale buyer is underwater at listing, the LP lock is PERMANENT, and there is no ` +
      `refund instruction. This is not undoable.`
    );
    return { raisedSol, ratio, ok: false, overridden: true };
  }

  throw new Error(
    `Refusing to open the pool below the presale price.\n` +
    `  realised raise      : ${raisedSol} SOL\n` +
    `  pool was sized for  : ${floorSol} SOL (liquidity.sizedForRaiseSol)\n` +
    `  pool opening price  : ${ratio.toFixed(4)}x the presale price\n` +
    `\n` +
    `Triggering now creates a PERMANENT pool (never-claim LP lock) in which every presale buyer is ` +
    `underwater at listing, and no refund instruction exists for a V2 presale — there is no way back ` +
    `for them and no way out for you.\n` +
    `\n` +
    `This is a decision, not a bug. Either the raise fell short of what the sale was priced for, or ` +
    `liquidity.sizedForRaiseSol does not describe liquidity.tokenAllocation. Check which, then:\n` +
    `  - re-run with --accept-below-presale to proceed deliberately (logged, still irreversible), or\n` +
    `  - hold off and decide what to tell depositors first. Note that NOT triggering leaves the raise ` +
    `in the presale bucket with no V2 withdraw path, so this is not a free option either.`
  );
}

async function assertEncodedSplitOnChain(umi, artifact, cfg) {
  const { fetchPresaleBucketV2 } = await import('@metaplex-foundation/genesis');
  const wantBps = Number(cfg.liquidity.raisedSolToLiquidityBps ?? 0);
  if (wantBps <= 0) return null;

  // Retry before concluding the behavior is absent. This guard is fail-closed by
  // design, but an RPC that has not caught up yet is not the same thing as a
  // missing behavior — and treating it as one blocks a correct launch while
  // reporting tampering. Seen on devnet: this fired immediately after a
  // setPresaleBucketV2Behaviors that had confirmed. Bounded retries keep the
  // guard's meaning (a genuinely absent behavior still fails) without the false
  // alarm.
  let split = null;
  let bucket = null;
  for (let i = 0; i < 15; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    bucket = await fetchPresaleBucketV2(umi, publicKey(artifact.bucket));
    split = (bucket.endBehaviors || []).find((b) => b.__kind === 'SendQuoteTokenPercentage');
    if (split) break;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, 1000));
  }
  if (!split) {
    throw new Error(
      `The presale bucket on chain carries NO SendQuoteTokenPercentage behavior, but the config ` +
      `expects ${wantBps} bps routed to the pool.\n` +
      'The genesis authority can replace end behaviors with setPresaleBucketV2Behaviors, so this ' +
      'is a state someone can produce deliberately. Do not proceed: the SOL side of the pool ' +
      'would never arrive.'
    );
  }
  if (Number(split.percentageBps) !== wantBps) {
    throw new Error(
      `On-chain quote split is ${split.percentageBps} bps but the config says ${wantBps} bps. ` +
      'The on-chain value is what executes. Reconcile before proceeding.'
    );
  }
  const wantDest = artifact.liquidityBucket;
  if (String(split.destinationBucket) !== String(wantDest)) {
    throw new Error(
      `On-chain quote split points at ${split.destinationBucket}, not the liquidity bucket ` +
      `${wantDest}. The raise would be routed somewhere else.`
    );
  }
  console.log(
    `  verified on chain: SendQuoteTokenPercentage ${split.percentageBps} bps -> ` +
    `${split.destinationBucket}${split.processed ? ' (ALREADY PROCESSED)' : ''}`
  );
  return split;
}

// ── allocate: create the buckets for the rest of the supply ─────────────────
//
// Without this the launch CANNOT be opened. `finalizeV2` refuses while any base
// token is unallocated — "Total supply must be fully allocated before finalize" —
// and finalize is what enables deposits. The presale and liquidity buckets cover
// 25% of a 1,000,000,000 supply, so the other 75% needs buckets or the whole
// thing is built and permanently unopenable.
//
// These are UNLOCKED buckets: an allocation to a recipient, claimable after a
// cliff. They do NOT implement the linear vesting tails the §4 table specifies
// for team, advisors and community — that needs Streamflow buckets, which are
// not built here. `unlockAt` is a hard cliff for the FULL amount, and the
// published vesting terms must match that or they are false.
async function cmdAllocate() {
  const cfg = loadConfig();
  const env = loadEnv();
  const umi = makeUmi(env);
  await assertDevnet(umi);
  const a = requirePresale();

  // Throws offline if the supply does not add up — before a single send.
  const { buckets, totalSupply } = deriveAllocationBuckets(cfg);
  const scale = 10n ** BigInt(cfg.token.decimals);

  console.log('=== Allocation buckets (DEVNET / DRAFT) ===');
  console.log('Total supply    :', (totalSupply / scale).toLocaleString(), cfg.token.symbol);
  console.log('Buckets to add  :', buckets.length);

  const written = { ...(a.allocationBuckets || {}) };
  // Indices 0 and 1 are the presale and liquidity buckets.
  let index = LIQUIDITY_BUCKET_INDEX + 1;
  for (const b of buckets) {
    const bucketIndex = index;
    index += 1;
    if (written[b.name]) {
      console.log(`  [skip] ${b.name} already created at ${written[b.name].address}`);
      continue;
    }
    // An unset recipient silently defaults to the signing key — the exact
    // single-hot-key custody roadmap §11 forbids. Refuse rather than default.
    if (!b.recipient) {
      throw new Error(
        `allocation "${b.name}" has no recipient. It would default to the signing key ` +
        `(${umi.identity.publicKey}), putting ${(b._tokens).toLocaleString()} tokens under one ` +
        'hot key. Set allocations.buckets[].recipient — the treasury bucket in particular MUST ' +
        'be the Squads vault PDA (docs/TOKEN_ROADMAP.md §11).'
      );
    }
    // Two bucket kinds. `cliff` is addUnlockedBucketV2 — the whole allocation
    // released at once, correct for the governance-gated buckets. `linear` is a
    // real Streamflow stream, which is what §4 promises for team, advisors and
    // community and what this repo did not implement until now.
    const isStream = b.kind === 'linear';
    const pda = isStream
      ? findStreamflowBucketV2Pda(umi, { genesisAccount: publicKey(a.genesisAccount), bucketIndex })
      : findUnlockedBucketV2Pda(umi, { genesisAccount: publicKey(a.genesisAccount), bucketIndex });

    const day = (t) => new Date(Number(t) * 1000).toISOString().slice(0, 10);
    if (isStream) {
      const s = b._schedule;
      console.log(
        `  [${bucketIndex}] ${b.name}: ${(b._tokens).toLocaleString()} -> ${b.recipient}
` +
        `        STREAM: cliff ${day(s.cliffTime)} (${s.cliffMonths}mo), then ${s.linearMonths}mo ` +
        `linear to ${day(s.endTime)} — ${s.periods} daily periods`
      );
    } else {
      console.log(
        `  [${bucketIndex}] ${b.name}: ${(b._tokens).toLocaleString()} -> ${b.recipient} ` +
        `(cliff ${day(b._unlockAt)} — FULL amount at once)`
      );
    }

    const builder = isStream
      ? addStreamflowBucketV2(umi, {
          genesisAccount: publicKey(a.genesisAccount),
          baseMint: publicKey(a.baseMint),
          bucketIndex,
          recipient: publicKey(b.recipient),
          baseTokenAllocation: b.baseTokenAllocation,
          config: b.streamConfig,
          lockStartCondition: b.lockStartCondition,
          lockEndCondition: b.lockEndCondition,
          // No backend signer and no separate Streamflow authority: both are
          // extra parties able to act on the stream, and a vesting schedule that
          // a third key can touch is not the thing §4 describes.
          backendSigner: null,
          streamflowAuthority: null,
        })
      : addUnlockedBucketV2(umi, {
          genesisAccount: publicKey(a.genesisAccount),
          baseMint: publicKey(a.baseMint),
          bucketIndex,
          recipient: publicKey(b.recipient),
          baseTokenAllocation: b.baseTokenAllocation,
          claimStartCondition: b.claimStartCondition,
          claimEndCondition: b.claimEndCondition,
        });

    const sig = await sendChecked(
      builder, umi, `${isStream ? 'addStreamflowBucketV2' : 'addUnlockedBucketV2'}(${b.name})`
    );
    console.log('     tx:', sig);
    await awaitAccount(umi, pda[0], `${b.name} bucket`);
    if (isStream) await assertStreamOnChain(umi, pda[0], b);
    written[b.name] = {
      address: pda[0].toString(), bucketIndex, tokens: b._tokens.toString(), tx: sig,
      kind: b.kind,
      ...(isStream ? { schedule: { cliff: day(b._schedule.cliffTime), end: day(b._schedule.endTime) } } : {}),
    };
    // Checkpoint after EVERY bucket: this is a multi-transaction sequence with
    // no atomicity, and a partial run must be resumable rather than repeated
    // (a repeat would try to reuse an index that already exists).
    saveArtifact(PRESALE_ARTIFACT, { ...a, allocationBuckets: written });
  }

  console.log('\nAll allocation buckets created. Supply is fully allocated —');
  console.log('`npm run presale:finalize` can now run.');
}

// ── finalize: lock the launch configuration and open it for deposits ─────────
//
// REQUIRED, and discovered only by running it. `depositPresaleV2` fails with
// custom program error 0x2c and the log "The Genesis Account is not finalized"
// until this is called, so a presale configured perfectly is simply not open for
// business. Nothing in the SDK's type surface says so — `finalizeV2` takes no
// arguments beyond padding, and `GenesisAccountV2.finalized` is just a boolean
// nobody has to read. It cost a full devnet run to find.
//
// Run it AFTER every bucket exists. Buckets are the configuration being frozen,
// so anything not added by this point may not be addable afterwards.
async function cmdFinalize() {
  const env = loadEnv();
  const umi = makeUmi(env);
  await assertDevnet(umi);
  const a = requirePresale();
  const cfg = loadConfig();

  // The last chance to check the split before deposits can start.
  await assertEncodedSplitOnChain(umi, a, cfg);

  console.log(`Finalizing genesis account ${a.genesisAccount}…`);
  console.log('  After this, deposits can be made. Configuration is being frozen —');
  console.log('  verify the presale bucket and the liquidity bucket are both correct NOW.');

  // EVERY bucket must be passed as a remaining account. The generated
  // instruction declares only genesisAccount/baseMint/authority, so this is
  // invisible from the SDK's type surface — the program reports it as:
  //   Program log: Missing bucket accounts for finalize
  // and it is checked in addition to the supply-fully-allocated rule.
  const allocationBuckets = Object.values(a.allocationBuckets || {}).map((b) => publicKey(b.address));
  const buckets = [publicKey(a.bucket), publicKey(a.liquidityBucket), ...allocationBuckets].filter(Boolean);
  console.log(`  passing ${buckets.length} bucket account(s) as remaining accounts`);
  // Every one of them must be readable, or the allocation sum silently comes up
  // short and finalize rejects with a message about supply rather than lag.
  for (const b of buckets) {
    // eslint-disable-next-line no-await-in-loop
    await awaitAccount(umi, b, 'bucket');
  }
  const sig = await sendChecked(
    finalizeV2(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      baseMint: publicKey(a.baseMint),
      padding: [0, 0, 0, 0, 0, 0, 0],
    }).addRemainingAccounts(
      buckets.map((pubkey) => ({ pubkey, isSigner: false, isWritable: true }))
    ),
    umi,
    'finalizeV2'
  );
  console.log('Finalized. tx:', sig);

  // Wait for the FLAG, not just for the transaction. `finalizeV2` confirming
  // does not mean the next RPC read sees finalized === true, and depositPresaleV2
  // rejects with "The Genesis Account is not finalized" (0x2c) when it does not —
  // which looks exactly like finalize having silently failed. Observed on devnet
  // immediately after a finalize that had, in fact, succeeded.
  const { fetchGenesisAccountV2 } = await import('@metaplex-foundation/genesis');
  for (let i = 0; i < 30; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    const ga = await fetchGenesisAccountV2(umi, publicKey(a.genesisAccount));
    if (ga.finalized) {
      console.log('  confirmed on chain: finalized = true. Deposits are now open.');
      break;
    }
    if (i === 29) {
      throw new Error(
        'finalizeV2 confirmed but the genesis account still reads finalized = false after 30 ' +
        'attempts. Do NOT run finalize again — inspect the account before doing anything else.'
      );
    }
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, 1000));
  }
  saveArtifact(PRESALE_ARTIFACT, { ...a, finalized: true, txs: { ...a.txs, finalize: sig } });
}

// ── claim: claimPresaleV2 ───────────────────────────────────────────────────
async function cmdClaim() {
  const env = loadEnv();
  const umi = makeUmi(env);
  // Every command that opens a connection verifies the cluster by GENESIS HASH.
  // This one did not, and `rpcUrl()`'s `url.includes('mainnet')` was the only
  // thing standing in front of it — a case-sensitive substring test that lets
  // through `https://API.MAINNET-BETA.SOLANA.COM` (DNS is case-insensitive; the
  // test is not), every rebranded endpoint, and any bare IP.
  await assertDevnet(umi);
  const a = requirePresale();
  console.log(`Claiming vested tokens from bucket ${a.bucket}…`);
  // Same shape as the deposit-side wSOL gap, on the base side: claimPresaleV2
  // sends the claimed tokens to the recipient's base-token ATA, and a wallet
  // that has never held the token does not have one. The program rejects the
  // empty address with "The token account is not owned by the SPL Token
  // program". Create it in the same transaction — createAtaIdempotent is
  // idempotent, so repeat claims are unaffected.
  // Two ATAs must exist and neither is ours to assume: the recipient's (a
  // wallet that never held the token), and — far less obviously — the Genesis
  // protocol FEE wallet's ATA for this mint. The SDK hardcodes fee wallet
  // 9kFjQsxtpBsaw8s7aUyiY3wazYDNgFP4Lj5rsBVVF8tb and derives its ATA for the
  // base mint, and for a mint created minutes ago that ATA cannot exist yet.
  //
  // The fee-wallet ATA is why the idempotence has to be real and on-chain
  // rather than a client-side existence check: EVERY claimer builds this same
  // instruction, exactly one of them creates the account, and the rest must
  // sail past it. A read-then-decide would make that a race whose loser's
  // otherwise-valid claim fails.
  const FEE_WALLET = publicKey('9kFjQsxtpBsaw8s7aUyiY3wazYDNgFP4Lj5rsBVVF8tb');
  const builder = createAtaIdempotent(umi, {
    mint: publicKey(a.baseMint),
    owner: umi.identity.publicKey,
  }).add(
    createAtaIdempotent(umi, {
      mint: publicKey(a.baseMint),
      owner: FEE_WALLET,
    })
  ).add(
    claimPresaleV2(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      bucket: publicKey(a.bucket),
      baseMint: publicKey(a.baseMint),
    })
  );
  const sig = await sendChecked(builder, umi, 'claimPresaleV2');
  console.log('Claim confirmed. tx:', sig);
}

// Exported for offline tests. The artifact lifecycle below is a safety control —
// it decides whether a half-created presale can be transacted against — so it
// needs regression coverage, and coverage needs the module to be importable
// without executing a command.
export { requirePresale, persistBaseMintKeypair, readArtifact, saveArtifact, PRESALE_ARTIFACT, arg, hasFlag };


// ── verify ──────────────────────────────────────────────────────────────────
//
// Read every claim the artifact makes back off the chain and check it.
//
// The artifact is a JSON file on somebody's laptop. It records which mint was
// sold, which genesis account holds the raise, and which buckets hold which
// allocations — and nothing has ever confirmed any of it against the chain. It
// is written by the same process that sends the transactions, so it records
// what that process INTENDED. A transaction that landed and did something
// slightly different, a hand-edited file, or a stale artifact from an earlier
// run all produce a document that looks exactly as authoritative.
//
// The audit asks for this specifically: "add a `presale:verify` command that
// checks the artifact's `baseMint` against chain" (B)(5). That single check is
// the sharpest one — the mint actually being sold is the one fact a buyer
// cannot afford to take on trust — but the same argument applies to every other
// field, so they are all checked.
//
// Exits non-zero on the first failed check so this can gate a publish step
// rather than only inform a human reading scrollback.
async function cmdVerify() {
  const cfg = loadConfig();
  const env = loadEnv();
  const umi = makeUmi(env);
  await assertDevnet(umi);
  const a = requirePresale();

  const {
    fetchGenesisAccountV2, fetchPresaleBucketV2, fetchRaydiumCpmmBucketV2,
  } = await import('@metaplex-foundation/genesis');

  const results = [];
  const check = (name, ok, detail) => { results.push({ name, ok, detail }); return ok; };

  console.log('=== presale:verify — artifact vs chain ===');
  console.log('artifact :', PRESALE_ARTIFACT);
  console.log('rpc      :', env.RPC_URL || '(default devnet)');
  console.log();

  // ---- genesis account -----------------------------------------------------
  const ga = await fetchGenesisAccountV2(umi, publicKey(a.genesisAccount));

  // THE check the audit named. If this disagrees, the artifact describes a
  // different sale than the one on chain and every other number is unmoored.
  check('baseMint matches the chain', ga.baseMint.toString() === a.baseMint,
    `artifact ${a.baseMint} / chain ${ga.baseMint.toString()}`);

  // Two separate claims, so two separate checks. "The sale is finalized" is a
  // fact about the chain; "the artifact told the truth about it" is a fact
  // about the file. Collapsing them into one comparison against the chain lets
  // a lying artifact pass whenever the chain happens to agree.
  check('genesis is finalized', ga.finalized === true, `chain finalized=${ga.finalized}`);
  check('artifact agrees about finalization', a.finalized === ga.finalized,
    `artifact ${a.finalized} / chain ${ga.finalized}`);

  // finalizeV2 refuses while any supply is unallocated, so this must hold on a
  // finalized account — but it is the invariant the whole allocation table
  // rests on, and reading it back costs nothing.
  check('entire supply is allocated',
    ga.totalSupplyBaseToken === ga.totalAllocatedSupplyBaseToken,
    `${ga.totalAllocatedSupplyBaseToken} of ${ga.totalSupplyBaseToken}`);

  const expectedSupply = BigInt(cfg.token.totalSupply) * 10n ** BigInt(cfg.token.decimals);
  check('total supply matches the config', ga.totalSupplyBaseToken === expectedSupply,
    `chain ${ga.totalSupplyBaseToken} / config ${expectedSupply}`);

  // ---- presale bucket ------------------------------------------------------
  const pb = await fetchPresaleBucketV2(umi, publicKey(a.bucket));
  const params = derivePresaleParams(cfg);

  check('presale bucket belongs to this genesis',
    pb.bucket.genesis.toString() === a.genesisAccount,
    `bucket.genesis ${pb.bucket.genesis.toString()}`);

  check('presale allocation matches the config',
    pb.bucket.baseTokenAllocation === params.baseTokenAllocation,
    `chain ${pb.bucket.baseTokenAllocation} / config ${params.baseTokenAllocation}`);

  check('hard cap matches the config',
    pb.allocationQuoteTokenCap === params.allocationQuoteTokenCap,
    `chain ${pb.allocationQuoteTokenCap} / config ${params.allocationQuoteTokenCap} lamports`);

  // ---- liquidity bucket ----------------------------------------------------
  if (a.liquidityBucket) {
    const lp = await fetchRaydiumCpmmBucketV2(umi, publicKey(a.liquidityBucket));
    check('liquidity bucket belongs to this genesis',
      lp.bucket.genesis.toString() === a.genesisAccount,
      `bucket.genesis ${lp.bucket.genesis.toString()}`);

    // The never-claim lock is the single most consequential irreversible
    // property of the sale, and it is expressed as the ABSENCE of a claim
    // authority. An absence is exactly the kind of thing that is easy to
    // believe without checking.
    const claimAuth = lp.lpClaimAuthority;
    const locked = claimAuth === null || claimAuth.__option === 'None';
    check('LP is permanently locked (no claim authority)', locked,
      locked ? 'lpClaimAuthority = None' : `lpClaimAuthority = ${JSON.stringify(claimAuth)}`);
  }

  // ---- allocation buckets --------------------------------------------------
  for (const [name, rec] of Object.entries(a.allocationBuckets || {})) {
    const info = await umi.rpc.getAccount(publicKey(rec.address));
    check(`allocation bucket "${name}" exists on chain`, info.exists,
      `${rec.address}`);
  }

  // ---- report --------------------------------------------------------------
  const width = Math.max(...results.map((r) => r.name.length));
  for (const r of results) {
    console.log(`  ${r.ok ? 'ok  ' : 'FAIL'}  ${r.name.padEnd(width)}  ${r.detail}`);
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    console.error(`\nVERIFY FAILED — ${failed.length} claim(s) in ${PRESALE_ARTIFACT} do not match the chain.`);
    process.exit(1);
  }
  console.log('VERIFY OK — every claim in the artifact matches the chain.');
}

const table = {
  plan: cmdPlan,
  verify: cmdVerify,
  whitelist: cmdWhitelist,
  create: cmdCreate,
  liquidity: cmdLiquidity,
  allocate: cmdAllocate,
  finalize: cmdFinalize,
  trigger: cmdTrigger,
  deposit: cmdDeposit,
  claim: cmdClaim,
  withdraw: cmdWithdraw,
  'withdraw-unsold': cmdWithdrawUnsold,
};

// Dispatch only when run as a script. Without this guard, importing the module
// runs whatever happens to be in argv[2] — under `node --test` that is a test
// file path, which falls through to the usage message and exits 2, taking the
// whole test run with it.
const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  const cmd = process.argv[2];
  if (!table[cmd]) {
    console.error(
      'Usage: node genesis_presale.mjs ' +
        '<plan|whitelist|create|liquidity|allocate|finalize|trigger|deposit --amount N|claim|withdraw|withdraw-unsold>'
    );
    process.exit(2);
  }
  await table[cmd]();
}
