#!/usr/bin/env node
// Claim the program's canonical IDL account — WITHOUT the Anchor CLI.
//
// WHY THIS EXISTS
//
// Anchor stores a program's IDL in an account whose address derives from
// nothing but the program id:
//
//     base = find_program_address([], program_id)
//     idl  = create_with_seed(base, "anchor:idl", program_id)
//
// It is **first-come-first-served**. The `Create` handler sets
// `idl_account.authority = *accounts.from.key` — the signer — so whoever calls
// it first owns the IDL for our program, permanently, and can publish whatever
// interface description they like. Wallets and explorers fetch that account to
// decode our instructions for users. A hostile IDL does not change what the
// program does; it changes what a user is shown they are agreeing to.
//
// The roadmap lists claiming it "in the same session as the deploy" as a
// pre-deploy item, and the documented way to do that is `anchor idl init`. The
// Anchor CLI is not installable in this container (it needs a source build and
// disk is short), which left the item blocked and the exposure open.
//
// It turns out the CLI is not required. The IDL instructions are compiled into
// every Anchor program unless the `no-idl` feature is set — ours does not set
// it — and they are dispatched by a fixed 8-byte tag rather than by a
// generated client. So the instruction can be built by hand.
//
// Every constant here was read out of the vendored source rather than
// remembered, because the first value I reached for from memory was wrong:
//   anchor-lang-0.30.1/src/idl.rs                      — IDL_IX_TAG, enum order, address derivation
//   anchor-syn-0.30.1/src/codegen/program/idl.rs       — account list, space formula, authority
//
// WHAT THIS DOES NOT DO
//
// It claims the account. It does not publish a real IDL, because generating one
// needs `anchor build --features idl-build`, which needs the CLI. The account
// is created with a placeholder and the authority retained, so the real IDL can
// be written later with `anchor idl upgrade` (or `Write`/`SetBuffer`) by the
// authority holder. Claiming is the part that is irreversible if lost; writing
// the content is not.
//
// Usage:
//   node claim_idl.mjs <PROGRAM_ID> [--rpc URL] [--keypair PATH]
//                                   [--set-authority <PUBKEY>] [--dry]
import fs from 'node:fs';
import {
  Connection, Keypair, PublicKey, SystemProgram, Transaction,
  TransactionInstruction, sendAndConfirmTransaction,
} from '@solana/web3.js';

// Sha256("anchor:idl")[..8], as a little-endian u64. NOT a normal Anchor
// discriminator — IDL instructions sit outside the program's instruction enum
// so the enum's variant tags can follow source order.
const IDL_IX_TAG_LE = Buffer.from([0x40, 0xf4, 0xbc, 0x78, 0xa7, 0xe9, 0x69, 0x0a]);

// IdlInstruction variant indices, in declaration order (Borsh enums are tagged
// by position, so this order is load-bearing):
//   0 Create{data_len:u64}  1 CreateBuffer  2 Write{data}  3 SetBuffer
//   4 SetAuthority{new_authority:Pubkey}  5 Close  6 Resize{data_len:u64}
const IX_CREATE = 0;
const IX_SET_AUTHORITY = 4;

const argv = process.argv.slice(2);
const arg = (f, d = undefined) => {
  const i = argv.indexOf(f);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const has = (f) => argv.includes(f);

const PROGRAM_ID = new PublicKey(argv[0] || (() => {
  throw new Error('Pass the program id as the first argument.');
})());
const RPC = arg('--rpc', process.env.RPC_URL || 'http://127.0.0.1:8899');
const KEYPAIR = arg('--keypair', `${process.env.HOME}/.config/solana/id.json`);
const DRY = has('--dry');

/** The canonical IDL address for a program id. Derivation from anchor-lang. */
export async function idlAddress(programId) {
  const [base] = PublicKey.findProgramAddressSync([], programId);
  return { base, address: await PublicKey.createWithSeed(base, 'anchor:idl', programId) };
}

function createIx({ programId, base, idl, payer, dataLen }) {
  const data = Buffer.concat([
    IDL_IX_TAG_LE,
    Buffer.from([IX_CREATE]),
    (() => { const b = Buffer.alloc(8); b.writeBigUInt64LE(BigInt(dataLen)); return b; })(),
  ]);
  return new TransactionInstruction({
    programId,
    data,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },   // from — pays, so writable
      { pubkey: idl, isSigner: false, isWritable: true },    // to
      { pubkey: base, isSigner: false, isWritable: false },  // base (seeds = [])
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: programId, isSigner: false, isWritable: false }, // program (executable)
    ],
  });
}

function setAuthorityIx({ programId, idl, authority, newAuthority }) {
  const data = Buffer.concat([
    IDL_IX_TAG_LE, Buffer.from([IX_SET_AUTHORITY]), newAuthority.toBuffer(),
  ]);
  return new TransactionInstruction({
    programId,
    data,
    // IdlAccounts: idl (mut, has_one = authority), authority (signer)
    keys: [
      { pubkey: idl, isSigner: false, isWritable: true },
      { pubkey: authority, isSigner: true, isWritable: false },
    ],
  });
}

/** Decode IdlAccount: 8 disc || authority(32) || data_len(u32 LE) || data. */
function decodeIdlAccount(buf) {
  return {
    authority: new PublicKey(buf.subarray(8, 40)),
    dataLen: buf.readUInt32LE(40),
  };
}

async function main() {
  const conn = new Connection(RPC, 'confirmed');
  const payer = Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(KEYPAIR, 'utf8')))
  );
  const { base, address: idl } = await idlAddress(PROGRAM_ID);

  console.log('rpc          :', RPC);
  console.log('program      :', PROGRAM_ID.toBase58());
  console.log('idl base PDA :', base.toBase58());
  console.log('IDL ACCOUNT  :', idl.toBase58());
  console.log('payer        :', payer.publicKey.toBase58());

  // Refuse to guess. The program must exist and be executable, or `Create`
  // fails on the `#[account(executable)]` constraint after we have paid fees.
  const progInfo = await conn.getAccountInfo(PROGRAM_ID);
  if (!progInfo) throw new Error(`program ${PROGRAM_ID.toBase58()} does not exist on ${RPC}`);
  if (!progInfo.executable) throw new Error('that address exists but is not executable');

  const existing = await conn.getAccountInfo(idl);
  if (existing) {
    const { authority, dataLen } = decodeIdlAccount(existing.data);
    console.log('\nIDL account ALREADY CLAIMED');
    console.log('  authority  :', authority.toBase58());
    console.log('  data_len   :', dataLen);
    console.log('  size       :', existing.data.length);
    const ours = authority.equals(payer.publicKey);
    console.log(ours ? '  -> held by this payer' : '  -> HELD BY SOMEONE ELSE');
    if (!ours) process.exitCode = 1;

    const newAuth = arg('--set-authority');
    if (newAuth && ours) {
      if (DRY) { console.log(`\n[dry] would set authority to ${newAuth}`); return; }
      const sig = await sendAndConfirmTransaction(conn, new Transaction().add(
        setAuthorityIx({
          programId: PROGRAM_ID, idl, authority: payer.publicKey,
          newAuthority: new PublicKey(newAuth),
        })
      ), [payer]);
      const after = decodeIdlAccount((await conn.getAccountInfo(idl)).data);
      console.log('\nauthority transferred:', sig);
      console.log('  now  :', after.authority.toBase58());
      if (!after.authority.equals(new PublicKey(newAuth))) {
        throw new Error('readback disagrees with the requested authority');
      }
    }
    return;
  }

  // Placeholder length. Real IDLs are written later via Write/SetBuffer, and
  // Resize exists for accounts that outgrow this.
  const dataLen = Number(arg('--data-len', '512'));
  const space = Math.min(8 + 32 + 4 + dataLen, 10_000);
  const rent = await conn.getMinimumBalanceForRentExemption(space);
  console.log(`\nUNCLAIMED — claiming ${space} bytes (${(rent / 1e9).toFixed(6)} SOL rent)`);
  if (DRY) { console.log('[dry] no transaction sent'); return; }

  const sig = await sendAndConfirmTransaction(conn, new Transaction().add(
    createIx({ programId: PROGRAM_ID, base, idl, payer: payer.publicKey, dataLen })
  ), [payer]);
  console.log('signature    :', sig);

  // Verify the RESULT, not the request. A confirmed transaction only means it
  // landed; it does not mean the account is ours.
  const info = await conn.getAccountInfo(idl);
  if (!info) throw new Error('transaction confirmed but the IDL account does not exist');
  const { authority, dataLen: storedLen } = decodeIdlAccount(info.data);
  console.log('\n=== readback ===');
  console.log('owner        :', info.owner.toBase58());
  console.log('authority    :', authority.toBase58());
  console.log('size         :', info.data.length, '| data_len field:', storedLen);

  const ok = info.owner.equals(PROGRAM_ID) && authority.equals(payer.publicKey);
  console.log(ok ? '\nIDL ACCOUNT CLAIMED' : '\nCLAIM FAILED — account is not owned/controlled as expected');
  process.exit(ok ? 0 : 1);
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
