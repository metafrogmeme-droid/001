'use strict';
/**
 * GET /api/public/studies — the pre-registered studies, for anyone.
 *
 * Public by construction: each study's sealed question, data, windows, pass
 * rule and verdict, and its results as Sharpe ratios, percents and counts
 * (app/lib/studies.js serves an allowlist of them). No account is in scope
 * and the one file it reads is the website's copy of `benchmark/studies.json`.
 * An unreadable registry is a 503 that names the exception class, never a
 * 200 with no studies, which the page would paint as research never done.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const studies = require('../lib/studies');

const router = express.Router();
router.use(rateLimit({ name: 'public_studies', windowMs: 60000, max: 30, key: ipKey }));

router.get('/', (req, res) => {
  let body;
  try {
    body = studies.publicRegistry(studies.readRegistry());
  } catch (err) {
    // The class, never the message: a path or a parser detail is not a
    // sentence for the page.
    const name = err && err.name ? String(err.name) : 'Error';
    console.error('Studies registry error:', name);
    return res.status(503).json({ error: 'studies_unreadable', exception: name });
  }
  res.setHeader('Cache-Control', 'no-cache');
  res.json(body);
});

module.exports = router;
