'use strict';
/**
 * Is Memeclaw on, and where? The one answer the routes, the page and the
 * indexer read.
 *
 * ON ONLY WHEN CHECKED. MEMECLAW_FACTORY must name an address whose code is,
 * byte for byte, the factory this site carries, on a chain the registry calls
 * a testnet (lib/memeclaw.checkFactory). A verified answer is kept ten
 * minutes, a mismatch one, and an unreadable chain is asked again next time:
 * "unknown" is never "on" and never cached as "off".
 */
const { publicChains } = require('./root_anchor');
const mc = require('./memeclaw');
const curve = require('./memeclaw_curve');
const mchain = require('./memeclaw_chain');

const checks = new Map(); // address -> { at, verdict }

async function factoryVerdict(address, checkFactory) {
  const hit = checks.get(address);
  const ttl = hit && hit.verdict.status === 'verified' ? 10 * 60000 : 60000;
  if (hit && hit.verdict.status !== 'unknown' && Date.now() - hit.at < ttl) return hit.verdict;
  const verdict = await (checkFactory || mc.checkFactory)(address);
  checks.set(address, { at: Date.now(), verdict });
  return verdict;
}

/**
 * { enabled, reason, chain, factory?, factory_block?, factory_check?, curve, limits }.
 * `deps`: { env, checkFactory } for tests.
 */
async function config(deps = {}) {
  const env = deps.env || process.env;
  const chain = publicChains().find((c) => c.key === mchain.CHAIN_KEY) || null;
  const base = {
    chain,
    curve: { supply: curve.SUPPLY.toString(), virtual_doge: curve.VIRTUAL_DOGE.toString(),
      virtual_tokens: curve.VIRTUAL_TOKENS.toString(), fee_bps: 0 },
    limits: { name_bytes: mc.MAX_NAME_BYTES, symbol_bytes: mc.MAX_SYMBOL_BYTES,
      description: mc.MAX_DESCRIPTION, slippage_bps_max: mc.MAX_SLIPPAGE_BPS,
      slippage_bps_default: mc.DEFAULT_SLIPPAGE_BPS, plan_seconds: mc.PLAN_TTL_S },
  };
  if (!chain || chain.testnet !== true) return { ...base, enabled: false, reason: 'no_testnet' };
  const raw = mc.lower(env.MEMECLAW_FACTORY || '');
  if (!raw) return { ...base, enabled: false, reason: 'not_deployed' };
  if (!mc.ADDR_RE.test(raw)) return { ...base, enabled: false, reason: 'factory_misconfigured' };
  const blockRaw = String(env.MEMECLAW_FACTORY_BLOCK || '').trim();
  const factoryBlock = /^\d{1,12}$/.test(blockRaw) ? Number(blockRaw) : null;
  const v = await factoryVerdict(raw, deps.checkFactory);
  return { ...base, factory: raw, factory_block: factoryBlock, enabled: v.status === 'verified',
    reason: v.status === 'verified' ? null : `factory_${v.status}`, factory_check: v };
}

module.exports = { config, _resetChecks: () => checks.clear() };
