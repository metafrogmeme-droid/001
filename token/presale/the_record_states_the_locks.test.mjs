// The record publishes the locks, and states what the plan sets and the chain holds.
//
// token/config/rclaw.mainnet.json is the one record the website (/token) and the bot (/rclaw)
// read, and both print each lock as the record writes it: its schedule, every unlock, the lock
// account, the wallet that claims and the certificate link. Those are claims about accounts
// nobody can edit or cancel, so each is held twice here: to the plan (locks.plan.json, through
// recordLocks) and to the chain (a snapshot `npm run presale:locks-verify -- --snapshot` took,
// run through the same check offline).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { decodeVesting, loadLocksPlan, recordLocks, VESTING_DISCRIMINATOR } from './locks_lib.mjs';
import { replaying, verifyLocks } from './locks_verify.mjs';
import { loadAllocation } from './roadmap_table.mjs';
import { exitCodeFor, loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SNAPSHOT = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'smithii_locks_live.json'), 'utf8'));
const plan = loadLocksPlan();
const cfg = loadSmithiiConfig();
const table = loadAllocation();
const record = loadTokenRecord();
const clone = (o) => JSON.parse(JSON.stringify(o));
const BASE58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const CREATED = ['vesting_account', 'claim_wallet', 'certificate_url'];
const readAtS = Math.floor(Date.parse(SNAPSHOT.readAt) / 1000);

function run({ answers = SNAPSHOT.answers, rec = record, nowS = readAtS } = {}) {
  return verifyLocks({ plan, cfg, table, record: rec, rpcCall: replaying(answers), nowS });
}

/** The snapshot's answer for an account, to tamper with; refuses an address it does not hold. */
function answerFor(answers, address) {
  const hit = answers.filter((a) => a.method === 'getAccountInfo' && a.params[0] === address);
  assert.equal(hit.length, 1, `the snapshot holds ${hit.length} answers for ${address}`);
  return hit[0];
}

const created = () => record.locks.items.filter((i) => i.vesting_account !== null);

test('the record lists every planned lock, once, with exactly the plan\'s terms', () => {
  const want = recordLocks(plan, { cfg, rows: table, record });
  const items = record.locks.items;
  assert.deepEqual(items.map((i) => i.bucket), want.map((w) => w.bucket), 'the record lists the plan\'s locks in the plan\'s order');
  for (const [i, w] of want.entries()) {
    const got = items[i];
    assert.deepEqual(Object.keys(got), [...Object.keys(w), 'vesting_account', 'claim_wallet'], `${w.bucket}: the keys, in order`);
    for (const k of ['bucket', 'tokens', 'schedule', 'unlocks']) {
      assert.deepEqual(got[k], w[k], `${w.bucket}: ${k} differs from locks.plan.json; the record should carry ${JSON.stringify(w[k])}, then run node app/scripts/sync_content.js`);
    }
  }
});

test('a lock is either created, with all three of its created fields, or not, with none', () => {
  for (const item of record.locks.items) {
    const nulls = CREATED.filter((k) => item[k] === null);
    assert.ok(nulls.length === 0 || nulls.length === CREATED.length, `${item.bucket}: ${nulls.join(', ')} null and the rest set`);
    if (nulls.length) continue;
    assert.match(item.vesting_account, BASE58, `${item.bucket}: vesting_account`);
    assert.match(item.claim_wallet, BASE58, `${item.bucket}: claim_wallet`);
    const planned = recordLocks(plan, { cfg, rows: table, record }).find((w) => w.bucket === item.bucket);
    assert.equal(item.certificate_url, planned.certificate_url, `${item.bucket}: the certificate is the one the tool gives the plan's slug`);
  }
  assert.ok(created().length >= 2, 'the Community and Team locks were created on 2026-10-13');
});

test('every lock the record names is on chain as the record and the plan say, in the snapshot', async () => {
  const { rows } = await run();
  const failed = rows.filter((r) => r.status !== 'PASS' && r.status !== 'UNVERIFIED');
  assert.deepEqual(failed, [], 'a row did not pass');
  // The only rows not read are the locks the record says are not created yet.
  const pending = record.locks.items.filter((i) => i.vesting_account === null).map((i) => `${i.bucket}: the lock`);
  assert.deepEqual(rows.filter((r) => r.status === 'UNVERIFIED').map((r) => r.check), pending);
  assert.equal(exitCodeFor(rows), pending.length ? 3 : 0);
  for (const item of created()) {
    const mine = rows.filter((r) => r.check.startsWith(`${item.bucket}: `));
    assert.equal(mine.length, 10, `${item.bucket}: ${mine.length} rows`);
    assert.ok(mine.every((r) => r.status === 'PASS'), `${item.bucket}: ${JSON.stringify(mine)}`);
  }
  for (const check of ['the vesting program\'s upgrade authority is the one disclosed', 'the vesting program has not been redeployed since it was measured']) {
    assert.equal(rows.find((r) => r.check === check)?.status, 'PASS', check);
  }
});

test('the date the pages print as "checked" is the snapshot\'s', () => {
  assert.equal(record.locks.verified_at, SNAPSHOT.readAt.slice(0, 10));
});

test('a changed unlock, creator, amount or claiming wallet fails, naming the lock', async () => {
  const item = created()[created().length - 1];
  const name = item.bucket;
  const bytes = (answers) => Buffer.from(answerFor(answers, item.vesting_account).value.value.data[0], 'base64');
  const put = (answers, buf) => { answerFor(answers, item.vesting_account).value.value.data[0] = buf.toString('base64'); };
  const failing = async (opts) => (await run(opts)).rows.filter((r) => r.status === 'FAIL').map((r) => r.check);

  let a = clone(SNAPSHOT.answers);
  let b = bytes(a);
  b.writeUInt8(b.readUInt8(108) + 1, 108); // the first step's percent
  put(a, b);
  assert.deepEqual(await failing({ answers: a }), [`${name}: the unlocks are the plan's`]);

  a = clone(SNAPSHOT.answers);
  b = bytes(a);
  b.writeBigInt64LE(b.readBigInt64LE(100) - 3600n, 100); // the first step an hour early
  put(a, b);
  assert.deepEqual(await failing({ answers: a }), [`${name}: the unlocks are the plan's`]);

  a = clone(SNAPSHOT.answers);
  b = bytes(a);
  b[8] ^= 1; // the creator
  put(a, b);
  assert.deepEqual(await failing({ answers: a }), [`${name}: the claiming wallet is the one recorded`]);

  a = clone(SNAPSHOT.answers);
  b = bytes(a);
  b.writeBigUInt64LE(b.readBigUInt64LE(72) - 1n, 72);
  put(a, b);
  assert.deepEqual(await failing({ answers: a }), [`${name}: the amount is the bucket's`]);

  const rec = clone(record);
  rec.locks.items.find((i) => i.bucket === name).claim_wallet = record.mint;
  assert.deepEqual(await failing({ rec }), [`${name}: the claiming wallet is the one recorded`]);

  a = clone(SNAPSHOT.answers);
  answerFor(a, item.vesting_account).value.value.owner = '11111111111111111111111111111111';
  assert.deepEqual(await failing({ answers: a }), [`${name}: the lock is owned by the vesting program`]);

  a = clone(SNAPSHOT.answers);
  b = bytes(a);
  b[0] ^= 1;
  put(a, b);
  assert.deepEqual(await failing({ answers: a }), [`${name}: the lock account decodes as a Vesting`]);
});

test('the vault: short of the amount fails before the first unlock and is a WARN after it, never a PASS', async () => {
  const item = created()[0];
  const name = item.bucket;
  const { context } = await run();
  const vault = context.find((c) => c.bucket === name).vault;
  const short = clone(SNAPSHOT.answers);
  const info = answerFor(short, vault).value.value.data.parsed.info;
  info.tokenAmount.amount = String(BigInt(info.tokenAmount.amount) - 1n);
  const row = async (nowS) => (await run({ answers: short, nowS })).rows.find((r) => r.check === `${name}: the vault holds the whole amount`).status;
  const first = Date.parse(item.unlocks[0].at) / 1000;
  assert.equal(await row(first - 1), 'FAIL');
  assert.equal(await row(first), 'WARN');

  const delegated = clone(SNAPSHOT.answers);
  answerFor(delegated, vault).value.value.data.parsed.info.delegate = record.mint;
  const rows = (await run({ answers: delegated })).rows;
  assert.equal(rows.find((r) => r.check === `${name}: nobody else can move the vault's tokens`).status, 'FAIL');

  const gone = clone(SNAPSHOT.answers);
  answerFor(gone, vault).value.value = null;
  assert.equal((await run({ answers: gone })).rows.find((r) => r.check === `${name}: the vault exists`).status, 'FAIL');
});

test('another chain answers nothing; an answer not read is UNVERIFIED, never a pass', async () => {
  const other = clone(SNAPSHOT.answers);
  other.find((a) => a.method === 'getGenesisHash').value = 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';
  const { rows } = await run({ answers: other });
  assert.deepEqual(rows.map((r) => r.status), ['FAIL']);

  const item = created()[0];
  const missing = SNAPSHOT.answers.filter((a) => !(a.method === 'getAccountInfo' && a.params[0] === item.vesting_account));
  const r2 = (await run({ answers: missing })).rows;
  assert.equal(r2.find((r) => r.check === `${item.bucket}: the lock account`).status, 'UNVERIFIED');
  assert.equal(exitCodeFor(r2), 3);
});

test('a redeployed program, or a new upgrade authority, fails', async () => {
  const pdAnswer = SNAPSHOT.answers.find((a) => a.method === 'getAccountInfo' && a.params[1]?.dataSlice?.length === 45);
  assert.ok(pdAnswer, 'the snapshot holds the ProgramData head');
  const at = (mutate) => {
    const a = clone(SNAPSHOT.answers);
    const hit = a.find((x) => x.method === 'getAccountInfo' && x.params[1]?.dataSlice?.length === 45);
    const b = Buffer.from(hit.value.value.data[0], 'base64');
    mutate(b);
    hit.value.value.data[0] = b.toString('base64');
    return a;
  };
  const failing = async (answers) => (await run({ answers })).rows.filter((r) => r.status === 'FAIL').map((r) => r.check);
  assert.deepEqual(await failing(at((b) => b.writeBigUInt64LE(b.readBigUInt64LE(4) + 1n, 4))),
    ['the vesting program has not been redeployed since it was measured']);
  assert.deepEqual(await failing(at((b) => { b[13] ^= 1; })),
    ['the vesting program\'s upgrade authority is the one disclosed']);
});

test('decodeVesting reads the snapshot\'s bytes and refuses what is not a Vesting', () => {
  const item = created()[0];
  const buf = Buffer.from(answerFor(SNAPSHOT.answers, item.vesting_account).value.value.data[0], 'base64');
  const v = decodeVesting(buf);
  assert.equal(v.mint, record.mint);
  assert.equal(v.creator, item.claim_wallet);
  assert.deepEqual(v.steps.map((s) => s.percent), item.unlocks.map((u) => u.percent));
  assert.ok(buf.subarray(0, 8).equals(VESTING_DISCRIMINATOR));
  assert.throws(() => decodeVesting(buf.subarray(0, 99)), /at least 100 bytes/);
  const many = Buffer.from(buf);
  many.writeUInt32LE(1000, 96);
  assert.throws(() => decodeVesting(many), /do not fit/);
  const none = Buffer.from(buf);
  none.writeUInt32LE(0, 96);
  assert.throws(() => decodeVesting(none), /0 steps/);
});
