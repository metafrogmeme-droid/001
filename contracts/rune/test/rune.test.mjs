'use strict';
/**
 * RuneOfEntry — compile the real contract, run the real bytecode on an
 * in-process EVM, sign real EIP-712 vouchers. No network, no mocks of the
 * thing under test. The contract's promises under test:
 *   free + gas-only (not payable), one per wallet, voucher-gated,
 *   soulbound on every path (ERC-5192), no admin surface at all,
 *   art fully on-chain and deterministic.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import solc from 'solc';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { Interface, Wallet, getBytes, concat } from 'ethers';

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(here, '..', 'RuneOfEntry.sol'), 'utf8');
const CHAIN_ID = 8453; // Base — the deployment target, so the domain matches

// ── compile once ─────────────────────────────────────────────────────────────
const input = {
  language: 'Solidity',
  sources: { 'RuneOfEntry.sol': { content: SOURCE } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
  },
};
const out = JSON.parse(solc.compile(JSON.stringify(input)));
const fatal = (out.errors || []).filter((e) => e.severity === 'error');
assert.equal(fatal.length, 0, 'compile errors: ' + JSON.stringify(fatal, null, 2));
const artifact = out.contracts['RuneOfEntry.sol'].RuneOfEntry;
const iface = new Interface(artifact.abi);
const BYTECODE = '0x' + artifact.evm.bytecode.object;

// ── EVM harness ──────────────────────────────────────────────────────────────
const voucherKey = new Wallet('0x' + '11'.repeat(32));
const minterA = '0x' + 'aa'.repeat(20);
const minterB = '0x' + 'bb'.repeat(20);
const GAS = 30_000_000n;

let vm, contractAddr;

async function call(from, data, { value = 0n } = {}) {
  const r = await vm.evm.runCall({
    caller: new Address(hexToBytes(from)),
    to: new Address(hexToBytes(contractAddr)),
    data: getBytes(data),
    gasLimit: GAS,
    value,
  });
  return r.execResult;
}

function revertReason(execResult) {
  const ret = bytesToHex(execResult.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)'])
      .decodeFunctionData('Error', ret)[0];
  }
  return execResult.exceptionError ? String(execResult.exceptionError.error) : null;
}

async function read(fn, ...args) {
  const r = await call(minterA, iface.encodeFunctionData(fn, args));
  assert.ok(!r.exceptionError, `${fn} reverted: ${revertReason(r)}`);
  return iface.decodeFunctionResult(fn, bytesToHex(r.returnValue));
}

function voucherFor(to) {
  return voucherKey.signTypedData(
    { name: 'RUNECLAW Rune of Entry', chainId: CHAIN_ID, verifyingContract: contractAddr },
    { MintVoucher: [{ name: 'to', type: 'address' }] },
    { to });
}

test.before(async () => {
  const common = Common.custom({ chainId: CHAIN_ID }, { hardfork: Hardfork.Shanghai });
  vm = await VM.create({ common });
  const deployData = concat([BYTECODE,
    iface.encodeDeploy([voucherKey.address])]);
  const r = await vm.evm.runCall({
    caller: new Address(hexToBytes(minterA)),
    data: getBytes(deployData),
    gasLimit: GAS,
  });
  assert.ok(!r.execResult.exceptionError,
    'deploy reverted: ' + revertReason(r.execResult));
  contractAddr = r.createdAddress.toString();
});

// ── the promises ─────────────────────────────────────────────────────────────

test('mint: a valid voucher mints exactly one soulbound token', async () => {
  const sig = await voucherFor(minterA);
  const r = await call(minterA, iface.encodeFunctionData('mint', [sig]));
  assert.ok(!r.exceptionError, 'mint reverted: ' + revertReason(r));
  assert.ok(r.executionGasUsed < 150_000n,
    `mint burned ${r.executionGasUsed} gas — must stay cheap enough to call "free"`);
  // Transfer(0 → minter, 1) and Locked(1), both emitted at mint.
  const topics = r.logs.map((l) => bytesToHex(l[1][0]));
  assert.ok(topics.includes(iface.getEvent('Transfer').topicHash));
  assert.ok(topics.includes(iface.getEvent('Locked').topicHash));

  assert.equal((await read('ownerOf', 1))[0].toLowerCase(), minterA);
  assert.equal((await read('balanceOf', minterA))[0], 1n);
  assert.equal((await read('totalMinted'))[0], 1n);
});

test('one per wallet: the same wallet cannot mint twice, even with a fresh voucher', async () => {
  const sig = await voucherFor(minterA);
  const r = await call(minterA, iface.encodeFunctionData('mint', [sig]));
  assert.equal(revertReason(r), 'already minted');
});

test('voucher binding: someone else\'s voucher does not mint for you', async () => {
  const sigForA = await voucherFor(minterA);
  const r = await call(minterB, iface.encodeFunctionData('mint', [sigForA]));
  assert.equal(revertReason(r), 'bad voucher');
});

test('voucher authority: a signature from any other key is refused', async () => {
  const wrongKey = new Wallet('0x' + '22'.repeat(32));
  const sig = await wrongKey.signTypedData(
    { name: 'RUNECLAW Rune of Entry', chainId: CHAIN_ID, verifyingContract: contractAddr },
    { MintVoucher: [{ name: 'to', type: 'address' }] },
    { to: minterB });
  const r = await call(minterB, iface.encodeFunctionData('mint', [sig]));
  assert.equal(revertReason(r), 'bad voucher');
});

test('free means free: the mint path cannot receive ether', async () => {
  const sig = await voucherFor(minterB);
  const r = await call(minterB, iface.encodeFunctionData('mint', [sig]), { value: 1n });
  assert.ok(r.exceptionError, 'a non-payable mint must reject value');
  // And the ABI as a whole holds the promise: nothing is payable.
  for (const f of artifact.abi.filter((x) => x.type === 'function')) {
    assert.notEqual(f.stateMutability, 'payable', `${f.name} must not be payable`);
  }
});

test('soulbound: every movement and approval path reverts; ERC-5192 says locked', async () => {
  for (const data of [
    iface.encodeFunctionData('transferFrom', [minterA, minterB, 1]),
    iface.encodeFunctionData('safeTransferFrom(address,address,uint256)', [minterA, minterB, 1]),
    iface.encodeFunctionData('safeTransferFrom(address,address,uint256,bytes)', [minterA, minterB, 1, '0x']),
    iface.encodeFunctionData('approve', [minterB, 1]),
    iface.encodeFunctionData('setApprovalForAll', [minterB, true]),
  ]) {
    const r = await call(minterA, data);
    assert.equal(revertReason(r), 'soulbound');
  }
  assert.equal((await read('locked', 1))[0], true);
  const unknown = await call(minterA, iface.encodeFunctionData('locked', [999]));
  assert.equal(revertReason(unknown), 'no such token');
  for (const iid of ['0x01ffc9a7', '0x80ac58cd', '0x5b5e139f', '0xb45a3c0e']) {
    assert.equal((await read('supportsInterface', iid))[0], true, iid);
  }
  assert.equal((await read('supportsInterface', '0xdeadbeef'))[0], false);
});

test('no admin surface: no owner, no pause, no withdraw, nothing to rug', () => {
  const names = artifact.abi.filter((x) => x.type === 'function').map((f) => f.name);
  for (const n of names) {
    // ownerOf is an ERC-721 read; setApprovalForAll is the mandatory ERC-721
    // stub whose only behavior — proven above — is revert('soulbound').
    if (n === 'ownerOf' || n === 'setApprovalForAll') continue;
    assert.doesNotMatch(n, /owner|admin|pause|withdraw|set[A-Z]|upgrade/,
      `function ${n} looks like a control surface — this contract must have none`);
  }
});

test('the art is fully on-chain, honest, and unique per token', async () => {
  // Mint a second token from another wallet for comparison.
  const sig = await voucherFor(minterB);
  const r = await call(minterB, iface.encodeFunctionData('mint', [sig]));
  assert.ok(!r.exceptionError, 'mint B reverted: ' + revertReason(r));

  const uris = [];
  for (const id of [1, 2]) {
    const [uri] = await read('tokenURI', id);
    assert.match(uri, /^data:application\/json;base64,/);
    const json = JSON.parse(Buffer.from(uri.split(',')[1], 'base64').toString('utf8'));
    assert.equal(json.name, `Rune of Entry #${id}`);
    assert.match(json.description, /not an investment/);
    assert.match(json.description, /cannot be transferred/);
    assert.match(json.image, /^data:image\/svg\+xml;base64,/);
    const svg = Buffer.from(json.image.split(',')[1], 'base64').toString('utf8');
    assert.match(svg, /^<svg xmlns='http:\/\/www\.w3\.org\/2000\/svg'/);
    assert.ok((svg.match(/<line /g) || []).length >= 3,
      'staff + at least two guaranteed branches — no token is an empty staff');
    assert.match(svg, /<\/g><\/svg>$/);
    uris.push(svg);
  }
  assert.notEqual(uris[0], uris[1], 'two minters, two sigils — seeded by id + owner');
  const unknown = await call(minterA, iface.encodeFunctionData('tokenURI', [999]));
  assert.equal(revertReason(unknown), 'no such token');
});

test('deploy honesty: a zero voucher signer is refused at the door', async () => {
  const deployData = concat([BYTECODE,
    iface.encodeDeploy(['0x' + '00'.repeat(20)])]);
  const r = await vm.evm.runCall({
    caller: new Address(hexToBytes(minterA)),
    data: getBytes(deployData),
    gasLimit: GAS,
  });
  assert.ok(r.execResult.exceptionError, 'constructor must refuse signer = 0');
});
