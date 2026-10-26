'use strict';
/**
 * Is this Pi access token real, and whose is it?
 *
 * A Pi Browser app signs a person in with the Pi SDK: `Pi.authenticate()`
 * hands the page an access token and a user object. The page's word for who
 * that is counts for nothing (a page can send any uid it likes). The token is
 * checked with the Pi Platform, the call Pi's Platform API documents for a
 * self-hosted app (pi-apps.github.io/pi-sdk-docs, platform/PlatformAPI and
 * platform/Authentication, read 2026-10-26):
 *
 *   GET https://api.minepi.com/v2/me
 *   Authorization: Bearer <access token from Pi.authenticate>
 *   200 -> UserDTO { uid, username? }
 *   401 -> the token is invalid or expired (empty body, not JSON)
 *
 * The same call serves a Mainnet app and a Testnet app: the token itself
 * says which app it belongs to, and no server API key is sent. App Studio's
 * `POST /pi/auth/v1/login` is that product's own session exchange. It is the
 * wrong verifier for this app, which is hosted here, and a token it cannot
 * place came back as "unavailable" rather than as a person.
 *
 * The uid in Pi's UserDTO is the only identity this app trusts. The exchange
 * happens here, on our server, once per sign-in, and our own session is
 * issued from its result (routes/pi_auth.js). The website's Pi Sign-In door
 * asks the same call (lib/pi_signin.js).
 *
 * Three answers, not two, as with SIWF (lib/siwf.js):
 *   { ok: true, uid, username }  Pi vouched for the token.
 *   { ok: false, reason }        Pi refused it: not who they say.
 *   throws                       Pi could not be asked, or answered nothing
 *                                usable. NOT a refusal: telling a real person
 *                                their sign-in was rejected while the Platform
 *                                is down is a lie that looks exactly like a
 *                                real refusal.
 *
 * Nothing here reads PI_AUTH_ORIGIN, PI_NETWORK_API_KEY or PI_TESTNET_API_KEY.
 * A knob that pointed this call at another host would send the person's
 * token there. The URL is the Platform's, and tests pass a base only through
 * the `apiBase` argument.
 */

const API_BASE = 'https://api.minepi.com/v2';
const TIMEOUT_MS = 8000;
/** Pi's uids look like `a1111111-aaaa-bbbb-2222-ccccccc3333d`; the column is 64 wide. */
const UID_RE = /^[A-Za-z0-9_-]{1,64}$/;
/** An access token is a long random string; anything far outside that is not one. */
const TOKEN_RE = /^[A-Za-z0-9._~+/=-]{8,2048}$/;

function wellFormedToken(token) {
  return typeof token === 'string' && TOKEN_RE.test(token);
}

function meUrl(deps = {}) {
  const base = String(deps.apiBase || API_BASE).trim().replace(/\/+$/, '');
  return `${base}/me`;
}

async function verifyAccessToken(accessToken, deps = {}) {
  if (!wellFormedToken(accessToken)) return { ok: false, reason: 'malformed_token' };
  const fetchImpl = deps.fetch || globalThis.fetch;
  const r = await fetchImpl(meUrl(deps), {
    method: 'GET',
    headers: { Authorization: `Bearer ${accessToken}`, accept: 'application/json' },
    signal: AbortSignal.timeout(deps.timeoutMs || TIMEOUT_MS),
  });
  // Pi answers an invalid or expired token with 401 and an empty body, before
  // any JSON. Reading the body first would turn that refusal into a throw.
  if (r.status === 401 || r.status === 403) return { ok: false, reason: 'token_rejected' };
  if (!r.ok) throw new Error(`Pi /v2/me answered ${r.status}`);
  let body;
  try {
    body = await r.json();
  } catch (_) {
    throw new Error('Pi /v2/me answered a body that is not JSON');
  }
  // UserDTO puts uid at the top. A nested `user.uid` is App Studio's old
  // shape, and a 200 without a usable uid is Pi answering nothing we can
  // stand on: unavailable, not a refusal of the person.
  const uid = body && typeof body.uid === 'string' ? body.uid.trim() : '';
  if (!UID_RE.test(uid)) throw new Error('Pi /v2/me answered without a usable uid');
  const username = typeof body.username === 'string' ? body.username.slice(0, 64) : null;
  return { ok: true, uid, username };
}

/**
 * A leaderboard name to OFFER a Pi user who has none: the username Pi
 * verified, cut to what the board accepts (routes/leaderboard.js HANDLE_RE,
 * 3 to 20 letters, numbers or underscores), or null when nothing usable is
 * left. It is offered, never saved: the Pi app fills its name box with it and
 * the person decides whether to tap Save (app/public/js/pi-leaders.js).
 */
function handleSuggestion(username) {
  if (typeof username !== 'string') return null;
  const cut = username.replace(/[^A-Za-z0-9_]/g, '').slice(0, 20);
  return cut.length >= 3 ? cut : null;
}

module.exports = {
  verifyAccessToken, wellFormedToken, handleSuggestion, meUrl,
  API_BASE, TIMEOUT_MS, UID_RE,
};
