'use strict';
/**
 * GET /api/hrc and GET /api/hrc/shock.
 *
 * optionalAuth identifies and does not 401, so an anonymous caller is a
 * 404 rather than a login wall. The same 404 is the answer for a signed-in
 * account that is not the operator, whether or not HRC_ENABLED is on.
 * Operators can read while the flag is off. That response is marked preview.
 *
 * The dashboard carries utilization and rate points. The shock, a dollar
 * amount, is only on /shock.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { flag_on } = require('../lib/hrc_flag');
const { isOperator } = require('../lib/operator_view');
const { loadReading, dashboard, adminPacket } = require('../lib/hrc_view');

const router = express.Router();
router.use(optionalAuth);

router.use(async (req, res, next) => {
  try {
    if (!(await isOperator(req))) return res.status(404).json({ error: 'Not found' });
    return next();
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(500).json({ error: name });
  }
});

function withPreview(packet) {
  packet.preview = !flag_on();
  return packet;
}

router.get('/', (req, res) => {
  try {
    res.set('Cache-Control', 'no-store').json(withPreview(dashboard(loadReading())));
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    res.status(500).json({ error: name });
  }
});

router.get('/shock', (req, res) => {
  try {
    res.set('Cache-Control', 'no-store').json(withPreview(adminPacket(loadReading())));
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    res.status(500).json({ error: name });
  }
});

module.exports = router;
