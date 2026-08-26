'use strict';
/**
 * The AUTHENTICATED Mini App surface — framable, and able to act.
 *
 * WHY THIS IS NOT A PAGE UNDER /embed. That router's entire security argument
 * is stated in its own header: it is safe to frame *because* there is nothing
 * to authenticate and nothing to trigger. Every clause of that is enforced by
 * embed_frame_policy — GET only, no cookie, no session, no Authorization
 * header, no action. Adding a page that signs people in and opens positions
 * would not be a new feature in that router; it would be the deletion of the
 * property the router exists to hold, and the tests that protect it would have
 * to be weakened one by one until they protected nothing.
 *
 * So the trade-off is made HERE instead, once, in the open.
 *
 * WHY FRAMING THIS IS STILL SAFE, WHICH IS THE WHOLE QUESTION. A framable page
 * that can open trades is a clickjacking target: overlay it invisibly, let a
 * victim click something else, and the click lands on a control they never
 * saw. That attack needs one thing to work — an authenticated session inside
 * the attacker's frame — and this page cannot have one.
 *
 *   1. There is NO COOKIE and NO STORAGE. The session is a bearer token held
 *      in a JavaScript variable for the life of the page. Nothing persists, so
 *      a fresh frame on an attacker's site starts with no session, every time.
 *   2. The only way to get a token is a SIWF signature over a nonce WE issued
 *      moments ago, in a message naming OUR domain. An attacker framing this
 *      page controls the postMessage replies, so they can answer our signIn
 *      with anything they like — and cannot answer it with a valid signature,
 *      because producing one needs the private key of the Farcaster account
 *      being claimed. lib/siwf.js rejects the rest: wrong domain, stale nonce,
 *      replayed nonce, unconfirmed fid.
 *   3. `connect-src 'self'` means the token cannot be sent anywhere but here,
 *      even by our own code, even by mistake.
 *
 * So the worst an attacker achieves by framing this page is a signed-out page
 * whose buttons do nothing — which is exactly what a stranger should get.
 *
 * EMBED_FRAME_ANCESTORS narrows who may frame it if you want the belt as well
 * as the braces; the session gate above is the control that actually matters,
 * and an allowlist of Farcaster's frame origins would be guesswork about
 * somebody else's infrastructure.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();

// Same cap as /embed: a Mini App is fetched once per launch, but it is still
// an unauthenticated public page and this is the door to a signed-in surface.
router.use(rateLimit({ windowMs: 60000, max: 120, key: ipKey }));

function frameAncestors() {
  const raw = String(process.env.EMBED_FRAME_ANCESTORS || '').trim();
  return raw || '*';
}

/**
 * The same tight policy /embed carries, and `connect-src 'self'` is doing
 * more work here than it does there: this page holds a session token, and that
 * directive is what makes it impossible to send it to a third party.
 */
function miniappCsp() {
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    'frame-ancestors ' + frameAncestors(),
  ].join('; ');
}

function framable(req, res, next) {
  res.removeHeader('X-Frame-Options');
  res.setHeader('Content-Security-Policy', miniappCsp());
  // The page authenticates with a bearer token it holds in memory. A cookie
  // would be withheld cross-site anyway (SameSite=Lax) and would be the one
  // thing that could survive into an attacker's frame, so it is stripped here
  // as deliberately as it is in /embed.
  res.removeHeader('Set-Cookie');
  // NOT cached. /embed pages are public and identical for everyone, so a
  // shared cache is free there. This one is a signed-in surface: caching it
  // would be caching somebody's session view.
  res.setHeader('Cache-Control', 'no-store');
  next();
}

router.use(framable);

const COMMON_JS = ['/js/farcaster-ready.js?v=3'];

function page(title, bodyClass, script, meta, deps) {
  const scripts = COMMON_JS.concat(deps || [], [script])
    .map((s) => `<script src="${s}" defer></script>`).join('\n');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title>
${meta || ''}
<link rel="stylesheet" href="/embed.css?v=5">
</head><body class="${bodyClass}">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${scripts}
</body></html>`;
}

/**
 * GET /miniapp/arena — sign in with Farcaster and trade the season.
 *
 * Its own launchable card, like /embed/arena: the manifest's homeUrl stays on
 * the signals board, and `embedTags` takes a url and a button title, so this
 * is a third entry point into one app rather than a second app.
 */
router.get('/arena', (req, res) => {
  let meta = '';
  try {
    const r = require('../lib/public_origin').resolve(req, process.env) || {};
    meta = r.origin
      ? require('../lib/farcaster_manifest').embedTags(r.origin, {
        url: `${r.origin}/miniapp/arena`,
        buttonTitle: 'Trade the season',
      })
      : '';
  } catch (_) { meta = ''; }
  res.type('html').send(
    page('RUNECLAW — trade the arena', 'e-arena m-app', '/js/miniapp-arena.js?v=1', meta,
      ['/js/embed-arena-view.js?v=2', '/js/miniapp-view.js?v=1']));
});

module.exports = router;
module.exports.miniappCsp = miniappCsp;
module.exports.frameAncestors = frameAncestors;
