/**
 * Recursion Breaker contracts on an in-process EVM.
 * Chain id 1 reverts. Chain id 31337 runs. No network.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { Interface, Wallet, getBytes, keccak256, AbiCoder, id as keccakId } from 'ethers';
import solc from 'solc';

const GAS = 30_000_000n;
const E18 = 10n ** 18n;
const alice = '0x' + 'a1'.repeat(20);
const bob = '0x' + 'b0'.repeat(20);
const carol = '0x' + 'c0'.repeat(20);
const coder = AbiCoder.defaultAbiCoder();
const TYPES = [
  'uint256', 'uint256', 'uint256', 'uint256', 'uint256',
  'bool', 'uint256', 'uint256', 'uint256', 'uint256',
];

const compiled = compile();
const signersAbi = compiled.RXBSigners;
const oracleAbi = compiled.RXBOracle;
const sentinelAbi = compiled.RXBSentinel;
const challengeAbi = compiled.RXBChallenge;
const vaultAbi = compiled.VaultStub;
const usdcAbi = compiled.MockUSDC;

function compile() {
  const source = readFileSync(new URL('../src/RXB.sol', import.meta.url), 'utf8');
  const input = {
    language: 'Solidity',
    sources: { 'src/RXB.sol': { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
      outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
    },
  };
  const out = JSON.parse(solc.compile(JSON.stringify(input)));
  const errors = (out.errors || []).filter((e) => e.severity === 'error');
  if (errors.length) throw new Error(errors.map((e) => e.formattedMessage).join('\n'));
  const contracts = out.contracts['src/RXB.sol'];
  const pick = (name) => ({
    abi: contracts[name].abi,
    bytecode: '0x' + contracts[name].evm.bytecode.object,
    iface: new Interface(contracts[name].abi),
  });
  return {
    RXBSigners: pick('RXBSigners'),
    RXBOracle: pick('RXBOracle'),
    RXBSentinel: pick('RXBSentinel'),
    RXBChallenge: pick('RXBChallenge'),
    VaultStub: pick('VaultStub'),
    MockUSDC: pick('MockUSDC'),
  };
}

function header(ts) {
  return {
    timestamp: ts, number: ts, coinbase: Address.zero(), difficulty: 0n,
    prevRandao: new Uint8Array(32), gasLimit: GAS, baseFeePerGas: 0n,
    getBlobGasPrice: () => 0n,
  };
}

function revertReason(execResult) {
  const ret = bytesToHex(execResult.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', ret)[0];
  }
  return ret;
}

async function newVm(chainId) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  for (const who of [alice, bob, carol]) {
    await vm.stateManager.putAccount(new Address(hexToBytes(who)), new Account(0n, 1_000n * E18));
  }
  return vm;
}

async function deploy(vm, data, from, ts) {
  return vm.evm.runCall({
    caller: new Address(hexToBytes(from)),
    data: getBytes(data),
    gasLimit: GAS,
    block: { header: header(ts) },
  });
}

function creation(contract, args) {
  const encoded = contract.iface.encodeDeploy(args);
  return contract.bytecode + encoded.slice(2);
}

test('mainnet chain ids revert and a fork deploys', async () => {
  const ts = 1_800_000_000n;
  for (const chain of [1, 8453, 4663, 42161]) {
    const vm = await newVm(chain);
    const data = creation(signersAbi, [alice, bob, carol]);
    const failed = await deploy(vm, data, alice, ts);
    assert.ok(failed.execResult.exceptionError, `chain ${chain} deployed`);
    const ret = bytesToHex(failed.execResult.returnValue || new Uint8Array());
    assert.ok(ret.startsWith(keccakId('MainnetRefusal()').slice(0, 10)), ret);
  }
  const ok = await newVm(31337);
  const created = await deploy(ok, creation(signersAbi, [alice, bob, carol]), alice, ts);
  assert.ok(!created.execResult.exceptionError, revertReason(created.execResult));
  assert.ok(created.createdAddress);
});

function structHash(p) {
  return keccak256(coder.encode(TYPES, [
    p.nav, p.vChain, p.vAtt, p.supply, p.pMkt, p.includeMkt,
    p.depthUsd, p.attestedAt, p.signedAt, p.nonce,
  ]));
}

async function sign(wallet, p) {
  return wallet.signMessage(getBytes(structHash(p)));
}

test('the oracle mins a liquid twap, drops a thin one, and reverts when unreadable', async () => {
  const vm = await newVm(31337);
  let clock = 1_800_000_000n;
  const k1 = new Wallet('0x' + '11'.repeat(32));
  const k2 = new Wallet('0x' + '22'.repeat(32));
  const k3 = new Wallet('0x' + '33'.repeat(32));
  const signers = await deploy(vm, creation(signersAbi, [k1.address, k2.address, k3.address]), alice, clock);
  assert.ok(!signers.execResult.exceptionError, revertReason(signers.execResult));
  const signersAt = signers.createdAddress.toString();
  const oracle = await deploy(vm, creation(oracleAbi, [signersAt]), alice, clock);
  assert.ok(!oracle.execResult.exceptionError, revertReason(oracle.execResult));
  const oracleAt = oracle.createdAddress.toString();

  async function send(from, to, data, ts) {
    const res = await vm.evm.runCall({
      caller: new Address(hexToBytes(from)),
      to: new Address(hexToBytes(to)),
      data: getBytes(data),
      gasLimit: GAS,
      block: { header: header(ts) },
    });
    return res.execResult;
  }
  async function read(to, iface, fn, args, ts) {
    const res = await send(alice, to, iface.encodeFunctionData(fn, args), ts);
    assert.ok(!res.exceptionError, `${fn} reverted: ${revertReason(res)}`);
    return iface.decodeFunctionResult(fn, bytesToHex(res.returnValue));
  }
  async function submit(p, ts, a = k1, b = k2) {
    const sigA = await sign(a, p);
    const sigB = await sign(b, p);
    return send(alice, signersAt, signersAbi.iface.encodeFunctionData('submit', [
      p.nav, p.vChain, p.vAtt, p.supply, p.pMkt, p.includeMkt, p.depthUsd,
      p.attestedAt, p.signedAt, p.nonce, sigA, sigB,
    ]), ts);
  }

  const base = {
    nav: 2n * E18, vChain: 2n * E18, vAtt: 0n, supply: E18, pMkt: E18,
    includeMkt: true, depthUsd: 999_999n, attestedAt: clock, signedAt: clock, nonce: 1n,
  };
  const thin = await submit(base, clock);
  assert.ok(!thin.exceptionError, revertReason(thin));
  const thinPrice = (await read(oracleAt, oracleAbi.iface, 'price', [], clock))[0];
  assert.equal(thinPrice, 2n * E18 * E18, 'a thin twap must not pull the price down');

  const deep = { ...base, nonce: 2n, depthUsd: 1_000_000n, pMkt: E18 };
  assert.ok(!(await submit(deep, clock)).exceptionError);
  const deepPrice = (await read(oracleAt, oracleAbi.iface, 'price', [], clock))[0];
  assert.equal(deepPrice, E18 * E18);

  const agedAt = clock;
  const aged = {
    ...base, nonce: 3n, includeMkt: false, depthUsd: 0n, pMkt: 1n,
    nav: 10n * E18, vChain: 0n, vAtt: 2n * E18,
    attestedAt: agedAt - 259200n, signedAt: agedAt,
  };
  assert.ok(!(await submit(aged, agedAt)).exceptionError);
  const half = (await read(oracleAt, oracleAbi.iface, 'weightWad', [259200n], agedAt))[0];
  assert.equal(half, E18 / 2n);
  const agedPrice = (await read(oracleAt, oracleAbi.iface, 'price', [], agedAt))[0];
  assert.equal(agedPrice, E18 * E18);

  const stale = await send(alice, oracleAt, oracleAbi.iface.encodeFunctionData('price', []), clock + 3601n);
  assert.ok(stale.exceptionError, 'a packet older than one hour reverts');

  const again = await submit(base, clock);
  assert.ok(again.exceptionError, 'a nonce cannot be replayed');
  const same = await submit({ ...base, nonce: 4n }, clock, k1, k1);
  assert.ok(same.exceptionError, 'one signer does not count twice');

  const zeroSupply = await submit({ ...base, nonce: 5n, supply: 0n }, clock);
  assert.ok(!zeroSupply.exceptionError, revertReason(zeroSupply));
  const unreadable = await send(alice, oracleAt, oracleAbi.iface.encodeFunctionData('price', []), clock);
  assert.ok(unreadable.exceptionError, 'supply zero reverts instead of returning zero');
});

test('the sentinel only lowers, and a zero timelock is not a safety property', async () => {
  const vm = await newVm(31337);
  const clock = 1_800_000_000n;
  async function send(from, to, data, ts = clock) {
    const res = await vm.evm.runCall({
      caller: new Address(hexToBytes(from)),
      to: to ? new Address(hexToBytes(to)) : undefined,
      data: getBytes(data),
      gasLimit: GAS,
      block: { header: header(ts) },
    });
    return res;
  }
  const vaultR = await deploy(vm, creation(vaultAbi, [alice, bob]), alice, clock);
  assert.ok(!vaultR.execResult.exceptionError, revertReason(vaultR.execResult));
  const vault = vaultR.createdAddress.toString();
  const sentR = await deploy(vm, creation(sentinelAbi, [alice]), alice, clock);
  assert.ok(!sentR.execResult.exceptionError, revertReason(sentR.execResult));
  const sentinel = sentR.createdAddress.toString();
  const names = sentinelAbi.iface.fragments.filter((f) => f.type === 'function').map((f) => f.name);
  assert.ok(names.includes('decreaseAbsolute'));
  assert.ok(names.includes('decreaseRelative'));
  assert.ok(!names.some((n) => n.toLowerCase().includes('increase')));

  const idData = '0x1234';
  const refused = await send(bob, vault, vaultAbi.iface.encodeFunctionData('setIsSentinel', [sentinel, true]));
  assert.ok(refused.execResult.exceptionError, 'the curator cannot grant the sentinel');
  const granted = await send(alice, vault, vaultAbi.iface.encodeFunctionData('setIsSentinel', [sentinel, true]));
  assert.ok(!granted.execResult.exceptionError, revertReason(granted.execResult));

  const raised = await send(alice, vault, vaultAbi.iface.encodeFunctionData('increaseAbsoluteCap', [idData, 100n]));
  assert.ok(!raised.execResult.exceptionError, revertReason(raised.execResult));
  const id = keccak256(idData);
  async function abs() {
    const res = await send(alice, vault, vaultAbi.iface.encodeFunctionData('absCap', [id]));
    return vaultAbi.iface.decodeFunctionResult('absCap', bytesToHex(res.execResult.returnValue))[0];
  }
  assert.equal(await abs(), 100n, 'a zero delay applies the increase in the same call');

  const delay = await send(alice, vault, vaultAbi.iface.encodeFunctionData('setIncreaseDelay', [86_400n]));
  assert.ok(!delay.execResult.exceptionError);
  const armed = await send(alice, vault, vaultAbi.iface.encodeFunctionData('increaseAbsoluteCap', [idData, 500n]));
  assert.ok(!armed.execResult.exceptionError);
  assert.equal(await abs(), 100n, 'a nonzero delay does not apply on the arming call');
  const early = await send(alice, vault, vaultAbi.iface.encodeFunctionData('increaseAbsoluteCap', [idData, 500n]), clock + 10n);
  assert.ok(early.execResult.exceptionError, 'the increase is refused inside the delay');

  const down = await send(alice, sentinel, sentinelAbi.iface.encodeFunctionData('decreaseAbsolute', [vault, idData, 40n]));
  assert.ok(!down.execResult.exceptionError, revertReason(down.execResult));
  assert.equal(await abs(), 40n);
  const up = await send(alice, sentinel, sentinelAbi.iface.encodeFunctionData('decreaseAbsolute', [vault, idData, 50n]));
  assert.ok(up.execResult.exceptionError, 'the sentinel refuses a higher cap');
  const stranger = await send(bob, sentinel, sentinelAbi.iface.encodeFunctionData('decreaseAbsolute', [vault, idData, 10n]));
  assert.ok(stranger.execResult.exceptionError);

  await send(alice, vault, vaultAbi.iface.encodeFunctionData('loadRelative', [idData, E18]));
  const rel = await send(alice, sentinel, sentinelAbi.iface.encodeFunctionData('decreaseRelative', [vault, idData, E18 / 2n]));
  assert.ok(!rel.execResult.exceptionError, revertReason(rel.execResult));
});

test('challenge bonds double for 30 days and a late reclaim does not', async () => {
  const vm = await newVm(31337);
  let clock = 1_800_000_000n;
  async function send(from, to, data, ts) {
    const res = await vm.evm.runCall({
      caller: new Address(hexToBytes(from)),
      to: to ? new Address(hexToBytes(to)) : undefined,
      data: getBytes(data),
      gasLimit: GAS,
      block: { header: header(ts) },
    });
    return res.execResult;
  }
  const usdcR = await deploy(vm, creation(usdcAbi, []), alice, clock);
  const usdc = usdcR.createdAddress.toString();
  const chR = await deploy(vm, creation(challengeAbi, [usdc, bob, carol]), alice, clock);
  assert.ok(!chR.execResult.exceptionError, revertReason(chR.execResult));
  const challenge = chR.createdAddress.toString();
  const BASE = 5000n * 10n ** 6n;
  async function bond(ts) {
    const res = await send(alice, challenge, challengeAbi.iface.encodeFunctionData('requiredBond', [alice]), ts);
    assert.ok(!res.exceptionError, revertReason(res));
    return challengeAbi.iface.decodeFunctionResult('requiredBond', bytesToHex(res.returnValue))[0];
  }
  async function balance(who, ts) {
    const res = await send(alice, usdc, usdcAbi.iface.encodeFunctionData('balanceOf', [who]), ts);
    return usdcAbi.iface.decodeFunctionResult('balanceOf', bytesToHex(res.returnValue))[0];
  }
  assert.equal(await bond(clock), BASE);
  await send(alice, usdc, usdcAbi.iface.encodeFunctionData('mint', [alice, BASE * 8n]), clock);
  await send(alice, usdc, usdcAbi.iface.encodeFunctionData('approve', [challenge, BASE * 8n]), clock);
  const id1 = keccak256(coder.encode(['string'], ['one']));
  const opened = await send(alice, challenge, challengeAbi.iface.encodeFunctionData('open', [id1]), clock);
  assert.ok(!opened.exceptionError, revertReason(opened));
  const stranger = await send(carol, challenge, challengeAbi.iface.encodeFunctionData('resolve', [id1, false]), clock);
  assert.ok(stranger.exceptionError);
  const failed = await send(bob, challenge, challengeAbi.iface.encodeFunctionData('resolve', [id1, false]), clock + 10n);
  assert.ok(!failed.exceptionError, revertReason(failed));
  assert.equal(await balance(carol, clock + 10n), BASE);
  assert.equal(await bond(clock + 10n), BASE * 2n);

  const id2 = keccak256(coder.encode(['string'], ['two']));
  const second = await send(alice, challenge, challengeAbi.iface.encodeFunctionData('open', [id2]), clock + 20n);
  assert.ok(!second.exceptionError, revertReason(second));
  const early = await send(alice, challenge, challengeAbi.iface.encodeFunctionData('reclaim', [id2]), clock + 30n);
  assert.ok(early.exceptionError, 'reclaim inside the window reverts');
  const later = clock + 20n + 72n * 3600n + 1n;
  const reclaimed = await send(alice, challenge, challengeAbi.iface.encodeFunctionData('reclaim', [id2]), later);
  assert.ok(!reclaimed.exceptionError, revertReason(reclaimed));
  assert.equal(await bond(later), BASE * 2n, 'a reclaim is not a failed challenge');
  const aged = clock + 10n + 30n * 86400n + 1n;
  assert.equal(await bond(aged), BASE, 'a failure older than 30 days drops out');
});
