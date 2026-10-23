'use strict';
/**
 * The signature X-ray (public/js/sigray-model.js) says what an off-chain
 * signature hands over, and claims a meaning only for the standards' own
 * types.
 *
 * The EIP-712 hash commits to the type string, so the model's table of
 * standard types is pinned here to the type strings the standards publish:
 * EIP-2612, DAI's permit, and Permit2's PermitHash.sol
 * (github.com/Uniswap/permit2, src/libraries/PermitHash.sol, read 22 Oct 2026).
 * Every fixture is a typed-data block ethers can hash, so none of them is a
 * shape no wallet would ever show.
 */

const test = require('node:test');
const assert = require('node:assert');
const { ethers } = require('ethers');

const S = require('../public/js/sigray-model.js');

const NOW = 1792800000; // 2026-10-24T00:00:00Z
const DAY = 86400;
const TOKEN = '0x' + '1a'.repeat(20);
const SPENDER = '0x' + 'ee'.repeat(20);
const OWNER = '0x' + 'dd'.repeat(20);
const U160_MAX = ((1n << 160n) - 1n).toString();
const U256_MAX = ((1n << 256n) - 1n).toString();

const ids = (r) => r.flags.map((f) => f.id).sort();
const tids = (r) => r.actions.map((a) => a.tid);

/** The published type strings, as ethers encodes the model's table. */
test('every standard type in the model is the type its standard publishes', () => {
  const enc = (types, primary) => ethers.TypedDataEncoder.from(types).encodeType(primary);
  const T = S.STANDARD;
  assert.strictEqual(enc({ Permit: T.Permit2612 }, 'Permit'),
    'Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)');
  assert.strictEqual(enc({ Permit: T.PermitDai }, 'Permit'),
    'Permit(address holder,address spender,uint256 nonce,uint256 expiry,bool allowed)');
  assert.strictEqual(enc({ PermitSingle: T.PermitSingle, PermitDetails: T.PermitDetails }, 'PermitSingle'),
    'PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)'
    + 'PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)');
  assert.strictEqual(enc({ PermitBatch: T.PermitBatch, PermitDetails: T.PermitDetails }, 'PermitBatch'),
    'PermitBatch(PermitDetails[] details,address spender,uint256 sigDeadline)'
    + 'PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)');
  assert.strictEqual(enc({ PermitTransferFrom: T.PermitTransferFrom, TokenPermissions: T.TokenPermissions },
    'PermitTransferFrom'),
  'PermitTransferFrom(TokenPermissions permitted,address spender,uint256 nonce,uint256 deadline)'
    + 'TokenPermissions(address token,uint256 amount)');
  assert.strictEqual(enc({ PermitBatchTransferFrom: T.PermitBatchTransferFrom, TokenPermissions: T.TokenPermissions },
    'PermitBatchTransferFrom'),
  'PermitBatchTransferFrom(TokenPermissions[] permitted,address spender,uint256 nonce,uint256 deadline)'
    + 'TokenPermissions(address token,uint256 amount)');
  assert.strictEqual(S.PERMIT2, '0x000000000022d473030f116ddee9f6b43ac78ba3');
});

function typed(primaryType, types, message, domain) {
  const td = {
    domain: domain || { name: 'Token', version: '1', chainId: 8453, verifyingContract: TOKEN },
    types: { EIP712Domain: [{ name: 'name', type: 'string' }, { name: 'version', type: 'string' },
      { name: 'chainId', type: 'uint256' }, { name: 'verifyingContract', type: 'address' }], ...types },
    primaryType, message,
  };
  // A fixture ethers cannot hash is a shape no wallet would show.
  const { EIP712Domain, ...rest } = td.types;
  ethers.TypedDataEncoder.hash(td.domain, rest, td.message);
  return td;
}

const permit = (value, deadline, fields) => typed('Permit', { Permit: fields || S.STANDARD.Permit2612 },
  { owner: OWNER, spender: SPENDER, value, nonce: '0', deadline });

test('an unlimited permit that can be submitted for a year says all three things', () => {
  const r = S.decodeTypedData(permit(U256_MAX, String(NOW + 365 * DAY)), { now: NOW });
  assert.deepStrictEqual(tids(r), ['sgr.a_permit']);
  assert.deepStrictEqual(ids(r), ['gasless_permit', 'long_lived', 'unlimited_approval']);
  assert.strictEqual(r.actions[0].params.amount, 'UNLIMITED');
  assert.strictEqual(r.actions[0].params.token, TOKEN);
  assert.strictEqual(r.actions[0].params.spender, SPENDER);
  assert.strictEqual(r.unknown, false);
});

test('a bounded permit for the next hour is still a permit, and only that', () => {
  const r = S.decodeTypedData(permit('1000000', String(NOW + 3600)), { now: NOW });
  assert.deepStrictEqual(ids(r), ['gasless_permit']);
  assert.strictEqual(r.actions[0].params.amount, '1000000');
  assert.strictEqual(r.actions[0].params.unlimited, false);
});

test('a permit past its deadline says it can no longer be submitted', () => {
  const r = S.decodeTypedData(permit('5', String(NOW - 1)), { now: NOW });
  assert.ok(ids(r).includes('expired'));
  assert.ok(!ids(r).includes('long_lived'));
});

test('a long-lived line sits exactly at thirty days', () => {
  const at = S.decodeTypedData(permit('5', String(NOW + 30 * DAY)), { now: NOW });
  const past = S.decodeTypedData(permit('5', String(NOW + 30 * DAY + 1)), { now: NOW });
  assert.ok(!ids(at).includes('long_lived'));
  assert.ok(ids(past).includes('long_lived'));
});

test('a "Permit" whose fields are not the standard\'s is not decoded as one', () => {
  const swapped = [...S.STANDARD.Permit2612];
  [swapped[3], swapped[4]] = [swapped[4], swapped[3]];
  const r = S.decodeTypedData(permit(U256_MAX, String(NOW + 365 * DAY), swapped), { now: NOW });
  assert.strictEqual(r.unknown, true);
  assert.deepStrictEqual(tids(r), ['sgr.a_unknown']);
  assert.deepStrictEqual(ids(r), ['unknown_message']);
  assert.strictEqual(r.actions[0].params.type, 'Permit');
});

const P2 = (primaryType, types, message, verifyingContract) => typed(primaryType, types, message,
  { name: 'Permit2', chainId: 8453, verifyingContract: verifyingContract || S.PERMIT2 });

const single = (amount, expiration, sigDeadline, at) => P2('PermitSingle',
  { PermitSingle: S.STANDARD.PermitSingle, PermitDetails: S.STANDARD.PermitDetails },
  { details: { token: TOKEN, amount, expiration, nonce: '0' }, spender: SPENDER, sigDeadline }, at);

test('the same names with a different type are not the standard either', () => {
  const retyped = S.STANDARD.Permit2612.map((f) => (f.name === 'value' ? { name: 'value', type: 'uint128' } : f));
  const td = typed('Permit', { Permit: retyped },
    { owner: OWNER, spender: SPENDER, value: '5', nonce: '0', deadline: String(NOW + 60) });
  assert.strictEqual(S.decodeTypedData(td, { now: NOW }).unknown, true);
});

test('a standard type with a field added is not the standard', () => {
  const longer = [...S.STANDARD.Permit2612, { name: 'extra', type: 'uint256' }];
  const td = typed('Permit', { Permit: longer },
    { owner: OWNER, spender: SPENDER, value: U256_MAX, nonce: '0', deadline: String(NOW + 60), extra: '1' });
  assert.strictEqual(S.decodeTypedData(td, { now: NOW }).unknown, true);
});

test('a Permit2 allowance names the token, the spender, the amount and when it ends', () => {
  const r = S.decodeTypedData(single(U160_MAX, String(NOW + 400 * DAY), String(NOW + 600)), { now: NOW });
  assert.deepStrictEqual(tids(r), ['sgr.a_p2_allow']);
  assert.deepStrictEqual(ids(r), ['long_lived', 'unlimited_approval']);
  const p = r.actions[0].params;
  assert.strictEqual(p.token, TOKEN);
  assert.strictEqual(p.spender, SPENDER);
  assert.strictEqual(p.amount, 'UNLIMITED');
  assert.strictEqual(p.expires, String(NOW + 400 * DAY));
});

test('a Permit2 expiration of 0 is this block only, not never', () => {
  const r = S.decodeTypedData(single('7', '0', String(NOW + 600)), { now: NOW });
  assert.strictEqual(r.actions[0].params.expires, 'BLOCK');
  assert.deepStrictEqual(ids(r), []);
});

test('Permit2\'s names on any other contract are flagged as a look-alike', () => {
  const r = S.decodeTypedData(single('7', String(NOW + 60), String(NOW + 60), TOKEN), { now: NOW });
  assert.ok(ids(r).includes('permit2_lookalike'));
  const real = S.decodeTypedData(single('7', String(NOW + 60), String(NOW + 60)), { now: NOW });
  assert.ok(!ids(real).includes('permit2_lookalike'));
});

test('a Permit2 batch reads every allowance in it', () => {
  const td = P2('PermitBatch', { PermitBatch: S.STANDARD.PermitBatch, PermitDetails: S.STANDARD.PermitDetails },
    { details: [
      { token: TOKEN, amount: '1', expiration: String(NOW + 60), nonce: '0' },
      { token: OWNER, amount: U160_MAX, expiration: String(NOW + 60), nonce: '0' }],
    spender: SPENDER, sigDeadline: String(NOW + 60) });
  const r = S.decodeTypedData(td, { now: NOW });
  assert.deepStrictEqual(tids(r), ['sgr.a_p2_batch', 'sgr.a_p2_allow', 'sgr.a_p2_allow']);
  assert.strictEqual(r.actions[0].params.n, 2);
  assert.strictEqual(r.actions[2].params.token, OWNER);
  assert.deepStrictEqual(ids(r), ['unlimited_approval']);
});

const WITNESS = [{ name: 'info', type: 'OrderInfo' }];
const ORDER_INFO = [{ name: 'reactor', type: 'address' }, { name: 'deadline', type: 'uint256' }];

test('a Permit2 transfer with a witness is decoded, and the witness is said to be undecoded', () => {
  const t = [...S.STANDARD.PermitTransferFrom, { name: 'witness', type: 'ExclusiveDutchOrder' }];
  const td = P2('PermitWitnessTransferFrom',
    { PermitWitnessTransferFrom: t, TokenPermissions: S.STANDARD.TokenPermissions,
      ExclusiveDutchOrder: WITNESS, OrderInfo: ORDER_INFO },
    { permitted: { token: TOKEN, amount: '25' }, spender: SPENDER, nonce: '1', deadline: String(NOW + 60),
      witness: { info: { reactor: SPENDER, deadline: String(NOW + 60) } } });
  const r = S.decodeTypedData(td, { now: NOW });
  assert.deepStrictEqual(tids(r), ['sgr.a_p2_take', 'sgr.a_p2_witness']);
  assert.deepStrictEqual(ids(r), ['signature_transfer', 'undecoded_witness']);
  assert.strictEqual(r.actions[1].params.witness, 'ExclusiveDutchOrder');
});

test('a "witness" transfer with no witness field is not decoded', () => {
  const td = P2('PermitWitnessTransferFrom',
    { PermitWitnessTransferFrom: S.STANDARD.PermitTransferFrom, TokenPermissions: S.STANDARD.TokenPermissions },
    { permitted: { token: TOKEN, amount: '25' }, spender: SPENDER, nonce: '1', deadline: String(NOW + 60) });
  assert.strictEqual(S.decodeTypedData(td, { now: NOW }).unknown, true);
});

test('a batch of one-time transfers reads each one', () => {
  const td = P2('PermitBatchTransferFrom',
    { PermitBatchTransferFrom: S.STANDARD.PermitBatchTransferFrom, TokenPermissions: S.STANDARD.TokenPermissions },
    { permitted: [{ token: TOKEN, amount: '1' }, { token: OWNER, amount: U256_MAX }],
      spender: SPENDER, nonce: '1', deadline: String(NOW + 60) });
  const r = S.decodeTypedData(td, { now: NOW });
  assert.deepStrictEqual(tids(r), ['sgr.a_p2_take_batch', 'sgr.a_p2_take', 'sgr.a_p2_take']);
  assert.deepStrictEqual(ids(r), ['signature_transfer', 'unlimited_approval']);
});

const dai = (allowed, expiry) => typed('Permit', { Permit: S.STANDARD.PermitDai },
  { holder: OWNER, spender: SPENDER, nonce: '0', expiry, allowed });

test('DAI\'s permit: allowed is unlimited, an expiry of 0 never ends, and a revoke is a revoke', () => {
  const on = S.decodeTypedData(dai(true, '0'), { now: NOW });
  assert.deepStrictEqual(tids(on), ['sgr.a_permit_dai_on']);
  assert.deepStrictEqual(ids(on), ['gasless_permit', 'long_lived', 'unlimited_approval']);
  assert.strictEqual(on.actions[0].params.deadline, 'NEVER');
  const off = S.decodeTypedData(dai(false, String(NOW + 60)), { now: NOW });
  assert.deepStrictEqual(tids(off), ['sgr.a_permit_dai_off']);
  assert.deepStrictEqual(ids(off), ['gasless_permit']);
});

test('the wallet\'s own request shapes decode the same as the object', () => {
  const td = permit(U256_MAX, String(NOW + 365 * DAY));
  const a = S.decodeTypedData(td, { now: NOW });
  const b = S.decodeTypedData(JSON.stringify(td), { now: NOW });
  const c = S.decodeTypedData([OWNER, JSON.stringify(td)], { now: NOW });
  assert.deepStrictEqual(b, a);
  assert.deepStrictEqual(c, a);
});

test('a number JSON already rounded is not read as the number signed', () => {
  const td = permit('1', String(NOW + 60));
  td.message.value = 2 ** 60; // past 2^53: whatever was signed, this is not it
  const r = S.decodeTypedData(td, { now: NOW });
  assert.strictEqual(r.unknown, true);
  assert.deepStrictEqual(ids(r), ['undecodable', 'unknown_message']);
});

test('the chain: one the wallet is not on, and none at all, are both said', () => {
  const td = permit('1', String(NOW + 60));
  assert.ok(ids(S.decodeTypedData(td, { now: NOW, chainId: 1 })).includes('chain_mismatch'));
  assert.ok(!ids(S.decodeTypedData(td, { now: NOW, chainId: 8453 })).includes('chain_mismatch'));
  const noChain = typed('Permit', { Permit: S.STANDARD.Permit2612 },
    { owner: OWNER, spender: SPENDER, value: '1', nonce: '0', deadline: String(NOW + 60) },
    { name: 'Token', version: '1', verifyingContract: TOKEN });
  delete noChain.types.EIP712Domain;
  assert.ok(ids(S.decodeTypedData(noChain, { now: NOW })).includes('no_chain'));
});

test('input that is not typed data is invalid, not clean', () => {
  for (const bad of ['', 'hello', '{}', '{"primaryType":"Permit"}', null, 42]) {
    const r = S.decodeTypedData(bad, { now: NOW });
    assert.strictEqual(r.invalid, true, String(bad));
    assert.deepStrictEqual(r.actions, []);
  }
});

test('an EIP-7702 delegation is always flagged, twice over on every chain, and a clear is not', () => {
  const d = S.decodeAuthorization({ chainId: 8453, address: SPENDER, nonce: 0 }, { chainId: 8453 });
  assert.deepStrictEqual(tids(d), ['sgr.a_7702']);
  assert.deepStrictEqual(ids(d), ['delegation_7702']);
  assert.strictEqual(d.actions[0].params.delegate, SPENDER);
  const any = S.decodeAuthorization(JSON.stringify({ chainId: '0x0', address: SPENDER, nonce: 0 }));
  assert.deepStrictEqual(ids(any), ['any_chain', 'delegation_7702']);
  assert.strictEqual(any.actions[0].params.chain, 'ANY');
  const other = S.decodeAuthorization({ chainId: 1, address: SPENDER, nonce: 0 }, { chainId: 8453 });
  assert.ok(ids(other).includes('chain_mismatch'));
  const clear = S.decodeAuthorization({ chainId: 8453, address: '0x' + '00'.repeat(20), nonce: 1 });
  assert.deepStrictEqual(tids(clear), ['sgr.a_7702_clear']);
  assert.deepStrictEqual(ids(clear), []);
  const bad = S.decodeAuthorization({ chainId: 8453, address: 'nope' });
  assert.strictEqual(bad.invalid, true);
  assert.deepStrictEqual(bad.actions, []);
});
