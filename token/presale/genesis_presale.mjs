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

import { generateSigner, publicKey } from '@metaplex-foundation/umi';
import {
  initializeV2,
  addPresaleBucketV2,
  addRaydiumCpmmBucketV2,
  depositPresaleV2,
  claimPresaleV2,
  withdrawPresaleV1,
  withdrawUnsoldPresaleV1,
  findGenesisAccountV2Pda,
  findPresaleBucketV2Pda,
} from '@metaplex-foundation/genesis';

import {
  loadConfig,
  loadEnv,
  makeUmi,
  assertDevnet,
  sendChecked,
  derivePresaleParams,
  fixedPriceSolPerToken,
  fundingModeValue,
  buildAllowlist,
  proofToPublicKeys,
  deriveLiquidityParams,
  findAssociatedTokenPda,
  findPresaleDepositV2Pda,
  TOKEN_ROOT,
} from './genesis_lib.mjs';

const GENESIS_PROGRAM_ID = 'GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B';
const BUCKET_INDEX = 0;
const LIQUIDITY_BUCKET_INDEX = 1;
const ALLOWLIST_ARTIFACT = 'allowlist.devnet.json';

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

function arg(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
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
  console.log('                 ', `NOT WIRED: the ${lp.raisedSolToLiquidityBps / 100}% quote-token split is config-only — no instruction encodes it yet (needs an endBehaviors SendQuoteTokenPercentage on the presale bucket).`);
  console.log('\nConditions/schedules via createTimeAbsoluteCondition / createClaimSchedule / createNeverClaimSchedule.');
  console.log('Flow: presale:whitelist → presale:create → presale:liquidity → presale:deposit → presale:claim.');
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
  const sigInit = await sendChecked(
    initializeV2(umi, {
      baseMint,
      authority: umi.identity,
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
  console.log('  tx:', sigInit);

  const genesisAccount = findGenesisAccountV2Pda(umi, { baseMint: baseMintPk, genesisIndex: 0 });
  const bucket = findPresaleBucketV2Pda(umi, { genesisAccount: genesisAccount[0], bucketIndex: BUCKET_INDEX });

  const presaleInput = {
    genesisAccount: genesisAccount[0],
    baseMint: baseMintPk,
    bucketIndex: BUCKET_INDEX,
    baseTokenAllocation: p.baseTokenAllocation,
    allocationQuoteTokenCap: p.allocationQuoteTokenCap,
    depositStartCondition: p.depositStartCondition,
    depositEndCondition: p.depositEndCondition,
    claimStartCondition: p.claimStartCondition,
    claimEndCondition: p.claimEndCondition,
    // Per-wallet floor/ceiling (OptionOrNullable — raw value is fine).
    minimumDepositAmount: { amount: p.perWalletMinLamports },
    depositLimit: { limit: p.perWalletMaxLamports },
    // 33% at TGE, linear tail.
    claimSchedule: p.claimSchedule,
  };
  if (allowlistArgs) {
    presaleInput.allowlist = allowlistArgs;
    console.log('    whitelist: applying Merkle root', wl.rootHex.slice(0, 16) + '… (ends at publicStart)');
  } else {
    console.log('    whitelist: NONE — this round is open to any wallet.');
  }
  console.log('[2/2] addPresaleBucketV2 — configuring the fixed-price presale…');
  const sigBucket = await sendChecked(
    addPresaleBucketV2(umi, presaleInput), umi, 'addPresaleBucketV2'
  );
  console.log('  tx:', sigBucket);

  // Written only after BOTH sends confirmed error-free — every downstream
  // command treats this artifact as proof the sale exists.
  const artifact = {
    cluster: env.CLUSTER || 'devnet',
    program: GENESIS_PROGRAM_ID,
    baseMint: baseMintPk.toString(),
    genesisAccount: genesisAccount[0].toString(),
    bucket: bucket[0].toString(),
    hardCapSol: cfg.sale.hardCapSol,
    allowlisted: Boolean(allowlistArgs),
    txs: { initialize: sigInit, addPresaleBucket: sigBucket },
    note: 'DRAFT / DEVNET artifact — see docs/TOKEN_ROADMAP.md',
  };
  const out = saveArtifact('presale.devnet.json', artifact);
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
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  const amountSol = Number(arg('--amount') || '0');
  if (!(amountSol > 0)) throw new Error('Provide --amount <SOL> greater than 0.');
  const amountQuoteToken = BigInt(Math.round(amountSol * 1e9));

  const input = {
    genesisAccount: publicKey(a.genesisAccount),
    bucket: publicKey(a.bucket),
    baseMint: publicKey(a.baseMint),
    amountQuoteToken,
  };
  // During the whitelist window the depositor must present their Merkle proof.
  const wl = readArtifact(ALLOWLIST_ARTIFACT);
  if (wl) {
    const me = umi.identity.publicKey.toString();
    const hexProof = wl.proofByAddress?.[me];
    if (!hexProof) {
      throw new Error(`Wallet ${me} is not on the whitelist (no proof). Add it and re-run presale:whitelist, or wait for the public round.`);
    }
    input.proof = proofToPublicKeys(hexProof);
    console.log(`  presenting whitelist proof (${hexProof.length} nodes)`);
  }
  console.log(`Depositing ${amountSol} SOL into presale bucket ${a.bucket}…`);
  const sig = await sendChecked(depositPresaleV2(umi, input), umi, 'depositPresaleV2');
  console.log('Deposit confirmed. tx:', sig);
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
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  const lp = deriveLiquidityParams(cfg);

  // Fail closed on the half of the commitment that no instruction encodes.
  //
  // The token side of this bucket is IRREVOCABLE (never-claim LP lock). The
  // quote side — "N% of the raise goes to the pool" — is currently config text
  // that nothing enforces. Creating the irrevocable half while the enforceable
  // half is merely promised is the wrong order to fail in, so refuse until the
  // routing is either wired up or explicitly acknowledged as manual.
  if (lp.raisedSolToLiquidityBps > 0 && !cfg.liquidity.acknowledgeQuoteSplitIsManual) {
    throw new Error(
      `config.liquidity.raisedSolToLiquidityBps is ${lp.raisedSolToLiquidityBps} ` +
      `(${lp.raisedSolToLiquidityBps / 100}% of the raise), but NO instruction encodes that split — ` +
      'it is an unenforced promise. This command would create a permanent, ' +
      'never-claim LP lock on the token side while the SOL side stays discretionary.\n\n' +
      'Either wire an `endBehaviors: [SendQuoteTokenPercentage{...}]` onto the presale ' +
      'bucket in presale:create, or set liquidity.acknowledgeQuoteSplitIsManual=true to ' +
      'state on the record that the split is operator-executed and unenforceable on-chain.'
    );
  }

  console.log(`Adding Raydium CPMM liquidity bucket (${cfg.liquidity.tokenAllocation} ${cfg.token.symbol}, permanent LP lock)…`);
  if (lp.raisedSolToLiquidityBps > 0) {
    console.log(
      `  NOTE: the ${lp.raisedSolToLiquidityBps / 100}% raise->pool split is operator-executed, ` +
      'NOT enforced on-chain (acknowledged in config).'
    );
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
}

// ── withdraw: depositor cancels their deposit (refund) ──────────────────────
async function cmdWithdraw() {
  const env = loadEnv();
  const umi = makeUmi(env);
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  const bucket = publicKey(a.bucket);
  const mint = publicKey(a.baseMint);
  const me = umi.identity.publicKey;
  const depositPda = findPresaleDepositV2Pda(umi, { bucket, recipient: me });
  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
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
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  const bucket = publicKey(a.bucket);
  const mint = publicKey(a.baseMint);
  const me = umi.identity.publicKey;
  const bucketTokenAccount = findAssociatedTokenPda(umi, { mint, owner: bucket });
  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
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

// ── claim: claimPresaleV2 ───────────────────────────────────────────────────
async function cmdClaim() {
  const env = loadEnv();
  const umi = makeUmi(env);
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  console.log(`Claiming vested tokens from bucket ${a.bucket}…`);
  const sig = await sendChecked(
    claimPresaleV2(umi, {
      genesisAccount: publicKey(a.genesisAccount),
      bucket: publicKey(a.bucket),
      baseMint: publicKey(a.baseMint),
    }),
    umi,
    'claimPresaleV2'
  );
  console.log('Claim confirmed. tx:', sig);
}

const cmd = process.argv[2];
const table = {
  plan: cmdPlan,
  whitelist: cmdWhitelist,
  create: cmdCreate,
  liquidity: cmdLiquidity,
  deposit: cmdDeposit,
  claim: cmdClaim,
  withdraw: cmdWithdraw,
  'withdraw-unsold': cmdWithdrawUnsold,
};
if (!table[cmd]) {
  console.error(
    'Usage: node genesis_presale.mjs ' +
      '<plan|whitelist|create|liquidity|deposit --amount N|claim|withdraw|withdraw-unsold>'
  );
  process.exit(2);
}
await table[cmd]();
