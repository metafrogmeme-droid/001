'use strict';
/**
 * GET /api/nav/tools — { tools: boolean }.
 *
 * The dashboard nav asks this before it paints a Tools link. The
 * boolean is `sar_oracle_flag.openFor`, the same decision that serves
 * or 404s /tools. A client flag is not consulted. Logged-out callers
 * are identified by optionalAuth and answered `false`, not 401: the
 * bar is on a page an anonymous reader can open.
 *
 * An unreadable check is a 503 with no boolean. `false` means the gate
 * would not serve the page. Those are different facts, and the nav
 * omits the link for both.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();
// Lazy: auth.js exits the process at load when JWT_SECRET is missing,
// and this file is required by the route census.
const optionalAuth = (req, res, next) => require('../auth').optionalAuth(req, res, next);
const readLimit = rateLimit({ name: 'nav.tools', windowMs: 60000, max: 120, key: ipKey });

router.get('/', optionalAuth, readLimit, async (req, res) => {
  try {
    const tools = (await require('../lib/sar_oracle_flag').openFor(req)) === true;
    res.set('Cache-Control', 'private, no-store');
    res.json({ tools });
  } catch (err) {
    console.error('nav tools:', err && err.name ? err.name : 'Error');
    if (!res.headersSent) res.status(503).json({ error: 'unavailable' });
  }
});

module.exports = router;
