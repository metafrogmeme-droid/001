/**
 * XNAV — compile the oracle, the swing vault and the mandate, and run
 * them on an in-process EVM. No network. Chain 998. Mainnet ids revert.
 * The precompile is etched at 0x802 for the test only.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import solc from 'solc';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes } from '@ethereumjs/util';
import { AbiCoder, Interface, Wallet, TypedDataEncoder, concat, getBytes, keccak256, toUtf8Bytes } from 'ethers';

const require = createRequire(import.meta.url);
const swing = require('../../../app/lib/xnav/swing.js');

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const REPO = join(ROOT, '..', '..');
const PH = JSON.parse(readFileSync(join(REPO, 'bot', 'xnav', 'placeholders.json'), 'utf8'));
const GAS = 30_000_000n;
const CHAIN = 998;
const MAINNETS = [1, 8453, 42161, 999, 4663];
const TS = 1_700_000_000n;
const PRECOMPILE = PH.vault_equity_precompile;
const ALICE = '0x' + '11'.repeat(20);
const VAULT = '0x' + 'ab'.repeat(20);
const ZERO = '0x' + '00'.repeat(20);
const KEYS = ['a1', 'b2', 'c3'].map((b) => new Wallet('0x' + b.repeat(32)));

const TYPES = {
  XNAVUpdate: [
    { name: 'vault', type: 'address' },
    { name: 'xnavBps', type: 'uint32' },
    { name: 'stressBps', type: 'uint32' },
    { name: 'ts', type: 'uint64' },
  ],
};

function compile() {
  const files = {
    'src/XNAVTestnet.sol': join(ROOT, 'src', 'XNAVTestnet.sol'),
    'src/XNAVOracle.sol': join(ROOT, 'src', 'XNAVOracle.sol'),
    'src/SwingVault4626.sol': join(ROOT, 'src', 'SwingVault4626.sol'),
    'src/XNAVMandate.sol': join(ROOT, 'src', 'XNAVMandate.sol'),
    'test/Mocks.sol': join(HERE, 'Mocks.sol'),
  };
  const sources = {};
  for (const [name, path] of Object.entries(files)) {
    sources[name] = { content: readFileSync(path, 'utf8') };
  }
  const out = JSON.parse(solc.compile(JSON.stringify({
    language: 'Solidity',
    sources,
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: 'paris',
      outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
    },
  })));
  const fatal = (out.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) throw new Error(JSON.stringify(fatal, null, 2));
  const pick = (file, name) => {
    const c = out.contracts[file][name];
    return { abi: c.abi, bytecode: '0x' + c.evm.bytecode.object, iface: new Interface(c.abi) };
  };
  return {
    oracle: pick('src/XNAVOracle.sol', 'XNAVOracle'),
    vault: pick('src/SwingVault4626.sol', 'SwingVault4626'),
    mandate: pick('src/XNAVMandate.sol', 'XNAVMandate'),
    equity: pick('test/Mocks.sol', 'MockEquity'),
    equityFail: pick('test/Mocks.sol', 'MockEquityFail'),
    asset: pick('test/Mocks.sol', 'MockAsset'),
    core: pick('test/Mocks.sol', 'MockCore'),
    coreFail: pick('test/Mocks.sol', 'MockCoreFail'),
  };
}

const BUILT = compile();
const coder = AbiCoder.defaultAbiCoder();

function header(timestamp) {
  return {
    timestamp, number: 1n, coinbase: Address.zero(), difficulty: 0n,
    prevRandao: new Uint8Array(32), gasLimit: GAS, baseFeePerGas: 0n,
    getBlobGasPrice: () => 0n,
  };
}

function revertReason(execResult) {
  const ret = Buffer.from(execResult.returnValue || new Uint8Array()).toString('hex');
  if (ret.startsWith('08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', '0x' + ret)[0];
  }
  return execResult.exceptionError ? String(execResult.exceptionError.error) : null;
}

async function newVm(chainId = CHAIN) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  await vm.stateManager.putAccount(new Address(hexToBytes(ALICE)), new Account(0n, 10n ** 18n));
  return vm;
}

async function run(vm, to, data, timestamp = TS) {
  return vm.evm.runCall({
    caller: new Address(hexToBytes(ALICE)),
    to: to ? new Address(hexToBytes(to)) : undefined,
    data: getBytes(data),
    gasLimit: GAS,
    value: 0n,
    block: { header: header(timestamp) },
  });
}

async function deploy(vm, bytecode, types, args, timestamp = TS) {
  const data = types ? concat([bytecode, coder.encode(types, args)]) : bytecode;
  const res = await run(vm, undefined, data, timestamp);
  return { res, address: res.createdAddress ? res.createdAddress.toString() : null };
}

async function etch(vm, live, dest) {
  const code = await vm.stateManager.getContractCode(new Address(hexToBytes(live)));
  assert.ok(code.length > 0);
  await vm.stateManager.putContractCode(new Address(hexToBytes(dest)), code);
}

function domain(verifying) {
  return { name: PH.eip712_name, version: PH.eip712_version, chainId: CHAIN, verifyingContract: verifying };
}

async function signUpdate(oracle, body) {
  const value = {
    vault: body.vault,
    xnavBps: body.xnavBps,
    stressBps: body.stressBps,
    ts: body.ts,
  };
  const sigs = [];
  for (const key of KEYS.slice(0, 2)) sigs.push(await key.signTypedData(domain(oracle), TYPES, value));
  return sigs;
}

const oracleArgs = [
  'address[]', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256',
];

function oracleParams() {
  return [
    KEYS.map((k) => k.address),
    2,
    PH.staleness_seconds,
    PH.delta_cap_bps,
    PH.max_feed_bps,
    PH.l1_read_gas_base,
    PH.l1_read_gas_per_word,
  ];
}

test('the type string in the placeholder file is the type the contract hashes', async () => {
  const vm = await newVm();
  const deployed = await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams());
  assert.equal(revertReason(deployed.res.execResult), null);
  const data = BUILT.oracle.iface.encodeFunctionData('UPDATE_TYPEHASH', []);
  const res = await run(vm, deployed.address, data);
  assert.equal(revertReason(res.execResult), null);
  const got = BUILT.oracle.iface.decodeFunctionResult('UPDATE_TYPEHASH', res.execResult.returnValue)[0];
  assert.equal(got, keccak256(toUtf8Bytes(PH.eip712_type)));
});

test('the constructor refuses mainnet chain ids', async () => {
  for (const id of MAINNETS) {
    const vm = await newVm(id);
    const oracle = await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams());
    assert.equal(revertReason(oracle.res.execResult), 'testnet only', `oracle ${id}`);
    const vault = await deploy(vm, BUILT.vault.bytecode,
      ['address', 'address', 'address', 'address', 'uint256', 'uint256', 'uint256'],
      [ALICE, ALICE, VAULT, PRECOMPILE, 500, 1500, 3600]);
    assert.equal(revertReason(vault.res.execResult), 'testnet only', `vault ${id}`);
    const mandate = await deploy(vm, BUILT.mandate.bytecode, ['address', 'address', 'uint256'],
      [ALICE, PH.corewriter, PH.add_api_wallet_action]);
    assert.equal(revertReason(mandate.res.execResult), 'testnet only', `mandate ${id}`);
  }
});

test('two of three stores a haircut, one does not, and unset is not zero', async () => {
  const vm = await newVm();
  const deployed = await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams());
  const oracle = deployed.address;
  assert.equal(revertReason(deployed.res.execResult), null, 'deploy');

  const quiet = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latest', [VAULT]));
  assert.equal(revertReason(quiet.execResult), 'unset');

  const body = { vault: VAULT, xnavBps: 4560, stressBps: 5000, ts: TS };
  const [sigA, sigB] = await signUpdate(oracle, body);
  const one = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'update', [VAULT, 4560, 5000, TS, sigA, sigA],
  ));
  assert.equal(revertReason(one.execResult), 'bad sig');

  const stored = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'update', [VAULT, 4560, 5000, TS, sigA, sigB],
  ));
  assert.equal(revertReason(stored.execResult), null, 'update');
  const live = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latest', [VAULT]));
  const decoded = BUILT.oracle.iface.decodeFunctionResult('latest', live.execResult.returnValue);
  assert.equal(decoded[0], 4560n);
  assert.equal(decoded[1], 5000n);
  assert.notEqual(decoded[0], 0n);

  const replay = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'update', [VAULT, 4560, 5000, TS, sigA, sigB],
  ), TS + 1n);
  assert.equal(revertReason(replay.execResult), 'replay');

  const jump = { vault: VAULT, xnavBps: 4560 + PH.delta_cap_bps + 1, stressBps: 5000, ts: TS + 1n };
  const jumped = await signUpdate(oracle, jump);
  const moved = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'update', [VAULT, jump.xnavBps, jump.stressBps, jump.ts, jumped[0], jumped[1]],
  ), TS + 1n);
  assert.equal(revertReason(moved.execResult), 'delta');

  const stale = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('latest', [VAULT]), TS + BigInt(PH.staleness_seconds) + 1n);
  assert.equal(revertReason(stale.execResult), 'stale');

  const scheme = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('rejectScheme', [1]));
  assert.equal(revertReason(scheme.execResult), 'scheme');
  const okScheme = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('rejectScheme', [0]));
  assert.equal(revertReason(okScheme.execResult), null);

  const gas = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData('l1ReadGas', [2, 1]));
  const gasOut = BUILT.oracle.iface.decodeFunctionResult('l1ReadGas', gas.execResult.returnValue)[0];
  assert.equal(gasOut, BigInt(PH.l1_read_gas_base + PH.l1_read_gas_per_word * 3));

  const hashed = TypedDataEncoder.hash(domain(oracle), TYPES, body);
  const onchain = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'digestFor', [CHAIN, oracle, VAULT, 4560, 5000, TS],
  ));
  const dig = BUILT.oracle.iface.decodeFunctionResult('digestFor', onchain.execResult.returnValue)[0];
  assert.equal(dig, hashed);

  const vector = JSON.parse(readFileSync(join(REPO, 'bot', 'xnav', 'fixtures', 'digest_vector.json'), 'utf8'));
  const frozen = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'digestFor', [vector.chain_id, vector.verifying, vector.vault, vector.xnav_bps, vector.stress_bps, vector.ts],
  ));
  const frozenDig = BUILT.oracle.iface.decodeFunctionResult('digestFor', frozen.execResult.returnValue)[0];
  assert.equal(frozenDig, '0x' + vector.digest);
});

test('a failed or negative equity precompile is not zero assets', async () => {
  const vm = await newVm();
  const oracle = (await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams())).address;
  const asset = (await deploy(vm, BUILT.asset.bytecode, [], [])).address;
  const vaultD = await deploy(vm, BUILT.vault.bytecode,
    ['address', 'address', 'address', 'address', 'uint256', 'uint256', 'uint256'],
    [asset, oracle, VAULT, PRECOMPILE, PH.flow_threshold_bps, PH.max_swing_bps, PH.epoch_seconds]);
  assert.equal(revertReason(vaultD.res.execResult), null, 'vault');
  const bare = await run(vm, vaultD.address, BUILT.vault.iface.encodeFunctionData('totalAssets', []));
  assert.equal(revertReason(bare.execResult), 'unreadable');

  const negative = await deploy(vm, BUILT.equity.bytecode, ['int256'], [-1]);
  await etch(vm, negative.address, PRECOMPILE);
  const neg = await run(vm, vaultD.address, BUILT.vault.iface.encodeFunctionData('totalAssets', []));
  assert.equal(revertReason(neg.execResult), 'unreadable');

  const fail = await deploy(vm, BUILT.equityFail.bytecode, [], []);
  await etch(vm, fail.address, PRECOMPILE);
  const failed = await run(vm, vaultD.address, BUILT.vault.iface.encodeFunctionData('vaultEquity', []));
  assert.equal(revertReason(failed.execResult), 'unreadable');
});

test('swing on the excess matches the off-chain split and stays in the vault', async () => {
  const vm = await newVm();
  const oracleD = await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams());
  const oracle = oracleD.address;
  const equity = await deploy(vm, BUILT.equity.bytecode, ['int256'], [0]);
  await etch(vm, equity.address, PRECOMPILE);
  const assetD = await deploy(vm, BUILT.asset.bytecode, [], []);
  const asset = assetD.address;
  const vaultD = await deploy(vm, BUILT.vault.bytecode,
    ['address', 'address', 'address', 'address', 'uint256', 'uint256', 'uint256'],
    [asset, oracle, VAULT, PRECOMPILE, PH.flow_threshold_bps, PH.max_swing_bps, PH.epoch_seconds]);
  const vault = vaultD.address;
  assert.equal(revertReason(vaultD.res.execResult), null);

  const body = { vault: VAULT, xnavBps: 4560, stressBps: 6000, ts: TS };
  const [sigA, sigB] = await signUpdate(oracle, body);
  const stored = await run(vm, oracle, BUILT.oracle.iface.encodeFunctionData(
    'update', [VAULT, 4560, 6000, TS, sigA, sigB],
  ));
  assert.equal(revertReason(stored.execResult), null, 'oracle');

  const amount = 10_000n;
  await run(vm, asset, BUILT.asset.iface.encodeFunctionData('mint', [ALICE, amount]));
  await run(vm, asset, BUILT.asset.iface.encodeFunctionData('approve', [vault, amount]));
  const deposited = await run(vm, vault, BUILT.vault.iface.encodeFunctionData('deposit', [amount, ALICE]));
  assert.equal(revertReason(deposited.execResult), null, 'deposit');

  const flow = amount;
  const total = amount;
  const supply = amount;
  for (let i = 1; i <= 20; i++) {
    const shares = BigInt(i) * 100n;
    const expected = swing.previewRedeem(
      total, supply, shares, flow,
      BigInt(PH.flow_threshold_bps), 4560n, BigInt(PH.max_swing_bps),
    );
    assert.equal(expected.readable, true, `case ${i}`);
    const preview = await run(vm, vault, BUILT.vault.iface.encodeFunctionData('previewRedeem', [shares]));
    assert.equal(revertReason(preview.execResult), null, `preview ${i}`);
    const got = BUILT.vault.iface.decodeFunctionResult('previewRedeem', preview.execResult.returnValue)[0];
    assert.equal(got, expected.assetsOut, `swing ${i}`);
    const nav = await run(vm, vault, BUILT.vault.iface.encodeFunctionData('convertToAssets', [shares]));
    const atNav = BUILT.vault.iface.decodeFunctionResult('convertToAssets', nav.execResult.returnValue)[0];
    if (expected.swingBps > 0n) assert.ok(got < atNav, `swing paid less than nav at ${i}`);
  }

  const half = 5_000n;
  const before = swing.previewRedeem(
    total, supply, half, flow, BigInt(PH.flow_threshold_bps), 4560n, BigInt(PH.max_swing_bps),
  );
  const redeemed = await run(vm, vault, BUILT.vault.iface.encodeFunctionData('redeem', [half, ALICE, ALICE]));
  assert.equal(revertReason(redeemed.execResult), null, 'redeem');
  const paid = BUILT.vault.iface.decodeFunctionResult('redeem', redeemed.execResult.returnValue)[0];
  assert.equal(paid, before.assetsOut);
  assert.ok(before.retained > 0n, 'swing cash stayed');
  const left = await run(vm, vault, BUILT.vault.iface.encodeFunctionData('convertToAssets', [1_000n]));
  const per = BUILT.vault.iface.decodeFunctionResult('convertToAssets', left.execResult.returnValue)[0];
  assert.ok(per > 1_000n, 'remaining shares gained the swing');
});

test('a coin with no cap is not unlimited, and a failed de-risk stays paused', async () => {
  const vm = await newVm();
  const core = await deploy(vm, BUILT.core.bytecode, [], []);
  await etch(vm, core.address, PH.corewriter);
  const mandateD = await deploy(vm, BUILT.mandate.bytecode, ['address', 'address', 'uint256'],
    [ALICE, PH.corewriter, PH.add_api_wallet_action]);
  const mandate = mandateD.address;
  assert.equal(revertReason(mandateD.res.execResult), null);
  const coin = keccak256(toUtf8Bytes('XMR'));
  const open = await run(vm, mandate, BUILT.mandate.iface.encodeFunctionData('check', [coin, 10]));
  assert.equal(revertReason(open.execResult), null);
  assert.equal(BUILT.mandate.iface.decodeFunctionResult('check', open.execResult.returnValue)[0], false);
  const paused = await run(vm, mandate, BUILT.mandate.iface.encodeFunctionData('depositsPaused', []));
  assert.equal(BUILT.mandate.iface.decodeFunctionResult('depositsPaused', paused.execResult.returnValue)[0], true);

  const freshCore = await deploy(vm, BUILT.core.bytecode, [], []);
  await etch(vm, freshCore.address, PH.corewriter);
  const second = await deploy(vm, BUILT.mandate.bytecode, ['address', 'address', 'uint256'],
    [ALICE, PH.corewriter, PH.add_api_wallet_action]);
  const m2 = second.address;
  await run(vm, m2, BUILT.mandate.iface.encodeFunctionData('setCap', [coin, 100]));
  const under = await run(vm, m2, BUILT.mandate.iface.encodeFunctionData('check', [coin, 100]));
  assert.equal(BUILT.mandate.iface.decodeFunctionResult('check', under.execResult.returnValue)[0], true);
  const over = await run(vm, m2, BUILT.mandate.iface.encodeFunctionData('check', [coin, 101]));
  assert.equal(BUILT.mandate.iface.decodeFunctionResult('check', over.execResult.returnValue)[0], false);

  const derisk = await run(vm, m2, BUILT.mandate.iface.encodeFunctionData('requestDerisk', [coin, 50]));
  assert.equal(revertReason(derisk.execResult), null, 'derisk');
  const hits = await run(vm, PH.corewriter, BUILT.core.iface.encodeFunctionData('hits', []));
  assert.equal(BUILT.core.iface.decodeFunctionResult('hits', hits.execResult.returnValue)[0], 1n);

  const failing = await deploy(vm, BUILT.coreFail.bytecode, [], []);
  await etch(vm, failing.address, PH.corewriter);
  const third = await deploy(vm, BUILT.mandate.bytecode, ['address', 'address', 'uint256'],
    [ALICE, PH.corewriter, PH.add_api_wallet_action]);
  const sent = await run(vm, third.address, BUILT.mandate.iface.encodeFunctionData('requestDerisk', [coin, 50]));
  assert.equal(revertReason(sent.execResult), null);
  const still = await run(vm, third.address, BUILT.mandate.iface.encodeFunctionData('depositsPaused', []));
  assert.equal(BUILT.mandate.iface.decodeFunctionResult('depositsPaused', still.execResult.returnValue)[0], true);
});

test('chain 998 accepts the oracle', async () => {
  const vm = await newVm(998);
  const deployed = await deploy(vm, BUILT.oracle.bytecode, oracleArgs, oracleParams());
  assert.equal(revertReason(deployed.res.execResult), null);
  assert.ok(deployed.address);
  assert.notEqual(deployed.address, ZERO);
});
