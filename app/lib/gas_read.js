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

// Execution gas of a bridge-ish transaction, chosen LOW on purpose: the
// figure it produces must be a floor. Real bridge deposits usually burn
// 100k–300k gas, and bridge fees, relayer fees, L1 data fees and
// destination-chain gas all come on top — so "gas × this × native price"
// can understate the cost of moving an asset, never overstate it.
const XFER_GAS_UNITS = 100_000;

/**
 * Enrich a readGas() body with what a transaction on each chain costs in
 * dollars RIGHT NOW — both public market facts (gas price × native price).
 * Pure: same-shape body out; a chain whose native mark is missing keeps its
 * gwei and simply gains nothing (omitted, never invented). Never throws.
 */
function withXferCosts(body, tickers) {
  if (!body || !body.chains || !tickers) return body;
  for (const c of CHAINS) {
    const hit = body.chains[c.key];
    const tk = c.native && tickers[c.native.ticker];
    const usd = tk && Number(tk.price);
    if (!hit || !Number.isFinite(usd) || usd <= 0) continue;
    hit.native_usd = usd;
    // Floor cost of one bridge-ish transaction: gwei → ETH-units → dollars.
    hit.xfer_cost_usd = Math.round(hit.gwei * 1e-9 * XFER_GAS_UNITS * usd * 10000) / 10000;
  }
  return body;
}

// Injectable fetch for tests (the same pattern lib/tickers uses). Null
// restores the default; setting it also drops the cache so a test never
// reads a body some earlier test priced.
let gasFetchImpl = null;
function setGasFetcher(fn) { gasFetchImpl = fn || null; cached = null; }

/**
 * The shared 60s-cached, price-enriched gas read — one cache for every
 * consumer (the public /api/gas route and the get_gas MCP tool), so N
 * surfaces never multiply into N RPC sweeps. An EMPTY read is never cached:
 * that would freeze a transient outage into "no data" for a minute. The
 * native-price read is bounded (display path) — nothing here may wait on a
 * cold 10s ticker fetch.
 */
let cached = null;
async function readGasCached() {
  if (cached && Date.now() - cached.at < 60_000) return cached.body;
  const body = await readGas(gasFetchImpl || undefined);
  const { map } = await require('./tickers').getTickersWithin(2500);
  withXferCosts(body, map);
  if (Object.keys(body.chains).length) cached = { at: Date.now(), body };
  return body;
}

module.exports = {
  readGas, readGasCached, setGasFetcher, withXferCosts,
  PER_CHAIN_TIMEOUT_MS, XFER_GAS_UNITS,
};
