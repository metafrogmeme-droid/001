'use strict';
/**
 * GET /api/gas — live per-chain gas, PUBLIC and unauthenticated: gwei is a
 * market fact, and the Escape planner works logged-out. Cached 60s;
 * unreadable chains are absent from the answer, never invented, and an
 * empty read is never cached (that would freeze a transient outage into
 * "no data" for a minute).
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { readGas } = require('../lib/gas_read');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

let cache = null;
router.get('/', async (req, res) => {
  try {
    if (cache && Date.now() - cache.at < 60000) {
      res.set('Cache-Control', 'public, max-age=30');
      return res.json(cache.body);
    }
    const body = await readGas();
    if (Object.keys(body.chains).length) cache = { at: Date.now(), body };
    res.set('Cache-Control', 'public, max-age=30');
    res.json(body);
  } catch (err) {
    console.error('Gas read error:', err.stack || err.message);
    res.status(500).json({ error: 'Gas read unavailable' });
  }
});

module.exports = router;
