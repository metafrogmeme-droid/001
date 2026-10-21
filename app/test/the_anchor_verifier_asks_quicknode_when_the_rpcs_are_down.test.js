'use strict';
/**
 * verifyAnchor (lib/root_anchor.js) has one more reader for a chain
 * QuickNode serves. Since 2026-10-21 a verdict needs two readers that agree
 * (lib/reader_agreement.js), so QuickNode is a SEAT, never the whole
 * bench: when an RPC fails to ANSWER, QuickNode is asked through Flovia, and
 * its reading must agree with the RPC that did answer, in the same block.
 * QuickNode alone is one reader and not a verdict. RPCs that both answer
 * are not second-guessed and QuickNode is not asked; a chain the registry
 * gives no QuickNode name (the DogeOS testnet) has no extra seat; with no
 * Flovia key the day stays unknown, each reader named with its class.
 */
delete process.env.DATABASE_URL;
const test = require('node:test');
const assert = require('node:assert');
const flovia = require('../lib/flovia');
const { verifyAnchor, verifyThroughQuickNode, payloadFor, chainInfo } = require('../lib/root_anchor');

const DAY = '2026-10-19';
const ROOT = 'ab'.repeat(32);
const TX = '0x' + 'cd'.repeat(32);
const FROM = '0x' + '11'.repeat(20);
const down = async () => { throw new Error('fetch failed'); };          // no RPC answers
const noSuch = async () => ({ ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, result: null }) });

let quick = {};     // method -> answer data
let lookups = [];
function reply(msg, data, isError) {
  return { status: 200, ok: true, headers: { get: (k) => (k === 'content-type' ? 'application/json' : k === 'mcp-session-id' ? 's' : null) },
    text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { isError: !!isError, content: [{ type: 'text', text: JSON.stringify(data) }] } }) };
}
flovia.setFetch(async (url, init) => {
  const msg = JSON.parse(init.body);
  if (msg.method === 'initialize') return { ...reply(msg, {}), text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2025-06-18' } }) };
  if (msg.method === 'notifications/initialized') return { status: 202, ok: true, headers: { get: () => null }, text: async () => '' };
  const a = msg.params.arguments; lookups.push(a);
  if (a.method in quick) return reply(msg, quick[a.method]);
  return reply(msg, { explanation: { meaning: 'refused' } }, true);
});
test.beforeEach(() => { lookups = []; process.env.FLOVIA_KEY = 'flv_test'; });

test('the registry names QuickNode for Base and for nothing else', () => {
  assert.equal(chainInfo('base').quicknode, 'base');
  assert.equal(chainInfo('dogeos-testnet').quicknode, undefined);
});

const BLOCK = '0x' + 'ee'.repeat(32);
const TS = '0x68f4a1c0';
const GOOD_TX = { hash: TX, from: FROM, to: FROM, input: payloadFor(DAY, ROOT), blockHash: BLOCK, blockNumber: '0x1a2b3c' };
/** RPCs by URL: a tx answer, or 'down'. Each answers eth_getTransactionByHash and eth_getBlockByHash. */
function rpcs(byUrl) {
  return async (url, init) => {
    const want = byUrl[url];
    if (want === undefined || want === 'down') throw new TypeError('fetch failed');
    const { method } = JSON.parse(init.body);
    const result = method === 'eth_getTransactionByHash' ? want
      : method === 'eth_getBlockByHash' ? { hash: want && want.blockHash, timestamp: TS } : null;
    return { ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  };
}
const MAIN = 'https://mainnet.base.org';
const PUBLICNODE = 'https://base-rpc.publicnode.com';
const quickSees = (tx) => ({ eth_getTransactionByHash: { result: tx }, eth_getBlockByNumber: { result: { number: '0x1a2b3c', timestamp: TS } } });

test('one RPC down, QuickNode takes its seat: verified only where it agrees with the RPC that answered', async () => {
  quick = quickSees(GOOD_TX);
  let v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: GOOD_TX }), 'base');
  assert.deepStrictEqual(v, { status: 'verified', block_time: new Date(0x68f4a1c0 * 1000).toISOString(), from: FROM,
    readers: ['base-rpc.publicnode.com', 'quicknode'] });
  assert.deepStrictEqual(lookups.map((l) => [l.chain, l.method, l.block]), [['base', 'eth_getTransactionByHash', undefined], ['base', 'eth_getBlockByNumber', 0x1a2b3c]]);
  assert.deepStrictEqual(lookups[0].params, { hash: TX });
  assert.deepStrictEqual(lookups[1].params, { header_only: true });

  // QuickNode sees no such transaction where the RPC sees the anchor: a disagreement, not a verdict.
  quick = { eth_getTransactionByHash: { result: null } };
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: GOOD_TX }), 'base');
  assert.deepStrictEqual([v.status, v.disagreement], ['unknown', true]);
  // QuickNode places it in another block: a reorg or a lie, not a verdict.
  quick = quickSees({ ...GOOD_TX, blockHash: '0x' + 'ab'.repeat(32) });
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: GOOD_TX }), 'base');
  assert.deepStrictEqual([v.status, v.disagreement], ['unknown', true]);
  // Both say there is no such transaction: that is a verdict.
  quick = { eth_getTransactionByHash: { result: null } };
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: null }), 'base');
  assert.deepStrictEqual([v.status, v.readers], ['mismatch', ['base-rpc.publicnode.com', 'quicknode']]);
});

test('one configured RPC and QuickNode are two readers: QuickNode fills the second seat', async () => {
  process.env.WEB3_RPC_URL_BASE_ANCHOR = PUBLICNODE;
  try {
    quick = quickSees(GOOD_TX);
    let v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [PUBLICNODE]: GOOD_TX }), 'base');
    assert.deepStrictEqual([v.status, v.readers], ['verified', ['base-rpc.publicnode.com', 'quicknode']]);
    quick = {};                                 // QuickNode refuses: one reader, not a verdict
    v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [PUBLICNODE]: GOOD_TX }), 'base');
    assert.strictEqual(v.status, 'unknown');
    assert.match(v.reason, /quicknode could not be read/);
  } finally { delete process.env.WEB3_RPC_URL_BASE_ANCHOR; }
});

test('every RPC down: QuickNode alone is one reader, and one reader is not a verdict', async () => {
  quick = quickSees(GOOD_TX);
  const v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /^1 of the 2 readers needed agree: /);
  assert.match(v.reason, /mainnet\.base\.org could not be read \(Error\)/);
  assert.equal(lookups.length, 2, 'it was asked, and its answer counted as one');
});

test('QuickNode\'s own reading: no such transaction, other calldata, or unmined', async () => {
  const chain = chainInfo('base');
  quick = { eth_getTransactionByHash: { result: null } };
  let v = await verifyThroughQuickNode(TX, DAY, ROOT, chain);
  assert.deepStrictEqual([v.status, v.source], ['mismatch', 'quicknode']);
  assert.match(v.reason, /no such transaction on Base/);
  quick = { eth_getTransactionByHash: { result: { ...GOOD_TX, input: '0xdeadbeef' } } };
  v = await verifyThroughQuickNode(TX, DAY, ROOT, chain);
  assert.deepStrictEqual([v.status, v.source], ['mismatch', 'quicknode']);
  assert.match(v.reason, /calldata/);
  quick = { eth_getTransactionByHash: { result: { ...GOOD_TX, blockHash: null, blockNumber: null } } };
  v = await verifyThroughQuickNode(TX, DAY, ROOT, chain);
  assert.deepStrictEqual([v.status, v.reason], ['unknown', 'transaction not yet mined']);
  quick = quickSees(GOOD_TX);
  v = await verifyThroughQuickNode(TX, DAY, ROOT, chain);
  assert.deepStrictEqual([v.status, v.same], ['verified', BLOCK], 'it says which block it saw');
});

test('RPCs that both answer are believed together; QuickNode is not asked', async () => {
  quick = quickSees(GOOD_TX);
  let v = await verifyAnchor(TX, DAY, ROOT, noSuch, 'base');
  assert.deepStrictEqual([v.status, v.readers], ['mismatch', ['mainnet.base.org', 'base-rpc.publicnode.com']]);
  assert.match(v.reason, /no such transaction on Base/);
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: GOOD_TX, [PUBLICNODE]: GOOD_TX }), 'base');
  assert.deepStrictEqual([v.status, v.readers], ['verified', ['mainnet.base.org', 'base-rpc.publicnode.com']]);
  // Two RPCs that disagree are not settled by a third: the day stays unknown.
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: GOOD_TX, [PUBLICNODE]: null }), 'base');
  assert.deepStrictEqual([v.status, v.disagreement], ['unknown', true]);
  assert.equal(lookups.length, 0);
});

test('no key, a refusal, or a chain without a QuickNode name: unknown, each reader named with its class', async () => {
  delete process.env.FLOVIA_KEY;
  let v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: GOOD_TX }), 'base');
  assert.deepStrictEqual([v.status, lookups.length], ['unknown', 0]);
  assert.match(v.reason, /^1 of the 2 readers needed agree: mainnet\.base\.org could not be read \(TypeError\)$/);
  process.env.FLOVIA_KEY = 'flv_test';
  quick = {};                                   // every method refused
  v = await verifyAnchor(TX, DAY, ROOT, rpcs({ [MAIN]: 'down', [PUBLICNODE]: GOOD_TX }), 'base');
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /quicknode could not be read/);
  assert.equal(lookups.length, 1, 'one ask, then the seat stays empty');
  lookups = [];
  quick = quickSees(GOOD_TX);
  v = await verifyAnchor(TX, DAY, ROOT, down, 'dogeos-testnet');
  assert.deepStrictEqual([v.status, lookups.length], ['unknown', 0], 'the DogeOS testnet has no extra seat');
});
