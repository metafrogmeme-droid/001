'use strict';
/**
 * Pi Sign-In on the website: "Sign in with Pi" outside the Pi Browser.
 *
 * Pi's Developer Portal issues the app an OAuth client id for an IMPLICIT
 * flow, and nothing else: no client secret, no code exchange
 * (github.com/pi-apps/pi-platform-docs, pi-sign-in.md, read 2026-10-17):
 *
 *   GET https://accounts.pinet.com/oauth/authorize
 *       ?response_type=token&client_id=…&redirect_uri=…&scope=username&state=…
 *   → redirect_uri#access_token=…&token_type=…&expires_in=…&state=…
 *   → redirect_uri#error=access_denied|expired|cancelled|server_error
 *
 * The token comes back in the URL FRAGMENT, which a browser never sends to a
 * server. So the redirect URI is a page, /auth/pi/callback
 * (routes/pi_signin_page.js), whose one script reads the fragment, clears it
 * from the address bar and posts the token and state to
 * POST /api/auth/oauth/pi/token (auth.js). The server then:
 *   1. claims the state: issued by /start, single use, ten minutes
 *      (lib/oauth_state_store.js, shared by every instance);
 *   2. asks Pi who the token belongs to: GET https://api.minepi.com/v2/me
 *      with `Authorization: Bearer <token>`, the call that document names for
 *      a website's backend;
 *   3. keeps the uid and nothing else, and signs the person in the way every
 *      other provider does (auth.js, findOrCreateOAuthUser).
 *
 * NOT THE PI BROWSER APP'S FLOW. Inside the Pi Browser the SDK's token is
 * exchanged with App Studio (lib/pi_auth.js), whose guidance is that the app
 * must not call /v2/me itself. This is the other door, and Pi documents
 * /v2/me as its check. Pi says the uid is stable per person within one app;
 * it does not say whether this flow and the Pi Browser app's yield the same
 * uid for one person. Both land on users.pi_uid, so if they match, a person
 * has one account either way, and if they do not, two. Check with a real
 * account after the deploy before announcing the button.
 *
 * Three answers from Pi, as everywhere else in this codebase:
 *   { ok: true, uid }        Pi vouched for the token.
 *   { ok: false, reason }    Pi refused it (401/403): not who they say.
 *   throws                   Pi could not be asked, or answered nothing usable.
 *                            Never reported as a refusal of the person.
 */

const { wellFormedToken } = require('./pi_auth');

const AUTHORIZE_URL = 'https://accounts.pinet.com/oauth/authorize';
const API_BASE = 'https://api.minepi.com/v2';
const CALLBACK_PATH = '/auth/pi/callback';
/** The least Pi offers: the uid comes with it. No wallet address is asked for. */
const SCOPE = 'username';
const TIMEOUT_MS = 8000;
/** Pi's uids look like `a1111111-aaaa-bbbb-2222-ccccccc3333d`; the column is 64 wide. */
const UID_RE = /^[A-Za-z0-9_-]{1,64}$/;

/** The public client id from the Developer Portal; '' when the door is not configured. */
function clientId(env = process.env) {
  return String(env.PI_OAUTH_CLIENT_ID || '').trim();
}

function configured(env = process.env) {
  return clientId(env).length > 0;
}

/** Must equal, byte for byte, a Redirect URI registered in the Developer Portal. */
function redirectUri(env = process.env) {
  const base = String(env.APP_BASE_URL || '').trim().replace(/\/+$/, '');
  return `${base}${CALLBACK_PATH}`;
}

function authorizeUrl({ state, env = process.env }) {
  const q = new URLSearchParams({
    response_type: 'token',
    client_id: clientId(env),
    redirect_uri: redirectUri(env),
    scope: SCOPE,
    state,
  });
  return `${AUTHORIZE_URL}?${q.toString()}`;
}

/** Who does this token belong to, according to Pi? See the header for the three answers. */
async function identify(accessToken, deps = {}) {
  if (!wellFormedToken(accessToken)) return { ok: false, reason: 'malformed_token' };
  const fetchImpl = deps.fetch || globalThis.fetch;
  const r = await fetchImpl(`${deps.apiBase || API_BASE}/me`, {
    headers: { Authorization: `Bearer ${accessToken}`, accept: 'application/json' },
    signal: AbortSignal.timeout(deps.timeoutMs || TIMEOUT_MS),
  });
  if (r.status === 401 || r.status === 403) return { ok: false, reason: 'token_rejected' };
  if (!r.ok) throw new Error(`Pi /v2/me answered ${r.status}`);
  let body;
  try {
    body = await r.json();
  } catch (_) {
    throw new Error('Pi /v2/me answered a body that is not JSON');
  }
  const uid = body && typeof body.uid === 'string' ? body.uid.trim() : '';
  // A 200 with no usable uid is Pi answering nothing we can stand on: unavailable, not a refusal.
  if (!UID_RE.test(uid)) throw new Error('Pi /v2/me answered without a usable uid');
  return { ok: true, uid };
}

module.exports = {
  clientId, configured, redirectUri, authorizeUrl, identify,
  AUTHORIZE_URL, API_BASE, CALLBACK_PATH, SCOPE, UID_RE,
};
