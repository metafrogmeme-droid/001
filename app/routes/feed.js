/**
 * Public agent mind-stream feed (read side).
 *
 * GET /api/feed/recent — newest agent events (scan cycles, theses, trade
 * opens/closes, stop moves, alerts, stance changes), pushed by the bot via
 * POST /api/bot/sync/events and stored in the bounded agent_events ring.
 * Public by design: this powers the landing page's "watch the agent think"
 * section and the dashboard timeline. The bot sends operator activity only,
 * and every event is passed through `publicFeedEvent` (no dollar amount of
 * the account) at the ingest and again here; a 5s micro-cache keeps hot
 * reload traffic off the DB.
 */

const express = require('express');
const { pool } = require('../db');
const { publicFeedEvent } = require('../lib/public_feed');

const router = express.Router();

const CACHE_MS = 5000;
let cache = { at: 0, limit: 0, events: null };

function safeParse(s) {
  if (!s) return {};
  try { return JSON.parse(s); } catch (e) { return {}; }
}

router.get('/recent', async (req, res) => {
  const limit = Math.min(Math.max(parseInt(req.query.limit) || 50, 1), 100);
  if (cache.events && cache.limit === limit && Date.now() - cache.at < CACHE_MS) {
    return res.json({ events: cache.events });
  }
  try {
    // LIMIT inlined (sanitized int above): placeholder LIMITs break on some
    // MySQL backends — same pattern as the markets panels fix.
    const [rows] = await pool.execute(
      `SELECT event_type, severity, symbol, title, body, data_json, created_at
       FROM agent_events ORDER BY id DESC LIMIT ${limit}`);
    // Scrubbed on the way OUT as well as on the way in: the ingest has made
    // every event public since it learned to, and the ring holds up to 500
    // rows written before that, some carrying the operator's dollar P&L.
    const events = rows.map(r => publicFeedEvent({
      event_type: r.event_type,
      severity: r.severity,
      symbol: r.symbol || '',
      title: r.title,
      body: r.body || '',
      data: safeParse(r.data_json),
      created_at: r.created_at instanceof Date
        ? r.created_at.toISOString() : String(r.created_at || ''),
    }));
    cache = { at: Date.now(), limit, events };
    res.json({ events });
  } catch (err) {
    console.error('Feed recent error:', err.stack || err.message);
    // `{events:[]}` is the mind-stream saying the agent has thought nothing.
    // Both callers already distinguish the two: dashboard.js's getFeed()
    // mustRead()s this, and the landing page has a drawUnreadable() branch
    // reading "this is a connection problem, not a quiet engine" — which an
    // HTTP 200 with an empty array could never reach.
    res.status(503).json({ error: 'feed_unavailable' });
  }
});

module.exports = router;
