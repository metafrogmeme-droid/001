'use strict';
/**
 * PUBLIC community-strategy catalogue — the published user strategies, served
 * with NO auth so anyone browsing /agents sees them alongside the engine's
 * presets. A community strategy is a CONFIG (intent-rule chips + prose) — §4:
 * percent/ratio/list only, never a dollar figure and never a performance claim.
 * Author identity is not exposed. IP-rate-limited and briefly cached.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const store = require('../lib/user_strategies');
const { decorate } = require('../lib/follow_counts');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey, message: 'rate_limited' }));

// No cache: this reads the local DB (not a slow gateway), so a freshly published
// strategy must appear immediately. The rate limiter already shields the store.
router.get('/', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  try {
    // Server-side search/filter. Enum filters are VALIDATED, not guessed: a
    // typo'd regime is a 400, never a silently-ignored filter that would make
    // an unfiltered list read as "no matches for my filter".
    const regime = String(req.query.regime || '');
    const horizon = String(req.query.horizon || '');
    if (regime && !store.REGIMES.has(regime)) {
      return res.status(400).json({ error: `Unknown regime "${regime.slice(0, 24)}"` });
    }
    if (horizon && !store.HORIZONS.has(horizon)) {
      return res.status(400).json({ error: `Unknown horizon "${horizon.slice(0, 24)}"` });
    }
    const agents = await store.listPublic(req.query.limit,
      { q: req.query.q, regime, horizon });
    await decorate(agents);   // follower counts — omitted (never zeroed) if unreadable
    res.json({ agents, count: agents.length, community: true });
  } catch (err) {
    console.error('Public community strategies error:', err.stack || err.message);
    res.status(502).json({ error: 'Community strategies unavailable' });
  }
});

router.get('/:slug', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  try {
    const agent = await store.getPublicBySlug(req.params.slug);
    if (!agent) return res.status(404).json({ error: 'Strategy not found' });
    res.json({ agent });
  } catch (err) {
    console.error('Public community strategy error:', err.stack || err.message);
    res.status(502).json({ error: 'Community strategy unavailable' });
  }
});

module.exports = router;
