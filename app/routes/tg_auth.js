'use strict';
/**
 * POST /api/tgapp/signin — the Telegram Mini App's sign-in.
 *
 *   { init_data }  ->  { token, handle }
 *
 * `init_data` is Telegram.WebApp.initData as the Duel's Mini App received it.
 * lib/tg_webapp.js checks Telegram's Ed25519 signature over it with
 * Telegram's public key and the Duel bot's public id (DUEL_BOT_ID), so this
 * site never holds the bot's token. A launch older than an hour is refused.
 *
 * THE SAME DOOR AS "LOG IN WITH TELEGRAM". The verified Telegram id finds or
 * creates the account the website's Telegram login finds or creates
 * (auth.js findOrCreateOAuthUser, provider 'telegram'), so a person is one
 * player whichever of the two they used. The session is a bearer token the
 * page holds in memory (arena-session.js), as the Farcaster and Pi apps do.
 *
 * NOT SINGLE-USE. A launch's initData is the same on every reload of that
 * launch, so a nonce would sign a person out on reload. The hour bound is
 * what limits a launch that leaked; a captured one is as good as the account
 * for that hour, which is Telegram's own model for its signed launch data.
 */

const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const tg = require('../lib/tg_webapp');
const { findOrCreateOAuthUser, signToken } = require('../auth');

const router = express.Router();

/** Unauthenticated by definition, so capped hard, as /api/farcaster is. */
router.use(rateLimit({ name: 'tgapp_auth', windowMs: 60000, max: 20, key: ipKey }));

router.post('/signin', async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const verdict = tg.verifyInitData((req.body || {}).init_data, { botId: tg.botIdOf(), keyHex: tg.keyOf() });
  // No bot id configured: every launch would fail, and that is ours to fix,
  // not a refusal of the person (503, not 401).
  if (!verdict.ok && verdict.reason === 'unconfigured') {
    return res.status(503).json({ error: 'tgapp_unconfigured' });
  }
  if (!verdict.ok) return res.status(401).json({ error: 'tgapp_rejected', reason: verdict.reason });
  try {
    const user = await findOrCreateOAuthUser({ provider: 'telegram', providerId: verdict.user.id, email: null });
    const [rows] = await pool.execute('SELECT leaderboard_handle FROM users WHERE id = ? LIMIT 1', [user.id]);
    res.json({ token: signToken(user), handle: rows && rows[0] ? rows[0].leaderboard_handle || null : null });
  } catch (err) {
    console.error('Telegram Mini App sign-in error:', err.name || 'Error');
    res.status(503).json({ error: 'tgapp_unavailable', exception: err.name || 'Error' });
  }
});

module.exports = router;
