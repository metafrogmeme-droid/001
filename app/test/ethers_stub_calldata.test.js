'use strict';
/**
 * PRODUCTION, REPRODUCED. This file installs the `ethers` stub that the live
 * host actually resolves and then drives every value this server hands to a
 * user's wallet through it.
 *
 * The stub is not hypothetical. On 2026-08-23 `/api/tool/registration-plan`
 * served
 *
 *     manifest_hash: 0x848ee4da2d488d6ad2d3a20b6568f62a73e5b84e9c93feaa484c979b3f033a97
 *
 * a well-formed 32-byte hex string that is plain SHA-256 of the canonical
 * manifest. The true keccak256 is 0x7fe3e5ec…b053. The stub's `keccak256` was
 * backed by Node's `crypto`, which has no keccak256 at all, so it fell through
 * to SHA-256; and its `Interface.encodeFunctionData` returned the string
 * `'0x'`. Both are the correct SHAPE and neither is detectable from any
 * response the server returns.
 *
 * Every other test in this repo ran against the REAL `ethers`, which is
 * installed on the build machine, and every one of them passed for as long as
 * the bug existed. That is the #999 shape one level up: the code was present
 * and the code was correct; the thing production ran was something else.
 *
 * So: three wallet-facing calls, all built under the stub, none allowed to be
 * wrong or empty.
 *
 *   registerTool  a wrong hash is written to Base permanently and no ERC-8257
 *                 verifier can ever reproduce it
 *   approve(s,0)  the REVOKE. '0x' calldata means the user is shown "revoke
 *                 this approval", their wallet confirms, the explorer says
 *                 success, and the unlimited allowance is untouched
 *   mint(voucher) gas spent, nothing minted
 *
 * Node's test runner gives each file its own process, so poisoning the module
 * cache here cannot leak into another suite.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://runeclaw.test';
process.env.TOOL_CREATOR_ADDRESS = '0x' + 'ab'.repeat(20);
process.env.NFT_CONTRACT_ADDRESS = '0x' + 'cc'.repeat(20);
process.env.NFT_VOUCHER_KEY = '0x' + '11'.repeat(32);

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

// ── install the stub, exactly as production has it ───────────────────────────

const STUB_SIG = '0x' + 'ab'.repeat(65);

const stub = {
  // "backed by Node's crypto" — and crypto has no keccak256, so this is what
  // the fallback produced.
  keccak256(bytes) {
    const b = typeof bytes === 'string' && bytes.startsWith('0x')
      ? Buffer.from(bytes.slice(2), 'hex') : Buffer.from(bytes);
    return '0x' + crypto.createHash('sha256').update(b).digest('hex');
  },
  toUtf8Bytes: (s) => Buffer.from(String(s), 'utf8'),
  toBigInt: (v) => BigInt(v),
  getAddress: (a) => String(a),
  isAddress: (a) => /^0x[0-9a-fA-F]{40}$/.test(String(a)),
  Wallet: class { async signTypedData() { return STUB_SIG; } },
  Interface: class {
    constructor() { /* accepts any ABI, understands none of it */ }
    encodeFunctionData() { return '0x'; }
    decodeFunctionResult() { throw new Error('stub cannot decode'); }
  },
};

const ethersPath = require.resolve('ethers');
require.cache[ethersPath] = {
  id: ethersPath, filename: ethersPath, loaded: true, exports: { ethers: stub, ...stub },
};

// Loaded AFTER the poisoning, so they get the stub the way production does.
const t8257 = require('../lib/tool8257');
const allow = require('../lib/allowances');
const nft = require('../lib/nft');
const abi = require('../lib/abi_call');
const keccak = require('../lib/keccak256');

// ── the stub is actually installed ───────────────────────────────────────────

test('the stub is really in place — otherwise every test below is vacuous', () => {
  // A reproduction that silently fails to reproduce is worse than no test: it
  // reports the bug as fixed while exercising the code that was never broken.
  const { ethers } = require('ethers');
  assert.equal(ethers.keccak256(ethers.toUtf8Bytes('hello')),
    '0x2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
    'the poisoned require did not take effect');
  assert.equal(new ethers.Interface([]).encodeFunctionData('anything', []), '0x');
});

// ── 1. the manifest hash ─────────────────────────────────────────────────────

test('manifestHash is true keccak256 even when ethers answers sha256', () => {
  const m = { name: 'x', tags: ['a'] };
  const canonical = '{"name":"x","tags":["a"]}';
  const sha256 = '0x' + crypto.createHash('sha256').update(canonical).digest('hex');

  const got = t8257.manifestHash(m);
  assert.notEqual(got, sha256, 'the hash came from the stub — this is the bug');
  assert.equal(got, keccak.keccak256Utf8(canonical));
  // Pinned by value so the assertion cannot be satisfied by two broken things
  // agreeing with each other.
  assert.equal(got, '0x9b0fab36b9f2ebc161b01feeaa8b4d5137d7c34f1d6f73110c4935f73f3993c6');
});

test('the served plan carries a keccak256 hash, a preimage, and says which', () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const plan = t8257.buildRegistrationPlan({ tools: TOOLS });

  assert.equal(plan.ready, true);
  assert.match(plan.manifest_hash, /^0x[0-9a-f]{64}$/);
  assert.match(plan.hash_algorithm, /keccak256/);
  assert.match(plan.hash_algorithm, /NOT SHA-256/);

  // The published preimage must actually hash to the published hash — that is
  // the whole point of shipping it, and it is the check nobody could run while
  // the endpoint was serving sha256.
  assert.equal(keccak.keccak256Utf8(plan.manifest_canonical), plan.manifest_hash);

  // And it must NOT be any of the plausible wrong answers.
  const c = plan.manifest_canonical;
  for (const alg of ['sha256', 'sha3-256', 'sha512-256']) {
    assert.notEqual(plan.manifest_hash,
      '0x' + crypto.createHash(alg).update(c).digest('hex'),
      `manifest_hash is ${alg}, not keccak256`);
  }
});

// ── 2. the calldata ──────────────────────────────────────────────────────────

test("registerTool calldata is a real call, not the stub's '0x'", () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const plan = t8257.buildRegistrationPlan({ tools: TOOLS });

  assert.notEqual(plan.calldata, '0x',
    'a transaction with empty data succeeds, costs gas and registers nothing');
  assert.equal(plan.calldata.slice(0, 10),
    abi.selector('registerTool(string,bytes32,address)'));
  // The hash is INSIDE the calldata; a plan whose two fields disagree is worse
  // than either being wrong alone.
  assert.ok(plan.calldata.includes(plan.manifest_hash.slice(2)),
    'the calldata does not carry the hash the plan reports');
});

test('the REVOKE plan is a real approve(spender, 0)', async () => {
  // The most dangerous '0x' in the repo: a security control that reports
  // success while leaving an unlimited allowance in place.
  allow.setEthCaller(async () => '0x' + 'f'.repeat(64));   // unlimited, every pair
  try {
    const out = await allow.readAllowances('0x' + '99'.repeat(20), 'ethereum');
    assert.ok(out.findings.length > 0, 'no findings — the fixture did not drive it');
    for (const f of out.findings) {
      const d = f.revoke_plan.data;
      assert.notEqual(d, '0x', `${f.token}/${f.spender_label}: revoke does nothing`);
      assert.equal(d.slice(0, 10), '0x095ea7b3', 'not approve(address,uint256)');
      assert.equal(d.length, 2 + 8 + 128, 'selector + spender word + amount word');
      // approve(spender, 0): the spender in the low 20 bytes, amount zero.
      assert.equal(d.slice(10 + 24, 10 + 64).toLowerCase(),
        f.spender.slice(2).toLowerCase());
      assert.equal(d.slice(10 + 64), '0'.repeat(64), 'the amount is not zero');
    }
  } finally {
    allow.setEthCaller(null);
  }
});

test('the mint calldata is a real mint(bytes)', async () => {
  nft.setNftFetcher(async () => ({ ok: true, json: async () => ({ result: '0x' + '00'.repeat(32) }) }));
  try {
    const plan = await nft.buildMintPlan('0x' + '77'.repeat(20));
    assert.equal(plan.ready, true, plan.not_ready_reasons);
    assert.notEqual(plan.calldata, '0x');
    assert.equal(plan.calldata.slice(0, 10), abi.selector('mint(bytes)'));
    assert.ok(plan.calldata.includes(STUB_SIG.slice(2)),
      'the voucher signature is not in the calldata');
  } finally {
    nft.setNftFetcher(null);
  }
});

// ── 3. what a stub CAN still break, and how that reads ───────────────────────

test('a hashing failure yields no hash and no calldata, never a placeholder', () => {
  // Absent is never a measurement. If the digest cannot be computed the plan
  // must say so and carry nothing sendable — the alternative is a field that
  // looks exactly like the working case.
  const TOOLS = require('../routes/mcp').TOOLS;
  const path = require.resolve('../lib/keccak256');
  const real = keccak.keccak256Utf8;
  keccak.keccak256Utf8 = () => { throw new Error('transpiled to nonsense'); };
  try {
    const plan = t8257.buildRegistrationPlan({ tools: TOOLS });
    assert.equal(plan.ready, false);
    assert.equal(plan.manifest_hash, null);
    assert.equal(plan.calldata, null);
    assert.ok(plan.not_ready_reasons.some((r) => /hashing failed/.test(r)));
    assert.match(plan.instructions[0], /DO NOT REGISTER/);
    assert.ok(plan.instructions.some((i) => /no calldata in this plan/.test(i)));
    assert.ok(!plan.instructions.some((i) => /cast send/.test(i)),
      'a cast command interpolating a null hash would read as a usable one');
  } finally {
    keccak.keccak256Utf8 = real;
    assert.equal(require.cache[path].exports.keccak256Utf8, real);
  }
});
