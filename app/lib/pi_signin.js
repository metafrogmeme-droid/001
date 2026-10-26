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
 *      with `Authorization: Bearer <token>`, the same Platform call the Pi
 *      Browser app uses (lib/pi_auth.js);
 *   3. keeps the uid and nothing else, and signs the person in the way every
 *      other provider does (auth.js, findOrCreateOAuthUser).
 *
 * SAME CHECK, TWO DOORS. This file is the website's OAuth door. The Pi
 * Browser app posts the SDK's token to /api/pi/signin, which asks the same
 * /v2/me. Pi says a uid is stable per person within one app, so a token from
 * the same Pi app yields the same uid either way, and both land on
 * users.pi_uid. The Testnet app and the Mainnet app are different apps.
 *
 * Three answers from Pi, as everywhere else in this codebase:
 *   { ok: true, uid }        Pi vouched for the token.
 *   { ok: false, reason }    Pi refused it (401/403): not who they say.
 *   throws                   Pi could not be asked, or answered nothing usable.
 *                            Never reported as a refusal of the person.
 */

const { verifyAccessToken, API_BASE, UID_RE } = require('./pi_auth');

const AUTHORIZE_URL = 'https://accounts.pinet.com/oauth/authorize';
const CALLBACK_PATH = '/auth/pi/callback';
/** The least Pi offers: the uid comes with it. No wallet address is asked for. */
const SCOPE = 'username';

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

/**
 * Who does this token belong to, according to Pi? The Platform call lives in
 * lib/pi_auth.js, shared with the Pi Browser app. This door keeps the uid
 * and drops the username: the website does not offer a board name from it.
 */
async function identify(accessToken, deps = {}) {
  const verdict = await verifyAccessToken(accessToken, deps);
  if (!verdict.ok) return verdict;
  return { ok: true, uid: verdict.uid };
}

module.exports = {
  clientId, configured, redirectUri, authorizeUrl, identify,
  AUTHORIZE_URL, API_BASE, CALLBACK_PATH, SCOPE, UID_RE,
};
