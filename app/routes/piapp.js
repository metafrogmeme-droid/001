'use strict';
/**
 * The Pi Browser app: the paper arena, signed in with Pi.
 *
 *   GET /pi   the one page. Listed in Pi's App Studio / Developer Portal as
 *             https://<this site>/pi.
 *
 * WHAT IS ON IT, AND WHAT IS NOT. The season, a paper account on the same
 * virtual stake as everyone else, and the positions it opens. No token, no
 * presale, no link to the rest of the site: almost every page of the site
 * carries the presale bar, and this app's audience is Pi's, so the page is
 * built here from the Mini App's parts rather than from the site's pages, and
 * loads no presale.js. app/test/the_pi_app_is_the_arena_and_nothing_else.test.js
 * holds that.
 *
 * IT RUNS FRAMED. Pi's SDK (sdk.minepi.com/pi-sdk.js, read 2026-10-16) talks
 * to its host with `window.parent.postMessage`, to an origin it works out from
 * the referrer: https://<app>.pinet.com or https://app-cdn.minepi.com in the
 * Pi Browser, https://sandbox.minepi.com in sandbox mode, and App Studio's
 * *.piappengine.com for an app converted there. So this page is always inside
 * somebody's frame, which is why the session is a bearer token in memory: a
 * SameSite=Lax cookie would not ride along.
 *
 * SAFE TO FRAME FOR THE SAME REASON THE MINI APP IS (routes/miniapp.js): no
 * cookie and no storage, a session held in memory, obtainable only with an
 * access token Pi's App Studio vouches for (lib/pi_auth.js). frame-ancestors
 * is the one Pi's App Studio guidance gives (2026-10-16): 'self' plus
 * https://*.pinet.com and https://*.minepi.com, never a wildcard.
 * PI_FRAME_ANCESTORS replaces it if Pi's hosts change.
 *
 * PLUS APP STUDIO ITSELF, which that guidance leaves out. App Studio's "Checking
 * Pi Auth" step previews this page in a frame, and App Studio (the Pi Dev
 * Portal) runs at APP_STUDIO; appstudio.pinet.com is only an iframe around it.
 * frame-ancestors is checked against EVERY ancestor, so on 2026-10-17 the
 * preview showed net::ERR_BLOCKED_BY_RESPONSE until this origin was listed.
 * It is the exact origin, not https://*.piappengine.com: that wildcard would
 * let every app App Studio hosts frame this page.
 *
 * THE POLICY IS PI'S, explicit and narrow. The SDK calls https://socialchain.app
 * internally during authentication, so connect-src names it; our own code
 * talks only to us (the App Studio exchange happens on our server, so its
 * origin is deliberately NOT in this page's connect-src). The SDK injects
 * inline STYLES, so style-src allows them; script-src never allows inline.
 *
 * THE ONE OUTSIDE SCRIPT is Pi's own SDK, which the platform requires to be
 * loaded from sdk.minepi.com. It is allowed on this page's policy only; the
 * site-wide policy is untouched.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();

router.use(rateLimit({ name: 'piapp', windowMs: 60000, max: 120, key: ipKey }));

const PI_SDK = 'https://sdk.minepi.com/pi-sdk.js';

/** Pi's App Studio (the Dev Portal); its backend is lib/pi_auth.js's DEFAULT_ORIGIN. */
const APP_STUDIO = 'https://appstudio-u7cm9zhmha0ruwv8.piappengine.com';

const PI_FRAME_ANCESTORS = `'self' https://*.pinet.com https://*.minepi.com ${APP_STUDIO}`;

function frameAncestors(env = process.env) {
  const raw = String(env.PI_FRAME_ANCESTORS || '').trim();
  return raw || PI_FRAME_ANCESTORS;
}

function piCsp(env = process.env) {
  return [
    "default-src 'none'",
    "script-src 'self' https://sdk.minepi.com",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self' https://socialchain.app",
    "frame-src https://*.minepi.com",
    "base-uri 'none'",
    "form-action 'none'",
    'frame-ancestors ' + frameAncestors(env),
  ].join('; ');
}

function framable(req, res, next) {
  res.removeHeader('X-Frame-Options');
  res.setHeader('Content-Security-Policy', piCsp());
  // A bearer token in memory is the session; no cookie may ride this page.
  res.removeHeader('Set-Cookie');
  // A signed-in surface: never cached.
  res.setHeader('Cache-Control', 'no-store');
  next();
}

router.use(framable);

/**
 * In the head, before anything else runs: api-retry, as on every page, then
 * Pi's SDK, so window.Pi exists before the arena's scripts ask for it.
 */
const HEAD_SCRIPTS = ['/js/api-retry.js?v=3', PI_SDK];
/**
 * Deferred, in order, after the document. The signals panel's parts are the
 * embed board's (routes/embed.js, /embed/signals), at the same versions: the
 * site's TradingView library, vendored, then the card and chart modules,
 * then the panel itself (pi-signals.js); then the Daily Duel's renderer
 * (duel.js, the website's own), the receipt check (pi-receipt.js) and the
 * Duel block (pi-duel.js); then the Leaders tab (pi-leaders.js). pi-arena.js
 * stays last: it starts the session with every block it needs already loaded.
 */
const SCRIPTS = [
  '/js/embed-arena-view.js?v=3',
  '/js/miniapp-view.js?v=9',
  '/js/arena-session.js?v=8',
  '/vendor/lightweight-charts.standalone.production.js?v=1',
  '/js/tv-chart.js?v=1',
  '/js/embed-read.js?v=2',
  '/js/confidence-score.js?v=2',
  '/js/rr-words.js?v=1',
  '/js/embed-row.js?v=6',
  '/js/signal-chart.js?v=3',
  '/js/signal-status-model.js?v=4',
  '/js/pi-signals.js?v=5',
  // The Daily Duel: the website's round renderer, then the block.
  '/js/duel.js?v=8',
  // The in-app receipt check, before the block that uses it.
  '/js/pi-receipt.js?v=1',
  '/js/pi-duel.js?v=6',
  // The Leaders tab: the season's standings and your name on them.
  '/js/pi-leaders.js?v=1',
  '/js/pi-arena.js?v=4',
];

function page() {
  const head = HEAD_SCRIPTS.map((s) => `<script src="${s}"></script>`).join('\n');
  const scripts = SCRIPTS.map((s) => `<script src="${s}" defer></script>`).join('\n');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RUNECLAW Arena</title>
<meta name="description" content="Paper-trade the RUNECLAW Arena and call the Daily Duel from the Pi Browser. Free, paper money, no real funds; standings by percent return.">
<meta property="og:title" content="RUNECLAW Arena">
<meta property="og:description" content="Paper-trade the Arena and call the Daily Duel from the Pi Browser. Free, paper money, no real funds.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.humanoid-traders.com/pi">
<meta property="og:image" content="https://www.humanoid-traders.com/pi_og_1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b0e14">
<link rel="icon" type="image/png" sizes="200x200" href="/pi_icon_200.png">
<link rel="apple-touch-icon" sizes="180x180" href="/pi_touch_180.png">
${head}
<link rel="stylesheet" href="/embed.css?v=16">
</head><body class="e-arena m-app">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${scripts}
</body></html>`;
}

router.get('/', (req, res) => {
  res.type('html').send(page());
});

/**
 * GET /validation-key.txt, at the site root (server.js) and beside the app
 * (/pi/validation-key.txt). Pi's Developer Portal asks an app's domain to
 * serve a key it issues, to prove the domain is the developer's, and it looks
 * for the file under the App URL: the portal's "Verify App Domain" step names
 * /pi/validation-key.txt for this app (2026-10-17). From the environment only:
 * an unset key is a 404, never an empty 200 that reads as "verified with
 * nothing".
 */
router.get('/validation-key.txt', validationKey);

function validationKey(req, res) {
  const key = String(process.env.PI_VALIDATION_KEY || '').trim();
  if (!key) return res.status(404).type('text/plain').send('Not found');
  res.setHeader('Cache-Control', 'no-cache');
  return res.type('text/plain').send(key);
}

module.exports = router;
module.exports.validationKey = validationKey;
module.exports._page = page;
module.exports._piCsp = piCsp;
// Pi's frame list, shared with routes/legal.js: Pi's listing links the policies.
module.exports.frameAncestors = frameAncestors;
module.exports.PI_SDK = PI_SDK;
module.exports.PI_FRAME_ANCESTORS = PI_FRAME_ANCESTORS;
module.exports.APP_STUDIO = APP_STUDIO;
