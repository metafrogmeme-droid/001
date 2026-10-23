'use strict';
/**
 * Scam or Safe? Daily — /api/scam-or-safe.
 *
 * Authed, like the duel. The rules and the seal are lib/scam_or_safe.js,
 * which seals through callseal.sealDuelPick. This router adds no second
 * reading of the puzzle, the day, or the verdict.
 *
 * Off unless SCAM_OR_SAFE_V0 is on. A closed flag is a 404, not an empty
 * puzzle: "no request today" would be a claim about the game.
 *
 * The offer has no verdict and no explanation. Both arrive on the response
 * that sealed the call, after the seal verifies. No amount, no full address.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const sos = require('../lib/scam_or_safe');

const router = express.Router();
router.use(authMiddleware);

const pickLimit = rateLimit({ name: 'scam.pick', windowMs: 60000, max: 20, key: userKey });

const CLOSED = 'This game is not open.';
const UNREAD = 'Today\'s request could not be read. Nothing was sealed.';

function send(res, out) {
  if (!out || out.ok === false) {
    if (out && out.error === 'off') return res.status(404).json({ error: CLOSED });
    if (out && out.error === 'pick') return res.status(400).json({ error: 'Call it safe or risky.' });
    if (out && out.error === 'closed') return res.status(409).json({ error: 'Today\'s request has closed. Nothing was sealed.' });
    if (out && out.error === 'moved') {
      return res.status(409).json({ error: 'moved', puzzle_id: out.puzzle_id });
    }
    return res.status(503).json({ error: UNREAD });
  }
  res.setHeader('Cache-Control', 'no-store');
  return res.json(out);
}

router.get('/today', (req, res) => {
  try {
    const key = sos.webPlayerKey(req.user.user_id);
    send(res, sos.offer({ now: new Date().toISOString(), player_key: key }));
  } catch (err) {
    res.status(503).json({ error: UNREAD });
  }
});

router.post('/pick', pickLimit, (req, res) => {
  try {
    const body = req.body || {};
    const key = sos.webPlayerKey(req.user.user_id);
    send(res, sos.sealPick({
      now: new Date().toISOString(),
      pick: body.pick,
      puzzle_id: body.puzzle_id,
      player_key: key,
    }));
  } catch (err) {
    res.status(503).json({ error: UNREAD });
  }
});

module.exports = router;
