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
 * NO ACCOUNT. A wallet launches and trades; the server hands it transactions
 * and reads the chain afterwards. The site's content security policy applies
 * unchanged: every script is ours, the wallet is a browser extension, and the
 * chain is read by the server, never by the page.
 */
const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(rateLimit({ name: 'memeclaw.page', windowMs: 60000, max: 120, key: ipKey }));

/** In the head: api-retry, as on every page. */
const HEAD_SCRIPTS = ['/js/api-retry.js?v=3'];
/** Deferred, in order: the wallet door, then the page. */
const SCRIPTS = ['/js/evm-wallet.js?v=1', '/js/memeclaw.js?v=1'];
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
<meta property="og:url" content="https://www.humanoid-traders.com/memeclaw">
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
  <div class="mc-brand"><b>Memeclaw</b> <span class="mc-chip">DogeOS testnet</span></div>
  <div id="mc-wallet" class="mc-wallet"></div>
</header>
<div class="mc-notice" role="note"><b>Testnet only.</b> Coins here have no value, and testnet DOGE comes free from the faucet. Anyone can launch a coin: its name and words are its creator's, not ours. RUNECLAW's agent never launches, buys or sells these coins.</div>
<nav class="mc-tabs" aria-label="Sections">
  <button type="button" class="mc-tab" data-view="coins" aria-pressed="true">Coins</button>
  <button type="button" class="mc-tab" data-view="launch" aria-pressed="false">Launch a coin</button>
  <button type="button" class="mc-tab" data-view="about" aria-pressed="false">How it works</button>
</nav>
<main id="mc-root" aria-live="polite"><div class="mc-load">Loading…</div></main>
<footer class="mc-foot"><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/call/">Receipts</a></footer>
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
