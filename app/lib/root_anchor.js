'use strict';
/**
 * On-chain anchoring for the daily seal roots — Provable Calls' missing leg.
 *
 * A mirrored root already proves no call can be back-inserted into a day
 * WITHOUT changing the root; what it cannot prove alone is WHEN the root
 * existed — for that you had to trust our database's timestamps. An anchor
 * transaction on Base closes that gap: the day's root rides as calldata in a
 * zero-value transaction, and the BLOCK timestamp — a fact no one here
 * controls — becomes the independent upper bound on when every seal in that
 * day was minted.
 *
 * Non-custodial to the letter (same doctrine as the ERC-8257 plan): the
 * server never holds a key and never sends a transaction. buildAnchorPlan()
 * produces the exact transaction for the operator's own wallet; verifyAnchor()
 * READS the chain through the same keyless RPC set the wallet mirror uses and
 * only ever records what it verified. On an RPC failure it answers UNKNOWN —
 * it never guesses either way.
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

function payloadFor(day, root) {
  return '0x' + Buffer.from(`${PREFIX}:${day}:${root}`, 'utf8').toString('hex');
}

/**
 * The transaction the operator sends from their own wallet: a zero-value
 * self-send on Base whose data is the tagged root. DRY RUN ONLY.
 */
function buildAnchorPlan(day, root) {
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) return null;
  const data = payloadFor(day, root);
  return {
    dry_run: true,
    chain_id: 8453,
    chain: 'Base',
    to: 'YOUR OWN ADDRESS (a zero-value self-send — no contract needed)',
    value: '0',
    data,
    day, root,
    instructions: [
      'Send a 0-value transaction FROM your own wallet TO the same address on '
        + 'Base, with `data` set to the data field above. Any wallet that '
        + 'allows hex data works.',
      `Or with foundry: cast send <YOUR_ADDRESS> --value 0 --rpc-url ${BASE_RPCS[0]} `
        + `--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER> ${data}`,
      'Then submit the transaction hash back via POST /api/roots/anchor — the '
        + 'server verifies the calldata against the chain before recording '
        + 'anything.',
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
 * the keyless Base RPCs with one rotation, like the wallet mirror.
 */
async function verifyAnchor(txHash, day, root, fetchImpl) {
  if (!TX_RE.test(String(txHash))) return { status: 'mismatch', reason: 'malformed transaction hash' };
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) {
    return { status: 'mismatch', reason: 'malformed day or root' };
  }
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
  for (const url of BASE_RPCS) {
    try {
      const tx = await call(url, 'eth_getTransactionByHash', [txHash]);
      if (!tx) return { status: 'mismatch', reason: 'no such transaction on Base' };
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
  return { status: 'unknown', reason: lastErr ? String(lastErr.message || lastErr).slice(0, 120) : 'rpc unreachable' };
}

module.exports = { buildAnchorPlan, verifyAnchor, payloadFor, PREFIX, BASE_RPCS };
