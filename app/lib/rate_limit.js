/**
 * In-process sliding-window rate limiter (per key).
 *
 * Extracted from the proven per-IP limiter in app/routes/market.js (RC-AUD-028a)
 * so the money endpoints (credential submit, control changes, emergency stop) can
 * bound abuse per user. Single-process; front with a shared store (Redis) for a
 * multi-replica deployment.
 *
 *   router.post('/', rateLimit({ windowMs: 60000, max: 10, key: userKey }), handler)
 *
 * `key(req)` derives the bucket (defaults to client IP). Use userKey to bucket by
 * authenticated user (place AFTER the auth middleware).
 *
 * `slidingWindow()` is the same window without the Express wrapper, for a
 * limit that has to be applied somewhere a request object is not at hand — an
 * MCP tool handler, which receives the caller's address in its context. One
 * window, two callers, so the MCP copy cannot quietly diverge from the
 * middleware's (the earlier hand-rolled map in routes/mcp.js never expired
 * entries — see the note above its rateLimit call).
 */

function ipKey(req) {
  return req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
}

function userKey(req) {
  return (req.user && req.user.user_id != null) ? `u:${req.user.user_id}` : ipKey(req);
}

function slidingWindow({ windowMs = 60000, max = 20 } = {}) {
  const hits = new Map(); // key -> number[] (timestamps in window)

  const prune = () => {
    const cutoff = Date.now() - windowMs;
    for (const [k, arr] of hits) {
      const recent = arr.filter(ts => ts > cutoff);
      if (recent.length === 0) hits.delete(k);
      else hits.set(k, recent);
    }
    if (hits.size > 10000) {
      const keys = [...hits.keys()];
      for (let i = 0; i < keys.length - 5000; i++) hits.delete(keys[i]);
    }
  };
  const timer = setInterval(prune, windowMs);
  if (timer.unref) timer.unref();

  return {
    /** Record a hit for `k` and say whether it fell within the allowance. */
    allow(k) {
      const now = Date.now();
      const cutoff = now - windowMs;
      const arr = (hits.get(k) || []).filter(ts => ts > cutoff);
      if (arr.length >= max) {
        hits.set(k, arr);
        return false;
      }
      arr.push(now);
      hits.set(k, arr);
      return true;
    },
    retryAfterSeconds: Math.ceil(windowMs / 1000),
  };
}

function rateLimit({ windowMs = 60000, max = 20, key = ipKey, message = 'Too many requests, slow down.' } = {}) {
  const window = slidingWindow({ windowMs, max });

  return function (req, res, next) {
    if (!window.allow(key(req))) {
      res.setHeader('Retry-After', window.retryAfterSeconds);
      return res.status(429).json({ error: message });
    }
    next();
  };
}

module.exports = { rateLimit, slidingWindow, ipKey, userKey };
