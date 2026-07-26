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
/**
 * The bucket index layout, in one place.
 *
 * 0 is the presale, 1 is liquidity, and the allocation buckets follow in config
 * order. This used to be a loop counter inside cmdAllocate, which was fine while
 * only that command needed it — but `presale:create` must derive the unsold
 * ROLLOVER destination's PDA before any allocation bucket exists, and
 * `presale:trigger` must derive the same address again to pass it as a remaining
 * account. Three commands deriving one layout from three copies of the rule is
 * how they end up pointing at different accounts.
 */
export const PRESALE_BUCKET_INDEX = 0;
export const LIQUIDITY_BUCKET_INDEX = 1;
export const ALLOCATION_BUCKET_START = LIQUIDITY_BUCKET_INDEX + 1;

/** A "month" for vesting arithmetic. Declared, not assumed. */
export const VESTING_DAYS_PER_MONTH = 30n;
const SECONDS_PER_DAY = 86_400n;

/**
 * Flags that must be false on every $RCLAW vesting stream.
 *
 * Streamflow streams are configurable in ways that quietly undo the promise the
 * schedule makes. A stream the sender can cancel is not a vesting commitment —
 * it is a revocable IOU, and the holder of the genesis authority could empty a
 * team or advisor stream at will. `canUpdateRate` is the same defect wearing a
 * different hat, and `pausable` lets the clock be stopped indefinitely.
 * `transferableBySender` would let the stream be reassigned away from the
 * person it was promised to.
 *
 * These are exactly the properties a buyer reading "12-month cliff, then
 * 24-month linear" believes they are getting, and none of them is visible from
 * that sentence. So they are pinned here, asserted in tests, and printed by
 * `presale:plan` rather than left to whoever fills in the config.
 *
 * `automaticWithdrawal` is false for a different, non-safety reason: it makes
 * the stream pay out on a schedule using a crank the recipient does not control,
 * which costs fees and is not needed when the recipient can withdraw on demand.
 */
export const REQUIRED_STREAM_FLAGS = Object.freeze({
  cancelableBySender: false,
  cancelableByRecipient: false,
  transferableBySender: false,
  transferableByRecipient: false,
  canTopup: false,
  pausable: false,
  canUpdateRate: false,
  automaticWithdrawal: false,
});

/**
 * Turn "12-month cliff, then 24-month linear" into an exact Streamflow config.
 *
 * The whole difficulty is CONSERVATION. A stream releases `cliffAmount` at the
 * cliff and then `amountPerPeriod` every `period` — so the schedule pays out
 * `cliffAmount + amountPerPeriod * periods`, and integer division guarantees
 * that will not equal the allocation unless something absorbs the remainder.
 * Anything it fails to release is stranded in the stream permanently.
 *
 * So the remainder is added to `cliffAmount` and the identity
 *
 *     cliffAmount + amountPerPeriod * periods === baseTokenAllocation
 *
 * is enforced here and asserted in the tests. Putting it at the cliff rather
 * than in a ragged final period keeps the END DATE exactly what §4 promises,
 * which is the number people actually check. The remainder is at most
 * `periods - 1` base units — sub-microtoken at 9 decimals — but "small" is not
 * a reason to let supply go missing.
 *
 * @param {object} cfg   the presale config
 * @param {object} b     one entry from cfg.allocations.buckets
 * @param {bigint} baseTokenAllocation  the bucket's allocation in base units
 */
export function deriveStreamConfig(cfg, b, baseTokenAllocation) {
  const v = b.vesting || {};
  const cliffMonths = BigInt(v.cliffMonths ?? 0);
  const linearMonths = BigInt(v.linearMonths ?? 0);
  if (linearMonths <= 0n) {
    throw new Error(
      `allocation "${b.name}" declares vesting.type "linear" but linearMonths is ` +
      `${v.linearMonths}. A linear stream with no duration is a cliff — use an unlocked ` +
      'bucket instead, and say so in the published terms.'
    );
  }

  // The stream starts at TGE; the cliff is measured from there.
  const tge = unix(cfg.timeline.tge);
  const cliffTime = tge + cliffMonths * VESTING_DAYS_PER_MONTH * SECONDS_PER_DAY;
  const period = SECONDS_PER_DAY; // daily releases
  const periods = linearMonths * VESTING_DAYS_PER_MONTH;
  const endTime = cliffTime + periods * period;

  // A percentage released AT the cliff, before the linear tail begins.
  const cliffBps = BigInt(v.cliffPercent ?? 0) * 100n;
  if (cliffBps < 0n || cliffBps > 10_000n) {
    throw new Error(`allocation "${b.name}": vesting.cliffPercent must be 0-100 (got ${v.cliffPercent}).`);
  }
  const atCliff = (baseTokenAllocation * cliffBps) / 10_000n;
  const streamed = baseTokenAllocation - atCliff;

  const amountPerPeriod = streamed / periods;
  const remainder = streamed - amountPerPeriod * periods;
  const cliffAmount = atCliff + remainder;

  // The invariant this function exists to guarantee. Checked here rather than
  // only in tests, because a config change is what would break it and a config
  // change does not run the tests.
  if (cliffAmount + amountPerPeriod * periods !== baseTokenAllocation) {
    throw new Error(
      `internal: stream for "${b.name}" does not conserve supply ` +
      `(${cliffAmount} + ${amountPerPeriod} * ${periods} !== ${baseTokenAllocation})`
    );
  }
  if (amountPerPeriod <= 0n) {
    throw new Error(
      `allocation "${b.name}": ${linearMonths} months of daily periods over ${streamed} base ` +
      'units rounds to zero per period. Shorten the schedule or raise the allocation.'
    );
  }

  return {
    config: {
      startTime: tge,
      period,
      amountPerPeriod,
      cliff: cliffTime,
      cliffAmount,
      streamName: encodeStreamName(`RCLAW ${b.name}`),
      withdrawFrequency: period,
      ...REQUIRED_STREAM_FLAGS,
    },
    // Surfaced for `presale:plan` and the tests; not part of the instruction.
    _schedule: {
      cliffTime,
      endTime,
      periods,
      amountPerPeriod,
      cliffAmount,
      cliffMonths,
      linearMonths,
      remainder,
    },
  };
}

/** Streamflow stores the name in a fixed 64-byte field. */
function encodeStreamName(name) {
  const buf = new Uint8Array(64);
  const bytes = new TextEncoder().encode(name);
  if (bytes.length > 64) throw new Error(`stream name too long (${bytes.length} > 64): ${name}`);
  buf.set(bytes);
  return buf;
}

/**
 * Resolve the unsold-token rollover destination to a concrete bucket.
 *
 * Returns null when the config declares none — which is a decision, not a
 * default, and `presale:create` says so out loud rather than silently omitting
 * the behavior. Without a rollover, presale tokens nobody bought are stranded in
 * the presale bucket forever: `withdrawUnsoldPresaleV1` is V1-only and rejects a
 * V2 genesis account (0x2f), and the SDK has no V2 equivalent.
 *
 * The destination is named by BUCKET NAME rather than address, because the
 * address is a PDA that does not exist yet at create time and the name is what a
 * human can check against §4.
 */
export function deriveUnsoldRollover(cfg) {
  const r = cfg.sale?.unsoldRollover;
  if (!r || !r.destination) return null;

  const bps = Number(r.percentageBps ?? 0);
  if (!Number.isInteger(bps) || bps <= 0 || bps > 10_000) {
    throw new Error(
      `sale.unsoldRollover.percentageBps must be 1-10000 (got ${r.percentageBps}). ` +
      'Anything not rolled over is stranded in the presale bucket permanently.'
    );
  }

  const { buckets } = deriveAllocationBuckets(cfg);
  const target = buckets.find((b) => b.name === r.destination);
  if (!target) {
    throw new Error(
      `sale.unsoldRollover.destination "${r.destination}" is not an allocation bucket. ` +
      `Known: ${buckets.map((b) => b.name).join(', ')}.`
    );
  }
  if (target.kind !== 'cliff') {
    // A Streamflow destination would fold unsold supply into a vesting stream
    // whose amountPerPeriod was computed for a different total, so the schedule
    // would no longer release what it holds — the conservation property the
    // vesting work exists to guarantee.
    throw new Error(
      `sale.unsoldRollover.destination "${r.destination}" is a ${target.kind} (Streamflow) bucket. ` +
      'Rolling unsold tokens into a stream breaks its release schedule: amountPerPeriod was derived ' +
      'from the original allocation, so the extra tokens would never be released. Pick a cliff bucket.'
    );
  }
  return { name: target.name, bucketIndex: target.bucketIndex, percentageBps: bps, kind: target.kind };
}

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
    const baseTokenAllocation = tokens * scale;

    // Two bucket KINDS, and the difference is the difference between what §4
    // promises and what the chain used to do. `addUnlockedBucketV2` is a hard
    // cliff for the FULL amount — correct for treasury/reserve, which are
    // governance-gated, and a misrepresentation for team/advisors/community,
    // which §4 describes as linear. Those now get a Streamflow stream.
    const kind = b.vesting?.type === 'linear' ? 'linear' : 'cliff';
    const stream = kind === 'linear' ? deriveStreamConfig(cfg, b, baseTokenAllocation) : null;

    return {
      name: b.name,
      kind,
      bucketIndex: ALLOCATION_BUCKET_START + i,
      recipient: b.recipient || null,
      baseTokenAllocation,
      claimStartCondition: createTimeAbsoluteCondition(unlockAt),
      // Open-ended: an unlocked bucket with a claim window that closes would
      // strand its allocation if nobody claimed in time. 100 years out is
      // "never" for this purpose and keeps the condition type uniform.
      claimEndCondition: createTimeAbsoluteCondition(unlockAt + 100n * 365n * 24n * 3600n),
      // A stream's lock window is its own schedule, not the bucket's unlockAt:
      // the stream starts at TGE and runs to the end of the linear tail.
      lockStartCondition: stream ? createTimeAbsoluteCondition(stream.config.startTime) : null,
      lockEndCondition: stream ? createTimeAbsoluteCondition(stream._schedule.endTime) : null,
      streamConfig: stream ? stream.config : null,
      _schedule: stream ? stream._schedule : null,
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
/**
 * Does a pool of `lpBaseUnits` tokens, fed by `raisedLamports`, open at or above
 * the presale price?
 *
 * Exact BigInt cross-multiplication, never floats. That is not fastidiousness:
 * the interesting case is the boundary, and the two prices there differ by a
 * lamport. A float comparison of `quoteToPool / lpBaseUnits` against
 * `hardCap / presaleAlloc` gets the sign wrong at exactly the allocation that
 * was CHOSEN for parity — which is how the plan output ended up warning that a
 * pool sized for parity opened "1.0x BELOW" the presale price.
 *
 * One exported copy, used by the config-time warning, the trigger-time refusal
 * and the tests alike. Three copies of one comparison is three chances for them
 * to disagree about the case that matters.
 */
export function opensAtOrAbovePresale(cfg, raisedLamports, lpBaseUnits) {
  const quoteToPool =
    (BigInt(raisedLamports) * BigInt(cfg.liquidity.raisedSolToLiquidityBps ?? 0)) / 10_000n;
  const presaleAlloc = BigInt(cfg.sale.presaleAllocation) * 10n ** BigInt(cfg.token.decimals);
  const hardCapLamports = BigInt(Math.round(cfg.sale.hardCapSol * 1e9));
  return quoteToPool * presaleAlloc >= hardCapLamports * BigInt(lpBaseUnits);
}

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
  //
  // Compared EXACTLY. The float form of this test misfires precisely at the
  // allocation chosen for parity, which is the one configuration that must not
  // produce a warning.
  const lpBaseUnits = BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(decimals);
  const softCapLamports = BigInt(Math.round(cfg.sale.softCapSol * 1e9));
  if (!opensAtOrAbovePresale(cfg, softCapLamports, lpBaseUnits)) {
    const ratio = (presalePrice / worstCasePoolPrice).toFixed(1);
    console.warn(
      `\nWARNING: liquidity.tokenAllocation is ${Number(cfg.liquidity.tokenAllocation).toLocaleString()}, ` +
      `which is priced for a raise ABOVE the soft cap. At the soft cap the pool would open ${ratio}x ` +
      `BELOW the presale price — every buyer underwater at listing, against a PERMANENT LP lock and ` +
      `with no refund instruction.\n` +
      `      Set liquidity.tokenAllocation <= ${softCapParityTokens.toLocaleString()} to be at or ` +
      `above the presale price at every raise from the soft cap up.\n` +
      `      This must be decided BEFORE presale:create. The allocation cannot be changed later: ` +
      `updateRaydiumCpmmBucketV2 is rejected once the genesis account is finalized (error 0x2b), ` +
      `and deposits are impossible before finalize (0x2c) — so by the time the raise is known, the ` +
      `number is already immutable. Verified on devnet 2026-07-26.\n`
    );
  }

  // The mirror image, and it is not free money. Sizing below soft-cap parity
  // opens the pool ABOVE the presale price, which sounds like a gift and means
  // a pool that is thin next to the tokens presale buyers hold. Say so, so the
  // choice is made with both sides visible rather than optimised in one
  // direction until it breaks the other.
  if (bestCasePoolPrice > presalePrice * 1.0001) {
    const mult = (bestCasePoolPrice / presalePrice).toFixed(2);
    const presaleTokens = Number(cfg.sale.presaleAllocation);
    console.warn(
      `\nNOTE: at a FULL raise the pool opens ${mult}x ABOVE the presale price, and holds ` +
      `${Number(cfg.liquidity.tokenAllocation).toLocaleString()} tokens against ` +
      `${presaleTokens.toLocaleString()} in presale hands.\n` +
      `      That is the deliberate trade for never opening below the presale price (see ` +
      `liquidity._liquidityPricing_comment): underwater-at-listing is unrecoverable, a thin pool is ` +
      `not. Fund the remedy — keep the earmarked reserve available to deepen liquidity once the ` +
      `raise is known.\n`
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
