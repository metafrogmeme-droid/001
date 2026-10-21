'use strict';
/**
 * Memeclaw's reader of the DogeOS testnet: JSON-RPC over the chain registry's
 * endpoints (lib/root_anchor.js, the one reading of a chain), with a time
 * limit on every call.
 *
 * READ ONLY. Nothing here signs or sends: the methods it may call are listed
 * in READS, and `rpc()` refuses any other. A wallet sends every Memeclaw
 * transaction; the server only asks the chain what happened.
 *
 * THREE ANSWERS. A call returns the chain's answer, or throws ChainUnreadable
 * when no endpoint answered in time. A caller turns that into "could not be
 * read", never into "no such transaction" or an empty list: a chain that did
 * not answer has said nothing.
 */
const { chainInfo } = require('./root_anchor');

const CHAIN_KEY = 'dogeos-testnet';
const TIMEOUT_MS = 6000;
const READS = new Set([
  'eth_chainId', 'eth_blockNumber', 'eth_getTransactionReceipt', 'eth_getTransactionByHash',
  'eth_getLogs', 'eth_getBlockByNumber', 'eth_getCode', 'eth_call',
]);

class ChainUnreadable extends Error {
  constructor(reason) {
    super(reason);
    this.name = 'ChainUnreadable';
  }
}

/** Test seam: { fetch, rpcs }. */
const seam = {};

function chain() {
  const c = chainInfo(CHAIN_KEY);
  if (!c || c.testnet !== true) {
    // Memeclaw runs on a testnet or not at all; a registry that ever lists
    // this key as a chain with value stops it here.
    throw new ChainUnreadable('memeclaw chain is not a testnet');
  }
  return c;
}

async function post(f, url, method, params) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await f(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
      signal: ctl.signal,
    });
    if (!res || !res.ok) throw new Error(`http ${res ? res.status : '?'}`);
    const body = await res.json();
    if (body && body.error) {
      const e = new Error(String(body.error.message || 'rpc error'));
      e.rpcError = true;
      throw e;
    }
    return body ? body.result : null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * One read, tried on each endpoint in turn. An endpoint that answers with a
 * JSON-RPC error has answered: the error is the chain's (a range too wide, a
 * bad parameter) and is thrown as it is, not retried elsewhere.
 */
async function rpc(method, params) {
  if (!READS.has(method)) throw new Error(`memeclaw_chain: ${method} is not a read`);
  const f = seam.fetch || (typeof fetch === 'function' ? fetch : null);
  if (!f) throw new ChainUnreadable('no fetch implementation');
  const urls = seam.rpcs || chain().rpcs;
  let last = null;
  for (const url of urls) {
    try {
      return await post(f, url, method, params);
    } catch (e) {
      if (e && e.rpcError) throw e;
      last = e;
    }
  }
  throw new ChainUnreadable(`no endpoint answered ${method}${last ? ` (${last.name || 'Error'})` : ''}`);
}

const hexNum = (n) => '0x' + BigInt(n).toString(16);

module.exports = {
  CHAIN_KEY, TIMEOUT_MS, READS, ChainUnreadable, seam, chain, rpc,
  chainId: () => rpc('eth_chainId', []).then((h) => Number(BigInt(h))),
  blockNumber: () => rpc('eth_blockNumber', []).then((h) => BigInt(h)),
  receipt: (tx) => rpc('eth_getTransactionReceipt', [tx]),
  transaction: (tx) => rpc('eth_getTransactionByHash', [tx]),
  code: (addr) => rpc('eth_getCode', [addr, 'latest']),
  call: (to, data) => rpc('eth_call', [{ to, data }, 'latest']),
  logs: (filter) => rpc('eth_getLogs', [filter]),
  block: (n) => rpc('eth_getBlockByNumber', [hexNum(n), false]),
  hexNum,
};
