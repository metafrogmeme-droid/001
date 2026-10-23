'use strict';
/**
 * GET /api/cover/adl and GET /api/cover/stopgap.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * With the matching flag off, the route is 404. With it on, the body is the
 * hold: offered is false, and the payload carries no price, stake or amount.
 * Nothing is signed. No key is read.
 */

const express = require('express');
const page = require('../lib/cover_page');

const router = express.Router();

function status(req, res, kind) {
  if (!page.flagOn(kind)) return res.status(404).json({ error: 'Not found' });
  return res.set('Cache-Control', 'no-store').json(page.publicStatus());
}

router.get('/adl', (req, res) => status(req, res, 'adl'));
router.get('/stopgap', (req, res) => status(req, res, 'stopgap'));

module.exports = router;
