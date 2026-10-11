'use strict';
/**
 * Request limits per key, shared by every web instance.
 *
 *   router.post('/', rateLimit({ name: 'things.write', windowMs: 60000, max: 10, key: userKey }), handler)
 *
 * Extracted from the proven per-IP limiter in app/routes/market.js (RC-AUD-028a)
 * so the money endpoints (credential submit, control changes, emergency stop) can
 * bound abuse per user.
 *
 * `key(req)` derives the bucket (defaults to client IP). Use userKey to bucket by
 * authenticated user (place AFTER the auth middleware). `name` names the shared
 * bucket: unique per limiter, stable across deploys.
 *
 * `slidingWindow()` is the same window without the Express wrapper, for a
 * limit that has to be applied somewhere a request object is not at hand — an
 * MCP tool handler, which receives the caller's address in its context. One
 * window, two callers, so the MCP copy cannot quietly diverge from the
 * middleware's (the earlier hand-rolled map in routes/mcp.js never expired
 * entries — see the note above its rateLimit call).
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, and the outgoing and incoming instances overlap. These windows
 * lived in a Map per process, so every instance kept its own count: two
 * instances answering at once allowed twice the stated number, and a rotation
 * started a fresh count. The header said "front with a shared store for a
 * multi-replica deployment". It is one.
 *
 * HOW ONE REQUEST IS DECIDED
 *
 *  1. This instance's own sliding window (the old limiter) answers first, from
 *     memory. It refuses without touching the database, and it is the whole
 *     limit while the database does not answer.
 *  2. The shared count, in `rate_hits`: one row per (limiter, key, window),
 *     raised by one conditional UPDATE that succeeds only below the allowance,
 *     so two instances can never both take the last slot. The allowance is the
 *     sliding-window estimate: `max` less the previous window's count, weighted
 *     by how much of that window still lies inside the last `windowMs`.
 *  3. A request that SPENDS something (any method but GET, HEAD and OPTIONS:
 *     chat, orders, keys, chain writes) waits for the shared answer. A READ is
 *     counted beside the request instead of before it: holding every page poll
 *     for a database round trip would buy exactness only against a parallel
 *     burst, which step 1 still bounds per instance. A shared refusal is
 *     remembered, so the key's next read is refused here without asking.
 *  4. When the database errors, or takes longer than SHARED_BUDGET_MS, this
 *     instance skips the shared count for BREAKER_MS: step 1 alone, the old
 *     behaviour. A request is never refused, and never held past the budget,
 *     because the database is slow or down.
 *
 * The stored bucket key is a SHA-256 of the caller's key, not the key itself,
 * and a row is pruned once its window can no longer be read (a few minutes).
 */

const crypto = require('node:crypto');

const SHARED_BUDGET_MS = 300;      // the longest a request waits for the shared count
const BREAKER_MS = 30_000;         // how long an instance limits alone after the database fails it
const MAX_INFLIGHT = 8;            // shared counts in flight per process, so limits never crowd the pool
const PRUNE_EVERY_MS = 5 * 60_000;
const READS = new Set(['GET', 'HEAD', 'OPTIONS']);
const NAME_RE = /^[a-z][a-z0-9_.-]{1,47}$/;

const NAMES = new Map(); // limiter name -> times constructed in this process
const S = { openUntil: 0, inflight: 0, lastPrune: 0, lastWarn: 0 };

function _pool() {
  try { return require('../db').pool; } catch (_) { return null; }
}

function ipKey(req) {
  return req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
}

function userKey(req) {
  return (req.user && req.user.user_id != null) ? `u:${req.user.user_id}` : ipKey(req);
}

/** `p`, or a rejection once SHARED_BUDGET_MS has passed without it. */
function withinBudget(p) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const e = new Error('the shared count took longer than its budget');
      e.code = 'ETIMEDOUT';
      reject(e);
    }, SHARED_BUDGET_MS);
    if (timer.unref) timer.unref();
  });
  return Promise.race([p, late]).finally(() => clearTimeout(timer));
}

function fellBack(err, now) {
  S.openUntil = now + BREAKER_MS;
  if (now - S.lastWarn < 10 * 60_000) return;
  S.lastWarn = now;
  // The class or code only: a driver message can carry a hostname.
  console.warn(`rate_limit: the shared count did not answer (${(err && (err.code || err.name)) || 'Error'}); `
    + `limiting on this instance alone for ${BREAKER_MS / 1000}s`);
}

function prune(pool, now) {
  if (now - S.lastPrune < PRUNE_EVERY_MS) return;
  S.lastPrune = now;
  Promise.resolve()
    .then(() => pool.execute('DELETE FROM rate_hits WHERE expires_at < ?', [now]))
    .catch(() => { /* best effort: an expired row is never read again */ });
}

function slidingWindow({ name, windowMs = 60000, max = 20 } = {}) {
  if (!NAME_RE.test(String(name || ''))) {
    throw new Error(`rate_limit: every limiter needs a name for its shared bucket, got ${JSON.stringify(name)}`);
  }
  NAMES.set(name, (NAMES.get(name) || 0) + 1);

  const hits = new Map(); // key -> number[] (timestamps in window), this instance's own count
  const memo = new Map(); // bucket hash -> what this instance knows of the shared count this window

  const prunePeriodically = () => {
    const now = Date.now();
    const cutoff = now - windowMs;
    for (const [k, arr] of hits) {
      const recent = arr.filter(ts => ts > cutoff);
      if (recent.length === 0) hits.delete(k);
      else hits.set(k, recent);
    }
    if (hits.size > 10000) {
      const keys = [...hits.keys()];
      for (let i = 0; i < keys.length - 5000; i++) hits.delete(keys[i]);
    }
    const win = Math.floor(now / windowMs);
    for (const [h, e] of memo) if (e.win < win) memo.delete(h);
  };
  const timer = setInterval(prunePeriodically, windowMs);
  if (timer.unref) timer.unref();

  const recent = (k, now) => (hits.get(k) || []).filter(ts => ts > now - windowMs);

  function entry(h, win) {
    let e = memo.get(h);
    if (!e || e.win !== win) {
      // prev: the previous window's shared count, once read. full: a lower
      // bound on this window's count, learned from a refusal. rowSeen: the row
      // for this window exists, so an UPDATE that matches nothing means full.
      e = { win, prev: undefined, prevRead: null, full: 0, rowSeen: false };
      memo.set(h, e);
      if (memo.size > 20000) memo.clear();
    }
    return e;
  }

  /** How many hits this window may hold at `now`: the sliding-window estimate. */
  function allowance(prev, now, win) {
    const overlap = 1 - (now - win * windowMs) / windowMs;
    return Math.ceil(max - prev * overlap);
  }

  /** Count one hit in the shared row: true counted, false full, null unreadable. */
  async function countShared(h, ent, now, win) {
    if (now < S.openUntil || S.inflight >= MAX_INFLIGHT) return null;
    const pool = _pool();
    if (!pool) return null;
    S.inflight += 1;
    try {
      const verdict = await withinBudget((async () => {
        if (ent.prev === undefined) {
          ent.prevRead = ent.prevRead || pool.execute(
            'SELECT hits FROM rate_hits WHERE bucket_key = ?', [`${name}:${win - 1}:${h}`])
            .then(([rows]) => (rows && rows[0] ? Number(rows[0].hits) : 0))
            .catch((e) => { ent.prevRead = null; throw e; });
          ent.prev = await ent.prevRead;
        }
        const limit = allowance(ent.prev, now, win);
        if (limit <= ent.full) return false; // full starts at 0, so no allowance at all is full too
        const id = `${name}:${win}:${h}`;
        if (!ent.rowSeen) {
          try {
            await pool.execute(
              'INSERT INTO rate_hits (bucket_key, hits, expires_at) VALUES (?, 1, ?)',
              [id, (win + 2) * windowMs]);
            ent.rowSeen = true;
            return true;
          } catch (e) {
            if (!(e && (e.code === 'ER_DUP_ENTRY' || e.errno === 1062))) throw e;
            ent.rowSeen = true; // another instance opened this window first
          }
        }
        const [upd] = await pool.execute(
          'UPDATE rate_hits SET hits = hits + 1 WHERE bucket_key = ? AND hits < ?', [id, limit]);
        if (upd && upd.affectedRows === 1) return true;
        ent.full = Math.max(ent.full, limit);
        return false;
      })());
      prune(pool, now); // only after the database answered: never a second call into a failing one
      return verdict;
    } catch (err) {
      fellBack(err, now);
      return null;
    } finally {
      S.inflight -= 1;
    }
  }

  /**
   * Decide one request for `k`: a boolean when the answer is known now, or a
   * promise of one when `wait` asks for the shared count first.
   */
  function take(k, wait) {
    const now = Date.now();
    const arr = recent(k, now);
    if (arr.length >= max) return false;
    const h = crypto.createHash('sha256').update(String(k)).digest('hex').slice(0, 40);
    const win = Math.floor(now / windowMs);
    const ent = entry(h, win);
    if (ent.prev !== undefined && allowance(ent.prev, now, win) <= ent.full) return false; // the shared count said full
    arr.push(now); // reserve this instance's slot before any await
    hits.set(k, arr);
    const counted = countShared(h, ent, now, win);
    if (!wait) return true;
    return counted.then((v) => {
      if (v !== false) return true; // counted, or unreadable: this instance's window already allowed it
      const mine = hits.get(k);
      const i = mine ? mine.lastIndexOf(now) : -1;
      if (i >= 0) mine.splice(i, 1);
      return false;
    });
  }

  return {
    name,
    /** Record a hit for `k`, after the shared count, and say whether it fell within the allowance. */
    async allow(k) { return take(k, true); },
    take,
    retryAfterSeconds: Math.ceil(windowMs / 1000),
  };
}

function rateLimit({ name, windowMs = 60000, max = 20, key = ipKey, message = 'Too many requests, slow down.' } = {}) {
  const window = slidingWindow({ name, windowMs, max });
  const refuse = (res) => {
    res.setHeader('Retry-After', window.retryAfterSeconds);
    return res.status(429).json({ error: message });
  };

  return function (req, res, next) {
    const verdict = window.take(key(req), !READS.has(req.method));
    if (verdict === true) return next();
    if (verdict === false) return refuse(res);
    verdict.then((ok) => (ok ? next() : refuse(res)), next);
  };
}

/** Test seam: the limiter names constructed in this process, with counts. */
function _names() {
  return new Map(NAMES);
}

module.exports = {
  rateLimit, slidingWindow, ipKey, userKey,
  SHARED_BUDGET_MS, BREAKER_MS, MAX_INFLIGHT,
  _names,
};
