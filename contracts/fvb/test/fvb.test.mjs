/**
 * Fair-value bridge contracts. Compiled with solc and run on an in-process
 * EVM. No network, no deploy script, no mainnet address.
 *
 * The digest vector is the one `bot/fvb/sign.py` produces for the same
 * packet, chain 11155111 and the placeholder contract
 * 0x000000000000000000000000000000000000ffb0.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import solc from 'solc';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes } from '@ethereumjs/util';
import {
  AbiCoder, Interface, Wallet, TypedDataEncoder, concat, getBytes,
} from 'ethers';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const SEPOLIA = 11155111;
const ROBINHOOD_TESTNET = 46630;
const MAINNETS = [1, 8453, 42161, 999, 4663];
const GAS = 30_000_000n;
const E8 = 100_000_000n;
const ALICE = '0x' + 'a1'.repeat(20);
const FROZEN_DIGEST = '0x01989c148e3153a42470b41b4d850ce210c7e1c30bb49c8367e49ac5142d57e2';
const PLACEHOLDER = '0x000000000000000000000000000000000000ffb0';

const TYPES = {
  FVBPacket: [
    { name: 'symbol', type: 'bytes32' },
    { name: 'fv', type: 'uint256' },
    { name: 'bandLo', type: 'uint256' },
    { name: 'bandHi', type: 'uint256' },
    { name: 'session', type: 'uint8' },
    { name: 'confirmed', type: 'bool' },
    { name: 'ts', type: 'uint64' },
    { name: 'version', type: 'uint32' },
    { name: 'nextEpoch', type: 'bytes32' },
  ],
};

function word(text) {
  const hex = Buffer.from(text, 'utf8').toString('hex');
  return '0x' + hex.padEnd(64, '0');
}

function packet(over = {}) {
  return {
    symbol: word('SPY'),
    fv: 100n * E8,
    bandLo: 99n * E8,
    bandHi: 101n * E8,
    session: 0,
    confirmed: true,
    ts: 1_700_000_000n,
    version: 1,
    nextEpoch: '0x' + '00'.repeat(32),
    ...over,
  };
}

function tuple(p) {
  return [p.symbol, p.fv, p.bandLo, p.bandHi, p.session, p.confirmed, p.ts, p.version, p.nextEpoch];
}

function compile() {
  const names = ['EulerOps.sol', 'FVBOracle.sol', 'MorphoFvbAdapter.sol'];
  const sources = {};
  for (const name of names) sources[name] = { content: readFileSync(join(SRC, name), 'utf8') };
  const input = {
    language: 'Solidity',
    sources,
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: 'paris',
      outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
    },
  };
  const out = JSON.parse(solc.compile(JSON.stringify(input)));
  const fatal = (out.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) throw new Error(JSON.stringify(fatal, null, 2));
  const pick = (file, name) => {
    const c = out.contracts[file][name];
    return { abi: c.abi, bytecode: '0x' + c.evm.bytecode.object, iface: new Interface(c.abi) };
  };
  return {
    oracle: pick('FVBOracle.sol', 'FVBOracle'),
    adapter: pick('MorphoFvbAdapter.sol', 'MorphoFvbAdapter'),
    euler: pick('EulerOps.sol', 'EulerOpsProbe'),
  };
}

const BUILT = compile();

function revertReason(execResult) {
  const ret = Buffer.from(execResult.returnValue || new Uint8Array()).toString('hex');
  if (ret.startsWith('08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', '0x' + ret)[0];
  }
  return execResult.exceptionError ? String(execResult.exceptionError.error) : null;
}

async function newVm(chainId) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  await vm.stateManager.putAccount(new Address(hexToBytes(ALICE)), new Account(0n, 10n ** 18n));
  return vm;
}

async function run(vm, to, data) {
  const res = await vm.evm.runCall({
    caller: new Address(hexToBytes(ALICE)),
    to: to ? new Address(hexToBytes(to)) : undefined,
    data: getBytes(data),
    gasLimit: GAS,
  });
  return res;
}

async function deploy(vm, bytecode, argTypes, args) {
  const data = argTypes
    ? concat([bytecode, AbiCoder.defaultAbiCoder().encode(argTypes, args)])
    : bytecode;
  const res = await run(vm, undefined, data);
  return { res, address: res.createdAddress ? res.createdAddress.toString() : null };
}

const KEYS = ['11', '22', '33', '44', '55'].map((b) => new Wallet('0x' + b.repeat(32)));

function domain(chainId, verifying) {
  return { name: 'Fair-Value Bridge', version: '1', chainId, verifyingContract: verifying };
}

async function signs(wallets, chainId, verifying, value) {
  const dom = domain(chainId, verifying);
  const out = [];
  for (const w of wallets) out.push(await w.signTypedData(dom, TYPES, value));
  return out;
}

test('the frozen digest matches the Python signer and ethers', async () => {
  const value = packet();
  const hashed = TypedDataEncoder.hash(domain(SEPOLIA, PLACEHOLDER), TYPES, value);
  assert.equal(hashed, FROZEN_DIGEST);
  const vm = await newVm(SEPOLIA);
  const deployed = await deploy(vm, BUILT.oracle.bytecode, ['address[]', 'uint256'], [[KEYS[0].address], 1]);
  assert.equal(revertReason(deployed.res.execResult), null, 'oracle deploy');
  const data = BUILT.oracle.iface.encodeFunctionData('digestFor', [SEPOLIA, PLACEHOLDER, tuple(value)]);
  const res = await run(vm, deployed.address, data);
  assert.equal(revertReason(res.execResult), null);
  const got = BUILT.oracle.iface.decodeFunctionResult('digestFor', res.execResult.returnValue)[0];
  assert.equal(got, FROZEN_DIGEST);
});

test('the constructor refuses the mainnets and accepts the two testnets', async () => {
  for (const id of MAINNETS) {
    const vm = await newVm(id);
    const deployed = await deploy(vm, BUILT.oracle.bytecode, ['address[]', 'uint256'], [[KEYS[0].address], 1]);
    assert.equal(revertReason(deployed.res.execResult), 'testnet only', `chain ${id}`);
    assert.ok(deployed.res.execResult.exceptionError, `chain ${id} stored a contract`);
    const adapter = await deploy(vm, BUILT.adapter.bytecode, ['address', 'bytes32'], [KEYS[0].address, word('SPY')]);
    assert.equal(revertReason(adapter.res.execResult), 'testnet only');
  }
  for (const id of [SEPOLIA, ROBINHOOD_TESTNET]) {
    const vm = await newVm(id);
    const deployed = await deploy(vm, BUILT.oracle.bytecode, ['address[]', 'uint256'], [KEYS.map((k) => k.address), 3]);
    assert.equal(revertReason(deployed.res.execResult), null, `chain ${id}`);
    assert.ok(deployed.address);
  }
});

test('three of five stores a confirmed price and two of five does not', async () => {
  const vm = await newVm(SEPOLIA);
  const deployed = await deploy(
    vm, BUILT.oracle.bytecode, ['address[]', 'uint256'], [KEYS.map((k) => k.address), 3],
  );
  const oracle = deployed.address;
  const value = packet({ fv: 180n * E8, bandLo: 170n * E8, bandHi: 190n * E8 });
  const sym = value.symbol;

  async function submit(sigs, body = value) {
    const data = BUILT.oracle.iface.encodeFunctionData('submit', [tuple(body), sigs]);
    return run(vm, oracle, data);
  }

  const two = await signs(KEYS.slice(0, 2), SEPOLIA, oracle, value);
  assert.equal(revertReason((await submit(two)).execResult), 'threshold');

  const one = await signs([KEYS[0]], SEPOLIA, oracle, value);
  assert.equal(revertReason((await submit([one[0], one[0], one[0]])).execResult), 'threshold');

  const tampered = packet({ fv: 1n * E8, bandLo: 170n * E8, bandHi: 190n * E8 });
  const good = await signs(KEYS.slice(0, 3), SEPOLIA, oracle, value);
  assert.equal(revertReason((await submit(good, tampered)).execResult), 'threshold');

  const quiet = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('fvOf', [sym]));
  assert.equal(revertReason(quiet.execResult), 'unreadable');
  const round = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latestRoundData', [sym]));
  assert.equal(revertReason(round.execResult), 'no reading');

  const unconfirmed = packet({ fv: 180n * E8, bandLo: 170n * E8, bandHi: 190n * E8, confirmed: false });
  const signedNo = await signs(KEYS.slice(0, 3), SEPOLIA, oracle, unconfirmed);
  assert.equal(revertReason((await submit(signedNo, unconfirmed)).execResult), 'unconfirmed');

  const zero = packet({ fv: 0n });
  assert.equal(revertReason((await submit([], zero)).execResult), 'unreadable');

  const stored = await submit(good, value);
  assert.equal(revertReason(stored.execResult), null, '3-of-5 submit');

  const fv = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('fvOf', [sym]));
  assert.equal(BUILT.oracle.iface.decodeFunctionResult('fvOf', fv.execResult.returnValue)[0], 180n * E8);
  const live = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latestRoundData', [sym]));
  const decoded = BUILT.oracle.iface.decodeFunctionResult('latestRoundData', live.execResult.returnValue);
  assert.equal(decoded[1], 180n * E8);
  assert.notEqual(decoded[1], 0n);

  const other = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latestRoundData', [word('QQQ')]));
  assert.equal(revertReason(other.execResult), 'no reading');

  const adapted = await deploy(vm, BUILT.adapter.bytecode, ['address', 'bytes32'], [oracle, sym]);
  assert.equal(revertReason(adapted.res.execResult), null);
  const px = await run(vm, adapted.address, BUILT.adapter.iface.encodeFunctionData('price', []));
  assert.equal(BUILT.adapter.iface.decodeFunctionResult('price', px.execResult.returnValue)[0], 180n * E8);

  const emptyAdapter = await deploy(vm, BUILT.adapter.bytecode, ['address', 'bytes32'], [oracle, word('QQQ')]);
  const missing = await run(vm, emptyAdapter.address, BUILT.adapter.iface.encodeFunctionData('price', []));
  assert.equal(revertReason(missing.execResult), 'unreadable');
});

test('OP_LIQUIDATE is bit 11 and bit 12 does not enable it', async () => {
  const vm = await newVm(SEPOLIA);
  const deployed = await deploy(vm, BUILT.euler.bytecode, null, null);
  assert.equal(revertReason(deployed.res.execResult), null);
  async function read(name, args = []) {
    const data = BUILT.euler.iface.encodeFunctionData(name, args);
    const res = await run(vm, deployed.address, data);
    assert.equal(revertReason(res.execResult), null, name);
    return BUILT.euler.iface.decodeFunctionResult(name, res.execResult.returnValue)[0];
  }
  assert.equal(await read('liquidateBit'), 11n);
  assert.equal(await read('liquidateMask'), 1n << 11n);
  assert.equal(await read('flashloanMask'), 1n << 12n);
  assert.notEqual(await read('liquidateMask'), 1n << 12n);
  assert.equal(await read('liquidateEnabled', [1n << 11n]), true);
  assert.equal(await read('liquidateEnabled', [1n << 12n]), false);
  assert.equal(await read('liquidateEnabled', [0n]), false);
  assert.equal(await read('liquidateEnabled', [(1n << 11n) | (1n << 12n)]), true);
});
