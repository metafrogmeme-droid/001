'use strict';
/**
 * Memeclaw: a memecoin launchpad on the DogeOS TESTNET (docs/memeclaw/
 * MEMECLAW_PLAN.md).
 *
 *   GET /memeclaw   the one page; its API is /api/memeclaw (routes/memeclaw.js)
 *
 * ITS OWN PAGE. Nothing of it is loaded by /doge or /pi, and it loads
 * nothing of theirs but the wallet door (evm-wallet.js). It carries no
 * presale, no $RCLAW and no site navigation: a launchpad for testnet coins
 * beside a page about a real token would read as a sale.
 *
 * ITS OWN DOMAIN, ONE SERVER. On a host named in MEMECLAW_HOSTS this page is
 * the home page and the only page (lib/memeclaw_host.js); MEMECLAW_PUBLIC_URL
 * is the address its link preview names.
 *
 * NO ACCOUNT. A wallet launches and trades; the server hands it transactions
 * and reads the chain afterwards. The site's content security policy applies
 * unchanged: every script is ours, the wallet is a browser extension, and the
 * chain is read by the server, never by the page.
 */
const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { publicUrl } = require('../lib/memeclaw_host');

const router = express.Router();
router.use(rateLimit({ name: 'memeclaw.page', windowMs: 60000, max: 120, key: ipKey }));

/** In the head: api-retry, as on every page. */
const HEAD_SCRIPTS = ['/js/api-retry.js?v=3'];
/**
 * Deferred, in order: the wallet door, the receipt check (the Pi app's,
 * which checks a launch receipt in the browser so the page never sends
 * anyone to the main site's receipt page), then the page.
 */
const SCRIPTS = ['/js/evm-wallet.js?v=1', '/js/pi-receipt.js?v=4', '/js/memeclaw.js?v=1'];
const STYLES = ['/memeclaw.css?v=1'];

function page() {
  const head = HEAD_SCRIPTS.map((s) => `<script src="${s}"></script>`).join('\n');
  const css = STYLES.map((s) => `<link rel="stylesheet" href="${s}">`).join('\n');
  const scripts = SCRIPTS.map((s) => `<script src="${s}" defer></script>`).join('\n');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Memeclaw · a testnet launchpad on DogeOS</title>
<meta name="description" content="Launch a memecoin from your own wallet on the DogeOS testnet, and trade it on a bonding curve. Testnet coins have no value.">
<meta property="og:title" content="Memeclaw · a testnet launchpad on DogeOS">
<meta property="og:description" content="Launch a memecoin from your own wallet on the DogeOS testnet. Fixed supply, no owner, no fee. Testnet coins have no value.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Humanoid Traders">
<meta property="og:url" content="${publicUrl()}">
<meta property="og:image" content="https://www.humanoid-traders.com/og_image_1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b0e14">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
${head}
${css}
</head><body class="mc">
<header class="mc-top">
  <div class="mc-brand"><b>Memeclaw</b> <span class="mc-chip" id="mc-mode-chip">Practice</span></div>
  <div id="mc-wallet" class="mc-wallet"></div>
</header>
<div class="mc-modes" role="group" aria-label="Mode">
  <button type="button" class="mc-mode" data-mode="practice" aria-pressed="true">Practice <span class="mc-muted">no wallet</span></button>
  <button type="button" class="mc-mode" data-mode="testnet" aria-pressed="false">Testnet <span class="mc-muted">DogeOS</span></button>
</div>
<div class="mc-notice" role="note"><b>Practice and testnet only.</b> Practice coins and practice DOGE live on this site and nowhere else; testnet coins live on the DogeOS testnet. Neither has any value. Anyone can launch a coin: its name and words are its creator's, not ours. RUNECLAW's agent never launches, buys or sells these coins.</div>
<nav class="mc-tabs" aria-label="Sections">
  <button type="button" class="mc-tab" data-view="coins" aria-pressed="true">Coins</button>
  <button type="button" class="mc-tab" data-view="launch" aria-pressed="false">Launch a coin</button>
  <button type="button" class="mc-tab" data-view="about" aria-pressed="false">How it works</button>
</nav>
<main id="mc-root" aria-live="polite"><div class="mc-load">Loading…</div></main>
<footer class="mc-foot"><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></footer>
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
module.exports.STYLES = STYLES;
