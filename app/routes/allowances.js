'use strict';
/**
 * Allowance X-ray API — public, read-only, rate-limited. The bounded
 * allowance grid for any address on one chain, with revoke plans the
 * caller's own wallet sends. 60s cache per (chain, address).
 */
const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { readAllowances } = require('../lib/allowances');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 20, key: ipKey }));

const cache = new Map();   // `${chain}|${addr}` -> { at, body }
const CACHE_MAX = 300;

router.get('/:chain/:address', async (req, res) => {
  try {
    const chain = String(req.params.chain || '').toLowerCase();
    const address = String(req.params.address || '');
    // Solana speaks base58, not 0x — the branch validates inside the lib.
    if (chain === 'solana') {
      const { readDelegates, isSolanaAddress } = require('../lib/solana');
      if (!isSolanaAddress(address)) return res.status(400).json({ error: 'Bad address' });
      const key = `solana|${address}`;
      const hit = cache.get(key);
      if (hit && Date.now() - hit.at < 60_000) {
        return res.set('Cache-Control', 'public, max-age=60').json(hit.body);
      }
      const body = await readDelegates(address);
      if (body.error) return res.status(400).json(body);
      if (!body.unreadable_pairs) {
        if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
        cache.set(key, { at: Date.now(), body });
      }
      return res.set('Cache-Control', 'public, max-age=60').json(body);
    }
    if (!/^0x[0-9a-fA-F]{40}$/.test(address)) return res.status(400).json({ error: 'Bad address' });
    const key = `${chain}|${address.toLowerCase()}`;
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < 60_000) {
      return res.set('Cache-Control', 'public, max-age=60').json(hit.body);
    }
    const body = await readAllowances(address, chain);
    if (body.error) return res.status(400).json(body);
    // Never cache a fully-unreadable read — the next caller gets a fresh try.
    if (body.findings.length || body.zero_pairs > 0) {
      if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
      cache.set(key, { at: Date.now(), body });
    }
    res.set('Cache-Control', 'public, max-age=60').json(body);
  } catch (err) {
    console.error('Allowances error:', err.stack || err.message);
    res.status(500).json({ error: 'Allowance read unavailable' });
  }
});

module.exports = router;
