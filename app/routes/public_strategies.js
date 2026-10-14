/**
 * PUBLIC Strategy-Agent marketplace catalogue — served with NO auth so anyone
 * can browse the engine's strategy agents. Each card is one of the real engine
 * presets and carries DESIGN + regime + qualitative risk only — never a dollar
 * figure and never a fabricated return (§4). Verified performance lives on the
 * honest Strategy Lab backtester + the verifiable leaderboard. Relayed from the
 * bot gateway, IP-rate-limited and briefly cached so a spike can't hammer it.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getGateway, relay, isConfigured } = require('../lib/gateway');
const { decorate } = require('../lib/follow_counts');
const { signedIn, strategyScanFor, viewerCacheControl } = require('../lib/signal_access');

// Identifies, never refuses. Lazy, as routes/sync.js requires it.
const optionalAuth = (req, res, next) => require('../auth').optionalAuth(req, res, next);

const router = express.Router();
router.use(rateLimit({ name: 'public_strategies', windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 5 * 60 * 1000;   // the catalogue changes only on a deploy
let cache = null;                  // { at: ms, data }

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) {
    return res.status(200).json(cache.data);
  }
  try {
    const r = await getGateway('/public/strategies', 15000);
    if (r.status >= 200 && r.status < 300) {
      // Local follower counts ride the relayed cards (count-only, §4-safe).
      // The catalogue itself is bot-authored; only `followers` is added here.
      if (r.data && Array.isArray(r.data.agents)) {
        try { await decorate(r.data.agents); } catch (e) { /* counts are optional */ }
      }
      cache = { at: now, data: r.data };
    }
    relay(res, r);
  } catch (err) {
    res.status(502).json({ error: 'Strategy catalogue unavailable' });
  }
});

// One strategy's board now: a preset the live bot does not run, read by its
// own rule on its markets' last closed 1h bars (bot/core/strategy_scan.py).
// Market prices, sides, scores and unread markets; no dollar figure, nothing
// placed. The bot reads the venue once per preset per closed hour; a minute's
// cache per id and a tighter per-IP limit keep a spike off the bot. The id
// rides as a query parameter, never a path segment.
//
// The board is the strategy's call now (a side, a signal, the market it would
// hold, the limit it would rest), so it is a member's: a visitor reads which
// markets it read (lib/signal_access.js). The cache holds the whole board and
// each viewer is shaped from it.
const SCAN_CACHE_MS = 60 * 1000;
const SCAN_ID = /^[a-z0-9-]{1,40}$/;
const scanCache = new Map();       // id -> { at: ms, data }

router.get('/scan', optionalAuth, rateLimit({ name: 'public_strategy_scan', windowMs: 60000, max: 6, key: ipKey }), async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const id = String(req.query.id || '').toLowerCase();
  if (!SCAN_ID.test(id)) return res.status(400).json({ error: 'bad_id' });
  const now = Date.now();
  viewerCacheControl(res, 0);
  const hit = scanCache.get(id);
  if (hit && (now - hit.at) < SCAN_CACHE_MS) {
    return res.status(200).json(strategyScanFor(signedIn(req), hit.data));
  }
  try {
    const r = await getGateway('/public/strategy-scan?id=' + encodeURIComponent(id), 55000);
    if (r.status === 200) {
      scanCache.set(id, { at: now, data: r.data });
      return res.status(200).json(strategyScanFor(signedIn(req), r.data));
    }
    // The bot names a failed venue read by its exception class; keep that
    // class (never a message) rather than the relay's generic 502.
    if (r.status === 502 && r.data && r.data.error === 'scan_failed') {
      return res.status(502).json({ error: 'scan_failed', detail: String(r.data.detail || '') });
    }
    relay(res, r);
  } catch (err) {
    res.status(502).json({ error: 'Strategy scan unavailable' });
  }
});

module.exports = router;
