'use strict';
/**
 * Is this Telegram Mini App launch real, and whose is it?
 *
 * A Mini App opened from the Duel bot receives `initData`, a query string
 * Telegram signs. The page's word for who it is counts for nothing; this
 * file checks Telegram's signature, the way Telegram documents for a third
 * party that does NOT hold the bot's token (core.telegram.org/bots/webapps,
 * "Validating data for Third-Party Use", read 2026-10-22):
 *
 *   data-check-string  "<bot_id>:WebAppData", a line feed, then every field
 *                      except `hash` and `signature`, sorted by name, as
 *                      key=<value>, joined by line feeds
 *   signature          base64url Ed25519 of that string, under Telegram's
 *                      public key (production below; the test environment
 *                      has its own)
 *
 * The website never holds DUEL_BOT_TOKEN: the bot id is public (it is the
 * part of the token before the colon, and Telegram shows it), so the check
 * needs nothing secret. `hash`, the bot-token HMAC, is ignored here.
 *
 * Three answers, as with lib/pi_auth.js and lib/siwf.js:
 *   { ok: true, user, auth_date }   Telegram signed this launch, recently
 *   { ok: false, reason }           it did not, or not for this bot, or not lately
 *   { ok: false, reason: 'unconfigured' }   no bot id to check against: ours, not theirs
 */

const crypto = require('crypto');

/** Telegram's Ed25519 keys for Mini App launch data, as the docs print them (2026-10-22). */
const TELEGRAM_KEYS = Object.freeze({
  production: 'e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d',
  test: '40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec',
});
/** A launch older than this is not a sign-in: initData is a bearer credential, and a short life bounds a replay. */
const MAX_AGE_S = 3600;
/** A clock a little ahead of ours is a clock, not a forgery; this far ahead is neither. */
const FUTURE_SLACK_S = 300;
const BOT_ID_RE = /^\d{5,20}$/;
const INIT_DATA_MAX = 4096;
/** DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410): 32 raw key bytes follow. */
const ED25519_SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');

/** The bot whose launches this site accepts: DUEL_BOT_ID, the digits before the colon of the Duel bot's token. */
function botIdOf(env = process.env) {
  const id = String(env.DUEL_BOT_ID || '').trim();
  return BOT_ID_RE.test(id) ? id : null;
}

/** Which of Telegram's keys: production, unless the operator names Telegram's test environment. */
function keyOf(env = process.env) {
  return String(env.TELEGRAM_WEBAPP_ENV || '').trim() === 'test' ? TELEGRAM_KEYS.test : TELEGRAM_KEYS.production;
}

function publicKey(hex) {
  return crypto.createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(hex, 'hex')]), format: 'der', type: 'spki' });
}

/** The string Telegram signed, from the launch's own fields. */
function dataCheckString(botId, params) {
  const pairs = [...params].filter(([k]) => k !== 'hash' && k !== 'signature');
  pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `${botId}:WebAppData\n${pairs.map(([k, v]) => `${k}=${v}`).join('\n')}`;
}

/**
 * Check one launch. `initData` is Telegram.WebApp.initData as the page
 * received it. opts: { botId, keyHex, now (ms) }.
 */
function verifyInitData(initData, { botId = botIdOf(), keyHex = keyOf(), now = Date.now() } = {}) {
  if (!botId) return { ok: false, reason: 'unconfigured' };
  const raw = typeof initData === 'string' ? initData : '';
  if (!raw || raw.length > INIT_DATA_MAX) return { ok: false, reason: 'malformed' };
  const params = new URLSearchParams(raw);
  const keys = [...params.keys()];
  // A field twice is two answers to one question: which one was signed is not ours to guess.
  if (new Set(keys).size !== keys.length) return { ok: false, reason: 'malformed' };
  const sigText = params.get('signature');
  if (!sigText || !/^[A-Za-z0-9_-]{86}$/.test(sigText)) return { ok: false, reason: 'unsigned' };
  const sig = Buffer.from(sigText, 'base64url');
  if (sig.length !== 64) return { ok: false, reason: 'unsigned' };
  let good = false;
  try {
    good = crypto.verify(null, Buffer.from(dataCheckString(botId, params), 'utf8'), publicKey(keyHex), sig);
  } catch (e) {
    good = false;
  }
  if (!good) return { ok: false, reason: 'bad_signature' };
  const authDate = Number(params.get('auth_date'));
  if (!Number.isInteger(authDate) || authDate <= 0) return { ok: false, reason: 'malformed' };
  const age = Math.floor(now / 1000) - authDate;
  if (age > MAX_AGE_S) return { ok: false, reason: 'expired' };
  if (age < -FUTURE_SLACK_S) return { ok: false, reason: 'malformed' };
  let user = null;
  try { user = JSON.parse(params.get('user') || 'null'); } catch (e) { user = null; }
  const id = user && (typeof user.id === 'number' || typeof user.id === 'string') ? String(user.id) : '';
  if (!/^\d{1,20}$/.test(id)) return { ok: false, reason: 'no_user' };
  return {
    ok: true,
    auth_date: authDate,
    user: {
      id,
      username: typeof user.username === 'string' ? user.username.slice(0, 64) : null,
      language_code: typeof user.language_code === 'string' ? user.language_code.slice(0, 16) : null,
    },
  };
}

module.exports = { TELEGRAM_KEYS, MAX_AGE_S, botIdOf, keyOf, dataCheckString, verifyInitData };
