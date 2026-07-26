'use strict';
// Live per-chain gas for the Escape planner — a public market fact, read over
// the verified keyless RPCs. Unreadable chains are OMITTED, never invented;
// the number is labeled indicative; and an empty read is never cached.

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { readGas, readGasCached, setGasFetcher, withXferCosts, XFER_GAS_UNITS }
  = require('../lib/gas_read.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const rpcOk = (weiHexByHost) => async (url) => ({
  ok: true,
  json: async () => {
    for (const [frag, hex] of Object.entries(weiHexByHost)) {
      if (url.includes(frag)) return { jsonrpc: '2.0', result: hex };
    }
    throw new Error('unreachable host');
  },
});

test('reads gwei per chain, sub-gwei precision for L2s', async () => {
  // 20 gwei on ethereum, 0.012 gwei on base; everything else unreachable.
  const g = await readGas(rpcOk({ 'publicnode.com': '0x4a817c800', 'base.org': '0xb71b00' }));
  assert.equal(g.indicative, true);
  assert.equal(g.chains.ethereum.gwei, 20);
  assert.equal(g.chains.base.gwei, 0.012);
});

test('an unreadable chain is absent — never zeroed, never carried', async () => {
  const g = await readGas(rpcOk({ 'base.org': '0xb71b00' }));
  assert.ok(g.chains.base);
  assert.ok(!('bnb' in g.chains) && !('optimism' in g.chains),
    'a chain we could not read must not appear in the answer');
});

test('a total outage answers an empty map, not a throw', async () => {
  const down = async () => { throw new Error('ECONNREFUSED'); };
  const g = await readGas(down);
  assert.deepEqual(g.chains, {});
});

test('garbage results are rotated past, not surfaced', async () => {
  const g = await readGas(async (url) => ({
    ok: true, json: async () => ({ jsonrpc: '2.0', result: '0x0' }),
  }));
  assert.deepEqual(g.chains, {}, 'a zero gas price is not a market read');
});

test('xfer cost: gas × a bridge-floor × the live native price — dollars from two market facts', async () => {
  // 20 gwei on ethereum with ETH at $2500: 20e-9 × 100k gas × 2500 = $5.
  const g = await readGas(rpcOk({ 'publicnode.com': '0x4a817c800' }));
  withXferCosts(g, { ETHUSDT: { price: 2500 } });
  assert.equal(g.chains.ethereum.native_usd, 2500);
  assert.equal(g.chains.ethereum.xfer_cost_usd, 20e-9 * XFER_GAS_UNITS * 2500);
  assert.equal(g.chains.ethereum.xfer_cost_usd, 5);
});

test('xfer cost: no fresh native mark → gwei stays, cost fields are absent', async () => {
  const g = await readGas(rpcOk({ 'base.org': '0xb71b00' }));
  withXferCosts(g, { BNBUSDT: { price: 600 } });          // wrong coin for base
  assert.equal(g.chains.base.gwei, 0.012, 'the gas read itself is untouched');
  assert.ok(!('xfer_cost_usd' in g.chains.base) && !('native_usd' in g.chains.base),
    'a cost we could not price is omitted, never invented');
  // Zero / garbage marks are refused the same way.
  const g2 = await readGas(rpcOk({ 'base.org': '0xb71b00' }));
  withXferCosts(g2, { ETHUSDT: { price: 0 } });
  assert.ok(!('xfer_cost_usd' in g2.chains.base));
  // And a missing map is a no-op, not a throw.
  assert.equal(withXferCosts(g2, null), g2);
});

test('the shared cache prices through the BOUNDED ticker read — nothing here waits on a cold fetch', () => {
  const src = read('lib', 'gas_read.js');
  assert.match(src, /getTickersWithin\(/, 'display path: bounded, with stale fallback');
  assert.doesNotMatch(src, /getTickers\(\)/,
    'the unbounded fetch can spend 10s — that budget belongs to order paths only');
  assert.match(read('routes', 'gas.js'), /readGasCached\(\)/,
    'the route and the MCP tool share ONE cache — two surfaces, one RPC sweep');
});

test('readGasCached: an empty read is never cached; a good read is', async () => {
  require('../lib/tickers').setTickerFetcher(async () => ({ ETHUSDT: { price: 2500 } }));
  try {
    setGasFetcher(async () => { throw new Error('ECONNREFUSED'); });
    const empty = await readGasCached();
    assert.deepEqual(empty.chains, {});
    // The outage must not freeze into "no data" for a minute: swapping in a
    // working fetcher resets the cache (test seam) AND the empty body was
    // never stored — either way the next caller gets a real read.
    let calls = 0;
    const ok = rpcOk({ 'publicnode.com': '0x4a817c800' });
    setGasFetcher(async (url, opts) => { calls++; return ok(url, opts); });
    const fresh = await readGasCached();
    assert.equal(fresh.chains.ethereum.gwei, 20);
    assert.equal(fresh.chains.ethereum.xfer_cost_usd, 5, 'priced through the shared path');
    const before = calls;
    const again = await readGasCached();
    assert.equal(calls, before, 'a good read IS cached — the second call is free');
    assert.deepEqual(again.chains, fresh.chains);
  } finally {
    setGasFetcher(null);
    require('../lib/tickers').setTickerFetcher(null);
  }
});

test('get_gas MCP tool: published-data family, the same honest read, floor stated as a floor', async () => {
  const { TOOLS } = require('../routes/mcp.js');
  assert.ok(TOOLS.get_gas, 'the gas read is agent-consumable');
  assert.ok(!TOOLS.get_gas.computesOnInput,
    'public market facts — the manifest family split must say published-data');
  assert.match(TOOLS.get_gas.description, /omitted, never invented/);
  require('../lib/tickers').setTickerFetcher(async () => ({ ETHUSDT: { price: 2000 } }));
  try {
    setGasFetcher(rpcOk({ 'base.org': '0xb71b00' }));
    const out = await TOOLS.get_gas.handler({});
    assert.equal(out.indicative, true);
    assert.equal(out.chains.base.gwei, 0.012);
    assert.equal(out.chains.base.native_usd, 2000);
    assert.match(out.note, /floor that can understate/);
  } finally {
    setGasFetcher(null);
    require('../lib/tickers').setTickerFetcher(null);
  }
});

test('the dust flag: bridged steps only, wallet-loaded values only, floor stated as a floor', () => {
  const page = read('public', 'escape.html');
  assert.match(page, /s\.type === 'bridged' && hit\.xfer_cost_usd > 0/,
    'the flag exists exactly where a bridge-home step meets a priced cost');
  assert.match(page, /usd: Number\(a\.usd\) \|\| 0/,
    'wallet rows carry their value; hand-typed rows never get the flag');
  assert.match(page, /pct >= 5/, 'below 5% the flag is noise, not honesty');
  assert.match(page, /es\.dust_t/, 'the floor-not-a-quote tooltip is the honesty surface');
  const i18n = require('../public/js/i18n.js');
  for (const k of ['es.dust', 'es.dust_t']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
      if (k === 'es.dust') {
        assert.ok(String(i18n.STRINGS[k][l.code]).includes('{p}'), `es.dust lost {p} in ${l.code}`);
      }
    }
  }
});

test('the route is public, and an empty read is never cached', () => {
  const src = read('routes', 'gas.js');
  assert.doesNotMatch(src, /authMiddleware/, 'gwei is a market fact — no login gate');
  assert.match(read('lib', 'gas_read.js'), /if \(Object\.keys\(body\.chains\)\.length\) cached =/,
    'caching an empty answer freezes a transient outage into "no data"');
  assert.match(read('server.js'), /app\.use\('\/api\/gas', require\('\.\/routes\/gas'\)\)/);
});

test('the escape page shows the chip only for chains it could read', () => {
  const page = read('public', 'escape.html');
  assert.match(page, /if \(!GAS \|\| !s\.chain\) return '';/);
  assert.match(page, /es\.gas_t/, 'the indicative-only tooltip is the honesty surface');
  assert.match(page, /omitted, never invented/);
  const i18n = require('../public/js/i18n.js');
  for (const k of ['es.gas', 'es.gas_t']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
    }
  }
});
