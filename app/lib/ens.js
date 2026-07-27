/**
 * ENS identity — resolve a wallet's ENS primary name and avatar, READ-ONLY.
 *
 * Reverse-resolves an address to its ENS name (`lookupAddress`) and, if set,
 * the on-chain avatar (`getAvatar`). Read calls only against an Ethereum RPC —
 * this module can never sign or write anything. Every failure is soft: a name
 * that doesn't resolve simply returns null, and the address (shortened) is
 * always the honest fallback.
 *
 * Provider construction mirrors lib/wallet.js (ethers JsonRpcProvider on the
 * ethereum RPC, overridable via WEB3_RPC_URL) and exposes a factory seam so
 * tests can inject a fake provider without network.
 */

'use strict';

const CACHE_MS = 5 * 60_000; // ENS records change rarely.
// cloudflare-eth.com answers -32046 to ordinary callers (see lib/wallet.js
// which moved off it for the same reason) — identity lookups silently died.
const RPC_DEFAULT = 'https://ethereum-rpc.publicnode.com';

let _providerFactory = null;
function setEnsProviderFactory(fn) { _providerFactory = fn; _cache.clear(); }

let _cachedProvider = null;
function provider() {
  if (_providerFactory) return _providerFactory();
  if (!_cachedProvider) {
    const { ethers } = require('ethers');
    _cachedProvider = new ethers.JsonRpcProvider(process.env.WEB3_RPC_URL || RPC_DEFAULT);
  }
  return _cachedProvider;
}

function isAddress(addr) {
  try { const { ethers } = require('ethers'); return ethers.isAddress(addr); }
  catch { return /^0x[a-fA-F0-9]{40}$/.test(String(addr || '')); }
}

function shorten(addr) {
  const a = String(addr || '');
  return a.length >= 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

const _cache = new Map(); // addr(lower) -> { at, identity }

/**
 * Resolve { address, short, ens, avatar, resolved } for an address. `resolved`
 * is false when the RPC lookup itself failed (so the UI can distinguish "no ENS
 * set" from "couldn't check"). Never throws.
 */
async function resolveIdentity(address) {
  if (!isAddress(address)) {
    return { address: null, short: null, ens: null, avatar: null, resolved: false };
  }
  const key = String(address).toLowerCase();
  const hit = _cache.get(key);
  if (hit && (Date.now() - hit.at) < CACHE_MS) return hit.identity;

  const base = { address, short: shorten(address), ens: null, avatar: null, resolved: false };
  try {
    const p = provider();
    // Let a genuine RPC/network error propagate to the outer catch (→
    // resolved:false); ethers returns null (not throws) when there's simply no
    // reverse record, which correctly reads as resolved-with-no-name.
    const name = await p.lookupAddress(address);
    let avatar = null;
    if (name) avatar = await p.getAvatar(name).catch(() => null);
    const identity = { ...base, ens: name || null, avatar: avatar || null, resolved: true };
    _cache.set(key, { at: Date.now(), identity });
    return identity;
  } catch {
    // RPC hiccup — return the honest address-only fallback, do NOT cache.
    return base;
  }
}

const NAME_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)*\.eth$/i;
const _fwdCache = new Map();   // name(lower) -> { at, address }

/**
 * Forward resolution: name.eth -> checksummed address, or null. Null covers
 * unset names, unresolvable names AND rpc failure — never a guessed or zero
 * address. Uses the same provider seam as resolveIdentity.
 */
async function resolveEnsName(name) {
  const n = String(name || '').trim().toLowerCase();
  if (!NAME_RE.test(n)) return null;
  const hit = _fwdCache.get(n);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.address;
  try {
    const address = await provider().resolveName(n);
    const out = address || null;
    // Cache only real answers — a null from a flaky rpc must retry next time.
    if (out) _fwdCache.set(n, { at: Date.now(), address: out });
    return out;
  } catch (e) { return null; }
}

module.exports = { resolveIdentity, resolveEnsName, setEnsProviderFactory, shorten, isAddress, NAME_RE };
