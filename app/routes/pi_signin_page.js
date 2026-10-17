'use strict';
/**
 * GET /auth/pi/callback: Pi Sign-In's redirect URI (lib/pi_signin.js).
 *
 * Pi returns the access token in the URL fragment, which only a script in
 * the page can read, so the redirect URI has to be a page. This is all of
 * it: one script of ours (public/js/pi-signin-callback.js), no inline code,
 * no data. The script posts the token to /api/auth/oauth/pi/token, which is
 * how the person OBTAINS a session, so this page cannot require one.
 *
 * A handler, not a router: server.js mounts it at the one path Pi is sent
 * to. It sits in routes/ because that is where the cache-buster ratchet
 * reads pages from, so its script's ?v= is held like every other page's.
 */

/** The page's script, by path and version, as the manifest records it. */
const CALLBACK_SCRIPT = '/js/pi-signin-callback.js?v=1';

function page() {
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Signing in with Pi — RUNECLAW</title>
<script src="${CALLBACK_SCRIPT}" defer></script>
</head><body>
<p id="pi-signin-status" role="status">Signing you in with Pi…</p>
<noscript><p>Sign-in with Pi needs JavaScript. <a href="/">Back to RUNECLAW</a></p></noscript>
</body></html>`;
}

function sendCallbackPage(req, res) {
  // The token rides the fragment, which no server or log sees; still, nothing
  // about this page may be cached or send a referrer.
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.type('html').send(page());
}

module.exports = sendCallbackPage;
module.exports.CALLBACK_SCRIPT = CALLBACK_SCRIPT;
module.exports._page = page;
