'use strict';
/**
 * GET /api/risk/bridge?chain=&oapp=&eid= — the Risk Scanner's bridge check.
 *
 * How many verifiers a LayerZero app needs before it takes a message, each
 * way on one route, read live from the chain through two readers that must
 * agree (lib/lz_verifiers.js). Public and read-only: an app's verifier
 * setting is on-chain for anyone to read, and nothing here signs, sends or
 * remembers who asked.
 *
 *   200  the setting as read, with who verifies and whether one is enough
 *   400  the question is malformed (chain, address or endpoint id)
 *   503  it could not be read, or the readers disagreed: never "safe"
 *
 * A COUNT, NEVER A VERDICT: the answer says how many and who, not whether
 * they are trustworthy. Cached for a minute per question, because each
 * answer is six calls to two public readers.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const lz = require('../lib/lz_verifiers');

const router = express.Router();
router.use(rateLimit({ name: 'risk.bridge', windowMs: 60000, max: 10, key: ipKey }));

const CACHE_MS = 60000;
const cache = new Map();

router.get('/bridge', async (req, res) => {
  const q = { chain: String(req.query.chain || ''), oapp: String(req.query.oapp || '').toLowerCase(), eid: Number(req.query.eid) };
  const key = `${q.chain}|${q.oapp}|${q.eid}`;
  try {
    const hit = cache.get(key);
    let out = hit && Date.now() - hit.at < CACHE_MS ? hit.body : null;
    if (!out) {
      out = await lz.bridgeCheck(q);
      if (out.status === 'read') {
        if (cache.size > 500) cache.clear();
        cache.set(key, { at: Date.now(), body: out });
      }
    }
    res.setHeader('Cache-Control', 'no-store');
    if (out.status === 'refused') return res.status(400).json({ error: out.reason });
    if (out.status !== 'read') return res.status(503).json({ error: 'could_not_read', reason: out.reason, ...(out.disagreement ? { disagreement: true } : {}) });
    return res.json(out);
  } catch (err) {
    console.error('Risk bridge check error:', err.name || 'Error');
    res.status(503).json({ error: 'could_not_read', exception: err.name || 'Error' });
  }
});

/** The chains the check reads, for the page's picker: names and endpoint ids, never a configured URL. */
router.get('/chains', (req, res) => {
  const all = lz.chains();
  res.setHeader('Cache-Control', 'public, max-age=300');
  res.json({ chains: Object.values(all).map((c) => ({ key: c.key, name: c.name, eid: c.eid })) });
});

router._cache = cache;
module.exports = router;
