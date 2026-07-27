'use strict';
/**
 * Allowance X-ray — the after-the-fact half of the firewall. The contract:
 * - BOUNDED and said out loud: only curated tokens × registry spenders;
 *   the note states that a clean result is never a guarantee;
 * - the spender registry is deterministic-deploy only, and the Uniswap
 *   router — whose address hosts DIFFERENT bytecode on Base — is chain-
 *   scoped explicitly (address-reuse is never trusted by code presence);
 * - UNLIMITED uses the exact 2^128 threshold the transaction X-ray uses;
 * - unreadable pairs are counted, never shown as zero;
 * - the revoke plan is approve(spender, 0) on the TOKEN, zero value, and
 *   the server never signs or sends it (there is no signing path at all).
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const express = require('express');
const { ethers } = require('ethers');
const { readAllowances, setEthCaller, SPENDERS, UNLIMITED_MIN, spendersFor } = require('../lib/allowances');

const OWNER = '0x020A4366595292aa16184536bAB2E9949d0D925F';
const pad32 = (v) => '0x' + v.toString(16).padStart(64, '0');

test.after(() => setEthCaller(null));

test('the spender registry is verified-deterministic, router chain-scoped', () => {
  const router = SPENDERS.find((s) => s.label.includes('SwapRouter02'));
  assert.deepEqual(router.chains, ['ethereum', 'arbitrum', 'optimism', 'polygon'],
    'the Base impostor bytecode keeps the router OFF Base/BNB/Avalanche');
  for (const s of SPENDERS) {
    assert.match(s.address, /^0x[0-9a-fA-F]{40}$/);
  }
  // Base gets four spenders (no router); Ethereum gets all five.
  assert.strictEqual(spendersFor('base').length, 4);
  assert.strictEqual(spendersFor('ethereum').length, 5);
  assert.strictEqual(UNLIMITED_MIN, 2n ** 128n, 'the X-ray halves agree');
});

test('findings carry honest amounts and a revoke plan the owner sends', async () => {
  // Stub: USDC→Permit2 unlimited, USDC→1inch a finite grant, all else zero.
  setEthCaller(async (chain, to, data) => {
    const spender = '0x' + data.slice(-40);
    if (spender.toLowerCase() === '0x000000000022d473030f116ddee9f6b43ac78ba3'
      && to === '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913') return pad32((2n ** 256n) - 1n);
    if (spender.toLowerCase() === '0x1111111254eeb25477b68fb85ed929f73a960582'
      && to === '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913') return pad32(2_500_000n); // 2.5 USDC
    return pad32(0n);
  });
  const r = await readAllowances(OWNER, 'base');
  assert.strictEqual(r.findings.length, 2);
  const [unlim, fin] = r.findings[0].unlimited ? r.findings : [r.findings[1], r.findings[0]];
  assert.strictEqual(unlim.unlimited, true);
  assert.strictEqual(unlim.spender_label, 'Permit2 (Uniswap)');
  assert.strictEqual(fin.unlimited, false);
  assert.strictEqual(fin.allowance_raw, '2500000', 'raw units, exactly what the chain said');
  // the revoke plan: approve(spender, 0) on the token, zero value
  const iface = new ethers.Interface(['function approve(address, uint256)']);
  const dec = iface.decodeFunctionData('approve', unlim.revoke_plan.data);
  assert.strictEqual(dec[0].toLowerCase(), unlim.spender.toLowerCase());
  assert.strictEqual(dec[1], 0n);
  assert.strictEqual(unlim.revoke_plan.to, unlim.token_address, 'sent TO the token');
  assert.strictEqual(unlim.revoke_plan.value, '0x0');
  assert.strictEqual(r.zero_pairs, 14, 'the rest of the 16-pair grid is zero');
});

test('unreadable pairs are counted, never shown as zero', async () => {
  setEthCaller(async () => { throw new Error('rpc down'); });
  const r = await readAllowances(OWNER, 'base');
  assert.strictEqual(r.findings.length, 0);
  assert.strictEqual(r.zero_pairs, 0);
  assert.strictEqual(r.unreadable_pairs, 16, 'every pair reported unreadable');
  assert.match(r.note, /not a guarantee/);
  assert.match(r.note, /never signs and never sends/);
});

test('bad inputs answer errors, not empty scans', async () => {
  assert.strictEqual((await readAllowances('nope', 'base')).error, 'bad address');
  assert.strictEqual((await readAllowances(OWNER, 'notachain')).error, 'unknown chain');
});

test('the route serves the grid and refuses to cache a dead read', async () => {
  setEthCaller(async () => pad32(0n));
  const app = express();
  app.use('/api/allowances', require('../routes/allowances'));
  const server = app.listen(0, '127.0.0.1');
  await new Promise((res) => server.on('listening', res));
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (p) => new Promise((resolve, reject) => {
    http.get(`${base}${p}`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks)) }));
    }).on('error', reject);
  });
  const ok = await get(`/api/allowances/base/${OWNER}`);
  assert.strictEqual(ok.status, 200);
  assert.strictEqual(ok.body.read_only, true);
  assert.strictEqual(ok.body.zero_pairs, 16);
  const bad = await get('/api/allowances/base/nothex');
  assert.strictEqual(bad.status, 400);
  // never-cache-a-dead-read: pinned in source
  const src = require('fs').readFileSync(
    require('path').join(__dirname, '..', 'routes', 'allowances.js'), 'utf8');
  assert.match(src, /body\.findings\.length \|\| body\.zero_pairs > 0/,
    'a fully-unreadable read is not cached');
  server.close();
});

test('the page is wired everywhere and carries the honesty copy', () => {
  const fs = require('fs');
  const path = require('path');
  const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
  assert.match(read('server.js'), /app\.get\('\/approvals'/);
  assert.match(read('server.js'), /'\/api\/allowances'/);
  assert.match(read('public', 'sitemap.xml'), /\/approvals<\/loc>/);
  assert.match(read('public', 'llms.txt'), /\/api\/allowances\/<chain>\/<address>/);
  const page = read('public', 'approvals.html');
  assert.match(page, /never a guarantee/);
  assert.match(page, /never signs, never sends, never holds a key/);
  assert.match(page, /reveal-on-scroll/);
  assert.match(page, /rc-constellation/);
  const m = page.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 86);
});
