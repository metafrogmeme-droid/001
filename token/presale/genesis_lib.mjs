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
import { base58, keypairIdentity, sol, lamports, publicKey, transactionBuilder } from '@metaplex-foundation/umi';
import { findAssociatedTokenPda, mplToolbox } from '@metaplex-foundation/mpl-toolbox';
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

export function rpcUrl(env) {
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

/**
 * Build a Umi instance with the genesis plugin and the devnet payer identity.
 *
 * `mplToolbox()` is not optional and its absence is not cosmetic. Umi resolves
 * program addresses through a repository that plugins register into, and
 * `initializeV2` internally calls `findAssociatedTokenPda`, which looks up
 * `splAssociatedToken`. With only `genesis()` registered that lookup throws:
 *
 *     ProgramNotRecognizedError: The provided program name [splAssociatedToken]
 *     is not recognized in the [devnet] cluster.
 *
 * so `presale:create` failed on its very first instruction and no presale could
 * ever have been created. This was invisible to every check in the repo —
 * `presale:plan` is pure derivation and never builds a Umi, and the offline
 * tests exercise the serializers directly — and it surfaced the moment the
 * first real transaction was attempted against devnet.
 */
export function makeUmi(env = loadEnv()) {
  const umi = createUmi(rpcUrl(env)).use(mplToolbox()).use(genesis());
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
/**
 * True only for a loopback RPC endpoint — i.e. a validator on this machine.
 *
 * Parsed with the URL API rather than a substring test, because that is the
 * mistake F-19 was about: "localhost" appearing anywhere in a string is not the
 * same as the host being localhost, and `https://localhost.evil.com` would pass
 * a naive check.
 */
export function isLoopbackRpc(url) {
  let host;
  try {
    ({ hostname: host } = new URL(url));
  } catch {
    return false;
  }
  return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
}

export async function assertDevnet(umi, rpcEndpoint = undefined) {
  const genesisHash = await umi.rpc.getGenesisHash();

  // Mainnet is refused FIRST and unconditionally, before any other reasoning.
  // A local validator can be pointed anywhere, and someone could tunnel
  // 127.0.0.1:8899 to a real mainnet RPC — so being on loopback must never be a
  // reason to skip this.
  if (genesisHash === MAINNET_GENESIS) {
    throw new Error(
      `Refusing to run against mainnet-beta (genesis ${genesisHash}). Draft/devnet tooling — ` +
        'a real launch is gated behind legal review + audit (docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  if ([DEVNET_GENESIS, TESTNET_GENESIS].includes(genesisHash)) return genesisHash;

  // A local test validator generates a FRESH genesis hash on every --reset, so
  // it can never be allowlisted by value. Identify it structurally instead: an
  // unrecognised chain reached over loopback is a validator running on this
  // machine, which by construction holds nothing of value.
  //
  // This exists because devnet SOL is faucet-rate-limited to 10 per 8 hours,
  // which made every verification run a scarce resource. A local validator with
  // the Genesis program cloned from devnet costs nothing and can be reset
  // freely:
  //
  //   solana-test-validator --reset --url https://api.devnet.solana.com \
  //     --clone-upgradeable-program GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B
  //
  // The narrowing is deliberate: unknown genesis + loopback = local. Unknown
  // genesis + any remote host is still refused, so a private RPC that merely
  // omits the word "mainnet" gains nothing.
  const endpoint = rpcEndpoint ?? umi.rpc.getEndpoint?.() ?? '';
  if (isLoopbackRpc(endpoint)) {
    console.warn(
      `NOTE: unrecognised genesis ${genesisHash} over loopback (${endpoint}) — treating this as ` +
      'a LOCAL test validator. Nothing here holds value. Mainnet is still refused by hash.'
    );
    return genesisHash;
  }

  throw new Error(
    `Refusing unrecognized cluster (genesis ${genesisHash}) at ${endpoint || 'unknown endpoint'}. ` +
    'Expected devnet, testnet, or a local validator on loopback.'
  );
}

export const SPL_ASSOCIATED_TOKEN_PROGRAM_ID = publicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL');
export const SPL_TOKEN_PROGRAM_ID = publicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
export const SPL_SYSTEM_PROGRAM_ID = publicKey('11111111111111111111111111111111');

/**
 * Create an associated token account, doing nothing if it already exists.
 *
 * This is the SPL Associated Token program's own `CreateIdempotent` — a bare
 * instruction built here rather than taken from a library, for two reasons that
 * both came out of running the presale rather than reading it.
 *
 * 1. It replaces `createTokenIfMissing` from `@metaplex-foundation/mpl-toolbox`,
 *    which the deposit, trigger and claim paths all used. That helper does not
 *    talk to the ATA program directly — it invokes MPL Token Extras
 *    (`TokExjvjJmhKaRBShsBAsbSvEWMA1AgUNK7ps4SAc2p`), a separate upgradeable
 *    program, which then CPIs into the ATA program. So every RCLAW deposit and
 *    every claim was routed through third-party bytecode that the sale does not
 *    need and whose upgrade authority we do not hold. It is deployed on
 *    mainnet, devnet and testnet, so this was never going to fail outright —
 *    which is exactly why it went unnoticed. It surfaced only when the presale
 *    was replayed against a local validator with just the programs the sale
 *    genuinely requires cloned into it, where the deposit died with
 *    `ProgramAccountNotFound` on an address nothing in this repo mentions.
 *
 * 2. mpl-toolbox's own `createIdempotentAssociatedToken` is not a usable
 *    substitute. Its generated code emits EMPTY instruction data, and the ATA
 *    program reads empty data as the legacy `Create` discriminant — the
 *    on-chain log says `Program log: Create`, so the "idempotent" instruction
 *    is the non-idempotent one and a second call fails with
 *    `AccountAlreadyInUse`. It also never derives a default for `ata` (unlike
 *    its non-idempotent sibling), so omitting that input substitutes the
 *    program id into the account slot and the instruction fails `InvalidSeeds`
 *    before it can do anything at all. Both were reproduced on a validator; see
 *    ata_idempotent.test.mjs, which pins the discriminant byte.
 *
 * Idempotence matters beyond tidiness: the claim path must create the Genesis
 * protocol fee wallet's ATA, which the FIRST claimer pays for and every later
 * claimer would otherwise collide with. Doing it with a client-side "does it
 * exist?" check instead would reintroduce that race.
 *
 * `tokenProgram` defaults to the LEGACY SPL Token program, not Token-2022, and
 * that is deliberate rather than an oversight: Genesis mints the base token
 * under legacy SPL Token and its own instructions pass that program, so an ATA
 * derived under a different one would simply not be the account the program
 * looks for. The default matches what `createTokenIfMissing` did here before,
 * so this swap changes no addresses — verified by replaying the presale.
 *
 * @param {import('@metaplex-foundation/umi').Umi} umi
 * @param {{mint: import('@metaplex-foundation/umi').PublicKey,
 *          owner: import('@metaplex-foundation/umi').PublicKey,
 *          tokenProgram?: import('@metaplex-foundation/umi').PublicKey}} input
 */
export function createAtaIdempotent(umi, { mint, owner, tokenProgram = SPL_TOKEN_PROGRAM_ID }) {
  const ata = findAssociatedTokenPda(umi, { mint, owner, tokenProgramId: tokenProgram })[0];
  const keys = [
    { pubkey: umi.payer.publicKey, isSigner: true, isWritable: true },
    { pubkey: ata, isSigner: false, isWritable: true },
    { pubkey: owner, isSigner: false, isWritable: false },
    { pubkey: mint, isSigner: false, isWritable: false },
    { pubkey: SPL_SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: tokenProgram, isSigner: false, isWritable: false },
  ];
  return transactionBuilder([
    {
      instruction: {
        keys,
        programId: SPL_ASSOCIATED_TOKEN_PROGRAM_ID,
        // 1 = CreateIdempotent. 0 (or empty data) is Create, which throws
        // AccountAlreadyInUse on an existing account. This single byte is the
        // whole difference and ata_idempotent.test.mjs mutates it to prove so.
        data: new Uint8Array([1]),
      },
      signers: [umi.payer],
      bytesCreatedOnChain: 0,
    },
  ]);
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


/**
 * Block until `address` is actually readable, then return.
 *
 * A multi-transaction sequence is not safe just because each send confirms.
 * `sendAndConfirm` returning means the transaction reached the confirmed
 * commitment level; the NEXT instruction is simulated by the RPC against
 * whatever slot that node has, which can still be behind. When it is, the
 * program sees an account that does not exist yet and rejects with something
 * that reads like a logic error rather than a race:
 *
 *     Program log: AddPresaleBucketV2Base
 *     Program log: The Genesis Account is invalid   (custom program error 0x2f)
 *
 * That is exactly what happened on devnet — the same code path succeeded on one
 * run and failed on the next two with no change to the arguments. Polling for
 * readability turns an intermittent, misleading failure into a bounded wait.
 */
export async function awaitAccount(umi, address, label, { tries = 30, delayMs = 1000 } = {}) {
  for (let i = 0; i < tries; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    const exists = await umi.rpc.accountExists(publicKey(address));
    if (exists) return true;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error(
    `${label} (${address}) was still not readable after ${tries} attempts. The transaction that ` +
    'created it confirmed, so this is an RPC lag or a wrong derivation — do NOT retry the ' +
    'sequence blindly, it would create a second launch.'
  );
}


/**
 * Derive the non-presale, non-liquidity allocation buckets, and prove the whole
 * supply is accounted for BEFORE anything is sent.
 *
 * `finalizeV2` refuses unless every base token is allocated across buckets:
 *
 *     Program log: Total supply must be fully allocated before finalize
 *
 * and finalize is the step that opens deposits, so a launch that is short by one
 * token is fully built and permanently unopenable. Worse, it fails at the END of
 * the sequence, after the genesis account, the presale bucket and the
 * irreversible LP lock already exist. Checking the arithmetic offline turns that
 * into a config error nobody pays for.
 */
export function deriveAllocationBuckets(cfg) {
  const decimals = BigInt(cfg.token.decimals);
  const scale = 10n ** decimals;
  const totalSupply = BigInt(cfg.token.totalSupply) * scale;
  const presale = BigInt(cfg.sale.presaleAllocation) * scale;
  const liquidity = BigInt(cfg.liquidity.tokenAllocation) * scale;

  const raw = (cfg.allocations && cfg.allocations.buckets) || [];
  const buckets = raw.map((b, i) => {
    if (!b.name) throw new Error(`allocations.buckets[${i}] has no name`);
    const tokens = BigInt(b.tokens);
    if (tokens <= 0n) {
      throw new Error(`allocation "${b.name}" must be a positive token amount (got ${b.tokens})`);
    }
    const unlockAt = unix(b.unlockAt);
    return {
      name: b.name,
      recipient: b.recipient || null,
      baseTokenAllocation: tokens * scale,
      claimStartCondition: createTimeAbsoluteCondition(unlockAt),
      // Open-ended: an unlocked bucket with a claim window that closes would
      // strand its allocation if nobody claimed in time. 100 years out is
      // "never" for this purpose and keeps the condition type uniform.
      claimEndCondition: createTimeAbsoluteCondition(unlockAt + 100n * 365n * 24n * 3600n),
      _unlockAt: unlockAt,
      _tokens: tokens,
    };
  });

  const allocated = buckets.reduce((a, b) => a + b.baseTokenAllocation, presale + liquidity);
  if (allocated !== totalSupply) {
    const short = totalSupply - allocated;
    const asTokens = (v) => (v / scale).toLocaleString();
    throw new Error(
      `Supply is not fully allocated: presale ${asTokens(presale)} + liquidity ` +
      `${asTokens(liquidity)} + ${buckets.length} allocation bucket(s) ` +
      `${asTokens(allocated - presale - liquidity)} = ${asTokens(allocated)}, but token.totalSupply ` +
      `is ${asTokens(totalSupply)} (${short > 0n ? 'short by' : 'over by'} ` +
      `${asTokens(short > 0n ? short : -short)}).\n` +
      'finalizeV2 rejects this with "Total supply must be fully allocated before finalize", and ' +
      'it fails AFTER the genesis account, the presale bucket and the permanent LP lock already ' +
      'exist. Fix allocations.buckets in the config before creating anything.'
    );
  }

  return { buckets, totalSupply, presale, liquidity, allocated };
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
  // The allocation that opens the pool exactly at the presale price on the
  // WEAKEST permitted raise. Sizing at or below this is the only way to be safe
  // at every raise, because the number is frozen before the raise is known.
  const softCapParityTokens = Number(
    parityLpBaseUnitsForRaise(cfg, BigInt(cfg.sale.softCapSol) * 1_000_000_000n) /
      10n ** BigInt(cfg.token.decimals)
  );
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
      `\nWARNING: liquidity.tokenAllocation is ${Number(cfg.liquidity.tokenAllocation).toLocaleString()}, ` +
      `which prices the pool for a FULL raise. At the soft cap the pool would open ${ratio}x BELOW ` +
      `the presale price.\n` +
      `      Set liquidity.tokenAllocation <= ${softCapParityTokens.toLocaleString()} to be at or ` +
      `above the presale price at EVERY raise between the caps.\n` +
      `      This must be decided BEFORE presale:create. The allocation cannot be changed later: ` +
      `updateRaydiumCpmmBucketV2 is rejected once the genesis account is finalized (error 0x2b), ` +
      `and deposits are impossible before finalize (0x2c) — so by the time the raise is known, the ` +
      `number is already immutable. Verified on devnet 2026-07-26.\n`
    );
  }

  return {
    baseTokenAllocation: BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(decimals),
    softCapParityTokens,
    lpLockSchedule: createNeverClaimSchedule(),
    startCondition: createTimeAbsoluteCondition(unix(cfg.timeline.depositEnd)),
    raisedSolToLiquidityBps: cfg.liquidity.raisedSolToLiquidityBps,
    // Surfaced so `presale:plan` can show the range an operator is committing to.
    _pricing: { presalePrice, worstCasePoolPrice, bestCasePoolPrice, softCapParityTokens },
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
