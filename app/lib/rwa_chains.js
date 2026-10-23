'use strict';
/**
 * Chains the RWA pre-check will read, and nothing else.
 *
 * One list. The page's <select>, GET /v1/rwa/chains and the reader all take
 * it from here. A second hand-written list is how a chain gets a button and
 * no reader.
 *
 * Every row is family `evm` and uses the same reader. Solana and Stellar are
 * not rows: they need their own readers (v0.1) and a family the dispatcher
 * does not have yet answers `no_reader` rather than borrowing this one.
 * Monad is not a row either. It was not verified as the same reader plus a
 * recorded fixture, and a chain id with no fixture is not added on a guess.
 *
 * RPC URLs are operator configuration. The env name is public; the value is
 * never stored in this repo and never copied onto a response. Several URLs,
 * separated by commas, are tried in order. The next URL is asked only when
 * this one could not be read. A revert is an answer and stops the walk.
 */

const CHAINS = [
  { id: 'ethereum', chain_id: 1, name: 'Ethereum', family: 'evm', rpc_env: 'RWA_RPC_URL_ETHEREUM' },
  { id: 'base', chain_id: 8453, name: 'Base', family: 'evm', rpc_env: 'RWA_RPC_URL_BASE' },
  { id: 'avalanche', chain_id: 43114, name: 'Avalanche C-Chain', family: 'evm', rpc_env: 'RWA_RPC_URL_AVALANCHE' },
  { id: 'bnb', chain_id: 56, name: 'BNB Chain', family: 'evm', rpc_env: 'RWA_RPC_URL_BNB' },
  { id: 'arbitrum', chain_id: 42161, name: 'Arbitrum One', family: 'evm', rpc_env: 'RWA_RPC_URL_ARBITRUM' },
  { id: 'polygon', chain_id: 137, name: 'Polygon', family: 'evm', rpc_env: 'RWA_RPC_URL_POLYGON' },
  { id: 'zksync', chain_id: 324, name: 'ZKsync Era', family: 'evm', rpc_env: 'RWA_RPC_URL_ZKSYNC' },
];

const BY_ID = new Map(CHAINS.map((c) => [c.id, c]));
const BY_NUM = new Map(CHAINS.map((c) => [c.chain_id, c]));

function chainByRef(ref) {
  if (ref == null) return null;
  if (typeof ref === 'number' && Number.isInteger(ref)) return BY_NUM.get(ref) || null;
  const text = String(ref).trim().toLowerCase();
  if (!text) return null;
  if (/^\d+$/.test(text)) return BY_NUM.get(Number(text)) || null;
  return BY_ID.get(text) || null;
}

/** URL strings for a chain, or [] when the variable is unset. Never logged. */
function rpcUrls(env, chain) {
  if (!chain || !chain.rpc_env) return [];
  const src = env || process.env;
  const raw = src[chain.rpc_env];
  if (raw == null) return [];
  return String(raw).split(',').map((s) => s.trim()).filter((s) => s.length > 0);
}

/** What a caller may see. The URL itself is not a field. */
function publicChain(chain, env) {
  return {
    id: chain.id,
    chain_id: chain.chain_id,
    name: chain.name,
    family: chain.family,
    rpc_configured: rpcUrls(env, chain).length > 0,
  };
}

module.exports = { CHAINS, chainByRef, rpcUrls, publicChain };
