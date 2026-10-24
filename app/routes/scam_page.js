'use strict';
/**
 * GET /scam — the website page for Scam or Safe? Daily.
 *
 * One HTML document. The call itself is POST /api/scam-or-safe/pick, which
 * carries authMiddleware. This page is the door in front of that sign-in,
 * the same way /duel is a document and /api/duel is the authed call.
 *
 * Off unless SCAM_OR_SAFE_V0 is on. A closed flag is a 404, not a page that
 * says the game has no request today.
 */

const express = require('express');
const sos = require('../lib/scam_or_safe');
const { flag_on_for, withBanner } = require('../lib/admin_preview');

const router = express.Router();

function page() {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RUNECLAW — Scam or Safe?</title>
<meta name="description" content="One decoded signing request a day. Call it safe or risky. Read only: nothing is signed, addresses are masked, amounts are hidden.">
<link rel="stylesheet" href="/styles.css?v=52">
<style>
  .sos-page { display: flex; justify-content: center; }
  .sos-wrap { max-width: 720px; width: 100%; padding: 28px 16px 64px; box-sizing: border-box; }
  .sos-nav { display: flex; gap: 16px; padding-bottom: 18px; }
  .sos-nav a { color: inherit; opacity: .75; }
</style>
</head>
<body>
<div class="sos-page"><div class="sos-wrap">
<nav class="sos-nav"><a href="/duel">Daily Duel</a><a href="/">Home</a></nav>
<div id="scam-root"></div>
</div></div>
<script src="/js/app.js?v=17"></script>
<script src="/js/api-retry.js?v=3"></script>
<script src="/js/scam-or-safe.js?v=2" defer></script>
</body>
</html>`;
}

router.get('/', async (req, res) => {
  try {
    const on = await flag_on_for(req, 'SCAM_OR_SAFE_V0');
    if (!on) {
      res.status(404).type('text/plain').send('This game is not open.');
      return;
    }
    res.setHeader('Cache-Control', 'no-store');
    const html = page();
    res.type('html').send(sos.enabled() ? html : withBanner(html));
  } catch (e) {
    if (!res.headersSent) res.status(503).type('text/plain').send('This game is not open.');
  }
});

module.exports = router;
