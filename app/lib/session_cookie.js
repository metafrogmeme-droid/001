'use strict';
/**
 * The session as a cookie the page cannot read.
 *
 * M14's other half. `script-src` no longer admits arbitrary inline script, but
 * the bearer token still lives in localStorage, which means any script that
 * DOES run — a compromised dependency, a future CSP gap, a `javascript:` sink —
 * can read the session and walk off with it. A token in an HttpOnly cookie is
 * unreadable to every one of them. The audit offers the two measures as
 * independent breaks in the same chain; this is the second.
 *
 * TWO COOKIES, AND ONLY ONE IS A SECRET
 *
 *   rc_jwt   HttpOnly — the token. No script can read it, by construction.
 *   rc_auth  readable — the literal string "1", and nothing else ever.
 *
 * The second exists because `LOGGED_IN` drives the entire UI: which nav
 * renders, whether a panel shows a login gate, whether the dashboard boots at
 * all. It was derived from "can I see a token", and an HttpOnly cookie makes
 * that question unanswerable in JS — so every user would render as logged out
 * while being perfectly authenticated, which is a worse bug than the one being
 * fixed. `rc_auth` answers "does a session exist" and carries nothing an
 * attacker could use; forging it logs you into a UI shell whose every request
 * still 401s.
 *
 * WHY NO cookie-parser DEPENDENCY. Parsing a header of `k=v; k=v` is the
 * fifteen lines below. Adding a package would put a new entry through the npm
 * advisory ratchet forever, for a function with no edge cases worth
 * outsourcing.
 *
 * CSRF. `SameSite=Lax` is what makes a cookie session safe here: the browser
 * withholds it from cross-site POSTs, which is every shape a CSRF takes
 * against this app (all state changes are POSTs from same-origin fetch). Lax
 * rather than Strict so a top-level GET arriving from an email link — verify,
 * reset, the Telegram OAuth return — still carries the session and does not
 * dump a just-verified user on a logged-out page.
 *
 * READING ORDER IS BEARER FIRST, DELIBERATELY. Non-browser callers (the MCP
 * tools, curl, the Telegram bot's link flow) authenticate with an Authorization
 * header and must keep working exactly as before. The cookie is a fallback, so
 * this module can ship without changing one existing caller's behaviour.
 */

const SESSION_COOKIE = 'rc_jwt';
const FLAG_COOKIE = 'rc_auth';

// Matches the JWT's own lifetime. A cookie that outlives its token leaves the
// UI claiming a session the server has already stopped honouring; a cookie
// that dies first logs out a user whose token was still good.
const DEFAULT_MAX_AGE_S = 30 * 24 * 60 * 60;

/** `k=v; k=v` → object. Never throws; an unparseable header is no cookies. */
function parseCookies(header) {
  const out = {};
  if (!header || typeof header !== 'string') return out;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 1) continue;
    const k = part.slice(0, eq).trim();
    if (!k || Object.prototype.hasOwnProperty.call(out, k)) continue;
    let v = part.slice(eq + 1).trim();
    if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1);
    try { out[k] = decodeURIComponent(v); } catch (e) { out[k] = v; }
  }
  return out;
}

/**
 * Is this request on HTTPS?
 *
 * `Secure` on a cookie served over plain HTTP means the browser drops it
 * silently — which on a local dev box would look exactly like "login is
 * broken" with nothing in any log. Behind the proxy `trust proxy` is set, so
 * `req.secure` already reflects X-Forwarded-Proto.
 */
function isSecure(req) {
  if (req && req.secure) return true;
  const proto = req && req.headers && req.headers['x-forwarded-proto'];
  return typeof proto === 'string' && proto.split(',')[0].trim() === 'https';
}

function serialize(name, value, { maxAge, httpOnly, secure }) {
  const parts = [`${name}=${encodeURIComponent(value)}`, 'Path=/', 'SameSite=Lax'];
  // Max-Age=0 with an empty value is the expiry form; both are set so a
  // browser ignoring one honours the other.
  parts.push(`Max-Age=${maxAge}`);
  if (maxAge === 0) parts.push('Expires=Thu, 01 Jan 1970 00:00:00 GMT');
  if (httpOnly) parts.push('HttpOnly');
  if (secure) parts.push('Secure');
  return parts.join('; ');
}

function append(res, cookie) {
  const prev = res.getHeader('Set-Cookie');
  const list = prev ? (Array.isArray(prev) ? prev.slice() : [prev]) : [];
  list.push(cookie);
  res.setHeader('Set-Cookie', list);
}

/** Issue the session pair alongside whatever the handler returns in its body. */
function setSession(req, res, token, { maxAge = DEFAULT_MAX_AGE_S } = {}) {
  if (!token) return;
  const secure = isSecure(req);
  append(res, serialize(SESSION_COOKIE, token, { maxAge, httpOnly: true, secure }));
  // Deliberately NOT HttpOnly: this one is for the page to read.
  append(res, serialize(FLAG_COOKIE, '1', { maxAge, httpOnly: false, secure }));
}

/** Expire both. Called on logout, beside the epoch bump that kills the token. */
function clearSession(req, res) {
  const secure = isSecure(req);
  append(res, serialize(SESSION_COOKIE, '', { maxAge: 0, httpOnly: true, secure }));
  append(res, serialize(FLAG_COOKIE, '', { maxAge: 0, httpOnly: false, secure }));
}

/**
 * The caller's raw JWT, from the Authorization header or failing that the
 * cookie — or null.
 *
 * Header first so no existing API client changes behaviour. Returning null
 * rather than '' matters: the callers branch on truthiness and an empty string
 * would be verified as a token and fail with "Invalid token" instead of
 * "Missing token".
 */
function tokenFromRequest(req) {
  const auth = req && req.headers && req.headers.authorization;
  if (auth && auth.startsWith('Bearer ')) {
    const raw = auth.slice(7).trim();
    if (raw) return raw;
  }
  const jar = parseCookies(req && req.headers && req.headers.cookie);
  return jar[SESSION_COOKIE] || null;
}

module.exports = { parseCookies, tokenFromRequest, setSession, clearSession,
                   isSecure, SESSION_COOKIE, FLAG_COOKIE, DEFAULT_MAX_AGE_S };
