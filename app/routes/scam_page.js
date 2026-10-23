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
  .sos-wrap { max-width: 720px; margin: 0 auto; padding: 28px 16px 64px; }
  .sos-nav { display: flex; gap: 16px; margin-bottom: 18px; }
  .sos-nav a { color: inherit; opacity: .75; }
</style>
</head>
<body>
<div class="sos-wrap">
<nav class="sos-nav"><a href="/duel">Daily Duel</a><a href="/">Home</a></nav>
<div id="scam-root"></div>
</div>
<script src="/js/api-retry.js?v=3"></script>
<script src="/js/scam-or-safe.js?v=1" defer></script>
</body>
</html>`;
}

router.get('/', (req, res) => {
  if (!sos.enabled()) {
    res.status(404).type('text/plain').send('This game is not open.');
    return;
  }
  res.setHeader('Cache-Control', 'no-store');
  res.type('html').send(page());
});

module.exports = router;
