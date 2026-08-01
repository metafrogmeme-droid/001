'use strict';
/**
 * F-15 for the website: an error message a stranger may read.
 *
 * `POST /api/tool/invoke` is public and unauthenticated (mounted at
 * server.js without authMiddleware). It runs MCP tool handlers and returned
 * the raw failure straight to the caller:
 *
 *     error: `Tool failed: ${String(e.message || e).slice(0, 200)}`
 *
 * Those handlers do `pool.execute` and `getGateway` calls. A database error
 * carries connection and schema detail; a gateway error carries the internal
 * URL it tried. Truncating to 200 characters bounds the size of a leak, not
 * whether one happens.
 *
 * This is the same rule the bot enforces with `_safe_exc_text` — F-15 says no
 * secret or internal config reaches user-facing text — and the website had no
 * equivalent. The bot's version also HTML-escapes because it writes into a
 * Telegram HTML message; this one does not, because it goes into a JSON body
 * where escaping would corrupt the value rather than protect anything.
 *
 * Deliberately keeps the useful part. Tools throw real validation errors
 * ("text is required") that a caller needs to see, so this scrubs rather than
 * blanks — a generic "something went wrong" would trade a leak for a support
 * burden.
 */

/** key=value / key: value secrets, however they are spelled. */
const INLINE_SECRET = new RegExp(
  '\\b(' + [
    'api[_-]?key', 'apikey', 'secret', 'token', 'password', 'passwd', 'pwd',
    'passphrase', 'authorization', 'auth', 'bearer', 'private[_-]?key',
    'session', 'cookie', 'credential', 'access[_-]?key',
  ].join('|') + ')(\\s*[=:]\\s*)(\\S+)',
  'gi',
);

/** A bare URL can carry credentials in its query string. Keep the host. */
const URL_QUERY = /(https?:\/\/[^\s?]+)\?\S*/gi;

/** Connection strings name the host, the database and often the user. */
const CONN_STRING = /\b[a-z][a-z0-9+.-]*:\/\/[^\s@]*@\S+/gi;

/** Absolute filesystem paths disclose the deployment layout. */
const ABS_PATH = /(?:^|\s)(\/(?:home|root|usr|var|etc|opt|srv)\/\S*)/g;

const LIMIT = 200;

/**
 * An error's message, safe to put in a response body.
 *
 * Order matters: redact BEFORE truncating, or a 200-character cut could slice
 * a secret in half and leave the first half in place. Never throws — a
 * scrubber that raises inside a catch block turns a handled failure into an
 * unhandled one.
 *
 * @param {unknown} err
 * @param {number} [limit]
 * @returns {string} scrubbed text, or "" when there is nothing usable
 */
function safeErrorText(err, limit = LIMIT) {
  let msg;
  try {
    msg = err && err.message ? String(err.message) : String(err == null ? '' : err);
  } catch (_) {
    return '';
  }
  if (!msg) return '';
  try {
    msg = msg.replace(INLINE_SECRET, '$1$2***REDACTED***');
    msg = msg.replace(CONN_STRING, '***REDACTED***');
    msg = msg.replace(URL_QUERY, '$1?***');
    msg = msg.replace(ABS_PATH, ' ***path***');
    msg = msg.replace(/\s+/g, ' ').trim();
  } catch (_) {
    // A regex that somehow failed must not fall through to the RAW string.
    return '';
  }
  const n = Math.max(1, Number(limit) || LIMIT);
  return msg.length > n ? msg.slice(0, n) : msg;
}

module.exports = { safeErrorText, LIMIT };
