'use strict';
/**
 * A read-only look at ERC-20 Transfer logs: did this destination send this
 * account anything the public logs can see?
 *
 * Native transfers are not in those logs. An empty answer is "no ERC-20
 * transfer in the window", not "no dust of any kind". A read that fails is
 * unread, not empty. Nothing is stored.
 *
 * The chains are the ones Claw Guard already simulates on (lib/evm_chains.js).
 * A chain with no block interval here is not guessed: the wrong interval
 * would turn a short read into a claim about 30 days.
 */

const { CHAINS } = require('./evm_chains');
const { WINDOW_DAYS } = require('../public/js/address-poison.js');

/** Seconds per block. Keyed by the same chain ids CHAINS holds. */
const BLOCK_SEC = {
  8453: 2,   // Base
  84532: 2,  // Base Sepolia
};

const TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
const TIMEOUT_MS = 6000;
const MAX_BLOCKS = 8;

function padTopic(addr) {
  return '0x' + addr.slice(2).toLowerCase().padStart(64, '0');
}

function hexQty(n) {
  return '0x' + n.toString(16);
}

async function rpc(fetchImpl, url, method, params, timeoutMs) {
  const res = await fetchImpl(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: method, params: params }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res || !res.ok) return null;
  const j = await res.json();
  if (!j || j.error || !('result' in j)) return { refused: true };
  return { result: j.result };
}

/**
 * → { read: true, inbound: [{from, amount, at}] }
 * → { read: false, reason: 'unsupported_chain'|'invalid'|'unreachable'|'rejected' }
 */
async function readErc20Inbound(opts) {
  const o = opts || {};
  const chains = o.chains || CHAINS;
  const chainId = o.chainId == null ? 8453 : Number(o.chainId);
  const chain = chains[chainId];
  const sec = BLOCK_SEC[chainId];
  if (!chain || !sec || !Array.isArray(chain.rpcs) || !chain.rpcs.length) {
    return { read: false, reason: 'unsupported_chain' };
  }
  const account = String(o.account || '');
  const destination = String(o.destination || '');
  if (!/^0x[0-9a-fA-F]{40}$/.test(account) || !/^0x[0-9a-fA-F]{40}$/.test(destination)) {
    return { read: false, reason: 'invalid' };
  }
  const fetchImpl = o.fetchImpl || (typeof fetch === 'function' ? fetch : null);
  if (!fetchImpl) return { read: false, reason: 'unreachable' };
  const timeoutMs = o.timeoutMs || TIMEOUT_MS;
  const span = BigInt(Math.ceil((WINDOW_DAYS * 24 * 60 * 60) / sec));

  let refused = false;
  for (const url of chain.rpcs) {
    let head;
    try {
      head = await rpc(fetchImpl, url, 'eth_blockNumber', [], timeoutMs);
    } catch (e) {
      continue;
    }
    if (!head) continue;
    if (head.refused) { refused = true; continue; }
    if (typeof head.result !== 'string' || !/^0x[0-9a-fA-F]+$/.test(head.result)) continue;
    let latest;
    try { latest = BigInt(head.result); } catch (e) { continue; }
    const fromBlock = latest > span ? latest - span : 0n;
    let logs;
    try {
      logs = await rpc(fetchImpl, url, 'eth_getLogs', [{
        fromBlock: hexQty(fromBlock),
        toBlock: hexQty(latest),
        topics: [TRANSFER_TOPIC, padTopic(destination), padTopic(account)],
      }], timeoutMs);
    } catch (e) {
      continue;
    }
    if (!logs) continue;
    if (logs.refused) { refused = true; continue; }
    if (!Array.isArray(logs.result)) continue;
    if (logs.result.length === 0) return { read: true, inbound: [] };
    const blocks = [];
    for (const log of logs.result) {
      const bn = log && typeof log.blockNumber === 'string' ? log.blockNumber : null;
      if (bn && blocks.indexOf(bn) < 0) blocks.push(bn);
    }
    if (blocks.length > MAX_BLOCKS) return { read: false, reason: 'rejected' };
    const times = new Map();
    let timesOk = true;
    for (const bn of blocks) {
      let block;
      try {
        block = await rpc(fetchImpl, url, 'eth_getBlockByNumber', [bn, false], timeoutMs);
      } catch (e) {
        timesOk = false;
        break;
      }
      if (!block || block.refused || !block.result || typeof block.result.timestamp !== 'string') {
        timesOk = false;
        break;
      }
      try { times.set(bn, Number(BigInt(block.result.timestamp)) * 1000); } catch (e) {
        timesOk = false;
        break;
      }
    }
    if (!timesOk) return { read: false, reason: 'rejected' };
    const inbound = [];
    for (const log of logs.result) {
      const data = log && typeof log.data === 'string' ? log.data : null;
      const at = log && times.get(log.blockNumber);
      if (!data || !/^0x[0-9a-fA-F]*$/.test(data) || !Number.isFinite(at)) {
        inbound.push({ from: destination, amount: null, at: null });
        continue;
      }
      inbound.push({ from: destination, amount: data, at: at });
    }
    return { read: true, inbound: inbound };
  }
  return { read: false, reason: refused ? 'rejected' : 'unreachable' };
}

module.exports = { BLOCK_SEC, TRANSFER_TOPIC, readErc20Inbound, MAX_BLOCKS };
