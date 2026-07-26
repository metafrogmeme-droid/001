#!/usr/bin/env node
// Check a proposed upgrade authority BEFORE handing it the program. Irreversibly.
//
// WHY
//
// `solana program set-upgrade-authority` takes any 32 bytes. It does not ask
// whether anything can actually sign for the address you gave it, and the
// operation is one-way: once the current authority signs it away, the only
// thing that can move it again is the new authority. Get it wrong and the
// program is either permanently un-upgradeable or under the control of
// something you did not intend — with no recovery, on a program that signs for
// every `["vault", mint]` PDA.
//
// The specific mistake this exists to stop is not exotic; it is the default one.
// A Squads multisig has TWO addresses:
//
//   multisig account   5b3bw9qJx38MVWgz5qoxZGPsYQeMxwRjU5JVJPtg7BGZ
//   vault PDA          BStC8ReWMGowGHZGnRMkRMP8sxhGEXE6LPpzdfUdEj5e   <- signs
//
// The multisig account is the one the Squads UI shows you and the one you would
// naturally copy. It is a *data* account. Nothing signs as it. Hand the program
// to it and the upgrade authority is gone forever — the transfer succeeds, the
// CLI prints nothing alarming, and the loss is silent until the first time an
// upgrade is needed.
//
// So this re-derives the vault PDA from the multisig you name and requires the
// target to equal it. Seeds are ["multisig", multisigPda, "vault", u8(index)]
// under SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf — reproduced here with plain
// web3.js rather than the Squads SDK, deliberately: a check that guards an
// irreversible action should not itself depend on a package that could change
// under it, and this repo does not otherwise need that dependency.
//
// Usage:
//   node check_upgrade_authority.mjs <PROGRAM_ID> --new-authority <ADDR>
//        [--squads-multisig <ADDR>] [--vault-index N] [--rpc URL]
//
// Exit 0 = safe to proceed, 1 = do not transfer.
import { Connection, PublicKey } from '@solana/web3.js';

const SQUADS_PROGRAM_ID = new PublicKey('SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf');
const BPF_LOADER_UPGRADEABLE = new PublicKey('BPFLoaderUpgradeab1e11111111111111111111111');
const SYSTEM_PROGRAM = new PublicKey('11111111111111111111111111111111');

const argv = process.argv.slice(2);
const arg = (f, d) => { const i = argv.indexOf(f); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };

/** Squads v4 vault PDA. Seeds read out of @sqds/multisig, reimplemented here. */
export function squadsVaultPda(multisigPda, index = 0) {
  return PublicKey.findProgramAddressSync(
    [Buffer.from('multisig'), multisigPda.toBytes(), Buffer.from('vault'), Buffer.from([index])],
    SQUADS_PROGRAM_ID
  )[0];
}

export async function checkAuthority({ conn, programId, target, multisig, vaultIndex }) {
  const findings = [];
  const fail = (m) => findings.push({ level: 'FAIL', m });
  const warn = (m) => findings.push({ level: 'WARN', m });
  const ok = (m) => findings.push({ level: 'ok', m });

  // The program has to be upgradeable in the first place, and the authority we
  // are about to replace has to exist.
  const prog = await conn.getAccountInfo(programId);
  if (!prog) { fail(`program ${programId.toBase58()} does not exist on this RPC`); return findings; }
  if (!prog.owner.equals(BPF_LOADER_UPGRADEABLE)) {
    fail('program is not owned by the upgradeable loader — it has no upgrade authority to transfer');
    return findings;
  }
  const programDataAddr = new PublicKey(prog.data.subarray(4, 36));
  const pd = await conn.getAccountInfo(programDataAddr);
  if (!pd) { fail(`programdata ${programDataAddr.toBase58()} not found`); return findings; }
  // ProgramData: 4-byte enum | 8-byte slot | 1-byte Option<Pubkey> | 32-byte authority
  const hasAuthority = pd.data[12] === 1;
  if (!hasAuthority) {
    fail('upgrade authority is already NONE — the program is immutable and this is not undoable');
    return findings;
  }
  ok(`current authority ${new PublicKey(pd.data.subarray(13, 45)).toBase58()}`);

  const onCurve = PublicKey.isOnCurve(target.toBytes());
  const info = await conn.getAccountInfo(target);

  if (multisig) {
    // The case this tool exists for. Re-derive rather than trust what was typed.
    const derived = squadsVaultPda(multisig, vaultIndex);
    if (target.equals(derived)) {
      ok(`target is the Squads vault PDA for ${multisig.toBase58()} (index ${vaultIndex})`);
    } else if (target.equals(multisig)) {
      fail(
        'target is the MULTISIG ACCOUNT, not its vault PDA. Nothing can sign as the multisig ' +
        `account — it is a data account. The signer is ${derived.toBase58()}. Transferring to ` +
        'the address you gave would destroy the upgrade authority permanently and silently.'
      );
    } else {
      fail(
        `target does not match the vault PDA derived from ${multisig.toBase58()} ` +
        `(index ${vaultIndex}). Expected ${derived.toBase58()}, got ${target.toBase58()}.`
      );
    }
    const msInfo = await conn.getAccountInfo(multisig);
    if (!msInfo) fail(`multisig ${multisig.toBase58()} does not exist on this RPC`);
    else if (!msInfo.owner.equals(SQUADS_PROGRAM_ID)) {
      fail(`multisig ${multisig.toBase58()} is not owned by the Squads program (owner ${msInfo.owner.toBase58()})`);
    } else ok('multisig account exists and is owned by the Squads program');
  } else if (onCurve) {
    // A normal wallet. It can sign, so this is not a brick — but it is one key.
    warn(
      'target is an ordinary keypair address, not a multisig. It CAN sign, so the program stays ' +
      'upgradeable, but a single key that can replace this bytecode can sign for every ' +
      '["vault", mint] PDA. See docs/TOKEN_ROADMAP.md §11.'
    );
    if (info && !info.owner.equals(SYSTEM_PROGRAM)) {
      warn(`target is on-curve but owned by ${info.owner.toBase58()} rather than the system program`);
    }
  } else {
    // Off-curve and no multisig named: it is a PDA of *something*, and whether
    // anything will ever sign for it cannot be established from here.
    fail(
      'target is OFF-CURVE (a PDA) and no --squads-multisig was given to check it against. ' +
      'No private key exists for this address; it can only ever sign if some program invokes ' +
      'with its seeds. If this is a Squads vault, pass --squads-multisig so it can be re-derived. ' +
      'If it is not, do not transfer: the upgrade authority would be unreachable.'
    );
  }
  return findings;
}

async function main() {
  const programId = new PublicKey(argv[0] || (() => { throw new Error('Pass the program id first.'); })());
  const target = new PublicKey(arg('--new-authority') || (() => { throw new Error('Pass --new-authority <ADDR>.'); })());
  const msArg = arg('--squads-multisig');
  const conn = new Connection(arg('--rpc', process.env.RPC_URL || 'http://127.0.0.1:8899'), 'confirmed');

  console.log('program        :', programId.toBase58());
  console.log('new authority  :', target.toBase58());
  if (msArg) console.log('squads multisig:', msArg);
  console.log();

  const findings = await checkAuthority({
    conn, programId, target,
    multisig: msArg ? new PublicKey(msArg) : null,
    vaultIndex: Number(arg('--vault-index', '0')),
  });

  for (const f of findings) console.log(`  ${f.level.padEnd(4)}  ${f.m}`);
  const failed = findings.filter((f) => f.level === 'FAIL');
  console.log();
  if (failed.length) {
    console.error(`DO NOT TRANSFER — ${failed.length} blocking finding(s).`);
    process.exit(1);
  }
  console.log('Safe to transfer. This is one-way: after it, only the new authority can move it again.');
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
