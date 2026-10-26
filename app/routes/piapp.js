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
 * access token the Pi Platform vouches for (lib/pi_auth.js). frame-ancestors
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
 * talks only to us (the Platform check happens on our server, so
 * api.minepi.com is deliberately NOT in this page's connect-src). The SDK injects
 * inline STYLES, so style-src allows them; script-src never allows inline.
 *
 * THE ONE OUTSIDE SCRIPT is Pi's own SDK, which the platform requires to be
 * loaded from sdk.minepi.com. It is allowed on this page's policy only; the
 * site-wide policy is untouched.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const express = require('express');
const { previewFlags, withBanner } = require('../lib/admin_preview');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const PiI18n = require('../public/js/pi-i18n.js');
const { isTestnetHost, pageOrigin } = require('../lib/pi_host');

const router = express.Router();

router.use(rateLimit({ name: 'piapp', windowMs: 60000, max: 120, key: ipKey }));

const PI_SDK = 'https://sdk.minepi.com/pi-sdk.js';

/**
 * Pi's App Studio (the Dev Portal). It may frame this page: its "Checking Pi
 * Auth" preview is an ancestor. Sign-in does not talk to it (lib/pi_auth.js
 * asks api.minepi.com).
 */
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
 * Duel block (pi-duel.js); then the Leaders tab (pi-leaders.js) and the
 * read-only daily proof (pi-proof.js). pi-arena.js
 * stays last: it starts the session with every block it needs already loaded.
 */
/**
 * The app's own words in the reader's language (pi-i18n.js), first of the
 * deferred scripts. The chosen language's chunk is placed right after it,
 * per request (page()), which finds it by this one name.
 */
const PI_I18N_SCRIPT = '/js/pi-i18n.js?v=2';

const SCRIPTS = [
  PI_I18N_SCRIPT,
  '/js/embed-arena-view.js?v=4',
  '/js/miniapp-view.js?v=11',
  '/js/arena-session.js?v=11',
  '/vendor/lightweight-charts.standalone.production.js?v=1',
  '/js/tv-chart.js?v=1',
  '/js/embed-read.js?v=2',
  '/js/confidence-score.js?v=2',
  '/js/rr-words.js?v=1',
  '/js/embed-row.js?v=7',
  '/js/signal-chart.js?v=3',
  '/js/signal-status-model.js?v=4',
  '/js/pi-signals.js?v=7',
  // The Daily Duel: the website's round renderer, then the block.
  '/js/duel.js?v=11',
  // The in-app receipt check, before the block that uses it.
  '/js/pi-receipt.js?v=5',
  '/js/pi-duel.js?v=13',
  // Ask the Claw, the coach tab.
  '/js/pi-coach.js?v=3',
  // The Leaders tab: the season's standings and your name on them.
  '/js/pi-leaders.js?v=3',
  // The daily proof, read-only, behind the header's Proof button.
  '/js/pi-proof.js?v=2',
  '/js/pi-arena.js?v=10',
];

// ── the reader's language ────────────────────────────────────────────────

const PI_I18N_DIR = path.join(__dirname, '..', 'public', 'js', 'pi-i18n');
let chunkMap = null;

/**
 * { code: '/js/pi-i18n/<code>.js?v=<hash>' } for every language whose chunk
 * is built (app/scripts/build_pi_i18n.js). The version is the first eight hex
 * of the chunk's own SHA-256, as the site's i18n chunks are versioned
 * (build_i18n.js): a translation that changes is a new address, with no
 * number for anyone to remember to bump. Read once per process; a deploy is a
 * new process. A language with no chunk on disk is offered nowhere.
 */
function chunks() {
  if (chunkMap) return chunkMap;
  const out = {};
  for (const code of PiI18n.CODES) {
    if (code === 'en') continue;
    let text;
    try { text = fs.readFileSync(path.join(PI_I18N_DIR, `${code}.js`)); } catch (e) { continue; }
    out[code] = `/js/pi-i18n/${code}.js?v=${crypto.createHash('sha256').update(text).digest('hex').slice(0, 8)}`;
  }
  chunkMap = out;
  return out;
}

/**
 * The language the page opens in: `?lang=` when it names one this app has,
 * else the first language of the Pi Browser's own Accept-Language that it
 * has, else English. A tag it does not speak is never an error; it is English.
 */
function pickLang(req, offered = chunks()) {
  const has = (c) => c === 'en' || Object.prototype.hasOwnProperty.call(offered, c);
  const asked = PiI18n.normalize(req && req.query ? req.query.lang : '');
  if (asked && has(asked)) return asked;
  const header = req && typeof req.get === 'function' ? req.get('accept-language') : '';
  const parts = String(header || '').split(',').map((p) => {
    const bits = p.trim().split(';q=');
    const q = bits.length > 1 ? Number(bits[1]) : 1;
    return { tag: bits[0], q: Number.isFinite(q) ? q : 0 };
  }).filter((p) => p.tag && p.q > 0).sort((a, b) => b.q - a.q);
  for (const p of parts) {
    const code = PiI18n.normalize(p.tag);
    if (code && has(code)) return code;
  }
  return 'en';
}

function escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function page(lang = 'en', offered = chunks(), preview = null, origin = 'https://www.humanoid-traders.com') {
  // The origin is written into the document. Only a hostname we built is used.
  if (!/^https:\/\/[a-z0-9.-]+$/.test(origin)) origin = 'https://www.humanoid-traders.com';
  const code = lang === 'en' || Object.prototype.hasOwnProperty.call(offered, lang) ? lang : 'en';
  const head = HEAD_SCRIPTS.map((s) => `<script src="${s}"></script>`).join('\n');
  const list = SCRIPTS.slice();
  // Scam or Safe is a tab only while SCAM_OR_SAFE_V0 is on for this caller.
  // Off, and not an admin preview, the script list is the one this page has
  // always shipped. `preview` defaults off so a direct _page() call stays that.
  if (require('../lib/scam_or_safe').enabled() || (preview && preview.scam)) {
    const at = list.indexOf('/js/pi-arena.js?v=10');
    list.splice(at < 0 ? list.length : at, 0, '/js/scam-or-safe.js?v=2');
  }
  // The chosen language's words load right after the module that holds them,
  // before anything draws.
  if (code !== 'en') list.splice(list.indexOf(PI_I18N_SCRIPT) + 1, 0, offered[code]);
  const scripts = list.map((s) => `<script src="${s}" defer></script>`).join('\n');
  const html = `<!doctype html>
<html lang="${code}" dir="${PiI18n.dirOf(code)}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RUNECLAW Arena</title>
<meta name="description" content="Paper-trade the RUNECLAW Arena and call the Daily Duel from the Pi Browser. Free, paper money, no real funds; standings by percent return.">
<meta property="og:title" content="RUNECLAW Arena">
<meta property="og:description" content="Paper-trade the Arena and call the Daily Duel from the Pi Browser. Free, paper money, no real funds.">
<meta property="og:type" content="website">
<link rel="canonical" href="${origin}/pi">
<meta property="og:url" content="${origin}/pi">
<meta property="og:image" content="${origin}/pi_og_1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b0e14">
<link rel="icon" type="image/png" sizes="200x200" href="/pi_icon_200.png">
<link rel="apple-touch-icon" sizes="180x180" href="/pi_touch_180.png">
${head}
<meta name="pi-i18n" content="${escAttr(JSON.stringify(offered))}">
<link rel="stylesheet" href="/embed.css?v=20">
</head><body class="e-arena m-app">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${scripts}
</body></html>`;
  return preview && preview.banner ? withBanner(html) : html;
}

router.get('/', async (req, res) => {
  // The page depends on the reader's language and, through the Worker, on
  // X-Runeclaw-Pi-Proxy (the canonical URL). It is never cached anyway
  // (framable(), no-store). Vary names both so a cache that kept the body
  // anyway cannot serve one app's links as the other's. The header name is
  // varied; the secret is not written into the response.
  res.setHeader('Vary', 'Accept-Language, X-Runeclaw-Pi-Proxy');
  // The Testnet app's canonical URL is its own hostname, including when a
  // Worker fetched this page from www and proved it with the proxy secret.
  const origin = pageOrigin(req);
  try {
    const flags = await previewFlags(req);
    res.type('html').send(page(pickLang(req), chunks(), {
      scam: flags.includes('SCAM_OR_SAFE_V0'),
      banner: flags.length > 0,
    }, origin));
  } catch (e) {
    if (!res.headersSent) res.type('html').send(page(pickLang(req), chunks(), null, origin));
  }
});

/**
 * GET /validation-key.txt, at the site root (server.js) and beside the app
 * (/pi/validation-key.txt). Pi's Developer Portal asks an app's domain to
 * serve a key it issues, to prove the domain is the developer's, and it looks
 * for the file under the App URL: the portal's "Verify App Domain" step names
 * /pi/validation-key.txt for this app (2026-10-17). From the environment only:
 * an unset key is a 404, never an empty 200 that reads as "verified with
 * nothing".
 *
 * TWO APPS, TWO KEYS. The Mainnet app keeps PI_VALIDATION_KEY. A request that
 * is the Testnet app (lib/pi_host: Host is PI_TESTNET_HOST, or the Worker
 * proxy secret matches) is answered with PI_TESTNET_VALIDATION_KEY, including
 * when that key is unset (a 404, not the Mainnet key: one file must not
 * verify both apps). The other direction holds too: a request that is not
 * the Testnet app is never answered with the Testnet key. The handler
 * answers in place. Nothing in this server redirects that host to www.
 *
 * NO SHARED CACHE. The two apps share this path, and through the Worker they
 * also share Host. no-store, and Vary names Host and X-Runeclaw-Pi-Proxy, so
 * a cache that kept the body anyway cannot hand one app the other's key. The
 * header name is varied; the secret is not written into the response.
 */
router.get('/validation-key.txt', validationKey);

function validationKey(req, res) {
  const env = process.env;
  const key = String((isTestnetHost(req, env) ? env.PI_TESTNET_VALIDATION_KEY : env.PI_VALIDATION_KEY) || '').trim();
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Vary', 'Host, X-Runeclaw-Pi-Proxy');
  if (!key) return res.status(404).type('text/plain').send('Not found');
  return res.type('text/plain').send(key);
}

module.exports = router;
module.exports.validationKey = validationKey;
module.exports._page = page;
module.exports._pickLang = pickLang;
module.exports._chunks = chunks;
// The Pi app's scripts, in load order: app/scripts/build_pi_i18n.js reads its
// sentences from exactly these.
module.exports.SCRIPTS = SCRIPTS;
module.exports._piCsp = piCsp;
// Pi's frame list, shared with routes/legal.js: Pi's listing links the policies.
module.exports.frameAncestors = frameAncestors;
module.exports.PI_SDK = PI_SDK;
module.exports.PI_FRAME_ANCESTORS = PI_FRAME_ANCESTORS;
module.exports.APP_STUDIO = APP_STUDIO;
