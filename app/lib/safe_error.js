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
 *
 * THE VOCABULARY IS NOT THIS FILE'S. It used to be: four patterns written here
 * beside the bot's eight, "wider on labels and narrower on token shapes" in
 * `secret_shapes.py`'s own words, which is a second answer to one question.
 * Driven, this file published a Telegram bot token, a bare `sk-…`/`xai-…` key,
 * a JWT, `RUNECLAW_SECRETS_KEY=`, `WEB3_SIGNER_PRIVATE_KEY=`, `WEB_CREDS_KEY=`
 * and `api key: bg_…`, and turned `Authorization: Bearer sk-ant-…` into
 * `Authorization: ***REDACTED*** sk-ant-…` — the label redacted, the key
 * printed, under a marker that reads as proof the line was scrubbed. The
 * label list matched `authorization` and then `(\S+)` took the word *Bearer*
 * as the value. The shared rows come from `./secret_shapes` now.
 *
 * WHAT STAYS HERE, and why it is not a second vocabulary: three shapes that
 * belong to an ERROR BODY and not to a chat card. A connection string, an
 * absolute path and the label words a driver uses (`session=`, `cookie=`,
 * `pwd=`) are diagnostics nobody's card prints, and the whole query string
 * goes rather than its credential-named parameters — the bot draws the same
 * line with `drop_url_queries`, its exception path's stricter rule. Each is
 * declared below with its reason; none of them re-states a shared row.
 */

const { scrubSecrets, looksLikeCredential, REDACTED } = require('./secret_shapes');

/**
 * Label words a DRIVER spells that no card does. The shared table's
 * `named_key_value` row holds the seven a user-facing surface can meet; these
 * are the rest — and `apikey` is NOT among them, because that row's
 * `api[_-]?key` already matches a bare one, which the guard below found by
 * refusing any shared spelling in this file. The value must look like a
 * credential before it goes —
 * which is what keeps `Authorization: Bearer ***REDACTED***` from losing its
 * scheme word to a second pass.
 */
const DRIVER_LABEL = new RegExp(
  '\\b(' + [
    'passwd', 'pwd', 'authorization', 'auth',
    'private[_-]?key', 'session', 'cookie', 'access[_-]?key',
  ].join('|') + ')(\\s*[=:]\\s*)([^\\s\'"]+)',
  'gi',
);

/** Connection strings name the host, the database and often the user. */
const CONN_STRING = /\b[a-z][a-z0-9+.-]*:\/\/[^\s@]*@\S+/gi;

/** A bare URL can carry credentials in its query string. Keep the host. */
const URL_QUERY = /(https?:\/\/[^\s?]+)\?\S*/gi;

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
 * The second argument takes a NUMBER (a character limit) or a STRING (the
 * sentence the reader gets instead of the driver's words). It had only the
 * first meaning, and two callers in `routes/agents.js` passed a sentence:
 * `Number('Your agents could not be read') || LIMIT` is 200, so the sentence
 * vanished and the driver's text was published under it — a caller's own
 * defence, silently discarded. Both of those callers `console.error` the stack
 * first, so the diagnosis stays in the log where it belongs and the reader
 * gets the sentence its author wrote.
 *
 * @param {unknown} err
 * @param {number|string} [limitOrMessage]
 * @returns {string} scrubbed text, or "" when there is nothing usable
 */
function safeErrorText(err, limitOrMessage = LIMIT) {
  const fixed = typeof limitOrMessage === 'string' ? limitOrMessage : null;
  const limit = fixed === null ? limitOrMessage : LIMIT;
  let msg;
  try {
    msg = err && err.message ? String(err.message) : String(err == null ? '' : err);
  } catch (_) {
    return fixed === null ? '' : fixed;
  }
  if (fixed !== null) return fixed;
  if (!msg) return '';
  try {
    msg = scrubSecrets(msg);
    msg = msg.replace(DRIVER_LABEL, (whole, label, sep, value) => (
      looksLikeCredential(value) ? `${label}${sep}${REDACTED}` : whole
    ));
    msg = msg.replace(CONN_STRING, REDACTED);
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
