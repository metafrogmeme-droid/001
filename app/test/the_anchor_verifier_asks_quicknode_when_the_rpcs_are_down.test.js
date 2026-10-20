'use strict';
/**
 * verifyAnchor (lib/root_anchor.js) has one more reader for a chain
 * QuickNode serves: when every RPC in the registry fails to ANSWER, it asks
 * QuickNode through Flovia for the transaction and its block, and verifies
 * the same calldata. The verdict says which reader gave it. An RPC that
 * answers "no such transaction" is an answer, and the fallback is not asked;
 * a chain the registry gives no QuickNode name (the DogeOS testnet) has no
 * fallback; and with no Flovia key the day stays unknown with the RPCs' own
 * reason.
 */
delete process.env.DATABASE_URL;
const test = require('node:test');
const assert = require('node:assert');
const flovia = require('../lib/flovia');
const { verifyAnchor, payloadFor, chainInfo } = require('../lib/root_anchor');

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

test('RPCs down, QuickNode answers: verified, from QuickNode, with the block time', async () => {
  quick = {
    eth_getTransactionByHash: { result: { hash: TX, from: FROM, to: FROM, input: payloadFor(DAY, ROOT), blockHash: '0x' + 'ee'.repeat(32), blockNumber: '0x1a2b3c' } },
    eth_getBlockByNumber: { result: { number: '0x1a2b3c', timestamp: '0x68f4a1c0' } },
  };
  const v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.equal(v.status, 'verified');
  assert.equal(v.source, 'quicknode');
  assert.equal(v.from, FROM);
  assert.equal(v.block_time, new Date(0x68f4a1c0 * 1000).toISOString());
  assert.deepStrictEqual(lookups.map((l) => [l.chain, l.method, l.block]), [['base', 'eth_getTransactionByHash', undefined], ['base', 'eth_getBlockByNumber', 0x1a2b3c]]);
  assert.deepStrictEqual(lookups[0].params, { hash: TX });
  assert.deepStrictEqual(lookups[1].params, { header_only: true });
});

test('QuickNode answers no such transaction, or other calldata: a mismatch from QuickNode', async () => {
  quick = { eth_getTransactionByHash: { result: null } };
  let v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.deepStrictEqual([v.status, v.source], ['mismatch', 'quicknode']);
  assert.match(v.reason, /no such transaction on Base/);
  quick = { eth_getTransactionByHash: { result: { hash: TX, from: FROM, input: '0xdeadbeef', blockHash: '0x' + 'ee'.repeat(32), blockNumber: '0x10' } } };
  v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.deepStrictEqual([v.status, v.source], ['mismatch', 'quicknode']);
  assert.match(v.reason, /calldata/);
  quick = { eth_getTransactionByHash: { result: { hash: TX, from: FROM, input: payloadFor(DAY, ROOT), blockHash: null, blockNumber: null } } };
  v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.deepStrictEqual([v.status, v.reason], ['unknown', 'transaction not yet mined']);
});

test('an RPC that answers is believed; QuickNode is not asked', async () => {
  quick = { eth_getTransactionByHash: { result: { hash: TX, from: FROM, input: payloadFor(DAY, ROOT), blockHash: '0x' + 'ee'.repeat(32), blockNumber: '0x10' } } };
  const v = await verifyAnchor(TX, DAY, ROOT, noSuch, 'base');
  assert.equal(v.status, 'mismatch');
  assert.match(v.reason, /no such transaction on Base/);
  assert.equal(v.source, undefined, 'the RPCs\' own verdict');
  assert.equal(lookups.length, 0);
});

test('no key, a refusal, or a chain without a QuickNode name: unknown with the RPCs\' reason', async () => {
  delete process.env.FLOVIA_KEY;
  let v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.deepStrictEqual([v.status, v.reason, lookups.length], ['unknown', 'fetch failed', 0]);
  process.env.FLOVIA_KEY = 'flv_test';
  quick = {};                                   // every method refused
  v = await verifyAnchor(TX, DAY, ROOT, down, 'base');
  assert.deepStrictEqual([v.status, v.reason], ['unknown', 'fetch failed']);
  assert.equal(lookups.length, 1, 'one ask, then the RPCs\' reason stands');
  lookups = [];
  quick = { eth_getTransactionByHash: { result: { hash: TX, from: FROM, input: payloadFor(DAY, ROOT), blockHash: '0x' + 'ee'.repeat(32), blockNumber: '0x10' } } };
  v = await verifyAnchor(TX, DAY, ROOT, down, 'dogeos-testnet');
  assert.deepStrictEqual([v.status, lookups.length], ['unknown', 0], 'the DogeOS testnet has no fallback');
});
