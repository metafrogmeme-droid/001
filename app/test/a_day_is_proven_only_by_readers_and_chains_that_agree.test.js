'use strict';
/**
 * Two layers of agreement before a day's root is called proven.
 *
 * READERS (lib/reader_agreement.js). A chain is read through RPCs and
 * explorer APIs, and one of them can be down, lag, or lie. Every anchor
 * check (Base calldata, Base EAS, the DogeOS testnet, Dogecoin, Bitcoin
 * headers) asks every reader it has and needs two that give the same
 * verdict in the same block, with none contradicting; less is "unknown".
 * The April 2026 bridge loss this answers was a verifier fed by one set of
 * poisoned RPCs (docs/lessons, the chapter on proof by agreement).
 *
 * CHAINS (lib/anchor_quorum.js). A day is "multi-anchored" when at least two
 * witnesses in different independence groups verify it live. A calldata
 * anchor and an EAS stamp on Base are one witness; a testnet is shown and
 * never counted; a witness that could not be read makes the quorum unknown
 * only where it could have changed the answer.
 *
 * Each rule is driven both ways, and the routes end to end with the chains
 * stubbed at fetch: GET /api/roots/quorum/:day, and /verify/:day caching a
 * verdict but never an unknown.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-'.repeat(4);
delete process.env.DATABASE_URL;
delete process.env.FLOVIA_KEY;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const seals = require('../lib/seal_roots');
const { agree, requiredOf } = require('../lib/reader_agreement');
const { quorumOf, QUORUM_K } = require('../lib/anchor_quorum');
const { payloadFor, publicChains, verifyAnchor, BASE_RPCS } = require('../lib/root_anchor');
const doge = require('../lib/dogecoin_anchor');
const ots = require('../lib/opentimestamps');
const rootOts = require('../lib/root_ots');
const fs = require('node:fs');
const path = require('node:path');
const { DOGEOS_TESTNET_RPCS } = require('../lib/root_anchor');

// ── the readers ──────────────────────────────────────────────────────────────

const V = (reader, same = 'b1', extra = {}) => ({ reader, status: 'verified', same, block_time: 't', from: 'f', ...extra });
const M = (reader) => ({ reader, status: 'mismatch', reason: 'no such transaction' });
const U = (reader) => ({ reader, status: 'unread', reason: `${reader} could not be read (TypeError)` });
const P = (reader) => ({ reader, status: 'unknown', reason: '3 of the 6 confirmations Dogecoin needs' });

test('two readers that agree are a verdict; one, or two that disagree, is not', () => {
  assert.strictEqual(requiredOf(2), 2);
  assert.strictEqual(requiredOf(3), 2, 'two of three, not all three: one reader down does not blank a day');
  assert.strictEqual(requiredOf(1), 1, 'one configured reader is all there is');
  assert.strictEqual(requiredOf(0), 1);

  assert.deepStrictEqual(agree([V('a'), V('b')], 2), { status: 'verified', block_time: 't', from: 'f', readers: ['a', 'b'] });
  assert.deepStrictEqual(agree([M('a'), M('b')], 2), { status: 'mismatch', reason: 'no such transaction', readers: ['a', 'b'] });
  // The other arm of each: one short.
  assert.strictEqual(agree([V('a'), U('b')], 2).status, 'unknown');
  assert.match(agree([V('a'), U('b')], 2).reason, /^1 of the 2 readers needed agree: b could not be read \(TypeError\)$/);
  assert.strictEqual(agree([M('a'), U('b')], 2).status, 'unknown', 'one reader saying "no such transaction" may be one lying');
  // Contradiction is never settled by counting.
  assert.deepStrictEqual(agree([V('a'), M('b'), V('c')], 2), { status: 'unknown', reason: 'the readers disagree', disagreement: true });
  assert.deepStrictEqual(agree([V('a', 'b1'), V('b', 'b2')], 2, 'the X readers'),
    { status: 'unknown', reason: 'the X readers place it in different blocks', disagreement: true });
  // A reader that answered "not yet" names the wait; two of three agreeing still decide.
  assert.deepStrictEqual(agree([V('a'), P('b')], 2), { status: 'unknown', reason: '3 of the 6 confirmations Dogecoin needs' });
  assert.strictEqual(agree([V('a'), P('b'), V('c')], 2).status, 'verified');
  assert.strictEqual(agree([U('a'), U('b')], 2).reason, 'a could not be read (TypeError); b could not be read (TypeError)');
});

// ── the chains ───────────────────────────────────────────────────────────────

const W = (key, group, status, counts = true) => ({ key, group, status, counts });

test('a day is multi-anchored only by independent chains, and never by a testnet', () => {
  assert.strictEqual(QUORUM_K, 2);
  let q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('bitcoin', 'bitcoin', 'verified')]);
  assert.deepStrictEqual([q.status, q.groups], ['multi_anchored', ['bitcoin', 'ethereum-l2']]);
  assert.strictEqual(q.meaning, 'time-proven, not performance');
  // The same chain twice is one witness.
  q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('base-eas', 'ethereum-l2', 'verified')]);
  assert.deepStrictEqual([q.status, q.groups], ['short', ['ethereum-l2']]);
  // A testnet anchor is shown and not counted.
  q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('dogeos-testnet', 'dogeos-testnet', 'verified', false)]);
  assert.strictEqual(q.status, 'short');
  // Dogecoin and Bitcoin, no Base at all.
  assert.strictEqual(quorumOf([W('dogecoin', 'dogecoin', 'verified'), W('bitcoin', 'bitcoin', 'verified')]).status, 'multi_anchored');
});

test('an unreadable witness makes the quorum unknown only where it could change the answer', () => {
  let q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('dogecoin', 'dogecoin', 'unknown')]);
  assert.deepStrictEqual([q.status, q.unreadable], ['unknown', ['dogecoin']]);
  // Unreadable in a group already verified: it could add nothing.
  q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('base-eas', 'ethereum-l2', 'unknown')]);
  assert.deepStrictEqual([q.status, q.unreadable], ['short', []]);
  // Even if it read verified, one is not two: measured short.
  q = quorumOf([W('dogecoin', 'dogecoin', 'unknown'), W('bitcoin', 'bitcoin', 'not_submitted')]);
  assert.strictEqual(q.status, 'short');
  // Waiting for depth is listed, not counted, and not "could not be read".
  q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('bitcoin', 'bitcoin', 'pending')]);
  assert.deepStrictEqual([q.status, q.pending, q.unreadable], ['short', ['bitcoin'], []]);
  // A recorded anchor the chain now refuses is said, whatever the count.
  q = quorumOf([W('base', 'ethereum-l2', 'verified'), W('bitcoin', 'bitcoin', 'verified'), W('dogecoin', 'dogecoin', 'mismatch')]);
  assert.deepStrictEqual([q.status, q.mismatched], ['multi_anchored', ['dogecoin']]);
});

test('every chain in the registry says what it is independent of, and whether it counts', () => {
  const by = Object.fromEntries(publicChains().map((c) => [c.key, c]));
  assert.deepStrictEqual(
    Object.keys(by).sort().map((k) => [k, by[k].independence_group, by[k].counts_toward_quorum]),
    [['base', 'ethereum-l2', true], ['base-eas', 'ethereum-l2', true], ['dogecoin', 'dogecoin', true],
      ['dogeos-testnet', 'dogeos-testnet', false]]);
});

// ── Bitcoin's header sources ─────────────────────────────────────────────────

const FIX = path.join(__dirname, 'fixtures', 'mainnet_anchors');
const HELLO = ots.parseProofFile(fs.readFileSync(path.join(FIX, 'hello-world.txt.ots')));
const BLOCK = JSON.parse(fs.readFileSync(path.join(FIX, 'bitcoin_block_358391.json'), 'utf8'));
const bytes = (status, body) => {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return { status, ok: status === 200, arrayBuffer: async () => Buffer.from(text), text: async () => text };
};
/** Header sources by host: { block, tip } or 'down'. */
function headerSources(byHost) {
  return async (url) => {
    const src = byHost[new URL(url).hostname];
    if (!src || src === 'down') throw new TypeError('fetch failed');
    if (/\/blocks\/tip\/height$/.test(url)) return bytes(200, String(src.tip));
    if (/\/block-height\/\d+$/.test(url)) return bytes(200, src.block.id);
    if (/\/block\/[0-9a-f]{64}$/.test(url)) {
      const b = src.block;
      return bytes(200, { id: b.id, height: b.height, merkle_root: b.merkle_root, timestamp: b.timestamp });
    }
    return bytes(404, 'not found');
  };
}
const APIS = ['https://one.example/api', 'https://two.example/api'];

test('a Bitcoin header is believed when two sources agree, and one lying or down source is not enough', async () => {
  const good = { block: BLOCK, tip: 958391 };
  const lie = { block: { ...BLOCK, merkle_root: '00'.repeat(32) }, tip: 958391 };
  let v = await ots.verifyBitcoin(HELLO.timestamp, { fetchImpl: headerSources({ 'one.example': good, 'two.example': good }), apis: APIS });
  assert.deepStrictEqual([v.status, v.height, v.readers], ['verified', 358391, ['one.example', 'two.example']]);
  v = await ots.verifyBitcoin(HELLO.timestamp, { fetchImpl: headerSources({ 'one.example': good, 'two.example': 'down' }), apis: APIS });
  assert.strictEqual(v.status, 'unknown');
  assert.match(v.reason, /^1 of the 2 readers needed agree: two\.example could not be read \(TypeError\)$/);
  v = await ots.verifyBitcoin(HELLO.timestamp, { fetchImpl: headerSources({ 'one.example': good, 'two.example': lie }), apis: APIS });
  assert.deepStrictEqual([v.status, v.disagreement], ['unknown', true]);
  v = await ots.verifyBitcoin(HELLO.timestamp, { fetchImpl: headerSources({ 'one.example': lie, 'two.example': lie }), apis: APIS });
  assert.strictEqual(v.status, 'mismatch', 'both read the header, and it is not this proof\'s');
});

test('the Bitcoin tip is the lowest the sources agree on, and none when too few answer', async () => {
  const at = (tip) => ({ block: BLOCK, tip });
  assert.strictEqual(await rootOts.bitcoinTip(headerSources({ 'blockstream.info': at(900005), 'mempool.space': at(900000) })), 900000,
    'a source that reports a higher tip cannot make a block look deeper');
  assert.strictEqual(await rootOts.bitcoinTip(headerSources({ 'blockstream.info': at(900005), 'mempool.space': 'down' })), null);
});

// ── the routes ───────────────────────────────────────────────────────────────

let server; let base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

const get = (p) => new Promise((resolve, reject) => {
  http.get(base + p, { headers: { accept: 'application/json' } }, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
  }).on('error', reject);
});

const DAY = seals.dayOf(new Date(Date.now() - 5 * 24 * 3600 * 1000));
const ROOT = '5'.repeat(64);
const TX_BASE = '0x' + '4'.repeat(64);
const TX_DOGE = '3'.repeat(64);
const DOGE_BLOCK = '2'.repeat(64);

/** The world the stub serves: which readers are up, and what Dogecoin's readers see. */
const world = { baseUp: { [BASE_RPCS[0]]: true, [BASE_RPCS[1]]: true }, evmDay: DAY, dogeDay: DAY, dogeConfirmations: 20, sochainBlock: DOGE_BLOCK };
const TX_DOGEOS = '0x' + '6'.repeat(64);
/** An EVM chain's answer to one JSON-RPC call: the anchor `tx`, carrying world.evmDay's payload. */
function evmAnswer(opts, tx, blockHash) {
  const { method, params } = JSON.parse(opts.body);
  const result = method === 'eth_getTransactionByHash'
    ? (params[0] === tx ? { hash: tx, blockHash, blockNumber: '0x10', input: payloadFor(world.evmDay, ROOT), from: '0xba5e' } : null)
    : method === 'eth_getBlockByHash' ? { timestamp: '0x68f4d2c2' }
      : method === 'eth_blockNumber' ? '0x1000'
        : method === 'eth_getBlockByNumber' ? { number: '0x1' } : null;
  return json(200, { jsonrpc: '2.0', id: 1, result });
}
const realFetch = global.fetch;
const json = (status, body) => ({ ok: status === 200, status, json: async () => body, text: async () => JSON.stringify(body) });
test.before(() => {
  global.fetch = async (url, opts) => {
    if (BASE_RPCS.includes(url)) {
      if (!world.baseUp[url]) throw new TypeError('fetch failed');
      return evmAnswer(opts, TX_BASE, '0xb10c');
    }
    if (DOGEOS_TESTNET_RPCS.includes(url)) return evmAnswer(opts, TX_DOGEOS, '0xd0e5');
    const payload = doge.payloadHex(world.dogeDay, ROOT);
    if (url === doge.TX_API + TX_DOGE) {
      return json(200, { hash: TX_DOGE, block_hash: DOGE_BLOCK, block_height: 5000000, confirmations: world.dogeConfirmations,
        confirmed: '2026-10-16T12:00:00Z', inputs: [{ addresses: ['DPayer'] }],
        outputs: [{ script_type: 'null-data', data_hex: payload }] });
    }
    if (url === `https://sochain.com/api/v3/transaction/DOGE/${TX_DOGE}`) {
      return json(200, { status: 'success', data: { hash: TX_DOGE, block_hash: world.sochainBlock, block_height: 5000000,
        confirmations: world.dogeConfirmations, time: Date.parse('2026-10-16T12:00:00Z') / 1000, inputs: [{ address: 'DPayer' }],
        outputs: [{ type: 'nulldata', script: { hex: '6a' + (payload.length / 2).toString(16).padStart(2, '0') + payload } }] } });
    }
    throw new TypeError(`unexpected fetch ${url}`);
  };
});
test.after(() => { global.fetch = realFetch; });

test('the quorum route checks every recorded anchor live and counts independent chains only', async () => {
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [DAY, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  // No anchor and no Bitcoin timestamp: short, with nothing unreadable.
  let r = await get(`/api/roots/quorum/${DAY}`);
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual([r.body.status, r.body.groups, r.body.anchors, r.body.bitcoin.status], ['short', [], [], 'not_submitted']);

  await seals.recordAnchor(DAY, 'base', TX_BASE, { block_time: '2026-10-16T12:00:00Z', from: '0xba5e' });
  await seals.recordAnchor(DAY, 'dogecoin', TX_DOGE, { block_time: '2026-10-16T12:00:00Z', from: 'DPayer' });
  r = await get(`/api/roots/quorum/${DAY}`);
  assert.strictEqual(r.body.status, 'multi_anchored');
  assert.deepStrictEqual(r.body.groups, ['dogecoin', 'ethereum-l2']);
  assert.strictEqual(r.body.meaning, 'time-proven, not performance');
  const byChain = Object.fromEntries(r.body.anchors.map((a) => [a.chain, a]));
  assert.deepStrictEqual(byChain.base.readers, ['mainnet.base.org', 'base-rpc.publicnode.com']);
  assert.deepStrictEqual(byChain.dogecoin.readers, ['blockcypher', 'sochain']);
  assert.strictEqual(byChain.dogecoin.independence_group, 'dogecoin');
  assert.ok(!/\$|_usd|amount/i.test(JSON.stringify(r.body)), 'counts and chain facts only');

  const missing = await get('/api/roots/quorum/2001-01-01');
  assert.strictEqual(missing.status, 404);
});

test('a day whose Dogecoin readers disagree is not multi-anchored, and says so', async () => {
  const day = seals.dayOf(new Date(Date.now() - 6 * 24 * 3600 * 1000));
  // Its own day, so no answer cached for the day above stands in for this one.
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [day, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  await seals.recordAnchor(day, 'base', TX_BASE, { block_time: '2026-10-16T12:00:00Z', from: '0xba5e' });
  await seals.recordAnchor(day, 'dogecoin', TX_DOGE, { block_time: '2026-10-16T12:00:00Z', from: 'DPayer' });
  world.sochainBlock = 'f'.repeat(64);
  world.dogeDay = day;
  try {
    // The Base anchor is in another day's payload here, so Base reads mismatch for this day;
    // Dogecoin's readers place the transaction in two blocks.
    const r = await get(`/api/roots/quorum/${day}`);
    const d = r.body.anchors.find((a) => a.chain === 'dogecoin');
    assert.deepStrictEqual([d.status, d.disagreement], ['unknown', true]);
    assert.notStrictEqual(r.body.status, 'multi_anchored');
  } finally { world.sochainBlock = DOGE_BLOCK; world.dogeDay = DAY; }
});

test('through the route, a testnet anchor is shown and never counted', async () => {
  const day = seals.dayOf(new Date(Date.now() - 8 * 24 * 3600 * 1000));
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [day, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  await seals.recordAnchor(day, 'base', TX_BASE, { block_time: '2026-10-13T12:00:00Z', from: '0xba5e' });
  await seals.recordAnchor(day, 'dogeos-testnet', TX_DOGEOS, { block_time: '2026-10-13T12:00:00Z', from: '0xba5e' });
  world.evmDay = day;
  try {
    const r = await get(`/api/roots/quorum/${day}`);
    const testnet = r.body.anchors.find((a) => a.chain === 'dogeos-testnet');
    assert.deepStrictEqual([testnet.status, testnet.counts_toward_quorum], ['verified', false]);
    assert.deepStrictEqual([r.body.status, r.body.groups], ['short', ['ethereum-l2']]);
  } finally { world.evmDay = DAY; }
});

test('a configured RPC is named by its place in a verdict, never by its URL: a URL can carry a key', async () => {
  process.env.WEB3_RPC_URL_BASE_ANCHOR = 'https://base.example/v2/SECRETKEY,https://mainnet.base.org';
  try {
    const down = async (url, opts) => {
      if (url.includes('SECRETKEY')) throw new TypeError(`fetch failed: ${url}`);
      return evmAnswer(opts, TX_BASE, '0xb10c');
    };
    let v = await verifyAnchor(TX_BASE, DAY, ROOT, down, 'base');
    assert.strictEqual(v.status, 'unknown');
    assert.match(v.reason, /configured rpc 1 could not be read \(TypeError\)/);
    assert.doesNotMatch(JSON.stringify(v), /SECRETKEY|base\.example/);
    v = await verifyAnchor(TX_BASE, DAY, ROOT, async (url, opts) => evmAnswer(opts, TX_BASE, '0xb10c'), 'base');
    assert.deepStrictEqual([v.status, v.readers], ['verified', ['configured rpc 1', 'mainnet.base.org']]);
  } finally { delete process.env.WEB3_RPC_URL_BASE_ANCHOR; }
});

test('the verify route keeps a verdict and never an unknown', async () => {
  const day = seals.dayOf(new Date(Date.now() - 7 * 24 * 3600 * 1000));
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [day, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  await seals.recordAnchor(day, 'dogecoin', TX_DOGE, { block_time: '2026-10-16T12:00:00Z', from: 'DPayer' });
  const before = world.dogeConfirmations;
  world.dogeConfirmations = 3;
  world.dogeDay = day;
  try {
    let r = await get(`/api/roots/verify/${day}?chain=dogecoin`);
    assert.deepStrictEqual([r.body.status, r.body.reason], ['unknown', '3 of the 6 confirmations Dogecoin needs']);
    world.dogeConfirmations = 20;
    r = await get(`/api/roots/verify/${day}?chain=dogecoin`);
    assert.strictEqual(r.body.status, 'verified', 'the unknown was not kept');
    assert.deepStrictEqual(r.body.readers, ['blockcypher', 'sochain']);
    world.dogeConfirmations = 3;
    r = await get(`/api/roots/verify/${day}?chain=dogecoin`);
    assert.strictEqual(r.body.status, 'verified', 'the verdict was kept');
  } finally { world.dogeConfirmations = before; world.dogeDay = DAY; }
});
