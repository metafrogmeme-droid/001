'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Quantum-canary circuit breaker. A tripped canary only tightens: this
 * process latches and does not clear. The trip is a flag on the reading
 * and, on chain, SaRVerifier.trip. This module does not send that
 * transaction and does not hold a key.
 *
 * The canary is an address with no known key. A nonce above the recorded
 * baseline means that address signed. Unconfigured is not "safe", and a
 * failed read does not trip and does not clear a trip already latched.
 */

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

let latched = false;

function resetForTests() {
  latched = false;
}

function latch() {
  latched = true;
  return { tripped: true, reason: null };
}

async function readCanary(deps) {
  if (latched) return { tripped: true, reason: null };
  const d = deps || {};
  const env = d.env || process.env;
  if (String(env.SAR_ORACLE_CANARY_TRIPPED == null ? '' : env.SAR_ORACLE_CANARY_TRIPPED).trim() === '1') {
    return latch();
  }
  const addr = env.SAR_ORACLE_CANARY_ADDRESS;
  const rpc = env.SAR_ORACLE_CANARY_RPC;
  if (addr == null || String(addr).trim() === '' || rpc == null || String(rpc).trim() === '') {
    return { tripped: false, reason: 'not_configured' };
  }
  if (!ADDR_RE.test(String(addr).trim())) return { tripped: null, reason: 'unreadable' };
  let base = 0n;
  const baseRaw = env.SAR_ORACLE_CANARY_BASE_NONCE;
  if (!(baseRaw == null || String(baseRaw).trim() === '')) {
    if (!/^\d+$/.test(String(baseRaw).trim())) return { tripped: null, reason: 'unreadable' };
    base = BigInt(String(baseRaw).trim());
  }
  const fetchImpl = d.fetch || globalThis.fetch;
  try {
    const res = await fetchImpl(String(rpc).trim(), {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'eth_getTransactionCount',
        params: [String(addr).trim(), 'latest'],
      }),
    });
    if (!res.ok) return { tripped: null, reason: 'unreadable' };
    const body = await res.json();
    if (!body || typeof body.result !== 'string' || !/^0x[0-9a-fA-F]+$/.test(body.result)) {
      return { tripped: null, reason: 'unreadable' };
    }
    const nonce = BigInt(body.result);
    if (nonce > base) return latch();
    return { tripped: false, reason: null, nonce: nonce.toString() };
  } catch {
    return { tripped: null, reason: 'unreadable' };
  }
}

module.exports = { readCanary, resetForTests, latch };
