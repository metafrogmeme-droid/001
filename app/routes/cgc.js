'use strict';
/**
 * GET /api/cgc/status.
 *
 * Admin-only. The flag does not open this to anyone else: a non-admin
 * is 404 while CGC_ENABLED is off and 404 while it is on. The payload
 * is ratios, counts and chain names. It carries no bond size. This
 * process does not sign and it holds no key.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { accessFor } = require('../lib/cgc_flag');
const { statusPayload } = require('../lib/cgc_reading');

const router = express.Router();
router.use(optionalAuth);

router.get('/status', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return res.status(404).json({ error: 'Not found' });
    res.set('Cache-Control', 'no-store').json(statusPayload({ preview: access.preview }));
  } catch (err) {
    console.error('[cgc] status failed:', err && err.constructor ? err.constructor.name : 'Error');
    res.status(500).json({ error: 'The status could not be read' });
  }
});

module.exports = router;
