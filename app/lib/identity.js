/**
 * Bot identity resolution for gateway-backed routes.
 *
 * Maps the JWT-authenticated website user to the identity the bot keys its
 * UserStore/portfolios on:
 *   - Telegram-linked account  -> their telegram_id (full feature set)
 *   - web-only account         -> "web:<user_id>" (auto-provisioned by the
 *     bot gateway as a PAPER-ONLY trader; structurally locked out of live)
 *
 * The identity is always resolved server-side from the DB — the browser can
 * never choose who it acts as. Live trading, exchange credentials and live
 * controls intentionally do NOT use this fallback: they stay Telegram-gated
 * (routes/controls.js, routes/credentials.js).
 */

const { pool } = require('../db');

async function resolveBotIdentity(req) {
  const uid = req.user.user_id;
  const [rows] = await pool.execute(
    'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
  const u = rows[0];
  if (u && u.telegram_linked && u.telegram_id) {
    return { id: String(u.telegram_id), linked: true, email: u.email || '' };
  }
  return { id: `web:${uid}`, linked: false, email: (u && u.email) || '' };
}

/**
 * RC-2026-025 — the step-up and the action must address the SAME subject.
 *
 * Every money path here has the same shape: the 2FA step-up reads
 * `totp_enabled`/`totp_secret` for `req.user.user_id`, and the action is then
 * performed as a `telegram_id`. Those are the same subject only while nothing
 * can put ANOTHER account's telegram_id on your row — and that property was
 * relied upon in three route files while being stated in none of them.
 *
 * Today no path writes a foreign telegram_id: RC-2026-001 closed the
 * unauthenticated bind, the bot-secret route refuses an id already on another
 * row, and `idx_users_telegram_id` makes the collision impossible at the
 * storage layer. So this returns null on every real request. It is a latent
 * defect made explicit, not a live one — the next route that writes
 * telegram_id, or a migration that repairs rows by hand, would otherwise
 * re-open a 2FA bypass on a money path with nothing to catch it.
 *
 * Asserting the invariant is deliberately chosen over re-reading the step-up
 * factors for the resolved identity: it is cheaper (one indexed lookup, no
 * change to how 2FA is evaluated) and it FAILS LOUDLY rather than silently
 * gating on someone else's factors.
 *
 * @param {string} telegramId  the id the action will be performed as
 * @param {number|string} uid  req.user.user_id — the account presenting 2FA
 * @returns {Promise<null | {status:number, body:object}>} null = allowed
 */
async function foreignIdentityBlock(telegramId, uid) {
  const id = String(telegramId || '').trim();
  // `web:<uid>` is the caller by construction — resolveBotIdentity built it
  // from this very uid, so there is no second subject to disagree with.
  if (!id || id.startsWith('web:')) return null;
  const [rows] = await pool.execute(
    'SELECT id FROM users WHERE telegram_id = ?', [id]);
  const owner = rows[0];
  if (!owner || String(owner.id) !== String(uid)) {
    return {
      status: 403,
      body: {
        error: 'identity_mismatch',
        detail: 'This account is not linked to that Telegram identity. '
              + 'Re-link your account and try again.',
      },
    };
  }
  return null;
}

module.exports = { resolveBotIdentity, foreignIdentityBlock };
