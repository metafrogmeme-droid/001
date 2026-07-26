#!/usr/bin/env node
// Cover createAtaIdempotent(), and pin the two library helpers it replaced.
//
// Split deliberately into two halves.
//
// The OFFLINE half needs no chain, no keypair and no network, so it runs on
// every CI run and can never quietly become a no-op. It guards the structural
// properties: that the deposit/trigger/claim path reaches nothing but SPL
// programs, that the CreateIdempotent discriminant is actually emitted, and
// that mpl-toolbox's `createIdempotentAssociatedToken` is still broken (a
// canary — if it is ever fixed upstream, this fails and we re-evaluate).
//
// The ON-CHAIN half proves the thing no amount of reading can: how the SPL
// Associated Token program DISPATCHES on that byte. `createIdempotentAssociated
// Token` shipped non-idempotent precisely because its name was checked by
// nobody and its behaviour by nothing, so the claim "1 means idempotent" is
// worth exactly what it is tested against. It self-skips without a validator.
//
// A bare validator is enough — the ATA program is built in, so no --clone and
// no devnet:
//
//   solana-test-validator --reset --quiet --ledger /tmp/ledger
//   RPC_URL=http://127.0.0.1:8899 node --test token/presale/ata_idempotent.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { generateSigner, keypairIdentity, publicKey, sol, transactionBuilder } from '@metaplex-foundation/umi';
import {
  findAssociatedTokenPda, mplToolbox, createIdempotentAssociatedToken,
} from '@metaplex-foundation/mpl-toolbox';
import {
  createAtaIdempotent, isLoopbackRpc, PRESALE_DIR,
  SPL_ASSOCIATED_TOKEN_PROGRAM_ID, SPL_TOKEN_PROGRAM_ID, SPL_SYSTEM_PROGRAM_ID,
} from './genesis_lib.mjs';

const RPC = process.env.RPC_URL || 'http://127.0.0.1:8899';
const WSOL = publicKey('So11111111111111111111111111111111111111112');
const MPL_TOKEN_EXTRAS = publicKey('TokExjvjJmhKaRBShsBAsbSvEWMA1AgUNK7ps4SAc2p');

/** A Umi that touches no network and needs no keyfile — construction is lazy. */
function offlineUmi() {
  const umi = createUmi('http://127.0.0.1:1').use(mplToolbox());
  return umi.use(keypairIdentity(umi.eddsa.generateKeypair()));
}

// ── offline: structural guards, always run ──────────────────────────────────

test('the money path reaches no program but SPL System/Token/ATA', () => {
  // The reason createAtaIdempotent exists. `createTokenIfMissing`, which the
  // deposit, trigger and claim paths all used, invokes MPL Token Extras
  // (TokExjvjJ…) — a third-party upgradeable program — which then CPIs into the
  // ATA program. Three instructions that move buyer money each executed
  // bytecode under an upgrade authority this project does not hold.
  const umi = offlineUmi();
  const ix = createAtaIdempotent(umi, { mint: WSOL, owner: umi.identity.publicKey }).getInstructions()[0];

  assert.equal(ix.programId, SPL_ASSOCIATED_TOKEN_PROGRAM_ID);
  const referenced = new Set([ix.programId, ...ix.keys.map((k) => k.pubkey)]);
  assert.ok(referenced.has(SPL_SYSTEM_PROGRAM_ID));
  assert.ok(referenced.has(SPL_TOKEN_PROGRAM_ID));
  assert.ok(!referenced.has(MPL_TOKEN_EXTRAS), 'MPL Token Extras must not be reachable from the money path');
});

test('the CreateIdempotent discriminant is emitted', () => {
  const umi = offlineUmi();
  const ix = createAtaIdempotent(umi, { mint: WSOL, owner: umi.identity.publicKey }).getInstructions()[0];
  // 1 = CreateIdempotent. 0 — or empty data, which the ATA program reads the
  // same way — is Create, which throws on an existing account. The on-chain
  // half below proves that difference is real rather than assumed.
  assert.deepEqual(Array.from(ix.data), [1]);
});

test('the ATA account slot holds the derived address, not the program id', () => {
  const umi = offlineUmi();
  const owner = generateSigner(umi).publicKey;
  const ix = createAtaIdempotent(umi, { mint: WSOL, owner }).getInstructions()[0];
  const expected = findAssociatedTokenPda(umi, { mint: WSOL, owner, tokenProgramId: SPL_TOKEN_PROGRAM_ID })[0];
  assert.equal(ix.keys[1].pubkey, expected);
  assert.equal(ix.keys[1].isWritable, true);
  assert.equal(ix.keys[2].pubkey, owner);
  assert.equal(ix.keys[3].pubkey, WSOL);
});

test('CANARY: mpl-toolbox createIdempotentAssociatedToken is still broken', () => {
  // Not a test of our code — a test of the assumption behind not using theirs.
  // If either assertion fails the library was fixed upstream, and the local
  // helper should be re-evaluated rather than kept out of habit.
  const umi = offlineUmi();
  const owner = generateSigner(umi).publicKey;
  const ix = createIdempotentAssociatedToken(umi, { mint: WSOL, owner }).getInstructions()[0];

  assert.equal(ix.data.length, 0, 'library emits no discriminant, so the ATA program sees Create');
  assert.equal(
    ix.keys[1].pubkey, SPL_ASSOCIATED_TOKEN_PROGRAM_ID,
    'library never derives a default `ata`, so the unresolved slot gets the program id',
  );
});

test('REGRESSION: no presale command imports createTokenIfMissing', () => {
  // The defect was invisible in review because the source says
  // `createTokenIfMissing` and nothing says `TokExjvjJ…`. Guard the name.
  for (const f of ['genesis_presale.mjs', 'genesis_lib.mjs']) {
    const src = fs.readFileSync(path.join(PRESALE_DIR, f), 'utf8');
    const code = src
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)) // the comments explain WHY it is gone
      .join('\n');
    assert.ok(
      !/createTokenIfMissing/.test(code),
      `${f} reintroduces createTokenIfMissing, which routes through MPL Token Extras`,
    );
  }
});

// ── on-chain: how the ATA program actually dispatches ───────────────────────

// A throwaway funded identity rather than .keys/mint-payer.json: this half must
// be runnable on a bare validator in CI, where no keyfile exists and none
// should. It also keeps the cases independent of whatever state a devnet run
// left behind.
//
// COMMITMENT is explicit, and it is the difference between this file taking
// half a minute and taking longer than the CI job's timeout.
//
// Umi's default is `finalized`, which is ~32 slots per confirmation. Eleven
// transactions at 32 slots each is fine on a laptop and not fine on a
// two-core CI runner: the first attempt sat in this step for over nine minutes.
// It also caused a confusing failure that has nothing to do with commitment on
// its face — an airdrop confirmed but not yet finalized makes the NEXT
// transaction fail preflight with "Attempt to debit an account but found no
// record of a prior credit", which reads as an empty wallet.
//
// `confirmed` is the right level here: a local validator is single-node, there
// is no fork to be rolled back on, and nothing in this file holds value. The
// presale commands themselves are deliberately left at umi's default — for a
// real sale the conservative level is the correct one, and the e2e harness's
// balance gate reads at `finalized` to match.
//
// The bounded wait stays. `confirmed` is fast, not instantaneous, and a fresh
// validator that is still catching up should make this skip loudly rather than
// fail eleven tests in a way that looks like a defect in the code under test.
async function liveUmi() {
  if (!isLoopbackRpc(RPC)) return null;
  const umi = createUmi(RPC, 'confirmed').use(mplToolbox());
  umi.use(keypairIdentity(umi.eddsa.generateKeypair()));
  try {
    await umi.rpc.getSlot();
  } catch {
    return null; // no validator — the offline half above still ran
  }
  await umi.rpc.airdrop(umi.identity.publicKey, sol(5));
  for (let i = 0; i < 120; i += 1) {
    const bal = await umi.rpc.getBalance(umi.identity.publicKey, { commitment: 'confirmed' });
    if (bal.basisPoints > 0n) return umi;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('airdrop never confirmed — validator is unhealthy');
}

const liveOrNull = await liveUmi();
const live = liveOrNull !== null;
const opts = { skip: live ? false : `no local validator at ${RPC} — start one (see the header)` };
const umi = liveOrNull ?? offlineUmi();

/** The same instruction, with the discriminant swappable. */
function ataIxWithData({ mint, owner }, data) {
  const ata = findAssociatedTokenPda(umi, { mint, owner, tokenProgramId: SPL_TOKEN_PROGRAM_ID })[0];
  return transactionBuilder([{
    instruction: {
      keys: [
        { pubkey: umi.payer.publicKey, isSigner: true, isWritable: true },
        { pubkey: ata, isSigner: false, isWritable: true },
        { pubkey: owner, isSigner: false, isWritable: false },
        { pubkey: mint, isSigner: false, isWritable: false },
        { pubkey: SPL_SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
        { pubkey: SPL_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      ],
      programId: SPL_ASSOCIATED_TOKEN_PROGRAM_ID,
      data,
    },
    signers: [umi.payer],
    bytesCreatedOnChain: 0,
  }]);
}

// A throwaway owner per case, so "does not exist yet" is a real precondition
// and the file can be re-run against the same ledger.
const freshOwner = () => generateSigner(umi).publicKey;

test('creates the ATA when it is missing', opts, async () => {
  const owner = freshOwner();
  const ata = findAssociatedTokenPda(umi, { mint: WSOL, owner })[0];
  assert.equal((await umi.rpc.getAccount(ata)).exists, false, 'precondition: ATA absent');

  await createAtaIdempotent(umi, { mint: WSOL, owner }).sendAndConfirm(umi);

  const acct = await umi.rpc.getAccount(ata);
  assert.equal(acct.exists, true);
  assert.equal(acct.owner, SPL_TOKEN_PROGRAM_ID, 'ATA must be owned by the token program');
});

test('is a no-op when the ATA already exists', opts, async () => {
  const owner = freshOwner();
  await createAtaIdempotent(umi, { mint: WSOL, owner }).sendAndConfirm(umi);
  // The second call is the whole point: every claimer rebuilds this instruction
  // for the shared Genesis fee-wallet ATA, and only one of them creates it.
  await createAtaIdempotent(umi, { mint: WSOL, owner }).sendAndConfirm(umi);
});

test('MUTATION: discriminant 0 is Create and rejects an existing account', opts, async () => {
  const owner = freshOwner();
  await ataIxWithData({ mint: WSOL, owner }, new Uint8Array([0])).sendAndConfirm(umi);
  // If this ever passes, the 1 in createAtaIdempotent is decorative and the
  // idempotence guarantee rests on nothing.
  await assert.rejects(
    ataIxWithData({ mint: WSOL, owner }, new Uint8Array([0])).sendAndConfirm(umi),
    /.*/,
    'Create (0) must reject an existing account — otherwise CreateIdempotent (1) proves nothing',
  );
});

test('empty instruction data dispatches as Create, not CreateIdempotent', opts, async () => {
  // Exactly what mpl-toolbox's createIdempotentAssociatedToken emits, which is
  // why the canary above matters: swapping it in on the strength of its name
  // would have worked for the first claimer and failed for every later one.
  const owner = freshOwner();
  await ataIxWithData({ mint: WSOL, owner }, new Uint8Array()).sendAndConfirm(umi);
  await assert.rejects(
    ataIxWithData({ mint: WSOL, owner }, new Uint8Array()).sendAndConfirm(umi),
    /.*/,
    'empty data must behave as Create',
  );
});

test('an off-curve PDA owner is supported', opts, async () => {
  // presale:trigger creates the liquidity bucket signer's wSOL account, and
  // that owner is a program-derived address. If CreateIdempotent refused
  // off-curve owners the quote share would have nowhere to land.
  const [pda] = umi.eddsa.findPda(SPL_ASSOCIATED_TOKEN_PROGRAM_ID, [
    new TextEncoder().encode('rclaw-offcurve-probe'),
  ]);
  const ata = findAssociatedTokenPda(umi, { mint: WSOL, owner: pda })[0];
  await createAtaIdempotent(umi, { mint: WSOL, owner: pda }).sendAndConfirm(umi);
  assert.equal((await umi.rpc.getAccount(ata)).exists, true);
  await createAtaIdempotent(umi, { mint: WSOL, owner: pda }).sendAndConfirm(umi);
});

test('the library helper does not land on chain either', opts, async () => {
  const built = createIdempotentAssociatedToken(umi, { mint: WSOL, owner: freshOwner() });
  await assert.rejects(built.sendAndConfirm(umi), /.*/, 'InvalidSeeds — the ata slot holds the program id');
});
