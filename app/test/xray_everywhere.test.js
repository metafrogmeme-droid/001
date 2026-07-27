'use strict';
/**
 * The X-ray goes everywhere: ENS names as input and a one-tap all-chains
 * sweep. Pins:
 * - forward resolution answers null for unset/unresolvable/rpc-failure —
 *   unresolved is never a zero address, and null is never cached;
 * - the resolve route registers BEFORE /:chain/:address (or 'resolve'
 *   would parse as a chain name);
 * - the page resolves BEFORE validating, and an unresolvable name scans
 *   nothing; the sweep excludes Solana for 0x addresses;
 * - the stale cloudflare-eth default (which silently killed the existing
 *   reverse-identity lookups too) is replaced and documented.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { resolveEnsName, setEnsProviderFactory } = require('../lib/ens');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

test.after(() => setEnsProviderFactory(null));

test('forward resolution: real answers cached, null never cached', async () => {
  let calls = 0;
  setEnsProviderFactory(() => ({
    resolveName: async (n) => { calls++; return n === 'set.eth' ? '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045' : null; },
  }));
  assert.strictEqual(await resolveEnsName('set.eth'), '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045');
  assert.strictEqual(await resolveEnsName('set.eth'), '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045');
  assert.strictEqual(calls, 1, 'a real answer is cached');
  assert.strictEqual(await resolveEnsName('unset.eth'), null);
  await resolveEnsName('unset.eth');
  assert.strictEqual(calls, 3, 'null retries — a flaky rpc must not pin failure');
  assert.strictEqual(await resolveEnsName('not-a-name'), null, 'non-.eth input refuses without rpc');
  assert.strictEqual(calls, 3);
});

test('rpc failure answers null, never a throw or zero', async () => {
  setEnsProviderFactory(() => ({ resolveName: async () => { throw new Error('down'); } }));
  assert.strictEqual(await resolveEnsName('vitalik.eth'), null);
});

test('the resolve route is registered before the chain route', () => {
  const src = read('routes', 'allowances.js');
  const resolveAt = src.indexOf("router.get('/resolve/:name'");
  const chainAt = src.indexOf("router.get('/:chain/:address'");
  assert.ok(resolveAt > 0 && chainAt > 0 && resolveAt < chainAt,
    "otherwise 'resolve' parses as a chain name");
});

test('the page resolves first, sweeps honestly', () => {
  const page = read('public', 'approvals.html');
  assert.match(page, /ENS_RE\.test\(a0\)/, 'names divert to resolution before validation');
  assert.match(page, /does not resolve — nothing was scanned/i);
  assert.match(page, /CHAINS\.filter\(function \(c\) \{ return c !== 'solana'; \}\)/,
    'the sweep excludes solana for 0x addresses');
  assert.match(page, /never averages truths together/, 'per-chain sections, honest in place');
  assert.match(page, /function renderBody\(d\)/, 'one renderer serves both paths');
});

test('the stale cloudflare default is gone and documented', () => {
  const src = read('lib', 'ens.js');
  assert.match(src, /RPC_DEFAULT = 'https:\/\/ethereum-rpc\.publicnode\.com'/);
  assert.match(src, /cloudflare-eth\.com answers -32046/, 'the reason survives in the code');
});
