'use strict';
/**
 * GET /api/cover/adl and GET /api/cover/stopgap.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * With both flags off, the router answers 404 before it identifies anyone.
 * With a flag on, optionalAuth runs first and the body is still the hold:
 * offered is false, and the payload carries no price, stake or amount.
 * Nothing is signed. No key is read.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const page = require('../lib/cover_page');

const router = express.Router();

// Both products off is the same answer as a path this server does not have,
// and that answer does not read the database. A flag that is on identifies
// the caller and still does not offer the cover.
router.use((req, res, next) => {
  if (!page.flagOn('adl') && !page.flagOn('stopgap')) {
    return res.status(404).json({ error: 'Not found' });
  }
  return next();
});

router.use(optionalAuth);

function status(req, res, kind) {
  if (!page.flagOn(kind)) return res.status(404).json({ error: 'Not found' });
  return res.set('Cache-Control', 'no-store').json(page.publicStatus());
}

router.get('/adl', (req, res) => status(req, res, 'adl'));
router.get('/stopgap', (req, res) => status(req, res, 'stopgap'));

module.exports = router;
