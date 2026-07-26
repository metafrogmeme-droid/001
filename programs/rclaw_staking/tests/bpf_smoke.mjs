#!/usr/bin/env node
// Exercise the REAL SBF bytecode on a local validator and report compute usage.
//
// WHY THIS EXISTS, given tests/attack.rs and tests/solvency.rs already pass.
//
// Those run through `solana-program-test`, which executes the program NATIVELY
// and in-process. That is genuinely useful — it is how the vault-drain and the
// solvency invariants are proven — but it cannot answer the questions the audit
// left open, because none of them exist natively:
//
//   - compute-unit consumption (native execution is not metered the same way)
//   - stack depth under the SBF calling convention
//   - syscall behaviour
//   - whether the deployed .so even loads
//
// So this deploys `target/deploy/rclaw_staking.so` to a local validator and
// drives `stake` — the heaviest instruction, since it does an `init_if_needed`
// on two accounts, a TLV extension walk in reject_hazardous_extensions(), and a
// transfer_checked CPI — then reads the compute units the runtime actually
// charged.
//
// Local validator only: it needs no devnet SOL, so this can run as often as
// wanted. Usage:
//
//   solana-test-validator --ledger /tmp/ledger --reset --quiet &
//   solana program deploy target/deploy/rclaw_staking.so
//   node programs/rclaw_staking/tests/bpf_smoke.mjs <PROGRAM_ID>
import {
  Connection, Keypair, PublicKey, SystemProgram, Transaction,
  sendAndConfirmTransaction, LAMPORTS_PER_SOL,
} from '@solana/web3.js';
import {
  TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID, MINT_SIZE,
  createInitializeMintInstruction, getMinimumBalanceForRentExemptMint,
  getAssociatedTokenAddressSync, createAssociatedTokenAccountInstruction,
  createMintToInstruction,
} from '@solana/spl-token';
import { createHash } from 'node:crypto';

const RPC = process.env.RPC_URL || 'http://127.0.0.1:8899';
const PROGRAM_ID = new PublicKey(process.argv[2] || (() => {
  throw new Error('Pass the deployed program id as argv[2].');
})());
const DECIMALS = 9;

/** Anchor's instruction discriminator: first 8 bytes of sha256("global:<name>"). */
function discriminator(name) {
  return createHash('sha256').update(`global:${name}`).digest().subarray(0, 8);
}

function stakeData(amount) {
  const buf = Buffer.alloc(16);
  discriminator('stake').copy(buf, 0);
  buf.writeBigUInt64LE(BigInt(amount), 8);
  return buf;
}

const conn = new Connection(RPC, 'confirmed');
const payer = Keypair.generate();

async function main() {
  const sig = await conn.requestAirdrop(payer.publicKey, 10 * LAMPORTS_PER_SOL);
  await conn.confirmTransaction(sig, 'confirmed');

  // A mint with NO freeze authority — the program rejects freeze-capable mints
  // at stake time (StakeError::MintHasFreezeAuthority), which is itself worth
  // exercising against real bytecode rather than assuming.
  const mint = Keypair.generate();
  const lamports = await getMinimumBalanceForRentExemptMint(conn);
  await sendAndConfirmTransaction(conn, new Transaction().add(
    SystemProgram.createAccount({
      fromPubkey: payer.publicKey, newAccountPubkey: mint.publicKey,
      space: MINT_SIZE, lamports, programId: TOKEN_PROGRAM_ID,
    }),
    createInitializeMintInstruction(mint.publicKey, DECIMALS, payer.publicKey, null, TOKEN_PROGRAM_ID),
  ), [payer, mint]);

  const userAta = getAssociatedTokenAddressSync(mint.publicKey, payer.publicKey);
  await sendAndConfirmTransaction(conn, new Transaction().add(
    createAssociatedTokenAccountInstruction(payer.publicKey, userAta, payer.publicKey, mint.publicKey),
    createMintToInstruction(mint.publicKey, userAta, payer.publicKey, 1_000_000_000n),
  ), [payer]);

  // Same seeds the program uses. Deriving them here rather than importing keeps
  // this honest: if the on-chain seeds change, this stops working.
  const [stakePda] = PublicKey.findProgramAddressSync(
    [Buffer.from('stake'), payer.publicKey.toBuffer(), mint.publicKey.toBuffer()], PROGRAM_ID);
  const [vaultAuthority] = PublicKey.findProgramAddressSync(
    [Buffer.from('vault'), mint.publicKey.toBuffer()], PROGRAM_ID);
  const vault = getAssociatedTokenAddressSync(mint.publicKey, vaultAuthority, true);

  const keys = [
    { pubkey: payer.publicKey, isSigner: true, isWritable: true },
    { pubkey: mint.publicKey, isSigner: false, isWritable: false },
    { pubkey: stakePda, isSigner: false, isWritable: true },
    { pubkey: vaultAuthority, isSigner: false, isWritable: false },
    { pubkey: vault, isSigner: false, isWritable: true },
    { pubkey: userAta, isSigner: false, isWritable: true },
    { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
  ];

  console.log('program :', PROGRAM_ID.toBase58());
  console.log('mint    :', mint.publicKey.toBase58());

  // First stake: creates BOTH init_if_needed accounts (stake record + vault ATA).
  // This is the worst case for compute.
  const txSig = await sendAndConfirmTransaction(conn, new Transaction().add({
    keys, programId: PROGRAM_ID, data: stakeData(400_000_000),
  }), [payer]);
  const tx = await conn.getTransaction(txSig, { commitment: 'confirmed', maxSupportedTransactionVersion: 0 });
  const logs = tx.meta.logMessages || [];
  const cu = logs.map((l) => l.match(/consumed (\d+) of (\d+) compute units/)).filter(Boolean).pop();

  console.log('\n=== FIRST stake (creates stake record + vault ATA) ===');
  console.log('signature      :', txSig);
  console.log('compute units  :', cu ? `${cu[1]} of ${cu[2]}` : '(not reported)');

  // Second stake: both accounts already exist, so this is the steady-state cost.
  const txSig2 = await sendAndConfirmTransaction(conn, new Transaction().add({
    keys, programId: PROGRAM_ID, data: stakeData(100_000_000),
  }), [payer]);
  const tx2 = await conn.getTransaction(txSig2, { commitment: 'confirmed', maxSupportedTransactionVersion: 0 });
  const cu2 = (tx2.meta.logMessages || []).map((l) => l.match(/consumed (\d+) of (\d+) compute units/)).filter(Boolean).pop();
  console.log('\n=== SECOND stake (accounts exist; steady state) ===');
  console.log('compute units  :', cu2 ? `${cu2[1]} of ${cu2[2]}` : '(not reported)');

  // Read the record back off the wire at the offsets bot/token/tier_gate.py
  // uses. Proving those against REAL bytecode closes the loop that the Borsh
  // unit test can only assert in-process.
  const acct = await conn.getAccountInfo(stakePda);
  const d = acct.data;
  const amount = d.readBigUInt64LE(73);
  const unlockAt = d.readBigInt64LE(89);
  console.log('\n=== StakeAccount, read at the tier-gate offsets ===');
  console.log('account size   :', d.length, '(8 disc + 90 body + 64 reserved = 162)');
  console.log('version   @8   :', d.readUInt8(8));
  console.log('amount    @73  :', amount.toString(), '(expect 500000000)');
  console.log('unlock_at @89  :', unlockAt.toString(), '->', new Date(Number(unlockAt) * 1000).toISOString());

  const ok = amount === 500_000_000n && d.length === 162 && d.readUInt8(8) === 1;
  console.log(ok ? '\nBPF SMOKE PASSED' : '\nBPF SMOKE FAILED');
  process.exit(ok ? 0 : 1);
}

await main();
