'use strict';
/**
 * A day's root anchored on Avalanche C-Chain, the same calldata self-send as
 * Base (app/lib/root_anchor.js). Avalanche runs its own consensus, so it is
 * its own independence group: the fifth that counts.
 *
 * Off unless AVALANCHE_ANCHOR_ENABLED is 1, true, yes or on. The server never
 * signs. tools/rcverify.js checks an Avalanche anchor against an endpoint it
 * names itself, and does not read the flag.
 *
 *   the flag     unset is absent; the plan and the registry grow only when on
 *   the plan     a dry-run zero-value self-send, the Base payload, no key
 *   two readers  agreement on the calldata and the block, or "unknown"
 *   no leak      a configured RPC is read, never named, never served
 *   the route    refused while off; recorded once while on; unknown again if
 *                the flag is later unset
 *   rcverify     its own reader and its own group, whatever the bundle says
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-'.repeat(4);

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const http = require('node:http');
const path = require('path');
const express = require('express');

const anchor = require('../lib/root_anchor');
const { pool } = require('../db');
const authModule = require('../auth');
const roots = require('../lib/seal_roots');
const rcverify = require('../../tools/rcverify');
const { BITCOIN_WITNESS } = require('../lib/anchor_quorum');

const ON = { AVALANCHE_ANCHOR_ENABLED: '1' };
const DAY = '2026-03-11';
const ROOT = 'c'.repeat(64);
const TX = '0x' + 'a'.repeat(64);
const FROM = '0x' + 'ab'.repeat(20);
const BLOCK = '0x' + 'bb'.repeat(32);
const [RPC_A, RPC_B] = anchor.AVALANCHE_RPCS;

function txOf(input, over = {}) {
  return { hash: TX, blockHash: BLOCK, blockNumber: '0x10', input, from: FROM, ...over };
}
function rpcFetch(byUrl, seen = []) {
  return async (url, opts) => {
    const body = JSON.parse(opts.body);
    seen.push({ url, method: body.method });
    const answer = byUrl[url] ? byUrl[url](body.method, body.params) : new Error('down');
    if (answer instanceof Error) throw answer;
    if (answer && answer.rpcError) {
      return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, error: { code: answer.rpcError, message: 'busy SECRETKEY' } }) };
    }
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result: answer }) };
  };
}
const good = (method) => (method === 'eth_getTransactionByHash'
  ? txOf(anchor.payloadFor(DAY, ROOT))
  : { timestamp: '0x68f4d2c0' });

test('the flag defaults off, and only the four words turn it on', () => {
  assert.strictEqual(anchor.AVALANCHE_ANCHOR_TARGET, '2026-11-05');
  assert.strictEqual(anchor.AVALANCHE_CHAIN_ID, 43114);
  assert.strictEqual(anchor.chainInfo('avalanche', {}), null);
  assert.strictEqual(anchor.buildAnchorPlan(DAY, ROOT, 'avalanche', {}), null);
  for (const off of ['', '0', 'false', 'no', 'off', 'enabled', '2']) {
    assert.strictEqual(anchor.chainInfo('avalanche', { AVALANCHE_ANCHOR_ENABLED: off }), null, off);
  }
  for (const on of ['1', 'true', 'TRUE', 'yes', 'on', '  On  ']) {
    const c = anchor.chainInfo('avalanche', { AVALANCHE_ANCHOR_ENABLED: on });
    assert.strictEqual(c && c.chain_id, 43114, on);
  }
  const keys = Object.keys(anchor.chains({})).sort();
  assert.ok(!keys.includes('avalanche'));
  assert.deepStrictEqual(keys, ['arbitrum-eas', 'base', 'base-eas', 'dogecoin', 'dogeos-testnet', 'linea-eas', 'scroll-eas', 'solana']);
});

test('when on, Avalanche is the fifth counting group and the same calldata plan as Base', () => {
  const pub = anchor.publicChains(ON);
  const c = pub.find((x) => x.key === 'avalanche');
  assert.ok(c, 'the public registry lists it only when the flag is on');
  assert.deepStrictEqual(
    [c.chain_id, c.name, c.testnet, c.method, c.independence_group, c.counts_toward_quorum, c.rpcs, c.explorer_tx],
    [43114, 'Avalanche C-Chain', false, 'calldata', 'avalanche', true, anchor.AVALANCHE_RPCS, 'https://snowtrace.io/tx/'],
  );
  assert.deepStrictEqual(c.native, { name: 'Avalanche', symbol: 'AVAX', decimals: 18 });
  const groups = new Set(pub.filter((x) => x.counts_toward_quorum).map((x) => x.independence_group));
  groups.add(BITCOIN_WITNESS.group);
  assert.deepStrictEqual([...groups].sort(), ['avalanche', 'bitcoin', 'dogecoin', 'ethereum-l2', 'solana']);
  // The two readers are the ones the registration module already asks.
  assert.deepStrictEqual([...require('../lib/erc8004').REGISTRIES[43114].rpcs], [...anchor.AVALANCHE_RPCS]);

  const plan = anchor.buildAnchorPlan(DAY, ROOT, 'avalanche', ON);
  const base = anchor.buildAnchorPlan(DAY, ROOT, 'base', {});
  assert.deepStrictEqual(
    [plan.dry_run, plan.chain_id, plan.chain_key, plan.value, plan.data, plan.to],
    [true, 43114, 'avalanche', '0', anchor.payloadFor(DAY, ROOT), base.to],
  );
  assert.strictEqual(plan.data, base.data, 'one payload, the Base bytes');
  assert.match(plan.non_custodial_note, /never holds a key and never sends/);
  assert.match(plan.instructions.join('\n'), /api\.avax\.network\/ext\/bc\/C\/rpc/);
  assert.ok(!JSON.stringify(plan).includes('$'), 'no amount on the plan');
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'root_anchor.js'), 'utf8');
  for (const forbidden of ['signTransaction', 'sendRawTransaction', 'private_key', 'eth_sendTransaction', 'AVALANCHE_PRIVATE']) {
    assert.ok(!src.includes(forbidden), forbidden);
  }
});

test('two readers: agreement verifies on Avalanche, and one down, lying or unmined reader is not a verdict', async () => {
  const seen = [];
  const both = await anchor.verifyAnchor(TX, DAY, ROOT, rpcFetch({ [RPC_A]: good, [RPC_B]: good }, seen), 'avalanche', ON);
  assert.deepStrictEqual([both.status, both.from, both.readers], ['verified', FROM, ['api.avax.network', 'avalanche-c-chain-rpc.publicnode.com']]);
  assert.strictEqual(both.block_time, new Date(0x68f4d2c0 * 1000).toISOString());
  assert.ok(seen.length >= 2 && seen.every((s) => anchor.AVALANCHE_RPCS.includes(s.url)), 'asked Avalanche, not Base');
  assert.ok(!('final' in both), 'no reorg depth: the same rule as Base');

  const down = await anchor.verifyAnchor(TX, DAY, ROOT, rpcFetch({ [RPC_A]: good }), 'avalanche', ON);
  assert.strictEqual(down.status, 'unknown');
  const lies = await anchor.verifyAnchor(TX, DAY, ROOT, rpcFetch({
    [RPC_A]: good,
    [RPC_B]: (method) => (method === 'eth_getTransactionByHash' ? txOf(anchor.payloadFor(DAY, 'd'.repeat(64))) : { timestamp: '0x1' }),
  }), 'avalanche', ON);
  assert.deepStrictEqual([lies.status, lies.disagreement], ['unknown', true]);
  const unmined = await anchor.verifyAnchor(TX, DAY, ROOT, rpcFetch({
    [RPC_A]: (method) => (method === 'eth_getTransactionByHash' ? txOf(anchor.payloadFor(DAY, ROOT), { blockHash: null }) : null),
    [RPC_B]: (method) => (method === 'eth_getTransactionByHash' ? txOf(anchor.payloadFor(DAY, ROOT), { blockHash: null }) : null),
  }), 'avalanche', ON);
  assert.strictEqual(unmined.status, 'unknown');
  const wrong = await anchor.verifyAnchor(TX, DAY, ROOT, rpcFetch({
    [RPC_A]: (method) => (method === 'eth_getTransactionByHash' ? txOf('0xdead') : { timestamp: '0x1' }),
    [RPC_B]: (method) => (method === 'eth_getTransactionByHash' ? txOf('0xdead') : { timestamp: '0x1' }),
  }), 'avalanche', ON);
  assert.strictEqual(wrong.status, 'mismatch');

  const asked = [];
  const off = await anchor.verifyAnchor(TX, DAY, ROOT, async (u) => { asked.push(u); throw new Error('must not be called'); }, 'avalanche', {});
  assert.deepStrictEqual([off.status, asked], ['mismatch', []]);
});

test('a configured Avalanche RPC is read but never named and never served', async () => {
  const secret = 'https://avax.example-provider.invalid/v2/SECRETKEY123';
  const env = { AVALANCHE_ANCHOR_ENABLED: '1', AVALANCHE_RPC_URLS: secret };
  const c = anchor.chainInfo('avalanche', env);
  assert.deepStrictEqual(c.rpcs, [secret]);
  const served = JSON.stringify(anchor.publicChains(env));
  assert.ok(!served.includes('SECRETKEY') && !served.includes('example-provider'));
  assert.deepStrictEqual(anchor.publicChains(env).find((x) => x.key === 'avalanche').rpcs, anchor.AVALANCHE_RPCS);
  const plan = anchor.buildAnchorPlan(DAY, ROOT, 'avalanche', env);
  assert.ok(!JSON.stringify(plan).includes('SECRETKEY'));
  const v = await anchor.verifyAnchor(TX, DAY, ROOT, async () => { const e = new Error('nope'); e.name = 'TypeError'; throw e; }, 'avalanche', env);
  assert.strictEqual(v.status, 'unknown');
  assert.match(v.reason, /configured rpc 1/);
  assert.doesNotMatch(v.reason, /SECRETKEY|example-provider/);
});

test('rcverify checks an Avalanche anchor on its own endpoint and counts its own group', async () => {
  const day = '2026-01-15';
  const payload = '{}';
  const root = crypto.createHash('sha256').update(payload).digest('hex');
  const data = anchor.payloadFor(day, root);
  const seen = [];
  const fetchImpl = async (url, opts) => {
    seen.push(url);
    const method = JSON.parse(opts.body).method;
    const result = method === 'eth_getTransactionByHash'
      ? { blockHash: '0x1', blockNumber: '0x22', input: data }
      : { status: '0x1' };
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  };
  const pass = await rcverify.checkAnchor({ chain: 'avalanche', tx: TX, independence_group: 'ethereum-l2' }, day, root, fetchImpl);
  assert.strictEqual(pass.status, 'pass');
  assert.ok(seen.every((u) => u === rcverify.CHAINS.avalanche.rpc), seen.join(','));
  assert.notStrictEqual(rcverify.CHAINS.avalanche.rpc, rcverify.CHAINS.base.rpc);

  const bad = await rcverify.checkAnchor({ chain: 'avalanche', tx: TX }, day, root, async (url, opts) => {
    const method = JSON.parse(opts.body).method;
    const result = method === 'eth_getTransactionByHash'
      ? { blockHash: '0x1', blockNumber: '0x22', input: anchor.payloadFor(day, 'e'.repeat(64)) }
      : { status: '0x1' };
    return { ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  });
  assert.strictEqual(bad.status, 'fail');
  const reverted = await rcverify.checkAnchor({ chain: 'avalanche', tx: TX }, day, root, async (url, opts) => {
    const method = JSON.parse(opts.body).method;
    const result = method === 'eth_getTransactionByHash'
      ? { blockHash: '0x1', blockNumber: '0x22', input: data }
      : { status: '0x0' };
    return { ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  });
  assert.strictEqual(reverted.status, 'fail');

  const seal = root;
  const bundle = {
    format: 'rcproof/1', seal, seal_payload: payload, sealed_at: `${day}T12:00:00.000Z`,
    merkle: { day, root, proof: [] },
    anchors: [
      { chain: 'base', tx: '0x' + 'b'.repeat(64), independence_group: 'solana', counts_toward_quorum: false },
      { chain: 'avalanche', tx: TX, independence_group: 'ethereum-l2', counts_toward_quorum: false },
    ],
  };
  const out = await rcverify.verifyBundle(bundle, { fetchImpl });
  const quorum = out.checks.find((c) => c.name === 'quorum');
  assert.match(quorum.detail, /multi-anchored: 2 independent chains verified here \(avalanche, ethereum-l2\)/);
  const reg = anchor.publicChains(ON).find((c) => c.key === 'avalanche');
  assert.strictEqual(rcverify.CHAINS.avalanche.group, reg.independence_group);
  assert.strictEqual(rcverify.CHAINS.avalanche.method, 'calldata');
  assert.ok(reg.rpcs.includes(rcverify.CHAINS.avalanche.rpc));
});

test('the anchor doc names every chain, the flag, the fifth group, and the cost', () => {
  const doc = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'ANCHORS.md'), 'utf8');
  for (const name of ['Base', 'Bitcoin', 'Dogecoin', 'Solana', 'Avalanche C-Chain', 'Arbitrum', 'Scroll', 'Linea', 'DogeOS']) {
    assert.ok(doc.includes(name), name);
  }
  assert.match(doc, /fifth independence group/);
  assert.match(doc, /AVALANCHE_ANCHOR_ENABLED/);
  assert.match(doc, /AVALANCHE_RPC_URLS/);
  assert.match(doc, /5 November 2026/);
  assert.match(doc, /0\.000064 AVAX/);
  assert.match(doc, /never holds a key and never signs/);
  assert.ok(!/SECRET|private_key|sk_live/.test(doc));
  const spec = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'PROVABLE_CALLS_SPEC.md'), 'utf8');
  assert.match(spec, /Avalanche C-Chain/);
  assert.match(spec, /AVALANCHE_ANCHOR_ENABLED/);
  const example = fs.readFileSync(path.join(__dirname, '..', '..', '.env.example'), 'utf8');
  assert.match(example, /AVALANCHE_ANCHOR_ENABLED=/);
  assert.match(example, /AVALANCHE_RPC_URLS=/);
});

// ── through the route, where the flag is the process environment ────────────

const ROUTE_DAY = roots.dayOf(new Date(Date.now() - 40 * 24 * 3600 * 1000));
let server;
let base;
function request(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, { method, headers: {
      accept: 'application/json', ...(data ? { 'content-type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    req.on('error', reject); if (data) req.write(data); req.end();
  });
}

test('the route hides Avalanche until the flag is on, then records one anchor', async () => {
  const previous = process.env.AVALANCHE_ANCHOR_ENABLED;
  const realFetch = global.fetch;
    const onAvalanche = (method) => (method === 'eth_getTransactionByHash'
      ? txOf(anchor.payloadFor(ROUTE_DAY, ROOT))
      : { timestamp: '0x68f4d2c0' });
    global.fetch = async (url, opts) => {
      if (!anchor.AVALANCHE_RPCS.includes(url)) {
        return { ok: false, status: 404, json: async () => ({}), text: async () => '' };
      }
      return rpcFetch({ [RPC_A]: onAvalanche, [RPC_B]: onAvalanche })(url, opts);
    };
    await pool.execute('DELETE FROM seal_root_anchors WHERE day = ?', [ROUTE_DAY]);
    await pool.execute('DELETE FROM seal_roots WHERE day = ?', [ROUTE_DAY]);
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  try {
    await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
    base = `http://127.0.0.1:${server.address().port}`;
    delete process.env.AVALANCHE_ANCHOR_ENABLED;
    await pool.execute(
      'INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
      [ROUTE_DAY, ROOT, 1, JSON.stringify([ROOT]), new Date()],
    );
    const hidden = await request('GET', `/api/roots/anchor-plan/${ROUTE_DAY}?chain=avalanche`);
    assert.strictEqual(hidden.status, 400);
    const listed = await request('GET', '/api/roots/chains');
    assert.ok(!listed.data.chains.some((c) => c.key === 'avalanche'));

    process.env.AVALANCHE_ANCHOR_ENABLED = '1';
    const plan = await request('GET', `/api/roots/anchor-plan/${ROUTE_DAY}?chain=avalanche`);
    assert.strictEqual(plan.status, 200, JSON.stringify(plan.data));
    assert.deepStrictEqual([plan.data.dry_run, plan.data.chain_id, plan.data.value, plan.data.data],
      [true, 43114, '0', anchor.payloadFor(ROUTE_DAY, ROOT)]);
    assert.ok(!JSON.stringify(plan.data).includes('$'));

    const bcrypt = require('bcryptjs');
    const email = `avax${Date.now()}@example.com`;
    await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, await bcrypt.hash('longenoughpassword1', 4)]);
    const [rows] = await pool.execute('SELECT id, token_epoch FROM users WHERE email = ?', [email]);
    const token = authModule.signToken({ id: rows[0].id, email, token_epoch: rows[0].token_epoch });
    const ok = await request('POST', '/api/roots/anchor', { token, body: { day: ROUTE_DAY, tx_hash: TX, chain: 'avalanche' } });
    assert.strictEqual(ok.status, 200, JSON.stringify(ok.data));
    assert.strictEqual(ok.data.explorer, `https://snowtrace.io/tx/${TX}`);
    const q = await request('GET', `/api/roots/quorum/${ROUTE_DAY}`);
    assert.strictEqual(q.status, 200, JSON.stringify(q.data));
    assert.ok(q.data.groups.includes('avalanche'), JSON.stringify(q.data.groups));
    const row = q.data.anchors.find((a) => a.chain === 'avalanche');
    assert.deepStrictEqual([row.status, row.counts_toward_quorum, row.independence_group], ['verified', true, 'avalanche']);

    delete process.env.AVALANCHE_ANCHOR_ENABLED;
    const again = await request('GET', `/api/roots/anchor-plan/${ROUTE_DAY}?chain=avalanche`);
    assert.strictEqual(again.status, 400, 'the flag off takes the chain back out of the registry');
    const q2 = await request('GET', `/api/roots/quorum/${ROUTE_DAY}`);
    const dropped = q2.data.anchors.find((a) => a.chain === 'avalanche');
    assert.strictEqual(dropped.status, 'unknown');
    assert.match(dropped.reason, /does not know/);
    assert.ok(!q2.data.groups.includes('avalanche'));
  } finally {
    if (previous === undefined) delete process.env.AVALANCHE_ANCHOR_ENABLED;
    else process.env.AVALANCHE_ANCHOR_ENABLED = previous;
    global.fetch = realFetch;
    if (server) await new Promise((res) => server.close(res));
  }
});
