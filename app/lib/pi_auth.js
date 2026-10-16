'use strict';
/**
 * Is this Pi access token real, and whose is it?
 *
 * A Pi Browser app signs a person in with the Pi SDK: `Pi.authenticate()`
 * hands the page an access token and a user object. The page's word for who
 * that is counts for nothing (a page can send any uid it likes). The token is
 * exchanged with Pi's App Studio, the way Pi requires of every app
 * (pi-apps.github.io/pi-sdk-docs, quick-start/genai/Authentication):
 *
 *   POST https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com/pi/auth/v1/login
 *   { "accessToken": "<from Pi.authenticate>" }
 *   200 -> { "sessionToken": "...", "user": { "uid": "...", "username": "..." } }
 *
 * App Studio checks the token against the Pi Platform before it answers, so
 * the uid in THAT answer is the only identity this app trusts. Pi's guidance
 * is explicit that an app must not call the Platform's /v2/me itself; the SDK
 * does that internally. The exchange happens here, on our server, once per
 * sign-in, and our own session is issued from its result (routes/pi_auth.js).
 *
 * Three answers, not two, as with SIWF (lib/siwf.js):
 *   { ok: true, uid, username }  App Studio vouched for the token.
 *   { ok: false, reason }        App Studio refused it: not who they say.
 *   throws                       App Studio could not be asked, or answered
 *                                nothing usable. NOT a refusal: telling a real
 *                                person their sign-in was rejected while our
 *                                dependency is down is a lie that looks exactly
 *                                like a real refusal.
 */

const DEFAULT_ORIGIN = 'https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com';
const LOGIN_PATH = '/pi/auth/v1/login';
const TIMEOUT_MS = 8000;
/** Pi's uids look like `a1111111-aaaa-bbbb-2222-ccccccc3333d`; the column is 64 wide. */
const UID_RE = /^[A-Za-z0-9_-]{1,64}$/;
/** An access token is a long random string; anything far outside that is not one. */
const TOKEN_RE = /^[A-Za-z0-9._~+/=-]{8,2048}$/;

function authOrigin(env = process.env) {
  return String(env.PI_AUTH_ORIGIN || DEFAULT_ORIGIN).trim().replace(/\/+$/, '');
}

function wellFormedToken(token) {
  return typeof token === 'string' && TOKEN_RE.test(token);
}

async function verifyAccessToken(accessToken, deps = {}) {
  if (!wellFormedToken(accessToken)) return { ok: false, reason: 'malformed_token' };
  const fetchImpl = deps.fetch || globalThis.fetch;
  const origin = deps.authOrigin || authOrigin();
  const r = await fetchImpl(`${origin}${LOGIN_PATH}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify({ accessToken }),
    signal: AbortSignal.timeout(deps.timeoutMs || TIMEOUT_MS),
  });
  if (r.status === 401 || r.status === 403) return { ok: false, reason: 'token_rejected' };
  if (!r.ok) throw new Error(`Pi App Studio login answered ${r.status}`);
  let body;
  try {
    body = await r.json();
  } catch (_) {
    throw new Error('Pi App Studio login answered a body that is not JSON');
  }
  const user = body && body.user;
  const uid = user && typeof user.uid === 'string' ? user.uid.trim() : '';
  // A 200 with no usable uid is App Studio answering nothing we can stand on,
  // so it is unavailable, not a refusal of the person.
  if (!UID_RE.test(uid)) throw new Error('Pi App Studio login answered without a usable uid');
  const username = typeof user.username === 'string' ? user.username.slice(0, 64) : null;
  return { ok: true, uid, username };
}

module.exports = { verifyAccessToken, authOrigin, wellFormedToken, DEFAULT_ORIGIN, LOGIN_PATH, TIMEOUT_MS };
