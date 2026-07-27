'use strict';
/**
 * Allowance X-ray — STRICTLY READ-ONLY. The after-the-fact half of the
 * firewall: the transaction X-ray warns before a signature; this reads what
 * a wallet has ALREADY granted. For each curated token on a chain it asks
 * `allowance(owner, spender)` against a short registry of well-known
 * spenders, and for every live grant it writes a REVOKE PLAN — the exact
 * approve(spender, 0) calldata the owner can send from their own wallet.
 * The server never signs it, never sends it, never holds a key.
 *
 * Honesty is the design:
 * - BOUNDED by construction and said out loud: only curated tokens ×
 *   registry spenders are checked. Approvals to anything else are NOT
 *   scanned — a clean result here is never a guarantee.
 * - Every spender is a deterministic same-address deployment VERIFIED on
 *   each listed chain before inclusion (eth_getCode, 2026-07-27). The
 *   Uniswap router is NOT deterministic across chains — the address hosts
 *   DIFFERENT bytecode on Base — so it is chain-scoped explicitly rather
 *   than trusted by code presence.
 * - A pair that cannot be read is counted and reported as unreadable,
 *   never shown as zero. UNLIMITED uses the same 2^128 threshold as the
 *   transaction X-ray, so both halves of the firewall agree.
 */

const { ethers } = require('ethers');
const { activeChains } = require('./wallet');

// Deterministic-deploy spenders (same address, same bytecode, cross-chain),
// except where `chains` scopes them. Labels are the protocols' own names.
const SPENDERS = [
  { label: 'Permit2 (Uniswap)', address: '0x000000000022D473030F116dDEE9F6B43aC78BA3', chains: null },
  { label: '1inch v5 router', address: '0x1111111254EEB25477B68fb85Ed929f73A960582', chains: null },
  { label: '0x Exchange Proxy', address: '0xDef1C0ded9bec7F1a1670819833240f027b25EfF', chains: null },
  { label: 'Seaport 1.6 (OpenSea)', address: '0x0000000000000068F116a894984e2DB1123eB395', chains: null },
  // Bytecode verified identical on these four only; the same address on
  // Base hosts a different contract, BNB/Avalanche have none.
  { label: 'Uniswap SwapRouter02', address: '0x68b3465833fb72A70ecDF485E0e4C7bd8665Fc45',
    chains: ['ethereum', 'arbitrum', 'optimism', 'polygon'] },
];

// Same threshold the transaction X-ray uses — the two halves must agree.
const UNLIMITED_MIN = 2n ** 128n;

const IFACE = new ethers.Interface([
  'function allowance(address owner, address spender) view returns (uint256)',
  'function approve(address spender, uint256 amount) returns (bool)',
]);

// ── eth_call with per-chain URL rotation; test seam replaces the whole thing.
async function defaultEthCall(chain, to, data) {
  const urls = [chain.rpcDefault, ...(chain.rpcFallbacks || [])];
  let lastErr = null;
  for (const url of urls) {
    try {
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), 4000);
      const r = await fetch(url, {
        method: 'POST', signal: ac.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to, data }, 'latest'] }),
      }).finally(() => clearTimeout(t));
      const j = await r.json();
      if (j && typeof j.result === 'string' && j.result.startsWith('0x') && j.result.length >= 66) return j.result;
      lastErr = new Error((j && j.error && j.error.message) || 'empty result');
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error('unreachable');
}
let ethCall = defaultEthCall;
function setEthCaller(fn) { ethCall = fn || defaultEthCall; }

function spendersFor(chainKey) {
  return SPENDERS.filter((s) => !s.chains || s.chains.includes(chainKey));
}

/**
 * Read one chain's allowance grid for `owner`.
 * @returns { chain, label, spenders_checked, findings, zero_pairs,
 *            unreadable_pairs, note } — never throws.
 */
async function readAllowances(owner, chainKey) {
  const chain = activeChains().find((c) => c.key === String(chainKey || '').toLowerCase());
  if (!chain) return { error: 'unknown chain' };
  if (!/^0x[0-9a-fA-F]{40}$/.test(String(owner || ''))) return { error: 'bad address' };

  const spenders = spendersFor(chain.key);
  const findings = [];
  let zero = 0, unreadable = 0;
  for (const t of chain.tokens) {
    for (const s of spenders) {
      try {
        const data = IFACE.encodeFunctionData('allowance', [owner, s.address]);
        const out = await ethCall(chain, t.address, data);
        const amount = ethers.toBigInt(out);
        if (amount === 0n) { zero++; continue; }
        findings.push({
          token: t.symbol,
          token_address: t.address,
          spender: s.address,
          spender_label: s.label,
          // Raw units, honest — the UI may also show decimals-scaled, but
          // the wire value is exactly what the chain said.
          allowance_raw: amount.toString(),
          decimals: t.decimals,
          unlimited: amount >= UNLIMITED_MIN,
          // The revoke plan: approve(spender, 0) on the TOKEN, sent by the
          // OWNER from their own wallet. The server only writes it down.
          revoke_plan: {
            to: t.address,
            data: IFACE.encodeFunctionData('approve', [s.address, 0n]),
            value: '0x0',
            chain_id: chain.chainId,
          },
        });
      } catch (e) { unreadable++; }
    }
  }
  return {
    read_only: true,
    chain: chain.key,
    label: chain.label,
    spenders_checked: spenders.map((s) => s.label),
    findings,
    zero_pairs: zero,
    unreadable_pairs: unreadable,
    note: 'A bounded check: curated tokens against well-known spenders only. '
      + 'Approvals to contracts outside this registry are NOT scanned — a '
      + 'clean result here is not a guarantee. Unreadable pairs are counted, '
      + 'never shown as zero. The revoke plan is written for YOUR wallet to '
      + 'send; this server never signs and never sends transactions.',
  };
}

module.exports = { readAllowances, setEthCaller, SPENDERS, UNLIMITED_MIN, spendersFor };
