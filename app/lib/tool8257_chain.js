'use strict';
/**
 * Did the ERC-8257 registration actually land on-chain? — ask the chain.
 *
 * WHAT WAS MISSING
 *
 * `tool8257.registrationCheck()` is three-valued and careful, and it compares
 * our computed manifest hash against `REGISTERED_MANIFEST_HASH` — an
 * environment variable WE set. Both sides of that comparison are ours. It
 * proves the operator typed the hash they meant to type; it cannot prove a
 * transaction was ever sent, that it reached the registry, or that it carried
 * this manifest.
 *
 * That is the same shape the daily seal roots had until 2026-08-25: a claim
 * about a chain, substantiated from our own table. There the fix was
 * `verifyAnchor`; this is its sibling, and it is deliberately built the same
 * way so the two cannot drift in doctrine.
 *
 * WHY THIS VERIFIES A TRANSACTION AND NOT THE REGISTRY STATE
 *
 * The obvious design is to read the ToolRegistry contract and ask whether a
 * record exists for our creator address. That needs the registry's READ ABI,
 * and ERC-8257 is a draft: this repo knows only the write signature it encodes
 * itself, `registerTool(string,bytes32,address)`. Inventing a getter signature
 * and reporting whatever came back would be the SIWF verifier mistake again —
 * a plausible call to an endpoint nobody read the spec for, wrong in a way the
 * surface cannot distinguish from right.
 *
 * So this verifies the transaction instead, using only `eth_getTransactionByHash`
 * — the same method root_anchor.js already relies on, and zero new ABI
 * knowledge:
 *
 *   - it was mined (a block hash exists)
 *   - it was sent TO the canonical ToolRegistry
 *   - its calldata EQUALS the exact registerTool payload we computed
 *
 * That is strictly weaker than reading registry state: it proves the
 * registration was submitted correctly, not that it survived (a later
 * re-registration could supersede it). The docstring says so rather than the
 * payload implying otherwise, and `reason` carries the distinction to callers.
 *
 * → { status: 'verified', block_time, from }   the calldata matches, at the registry
 * → { status: 'mismatch', reason }             it exists and is not this registration
 * → { status: 'unknown',  reason }             the chain could not be read — NOT a verdict
 */

const TX_RE = /^0x[0-9a-f]{64}$/i;
const HEX_RE = /^0x[0-9a-f]*$/i;

// Keyless public endpoints, same set and same order as root_anchor.js.
const RPCS = {
  8453: ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'],
  1: ['https://ethereum-rpc.publicnode.com', 'https://eth.llamarpc.com'],
};

function eq(a, b) {
  return String(a || '').toLowerCase() === String(b || '').toLowerCase();
}

async function rpcCall(fetchImpl, url, method, params) {
  const res = await fetchImpl(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  });
  if (!res || !res.ok) throw new Error(`rpc ${method} http ${res ? res.status : '?'}`);
  const body = await res.json();
  if (body && body.error) throw new Error(String(body.error.message || 'rpc error'));
  return body ? body.result : null;
}

/**
 * @param txHash    the registerTool transaction
 * @param expected  { registry, calldata, creator } — from buildRegistrationPlan
 * @param chainId   8453 (Base) or 1 (Ethereum)
 * @param fetchImpl injectable for tests; defaults to global fetch
 *
 * `creator` MATTERS AND WAS MISSING FROM THE FIRST VERSION. The manifest
 * declares `creatorAddress`, and the registry records `msg.sender` as the
 * creator of the record. Send the registration from a different wallet and
 * those two disagree: the on-chain record says one address created the tool
 * while the manifest it points at claims another. The first version of this
 * function checked the destination and the calldata and never looked at the
 * sender — so it would have answered `verified` for exactly that.
 *
 * That is the same asymmetry this file's own commit message pointed out in
 * `bot/proofofpnl/anchor.py` (which checks the sender and not the
 * destination), reproduced here within the hour, in the opposite direction.
 * Optional rather than required: `creator` is unset in some configurations,
 * and refusing to verify a good registration because we could not resolve our
 * OWN expectation would be blaming the chain for a gap on this side.
 */
async function verifyRegistration(txHash, expected, chainId, fetchImpl) {
  const f = fetchImpl || (typeof fetch === 'function' ? fetch : null);
  if (!f) return { status: 'unknown', reason: 'no fetch implementation available' };

  if (!TX_RE.test(String(txHash || ''))) {
    return { status: 'mismatch', reason: 'malformed transaction hash' };
  }
  // A malformed EXPECTATION is not a statement about the chain. Reporting it
  // as a mismatch would blame a transaction for our own broken plan — the
  // failure the roots verifier separates with its `unknown` code, for exactly
  // this reason.
  const registry = expected && expected.registry;
  const calldata = expected && expected.calldata;
  if (!registry || !calldata || !HEX_RE.test(String(calldata))) {
    return { status: 'unknown', reason: 'no usable registration plan to compare against' };
  }

  const urls = RPCS[Number(chainId)];
  if (!urls) return { status: 'unknown', reason: `no RPC configured for chain ${chainId}` };

  let lastErr = null;
  for (const url of urls) {
    try {
      const tx = await rpcCall(f, url, 'eth_getTransactionByHash', [txHash]);
      if (!tx) return { status: 'mismatch', reason: 'no such transaction on this chain' };
      if (!tx.blockHash) return { status: 'unknown', reason: 'transaction not yet mined' };
      if (!eq(tx.to, registry)) {
        return { status: 'mismatch', reason: `sent to ${tx.to}, not the ToolRegistry` };
      }
      // EQUALS, not "contains". A payload buried inside a larger call is a
      // different transaction doing something else that happens to mention
      // ours — the same distinction root_anchor.js draws for the anchor.
      if (!eq(tx.input, calldata)) {
        return { status: 'mismatch', reason: 'calldata does not equal this registration' };
      }
      // The registry records msg.sender as the creator. If that is not the
      // address the manifest names, the on-chain record and the document it
      // points at disagree about who made it — a registration that verifies
      // byte-for-byte and still misattributes itself.
      const creator = expected.creator;
      if (creator && !eq(tx.from, creator)) {
        return {
          status: 'mismatch',
          reason: `sent from ${tx.from || 'unknown'}, not the manifest's creatorAddress`,
        };
      }
      const block = await rpcCall(f, url, 'eth_getBlockByHash', [tx.blockHash, false]);
      if (!block || !block.timestamp) return { status: 'unknown', reason: 'block unreadable' };
      return {
        status: 'verified',
        block_time: new Date(Number(block.timestamp) * 1000).toISOString(),
        from: tx.from || null,
        reason: 'submission verified; this does not prove the record was not later superseded',
      };
    } catch (e) {
      lastErr = e;
    }
  }
  return {
    status: 'unknown',
    reason: lastErr ? String(lastErr.message || lastErr).slice(0, 120) : 'rpc unreachable',
  };
}

/** The recorded registration transaction, or null. Operator-set, like the hash. */
function registeredTx() {
  const t = String(process.env.REGISTERED_TOOL_TX || '').trim().toLowerCase();
  return TX_RE.test(t) ? t : null;
}

/** Which chain the recorded transaction is on. Defaults to Base, the recommended chain. */
function registeredChainId() {
  const n = Number(String(process.env.REGISTERED_TOOL_CHAIN_ID || '8453').trim());
  return RPCS[n] ? n : 8453;
}

module.exports = {
  verifyRegistration,
  registeredTx,
  registeredChainId,
  RPCS,
};
