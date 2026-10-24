'use strict';
/**
 * Unsigned revokes for Panic Button.
 *
 * approve(spender, 0) is allowances.js's revoke plan. setApprovalForAll and
 * the Permit2 lockdown are encoded here and checked against the Claw Guard
 * decoder (txray-model.js, sigray-model.js). Nothing in this file signs or
 * broadcasts. A 7702 clear is an authorisation to the zero address, which
 * the user's wallet signs.
 */

const { keccak256, toUtf8Bytes } = require('./keccak256');
const { encodeCall } = require('./abi_call');
const { SPENDERS, revokePlan } = require('./allowances');
const txray = require('../public/js/txray-model');
const sigray = require('../public/js/sigray-model');

const ZERO = '0x0000000000000000000000000000000000000000';
const PERMIT2 = SPENDERS.find((s) => s.label.indexOf('Permit2') === 0).address;

function word(hexBody) {
  const body = String(hexBody).replace(/^0x/i, '').toLowerCase();
  if (!/^[0-9a-f]*$/.test(body) || body.length > 64) throw new Error('panic_tx: word');
  return body.padStart(64, '0');
}

/** Permit2 lockdown((address,address)[]). One pair per revoke. */
function encodeLockdown(pairs) {
  if (!Array.isArray(pairs) || pairs.length < 1 || pairs.length > 20) {
    throw new Error('panic_tx: lockdown pairs');
  }
  const sel = keccak256(toUtf8Bytes('lockdown((address,address)[])')).slice(0, 10);
  let tail = word(pairs.length.toString(16));
  for (const p of pairs) {
    if (!/^0x[0-9a-fA-F]{40}$/.test(p.token) || !/^0x[0-9a-fA-F]{40}$/.test(p.spender)) {
      throw new Error('panic_tx: lockdown address');
    }
    tail += word(p.token.slice(2));
    tail += word(p.spender.slice(2));
  }
  return sel + word('20') + tail;
}

function buildRevoke(item) {
  const chainId = item.chain_id;
  const value = '0x0';
  if (item.kind === 'erc20') {
    const plan = revokePlan(item.token, item.spender, chainId);
    return {
      kind: 'transaction', to: plan.to, data: plan.data, value: plan.value, chain_id: plan.chain_id,
    };
  }
  if (item.kind === 'approval_for_all') {
    return {
      kind: 'transaction',
      to: item.token,
      data: encodeCall('setApprovalForAll(address,bool)', [item.spender, false]),
      value,
      chain_id: chainId,
    };
  }
  if (item.kind === 'permit2') {
    return {
      kind: 'transaction',
      to: PERMIT2,
      data: encodeLockdown([{ token: item.token, spender: item.spender }]),
      value,
      chain_id: chainId,
    };
  }
  if (item.kind === 'delegation_7702') {
    return {
      kind: 'authorization',
      to: null,
      data: '0x',
      value,
      chain_id: chainId,
      authorization: { chainId: chainId, address: ZERO },
    };
  }
  if (item.kind === 'erc721_single' && item.nft_id != null && String(item.nft_id) !== '') {
    return {
      kind: 'transaction',
      to: item.token,
      data: encodeCall('approve(address,uint256)', [ZERO, BigInt(item.nft_id)]),
      value,
      chain_id: chainId,
    };
  }
  return null;
}

/** A wallet deep link. This page does not call the wallet itself. */
function walletLink(item, tx) {
  if (!tx) return null;
  if (tx.kind === 'authorization') return 'ethereum:' + ZERO + '@' + tx.chain_id;
  if (item.kind === 'erc20') {
    return 'ethereum:' + tx.to + '@' + tx.chain_id + '/approve?address=' + item.spender + '&uint256=0';
  }
  if (item.kind === 'approval_for_all') {
    return 'ethereum:' + tx.to + '@' + tx.chain_id
      + '/setApprovalForAll?address=' + item.spender + '&bool=false';
  }
  return 'ethereum:' + tx.to + '@' + tx.chain_id + '?value=0&data=' + tx.data;
}

function attachRevokes(result) {
  const items = (result.items || []).map((item) => {
    const tx = buildRevoke(item);
    const copy = {};
    for (const k of Object.keys(item)) {
      if (k === 'nft_id') continue;
      copy[k] = item[k];
    }
    copy.tx = tx;
    copy.open_url = walletLink(item, tx);
    return copy;
  });
  return Object.assign({}, result, { items: items });
}

function decodePrepared(tx) {
  if (!tx) return null;
  if (tx.kind === 'authorization') return sigray.decodeAuthorization(tx.authorization);
  return txray.decodeTx({ data: tx.data, value: tx.value == null ? '0' : String(tx.value) });
}

module.exports = {
  ZERO: ZERO,
  PERMIT2: PERMIT2,
  encodeLockdown: encodeLockdown,
  buildRevoke: buildRevoke,
  walletLink: walletLink,
  attachRevokes: attachRevokes,
  decodePrepared: decodePrepared,
};
