'use strict';
/**
 * Sign In With Farcaster — the two endpoints a Mini App calls.
 *
 *   POST /api/farcaster/nonce   -> { nonce }
 *   POST /api/farcaster/signin  -> { token, fid, handle }
 *
 * The checks all live in lib/siwf.js and are tested against every way in;
 * this file is the plumbing: a single-use nonce store, the find-or-create that
 * turns a verified fid into a RUNECLAW user, and a session token.
 *
 * WHY A BEARER TOKEN AND NOT A COOKIE. The session cookie is `SameSite=Lax`
 * with no `Partitioned` attribute, so a browser withholds it inside a
 * third-party frame — which is exactly where a Mini App runs. A cookie-based
 * session here would work when tested in a tab and fail silently in Warpcast,
 * and the failure would look like a login that "didn't take". `authMiddleware`
 * has read the Authorization header first since the session work, so the whole
 * authenticated arena API works unchanged for a caller holding this token.
 *
 * The Mini App keeps it in memory only. `embed_frame_policy` forbids
 * localStorage on a framed page and that is the right rule twice over here: a
 * session token in storage on a page anyone may frame is a token any framing
 * page has a shot at.
 */

const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const siwf = require('../lib/siwf');
const { findOrCreateOAuthUser, signToken } = require('../auth');

const router = express.Router();

/**
 * Sign-in is unauthenticated by definition, so it is capped hard.
 *
 * Tighter than the public boards (120/min) because this one costs a database
 * write and an outbound verification per call, and because a legitimate user
 * signs in once per session rather than polling.
 */
router.use(rateLimit({ windowMs: 60000, max: 20, key: ipKey }));

/**
 * The nonce store, backed by a table rather than memory.
 *
 * A `Set` in process would issue on one replica and miss on the next, so
 * sign-in would succeed or fail depending on which container answered — the
 * kind of intermittent failure nobody can reproduce.
 *
 * `consume` marks rather than deletes, and the distinction is deliberate: a
 * deleted nonce is indistinguishable from one that never existed, so a replay
 * attempt and a fabricated nonce would look identical in the logs. Marked, we
 * can tell "this was used already" from "we never issued this".
 */
const nonceStore = {
  /**
   * A fresh nonce, retried on the one collision that can happen.
   *
   * `nonce` is the primary key and 128 bits of CSPRNG makes a collision
   * vanishingly unlikely — but vanishingly unlikely is not handled, and an
   * unhandled duplicate key here is a 500 on a sign-in that should simply have
   * drawn again. The retry is one line; the alternative is an error nobody can
   * reproduce.
   *
   * Only ER_DUP_ENTRY is absorbed. Every other database fault rethrows, so a
   * real outage still reaches the caller as one instead of being retried three
   * times and reported as a collision.
   */
  async issue(nowMs) {
    const now = new Date(nowMs || Date.now());
    let lastErr = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const nonce = siwf.newNonce();
      try {
        await pool.execute(
          'INSERT INTO siwf_nonces (nonce, created_at, expires_at) VALUES (?, ?, ?)',
          [nonce, now, new Date(now.getTime() + siwf.NONCE_TTL_MS)]);
        return nonce;
      } catch (err) {
        if (!err || (err.code !== 'ER_DUP_ENTRY' && err.errno !== 1062)) throw err;
        lastErr = err;
      }
    }
    throw lastErr || new Error('siwf_nonce_collision');
  },

  /** true exactly once per issued, unexpired nonce. */
  async consume(nonce, nowMs) {
    const now = new Date(nowMs || Date.now());
    const [rows] = await pool.execute(
      'SELECT nonce, expires_at, used_at FROM siwf_nonces WHERE nonce = ? LIMIT 1', [nonce]);
    const row = rows[0];
    if (!row) return false;                                   // never issued
    if (row.used_at) return false;                            // replay
    if (new Date(row.expires_at).getTime() <= now.getTime()) return false;
    // The UPDATE is conditional on used_at still being NULL, so two requests
    // racing the same nonce cannot both pass: the database picks one and the
    // loser sees zero affected rows. A read-then-write without this condition
    // is a check-then-act, and the whole point of a nonce is that it cannot be
    // spent twice.
    const [res] = await pool.execute(
      'UPDATE siwf_nonces SET used_at = ? WHERE nonce = ? AND used_at IS NULL', [now, nonce]);
    return (res && (res.affectedRows === undefined || res.affectedRows > 0)) === true;
  },
};

/** The hosts a SIWF message may name. Empty means nothing is accepted. */
function allowedDomains(req) {
  const out = [];
  try {
    const r = require('../lib/public_origin').resolve(req, process.env) || {};
    if (r.origin) out.push(r.origin);
  } catch (e) { /* fall through to the explicit list */ }
  const extra = String(process.env.SIWF_ALLOWED_DOMAINS || '').trim();
  if (extra) for (const d of extra.split(/[\s,]+/)) if (d) out.push(d);
  return out;
}

// POST /api/farcaster/nonce — a fresh single-use nonce for a sign-in attempt.
router.post('/nonce', async (req, res) => {
  try {
    res.json({ nonce: await nonceStore.issue() });
  } catch (err) {
    console.error('SIWF nonce error:', err.stack || err.message);
    // No nonce means no sign-in. Saying so is better than handing back one we
    // did not record, which would fail verification later for no visible reason.
    res.status(503).json({ error: 'nonce_unavailable' });
  }
});

/**
 * POST /api/farcaster/signin — { message, signature } -> a session.
 *
 * Every rejection returns 401 with a REASON CODE from a fixed vocabulary. The
 * codes name what failed (`domain_mismatch`, `unknown_or_used_nonce`) because
 * an operator staring at a sign-in that will not complete needs to know which
 * of three checks refused it — and none of them leaks anything the caller did
 * not already send us.
 */
router.post('/signin', async (req, res) => {
  const { message, signature } = req.body || {};
  const domains = allowedDomains(req);

  // No configured origin means every domain check would fail anyway. Answering
  // 503 says "we cannot do this" rather than 401's "you are not who you say" —
  // a misconfiguration reported as a rejected identity sends the operator
  // looking at the wrong thing entirely.
  if (!domains.length) {
    console.error('SIWF signin: no public origin resolved and SIWF_ALLOWED_DOMAINS unset');
    return res.status(503).json({ error: 'siwf_unconfigured' });
  }

  let verdict;
  try {
    verdict = await siwf.verifySignIn({ message, signature }, {
      allowedDomains: domains,
      store: nonceStore,
    });
  } catch (err) {
    // The verifier could not be reached. NOT a rejection — telling a
    // legitimate user their signature was refused when our dependency is down
    // is a lie that looks identical to a real refusal.
    console.error('SIWF verify unavailable:', err.stack || err.message);
    return res.status(503).json({ error: 'verifier_unavailable' });
  }

  if (!verdict.ok) {
    return res.status(401).json({ error: 'siwf_rejected', reason: verdict.reason });
  }

  try {
    const user = await findOrCreateOAuthUser({
      provider: 'farcaster',
      providerId: String(verdict.fid),
    });
    const [rows] = await pool.execute(
      'SELECT leaderboard_handle FROM users WHERE id = ? LIMIT 1', [user.id]);
    res.json({
      token: signToken(user),
      fid: verdict.fid,
      // null is a real answer: this account has not chosen a handle, so it is
      // invisible on every board until it does. The client says so rather than
      // inventing one.
      handle: (rows[0] && rows[0].leaderboard_handle) || null,
    });
  } catch (err) {
    console.error('SIWF signin error:', err.stack || err.message);
    res.status(500).json({ error: 'signin_failed' });
  }
});

module.exports = router;
module.exports._nonceStore = nonceStore;
module.exports._allowedDomains = allowedDomains;
