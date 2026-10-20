'use strict';
/**
 * The DogeOS app: the Daily Duel and the Arena, signed in with a DogeOS
 * wallet, with every day's seal root anchored on the DogeOS testnet.
 *
 *   GET /doge   the one page. The Vibe Code on DOGE entry (2026-10-19).
 *
 * WHAT IS ON IT. The same arena session the Pi app runs (arena-session.js),
 * with the Daily Duel block (pi-duel.js, the website's own round renderer) and
 * one block of its own: the proof panel (doge-proof.js), which lists the
 * latest daily seal roots with their anchors on every chain and lets the
 * signed-in player anchor a day's root on the DogeOS testnet FROM THEIR OWN
 * WALLET. The server hands out the zero-value transaction (dry run) and
 * records the hash only after the chain confirmed it: non-custodial to the
 * letter, the doctrine lib/root_anchor.js states.
 *
 * SIGN-IN IS THE SITE'S OWN WALLET SIGN-IN (/api/auth/wallet/nonce and
 * /verify, a signed nonce, no transaction, no gas), after the page has asked
 * the wallet to switch to the DogeOS testnet, whose id, RPC and explorer it
 * reads from /api/roots/chains rather than carrying literals of its own.
 *
 * NO PRESALE, NO TOKEN, as the Pi app: the page is built here from the Mini
 * App's parts and loads no presale.js and no site navigation; a hackathon
 * judge lands on the game and the proof, not on a sale. The site's own
 * content security policy applies unchanged: every script is ours, the
 * wallet is a browser extension and needs no connect-src of its own, and the
 * chain is read by the server, never by the page.
 */
const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(rateLimit({ name: 'dogeapp', windowMs: 60000, max: 120, key: ipKey }));

/** In the head: api-retry, as on every page. */
const HEAD_SCRIPTS = ['/js/api-retry.js?v=3'];
/**
 * Deferred, in order, after the document: the arena session and its views,
 * the Daily Duel's renderer and block (the Pi app's, unchanged), the anchor
 * cell the roots page paints with, then the proof block and the app itself,
 * which starts the session with every block already loaded.
 */
const SCRIPTS = [
  '/js/embed-arena-view.js?v=3',
  '/js/miniapp-view.js?v=7',
  '/js/arena-session.js?v=6',
  '/js/duel.js?v=7',
  '/js/pi-duel.js?v=4',
  '/js/anchor_cell.js?v=2',
  '/js/doge-proof.js?v=1',
  '/js/doge-app.js?v=1',
];

function page() {
  const head = HEAD_SCRIPTS.map((s) => `<script src="${s}"></script>`).join('\n');
  const scripts = SCRIPTS.map((s) => `<script src="${s}" defer></script>`).join('\n');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RUNECLAW on DogeOS</title>
<meta name="description" content="Call the Daily Duel against the RUNECLAW agent and paper-trade the Arena, signed in with your DogeOS wallet. Every call is sealed before the market moves, and every day's root is anchored on the DogeOS testnet.">
<meta property="og:title" content="RUNECLAW on DogeOS">
<meta property="og:description" content="The Daily Duel and the paper Arena, signed in with a DogeOS wallet. Sealed before the market moves; anchored on the DogeOS testnet.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.humanoid-traders.com/doge">
<meta property="og:image" content="https://www.humanoid-traders.com/pi_og_1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b0e14">
<link rel="icon" type="image/png" sizes="200x200" href="/pi_icon_200.png">
<link rel="apple-touch-icon" sizes="180x180" href="/pi_touch_180.png">
${head}
<link rel="stylesheet" href="/embed.css?v=14">
</head><body class="e-arena m-app">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${scripts}
</body></html>`;
}

router.get('/', (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.type('html').send(page());
});

module.exports = router;
module.exports.SCRIPTS = SCRIPTS;
module.exports.HEAD_SCRIPTS = HEAD_SCRIPTS;
