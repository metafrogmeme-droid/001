// The record publishes the treasury multisig, and states what the chain holds.
//
// token/config/rclaw.mainnet.json is the one record the website (/token) and the bot (/rclaw) read,
// and both print its multisig block as written: the vault and the multisig account, how many
// approvals move tokens, the time lock, and what the vault holds, split into the buckets of
// docs/TOKEN_ROADMAP.md section 4. Each is held here to the roadmap's allocation table and to the
// chain (a snapshot `npm run presale:multisig-verify -- --snapshot` took, run through the same
// check offline), and every check is shown to fail on the change it exists to catch.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  bucketProblems, decodeMultisig, decodeProposal, MULTISIG_DISCRIMINATOR, proposalAddress, timeLockText, vaultAddress,
} from './multisig_lib.mjs';
import { replaying, verifyMultisig } from './multisig_verify.mjs';
import { loadAllocation } from './roadmap_table.mjs';
import { exitCodeFor, loadTokenRecord } from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SNAPSHOT = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'squads_multisig_live.json'), 'utf8'));
const record = loadTokenRecord();
const ms = record.multisig;
const clone = (o) => JSON.parse(JSON.stringify(o));
const BASE58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const KEYS = ['_comment', 'verified_at', 'program', 'account', 'vault_index', 'vault', 'threshold', 'members',
  'time_lock_seconds', 'holds_tokens', 'buckets'];

function run({ answers = SNAPSHOT.answers, rec = record } = {}) {
  return verifyMultisig({ record: rec, rpcCall: replaying(answers) });
}

const statuses = async (opts) => Object.fromEntries((await run(opts)).rows.map((r) => [r.check, r.status]));
const failing = async (opts) => (await run(opts)).rows.filter((r) => r.status === 'FAIL').map((r) => r.check);

/** The snapshot's single answer to `method` whose first param is `first`; refuses zero or two. */
function answer(answers, method, first) {
  const hit = answers.filter((a) => a.method === method && (first === undefined || a.params[0] === first
    || (Array.isArray(a.params[0]) && first === '*')));
  assert.equal(hit.length, 1, `the snapshot holds ${hit.length} ${method} answers for ${first}`);
  return hit[0];
}

/** Rewrite the multisig account's bytes in a copy of the snapshot. */
function withMultisig(mutate) {
  const a = clone(SNAPSHOT.answers);
  const hit = answer(a, 'getAccountInfo', ms.account);
  const b = Buffer.from(hit.value.value.data[0], 'base64');
  mutate(b);
  hit.value.value.data[0] = b.toString('base64');
  return a;
}

/** Rewrite proposal #index's bytes in a copy of the snapshot (null removes the account). */
function withProposal(index, mutate) {
  const a = clone(SNAPSHOT.answers);
  const hit = answer(a, 'getMultipleAccounts', '*');
  const at = hit.params[0].indexOf(proposalAddress(ms.program, ms.account, index));
  assert.ok(at >= 0, `the snapshot did not read proposal #${index}`);
  if (mutate === null) hit.value.value[at] = null;
  else {
    const b = Buffer.from(hit.value.value[at].data[0], 'base64');
    mutate(b);
    hit.value.value[at].data[0] = b.toString('base64');
  }
  return a;
}

test('the record\'s multisig block has its fields, in order, of the shapes every reader expects', () => {
  assert.deepEqual(Object.keys(ms), KEYS);
  for (const k of ['program', 'account', 'vault']) assert.match(ms[k], BASE58, k);
  assert.ok(Number.isInteger(ms.vault_index) && ms.vault_index >= 0 && ms.vault_index <= 255);
  assert.ok(Array.isArray(ms.members) && ms.members.length >= 2);
  for (const m of ms.members) assert.match(m, BASE58);
  assert.deepEqual(ms.members, [...new Set(ms.members)].sort(), 'members sorted, each once');
  assert.ok(Number.isInteger(ms.threshold) && ms.threshold >= 2 && ms.threshold <= ms.members.length,
    'a threshold of 1 is a single key, not custody with approvals');
  assert.ok(Number.isInteger(ms.time_lock_seconds) && ms.time_lock_seconds > 0);
  assert.match(ms.holds_tokens, /^\d+$/);
  assert.ok(!ms.members.includes(ms.vault) && ms.account !== ms.vault, 'the vault is neither a member nor the multisig account');
});

test('the buckets are section 4\'s: treasury and partnerships whole, the reserve the remainder', () => {
  assert.deepEqual(bucketProblems(ms, record.decimals), []);
  const rows = loadAllocation().rows;
  const row = (label) => {
    const hit = rows.filter((r) => r.label === label);
    assert.equal(hit.length, 1, label);
    return hit[0];
  };
  const [treasury, partners, reserve] = ms.buckets;
  assert.equal(ms.buckets.length, 3);
  assert.equal(treasury.tokens, String(row(treasury.bucket).tokens), treasury.bucket);
  assert.equal(partners.tokens, String(row(partners.bucket).tokens), partners.bucket);
  const reserveRow = rows.filter((r) => r.label.startsWith(reserve.bucket));
  assert.equal(reserveRow.length, 1, `the reserve bucket names section 4's row: ${reserve.bucket}`);
  assert.ok(BigInt(reserve.tokens) <= BigInt(reserveRow[0].tokens), 'more reserve in the vault than section 4 allocates');

  const short = clone(ms);
  short.buckets[2].tokens = String(BigInt(short.buckets[2].tokens) - 1n);
  assert.deepEqual(bucketProblems(short, record.decimals), [`the buckets sum to ${BigInt(ms.holds_tokens) - 1n}, not the ${ms.holds_tokens} the vault holds`]);
});

test('the multisig the record names is on chain as the record says, in the snapshot', async () => {
  const { rows, context } = await run();
  assert.deepEqual(rows.map((r) => [r.check, r.status]), [
    ['RPC is mainnet-beta', 'PASS'],
    ['the Squads v4 program cannot be upgraded', 'PASS'],
    ['the multisig is owned by the Squads v4 program', 'PASS'],
    ['the approvals needed are the record\'s', 'PASS'],
    ['the members who can approve are the record\'s', 'PASS'],
    ['the time lock is the record\'s', 'PASS'],
    ['nobody can change the rules without a proposal', 'PASS'],
    ['the vault is the multisig\'s own', 'PASS'],
    ['the vault holds what the record states', 'PASS'],
    ['nobody else can move the vault\'s tokens', 'PASS'],
    ['no proposal is waiting to execute', 'PASS'],
  ]);
  assert.equal(exitCodeFor(rows), 0);
  assert.deepEqual(context, { account: ms.account, vault: ms.vault });
});

test('the date the pages print as "checked" is the snapshot\'s', () => {
  assert.equal(ms.verified_at, SNAPSHOT.readAt.slice(0, 10));
});

test('a changed threshold, time lock, member, config authority or owner fails, naming what changed', async () => {
  assert.deepEqual(await failing({ answers: withMultisig((b) => b.writeUInt16LE(1, 72)) }), ['the approvals needed are the record\'s']);
  assert.deepEqual(await failing({ answers: withMultisig((b) => b.writeUInt32LE(0, 74)) }), ['the time lock is the record\'s']);
  assert.deepEqual(await failing({ answers: withMultisig((b) => { b[100] ^= 1; }) }), ['the members who can approve are the record\'s']);
  // A member who keeps a seat but loses the vote: "2 of 3" would then be 2 of 2.
  assert.deepEqual(await failing({ answers: withMultisig((b) => b.writeUInt8(5, 132)) }), ['the members who can approve are the record\'s']);
  assert.deepEqual(await failing({ answers: withMultisig((b) => { b[40] = 7; }) }), ['nobody can change the rules without a proposal']);
  assert.deepEqual(await failing({ answers: withMultisig((b) => { b[0] ^= 1; }) }), ['the multisig account decodes as a Squads v4 Multisig']);

  const owner = clone(SNAPSHOT.answers);
  answer(owner, 'getAccountInfo', ms.account).value.value.owner = '11111111111111111111111111111111';
  assert.deepEqual(await failing({ answers: owner }), ['the multisig is owned by the Squads v4 program']);

  const gone = clone(SNAPSHOT.answers);
  answer(gone, 'getAccountInfo', ms.account).value.value = null;
  assert.deepEqual(await failing({ answers: gone }), ['the multisig account exists']);
});

test('a record that names another vault, member, threshold or time lock fails against the same chain', async () => {
  const rec = (edit) => { const r = clone(record); edit(r.multisig); return r; };
  assert.deepEqual(await failing({ rec: rec((m) => { m.vault_index = 1; }) }), ['the vault is the multisig\'s own']);
  assert.deepEqual(await failing({ rec: rec((m) => { m.members[0] = record.mint; }) }), ['the members who can approve are the record\'s']);
  assert.deepEqual(await failing({ rec: rec((m) => { m.threshold = 3; }) }), ['the approvals needed are the record\'s']);
  assert.deepEqual(await failing({ rec: rec((m) => { m.time_lock_seconds = 86400; }) }), ['the time lock is the record\'s']);
});

test('the vault: a balance other than the record\'s fails either way, and a delegate fails', async () => {
  const r = (edit) => { const x = clone(record); edit(x.multisig); return x; };
  assert.deepEqual(await failing({ rec: r((m) => { m.holds_tokens = String(BigInt(m.holds_tokens) + 1n); }) }), ['the vault holds what the record states']);
  assert.deepEqual(await failing({ rec: r((m) => { m.holds_tokens = String(BigInt(m.holds_tokens) - 1n); }) }), ['the vault holds what the record states']);

  const delegated = clone(SNAPSHOT.answers);
  answer(delegated, 'getTokenAccountsByOwner', ms.vault).value.value[0].account.data.parsed.info.delegate = record.mint;
  assert.deepEqual(await failing({ answers: delegated }), ['nobody else can move the vault\'s tokens']);

  const empty = clone(SNAPSHOT.answers);
  answer(empty, 'getTokenAccountsByOwner', ms.vault).value.value = [];
  assert.deepEqual(await failing({ answers: empty }), ['the vault holds what the record states']);

  // An entry that is not an RCLAW account the vault owns is named, not summed.
  for (const edit of [(i) => { i.owner = record.mint; }, (i) => { i.mint = ms.vault; }, (i) => { delete i.tokenAmount; }]) {
    const odd = clone(SNAPSHOT.answers);
    edit(answer(odd, 'getTokenAccountsByOwner', ms.vault).value.value[0].account.data.parsed.info);
    assert.deepEqual(await failing({ answers: odd }), ['the vault\'s token accounts are RCLAW accounts it owns']);
  }
});

test('an upgradeable Squads program fails: the approvals and the delay are only as strong as its code', async () => {
  const a = clone(SNAPSHOT.answers);
  const hit = a.find((x) => x.method === 'getAccountInfo' && x.params[1]?.dataSlice?.length === 45);
  assert.ok(hit, 'the snapshot holds the ProgramData head');
  const b = Buffer.from(hit.value.value.data[0], 'base64');
  assert.equal(b[12], 0, 'the snapshot was taken with no upgrade authority');
  b[12] = 1;
  const head = Buffer.alloc(45);
  b.copy(head, 0, 0, Math.min(b.length, 45));
  head.fill(9, 13, 45);
  hit.value.value.data[0] = head.toString('base64');
  assert.deepEqual(await failing({ answers: a }), ['the Squads v4 program cannot be upgraded']);
});

test('an open proposal is a WARN that names it, not a pass and not a mismatch', async () => {
  const last = Number(decodeMultisig(Buffer.from(answer(SNAPSHOT.answers, 'getAccountInfo', ms.account).value.value.data[0], 'base64')).transactionIndex);
  assert.ok(last >= 1);
  const approved = withProposal(last, (b) => b.writeUInt8(3, 48));
  const s = await statuses({ answers: approved });
  assert.equal(s[`proposal #${last} is open`], 'WARN');
  assert.ok(!('no proposal is waiting to execute' in s));
  assert.equal(exitCodeFor((await run({ answers: approved })).rows), 0);

  // Active: open while it is newer than the stale index, dead once a config change made it stale.
  const active = withProposal(last, (b) => b.writeUInt8(1, 48));
  const stale = decodeMultisig(Buffer.from(answer(active, 'getAccountInfo', ms.account).value.value.data[0], 'base64')).staleTransactionIndex;
  const fresh = (await statuses({ answers: active }))[`proposal #${last} is open`];
  assert.equal(fresh === 'WARN', BigInt(last) > stale, `#${last} against stale index ${stale}`);
  const notStale = withMultisig((b) => b.writeBigUInt64LE(0n, 86));
  const p = answer(notStale, 'getMultipleAccounts', '*');
  const at = p.params[0].indexOf(proposalAddress(ms.program, ms.account, last));
  const pb = Buffer.from(p.value.value[at].data[0], 'base64');
  pb.writeUInt8(1, 48);
  p.value.value[at].data[0] = pb.toString('base64');
  assert.equal((await statuses({ answers: notStale }))[`proposal #${last} is open`], 'WARN');

  const closed = withProposal(last, null);
  const row = (await run({ answers: closed })).rows.find((r) => r.check === 'no proposal is waiting to execute');
  assert.equal(row.status, 'PASS');
  assert.match(row.detail, new RegExp(`closed #${last}`));
});

test('another chain answers nothing; an answer not read is UNVERIFIED, never a pass', async () => {
  const other = clone(SNAPSHOT.answers);
  answer(other, 'getGenesisHash').value = 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';
  assert.deepEqual((await run({ answers: other })).rows.map((r) => r.status), ['FAIL']);

  const noVault = SNAPSHOT.answers.filter((a) => a.method !== 'getTokenAccountsByOwner');
  const rows = (await run({ answers: noVault })).rows;
  assert.equal(rows.find((r) => r.check === 'the vault\'s RCLAW').status, 'UNVERIFIED');
  assert.ok(!rows.some((r) => r.check === 'the vault holds what the record states'));
  assert.equal(exitCodeFor(rows), 3);

  const noProposals = SNAPSHOT.answers.filter((a) => a.method !== 'getMultipleAccounts');
  const r2 = (await run({ answers: noProposals })).rows;
  assert.equal(r2.find((r) => r.check === 'the proposals').status, 'UNVERIFIED');
  assert.ok(!r2.some((r) => r.check === 'no proposal is waiting to execute'), 'proposals not read are not "none open"');
});

test('the decoders read the snapshot\'s bytes and refuse what is not theirs', () => {
  const buf = Buffer.from(answer(SNAPSHOT.answers, 'getAccountInfo', ms.account).value.value.data[0], 'base64');
  const m = decodeMultisig(buf);
  assert.equal(m.threshold, ms.threshold);
  assert.equal(m.timeLockSeconds, ms.time_lock_seconds);
  assert.equal(m.configAuthority, null);
  assert.deepEqual(m.members.map((x) => x.key).sort(), ms.members);
  assert.ok(buf.subarray(0, 8).equals(MULTISIG_DISCRIMINATOR));
  assert.throws(() => decodeMultisig(buf.subarray(0, 120)), /does not fit/);
  assert.throws(() => decodeProposal(buf), /not a Squads v4 Proposal/);
  assert.equal(vaultAddress(ms.program, ms.account, ms.vault_index), ms.vault);

  const p = answer(SNAPSHOT.answers, 'getMultipleAccounts', '*');
  const prop = decodeProposal(Buffer.from(p.value.value[0].data[0], 'base64'));
  assert.equal(prop.multisig, ms.account);
  assert.equal(prop.transactionIndex, 1n);
  assert.ok(prop.approved.length >= ms.threshold, 'an executed proposal carries its approvals');
  assert.throws(() => decodeMultisig(Buffer.from(p.value.value[0].data[0], 'base64')), /not a Squads v4 Multisig/);
});

test('the time lock reads as the pages print it', () => {
  assert.equal(timeLockText(172800), '48 hours');
  assert.equal(timeLockText(5400), '5400 seconds');
  assert.equal(timeLockText(0), 'none');
});
