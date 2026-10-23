'use strict';
/**
 * ERC-8004 on mainnet (lib/erc8004.js): the registry addresses, the
 * registration file, the plan the owner signs, and the read that says whether
 * a registration happened.
 *
 * The addresses are the ones the standard's contracts repository lists
 * (github.com/erc-8004/erc-8004-contracts, read 22 Oct 2026), and on Base the
 * identity registry answered name "AgentIdentity", symbol "AGENT" that day.
 * The verify read runs against a real Base receipt
 * (test/fixtures/erc8004/registered_receipt.json): one transaction that
 * registered six agents, which is what a registration on this registry looks
 * like in practice.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');

const E = require('../lib/erc8004');
const FIX = require('./fixtures/erc8004/registered_receipt.json');

const ORIGIN = 'https://www.humanoid-traders.com';
const OURS = ORIGIN + E.REGISTRATION_PATH;

test('the registries are the standard\'s, mainnet and testnet, and nothing else', () => {
  assert.deepStrictEqual(Object.keys(E.REGISTRIES).map(Number).sort((a, b) => a - b),
    [1, 10, 56, 137, 8453, 42161, 43114, 59144, 84532, 534352]);
  // Every mainnet carries the one mainnet pair; every one but Base (lib/nft.js
  // serves it) brings two readers from two operators.
  for (const [id, r] of Object.entries(E.REGISTRIES)) {
    if (r.testnet) continue;
    assert.strictEqual(r.identity, E.REGISTRIES[8453].identity, id);
    assert.strictEqual(r.reputation, E.REGISTRIES[8453].reputation, id);
    if (Number(id) === 8453) continue;
    assert.strictEqual(new Set(r.rpcs.map((u) => new URL(u).hostname.split('.').slice(-2).join('.'))).size, 2, `${id}: two operators`);
  }
  // One pair on every mainnet: Avalanche's is Base's, with readers of its own.
  assert.strictEqual(E.REGISTRIES[43114].identity, E.REGISTRIES[8453].identity);
  assert.strictEqual(E.REGISTRIES[43114].testnet, false);
  assert.strictEqual(E.REGISTRIES[43114].rpcs.length, 2);
  assert.strictEqual(E.REGISTRIES[8453].identity, '0x8004A169FB4a3325136EB29fA0ceB6D2e539a432'.toLowerCase());
  assert.strictEqual(E.REGISTRIES[8453].reputation, '0x8004BAa17C55a88189AE136b182e5fdA19dE9b63'.toLowerCase());
  assert.strictEqual(E.REGISTRIES[84532].identity, '0x8004A818BFB912233c491871b3d84c89A494BD9e'.toLowerCase());
  assert.strictEqual(E.REGISTRIES[8453].testnet, false);
  assert.strictEqual(E.REGISTRIES[84532].testnet, true);
  assert.strictEqual(E.agentRegistry(8453), 'eip155:8453:0x8004a169fb4a3325136eb29fa0ceb6d2e539a432');
  assert.strictEqual(E.agentRegistry(999999999), null);
});

test('the plan is register(string) with this deployment\'s URI, to the identity registry, for the owner to sign', () => {
  const plan = E.registrationPlan(8453, ORIGIN + '/');
  assert.strictEqual(plan.to, E.REGISTRIES[8453].identity);
  assert.strictEqual(plan.value, '0');
  assert.strictEqual(plan.agent_uri, OURS);
  const iface = new ethers.Interface(['function register(string agentURI)']);
  const parsed = iface.parseTransaction({ data: plan.calldata });
  assert.strictEqual(parsed.signature, 'register(string)');
  assert.strictEqual(parsed.args[0], OURS);
  assert.match(plan.signs, /owner.*own wallet.*never signs/);
  assert.deepStrictEqual(E.registrationPlan(999999999, ORIGIN), { error: 'unsupported_chain' });
  assert.deepStrictEqual(E.registrationPlan(8453, null), { error: 'no_public_origin' });
});

test('the event the verifier reads is the registry\'s own Registered event', () => {
  const { eventTopic } = require('../lib/abi_decode');
  assert.strictEqual(eventTopic(E.REGISTERED), ethers.id('Registered(uint256,string,address)'));
});

const answering = (answer, seen = []) => async (url, init) => {
  seen.push({ url, body: JSON.parse(init.body) });
  return { ok: true, status: 200, json: async () => answer };
};
const RPCS = ['https://a.example', 'https://b.example'];

test('a registration of this URI in a six-agent batch is found and verified', async () => {
  const theirs = 'https://centralcity.ai/a/MUNYiR';
  const seen = [];
  const v = await E.verifyRegistration(FIX.tx, 8453, theirs, { fetchImpl: answering(FIX.answer, seen), rpcs: RPCS });
  assert.deepStrictEqual(v, { status: 'verified', agent_id: '632142', owner: '0x45ac986bc609148850c9c5a6d45cc23e57be871f',
    agent_uri: theirs, block: parseInt(FIX.answer.result.blockNumber, 16) });
  assert.strictEqual(seen[0].body.method, 'eth_getTransactionReceipt');
  assert.deepStrictEqual(seen.map((s) => s.url), [RPCS[0]], 'the first endpoint answered, and the rest were asked anyway');
});

test('the same receipt is not our registration: it says so, and why', async () => {
  const v = await E.verifyRegistration(FIX.tx, 8453, OURS, { fetchImpl: answering(FIX.answer), rpcs: RPCS });
  assert.deepStrictEqual(v, { status: 'mismatch', reason: 'registered a different agent URI' });
  // On the testnet registry, the mainnet events are not the registry's.
  const t = await E.verifyRegistration(FIX.tx, 84532, 'https://centralcity.ai/a/MUNYiR', { fetchImpl: answering(FIX.answer), rpcs: RPCS });
  assert.deepStrictEqual(t, { status: 'mismatch', reason: 'no Registered event from the identity registry' });
  // Avalanche asks its own readers, not Base's.
  const seen = [];
  await E.verifyRegistration(FIX.tx, 43114, OURS, { fetchImpl: answering({ result: null }, seen) });
  assert.deepStrictEqual(seen.map((s) => s.url), [E.REGISTRIES[43114].rpcs[0]]);
});

test('not mined, reverted, unreadable and malformed are four different answers', async () => {
  assert.deepStrictEqual(await E.verifyRegistration(FIX.tx, 8453, OURS, { fetchImpl: answering({ result: null }), rpcs: RPCS }),
    { status: 'pending' });
  const reverted = { result: { ...FIX.answer.result, status: '0x0' } };
  assert.deepStrictEqual(await E.verifyRegistration(FIX.tx, 8453, OURS, { fetchImpl: answering(reverted), rpcs: RPCS }),
    { status: 'mismatch', reason: 'the transaction reverted' });
  const tried = [];
  const dead = async (url) => { tried.push(url); throw new Error('down'); };
  assert.deepStrictEqual(await E.verifyRegistration(FIX.tx, 8453, OURS, { fetchImpl: dead, rpcs: RPCS }),
    { status: 'unknown', reason: 'no endpoint answered' });
  assert.deepStrictEqual(tried, RPCS, 'every endpoint is asked before the read is called unknown');
  assert.deepStrictEqual(await E.verifyRegistration('0x12', 8453, OURS, { fetchImpl: dead, rpcs: RPCS }),
    { status: 'mismatch', reason: 'malformed transaction hash' });
  assert.deepStrictEqual(await E.verifyRegistration(FIX.tx, 999999999, OURS, { fetchImpl: dead, rpcs: RPCS }),
    { status: 'mismatch', reason: 'unsupported chain' });
});

test('the registration file is the standard\'s shape, and lists only a recorded registration', () => {
  const f = E.registrationFile(ORIGIN, { env: {}, mcpVersion: '2025-03-26', toolSlug: 'runeclaw-intel' });
  assert.strictEqual(f.type, 'https://eips.ethereum.org/EIPS/eip-8004#registration-v1');
  for (const k of ['name', 'description', 'image']) assert.ok(typeof f[k] === 'string' && f[k], k);
  assert.deepStrictEqual(f.services, [
    { name: 'web', endpoint: `${ORIGIN}/` },
    { name: 'MCP', endpoint: `${ORIGIN}/mcp`, version: '2025-03-26' },
    { name: 'ERC-8257', endpoint: `${ORIGIN}/.well-known/ai-tool/runeclaw-intel.json` },
  ]);
  assert.strictEqual(f.x402Support, false);
  assert.strictEqual(f.active, true);
  assert.ok(!('supportedTrust' in f), 'a trust model claimed that nothing here uses');
  assert.deepStrictEqual(f.registrations, [], 'an unrecorded registration was listed');
  const rec = E.registrationFile(ORIGIN, { env: { ERC8004_AGENT_ID: '632999', ERC8004_CHAIN_ID: '8453' } });
  assert.deepStrictEqual(rec.registrations, [{ agentId: 632999, agentRegistry: E.agentRegistry(8453) }]);
  for (const env of [{ ERC8004_AGENT_ID: '5' }, { ERC8004_CHAIN_ID: '8453' }, { ERC8004_AGENT_ID: 'x', ERC8004_CHAIN_ID: '8453' },
    { ERC8004_AGENT_ID: '5', ERC8004_CHAIN_ID: '999999999' }]) {
    assert.deepStrictEqual(E.registrationFile(ORIGIN, { env }).registrations, [], JSON.stringify(env));
  }
});

function serve(env) {
  const saved = {};
  for (const [k, v] of Object.entries(env)) { saved[k] = process.env[k]; if (v == null) delete process.env[k]; else process.env[k] = v; }
  delete require.cache[require.resolve('../routes/discovery')];
  const app = express();
  app.use(require('../routes/discovery'));
  const server = http.createServer(app);
  return new Promise((r) => server.listen(0, '127.0.0.1', () => r({
    base: `http://127.0.0.1:${server.address().port}`,
    close: () => { server.close(); for (const [k, v] of Object.entries(saved)) { if (v == null) delete process.env[k]; else process.env[k] = v; } },
  })));
}

test('the routes serve the file, the plan from the configured origin only, and refuse without one', async () => {
  const s = await serve({ PUBLIC_ORIGIN: ORIGIN, APP_BASE_URL: null, WEBSITE_URL: null, ERC8004_AGENT_ID: null, ERC8004_CHAIN_ID: null });
  try {
    const f = await (await fetch(`${s.base}/.well-known/agent-registration.json`)).json();
    assert.strictEqual(f.type, E.REGISTRATION_TYPE);
    assert.strictEqual(f.services[0].endpoint, `${ORIGIN}/`);
    const plan = await (await fetch(`${s.base}/api/erc8004/plan`, { headers: { host: 'evil.example' } })).json();
    assert.strictEqual(plan.agent_uri, OURS, 'the plan took its URI from the request');
    assert.strictEqual(plan.chain_id, 8453);
    assert.strictEqual(plan.recorded, null);
    assert.strictEqual((await fetch(`${s.base}/api/erc8004/plan?chain=999999999`)).status, 400);
    // The verify route answers a malformed hash from the request alone, without reading any chain.
    assert.deepStrictEqual(await (await fetch(`${s.base}/api/erc8004/verify?tx=0x12`)).json(),
      { status: 'mismatch', reason: 'malformed transaction hash' });
  } finally {
    s.close();
  }
  const bare = await serve({ PUBLIC_ORIGIN: null, APP_BASE_URL: null, WEBSITE_URL: null });
  try {
    const r = await fetch(`${bare.base}/api/erc8004/plan`);
    assert.strictEqual(r.status, 503);
    assert.deepStrictEqual(await r.json(), { error: 'no_public_origin' });
  } finally {
    bare.close();
  }
});
