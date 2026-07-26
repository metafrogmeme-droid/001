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
import { base58, keypairIdentity, sol, lamports, publicKey } from '@metaplex-foundation/umi';
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

// Cluster identity. A genesis hash cannot be spoofed by a URL that merely omits
// the word "mainnet"; see assertDevnet below.
export const MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d';
export const DEVNET_GENESIS = 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';
export const TESTNET_GENESIS = '4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY';

export function loadConfig() {
  // GENESIS_CONFIG lets the e2e dry-run harness point the presale commands at a
  // generated near-now config without editing the committed one. Absolute path
  // or relative to the token/ root; defaults to the committed config.
  const override = process.env.GENESIS_CONFIG;
  const cfgPath = override
    ? (path.isAbsolute(override) ? override : path.join(TOKEN_ROOT, override))
    : path.join(PRESALE_DIR, 'metaplex-genesis.config.json');
  return JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
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

/**
 * Prove which chain we are on before anything is signed.
 *
 * `rpcUrl`'s substring test is a textual heuristic that a rebranded or private
 * mainnet endpoint defeats. The genesis hash is the chain's identity, so this is
 * the check that actually holds — and it fails CLOSED on anything unrecognised.
 */
export async function assertDevnet(umi) {
  const genesisHash = await umi.rpc.getGenesisHash();
  if (genesisHash === MAINNET_GENESIS) {
    throw new Error(
      `Refusing to run against mainnet-beta (genesis ${genesisHash}). Draft/devnet tooling — ` +
        'a real launch is gated behind legal review + audit (docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  if (![DEVNET_GENESIS, TESTNET_GENESIS].includes(genesisHash)) {
    throw new Error(`Refusing unrecognized cluster (genesis ${genesisHash}). Expected devnet.`);
  }
  return genesisHash;
}

/**
 * Send a built transaction and fail loudly if it landed with an on-chain error.
 *
 * `sendAndConfirm` resolves for a transaction that was *included* — including
 * one that reverted. Discarding its result and printing "confirmed" reports
 * success for a sale that did not happen, which is exactly what every command
 * here used to do.
 */
export async function sendChecked(builder, umi, label) {
  const { signature, result } = await builder.sendAndConfirm(umi);
  const sig = base58.deserialize(signature)[0];
  if (result && result.value && result.value.err) {
    throw new Error(
      `${label} FAILED on-chain: ${JSON.stringify(result.value.err)} (sig ${sig})`
    );
  }
  return sig;
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

  // Deposits open at the WHITELIST start (when one is configured) so the
  // allowlist window [whitelistStart, publicStart) is actually inside the
  // deposit window. Opening at publicStart — which is exactly when the
  // allowlist expires — would make the whitelist round zero-length.
  const hasWhitelist = Array.isArray(cfg.whitelist) && cfg.whitelist.length > 0;
  const depositStart = unix(hasWhitelist && t.whitelistStart ? t.whitelistStart : t.publicStart);
  const depositEnd = unix(t.depositEnd);
  const tge = unix(t.tge);
  const claimEnd = unix(t.claimEnd);

  const vestingPeriod = BigInt(cfg.vesting.vestingPeriodSeconds);
  // Linear tail: full unlock `linearMonthsAfterTge` after TGE (~30d/month).
  const vestingEnd = tge + BigInt(cfg.vesting.linearMonthsAfterTge) * 30n * 86400n;

  // Ordering + economic invariants. Every value below becomes an IMMUTABLE
  // on-chain condition, so a transposed date has to fail here — loudly and
  // offline via `presale:plan` — rather than at addPresaleBucketV2 or, worse,
  // silently in a sale that opens in the past and never closes.
  const req = (ok, msg) => {
    if (!ok) throw new Error(`Invalid presale config: ${msg}`);
  };
  req(depositStart < depositEnd,
      `depositStart (${depositStart}) must precede depositEnd (${depositEnd})`);
  req(depositEnd <= tge,
      `depositEnd (${depositEnd}) must not follow tge (${tge}) — the claim schedule is ` +
      'absolute-time and would unlock unevenly across depositors');
  req(tge < claimEnd, `tge (${tge}) must precede claimEnd (${claimEnd})`);
  req(vestingEnd <= claimEnd,
      `vesting ends (${vestingEnd}) after claimEnd (${claimEnd}) — the tail would be unclaimable`);
  if (hasWhitelist && t.whitelistStart) {
    req(unix(t.whitelistStart) < unix(t.publicStart),
        'whitelistStart must precede publicStart or the allowlist round is zero-length');
  }

  const softCap = solToLamports(cfg.sale.softCapSol);
  const hardCap = solToLamports(cfg.sale.hardCapSol);
  const perMin = solToLamports(cfg.sale.minContributionSol);
  const perMax = solToLamports(cfg.sale.maxContributionSol);
  req(hardCap > 0n, 'hardCapSol must be greater than zero (it sets the fixed price)');
  req(softCap <= hardCap, `softCapSol (${softCap}) must not exceed hardCapSol (${hardCap})`);
  req(perMin <= perMax,
      `minContributionSol (${perMin}) must not exceed maxContributionSol (${perMax})`);
  req(perMax <= hardCap, `maxContributionSol (${perMax}) must not exceed the hard cap (${hardCap})`);
  req(BigInt(cfg.sale.presaleAllocation) > 0n, 'presaleAllocation must be greater than zero');
  req(cfg.vesting.cliffAmountBps >= 0 && cfg.vesting.cliffAmountBps <= 10_000,
      `cliffAmountBps (${cfg.vesting.cliffAmountBps}) must be within 0..10000`);

  // A soft cap that no instruction enforces must not ship as if it were a
  // guarantee. Nothing here reaches an on-chain account, so a config promising
  // refunds is promising something the program cannot do.
  if (cfg.sale.refundIfSoftCapMissed) {
    throw new Error(
      'Invalid presale config: refundIfSoftCapMissed=true, but no soft-cap or refund ' +
      'instruction is wired — softCapLamports reaches no on-chain account, so the ' +
      'refund is an unenforceable promise. Either implement it (a min-raise endBehaviour ' +
      'on the presale bucket) or set refundIfSoftCapMissed=false and describe the ' +
      'operational procedure in RUNBOOK.md.'
    );
  }

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

/**
 * The LP base-token allocation that opens the pool at EXACTLY the presale price
 * for a given realised raise. Returns base units.
 *
 * This is the answer to the soft-cap spread. The pool's opening price is
 * (quote received) / (base allocated). The quote side scales with the raise
 * automatically — `SendQuoteTokenPercentage` sends a percentage — while the base
 * side is a constant fixed at bucket creation, so the pool price scales linearly
 * with the raise while the presale price does not. At the 1,000 SOL soft cap
 * that lands the pool ~5x below the price presale buyers paid.
 *
 * Scaling the base side by the same proportion cancels it exactly:
 *
 *     lpBaseUnits = (raised * bps/10000) / presalePricePerBaseUnit
 *                 = raised * bps * presaleAllocationBaseUnits
 *                   ------------------------------------------
 *                        10000 * hardCapLamports
 *
 * which is independent of `raised` in the ratio, so the pool opens at the
 * presale price for ANY raise between the soft and hard cap.
 *
 * All BigInt: these become an immutable on-chain allocation, and float rounding
 * at 10^17 base units is not a rounding error, it is millions of tokens.
 */
export function parityLpBaseUnitsForRaise(cfg, raisedLamports) {
  const raised = BigInt(raisedLamports);
  if (raised < 0n) throw new Error(`raisedLamports must not be negative (got ${raised})`);
  const bps = BigInt(cfg.liquidity.raisedSolToLiquidityBps ?? 0);
  if (bps <= 0n) {
    throw new Error(
      'liquidity.raisedSolToLiquidityBps is 0 — no quote is routed to the pool, so there is ' +
      'no price to reach parity with.'
    );
  }
  const decimals = BigInt(cfg.token.decimals);
  const presaleAllocationBaseUnits = BigInt(cfg.sale.presaleAllocation) * 10n ** decimals;
  const hardCapLamports = BigInt(cfg.sale.hardCapSol) * 1_000_000_000n;
  if (hardCapLamports <= 0n) throw new Error('sale.hardCapSol must be positive');

  // Derive from the quote that will ACTUALLY reach the pool, not from the raw
  // raise. `SendQuoteTokenPercentage` moves floor(raised * bps / 10000), and
  // computing the token side off the unfloored figure makes the two sides round
  // in opposite directions: the pool would receive marginally less SOL than the
  // allocation was priced for, which opens it BELOW the presale price. A test
  // over small raises caught exactly that — at 1 lamport the quote share floors
  // to 0 while the token side stayed positive, pricing the pool at zero.
  const quoteToPool = (raised * bps) / 10_000n;

  // Floor again: fewer tokens against the same quote opens the pool a hair
  // ABOVE the presale price. Both roundings now favour the buyer, which is the
  // only direction that is safe against a permanent LP lock.
  return (quoteToPool * presaleAllocationBaseUnits) / hardCapLamports;
}

/**
 * What to actually write on-chain for a realised raise, with the safety clamp.
 *
 * Never allocates MORE than the bucket was created with. Two reasons: the extra
 * tokens may simply not be there, and "the operator raised the LP allocation" is
 * not a parity fix — it is a different, unreviewed decision. Clamping down is
 * always safe because fewer tokens against the same quote means a HIGHER opening
 * price, never a lower one.
 */
export function rebalancedLpAllocation(cfg, raisedLamports) {
  const decimals = BigInt(cfg.token.decimals);
  const configured = BigInt(cfg.liquidity.tokenAllocation) * 10n ** decimals;
  const parity = parityLpBaseUnitsForRaise(cfg, raisedLamports);
  const clamped = parity > configured;
  return {
    allocation: clamped ? configured : parity,
    parity,
    configured,
    clamped,
  };
}

export function fundingModeValue(cfg) {
  const fm = cfg.fundingMode || {};
  return (fm.mode === 'transfer') ? (fm.transferValue ?? 1) : (fm.mintValue ?? 0);
}

// ── Whitelist (Merkle allowlist) ────────────────────────────────────────────

const toHex = (u8) => Buffer.from(u8).toString('hex');
const fromHex = (hex) => Uint8Array.from(Buffer.from(hex, 'hex'));

/** Required u8[6] `padding` field of AllowlistInitArgs (no serializer default). */
const ALLOWLIST_PADDING = [0, 0, 0, 0, 0, 0];

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
      // `padding` is a required fixed-size u8[6] in AllowlistInitArgs with no
      // kinobi default; omitting it makes the umi array serializer throw on
      // `value.length` before any transaction is built.
      padding: ALLOWLIST_PADDING,
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
    padding: ALLOWLIST_PADDING,
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

  // The LP token allocation is FIXED while the SOL side scales with whatever is
  // actually raised, so the pool's opening price is a function of the raise.
  // If the raise lands near the soft cap, the pool can open BELOW the presale
  // price — every presale buyer is underwater the moment trading starts, and
  // the LP lock is permanent so nothing can be corrected afterwards. Compute
  // both ends of the range so the mismatch is visible rather than discovered.
  const presalePrice = fixedPriceSolPerToken(cfg);
  const bps = Number(cfg.liquidity.raisedSolToLiquidityBps);
  const lpTokens = Number(cfg.liquidity.tokenAllocation);
  const poolPriceAt = (raisedSol) => ((bps / 10_000) * Number(raisedSol)) / lpTokens;
  const worstCasePoolPrice = poolPriceAt(cfg.sale.softCapSol);
  const bestCasePoolPrice = poolPriceAt(cfg.sale.hardCapSol);

  // Best case below the presale price is unambiguously broken: there is no
  // outcome in which buyers are whole. Parity is reached when the pool receives
  // the same PROPORTION of tokens as of SOL, i.e.
  //   tokenAllocation <= (bps/10000) * presaleAllocation
  if (bestCasePoolPrice < presalePrice) {
    const parity = (bps / 10_000) * Number(cfg.sale.presaleAllocation);
    throw new Error(
      `Invalid liquidity config: even a FULL raise opens the pool at ` +
      `${bestCasePoolPrice.toExponential(6)} SOL/token, below the presale price ` +
      `${presalePrice.toExponential(6)}. Every buyer would be underwater at listing and the ` +
      `LP lock is permanent. The pool must receive at most the same proportion of tokens as ` +
      `of SOL: set liquidity.tokenAllocation <= ${parity.toLocaleString()} ` +
      `(= ${bps / 100}% of sale.presaleAllocation), or raise liquidity.raisedSolToLiquidityBps.`
    );
  }

  // Worst case below the presale price is a real risk rather than a bug — it is
  // inherent to a fixed token allocation paired with a soft/hard cap spread —
  // so it warns rather than blocks. An operator should see it before committing
  // to an irreversible lock.
  if (worstCasePoolPrice < presalePrice) {
    const ratio = (presalePrice / worstCasePoolPrice).toFixed(1);
    console.warn(
      `WARNING: at the SOFT cap the pool opens at ${worstCasePoolPrice.toExponential(6)} ` +
      `SOL/token — ${ratio}x below the presale price ${presalePrice.toExponential(6)}. ` +
      `Presale buyers would be underwater at listing on a weak raise, and the LP lock is ` +
      `permanent. This is inherent to a fixed tokenAllocation across a ` +
      `${(Number(cfg.sale.hardCapSol) / Number(cfg.sale.softCapSol)).toFixed(1)}x cap spread; ` +
      `narrowing the spread or scaling the allocation with the raise is the only real fix.`
    );
  }

  return {
    baseTokenAllocation: BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(decimals),
    lpLockSchedule: createNeverClaimSchedule(),
    startCondition: createTimeAbsoluteCondition(unix(cfg.timeline.depositEnd)),
    raisedSolToLiquidityBps: cfg.liquidity.raisedSolToLiquidityBps,
    // Surfaced so `presale:plan` can show the range an operator is committing to.
    _pricing: { presalePrice, worstCasePoolPrice, bestCasePoolPrice },
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
