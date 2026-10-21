'use strict';
/**
 * On-chain anchoring for the daily seal roots — Provable Calls' missing leg,
 * on more than one chain.
 *
 * A mirrored root already proves no call can be back-inserted into a day
 * WITHOUT changing the root; what it cannot prove alone is WHEN the root
 * existed — for that you had to trust our database's timestamps. An anchor
 * transaction closes that gap: the day's root rides as calldata in a
 * zero-value transaction, and the BLOCK timestamp — a fact no one here
 * controls — becomes the independent upper bound on when every seal in that
 * day was minted.
 *
 * Non-custodial to the letter (same doctrine as the ERC-8257 plan): the
 * server never holds a key and never sends a transaction. buildAnchorPlan()
 * produces the exact transaction for a wallet's owner; verifyAnchor() READS
 * the chain through keyless RPCs and only ever records what it verified. On
 * an RPC failure it answers UNKNOWN — it never guesses either way.
 *
 * ONE PAYLOAD, EVERY CHAIN. The bytes are the same on Base and on the DogeOS
 * testnet (2026-10-19, the Vibe Code on DOGE entry): what differs is which
 * chain's block clock bounds the day, and a day may be anchored on each.
 * The chain registry below is the one reading of a chain's id, RPCs and
 * explorer; the routes, the pages and the DogeOS app read it from here
 * (`GET /api/roots/chains`) rather than carrying literals of their own.
 *
 * Payload format, self-describing and greppable on-chain:
 *   utf8("RCROOT1:" + day + ":" + root)   as the tx `data`, hex-encoded.
 */
const PREFIX = 'RCROOT1';
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ROOT_RE = /^[0-9a-f]{64}$/;
const TX_RE = /^0x[0-9a-f]{64}$/i;

// Same keyless Base endpoints the wallet mirror verified (lib/wallet.js).
const BASE_RPCS = ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'];
// DogeOS's own public testnet endpoint (docs.dogeos.com, read 2026-10-19).
const DOGEOS_TESTNET_RPCS = ['https://rpc.testnet.dogeos.com/'];

/**
 * A comma list in the environment replaces a chain's default RPCs, for the
 * SERVER's reads only. It may carry a key (a provider's URL often does), so
 * it is never handed to a page or a wallet: those get `public_rpcs`, the
 * keyless defaults below.
 */
function rpcList(envKey, defaults, env) {
  const raw = String((env || process.env)[envKey] || '').trim();
  const urls = raw ? raw.split(',').map((s) => s.trim()).filter((s) => /^https?:\/\//.test(s)) : [];
  return urls.length ? urls : defaults.slice();
}

/**
 * The chains a day's root may be anchored on. `key` is what the API and the
 * database call the chain; `chain_id` is what a wallet is asked to switch to;
 * `native` is what `wallet_addEthereumChain` needs to describe it.
 */
function chains(env) {
  return {
    base: {
      key: 'base', chain_id: 8453, name: 'Base', testnet: false,
      rpcs: rpcList('WEB3_RPC_URL_BASE_ANCHOR', BASE_RPCS, env),
      public_rpcs: BASE_RPCS.slice(),
      explorer_tx: 'https://basescan.org/tx/',
      native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
      // QuickNode's name for this chain, through Flovia (lib/flovia.js): a
      // second reader of a transaction when every RPC above fails to answer.
      // A chain without one (the DogeOS testnet) has no fallback.
      quicknode: 'base',
    },
    'dogeos-testnet': {
      key: 'dogeos-testnet', chain_id: 6281971, name: 'DogeOS testnet', testnet: true,
      rpcs: rpcList('DOGEOS_TESTNET_RPC_URLS', DOGEOS_TESTNET_RPCS, env),
      public_rpcs: DOGEOS_TESTNET_RPCS.slice(),
      explorer_tx: 'https://dogeos-testnet.l2scan.co/tx/',
      faucet: 'https://faucet.testnet.dogeos.com',
      native: { name: 'Dogecoin', symbol: 'DOGE', decimals: 18 },
    },
  };
}
const DEFAULT_CHAIN = 'base';

/** The registry entry for `key`, or null for a chain this build does not know. */
function chainInfo(key, env) {
  const all = chains(env);
  return Object.prototype.hasOwnProperty.call(all, String(key || '')) ? all[String(key)] : null;
}

/**
 * What a page or an app may be told about every chain. The RPCs are the
 * keyless defaults, never the environment's override: until 21 October 2026
 * this returned `c.rpcs`, so a provider URL set in DOGEOS_TESTNET_RPC_URLS or
 * WEB3_RPC_URL_BASE_ANCHOR, key and all, was served on /api/roots/chains and
 * handed to every wallet asked to add the chain.
 */
function publicChains(env) {
  return Object.values(chains(env)).map((c) => ({
    key: c.key, chain_id: c.chain_id, name: c.name, testnet: c.testnet,
    rpcs: c.public_rpcs.slice(), explorer_tx: c.explorer_tx, native: c.native,
    ...(c.faucet ? { faucet: c.faucet } : {}),
  }));
}

function payloadFor(day, root) {
  return '0x' + Buffer.from(`${PREFIX}:${day}:${root}`, 'utf8').toString('hex');
}

/**
 * The transaction a wallet's owner sends: a zero-value self-send on `chain`
 * whose data is the tagged root. DRY RUN ONLY. Null for a malformed day or
 * root, or a chain this build does not know.
 */
function buildAnchorPlan(day, root, chainKey = DEFAULT_CHAIN) {
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) return null;
  const chain = chainInfo(chainKey);
  if (!chain) return null;
  const data = payloadFor(day, root);
  return {
    dry_run: true,
    chain_id: chain.chain_id,
    chain: chain.name,
    chain_key: chain.key,
    to: 'YOUR OWN ADDRESS (a zero-value self-send — no contract needed)',
    value: '0',
    data,
    day, root,
    instructions: [
      `Send a 0-value transaction FROM your own wallet TO the same address on ${chain.name}, `
        + 'with `data` set to the data field above. Any wallet that allows hex data works.',
      `Or with foundry: cast send <YOUR_ADDRESS> --value 0 --rpc-url ${chain.public_rpcs[0]} `
        + `--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER> ${data}`,
      'Then submit the transaction hash back via POST /api/roots/anchor '
        + `{ day, tx_hash, chain: "${chain.key}" } — the server verifies the calldata `
        + 'against the chain before recording anything.',
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

/**
 * Read the chain and answer: does this transaction anchor this day's root?
 * → { status: 'verified', block_time, from }    the calldata matches
 * → { status: 'mismatch', reason }              the tx exists but does not anchor this root
 * → { status: 'unknown', reason }               the chain could not be read — NOT a verdict
 *
 * `fetchImpl` is injectable for tests; production uses global fetch against
 * the chain's keyless RPCs with one rotation, like the wallet mirror.
 */
async function verifyAnchor(txHash, day, root, fetchImpl, chainKey = DEFAULT_CHAIN) {
  if (!TX_RE.test(String(txHash))) return { status: 'mismatch', reason: 'malformed transaction hash' };
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) {
    return { status: 'mismatch', reason: 'malformed day or root' };
  }
  const chain = chainInfo(chainKey);
  if (!chain) return { status: 'mismatch', reason: 'unknown chain' };
  const f = fetchImpl || global.fetch;
  const call = async (url, method, params) => {
    const r = await f(url, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    });
    if (!r.ok) throw new Error(`rpc http ${r.status}`);
    const j = await r.json();
    if (j.error) throw new Error(`rpc ${j.error.code}: ${j.error.message}`);
    return j.result;
  };
  let lastErr = null;
  for (const url of chain.rpcs) {
    try {
      const tx = await call(url, 'eth_getTransactionByHash', [txHash]);
      if (!tx) return { status: 'mismatch', reason: `no such transaction on ${chain.name}` };
      if (!tx.blockHash) return { status: 'unknown', reason: 'transaction not yet mined' };
      const expected = payloadFor(day, root).toLowerCase();
      const data = String(tx.input || tx.data || '').toLowerCase();
      if (data !== expected) {
        // includes() would accept a payload buried in unrelated calldata —
        // exact equality is the claim being made.
        return { status: 'mismatch', reason: 'calldata does not equal the tagged root payload' };
      }
      const block = await call(url, 'eth_getBlockByHash', [tx.blockHash, false]);
      if (!block || !block.timestamp) return { status: 'unknown', reason: 'block unreadable' };
      return {
        status: 'verified',
        block_time: new Date(parseInt(block.timestamp, 16) * 1000).toISOString(),
        from: String(tx.from || ''),
      };
    } catch (e) { lastErr = e; }
  }
  // Every RPC failed to ANSWER (a "no such transaction" above is an answer
  // and returns before this). A chain QuickNode serves gets one more reader,
  // through Flovia, so a day's anchor is not unverifiable because two public
  // endpoints were down. The verdict says which reader gave it.
  if (chain.quicknode) {
    const second = await verifyThroughQuickNode(txHash, day, root, chain);
    if (second) return second;
  }
  return { status: 'unknown', reason: lastErr ? String(lastErr.message || lastErr).slice(0, 120) : 'rpc unreachable' };
}

/**
 * The same verification over QuickNode's read of the chain (two reads: the
 * transaction, then its block's header for the time). Null when Flovia is
 * unconfigured or refused, or answered something this cannot read, so the
 * caller's own "unknown" stands with its own reason; a verdict otherwise,
 * marked `source: 'quicknode'`.
 */
async function verifyThroughQuickNode(txHash, day, root, chain) {
  let flovia;
  try { flovia = require('./flovia'); } catch (e) { return null; }
  if (!flovia.configured()) return null;
  const ask = (method, params, block) => flovia.call('flovia_quicknode', {
    chain: chain.quicknode, method, params, ...(block ? { block } : {}),
    request_text: `RUNECLAW seal-root anchor check: ${method} on ${chain.name} for ${txHash}`,
    purpose: 'Verifying a day\'s seal-root anchor when the public RPCs do not answer; the verdict is shown on the roots page.',
  });
  const txR = await ask('eth_getTransactionByHash', { hash: txHash });
  if (!txR.read) return null;
  const tx = txR.data && typeof txR.data === 'object' ? (txR.data.result !== undefined ? txR.data.result : txR.data) : null;
  if (tx === null) return { status: 'mismatch', reason: `no such transaction on ${chain.name}`, source: 'quicknode' };
  if (!tx || typeof tx !== 'object') return null;
  if (!tx.blockHash && !tx.blockNumber) return { status: 'unknown', reason: 'transaction not yet mined', source: 'quicknode' };
  const expected = payloadFor(day, root).toLowerCase();
  const data = String(tx.input || tx.data || '').toLowerCase();
  if (data !== expected) return { status: 'mismatch', reason: 'calldata does not equal the tagged root payload', source: 'quicknode' };
  const num = typeof tx.blockNumber === 'string' && /^0x/i.test(tx.blockNumber) ? parseInt(tx.blockNumber, 16) : Number(tx.blockNumber);
  if (!Number.isFinite(num)) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  const blockR = await ask('eth_getBlockByNumber', { header_only: true }, num);
  if (!blockR.read) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  const block = blockR.data && typeof blockR.data === 'object' ? (blockR.data.result !== undefined ? blockR.data.result : blockR.data) : null;
  const ts = block && (block.timestamp !== undefined ? block.timestamp : block.time);
  const secs = typeof ts === 'string' && /^0x/i.test(ts) ? parseInt(ts, 16) : Number(ts);
  if (!Number.isFinite(secs) || secs <= 0) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  return {
    status: 'verified', source: 'quicknode',
    block_time: new Date(secs * 1000).toISOString(),
    from: String(tx.from || ''),
  };
}

module.exports = {
  buildAnchorPlan, verifyAnchor, verifyThroughQuickNode, payloadFor, PREFIX, BASE_RPCS, DOGEOS_TESTNET_RPCS,
  chains, chainInfo, publicChains, DEFAULT_CHAIN,
};
