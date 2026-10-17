'use strict';
/**
 * Sign in with Pi: the one endpoint the Pi Browser app (routes/piapp.js) calls.
 *
 *   POST /api/pi/signin  { accessToken }  -> { token, handle }
 *
 * The page gets `accessToken` from the Pi SDK's `Pi.authenticate()`. This
 * route exchanges it with Pi's App Studio (lib/pi_auth.js), once per sign-in,
 * finds or creates the RUNECLAW account for the uid App Studio returns, and
 * answers our own session token. Anything else the request carries (a uid, a
 * username) is ignored: the browser is the end user's to control.
 *
 * A BEARER TOKEN, NOT A COOKIE, for the reason routes/farcaster_auth.js gives:
 * an app inside another app's frame does not get a SameSite=Lax cookie, so a
 * cookie session would work in a tab and fail silently in the Pi Browser.
 * `authMiddleware` reads the Authorization header first, so the whole arena
 * API works unchanged for a caller holding this token. The page keeps it in
 * memory only.
 *
 * Nothing here takes a payment. The Pi SDK's payment callback exists on the
 * page only because `Pi.authenticate` requires one.
 */

const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const piAuth = require('../lib/pi_auth');
const { findOrCreateOAuthUser, signToken } = require('../auth');

const router = express.Router();

/** Unauthenticated by definition, and each call costs a call to Pi and a write. */
router.use(rateLimit({ name: 'pi_auth', windowMs: 60000, max: 20, key: ipKey }));

router.post('/signin', async (req, res) => {
  const accessToken = req.body && req.body.accessToken;
  if (!piAuth.wellFormedToken(accessToken)) {
    return res.status(400).json({ error: 'access_token_required' });
  }

  let verdict;
  try {
    verdict = await piAuth.verifyAccessToken(accessToken, router._deps || {});
  } catch (err) {
    // App Studio could not be asked. Ours to report, not a refusal of the person.
    console.error('Pi App Studio login unavailable:', err.stack || err.message);
    return res.status(503).json({ error: 'verifier_unavailable' });
  }

  if (!verdict.ok) {
    return res.status(401).json({ error: 'pi_rejected', reason: verdict.reason });
  }

  try {
    const user = await findOrCreateOAuthUser({ provider: 'pi', providerId: verdict.uid });
    const [rows] = await pool.execute(
      'SELECT leaderboard_handle FROM users WHERE id = ? LIMIT 1', [user.id]);
    res.json({
      token: signToken(user),
      // null is a real answer: no handle chosen yet, so this account is not on
      // any board. The page says so rather than inventing one.
      handle: (rows[0] && rows[0].leaderboard_handle) || null,
    });
  } catch (err) {
    console.error('Pi signin error:', err.stack || err.message);
    res.status(500).json({ error: 'signin_failed' });
  }
});

module.exports = router;
