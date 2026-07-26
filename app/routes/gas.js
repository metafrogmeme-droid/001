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
const { readGasCached } = require('../lib/gas_read');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

router.get('/', async (req, res) => {
  try {
    // The 60s cache, price enrichment (bounded ticker read) and the
    // never-cache-an-empty-read rule all live in readGasCached — one cache
    // shared with the get_gas MCP tool, so two surfaces never mean two
    // RPC sweeps.
    const body = await readGasCached();
    res.set('Cache-Control', 'public, max-age=30');
    res.json(body);
  } catch (err) {
    console.error('Gas read error:', err.stack || err.message);
    res.status(500).json({ error: 'Gas read unavailable' });
  }
});

module.exports = router;
