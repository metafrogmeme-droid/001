'use strict';
/**
 * The public-market fetch + tiny TTL cache, shared.
 *
 * These two helpers lived inside routes/market.js while the website was their
 * only caller. They moved here when the MCP server started serving the same
 * market reads: a second private copy of the cache would mean the site and an
 * agent could be looking at different ticker snapshots of the same instant,
 * and then disagree about a number neither of them computed differently.
 *
 * The cache is keyed by string, so callers that pass the same key genuinely
 * share one entry — that sharing is the point, not a side effect.
 */

const https = require('https');

/** Simple HTTPS GET → parsed JSON. Rejects on network error, timeout or bad JSON. */
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: 8000 }, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); }
        catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

// Shared across every caller — see the module note above.
const cache = {};
// Fetches in flight, by key. Callers who miss together share one.
const inflight = new Map();

/**
 * cached(key, ttlMs, fetcher) → async () => data
 *
 * A miss awaits `fetcher`, and a caller who misses while that fetch is in
 * flight awaits the SAME fetch: callers who miss together used to fetch once
 * each, so a crowd opening a page at the same moment sent the venue one
 * request per person for one snapshot. A fetch that rejects is not cached,
 * and every caller waiting on it gets the rejection; the next caller fetches
 * again.
 */
function cached(key, ttlMs, fetcher) {
  return async () => {
    const now = Date.now();
    if (cache[key] && now - cache[key].ts < ttlMs) return cache[key].data;
    if (inflight.has(key)) return inflight.get(key);
    const p = (async () => {
      try {
        const data = await fetcher();
        cache[key] = { data, ts: now };
        return data;
      } finally {
        inflight.delete(key);
      }
    })();
    inflight.set(key, p);
    return p;
  };
}

/** Test seam: drop cached entries so a test can control what a caller sees. */
function _clearCache() {
  for (const k of Object.keys(cache)) delete cache[k];
  inflight.clear();
}

module.exports = { fetchJSON, cached, _clearCache };
