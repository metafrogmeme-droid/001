'use strict';
/**
 * Simulate one transaction on Base, for the account that would send it.
 *
 * The reading is `public/js/txsim-model.js`; this is the transport. It asks
 * the chain's own keyless public endpoints (the set `lib/nft.js` CHAINS holds)
 * in order, each under a time budget, and returns the first answer that reads.
 *
 * Nothing here signs or sends a transaction: `eth_simulateV1` executes the
 * call against a copy of the latest state and throws the copy away. Nothing
 * is stored. The endpoint sees the transaction and the sending address,
 * which is what any wallet's own RPC sees before it signs.
 *
 * → { ok: true, chain_id, ... }   the model's reading
 * → { ok: false, reason }         'invalid' | 'unsupported_chain' | 'rejected' | 'unreachable'
 *
 * 'rejected' means every endpoint answered and refused the request (a
 * JSON-RPC error); 'unreachable' means none answered. Neither is "no changes".
 */

const SIM = require('../public/js/txsim-model.js');
const { CHAINS } = require('./nft');

const TIMEOUT_MS = 6000;

async function simulate(tx, opts) {
  const o = opts || {};
  const chainId = o.chainId == null ? 8453 : Number(o.chainId);
  const chain = CHAINS[chainId];
  if (!chain) return { ok: false, reason: 'unsupported_chain' };
  const body = SIM.request(tx);
  if (!body) return { ok: false, reason: 'invalid' };
  const f = o.fetchImpl || (typeof fetch === 'function' ? fetch : null);
  if (!f) return { ok: false, reason: 'unreachable' };

  let refused = null;
  for (const url of chain.rpcs) {
    let answer;
    try {
      const res = await f(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(o.timeoutMs || TIMEOUT_MS),
      });
      if (!res || !res.ok) continue;
      answer = await res.json();
    } catch (err) {
      continue;
    }
    const r = SIM.read(answer, body.params[0].blockStateCalls[0].calls[0].from);
    if (r.ok) return { chain_id: chainId, ...r };
    if (r.reason === 'rpc_error' && !refused) refused = r;
  }
  return refused
    ? { ok: false, reason: 'rejected', code: refused.code }
    : { ok: false, reason: 'unreachable' };
}

module.exports = { simulate, TIMEOUT_MS };
