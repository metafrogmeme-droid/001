'use strict';
/**
 * RWA pre-check. The chain is a script, never the network: each test hands
 * the reader the JSON-RPC answers it should see.
 *
 * What this file is for: a revert is not a false, an empty return is not a
 * zero, a missing heartbeat is not "fresh", and a sealed receipt does not
 * contain the address it was about.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');
const { AbiCoder, Interface } = require('ethers');

const { pool } = require('../db');
const { CHAINS } = require('../lib/rwa_chains');
const { precheck, readerFor, canonicalInput, DISCLAIMER } = require('../lib/rwa_precheck');
const { httpRpc, SELECTOR_OF, SLOT_IMPL } = require('../lib/rwa_evm');
const { pageGate } = require('../lib/rwa_flag');
const { codeOnly } = require('./helpers/code_only');

const coder = AbiCoder.defaultAbiCoder();
const VIEWS = new Interface([
  'function name() view returns (string)',
  'function identityRegistry() view returns (address)',
  'function compliance() view returns (address)',
  'function paused() view returns (bool)',
  'function owner() view returns (address)',
  'function isFrozen(address) view returns (bool)',
  'function isVerified(address) view returns (bool)',
  'function isAgent(address) view returns (bool)',
  'function getModules() view returns (address[])',
  'function getImplementationAuthority() view returns (address)',
  'function getTokenImplementation() view returns (address)',
  'function latestRoundData() view returns (uint80,int256,uint256,uint256,uint80)',
  'function description() view returns (string)',
  'function heartbeat() view returns (uint256)',
]);

const A = '0x' + '11'.repeat(20);
const REG = '0x' + '22'.repeat(20);
const COMP = '0x' + '33'.repeat(20);
const OWN = '0x' + '44'.repeat(20);
const IMPL = '0x' + '55'.repeat(20);
const ACCT = '0x' + '66'.repeat(20);
const IA = '0x' + '77'.repeat(20);
const OTHER = '0x' + '88'.repeat(20);
const ANSWER = 424242424242n;

const HTML = fs.readFileSync(path.join(__dirname, '..', 'public', 'rwa.html'), 'utf8');
const PAGE_JS = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'rwa-precheck.js'), 'utf8');

function enc(types, values) {
  return coder.encode(types, values);
}

function word(addr) {
  return '0x' + addr.slice(2).toLowerCase().padStart(64, '0');
}

function push4(sel) {
  return '0x63' + sel.slice(2) + '00';
}

/**
 * A chain that answers from tables. An eth_call with no row reverts.
 * A missing code is unreadable, not empty: empty has to be said on purpose.
 */
function rpcOf({ block, codes, slots, calls }) {
  const b = block || { number: 100, timestamp: 1700000000 };
  return async (method, params) => {
    if (method === 'eth_getBlockByNumber') {
      if (b.fail) return { ok: false, reason: 'unreadable' };
      return { ok: true, result: {
        number: '0x' + b.number.toString(16),
        timestamp: '0x' + b.timestamp.toString(16),
      } };
    }
    if (method === 'eth_getCode') {
      const code = (codes || {})[params[0].toLowerCase()];
      if (code == null) return { ok: false, reason: 'unreadable' };
      return { ok: true, result: code };
    }
    if (method === 'eth_getStorageAt') {
      const row = ((slots || {})[params[0].toLowerCase()] || {})[params[1].toLowerCase()];
      if (row == null) return { ok: true, result: '0x' + '0'.repeat(64) };
      return { ok: true, result: row };
    }
    if (method === 'eth_call') {
      const to = params[0].to.toLowerCase();
      const data = params[0].data.toLowerCase();
      const table = (calls || {})[to] || {};
      if (!Object.prototype.hasOwnProperty.call(table, data)) return { ok: false, reason: 'revert' };
      const v = table[data];
      if (v === 'unreadable') return { ok: false, reason: 'unreadable' };
      return { ok: true, result: v };
    }
    return { ok: false, reason: 'unreadable' };
  };
}

function put(calls, to, fragment, args, result) {
  const data = VIEWS.encodeFunctionData(fragment, args || []).toLowerCase();
  const key = to.toLowerCase();
  if (!calls[key]) calls[key] = {};
  calls[key][data] = result;
}

function baseCalls(extra) {
  const calls = {};
  put(calls, A, 'identityRegistry', [], enc(['address'], [REG]));
  put(calls, A, 'compliance', [], enc(['address'], [COMP]));
  put(calls, A, 'paused', [], enc(['bool'], [false]));
  put(calls, A, 'owner', [], enc(['address'], [OWN]));
  put(calls, COMP, 'getModules', [], enc(['address[]'], [[]]));
  if (extra) extra(calls);
  return calls;
}

function run(opts) {
  return precheck({
    chain: 'base',
    asset: A,
    env: {},
    rpc: rpcOf(opts),
    ...opts.pre,
  });
}

function flag(reading, id) {
  return (reading.flags || []).find((f) => f.id === id) || null;
}

function keysDeep(value, into) {
  const out = into || [];
  if (!value || typeof value !== 'object') return out;
  if (Array.isArray(value)) { value.forEach((v) => keysDeep(v, out)); return out; }
  for (const k of Object.keys(value)) {
    out.push(k);
    keysDeep(value[k], out);
  }
  return out;
}

test('the chain list is the config, and Monad, Solana and Stellar are not on it', () => {
  assert.deepEqual(CHAINS.map((c) => c.id),
    ['ethereum', 'base', 'avalanche', 'bnb', 'arbitrum', 'polygon', 'zksync']);
  assert.ok(CHAINS.every((c) => c.family === 'evm'));
  assert.equal(readerFor('evm') != null, true);
  assert.equal(readerFor('solana'), null);
  assert.equal(readerFor('stellar'), null);
  const values = [...HTML.matchAll(/<option value="([^"]*)">/g)].map((m) => m[1]);
  assert.deepEqual(values, CHAINS.map((c) => c.id), 'the page select is the chain list');
});

test('a family with no reader is not read as EVM', async () => {
  let called = 0;
  const r = await precheck({
    chainRecord: { id: 'solana', chain_id: 0, name: 'Solana', family: 'solana', rpc_env: 'RWA_RPC_URL_SOLANA' },
    asset: A,
    rpc: async () => { called += 1; return { ok: false, reason: 'unreadable' }; },
  });
  assert.equal(r.read, false);
  assert.equal(r.reason, 'no_reader');
  assert.equal(called, 0);
});

test('an ERC-3643 reading names the registry, the pause bit and a selector it saw', async () => {
  const r = await run({
    codes: { [A]: push4(SELECTOR_OF.forcedTransfer) },
    calls: baseCalls(),
  });
  assert.equal(r.read, true);
  assert.equal(r.block.number, '100');
  assert.equal(flag(r, 'erc3643').state, 'detected');
  assert.equal(flag(r, 'erc3643').tone, 'neutral');
  assert.equal(flag(r, 'paused').state, 'no');
  assert.equal(flag(r, 'paused').tone, 'neutral');
  assert.equal(flag(r, 'forced_transfer').state, 'observed');
  assert.equal(flag(r, 'forced_transfer').tone, 'caution');
  assert.equal(flag(r, 'freeze').state, 'not_observed');
  assert.equal(flag(r, 'freeze').tone, 'muted');
  assert.equal(flag(r, 'investor').state, 'not_checked');
  assert.ok(!flag(r, 'investor_verified'));
  assert.equal(r.setup.registry.address, REG);
  assert.equal(r.disclaimer, DISCLAIMER);
});

test('a paused contract is caution, and the zero address is not a registry', async () => {
  const calls = baseCalls();
  put(calls, A, 'paused', [], enc(['bool'], [true]));
  put(calls, A, 'identityRegistry', [], enc(['address'], ['0x' + '0'.repeat(40)]));
  const r = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(flag(r, 'paused').state, 'yes');
  assert.equal(flag(r, 'paused').tone, 'caution');
  assert.equal(flag(r, 'erc3643').state, 'not_detected');
  assert.equal(flag(r, 'identity_registry').state, 'unset');
});

test('a reverted identityRegistry is not "outside ERC-3643"', async () => {
  const calls = {};
  put(calls, A, 'compliance', [], enc(['address'], [COMP]));
  put(calls, A, 'paused', [], enc(['bool'], [false]));
  put(calls, A, 'owner', [], enc(['address'], [OWN]));
  const r = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(flag(r, 'erc3643').state, 'not_observed');
  assert.equal(flag(r, 'erc3643').tone, 'muted');
  assert.ok(!r.flags.some((f) => f.state === 'not_detected'));
});

test('isVerified reverting is unreadable, not unverified', async () => {
  const calls = baseCalls((c) => {
    put(c, A, 'isFrozen', [ACCT], enc(['bool'], [false]));
    put(c, A, 'isAgent', [ACCT], enc(['bool'], [false]));
  });
  const r = await run({ codes: { [A]: '0x00' }, calls, pre: { account: ACCT } });
  assert.ok(r.unreadable.includes('investor_verified'));
  assert.ok(!r.flags.some((f) => f.id === 'investor_verified'));
});

test('isVerified false is the caution, and true is not a recommendation', async () => {
  const calls = baseCalls((c) => {
    put(c, A, 'isFrozen', [ACCT], enc(['bool'], [true]));
    put(c, A, 'isAgent', [ACCT], enc(['bool'], [true]));
    put(c, REG, 'isVerified', [ACCT], enc(['bool'], [false]));
  });
  const r = await run({ codes: { [A]: '0x00' }, calls, pre: { account: ACCT } });
  assert.equal(flag(r, 'investor_verified').state, 'no');
  assert.equal(flag(r, 'investor_verified').tone, 'caution');
  assert.equal(flag(r, 'investor_frozen').tone, 'caution');
  assert.equal(flag(r, 'investor_agent').tone, 'caution');
  assert.ok(!/recommend|safe|good/i.test(flag(r, 'investor_verified').text));
});

test('empty code is not a list of absent functions', async () => {
  const r = await run({ codes: { [A]: '0x' }, calls: baseCalls() });
  assert.equal(r.code, 'empty');
  assert.equal(flag(r, 'contract').state, 'empty');
  assert.ok(!flag(r, 'forced_transfer'));
  assert.ok(!flag(r, 'freeze'));
});

test('a 0xef marker keeps the view calls and does not claim functions are absent', async () => {
  const calls = baseCalls();
  put(calls, A, 'name', [], enc(['string'], ['Apple Inc.']));
  const r = await run({ codes: { [A]: '0xef' }, calls });
  assert.equal(r.code, 'marker');
  assert.equal(r.token.name, 'Apple Inc.');
  assert.equal(flag(r, 'selectors').state, 'not_evm');
  assert.equal(flag(r, 'erc3643').state, 'detected');
  assert.ok(!flag(r, 'forced_transfer'));
});

test('the implementation slot is the code that is scanned when implementation() reverts', async () => {
  const r = await run({
    codes: { [A]: '0x00', [IMPL]: push4(SELECTOR_OF.forcedTransfer) },
    slots: { [A]: { [SLOT_IMPL]: word(IMPL) } },
    calls: baseCalls(),
  });
  assert.equal(r.setup.implementation.address, IMPL);
  assert.equal(flag(r, 'upgradeable').state, 'yes');
  assert.equal(flag(r, 'upgradeable').tone, 'caution');
  assert.equal(flag(r, 'forced_transfer').state, 'observed');
});

test('the ERC-1967 slot wins over a T-REX implementation authority', async () => {
  const calls = baseCalls();
  put(calls, A, 'getImplementationAuthority', [], enc(['address'], [IA]));
  put(calls, IA, 'getTokenImplementation', [], enc(['address'], [OTHER]));
  const r = await run({
    codes: { [A]: '0x00', [IMPL]: push4(SELECTOR_OF.forcedTransfer), [OTHER]: '0x00' },
    slots: { [A]: { [SLOT_IMPL]: word(IMPL) } },
    calls,
  });
  assert.equal(r.setup.implementation.address, IMPL);
  assert.equal(flag(r, 'forced_transfer').state, 'observed');
});

test('a T-REX proxy is scanned through its implementation authority', async () => {
  const calls = baseCalls();
  put(calls, A, 'getImplementationAuthority', [], enc(['address'], [IA]));
  put(calls, IA, 'getTokenImplementation', [], enc(['address'], [IMPL]));
  const r = await run({
    codes: {
      [A]: push4(SELECTOR_OF.setImplementationAuthority),
      [IMPL]: push4(SELECTOR_OF.forcedTransfer),
    },
    calls,
  });
  assert.equal(r.setup.implementation.address, IMPL);
  assert.equal(flag(r, 'upgradeable').state, 'yes');
  assert.equal(flag(r, 'forced_transfer').state, 'observed');
  assert.equal(flag(r, 'forced_transfer').tone, 'caution');
});

test('an unreadable token implementation is not reported as absent', async () => {
  const calls = baseCalls();
  put(calls, A, 'getImplementationAuthority', [], enc(['address'], [IA]));
  put(calls, IA, 'getTokenImplementation', [], 'unreadable');
  const r = await run({
    codes: { [A]: push4(SELECTOR_OF.setImplementationAuthority) },
    calls,
  });
  assert.equal(r.setup.implementation.state, 'unreadable');
  assert.ok(r.unreadable.includes('forced_transfer'));
  assert.ok(!flag(r, 'forced_transfer'));
  assert.equal(flag(r, 'upgradeable').state, 'yes');
});

test('a feed older than its heartbeat is stale, and the answer is not in the result', async () => {
  const calls = baseCalls();
  const updated = 1700000000 - 5000;
  put(calls, A, 'latestRoundData', [], enc(
    ['uint80', 'int256', 'uint256', 'uint256', 'uint80'],
    [1, ANSWER, updated, updated, 1],
  ));
  put(calls, A, 'description', [], enc(['string'], ['Example NAV']));
  put(calls, A, 'heartbeat', [], enc(['uint256'], [1000]));
  const r = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(r.oracle.referenced, true);
  assert.equal(r.oracle.kind, 'nav');
  assert.equal(r.oracle.age_seconds, 5000);
  assert.equal(r.oracle.heartbeat_seconds, 1000);
  assert.equal(r.oracle.stale, true);
  assert.equal(flag(r, 'oracle').state, 'stale');
  assert.equal(flag(r, 'oracle').tone, 'caution');
  assert.ok(!keysDeep(r).includes('answer'));
  assert.ok(!keysDeep(r).includes('price'));
  assert.ok(!JSON.stringify(r).includes(String(ANSWER)));
});

test('within the heartbeat is neutral, not a claim the data is right', async () => {
  const calls = baseCalls();
  const updated = 1700000000 - 5000;
  put(calls, A, 'latestRoundData', [], enc(
    ['uint80', 'int256', 'uint256', 'uint256', 'uint80'],
    [1, ANSWER, updated, updated, 1],
  ));
  put(calls, A, 'description', [], enc(['string'], ['Reserve total']));
  put(calls, A, 'heartbeat', [], enc(['uint256'], [10000]));
  const r = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(r.oracle.kind, 'por');
  assert.equal(r.oracle.stale, false);
  assert.equal(flag(r, 'oracle').state, 'within_heartbeat');
  assert.equal(flag(r, 'oracle').tone, 'neutral');
});

test('a missing heartbeat leaves stale null, and updatedAt 0 is not age 0', async () => {
  const calls = baseCalls();
  const updated = 1700000000 - 5000;
  put(calls, A, 'latestRoundData', [], enc(
    ['uint80', 'int256', 'uint256', 'uint256', 'uint80'],
    [1, 1, updated, updated, 1],
  ));
  put(calls, A, 'description', [], enc(['string'], ['Example NAV']));
  const missing = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(missing.oracle.heartbeat_seconds, null);
  assert.equal(missing.oracle.stale, null);
  assert.equal(missing.oracle.age_seconds, 5000);
  assert.equal(flag(missing, 'oracle').state, 'unknown');

  const zeroCalls = baseCalls();
  put(zeroCalls, A, 'latestRoundData', [], enc(
    ['uint80', 'int256', 'uint256', 'uint256', 'uint80'],
    [1, 1, 0, 0, 1],
  ));
  put(zeroCalls, A, 'heartbeat', [], enc(['uint256'], [3600]));
  const zero = await run({ codes: { [A]: '0x00' }, calls: zeroCalls });
  assert.equal(zero.oracle.age_seconds, null);
  assert.equal(zero.oracle.update, 'no_update');
  assert.equal(zero.oracle.stale, null);
});

test('an update after the block is clock skew, not a negative age', async () => {
  const calls = baseCalls();
  const updated = 1700000000 + 90;
  put(calls, A, 'latestRoundData', [], enc(
    ['uint80', 'int256', 'uint256', 'uint256', 'uint80'],
    [1, 1, updated, updated, 1],
  ));
  put(calls, A, 'heartbeat', [], enc(['uint256'], [3600]));
  const r = await run({ codes: { [A]: '0x00' }, calls });
  assert.equal(r.oracle.update, 'clock_skew');
  assert.equal(r.oracle.age_seconds, null);
  assert.equal(r.oracle.stale, null);
});

test('a block that cannot be read fails the check instead of reporting findings', async () => {
  const r = await run({ block: { fail: true }, codes: { [A]: '0x00' }, calls: baseCalls() });
  assert.equal(r.read, false);
  assert.equal(r.reason, 'unreadable');
  assert.ok(!r.flags);
});

test('the next RPC URL is asked when the first cannot be read, and not when it reverts', async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push(url);
    const body = JSON.parse(init.body);
    if (url.endsWith('/a') && body.method !== 'eth_call') throw new Error('down');
    if (body.method === 'eth_call') {
      return { ok: true, json: async () => ({ error: { code: 3, message: 'execution reverted' } }) };
    }
    return { ok: true, json: async () => ({ result: { number: '0x10', timestamp: '0x20' } }) };
  };
  const rpc = httpRpc(['http://rpc.test/a', 'http://rpc.test/b'], fetchImpl, 1000);
  const block = await rpc('eth_getBlockByNumber', ['latest', false]);
  assert.equal(block.ok, true);
  assert.deepEqual(seen, ['http://rpc.test/a', 'http://rpc.test/b']);
  seen.length = 0;
  const call = await rpc('eth_call', [{ to: A, data: '0x' }, '0x10']);
  assert.equal(call.reason, 'revert');
  assert.deepEqual(seen, ['http://rpc.test/a']);
});

test('no URL configured is its own reason, and a bad address is refused', async () => {
  const none = await precheck({ chain: 'base', asset: A, env: {} });
  assert.equal(none.reason, 'rpc_unconfigured');
  assert.equal(none.chain.rpc_configured, false);
  assert.ok(!JSON.stringify(none).includes('http'));
  const bad = await precheck({ chain: 'base', asset: 'not-an-address', env: {} });
  assert.equal(bad.reason, 'invalid_asset');
  const unknown = await precheck({ chain: 'monad', asset: A, env: {} });
  assert.equal(unknown.reason, 'unsupported_chain');
});

test('the canonical input is chain, asset, account — and account null is not omitted', () => {
  const text = canonicalInput('Base', A.toUpperCase(), null);
  assert.equal(text, JSON.stringify({ chain: 'Base', asset: A, account: null }));
  assert.deepEqual(Object.keys(JSON.parse(text)), ['chain', 'asset', 'account']);
});

function serve(userId) {
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => { if (userId) req.user = { user_id: userId }; next(); });
  delete require.cache[require.resolve('../routes/rwa_precheck')];
  app.use('/v1/rwa', require('../routes/rwa_precheck'));
  return http.createServer(app);
}

function post(userId, body) {
  return new Promise((resolve, reject) => {
    const s = serve(userId);
    s.listen(0, '127.0.0.1', () => {
      const data = JSON.stringify(body);
      const req = http.request({
        port: s.address().port, path: '/v1/rwa/precheck', method: 'POST',
        headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) },
      }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => { s.close(); resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }); });
      });
      req.on('error', (e) => { s.close(); reject(e); });
      req.end(data);
    });
  });
}

async function sealCount() {
  const [rows] = await pool.execute('SELECT scan_key FROM scan_seals', []);
  return rows.length;
}

test('the flag off is a 404, and a signed-in check seals v11 without the address', async () => {
  const prevFlag = process.env.RWA_PRECHECK_ENABLED;
  const prevRpc = process.env.RWA_RPC_URL_BASE;
  delete process.env.RWA_PRECHECK_ENABLED;
  try {
    const off = await post(null, { chain: 'base', asset: A });
    assert.equal(off.status, 404);
  } finally {
    if (prevFlag == null) delete process.env.RWA_PRECHECK_ENABLED;
    else process.env.RWA_PRECHECK_ENABLED = prevFlag;
  }

  process.env.RWA_PRECHECK_ENABLED = '1';
  process.env.RWA_RPC_URL_BASE = 'http://rpc.test/only';
  const real = global.fetch;
  global.fetch = async (_url, init) => {
    const body = JSON.parse(init.body);
    const scripted = rpcOf({ codes: { [A]: push4(SELECTOR_OF.forcedTransfer) }, calls: baseCalls() });
    const out = await scripted(body.method, body.params);
    if (!out.ok && out.reason === 'revert') {
      return { ok: true, status: 200, json: async () => ({ error: { code: 3, message: 'execution reverted' } }) };
    }
    if (!out.ok) return { ok: false, status: 599, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ result: out.result }) };
  };
  const before = await sealCount();
  try {
    const anon = await post(null, { chain: 'base', asset: A });
    assert.equal(anon.status, 200, JSON.stringify(anon.body));
    assert.equal(anon.body.read, true);
    assert.equal(anon.body.receipt, null);
    assert.equal(anon.body.disclaimer, DISCLAIMER);
    assert.equal(await sealCount(), before);

    const signed = await post(7, { chain: 'base', asset: '0x' + A.slice(2).toUpperCase() });
    assert.equal(signed.status, 200, JSON.stringify(signed.body));
    assert.ok(signed.body.receipt && signed.body.receipt.scan_key.startsWith('sc_'));
    const [rows] = await pool.execute(
      'SELECT tool, seal, seal_payload FROM scan_seals WHERE scan_key = ?',
      [signed.body.receipt.scan_key]);
    assert.equal(rows[0].tool, 'rwa_precheck');
    const payload = rows[0].seal_payload;
    const parsed = JSON.parse(payload);
    assert.equal(parsed.v, 11);
    assert.equal(parsed.kind, 'rwa_precheck');
    assert.equal(parsed.chain_id, 8453);
    assert.equal(parsed.block, '100');
    assert.ok(parsed.flags.includes('erc3643:detected'));
    assert.equal(rows[0].seal, crypto.createHash('sha256').update(payload, 'utf8').digest('hex'));
    assert.ok(!/0x[0-9a-fA-F]{40}/.test(payload));
    assert.equal(parsed.input_sha256, crypto.createHash('sha256')
      .update(canonicalInput('base', A, null), 'utf8').digest('hex'));
  } finally {
    global.fetch = real;
    if (prevFlag == null) delete process.env.RWA_PRECHECK_ENABLED;
    else process.env.RWA_PRECHECK_ENABLED = prevFlag;
    if (prevRpc == null) delete process.env.RWA_RPC_URL_BASE;
    else process.env.RWA_RPC_URL_BASE = prevRpc;
  }
});

test('the page says the disclaimer, names every control, and does not fetch on load', () => {
  assert.ok(HTML.includes(DISCLAIMER));
  assert.match(HTML, /styles\.css\?v=52/);
  assert.match(HTML, /src="\/js\/api-retry\.js\?v=3"/);
  assert.ok(HTML.indexOf('/js/api-retry.js') < HTML.indexOf('/js/rwa-precheck.js'));
  assert.equal((HTML.match(/<script\b/g) || []).length, 2);
  assert.ok(!/onclick=|onchange=/.test(HTML));
  assert.ok(!/aria-label=/.test(HTML));
  assert.match(HTML, /<label for="rwa-chain">/);
  assert.match(HTML, /<label for="rwa-asset">/);
  assert.match(HTML, /<label for="rwa-account">/);
  assert.equal(HTML.match(/property="og:site_name" content="([^"]*)"/)[1], 'Humanoid Traders');
  assert.ok(!/console\.error/.test(PAGE_JS));
  assert.ok(!/fetch\(/.test(PAGE_JS.slice(0, PAGE_JS.indexOf('async function run'))));
  assert.match(PAGE_JS, /AbortSignal\.timeout\(/);
  assert.ok(!/tone-good|#00ff|recommended/.test(PAGE_JS));
});

test('the page is gated ahead of static, and the flag hides it', async () => {
  const serverSrc = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
  const gateAt = serverSrc.indexOf("require('./lib/rwa_flag').pageGate");
  const staticAt = serverSrc.indexOf('express.static');
  assert.ok(gateAt > 0 && gateAt < staticAt, 'the gate is mounted before static');

  const prev = process.env.RWA_PRECHECK_ENABLED;
  const app = express();
  app.use(pageGate);
  const server = await new Promise((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
  try {
    delete process.env.RWA_PRECHECK_ENABLED;
    const off = await fetch(`http://127.0.0.1:${server.address().port}/rwa`);
    assert.equal(off.status, 404);
    process.env.RWA_PRECHECK_ENABLED = 'true';
    const on = await fetch(`http://127.0.0.1:${server.address().port}/rwa`);
    assert.equal(on.status, 200);
    assert.ok((await on.text()).includes(DISCLAIMER));
  } finally {
    server.close();
    if (prev == null) delete process.env.RWA_PRECHECK_ENABLED;
    else process.env.RWA_PRECHECK_ENABLED = prev;
  }
});
