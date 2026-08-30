/**
 * Shared-secret authentication for the BOT -> WEB channel.
 *
 * Extracted verbatim from routes/sync.js, which has used it since the sync
 * endpoints existed. It lives here rather than being written a second time
 * because the alternative is another constant-time comparison from memory, and
 * a comparison that is subtly NOT constant-time is indistinguishable from one
 * that is until somebody measures it.
 *
 * One other consumer today: POST /api/auth/validate-token, which binds a
 * Telegram chat_id to an account. That route was anonymous; the argument for
 * leaving it so ("the token IS the credential being checked") is true of a
 * lookup and false of a bind.
 *
 * The secret is read PER REQUEST rather than captured at require() time.
 * sync.js captured it at module scope, so whether the channel worked depended
 * on whether .env had been loaded before this file was first required — and on
 * the bot box .env arrives through the vault restore. A per-request read has no
 * import-order behaviour to reason about. Every existing test sets
 * BOT_SYNC_SECRET before requiring the router, so none of them change.
 */

const crypto = require('crypto');

/**
 * Express middleware. Three outcomes, and the third is the point:
 *
 *   503  the server has no secret configured — it CANNOT say who is calling
 *   403  a secret was presented and it is wrong
 *   next the caller is the bot
 *
 * "Not configured" is not "invalid secret". Reporting the first as the second
 * sends an operator to rotate a credential that was never the problem, which is
 * the same rule /readyz follows with its coarse reason codes.
 *
 * Note server.js refuses to boot without BOT_SYNC_SECRET, so the 503 is not
 * reachable through a normal start. It is reachable when the router is mounted
 * by something that is not server.js — which is exactly what the test suites
 * do — and it costs one branch.
 */
function botAuth(req, res, next) {
  const secret = process.env.BOT_SYNC_SECRET;
  if (!secret) {
    return res.status(503).json({ error: 'Sync not configured (BOT_SYNC_SECRET unset)' });
  }
  const a = Buffer.from(req.headers['x-bot-secret'] || '');
  const b = Buffer.from(secret);
  // timingSafeEqual THROWS on unequal-length buffers — length-check first so a
  // wrong-length secret returns a clean 403 instead of crashing to a 500.
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return res.status(403).json({ error: 'Invalid bot secret' });
  }
  next();
}

module.exports = { botAuth };
