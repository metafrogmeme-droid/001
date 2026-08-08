'use strict';
/**
 * Daily Duel — /api/duel. Three rounds a UTC day, drawn from the agent's own
 * live signals and topped up from the majors. The player calls LONG / SHORT /
 * PASS; the agent's own call stays hidden until the player has committed; the
 * market settles it 24 hours after the call was made.
 *
 * Zero setup: any registered user can play immediately. No exchange keys, no
 * bot gateway, no paper balance to fund — which is the point, since the Arena's
 * on-ramp still asks for a trading decision with size attached and this asks
 * only for an opinion.
 *
 * Rules are lib/duel.js (pure); reads and writes are lib/duel_service.js,
 * shared with the Telegram channel so the two surfaces cannot fork the rules.
 *
 * §4: virtual throughout — there is no stake, no wager and no amount of any
 * currency anywhere in this surface. Only counts, percent and market prices.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const svc = require('../lib/duel_service');

const router = express.Router();
router.use(authMiddleware);

const pickLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });

// Same sanitiser philosophy as routes/arena.js: say WHY a read failed without
// letting a driver message, URI or credential reach the client.
function safeReason(err) {
  if (!err) return null;
  let m = String(err.code ? `${err.code}: ${err.message}` : err.message || err).slice(0, 200);
  m = m.replace(/\b[a-z+]+:\/\/[^\s'")]+/gi, '<uri>');
  m = m.replace(/(?:\/[\w.-]+){3,}/g, '<path>');
  m = m.replace(/\b[0-9a-f]{32,}\b/gi, '<hex>');
  m = m.replace(/password[=:]\S+/gi, '<redacted>');
  return m || null;
}

// GET /today — the day's card plus whatever the caller has already called.
router.get('/today', async (req, res) => {
  try {
    res.json(await svc.cardFor(req.user.user_id));
  } catch (err) {
    console.error('Duel today error:', err.stack || err.message);
    res.status(503).json({
      error: 'The day\'s rounds are unavailable',
      reason: err.rcReason || null,
      detail: safeReason(err),
    });
  }
});

// POST /pick { round_id, pick } — commit a call. Write-once, while the round is open.
router.post('/pick', pickLimit, async (req, res) => {
  try {
    const body = req.body || {};
    const out = await svc.placePick(req.user.user_id, Number(body.round_id), body.pick);
    if (!out.ok) {
      const { status, ...rest } = out;
      return res.status(status).json(rest);
    }
    res.json(out);
  } catch (err) {
    console.error('Duel pick error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not record your call', detail: safeReason(err) });
  }
});

// GET /me — the caller's record. Counts and percent; no amount of anything.
router.get('/me', async (req, res) => {
  try {
    const rec = await svc.recordFor(req.user.user_id);
    const handle = await svc.handleFor(req.user.user_id);
    res.json({
      handle,
      window_days: rec.window_days,
      accuracy: rec.accuracy,
      marks: rec.marks,
      streak: rec.streak,
      quests: rec.quests,
      history: rec.entries
        .slice()
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        .slice(0, 60),
      counts_only: true,
      private: true,
    });
  } catch (err) {
    console.error('Duel record error:', err.stack || err.message);
    res.status(500).json({ error: 'Your record is unavailable', detail: safeReason(err) });
  }
});

// GET /season — the caller's standing this calendar month (UTC).
router.get('/season', async (req, res) => {
  try {
    const now = new Date();
    const season = now.toISOString().slice(0, 7);
    const duel = require('../lib/duel');
    const [rounds, picks] = await Promise.all([
      svc.loadRoundsSince(season + '-01'), svc.loadPicks(req.user.user_id),
    ]);
    const entries = duel.scoreEntries(rounds, picks)
      .filter((e) => String(e.day).slice(0, 7) === season);
    res.json({
      season,
      accuracy: duel.accuracy(entries),
      marks: duel.computeMarks(entries),
      counts_only: true,
    });
  } catch (err) {
    console.error('Duel season error:', err.stack || err.message);
    res.status(500).json({ error: 'The season standing is unavailable', detail: safeReason(err) });
  }
});

module.exports = router;
module.exports.RECORD_WINDOW_DAYS = svc.RECORD_WINDOW_DAYS;
