/**
 * Market insight proxy — forwards to the bot's API bridge `/insight/{symbol}`
 * so the dashboard can render the SAME decision picture the bot trades off
 * (scored S/R levels, FVGs, liquidity pools, confluence votes, tape CVD and
 * gate telemetry) without exposing the bridge directly. Public market data,
 * same trust level as /api/market/* — no auth required.
 */

const express = require('express');
const http = require('http');
const https = require('https');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();

// Bot API bridge base URL (uvicorn api_bridge:app, :8000 in docker-compose).
const BOT_API_URL = (process.env.BOT_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

// Per-IP sliding-window rate limit, shared by every web instance
// (lib/rate_limit). The bridge itself enforces 30 req/min per client IP, so we
// stay comfortably below that with the cache and keep abusive clients out.
router.use(rateLimit({ name: 'insight', windowMs: 60000, max: 60, key: ipKey, message: 'Too many requests' }));

// Simple GET with promise (http or https depending on the bridge URL)
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https:') ? https : http;
    const req = mod.get(url, { timeout: 12000 }, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(body) }); }
        catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

// Cache to avoid hammering the bridge (30-second TTL; overlays refresh ~60s)
const cache = {};
function cached(key, ttlMs, fetcher) {
  return async () => {
    const now = Date.now();
    if (cache[key] && now - cache[key].ts < ttlMs) return cache[key].data;
    const data = await fetcher();
    cache[key] = { data, ts: now };
    return data;
  };
}

// Symbol validation — mirrors the bridge's _SYMBOL_RE (accepts "BTC/USDT");
// prevents path/query injection before the value reaches the upstream URL.
function validateSymbol(sym) {
  return /^[A-Z0-9]{1,15}(\/[A-Z0-9]{1,15})?$/.test(sym);
}

// GET /api/insight?symbol=BTC/USDT&timeframe=1h&limit=200 - Bot decision picture
// The symbol travels as a QUERY PARAM, not a path segment: several hosting
// proxies (including the live deployment's) reject the %2F an encoded-slash
// path segment needs, 404ing at the edge before Express ever sees the request.
// The legacy /:symbol path form is kept below for old cached clients.
// The symbol goes UP as a query param too, and that is the whole fix for a
// live outage. `BTC/USDT` contains a slash; as a path segment it must be
// percent-encoded, and the edge in front of the bridge decodes `%2F` back to
// `/` before it matches a path — so `${BOT_API_URL}/{route}/BTC%2FUSDT`
// arrived as a two-segment path, matched nothing, and came back as the
// WEBSITE'S HTML 404. `fetchJSON` then failed to parse it and the panel
// showed a 502, which reads as "the bridge is down" about a bridge that was
// answering every other request correctly. Measured 2026-09-13: `BTCUSDT`
// 200 with real data, `BTC%2FUSDT` 404 HTML, same host, same second.
//
// The comment fifteen lines above this one already recorded this exact trap
// for the INBOUND edge and fixed it the same way. Asking which other surface
// makes the same claim is the step that was skipped.
async function insightHandler(req, res, rawSym) {
  try {
    const sym = String(rawSym || '').toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const tf = req.query.timeframe || '1h';
    if (!/^(1m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)$/.test(tf)) return res.status(400).json({ error: 'Invalid timeframe' });
    const limit = Math.min(parseInt(req.query.limit) || 200, 500);
    const r = await cached(`insight_${sym}_${tf}_${limit}`, 30000, () =>
      fetchJSON(`${BOT_API_URL}/insight?symbol=${encodeURIComponent(sym)}&timeframe=${tf}&limit=${limit}`)
    )();
    if (r.status !== 200) {
      const detail = r.data && r.data.detail;
      return res.status(r.status >= 500 ? 502 : r.status).json({ error: detail || 'Insight unavailable' });
    }
    res.json(r.data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch insight' });
  }
}

router.get('/', (req, res) => insightHandler(req, res, req.query.symbol));
router.get('/:symbol', (req, res) => insightHandler(req, res, req.params.symbol));

module.exports = router;
