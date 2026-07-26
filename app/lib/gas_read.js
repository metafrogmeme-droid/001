'use strict';
/**
 * Live per-chain gas read — public market facts for the Escape planner.
 *
 * The plan tells you the ORDER; this tells you what each chain's gas market
 * looks like RIGHT NOW, read over the same keyless RPC set the wallet mirror
 * verified. Honest by construction:
 *   - eth_gasPrice is a public fact; gwei is a market price, never advice.
 *   - A chain that cannot be read is OMITTED from the answer — a stale or
 *     invented number would ride into someone's exit decision.
 *   - The read is indicative: it is the node's current suggestion, not a
 *     quote for any specific transaction, and the UI must say so.
 *
 * Pure-ish: fetchImpl is injectable for tests; production uses global fetch
 * with a hard per-chain timeout so one slow RPC never stalls the panel.
 */

const { CHAINS } = require('./wallet');

const PER_CHAIN_TIMEOUT_MS = 3500;

async function readOne(chain, f) {
  const urls = [chain.rpcDefault, ...(chain.rpcFallbacks || [])].filter(Boolean);
  for (const url of urls) {
    try {
      const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), PER_CHAIN_TIMEOUT_MS) : null;
      const r = await f(url, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_gasPrice', params: [] }),
        ...(ctrl ? { signal: ctrl.signal } : {}),
      });
      if (timer) clearTimeout(timer);
      if (!r.ok) continue;
      const j = await r.json();
      const wei = j && j.result ? parseInt(j.result, 16) : NaN;
      if (!Number.isFinite(wei) || wei <= 0) continue;
      // gwei with sub-gwei precision — L2s live well below 1.
      return Math.round(wei / 1e9 * 1000) / 1000;
    } catch (e) { /* rotate to the next endpoint */ }
  }
  return null;
}

/**
 * → { chains: { <key>: { gwei, label } }, read_at, indicative: true }
 * Unreadable chains are absent, never zeroed or carried over.
 */
async function readGas(fetchImpl) {
  const f = fetchImpl || global.fetch;
  const evm = CHAINS.filter((c) => c.rpcDefault);
  const results = await Promise.all(evm.map((c) => readOne(c, f)));
  const chains = {};
  evm.forEach((c, i) => {
    if (results[i] != null) chains[c.key] = { gwei: results[i], label: c.label };
  });
  return { chains, read_at: new Date().toISOString(), indicative: true };
}

module.exports = { readGas, PER_CHAIN_TIMEOUT_MS };
