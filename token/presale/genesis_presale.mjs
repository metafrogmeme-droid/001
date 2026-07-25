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
  depositPresaleV2,
  claimPresaleV2,
  findGenesisAccountV2Pda,
  findPresaleBucketV2Pda,
} from '@metaplex-foundation/genesis';

import {
  loadConfig,
  loadEnv,
  makeUmi,
  derivePresaleParams,
  fixedPriceSolPerToken,
  fundingModeValue,
  TOKEN_ROOT,
} from './genesis_lib.mjs';

const GENESIS_PROGRAM_ID = 'GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B';
const BUCKET_INDEX = 0;

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
  console.log('\nConditions + claim schedule built via createTimeAbsoluteCondition / createClaimSchedule.');
  console.log('Run `npm run presale:create` to send initializeV2 + addPresaleBucketV2 to devnet.');
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
  const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi);
  const baseMintPk = transferMode ? baseMint : baseMint.publicKey;

  console.log('=== Genesis presale create (DEVNET / DRAFT) ===');
  console.log('Authority/payer :', umi.identity.publicKey);
  console.log('Base mint       :', baseMintPk, transferMode ? '(existing)' : '(new)');

  console.log('\n[1/2] initializeV2 — creating the genesis account…');
  await initializeV2(umi, {
    baseMint,
    authority: umi.identity,
    fundingMode: fundingModeValue(cfg),
    decimals: cfg.token.decimals,
    totalSupplyBaseToken: BigInt(cfg.token.totalSupply) * 10n ** BigInt(cfg.token.decimals),
    name: cfg.token.name,
    symbol: cfg.token.symbol,
    uri: cfg.token.metadataUri,
  }).sendAndConfirm(umi);

  const genesisAccount = findGenesisAccountV2Pda(umi, { baseMint: baseMintPk, genesisIndex: 0 });
  const bucket = findPresaleBucketV2Pda(umi, { genesisAccount: genesisAccount[0], bucketIndex: BUCKET_INDEX });

  console.log('[2/2] addPresaleBucketV2 — configuring the fixed-price presale…');
  await addPresaleBucketV2(umi, {
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
  }).sendAndConfirm(umi);

  const artifact = {
    cluster: env.CLUSTER || 'devnet',
    program: GENESIS_PROGRAM_ID,
    baseMint: baseMintPk.toString(),
    genesisAccount: genesisAccount[0].toString(),
    bucket: bucket[0].toString(),
    hardCapSol: cfg.sale.hardCapSol,
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

  console.log(`Depositing ${amountSol} SOL into presale bucket ${a.bucket}…`);
  await depositPresaleV2(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    bucket: publicKey(a.bucket),
    baseMint: publicKey(a.baseMint),
    amountQuoteToken,
  }).sendAndConfirm(umi);
  console.log('Deposit confirmed.');
}

// ── claim: claimPresaleV2 ───────────────────────────────────────────────────
async function cmdClaim() {
  const env = loadEnv();
  const umi = makeUmi(env);
  const a = readArtifact('presale.devnet.json');
  if (!a) throw new Error('No presale.devnet.json — run `npm run presale:create` first.');
  console.log(`Claiming vested tokens from bucket ${a.bucket}…`);
  await claimPresaleV2(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    bucket: publicKey(a.bucket),
    baseMint: publicKey(a.baseMint),
  }).sendAndConfirm(umi);
  console.log('Claim confirmed.');
}

const cmd = process.argv[2];
const table = { plan: cmdPlan, create: cmdCreate, deposit: cmdDeposit, claim: cmdClaim };
if (!table[cmd]) {
  console.error('Usage: node genesis_presale.mjs <plan|create|deposit --amount N|claim>');
  process.exit(2);
}
await table[cmd]();
