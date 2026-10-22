'use strict';
/**
 * GET /tg — the Daily Duel as a Telegram Mini App (public/js/tg-app.js).
 *
 * ONE HTML DOCUMENT, like /pi: the Duel and the daily proof, signed in with
 * Telegram's signed launch data (routes/tg_auth.js). It holds no session of
 * its own: the token lives in the page's memory, so no cookie rides it.
 *
 * IT MAY RUN FRAMED. Telegram's apps open a Mini App in a webview, but its web
 * client (web.telegram.org) opens it in an iframe, so frame-ancestors names
 * that origin; TG_FRAME_ANCESTORS replaces the list. Telegram's script
 * (telegram.org/js/telegram-web-app.js, the URL its docs give) is the one
 * outside script, allowed on this page's policy only.
 *
 * NOTHING ON A CHAIN. Telegram requires TON for blockchain functionality in a
 * Mini App; this page has no wallet, no transaction and no token, and its
 * connect-src is our own origin. The proof it shows is read-only.
 *
 * THE SAME SCRIPTS AS THE PI APP, at the same versions: each is looked up in
 * routes/piapp.js SCRIPTS, so a bundle bumped there is the bundle served
 * here, and the sentences are the ones the Pi app's translation build reads.
 */

const express = require('express');
const piapp = require('./piapp');
const PiI18n = require('../public/js/pi-i18n.js');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();

router.use(rateLimit({ name: 'tgapp', windowMs: 60000, max: 120, key: ipKey }));

/** Telegram's script, as core.telegram.org/bots/webapps prints it (read 2026-10-22). */
const TELEGRAM_JS = 'https://telegram.org/js/telegram-web-app.js?64';
const TG_FRAME_ANCESTORS = "'self' https://web.telegram.org";

function frameAncestors(env = process.env) {
  const raw = String(env.TG_FRAME_ANCESTORS || '').trim();
  return raw || TG_FRAME_ANCESTORS;
}

function tgCsp(env = process.env) {
  return [
    "default-src 'none'",
    "script-src 'self' https://telegram.org",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    'frame-ancestors ' + frameAncestors(env),
  ].join('; ');
}

function framable(req, res, next) {
  res.removeHeader('X-Frame-Options');
  res.setHeader('Content-Security-Policy', tgCsp());
  // A bearer token in memory is the session; no cookie may ride this page.
  res.removeHeader('Set-Cookie');
  // A signed-in surface: never cached.
  res.setHeader('Cache-Control', 'no-store');
  next();
}

router.use(framable);

/** One of the Pi app's scripts, by name, at the version the Pi app serves. */
function piScript(name) {
  const found = piapp.SCRIPTS.find((s) => s.startsWith(`/js/${name}?`));
  if (!found) throw new Error(`routes/piapp.js no longer loads ${name}`);
  return found;
}

/** In load order: the words, the shared view and shell, the Duel, its receipts, the proof, then this host. */
function scripts() {
  return [
    piScript('pi-i18n.js'),
    piScript('embed-arena-view.js'),
    piScript('miniapp-view.js'),
    piScript('arena-session.js'),
    piScript('duel.js'),
    piScript('pi-receipt.js'),
    piScript('pi-duel.js'),
    piScript('pi-proof.js'),
    '/js/tg-app.js?v=1',
  ];
}

function escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function page(lang = 'en', offered = piapp._chunks()) {
  const code = lang === 'en' || Object.prototype.hasOwnProperty.call(offered, lang) ? lang : 'en';
  const list = scripts();
  if (code !== 'en') list.splice(1, 0, offered[code]);
  return `<!doctype html>
<html lang="${code}" dir="${PiI18n.dirOf(code)}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>RUNECLAW Daily Duel</title>
<script src="/js/api-retry.js?v=3"></script>
<script src="${TELEGRAM_JS}"></script>
<meta name="pi-i18n" content="${escAttr(JSON.stringify(offered))}">
<link rel="stylesheet" href="/embed.css?v=19">
</head><body class="e-arena m-app">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${list.map((s) => `<script src="${s}" defer></script>`).join('\n')}
</body></html>`;
}

router.get('/', (req, res) => {
  res.setHeader('Vary', 'Accept-Language');
  res.type('html').send(page(piapp._pickLang(req)));
});

module.exports = router;
module.exports._page = page;
module.exports.tgCsp = tgCsp;
module.exports.TELEGRAM_JS = TELEGRAM_JS;
