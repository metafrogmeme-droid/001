// Shared helpers for the Metaplex Genesis presale integration. DRAFT / DEVNET.
//
// Wraps the real @metaplex-foundation/genesis SDK (built on Umi). Loads the
// presale config, sets up a Umi instance with the genesis plugin, and derives
// the exact on-chain parameters (lamport caps, unix-second time conditions,
// base-unit allocations, vesting claim schedule) from the human-readable JSON.
//
// It refuses mainnet — this is devnet draft tooling. See docs/TOKEN_ROADMAP.md.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { keypairIdentity, sol, lamports, publicKey } from '@metaplex-foundation/umi';
import { findAssociatedTokenPda } from '@metaplex-foundation/mpl-toolbox';
import {
  genesis,
  WRAPPED_SOL_MINT,
  createTimeAbsoluteCondition,
  createClaimSchedule,
  createNeverClaimSchedule,
  prepareAllowlist,
  findPresaleDepositV2Pda,
} from '@metaplex-foundation/genesis';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const PRESALE_DIR = __dirname;
export const TOKEN_ROOT = path.resolve(__dirname, '..');

const DEVNET_RPC = 'https://api.devnet.solana.com';

export function loadConfig() {
  const raw = fs.readFileSync(path.join(PRESALE_DIR, 'metaplex-genesis.config.json'), 'utf8');
  return JSON.parse(raw);
}

// Minimal .env reader (no runtime dependency) — mirrors token/scripts/lib.mjs.
export function loadEnv() {
  const envPath = path.join(TOKEN_ROOT, '.env');
  const env = { ...process.env };
  if (fs.existsSync(envPath)) {
    for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && env[m[1]] === undefined) env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
  return env;
}

function rpcUrl(env) {
  const url = env.RPC_URL || DEVNET_RPC;
  if (url.includes('mainnet')) {
    throw new Error(
      'Refusing mainnet. This is draft/devnet tooling — a real launch is gated behind ' +
        'legal review + audit (docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  return url;
}

function loadKeypair(umi, env) {
  const kpPath = env.KEYPAIR_PATH || './.keys/mint-payer.json';
  const abs = path.isAbsolute(kpPath) ? kpPath : path.join(TOKEN_ROOT, kpPath);
  if (!fs.existsSync(abs)) {
    throw new Error(`Keypair not found at ${abs}. Run \`npm run keygen\` or set KEYPAIR_PATH.`);
  }
  const secret = Uint8Array.from(JSON.parse(fs.readFileSync(abs, 'utf8')));
  return umi.eddsa.createKeypairFromSecretKey(secret);
}

/** Build a Umi instance with the genesis plugin and the devnet payer identity. */
export function makeUmi(env = loadEnv()) {
  const umi = createUmi(rpcUrl(env)).use(genesis());
  const kp = loadKeypair(umi, env);
  umi.use(keypairIdentity(kp));
  return umi;
}

// ── Derivations: human config → exact on-chain params ───────────────────────

const LAMPORTS_PER_SOL = 1_000_000_000n;

function unix(rfc3339) {
  // Deterministic: parse an explicit RFC3339 string (no Date.now()).
  const ms = Date.parse(rfc3339);
  if (Number.isNaN(ms)) throw new Error(`Bad timestamp in config: ${rfc3339}`);
  return BigInt(Math.floor(ms / 1000));
}

function solToLamports(amountSol) {
  // amountSol may be fractional (e.g. 0.25); scale via a fixed-point conversion.
  return BigInt(Math.round(Number(amountSol) * 1e9));
}

function baseUnits(whole, decimals) {
  return BigInt(whole) * 10n ** BigInt(decimals);
}

/**
 * Derive every on-chain presale parameter from the config. Pure/offline — used
 * by both the `plan` (dry-run) and `create` commands so what you preview is
 * exactly what gets sent.
 */
export function derivePresaleParams(cfg) {
  const decimals = cfg.token.decimals;
  const t = cfg.timeline;

  const depositStart = unix(t.publicStart);
  const depositEnd = unix(t.depositEnd);
  const tge = unix(t.tge);
  const claimEnd = unix(t.claimEnd);

  const vestingPeriod = BigInt(cfg.vesting.vestingPeriodSeconds);
  // Linear tail: full unlock `linearMonthsAfterTge` after TGE (~30d/month).
  const vestingEnd = tge + BigInt(cfg.vesting.linearMonthsAfterTge) * 30n * 86400n;

  return {
    decimals,
    baseTokenAllocation: baseUnits(cfg.sale.presaleAllocation, decimals),
    // Fixed price is set by (allocation / cap): cap == hard cap in lamports.
    allocationQuoteTokenCap: solToLamports(cfg.sale.hardCapSol),
    softCapLamports: solToLamports(cfg.sale.softCapSol),
    perWalletMinLamports: solToLamports(cfg.sale.minContributionSol),
    perWalletMaxLamports: solToLamports(cfg.sale.maxContributionSol),
    // Time conditions (TimeAbsolute) for the deposit and claim windows.
    depositStartCondition: createTimeAbsoluteCondition(depositStart),
    depositEndCondition: createTimeAbsoluteCondition(depositEnd),
    claimStartCondition: createTimeAbsoluteCondition(tge),
    claimEndCondition: createTimeAbsoluteCondition(claimEnd),
    // Vesting: cliff at TGE (cliffAmountBps unlocked), linear to vestingEnd.
    claimSchedule: createClaimSchedule({
      startTime: tge,
      endTime: vestingEnd,
      cliffTime: tge,
      cliffAmountBps: cfg.vesting.cliffAmountBps,
      period: vestingPeriod,
    }),
    // Raw unix for display.
    _times: { depositStart, depositEnd, tge, vestingEnd, claimEnd },
  };
}

/** Effective fixed price in SOL per token (allocation / cap), for display. */
export function fixedPriceSolPerToken(cfg) {
  return Number(cfg.sale.hardCapSol) / Number(cfg.sale.presaleAllocation);
}

export function fundingModeValue(cfg) {
  const fm = cfg.fundingMode || {};
  return (fm.mode === 'transfer') ? (fm.transferValue ?? 1) : (fm.mintValue ?? 0);
}

// ── Whitelist (Merkle allowlist) ────────────────────────────────────────────

const toHex = (u8) => Buffer.from(u8).toString('hex');
const fromHex = (hex) => Uint8Array.from(Buffer.from(hex, 'hex'));

/**
 * Build a Merkle allowlist from a list of base58 addresses. Returns the tree
 * root, per-address proofs, height, and the `AllowlistInitArgs` to hand to
 * `addPresaleBucketV2`. The whitelist round ends at `timeline.publicStart`
 * (after which anyone may deposit), and is capped at the hard cap.
 */
export function buildAllowlist(cfg, addresses) {
  const members = addresses.map((a) => ({ address: publicKey(a) }));
  const { root, proofs, treeHeight } = prepareAllowlist(members);
  const endTime = unix(cfg.timeline.publicStart);
  const quoteCap = solToLamports(cfg.sale.hardCapSol);
  // Map address -> hex-encoded proof nodes (stable JSON for the artifact).
  const proofByAddress = {};
  addresses.forEach((a, i) => {
    proofByAddress[a] = (proofs[i] || []).map(toHex);
  });
  return {
    rootHex: toHex(root),
    treeHeight,
    proofByAddress,
    initArgs: {
      enabled: true,
      merkleTreeHeight: treeHeight,
      merkleRoot: root,
      endTime,
      quoteCap,
    },
  };
}

/** Rebuild the AllowlistInitArgs from a saved allowlist artifact (hex root). */
export function allowlistInitArgsFromArtifact(cfg, artifact) {
  return {
    enabled: true,
    merkleTreeHeight: artifact.treeHeight,
    merkleRoot: fromHex(artifact.rootHex),
    endTime: unix(cfg.timeline.publicStart),
    quoteCap: solToLamports(cfg.sale.hardCapSol),
  };
}

/** Convert a saved hex proof (array of 32-byte nodes) to umi PublicKey[]. */
export function proofToPublicKeys(hexNodes) {
  return (hexNodes || []).map((h) => publicKey(fromHex(h)));
}

// ── Raydium liquidity bucket (permanent LP lock) ────────────────────────────

/**
 * Derive params for `addRaydiumCpmmBucketV2`: the LP token allocation, a
 * permanent LP lock (never-claim schedule), and a start condition at the end
 * of the deposit window (pool created once the presale closes).
 */
export function deriveLiquidityParams(cfg) {
  const decimals = cfg.token.decimals;
  return {
    baseTokenAllocation: BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(decimals),
    lpLockSchedule: createNeverClaimSchedule(),
    startCondition: createTimeAbsoluteCondition(unix(cfg.timeline.depositEnd)),
    raisedSolToLiquidityBps: cfg.liquidity.raisedSolToLiquidityBps,
  };
}

export {
  WRAPPED_SOL_MINT,
  sol,
  lamports,
  publicKey,
  findAssociatedTokenPda,
  findPresaleDepositV2Pda,
};
