'use strict';
/**
 * Follower counts — the one genuine popularity signal the platform already
 * has. A count of DISTINCT accounts following each agent slug, read from
 * copy_subscriptions and briefly cached.
 *
 * Honesty (§4): a count is a fact — "N accounts follow this" — and that is
 * ALL it claims: it is never a quality score, never a default sort, and it
 * carries no performance implication. Counts are real rows, never seeded.
 * On a read failure the decoration is OMITTED (cards simply carry no count),
 * never zeroed — absence of data is not a zero.
 */

const { pool } = require('../db');

const CACHE_MS = 60 * 1000;
let cache = null;   // { at, counts: Map<slug, n> }

/** slug -> distinct-follower count. Returns null (never {}) on a failed read. */
async function followerCounts() {
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) return cache.counts;
  try {
    const [rows] = await pool.execute(
      'SELECT agent_id, COUNT(DISTINCT user_id) AS n FROM copy_subscriptions GROUP BY agent_id');
    const counts = new Map();
    for (const r of rows) counts.set(String(r.agent_id), Number(r.n) || 0);
    cache = { at: now, counts };
    return counts;
  } catch (err) {
    console.error('Follower counts error:', err.stack || err.message);
    return null;   // unreadable ≠ zero — callers omit the decoration
  }
}

/** Attach `followers` to each card (by id/slug) when counts are readable. */
async function decorate(cards) {
  const counts = await followerCounts();
  if (!counts) return cards;
  for (const c of (cards || [])) {
    const n = counts.get(String(c.slug || c.id || ''));
    if (n) c.followers = n;
  }
  return cards;
}

function _resetCache() { cache = null; }

module.exports = { followerCounts, decorate, _resetCache };
