'use strict';
/**
 * A day's seal root, put on three mainnets (the owner's go, 21 October 2026),
 * none of them by a key of ours:
 *
 *   EAS on Base   a visitor's wallet calls timestamp(root) on the Ethereum
 *                 Attestation Service; the server reads the stamp back from
 *                 EAS (getTimestamp) before recording it.
 *   Bitcoin       the server POSTs the statement's hash to OpenTimestamps
 *                 calendars (no signature, no transaction of ours) and keeps
 *                 the proof; a height is written only after the server read
 *                 that block's header and it is six deep.
 *   Dogecoin      the owner's wallet sends an OP_RETURN from Dogecoin Core;
 *                 the server reads the transaction back.
 *
 * Fixtures are real: a Base transaction that called EAS's timestamp(), the
 * Bitcoin header of block 358391 and the OpenTimestamps project's own
 * example proof for it, four calendars' answers to a submitted digest, and
 * a Dogecoin transaction with an OP_RETURN output (app/test/fixtures/mainnet_anchors,
 * each file saying where and when it was read). Every network read is
 * stubbed, and every method the server sends is recorded: none of them may
 * send a transaction.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-'.repeat(4);

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const authModule = require('../auth');
const seal = require('../lib/seal_roots');
const anchor = require('../lib/root_anchor');
const doge = require('../lib/dogecoin_anchor');
const ots = require('../lib/opentimestamps');
const rootOts = require('../lib/root_ots');
const { keccak256, toUtf8Bytes } = require('../lib/keccak256');
const { otsCell } = require('../public/js/anchor_cell.js');
const { stamp } = require('../public/js/roots-stamp.js');

const FIX = path.join(__dirname, 'fixtures', 'mainnet_anchors');
const fixture = (f) => JSON.parse(fs.readFileSync(path.join(FIX, f), 'utf8'));
const EAS_FIX = fixture('eas_base_stamp.json');
const BTC_BLOCK = fixture('bitcoin_block_358391.json');
const DOGE_TX = fixture('dogecoin_op_return_tx.json');

// ── a stand-in for every outside service, recording what it was asked ────────

const asked = [];
const realFetch = global.fetch;
const reply = (status, body, bytes) => ({
  ok: status >= 200 && status < 300, status,
  json: async () => (typeof body === 'string' ? JSON.parse(body) : body),
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  arrayBuffer: async () => { const b = bytes || Buffer.from(typeof body === 'string' ? body : JSON.stringify(body)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.length); },
});

/** An EVM JSON-RPC that knows `world.txs`, receipts, blocks and EAS stamps. */
function evm(world) {
  return async (url, opts) => {
    if (world.down) throw new TypeError('fetch failed');
    const { method, params } = JSON.parse(opts.body);
    asked.push({ url, method });
    let result = null;
    if (method === 'eth_getTransactionByHash') result = world.txs[params[0]] || null;
    else if (method === 'eth_getTransactionReceipt') result = world.receipts[params[0]] || null;
    else if (method === 'eth_getBlockByHash') result = world.blocks[params[0]] || null;
    else if (method === 'eth_call') {
      assert.strictEqual(params[0].to.toLowerCase(), anchor.EAS_BASE.toLowerCase());
      assert.ok(params[0].data.startsWith(anchor.EAS_GET_TIMESTAMP), 'only getTimestamp is ever called');
      const root = params[0].data.slice(10);
      result = '0x' + BigInt(world.stamps[root] || 0).toString(16).padStart(64, '0');
    } else throw new Error(`not modelled: ${method}`);
    return reply(200, { jsonrpc: '2.0', id: 1, result });
  };
}

/** The real Base transaction, as the RPC answers it. */
function easWorld() {
  return {
    txs: { [EAS_FIX.tx.hash]: { ...EAS_FIX.tx } },
    receipts: { [EAS_FIX.tx.hash]: { ...EAS_FIX.receipt } },
    blocks: { [EAS_FIX.block.hash]: { ...EAS_FIX.block } },
    stamps: { [EAS_FIX.root]: BigInt(EAS_FIX.getTimestamp) },
  };
}

// ── EAS on Base ──────────────────────────────────────────────────────────────

test('the EAS selectors are the keccak256 of their signatures, and the plan is a timestamp(root) call to EAS', () => {
  const sel = (sig) => keccak256(toUtf8Bytes(sig)).slice(0, 10);
  assert.strictEqual(anchor.EAS_TIMESTAMP, sel('timestamp(bytes32)'));
  assert.strictEqual(anchor.EAS_GET_TIMESTAMP, sel('getTimestamp(bytes32)'));
  const c = anchor.chainInfo('base-eas');
  assert.strictEqual(c.chain_id, 8453);
  assert.strictEqual(c.testnet, false);
  assert.strictEqual(c.eas, '0x4200000000000000000000000000000000000021', 'the OP Stack predeploy');
  const pub = anchor.publicChains().find((x) => x.key === 'base-eas');
  assert.strictEqual(pub.method, 'eas_timestamp');
  assert.strictEqual(pub.eas, c.eas);
  assert.strictEqual(anchor.publicChains().find((x) => x.key === 'base').method, 'calldata');
  const root = 'ab'.repeat(32);
  const plan = anchor.buildAnchorPlan('2026-10-20', root, 'base-eas');
  assert.strictEqual(plan.to, c.eas);
  assert.strictEqual(plan.data, '0x4d003070' + root);
  assert.strictEqual(plan.value, '0');
  assert.strictEqual(plan.dry_run, true);
});

test('a real Base EAS stamp verifies; any other root, contract, outcome or time does not', async () => {
  const { tx, root } = { tx: EAS_FIX.tx.hash, root: EAS_FIX.root };
  const day = '2026-10-21';
  let w = easWorld();
  let v = await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas');
  assert.deepStrictEqual(v, { status: 'verified', block_time: '2026-10-21T15:30:17.000Z', from: EAS_FIX.tx.from });

  v = await anchor.verifyAnchor(tx, day, 'cd'.repeat(32), evm(w), 'base-eas');
  assert.strictEqual(v.status, 'mismatch', 'the transaction stamps another root');

  w = easWorld(); w.txs[tx].to = '0x' + '11'.repeat(20);
  assert.strictEqual((await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas')).status, 'mismatch', 'not a call to EAS');

  w = easWorld(); w.receipts[tx].status = '0x0';
  v = await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas');
  assert.strictEqual(v.status, 'mismatch', 'a reverted call stamped nothing');
  assert.match(v.reason, /reverted/);

  w = easWorld(); w.stamps[root] = BigInt(EAS_FIX.block.timestamp) - 60n;
  v = await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas');
  assert.strictEqual(v.status, 'mismatch', 'EAS holds an earlier stamp: this transaction is not the one');

  w = easWorld(); w.stamps[root] = 0n;
  assert.strictEqual((await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas')).status, 'unknown', 'EAS silent is not a verdict');

  w = easWorld(); w.txs[tx].blockHash = null;
  assert.strictEqual((await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas')).status, 'unknown');

  w = easWorld(); w.down = true;
  assert.strictEqual((await anchor.verifyAnchor(tx, day, root, evm(w), 'base-eas')).status, 'unknown');
});

test('EAS is read by the root alone: stamped, unstamped, or could not be read', async () => {
  const w = easWorld();
  assert.deepStrictEqual(await anchor.easStampOf(EAS_FIX.root, evm(w)), { status: 'stamped', stamped_at: '2026-10-21T15:30:17.000Z' });
  assert.deepStrictEqual(await anchor.easStampOf('cd'.repeat(32), evm(w)), { status: 'unstamped' });
  w.down = true;
  assert.strictEqual((await anchor.easStampOf(EAS_FIX.root, evm(w))).status, 'unknown');
  assert.strictEqual((await anchor.easStampOf(EAS_FIX.root, evm(w), 'base')).status, 'unknown', 'Base by calldata is not an EAS chain');
});

// ── Dogecoin ─────────────────────────────────────────────────────────────────

test('the Dogecoin payload fits an OP_RETURN, where the EVM chains\' text would not', () => {
  const day = '2026-10-20';
  const root = 'ab'.repeat(32);
  assert.strictEqual(Buffer.from(anchor.payloadFor(day, root).slice(2), 'hex').length, 83, 'why Dogecoin has its own form');
  const hex = doge.payloadHex(day, root);
  assert.strictEqual(hex.length / 2, 48);
  assert.ok(hex.length / 2 <= doge.MAX_OP_RETURN_BYTES);
  assert.strictEqual(Buffer.from(hex, 'hex').subarray(0, 16).toString('utf8'), `RCD1:${day}:`);
  assert.strictEqual(hex.slice(32), root);
  const plan = anchor.buildAnchorPlan(day, root, 'dogecoin');
  assert.strictEqual(plan.data, hex);
  assert.ok(plan.instructions.some((l) => l.includes(`createrawtransaction "[]" "{\\"data\\":\\"${hex}\\"}"`)));
  assert.strictEqual(anchor.chainInfo('dogecoin').reorg_depth, 6);
});

function dogeFetch(answer) {
  return async (url, opts) => {
    asked.push({ url, method: (opts && opts.method) || 'GET' });
    assert.ok(url.startsWith(doge.TX_API), url);
    return answer(url.slice(doge.TX_API.length));
  };
}

test('a Dogecoin anchor verifies only with exactly the payload, once, six blocks deep', async () => {
  const day = '2026-10-20';
  const root = 'ab'.repeat(32);
  const id = DOGE_TX.hash;
  const withPayload = (over = {}) => ({ ...DOGE_TX, ...over,
    outputs: DOGE_TX.outputs.map((o) => (o.script_type === 'null-data' ? { ...o, data_hex: doge.payloadHex(day, root) } : o)) });

  // The real transaction carries someone else's bytes.
  let v = await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, DOGE_TX)), 'dogecoin');
  assert.strictEqual(v.status, 'mismatch');

  // The other arm: the same transaction carrying this day's payload, deep enough.
  v = await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, withPayload({ confirmations: 6 }))), 'dogecoin');
  assert.deepStrictEqual(v, { status: 'verified', block_time: new Date(DOGE_TX.confirmed).toISOString(),
    from: DOGE_TX.inputs[0].addresses[0] });

  v = await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, withPayload({ confirmations: 5 }))), 'dogecoin');
  assert.strictEqual(v.status, 'unknown', 'one block short is still short');
  assert.match(v.reason, /5 of the 6 confirmations/);

  v = await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, withPayload({ confirmations: 0, block_height: -1 }))), 'dogecoin');
  assert.strictEqual(v.status, 'unknown');

  // The payload buried in a longer push is not this anchor: exact bytes only.
  const buried = withPayload({ confirmations: 9 });
  buried.outputs = buried.outputs.map((o) => (o.script_type === 'null-data' ? { ...o, data_hex: '00' + o.data_hex } : o));
  assert.strictEqual((await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, buried)), 'dogecoin')).status, 'mismatch');

  const twice = withPayload({ confirmations: 9 });
  twice.outputs = [...twice.outputs, twice.outputs.find((o) => o.script_type === 'null-data')];
  assert.strictEqual((await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(200, twice)), 'dogecoin')).status, 'mismatch');

  assert.strictEqual((await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(404, { error: 'not found' })), 'dogecoin')).status, 'mismatch');
  assert.strictEqual((await anchor.verifyAnchor(id, day, root, dogeFetch(() => reply(429, { error: 'limit' })), 'dogecoin')).status, 'unknown');
  assert.strictEqual((await anchor.verifyAnchor(id, day, root, dogeFetch(() => { throw new TypeError('fetch failed'); }), 'dogecoin')).status, 'unknown');
  assert.strictEqual((await anchor.verifyAnchor('0x' + id, day, root, dogeFetch(() => reply(200, DOGE_TX)), 'dogecoin')).status, 'mismatch',
    'a Dogecoin id has no 0x');
});

// ── Bitcoin through OpenTimestamps ───────────────────────────────────────────

const HELLO_OTS = fs.readFileSync(path.join(FIX, 'hello-world.txt.ots'));
const CAL_HOSTS = ['a.pool.opentimestamps.org', 'b.pool.opentimestamps.org', 'a.pool.eternitywall.com', 'ots.btc.catallaxy.com'];
const PROBE_DIGEST = crypto.createHash('sha256').update('runeclaw ots probe 2026-10-21').digest();

/** Bitcoin header APIs that know `blocks` (height -> { id, merkle_root, timestamp }) and `tip`. */
function headers(world) {
  return async (url) => {
    asked.push({ url, method: 'GET' });
    if (world.down) throw new TypeError('fetch failed');
    let m = /\/blocks\/tip\/height$/.exec(url);
    if (m) return reply(200, String(world.tip));
    m = /\/block-height\/(\d+)$/.exec(url);
    if (m) { const b = world.blocks[Number(m[1])]; return b ? reply(200, b.id) : reply(404, 'Block not found'); }
    m = /\/block\/([0-9a-f]{64})$/.exec(url);
    if (m) {
      const b = Object.values(world.blocks).find((x) => x.id === m[1]);
      return b ? reply(200, { id: b.id, height: b.height, merkle_root: b.merkle_root, timestamp: b.timestamp }) : reply(404, 'Block not found');
    }
    return null;
  };
}

test('the OpenTimestamps project\'s own proof reads back byte for byte and leads to block 358391', async () => {
  const f = ots.parseProofFile(HELLO_OTS);
  assert.ok(f.digest.equals(crypto.createHash('sha256').update(fs.readFileSync(path.join(FIX, 'hello-world.txt'))).digest()));
  assert.ok(ots.writeProofFile(f.digest, f.timestamp).equals(HELLO_OTS), 'written back exactly as read');
  const world = { blocks: { 358391: BTC_BLOCK }, tip: 958391 };
  let v = await ots.verifyBitcoin(f.timestamp, { fetchImpl: headers(world) });
  assert.deepStrictEqual(v, { status: 'verified', height: 358391, block_time: new Date(BTC_BLOCK.timestamp * 1000).toISOString() });
  // Another Merkle root at that height: the proof does not lead there.
  const other = { blocks: { 358391: { ...BTC_BLOCK, merkle_root: '00'.repeat(32) } }, tip: 958391 };
  v = await ots.verifyBitcoin(f.timestamp, { fetchImpl: headers(other) });
  assert.strictEqual(v.status, 'mismatch');
  v = await ots.verifyBitcoin(f.timestamp, { fetchImpl: headers({ down: true, blocks: {} }) });
  assert.strictEqual(v.status, 'unknown', 'no header read is not a verdict');
  // A proof with no Bitcoin attestation yet is pending, and asks nothing.
  const calendar = ots.parseCalendarAnswer(fs.readFileSync(path.join(FIX, `calendar_${CAL_HOSTS[0]}.bin`)), PROBE_DIGEST);
  const before = asked.length;
  assert.deepStrictEqual(await ots.verifyBitcoin(calendar, { fetchImpl: headers(world) }), { status: 'pending' });
  assert.strictEqual(asked.length, before);
});

/**
 * Calendars that answer the submitted digest with the recorded answers, and,
 * once `world.mined` is set, the rest of the path from each pending
 * commitment: one SHA-256, then a Bitcoin attestation at `world.height`. The
 * header world is built to match, so the proof leads to that block.
 */
function calendars(world) {
  return async (url, opts) => {
    asked.push({ url, method: (opts && opts.method) || 'GET' });
    const host = new URL(url).host;
    if (/\/digest$/.test(url)) {
      if (world.refuse) return reply(503, 'busy');
      const at = CAL_HOSTS.indexOf(host);
      // The recorded answers were made for PROBE_DIGEST; for another digest,
      // a calendar answers with one append and a pending attestation.
      if (at >= 0 && Buffer.from(opts.body).equals(PROBE_DIGEST)) {
        return reply(200, null, fs.readFileSync(path.join(FIX, `calendar_${host}.bin`)));
      }
      const t = { msg: Buffer.from(opts.body), attestations: [], ops: [] };
      const op = { tag: 0xf0, name: 'append', arg: Buffer.from(host) };
      t.ops.push({ op, stamp: { msg: ots.applyOp(op, t.msg), attestations: [{ kind: 'pending', uri: `https://${host}` }], ops: [] } });
      return reply(200, null, ots.writeTimestamp(t));
    }
    const m = /\/timestamp\/([0-9a-f]+)$/.exec(url);
    if (m) {
      if (!world.mined) return reply(404, 'Pending confirmation in Bitcoin blockchain');
      // Each calendar reaches its own block: the first at world.height, the
      // others after it, as calendars that commit independently do.
      const height = world.height + Math.max(0, CAL_HOSTS.indexOf(host));
      const commitment = Buffer.from(m[1], 'hex');
      const op = { tag: 0x08, name: 'sha256' };
      const leaf = ots.applyOp(op, commitment);
      world.blocks[height] = { id: crypto.createHash('sha256').update(leaf).digest('hex'), height,
        merkle_root: Buffer.from(leaf).reverse().toString('hex'), timestamp: 1792000000 };
      const t = { msg: commitment, attestations: [], ops: [{ op, stamp: { msg: leaf, attestations: [{ kind: 'bitcoin', height }], ops: [] } }] };
      return reply(200, null, ots.writeTimestamp(t));
    }
    return headers(world)(url, opts);
  };
}

test('a digest submitted to the calendars comes back pending at each, and none accepting is an error', async () => {
  const world = { blocks: {} };
  const { timestamp, calendars: got } = await ots.submit(PROBE_DIGEST, { fetchImpl: calendars(world) });
  assert.deepStrictEqual(got, ots.CALENDARS.slice().sort());
  const pending = ots.attestationsOf(timestamp).filter((x) => x.attestation.kind === 'pending').map((x) => x.attestation.uri).sort();
  assert.deepStrictEqual(pending, ['https://alice.btc.calendar.opentimestamps.org', 'https://bob.btc.calendar.opentimestamps.org',
    'https://btc.calendar.catallaxy.com', 'https://finney.calendar.eternitywall.com']);
  // Every POST carried exactly the digest: a hash, nothing to sign.
  for (const a of asked.filter((x) => x.method === 'POST' && /\/digest$/.test(x.url))) assert.ok(ots.CALENDARS.some((c) => a.url === `${c}/digest`));
  await assert.rejects(ots.submit(PROBE_DIGEST, { fetchImpl: calendars({ refuse: true, blocks: {} }) }), ots.OtsError);
});

// ── the job, the table and the routes ────────────────────────────────────────

let server; let base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); global.fetch = realFetch; });

function request(method, p, { token, body, raw } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, { method, headers: {
      accept: 'application/json', ...(data ? { 'content-type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}) } }, (res) => {
      const chunks = []; res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        resolve({ status: res.statusCode, headers: res.headers, raw: buf,
          data: raw ? null : (buf.length && /json/.test(res.headers['content-type'] || '') ? JSON.parse(buf.toString('utf8')) : {}) });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

let seq = 0;
async function newUser() {
  const bcrypt = require('bcryptjs');
  const email = `mainnet${Date.now()}_${seq++}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, await bcrypt.hash('longenoughpassword1', 4)]);
  const [rows] = await pool.execute('SELECT id, token_epoch FROM users WHERE email = ?', [email]);
  return { id: rows[0].id, token: authModule.signToken({ id: rows[0].id, email, token_epoch: rows[0].token_epoch }) };
}

const DAY = seal.dayOf(new Date(Date.now() - 2 * 24 * 3600 * 1000));
const ROOT = EAS_FIX.root;   // so the real Base stamp is this day's

test('the job submits a day to Bitcoin, completes it only once the block is six deep, and the routes serve the proof', async () => {
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [DAY, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  const world = { blocks: {}, height: 900000, tip: 900002 };
  const f = calendars(world);
  global.fetch = f;

  let out = await rootOts.runOnce({ fetchImpl: f });
  assert.strictEqual(out.stamped, 1);
  let row = await rootOts.rowFor(DAY);
  assert.strictEqual(row.root, ROOT);
  assert.strictEqual(row.bitcoin_height, null);
  let r = await request('GET', `/api/roots/ots/${DAY}`);
  assert.strictEqual(r.data.status, 'pending');
  assert.strictEqual(r.data.statement, `RCROOT1:${DAY}:${ROOT}`);

  // The calendars reach Bitcoin; the block is only three deep.
  world.mined = true;
  out = await rootOts.runOnce({ fetchImpl: f });
  assert.strictEqual(out.upgraded, 0);
  row = await rootOts.rowFor(DAY);
  assert.strictEqual(row.bitcoin_height, null, 'no height written before six confirmations');
  assert.ok(ots.attestationsOf(ots.parseProofFile(Buffer.from(row.proof, 'base64')).timestamp).some((x) => x.attestation.kind === 'bitcoin'),
    'the completed path is kept');

  world.tip = world.height + 5;
  out = await rootOts.runOnce({ fetchImpl: f });
  assert.strictEqual(out.upgraded, 1);
  row = await rootOts.rowFor(DAY);
  assert.strictEqual(Number(row.bitcoin_height), 900000);

  r = await request('GET', `/api/roots/ots/${DAY}`);
  assert.strictEqual(r.data.status, 'verified');
  assert.strictEqual(r.data.bitcoin_height, 900000);
  const feed = await request('GET', '/api/roots');
  assert.deepStrictEqual(feed.data.roots.find((x) => x.day === DAY).ots, { submitted: true, bitcoin_height: 900000 });

  // The downloads are the proof and the exact bytes it timestamps.
  const proof = await request('GET', `/api/roots/ots/${DAY}/proof`, { raw: true });
  assert.strictEqual(proof.status, 200);
  assert.match(proof.headers['content-disposition'], new RegExp(`RCROOT1-${DAY}\\.txt\\.ots`));
  const statement = await request('GET', `/api/roots/ots/${DAY}/statement`, { raw: true });
  assert.strictEqual(statement.raw.toString('utf8'), `RCROOT1:${DAY}:${ROOT}`);
  assert.ok(ots.parseProofFile(proof.raw).digest.equals(crypto.createHash('sha256').update(statement.raw).digest()));

  // A stored proof of another statement is caught, not shown as this day's.
  const other = { ...row, root: 'ee'.repeat(32) };
  assert.strictEqual((await rootOts.checkProof(other, { fetchImpl: f })).status, 'mismatch');
  assert.strictEqual((await rootOts.checkProof({ ...row, proof: 'bm90IGEgcHJvb2Y=' }, { fetchImpl: f })).status, 'unknown',
    'an unreadable proof is not a verdict');
  // The block matches, but the tip cannot be read: no depth, so no verdict.
  assert.strictEqual((await rootOts.checkProof(row, { fetchImpl: headers({ ...world, tip: null }) })).status, 'unknown');
  assert.strictEqual((await rootOts.checkProof(row, { fetchImpl: headers(world) })).status, 'verified', 'the other arm');

  // A second pass never resubmits a day it holds.
  const posts = asked.filter((x) => /\/digest$/.test(x.url)).length;
  await rootOts.runOnce({ fetchImpl: f });
  assert.strictEqual(asked.filter((x) => /\/digest$/.test(x.url)).length, posts);
});

test('the EAS stamp is planned only while EAS has none, and recorded only after EAS holds it', async () => {
  const w = easWorld();
  w.stamps = {};
  global.fetch = evm(w);
  let plan = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=base-eas`);
  assert.strictEqual(plan.status, 200);
  assert.strictEqual(plan.data.to, anchor.EAS_BASE);
  assert.strictEqual(plan.data.data, '0x4d003070' + ROOT);

  Object.assign(w, easWorld());
  plan = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=base-eas`);
  assert.deepStrictEqual(plan.data, { already_stamped: true, day: DAY, root: ROOT, chain: 'base-eas', stamped_at: '2026-10-21T15:30:17.000Z' });

  w.down = true;
  plan = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=base-eas`);
  assert.strictEqual(plan.status, 503, 'unreadable is not "unstamped"');
  w.down = false;

  const u = await newUser();
  const ok = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: EAS_FIX.tx.hash, chain: 'base-eas' } });
  assert.strictEqual(ok.status, 200, JSON.stringify(ok.data));
  const v = await request('GET', `/api/roots/verify/${DAY}?chain=base-eas`);
  assert.strictEqual(v.data.status, 'verified');
  assert.strictEqual(v.data.method, 'eas_timestamp');
  assert.strictEqual(v.data.explorer, 'https://basescan.org/tx/' + EAS_FIX.tx.hash);
});

test('nothing the server asked any chain, calendar or explorer sends a transaction', () => {
  assert.ok(asked.length > 20, 'the stand-ins were exercised');
  for (const a of asked) {
    assert.ok(!/^eth_send/i.test(a.method), `${a.method} sent to ${a.url}`);
    assert.ok(!/sendrawtransaction|\/txs\/push|broadcast/i.test(a.url), `${a.url} broadcasts`);
    if (a.method === 'POST' && !/^eth_/.test(a.method)) assert.ok(/\/digest$/.test(a.url), `POST to ${a.url}`);
  }
});

// ── the page ─────────────────────────────────────────────────────────────────

test('the Bitcoin cell is green only on the live check\'s word', () => {
  const T = (k, en) => en;
  assert.match(otsCell({ submitted: false, bitcoin_height: null }, null, T, DAY), /not yet in Bitcoin/);
  assert.match(otsCell({ submitted: true, bitcoin_height: null }, null, T, DAY), /waiting for a block/);
  // Our row names a height; until the live check agrees it is not green.
  const recorded = otsCell({ submitted: true, bitcoin_height: 9 }, null, T, DAY);
  assert.doesNotMatch(recorded, /var\(--up\)/);
  assert.match(recorded, /not reachable to confirm/);
  const green = otsCell({ submitted: true, bitcoin_height: 9 }, { status: 'verified', bitcoin_height: 9 }, T, DAY);
  assert.match(green, /color:var\(--up\)[^>]*>₿ in Bitcoin block 9/);
  assert.match(green, new RegExp(`/api/roots/ots/${DAY}/proof`));
  assert.match(otsCell({ submitted: true }, { status: 'mismatch' }, T, DAY), /var\(--down\)/);
  assert.match(otsCell(null, null, T, DAY), /not reachable to confirm/, 'an unreadable line is not "not yet"');
});

test('the stamp button sends exactly EAS\'s timestamp(root) from the visitor\'s wallet, and nothing for a plan that points elsewhere', async () => {
  const chain = anchor.publicChains().find((c) => c.key === 'base-eas');
  const sent = [];
  const eth = { request: async ({ method, params }) => { sent.push({ method, params }); return method === 'eth_sendTransaction' ? '0x' + '5'.repeat(64) : null; } };
  const wallet = { account: async () => '0x' + 'a'.repeat(40), switchTo: async (e, c) => { sent.push({ method: 'switch', params: [c.chain_id] }); } };
  const plan = { status: 200, body: { to: chain.eas, data: '0x4d003070' + ROOT, value: '0' } };
  const posts = [];
  const answers = [{ status: 503, body: { reason: 'transaction not yet mined' } }, { status: 200, body: { ok: true } }];
  let out = await stamp(DAY, { eth, wallet, chain, retryMs: 0, getJSON: async () => plan,
    postJSON: async (u, b) => { posts.push(b); return answers.shift(); } });
  assert.deepStrictEqual(out, { state: 'recorded', tx: '0x' + '5'.repeat(64) });
  assert.deepStrictEqual(sent.find((x) => x.method === 'eth_sendTransaction').params,
    [{ from: '0x' + 'a'.repeat(40), to: chain.eas, data: '0x4d003070' + ROOT, value: '0x0' }]);
  assert.deepStrictEqual(sent.find((x) => x.method === 'switch').params, [8453]);
  assert.strictEqual(posts.length, 2, 'retried while Base had not confirmed it');
  assert.deepStrictEqual(posts[1], { day: DAY, tx_hash: '0x' + '5'.repeat(64), chain: 'base-eas' });

  // Signed out: stamped on Base all the same, just not in the feed yet.
  out = await stamp(DAY, { eth, wallet, chain, retryMs: 0, getJSON: async () => plan, postJSON: async () => ({ status: 401, body: {} }) });
  assert.strictEqual(out.state, 'stamped_unrecorded');

  // Already stamped: the wallet is never asked to pay for a revert.
  sent.length = 0;
  out = await stamp(DAY, { eth, wallet, chain, getJSON: async () => ({ status: 200, body: { already_stamped: true, stamped_at: 'x' } }),
    postJSON: async () => assert.fail('nothing to record') });
  assert.strictEqual(out.state, 'already');
  assert.ok(!sent.some((x) => x.method === 'eth_sendTransaction'));

  // A plan naming another contract, or another call, sends nothing.
  for (const body of [{ to: '0x' + '66'.repeat(20), data: '0x4d003070' + ROOT }, { to: chain.eas, data: '0xa9059cbb' + ROOT }]) {
    sent.length = 0;
    await assert.rejects(stamp(DAY, { eth, wallet, chain, getJSON: async () => ({ status: 200, body }), postJSON: async () => assert.fail() }));
    assert.ok(!sent.some((x) => x.method === 'eth_sendTransaction'), JSON.stringify(body));
  }
});
