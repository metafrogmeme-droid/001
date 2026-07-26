'use strict';
// Live per-chain gas for the Escape planner — a public market fact, read over
// the verified keyless RPCs. Unreadable chains are OMITTED, never invented;
// the number is labeled indicative; and an empty read is never cached.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { readGas } = require('../lib/gas_read.js');
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

test('the route is public, and an empty read is never cached', () => {
  const src = read('routes', 'gas.js');
  assert.doesNotMatch(src, /authMiddleware/, 'gwei is a market fact — no login gate');
  assert.match(src, /if \(Object\.keys\(body\.chains\)\.length\) cache =/,
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
