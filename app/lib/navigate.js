'use strict';
/**
 * Send the browser somewhere with a page that navigates, never a 3xx.
 *
 * THE FINDING (live, 2026-10-18). The web host answers a redirect itself: it
 * follows the Location server-side and hands the browser whatever is at the
 * far end, under OUR address. Measured from outside the day Sign in with Pi
 * went live:
 *   GET /api/auth/oauth/pi/start      our 302 to accounts.pinet.com came back
 *                                     as a 200 carrying Pi's authorize page
 *                                     (`x-powered-by: Next.js`, `via: 1.1
 *                                     google`, no `x-fc-request-id`), whose
 *                                     /_next/ assets then 404 on our domain.
 *   GET /api/auth/oauth/discord/callback?error=…
 *                                     our 302 to /#oauth_error=… came back as
 *                                     a 200 carrying the home page; a fragment
 *                                     cannot survive a server-side hop, so the
 *                                     sentence (and on success, the session)
 *                                     was lost.
 * Every test passed, because no test ran behind that host.
 *
 * A 200 page is passed through untouched, so the hop happens in the browser:
 * a meta refresh, and a plain link for a browser that has refresh switched
 * off. No script, so the page needs nothing from the site's CSP. The target
 * is HTML-escaped into an unquoted refresh URL (every caller's target starts
 * with `/` or `https://`, so the refresh parser never reads it as quoted).
 */

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function navigationPage(url) {
  const u = esc(url);
  return '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + `<meta http-equiv="refresh" content="0; url=${u}">`
    + '<title>Continuing…</title></head>'
    + `<body><p><a href="${u}">Continue</a></p></body></html>`;
}

/**
 * Answer `res` with the page. no-store: the page may carry a session in its
 * fragment. no-referrer: the page's own URL may carry an OAuth code.
 */
function navigate(res, url) {
  res.status(200)
    .set('Cache-Control', 'no-store')
    .set('Referrer-Policy', 'no-referrer')
    .type('html')
    .send(navigationPage(url));
}

module.exports = { navigate, navigationPage };
