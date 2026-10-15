// Squads v4: what the treasury multisig and its proposals hold, decoded from their bytes, and the
// rows that compare them with the token record's multisig block (token/config/rclaw.mainnet.json).
//
// The layouts are the program's Anchor accounts (Squads-Protocol/v4, programs/squads_multisig_program
// /src/state): an 8-byte discriminator, sha256("account:<Name>")[0..8], then the fields in order.
// Read-only: nothing here holds a key or builds a transaction.
import { createHash } from 'node:crypto';

import { PublicKey } from '@solana/web3.js';

import { base58Encode, decimalToUnits, formatUnits } from './smithii_lib.mjs';

const discriminator = (name) => createHash('sha256').update(`account:${name}`).digest().subarray(0, 8);
export const MULTISIG_DISCRIMINATOR = discriminator('Multisig');
export const PROPOSAL_DISCRIMINATOR = discriminator('Proposal');

/** Pubkey::default(): how an Option-less Pubkey field says "none" (the config authority). */
const NO_KEY = '11111111111111111111111111111111';
/** A member's permission mask: initiate 1, vote 2, execute 4. Only a voter counts towards the threshold. */
export const VOTE = 2;
export const PROPOSAL_STATUS = ['Draft', 'Active', 'Rejected', 'Approved', 'Executing', 'Executed', 'Cancelled'];

function reader(buf) {
  let o = 0;
  const need = (n, what) => {
    if (o + n > buf.length) throw new Error(`${what} does not fit: the account is ${buf.length} bytes`);
  };
  return {
    get offset() { return o; },
    skip(n) { need(n, 'a field'); o += n; },
    key(what) { need(32, what); const k = base58Encode(buf.subarray(o, o + 32)); o += 32; return k; },
    u8(what) { need(1, what); return buf.readUInt8(o++); },
    u16(what) { need(2, what); const v = buf.readUInt16LE(o); o += 2; return v; },
    u32(what) { need(4, what); const v = buf.readUInt32LE(o); o += 4; return v; },
    u64(what) { need(8, what); const v = buf.readBigUInt64LE(o); o += 8; return v; },
    i64(what) { need(8, what); const v = buf.readBigInt64LE(o); o += 8; return v; },
  };
}

/**
 * A Multisig account: create_key, config_authority, threshold u16, time_lock u32 (seconds),
 * transaction_index u64, stale_transaction_index u64, rent_collector Option<Pubkey>, bump,
 * members Vec<{ key, permissions mask u8 }>. Throws on anything that is not one.
 */
export function decodeMultisig(buf) {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(MULTISIG_DISCRIMINATOR)) {
    throw new Error('not a Squads v4 Multisig account (discriminator)');
  }
  const r = reader(buf);
  r.skip(8);
  const createKey = r.key('create_key');
  const authority = r.key('config_authority');
  const threshold = r.u16('threshold');
  const timeLockSeconds = r.u32('time_lock');
  const transactionIndex = r.u64('transaction_index');
  const staleTransactionIndex = r.u64('stale_transaction_index');
  const rentCollector = r.u8('rent_collector tag') === 1 ? r.key('rent_collector') : null;
  r.u8('bump');
  const n = r.u32('members length');
  const members = [];
  for (let i = 0; i < n; i++) members.push({ key: r.key(`member ${i}`), mask: r.u8(`member ${i} permissions`) });
  return {
    createKey, configAuthority: authority === NO_KEY ? null : authority, threshold, timeLockSeconds,
    transactionIndex, staleTransactionIndex, rentCollector, members,
  };
}

/**
 * A Proposal account: multisig, transaction_index u64, status (an enum tag, then an i64 timestamp
 * for every variant but Executing), bump, approved / rejected / cancelled Vec<Pubkey>.
 */
export function decodeProposal(buf) {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(PROPOSAL_DISCRIMINATOR)) {
    throw new Error('not a Squads v4 Proposal account (discriminator)');
  }
  const r = reader(buf);
  r.skip(8);
  const multisig = r.key('multisig');
  const transactionIndex = r.u64('transaction_index');
  const tag = r.u8('status');
  const status = PROPOSAL_STATUS[tag];
  if (status === undefined) throw new Error(`unknown proposal status ${tag}`);
  const since = status === 'Executing' ? null : Number(r.i64('status timestamp'));
  r.u8('bump');
  const keys = (what) => {
    const n = r.u32(`${what} length`);
    const out = [];
    for (let i = 0; i < n; i++) out.push(r.key(`${what} ${i}`));
    return out;
  };
  return { multisig, transactionIndex, status, since, approved: keys('approved'), rejected: keys('rejected'), cancelled: keys('cancelled') };
}

const seed = (s) => Buffer.from(s);
const u64le = (n) => { const b = Buffer.alloc(8); b.writeBigUInt64LE(BigInt(n)); return b; };

/** The vault the multisig signs for: ["multisig", multisig, "vault", u8 index]. The only address that holds its tokens. */
export function vaultAddress(program, multisig, index) {
  return PublicKey.findProgramAddressSync(
    [seed('multisig'), new PublicKey(multisig).toBuffer(), seed('vault'), Buffer.from([index])], new PublicKey(program))[0].toBase58();
}

/** Proposal #index: ["multisig", multisig, "transaction", u64 index, "proposal"]. */
export function proposalAddress(program, multisig, index) {
  return PublicKey.findProgramAddressSync(
    [seed('multisig'), new PublicKey(multisig).toBuffer(), seed('transaction'), u64le(index), seed('proposal')], new PublicKey(program))[0].toBase58();
}

/** "172800" -> "48 hours"; a delay that is not whole hours is printed in seconds. */
export function timeLockText(seconds) {
  if (seconds === 0) return 'none';
  return seconds % 3600 === 0 ? `${seconds / 3600} hours` : `${seconds} seconds`;
}

/** The rows for the multisig account itself, against the record's block. */
export function compareMultisig(want, observed) {
  const rows = [];
  const add = (check, ok, detail) => rows.push({ check, status: ok ? 'PASS' : 'FAIL', detail });
  const ms = observed.multisig;
  add('the multisig is owned by the Squads v4 program', observed.owner === want.program,
    `owner ${observed.owner}; the record names ${want.program}`);
  add('the approvals needed are the record\'s', ms.threshold === want.threshold,
    `threshold ${ms.threshold} on chain; the record says ${want.threshold}`);
  const voters = ms.members.filter((m) => (m.mask & VOTE) !== 0).map((m) => m.key).sort();
  const listed = [...want.members].sort();
  const same = voters.length === listed.length && voters.every((k, i) => k === listed[i]);
  add('the members who can approve are the record\'s', same,
    same ? `${voters.length} voters: ${voters.join(', ')}` : `on chain ${voters.join(', ') || 'none'}; the record lists ${listed.join(', ')}`);
  add('the time lock is the record\'s', ms.timeLockSeconds === want.time_lock_seconds,
    `${ms.timeLockSeconds} s (${timeLockText(ms.timeLockSeconds)}) on chain; the record says ${want.time_lock_seconds} s`);
  add('nobody can change the rules without a proposal', ms.configAuthority === null,
    ms.configAuthority === null
      ? 'no config authority: members, threshold and time lock change only by an approved, time-locked proposal'
      : `config authority ${ms.configAuthority} can change members, threshold and time lock alone, with no approval and no delay`);
  const vault = vaultAddress(want.program, want.account, want.vault_index);
  add('the vault is the multisig\'s own', vault === want.vault,
    `vault #${want.vault_index} of ${want.account} is ${vault}; the record names ${want.vault}`);
  return rows;
}

/**
 * The vault's RCLAW, from every token account it owns for the mint. Three values: a sum that equals
 * the record is a PASS; any other sum is a FAIL, because the page prints the record's figure as
 * what the vault holds, so a balance that moved, either way, is a page that is wrong.
 */
export function compareVaultHoldings(want, decimals, accounts) {
  const rows = [];
  const add = (check, ok, detail) => rows.push({ check, status: ok ? 'PASS' : 'FAIL', detail });
  const expected = decimalToUnits(want.holds_tokens, decimals).units;
  const held = accounts.reduce((s, a) => s + a.amount, 0n);
  add('the vault holds what the record states', held === expected,
    `${formatUnits(held, decimals)} RCLAW in ${accounts.length} token account(s) on chain; the record states ${formatUnits(expected, decimals)}`
      + (held === expected ? '' : ': re-take the snapshot and re-record holds_tokens and buckets'));
  const loose = accounts.filter((a) => a.delegate !== null || a.closeAuthority !== null);
  add('nobody else can move the vault\'s tokens', loose.length === 0,
    loose.length ? `a delegate or close authority is set on ${loose.map((a) => a.address).join(', ')}` : 'no delegate and no close authority');
  return rows;
}

/**
 * One WARN row per proposal that can still execute: approvals and a time lock are custody with a
 * delay, not a lock, so an open proposal is the warning that tokens or rules may be about to change.
 * Approved (and the deprecated mid-batch Executing) is open; Draft or Active is open unless it is
 * stale, since a stale proposal can never be approved. A proposal account that is gone was closed
 * (rent reclaimed), which the program allows only once it is settled or stale.
 */
export function compareProposals(staleIndex, proposals) {
  const live = (p) => {
    const s = p.decoded?.status;
    if (s === 'Approved' || s === 'Executing') return true;
    return (s === 'Draft' || s === 'Active') && BigInt(p.index) > staleIndex;
  };
  const open = proposals.filter(live);
  if (open.length === 0) {
    const settled = proposals.filter((p) => p.decoded).map((p) => `#${p.index} ${p.decoded.status}`);
    const closed = proposals.filter((p) => !p.decoded).map((p) => `#${p.index}`);
    const read = [...settled, ...(closed.length ? [`closed ${closed.join(', ')}`] : [])];
    return [{ check: 'no proposal is waiting to execute', status: 'PASS', detail: `${proposals.length} read: ${read.join(', ') || 'none yet'}` }];
  }
  return open.map((p) => ({
    check: `proposal #${p.index} is open`, status: 'WARN',
    detail: `${p.decoded.status}${p.decoded.since ? ` since ${new Date(p.decoded.since * 1000).toISOString()}` : ''}, `
      + `${p.decoded.approved.length} approval(s): once approved and past the time lock it can move tokens or change the rules`,
  }));
}

/** The record's buckets: each a whole number of tokens, together exactly what the vault holds. */
export function bucketProblems(want, decimals) {
  const out = [];
  if (!Array.isArray(want.buckets) || want.buckets.length === 0) return ['no buckets'];
  let sum = 0n;
  for (const b of want.buckets) {
    if (typeof b.bucket !== 'string' || !b.bucket) out.push('a bucket without a name');
    if (typeof b.tokens !== 'string' || !/^\d+$/.test(b.tokens)) out.push(`${b.bucket}: tokens is not a whole number`);
    else sum += BigInt(b.tokens);
  }
  const held = decimalToUnits(want.holds_tokens, decimals).units;
  if (sum * 10n ** BigInt(decimals) !== held) out.push(`the buckets sum to ${sum}, not the ${want.holds_tokens} the vault holds`);
  return out;
}
