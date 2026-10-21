'use strict';
/**
 * The in-repo ABI decoder against real `ethers` (installed here, the
 * reference), for the reason abi_call.test.js gives: production resolves
 * `ethers` to a stub, so what the server reads from a chain is decoded by
 * code that has no dependency to substitute.
 */
const test = require('node:test');
const assert = require('node:assert');
const { ethers } = require('ethers');

const { decodeWords, decodeLog, eventTopic } = require('../lib/abi_decode');
const memeclaw = require('../lib/memeclaw');

const coder = ethers.AbiCoder.defaultAbiCoder();

test('static and string words decode as ethers encodes them', () => {
  const types = ['address', 'uint256', 'bool', 'bytes32', 'string', 'uint64', 'string', 'bool'];
  const values = ['0x' + 'ab'.repeat(20), 123456789012345678901234567890n, true,
    '0x' + '5e'.repeat(32), 'Much Claw ✓ ünïcode', 1_800_000_000n, '', false];
  const hex = coder.encode(types, values);
  const got = decodeWords(types, hex);
  assert.equal(got[0], values[0].toLowerCase());
  assert.equal(got[1], values[1]);
  assert.equal(got[2], true);
  assert.equal(got[3], values[3]);
  assert.equal(got[4], values[4]);
  assert.equal(got[5], values[5]);
  assert.equal(got[6], '');
  assert.equal(got[7], false);
});

test('the factory\'s events decode as ethers parses them', () => {
  const iface = new ethers.Interface(memeclaw.FACTORY.abi);
  for (const ev of [memeclaw.EVENTS.Launched, memeclaw.EVENTS.Trade]) {
    assert.equal(eventTopic(ev), iface.getEvent(ev.name).topicHash, ev.name);
  }
  const token = '0x' + '12'.repeat(20);
  const creator = '0x' + '34'.repeat(20);
  const terms = '0x' + 'ef'.repeat(32);
  const launched = iface.encodeEventLog('Launched', [token, creator, terms, 'Much Claw', 'CLAW']);
  const a = decodeLog(memeclaw.EVENTS.Launched, { ...launched, address: '0xF00' });
  assert.deepEqual(a.args, { token, creator, termsHash: terms, name: 'Much Claw', symbol: 'CLAW' });
  const trade = iface.encodeEventLog('Trade', [token, creator, false, 5n, 7n, 9n, 11n]);
  const t = decodeLog(memeclaw.EVENTS.Trade, trade);
  assert.deepEqual(t.args, { token, trader: creator, isBuy: false, dogeAmount: 5n,
    tokenAmount: 7n, dogeReserve: 9n, tokenReserve: 11n });
  // The other arm: a Trade log is not a Launched log.
  assert.equal(decodeLog(memeclaw.EVENTS.Launched, trade), null);
});

test('what does not decode is refused, never read approximately', () => {
  assert.throws(() => decodeWords(['uint8'], '0x' + '00'.repeat(32)), /unsupported/);
  assert.throws(() => decodeWords(['uint256'], '0x' + '00'.repeat(31)), /ends inside a word/);
  assert.throws(() => decodeWords(['bool'], '0x' + '00'.repeat(31) + '02'), /neither 0 nor 1/);
  assert.throws(() => decodeWords(['address'], '0x' + 'ff'.repeat(32)), /high bits/);
  assert.throws(() => decodeWords(['uint256'], '0x0'), /even-length/);
  const short = coder.encode(['string'], ['abcdef']).slice(0, -64);
  assert.throws(() => decodeWords(['string'], short), /ends inside/);
  assert.throws(() => decodeWords(['uint64'], coder.encode(['uint256'], [1n << 64n])), /uint64 out of range/);
});
