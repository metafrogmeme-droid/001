'use strict';
/**
 * Transaction X-ray — the decode is checked against REAL ABI encodings
 * (ethers builds the calldata, the dependency-free model must read it back),
 * and every selector in the model is pinned to its signature by keccak, so a
 * typo'd selector can never silently misdescribe a transaction.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { ethers } = require('ethers');

const X = require('../public/js/txray-model.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const IF = new ethers.Interface([
  'function approve(address,uint256)',
  'function increaseAllowance(address,uint256)',
  'function transfer(address,uint256)',
  'function transferFrom(address,address,uint256)',
  'function permit(address,address,uint256,uint256,uint8,bytes32,bytes32)',
  'function setApprovalForAll(address,bool)',
  'function safeTransferFrom(address,address,uint256)',
  'function multicall(bytes[])',
]);
const SPENDER = '0x' + 'ee'.repeat(20);
const OTHER = '0x' + 'dd'.repeat(20);

test('every selector in the model is the keccak of a real signature', () => {
  const sigs = ['approve(address,uint256)', 'increaseAllowance(address,uint256)',
    'transfer(address,uint256)', 'transferFrom(address,address,uint256)',
    'permit(address,address,uint256,uint256,uint8,bytes32,bytes32)',
    'setApprovalForAll(address,bool)', 'safeTransferFrom(address,address,uint256)',
    'safeTransferFrom(address,address,uint256,bytes)',
    'safeTransferFrom(address,address,uint256,uint256,bytes)', 'multicall(bytes[])'];
  const real = new Set(sigs.map((s) => ethers.id(s).slice(0, 10)));
  for (const sel of X.SELECTORS) {
    assert.ok(real.has(sel), `${sel} is not the selector of any pinned signature`);
  }
  assert.equal(X.SELECTORS.length, real.size, 'every pinned signature is decoded');
});

test('approve: exact allowance decoded; 2^128 is the unlimited line', () => {
  const below = X.decodeTx({ data: IF.encodeFunctionData('approve', [SPENDER, (1n << 128n) - 1n]) });
  assert.equal(below.actions[0].tid, 'txr.a_approve');
  assert.equal(below.actions[0].params.spender, SPENDER);
  assert.equal(below.actions[0].params.amount, ((1n << 128n) - 1n).toString());
  assert.equal(below.flags.length, 0, 'a finite allowance is not flagged');

  const at = X.decodeTx({ data: IF.encodeFunctionData('approve', [SPENDER, 1n << 128n]) });
  assert.equal(at.actions[0].params.unlimited, true);
  assert.deepEqual(at.flags, [{ id: 'unlimited_approval', sev: 'high' }]);

  const max = X.decodeTx({ data: IF.encodeFunctionData('approve', [SPENDER, ethers.MaxUint256]) });
  assert.equal(max.actions[0].params.amount, 'UNLIMITED');
});

test('setApprovalForAll: true is the drain primitive, false is a revocation', () => {
  const on = X.decodeTx({ data: IF.encodeFunctionData('setApprovalForAll', [SPENDER, true]) });
  assert.equal(on.actions[0].tid, 'txr.a_forall_on');
  assert.deepEqual(on.flags, [{ id: 'approval_for_all', sev: 'high' }]);
  const off = X.decodeTx({ data: IF.encodeFunctionData('setApprovalForAll', [SPENDER, false]) });
  assert.equal(off.actions[0].tid, 'txr.a_forall_off');
  assert.equal(off.flags.length, 0, 'revoking is the SAFE direction — no flag');
});

test('permit: flagged as a signature-moved approval', () => {
  const d = X.decodeTx({ data: IF.encodeFunctionData('permit',
    [OTHER, SPENDER, ethers.MaxUint256, 9999999999n, 27, ethers.ZeroHash, ethers.ZeroHash]) });
  assert.equal(d.actions[0].tid, 'txr.a_permit');
  assert.equal(d.actions[0].params.owner, OTHER);
  assert.deepEqual(d.flags.map((f) => f.id).sort(), ['gasless_permit', 'unlimited_approval']);
});

test('transfers decode exactly; raw units are called raw', () => {
  const t = X.decodeTx({ data: IF.encodeFunctionData('transfer', [OTHER, 123456n]) });
  assert.equal(t.actions[0].params.to, OTHER);
  assert.equal(t.actions[0].params.amount, '123456');
  assert.match(t.actions[0].en, /raw token units/,
    'without the token’s decimals a human number would be invented');
  const tf = X.decodeTx({ data: IF.encodeFunctionData('transferFrom', [OTHER, SPENDER, 7n]) });
  assert.equal(tf.actions[0].params.from, OTHER);
  const st = X.decodeTx({ data: IF.encodeFunctionData('safeTransferFrom', [OTHER, SPENDER, 42n]) });
  assert.equal(st.actions[0].params.id, '42');
});

test('multicall: inner calls are unwrapped and decoded individually', () => {
  const inner1 = IF.encodeFunctionData('approve', [SPENDER, ethers.MaxUint256]);
  const inner2 = IF.encodeFunctionData('transfer', [OTHER, 5n]);
  const d = X.decodeTx({ data: IF.encodeFunctionData('multicall', [[inner1, inner2]]) });
  assert.equal(d.actions[0].tid, 'txr.a_multicall');
  assert.equal(d.actions[0].params.n, 2);
  assert.equal(d.actions[1].tid, 'txr.a_approve');
  assert.equal(d.actions[2].tid, 'txr.a_transfer');
  assert.deepEqual(d.flags, [{ id: 'unlimited_approval', sev: 'high' }]);
});

test('unknown means unknown — never safe, never dangerous', () => {
  const d = X.decodeTx({ data: '0xdeadbeef' + '00'.repeat(64) });
  assert.equal(d.unknown, true);
  assert.equal(d.actions[0].tid, 'txr.a_unknown');
  assert.match(d.actions[0].en, /Unknown is not the same as safe/);
  assert.deepEqual(d.flags, [{ id: 'unknown_function', sev: 'info' }]);
});

test('native value: exact wei→coin arithmetic, no rounding invention', () => {
  const d = X.decodeTx({ data: '0x', value: '1500000000000000000' });
  assert.equal(d.actions[0].params.eth, '1.5');
  const tiny = X.decodeTx({ data: '0x', value: '1' });
  assert.equal(tiny.actions[0].params.eth, '0.000000000000000001');
  const junk = X.decodeTx({ data: '0x12', value: 'lol' });
  assert.equal(junk.invalid, true);
  const empty = X.decodeTx({ data: '', value: '0' });
  assert.equal(empty.empty, true);
});

test('the MCP tool serves the same decode, caller-input family, stores nothing', async () => {
  const { TOOLS } = require('../routes/mcp.js');
  assert.ok(TOOLS.xray_transaction);
  assert.equal(TOOLS.xray_transaction.computesOnInput, true,
    'it evaluates what the CALLER sends — the manifest family split must say so');
  const out = await TOOLS.xray_transaction.handler({
    data: IF.encodeFunctionData('setApprovalForAll', [SPENDER, true]) });
  assert.equal(out.actions[0].tid, 'txr.a_forall_on');
  assert.match(out.note, /Nothing sent here is stored/);
  assert.match(out.note, /[Hh]euristic/);
});

test('the firewall page renders the X-ray through tids with English fallback', () => {
  const page = read('public', 'firewall.html');
  assert.match(page, /txray-model\.js/);
  assert.match(page, /XR\.decodeTx/);
  assert.match(page, /XT\(a\.tid, a\.en\)/,
    'per-tid translation with the model’s English as fallback — whole-line, never mixed');
  assert.match(page, /XT\('txr\.f_' \+ f\.id/, 'flags translate by stable id too');
  const i18n = require('../public/js/i18n.js');
  // Slot coverage derived from the ENGLISH entries, so new slots auto-covered.
  for (const k of Object.keys(i18n.STRINGS).filter((x) => x.startsWith('txr.') || x.startsWith('fw.x_'))) {
    const en = String(i18n.STRINGS[k].en || '');
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
  assert.ok(Object.keys(i18n.STRINGS).some((x) => x.startsWith('txr.a_approve')),
    'the action tids exist in the dictionary');
});
