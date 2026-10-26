'use strict';
/**
 * Unsigned calldata for Claw Firewall.
 *
 * abi_call cannot encode a tuple array, so executeBatch((address,uint256,bytes)[])
 * is built here and checked against ethers in the route test. Nothing in this
 * file signs or broadcasts. A 7702 clear is only offered on a chain that has
 * EIP-7702. Fuji does not. The wallet-delegation test is Base Sepolia.
 */

const { keccak256, toUtf8Bytes } = require('./keccak256');
const { encodeCall } = require('./abi_call');
const panicTx = require('./panic_tx');
const encodeLockdown = panicTx.encodeLockdown;
const buildRevoke = panicTx.buildRevoke;
const PERMIT2 = panicTx.PERMIT2;

const SUPPORTS_7702 = new Set([84532, 421614]);
const DELEGATION_TEST_CHAIN = 84532;
const PERMIT2_CHUNK = 20;

function word(hexBody) {
  const body = String(hexBody).replace(/^0x/i, '').toLowerCase();
  if (!/^[0-9a-f]*$/.test(body) || body.length > 64) throw new Error('claw_firewall_tx: word');
  return body.padStart(64, '0');
}

function padRight(body) {
  if (!body.length) return '';
  const rem = body.length % 64;
  return rem === 0 ? body : body + '0'.repeat(64 - rem);
}

function addressWord(addr) {
  if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) throw new Error('claw_firewall_tx: address');
  return word(addr.slice(2));
}

function uintWord(value) {
  const n = typeof value === 'bigint' ? value : BigInt(value);
  if (n < 0n) throw new Error('claw_firewall_tx: value');
  return word(n.toString(16));
}

function encodeTuple(call) {
  const data = String(call.data || '0x').replace(/^0x/i, '').toLowerCase();
  if (!/^[0-9a-f]*$/.test(data) || data.length % 2 !== 0) throw new Error('claw_firewall_tx: data');
  const value = call.value == null ? 0 : call.value;
  const head = addressWord(call.target) + uintWord(value) + word('60');
  return head + word((data.length / 2).toString(16)) + padRight(data);
}

/** executeBatch((address,uint256,bytes)[]). One shared account entry, not a helper the EOA calls. */
function encodeExecuteBatch(calls) {
  if (!Array.isArray(calls) || calls.length < 1 || calls.length > 64) {
    throw new Error('claw_firewall_tx: batch');
  }
  const sel = keccak256(toUtf8Bytes('executeBatch((address,uint256,bytes)[])')).slice(2, 10);
  const tuples = calls.map(encodeTuple);
  let offsets = '';
  let tail = '';
  let cursor = calls.length * 32;
  for (const tuple of tuples) {
    offsets += word(cursor.toString(16));
    tail += tuple;
    cursor += tuple.length / 2;
  }
  return '0x' + sel + word('20') + word(calls.length.toString(16)) + offsets + tail;
}

function pairKey(item) {
  const token = String(item.token || '').toLowerCase();
  const spender = String(item.spender || '').toLowerCase();
  return String(item.chain_id) + ':' + token + ':' + spender;
}

function pairsHash(keys) {
  const lines = (keys || []).map(String);
  lines.sort();
  return keccak256(toUtf8Bytes(lines.join('\n')));
}

function chunk(list, size) {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

/**
 * Individual revokes, plus one executeBatch for an account that can batch.
 * Permit2 pairs on one chain share a lockdown, 20 at a time. Nothing is dropped.
 * A plain EOA still uses the individual transactions. Revoke stays available.
 */
function prepareRevokes(items, keepKeys) {
  const keep = new Set(keepKeys || []);
  const kept = [];
  const revoking = [];
  for (const item of items || []) {
    if (item.kind === 'delegation_7702') {
      revoking.push(item);
      continue;
    }
    if (keep.has(pairKey(item))) kept.push(item);
    else revoking.push(item);
  }
  const individual = [];
  const authorizations = [];
  const byChain = new Map();
  const plainCalls = [];
  for (const item of revoking) {
    if (item.kind === 'delegation_7702') {
      if (!SUPPORTS_7702.has(Number(item.chain_id))) {
        individual.push(Object.assign({}, item, {
          tx: null,
          note: 'Claw Firewall does not prepare a 7702 clear on this chain.',
        }));
        continue;
      }
      const tx = buildRevoke(item);
      individual.push(Object.assign({}, item, { tx: tx }));
      if (tx) authorizations.push(tx);
      continue;
    }
    const tx = buildRevoke(item);
    individual.push(Object.assign({}, item, { tx: tx }));
    if (!tx || tx.kind !== 'transaction') continue;
    if (item.kind === 'permit2') {
      const list = byChain.get(item.chain_id) || [];
      list.push({ token: item.token, spender: item.spender, chain_id: item.chain_id });
      byChain.set(item.chain_id, list);
      continue;
    }
    plainCalls.push({ target: tx.to, value: 0, data: tx.data, chain_id: tx.chain_id });
  }
  const permit2Calls = [];
  for (const [chainId, pairs] of byChain) {
    const parts = chunk(pairs, PERMIT2_CHUNK);
    for (const part of parts) {
      permit2Calls.push({
        target: PERMIT2,
        value: 0,
        data: encodeLockdown(part),
        chain_id: chainId,
        pairs: part.length,
        split: parts.length > 1,
      });
    }
  }
  const batchCalls = plainCalls.concat(permit2Calls.map(function (c) {
    return { target: c.target, value: 0, data: c.data };
  }));
  let executeBatch = null;
  if (batchCalls.length > 0) {
    executeBatch = {
      data: encodeExecuteBatch(batchCalls),
      value: '0x0',
      note: 'One transaction for a 7702 account or an ERC-7579 account. An EOA sends each revoke on its own.',
    };
  }
  return {
    kept: kept.map(pairKey),
    kept_line: 'Left in place. This is not a judgement.',
    individual: individual,
    authorizations: authorizations,
    permit2_calls: permit2Calls,
    execute_batch: executeBatch,
    delegation_test_chain: DELEGATION_TEST_CHAIN,
  };
}

function encodeInit(guardian, vault) {
  return encodeCall('init(address,address,bytes)', [guardian, vault, '0x']);
}

module.exports = {
  SUPPORTS_7702: SUPPORTS_7702,
  DELEGATION_TEST_CHAIN: DELEGATION_TEST_CHAIN,
  encodeExecuteBatch: encodeExecuteBatch,
  pairKey: pairKey,
  pairsHash: pairsHash,
  prepareRevokes: prepareRevokes,
  encodeInit: encodeInit,
};
