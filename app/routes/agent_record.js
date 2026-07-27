'use strict';
/**
 * PUBLIC per-agent live paper record — GET /api/public/agent-record/:slug.
 *
 * The forward-only record of members paper-copying this agent's picks:
 * every row was sealed at OPEN (before its outcome existed) and each carries
 * its /call/<key> receipt, so the whole record is third-party verifiable.
 * §4: percent / ratio / count only — no vUSDT figures, no member identity.
 * No auth: it aggregates already-sealed public receipts. IP-rate-limited and
 * briefly cached per slug.
 */

const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { computeAgentRecord } = require('../lib/agent_record');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const CACHE_MS = 60 * 1000;
const cache = new Map();   // slug -> { at, data }

router.get('/:slug', async (req, res) => {
  const slug = String(req.params.slug || '').toLowerCase();
  if (!SLUG_RE.test(slug)) return res.status(400).json({ error: 'Invalid slug' });
  const now = Date.now();
  const hit = cache.get(slug);
  if (hit && (now - hit.at) < CACHE_MS) return res.json(hit.data);
  try {
    const [rows] = await pool.execute(
      `SELECT user_id, symbol, direction, margin, pnl, reason, trade_key, sealed_at, closed_at
       FROM arena_trades WHERE agent_slug = ? ORDER BY closed_at DESC LIMIT 500`, [slug]);
    let openNow = 0;
    try {
      const [oc] = await pool.execute(
        'SELECT COUNT(*) AS n FROM arena_positions WHERE agent_slug = ?', [slug]);
      openNow = Number(oc[0]?.n || 0);
    } catch (e) { /* open-count is decoration; the closed record stands alone */ }
    const data = { slug, ...computeAgentRecord(rows), open_now: openNow };
    if (cache.size > 128) cache.clear();
    cache.set(slug, { at: now, data });
    res.json(data);
  } catch (err) {
    console.error('Agent record error:', err.stack || err.message);
    res.status(502).json({ error: 'Record unavailable' });
  }
});

function _resetCache() { cache.clear(); }
router._resetCache = _resetCache;

module.exports = router;
