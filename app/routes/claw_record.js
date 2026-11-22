'use strict';
/**
 * PUBLIC: the Claw Record,
 * GET /api/public/claw-record?window=7|30|90|365&limit=1..200&before=<resolved_at>&before_key=<key>
 * (`days=` is read too, for the page and older links; `window` wins).
 *
 * The agent's own calls over a window, counted only where sealed before
 * their outcome (lib/claw_record.js), with each day's seal root and the
 * anchors recorded for it. The anchors here are what our table holds, not
 * a verdict: /roots re-checks every one against its chain live, and the
 * page links there rather than painting them itself.
 *
 * EXACT OVER THE WINDOW. The counts are made by the database (groupsSql), so a
 * 365-day window is counted whole; it used to be the window's newest 20,000
 * rows, counted here, and partial. The resolved calls listed are a page:
 * RECENT by default, `limit` up to MAX_RECENT, and `before` + `before_key`
 * (the last listed call's resolution time and key, which the body hands back
 * as `next_before` / `next_before_key`) for the next one.
 *
 * No session; counts, percent and R only; a live call is counted and never
 * shown. IP-rate-limited. The counts are cached a minute per window, and the
 * first page per window and limit; a cursor page reads its rows live, so the
 * cache stays bounded whatever cursors are sent.
 */
const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const cr = require('../lib/claw_record');
const { listRoots } = require('../lib/seal_roots');

const router = express.Router();
router.use(rateLimit({ name: 'claw_record', windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 60 * 1000;
/**
 * The most groups one window's count reads. A year is ~365 days x 3
 * eligibilities x a handful of outcome words, so this is never reached by a
 * real window; a read that reaches it is reported partial. Tests lower it.
 */
const limits = { maxGroups: 50000 };
const counts = new Map();   // window -> { at, groups, partial }
const pages = new Map();    // `${window}:${limit}` -> { at, body }

/** Each day's root and recorded anchors, by day; null when the roots could not be read. */
async function rootsByDay(windowDays) {
  try {
    const roots = await listRoots(Math.min(windowDays, 90));
    const out = new Map();
    for (const r of roots || []) {
      out.set(String(r.day), { root: r.root, anchors: (r.anchors || []).map((a) => a.chain) });
    }
    return out;
  } catch (e) { return null; }
}

/**
 * The window's grouped counts, from the cache or the database. Throws on a
 * failed read. A fresh read is cached by the caller once it has folded, so an
 * answer the record cannot read is never served from the cache.
 */
async function windowCounts(windowDays, since) {
  const hit = counts.get(windowDays);
  if (hit && Date.now() - hit.at < CACHE_MS) return { rows: hit.rows, fresh: false };
  const [rows] = await pool.execute(cr.groupsSql(limits.maxGroups), [since]);
  return { rows, fresh: true };
}

router.get('/', async (req, res) => {
  const windowDays = cr.windowOf(req.query.window ?? req.query.days);
  const limit = cr.recentLimitOf(req.query.limit);
  const cursor = cr.cursorOf(req.query.before, req.query.before_key);
  if (cursor === undefined) {
    return res.status(400).json({ error: 'before is not a time' });
  }
  const pageKey = `${windowDays}:${limit}`;
  if (cursor === null) {
    const hit = pages.get(pageKey);
    if (hit && Date.now() - hit.at < CACHE_MS) {
      res.set('Cache-Control', 'public, max-age=60');
      return res.json(hit.body);
    }
  }
  const now = new Date();
  const since = new Date(now.getTime() - windowDays * 86400000);
  let grouped; let recentRows;
  try {
    grouped = await windowCounts(windowDays, since);
    [recentRows] = await pool.execute(cr.recentSql(limit, cursor), cr.recentParams(since, cursor));
  } catch (err) {
    // A database that did not answer is not an agent with no calls.
    console.error('Claw record read failed:', err.name || 'Error');
    return res.status(503).json({ error: 'The record could not be read', exception: err.name || 'Error' });
  }
  // Express does not catch a throw in an async handler: without this, a row
  // the computation cannot read would leave the request unanswered.
  let body;
  try {
    const more = recentRows.length > limit;
    const listed = recentRows.slice(0, limit);
    body = cr.recordFromGroups(cr.groupsFromSql(grouped.rows), listed, { now, windowDays });
    // A count that reached its read cap is partial, said so rather than
    // printed as the whole. Untruncated, the counts are the whole window.
    body.partial = grouped.rows.length >= limits.maxGroups;
    body.limit = limit;
    const last = more ? listed[listed.length - 1] : null;
    body.next_before = last ? new Date(last.resolved_at || last.created_at).toISOString() : null;
    body.next_before_key = last ? String(last.signal_key) : null;
    const roots = await rootsByDay(windowDays);
    body.roots_readable = roots !== null;
    for (const d of body.days) {
      const r = roots ? roots.get(d.day) : null;
      d.root = r ? r.root : null;
      d.anchors = r ? r.anchors : null;
    }
  } catch (err) {
    console.error('Claw record failed:', err.name || 'Error');
    return res.status(500).json({ error: 'The record could not be computed', exception: err.name || 'Error' });
  }
  if (grouped.fresh) counts.set(windowDays, { at: Date.now(), rows: grouped.rows });
  if (cursor === null) pages.set(pageKey, { at: Date.now(), body });
  res.set('Cache-Control', 'public, max-age=60');
  res.json(body);
});

module.exports = router;
module.exports._resetCache = () => { counts.clear(); pages.clear(); };
module.exports._limits = limits;
