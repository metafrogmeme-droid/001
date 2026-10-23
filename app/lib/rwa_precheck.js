'use strict';
/**
 * RWA pre-check. One dispatcher, one EVM reader.
 *
 * `readerFor` is the seam a later family plugs into. Solana and Stellar are
 * not registered: asking for them returns null, and the check answers
 * `no_reader` instead of running the EVM reader against a chain it cannot
 * read. Adding a reader is a new key in READERS, not a second stack.
 */

const { chainByRef, rpcUrls, publicChain, CHAINS } = require('./rwa_chains');
const { httpRpc, readEvm } = require('./rwa_evm');
const { DISCLAIMER } = require('./rwa_words');

const READERS = {
  evm: readEvm,
};

function readerFor(family) {
  if (typeof family !== 'string') return null;
  return READERS[family] || null;
}

function normalizeAddress(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(text)) return null;
  return text.toLowerCase();
}

/** The bytes the seal commits to. Key order is part of the commitment. */
function canonicalInput(chain, asset, account) {
  return JSON.stringify({
    chain: String(chain),
    asset: String(asset).toLowerCase(),
    account: account ? String(account).toLowerCase() : null,
  });
}

function fail(reason, chain, env, extra) {
  const more = extra || {};
  return {
    read: false,
    reason,
    chain: chain ? publicChain(chain, env) : null,
    asset: more.asset || null,
    account: more.account || null,
    disclaimer: DISCLAIMER,
  };
}

/**
 * @param opts.chain   id ('base') or chain id (8453)
 * @param opts.asset   0x address
 * @param opts.account optional 0x address
 * @param opts.env     process environment (RPC URLs). Defaults to process.env.
 * @param opts.rpc     injected transport `(method, params) => {ok, result|reason}`
 * @param opts.fetchImpl
 */
async function precheck(opts) {
  const o = opts || {};
  const env = o.env || process.env;
  const chain = o.chainRecord || chainByRef(o.chain);
  if (!chain) return fail('unsupported_chain', null, env);
  const asset = normalizeAddress(o.asset);
  if (!asset) return fail('invalid_asset', chain, env);
  let account = null;
  if (o.account != null && String(o.account).trim() !== '') {
    account = normalizeAddress(o.account);
    if (!account) return fail('invalid_account', chain, env);
  }
  const known = { asset, account };
  const reader = readerFor(chain.family);
  if (!reader) return fail('no_reader', chain, env, known);
  let rpc = o.rpc || null;
  if (!rpc) {
    const urls = rpcUrls(env, chain);
    if (!urls.length) return fail('rpc_unconfigured', chain, env, known);
    rpc = httpRpc(urls, o.fetchImpl, o.timeoutMs);
  }
  try {
    const reading = await reader({ chain, asset, account, rpc });
    reading.disclaimer = DISCLAIMER;
    if (!reading.asset) reading.asset = asset;
    if (reading.account == null) reading.account = account;
    if (reading.chain) reading.chain.rpc_configured = rpcUrls(env, chain).length > 0;
    return reading;
  } catch (err) {
    console.error('[rwa] read failed:', err && err.constructor ? err.constructor.name : 'Error');
    return fail('unreadable', chain, env, known);
  }
}

/** The part of a reading that may be sealed. Addresses stay out. */
function project(reading) {
  if (!reading || reading.read !== true) {
    return {
      chain_id: reading && reading.chain && Number.isInteger(reading.chain.chain_id)
        ? reading.chain.chain_id : null,
      block: null,
      flags: [],
      unreadable: ['read'],
    };
  }
  return {
    chain_id: reading.chain.chain_id,
    block: reading.block && reading.block.number != null ? String(reading.block.number) : null,
    flags: (reading.flags || []).map((f) => ({ id: String(f.id), state: String(f.state) })),
    unreadable: (reading.unreadable || []).map(String),
  };
}

module.exports = {
  precheck, readerFor, canonicalInput, project, normalizeAddress,
  READERS, CHAINS, DISCLAIMER,
};
