'use strict';
/**
 * Agent identity — /api/agents. Claim a slug, list the ones you own.
 *
 * Everything here is authenticated and user-scoped: a claim binds a slug to
 * the caller. The PUBLIC read of a claim lives in routes/public_agent_identity
 * so that the two auth postures never share a module.
 *
 * §4: nothing on this surface carries an amount. A claim is a name, a
 * timestamp and a hash.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const agents = require('../lib/agents');
const { safeErrorText } = require('../lib/safe_error');

const router = express.Router();
router.use(authMiddleware);
// Claiming writes a row and consults two catalogues, so it is the expensive
// one; the shared limiter is sized for the write.
router.use(rateLimit({ windowMs: 60_000, max: 20, key: userKey }));

// GET /api/agents — the slugs this caller owns.
router.get('/', async (req, res) => {
  try {
    res.json({ agents: await agents.forUser(req.user.user_id) });
  } catch (err) {
    console.error('Agents list error:', err.stack || err.message);
    res.status(503).json({ error: safeErrorText(err, 'Your agents could not be read') });
  }
});

// POST /api/agents  { slug, display_name? } — claim a slug.
//
// A refusal names WHICH namespace the slug is already in, because "taken" and
// "the catalogue could not be read" are different facts and only one of them
// means try a different name. `lib/agents.slugTaken` refuses on an unreadable
// catalogue rather than allowing the claim: the check exists to stop two
// different agents' trades merging under one slug on the public record, and an
// unanswered question is not a free slug.
router.post('/', async (req, res) => {
  try {
    const body = req.body || {};
    const r = await agents.claim(
      req.user.user_id, body.slug, body.display_name);
    if (!r.ok) {
      // 503 for the two "could not check" codes — a transient catalogue read
      // failure is not the caller's bad request, and answering 400 would tell
      // them to change a slug that is very probably fine.
      const transient = r.code === 'catalogue_unreadable' || r.code === 'community_unreadable';
      return res.status(transient ? 503 : 400).json({ error: r.error, code: r.code });
    }
    res.status(201).json({ ok: true, agent: r.agent,
      note: 'The claim is sealed. Its hash rides into today\'s Merkle root and is '
        + 'anchored on Base once that day completes — after which the claim date '
        + 'rests on a block timestamp, not on our clock.' });
  } catch (err) {
    console.error('Agent claim error:', err.stack || err.message);
    res.status(503).json({ error: safeErrorText(err, 'The claim could not be recorded') });
  }
});

module.exports = router;
