'use strict';
/**
 * GET /api/cover/adl and GET /api/cover/stopgap.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * The public flag off is 404, the same answer as a missing path. An admin
 * can preview: flag_on_for re-reads users.plan and does not trust the JWT.
 * With the flag on, the body is still the hold. The payload carries no
 * price, stake or amount. Nothing is signed. No key is read.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { flag_on_for } = require('../lib/admin_preview');
const page = require('../lib/cover_page');

const router = express.Router();

router.use(optionalAuth);

async function status(req, res, kind) {
  try {
    const name = page.FLAG_NAME[kind];
    if (!name || (await flag_on_for(req, name)) !== true) {
      return res.status(404).json({ error: 'Not found' });
    }
    const cache = page.flagOn(kind) ? 'no-store' : 'private, no-store';
    return res.set('Cache-Control', cache).json(page.publicStatus());
  } catch (e) {
    return res.status(500).json({ error: 'Not found' });
  }
}

router.get('/adl', (req, res) => status(req, res, 'adl'));
router.get('/stopgap', (req, res) => status(req, res, 'stopgap'));

module.exports = router;
