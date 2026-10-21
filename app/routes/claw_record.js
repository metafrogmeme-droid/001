'use strict';
/**
 * PUBLIC: the Claw Record, GET /api/public/claw-record?days=7|30|90|365.
 *
 * The agent's own calls over a window, counted only where sealed before
 * their outcome (lib/claw_record.js), with each day's seal root and the
 * anchors recorded for it. The anchors here are what our table holds, not
 * a verdict: /roots re-checks every one against its chain live, and the
 * page links there rather than painting them itself.
 *
 * No session; counts, percent and R only; a live call is counted and never
 * shown. IP-rate-limited and cached a minute per window.
 */
const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { computeClawRecord, windowOf } = require('../lib/claw_record');
const { listRoots } = require('../lib/seal_roots');

const router = express.Router();
router.use(rateLimit({ name: 'claw_record', windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 60 * 1000;
/** The most rows one window reads: a year of the engine's calls, with room. Tests lower it. */
const limits = { maxRows: 20000 };
const cache = new Map();   // window -> { at, body }

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

router.get('/', async (req, res) => {
  const windowDays = windowOf(req.query.days);
  const hit = cache.get(windowDays);
  if (hit && Date.now() - hit.at < CACHE_MS) {
    res.set('Cache-Control', 'public, max-age=60');
    return res.json(hit.body);
  }
  let rows;
  try {
    const since = new Date(Date.now() - windowDays * 86400000);
    [rows] = await pool.execute(
      `SELECT signal_key, symbol, direction, status, pnl, created_at, resolved_at, seal, sealed_at
       FROM signals WHERE created_at >= ? ORDER BY created_at DESC LIMIT ${Number(limits.maxRows)}`, [since]);
  } catch (err) {
    // A database that did not answer is not an agent with no calls.
    console.error('Claw record read failed:', err.name || 'Error');
    return res.status(503).json({ error: 'The record could not be read', exception: err.name || 'Error' });
  }
  // Express does not catch a throw in an async handler: without this, a row
  // the computation cannot read would leave the request unanswered.
  let body;
  try {
    body = computeClawRecord(rows, { now: new Date(), windowDays });
    // A window that hit the row cap is a partial count, said so rather than
    // printed as the whole.
    body.partial = Array.isArray(rows) && rows.length >= limits.maxRows;
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
  cache.set(windowDays, { at: Date.now(), body });
  res.set('Cache-Control', 'public, max-age=60');
  res.json(body);
});

module.exports = router;
module.exports._resetCache = () => cache.clear();
module.exports._limits = limits;
