'use strict';
/**
 * GET /api/t2c/signal.
 *
 * Flag off: 404, unless the caller is the operator. Flag on: the public
 * feed, percent and ratio only. The operator also receives the dated
 * research note. P is a cap signal. This route does not serve a liquidation
 * price and it does not recompute one.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { isOperator } = require('../lib/operator_view');
const { flag_on } = require('../lib/t2c_flag');
const { loadReading } = require('../lib/t2c_bridge');

const router = express.Router();
router.use(optionalAuth);

router.get('/signal', async (req, res) => {
  let admin = false;
  try {
    admin = await isOperator(req);
  } catch (err) {
    console.error('[t2c] operator:', err && err.constructor ? err.constructor.name : 'Error');
    admin = false;
  }
  if (!flag_on() && !admin) return res.status(404).json({ error: 'Not found' });
  try {
    const reading = await loadReading();
    const body = admin ? reading.admin : reading.public;
    if (!body || typeof body !== 'object') {
      return res.status(500).json({ error: 'The signal could not be read' });
    }
    res.set('Cache-Control', 'no-store').json(body);
  } catch (err) {
    console.error('[t2c] signal:', err && err.constructor ? err.constructor.name : 'Error');
    res.status(500).json({ error: 'The signal could not be read' });
  }
});

module.exports = router;
