'use strict';
/**
 * GET /api/market-clock
 *
 * Admin-only. While MARKET_CLOCK_ENABLED is off, flag_on_for admits an
 * operator and the body carries the preview banner. A non-operator gets
 * 404, flag on or off. Times and rules only. This route does not sign.
 */

const express = require('express');

const { optionalAuth } = require('../auth');
const { accessFor, BANNER } = require('../lib/market_clock_flag');
const { calendar } = require('../lib/market_clock/view');

const router = express.Router();
router.use(optionalAuth);

function closed(res) {
  return res.status(404).json({ error: 'Not found' });
}

function unreadable(res) {
  return res.status(503).json({
    error: 'MarketClockUnreadable',
    readable: false,
  });
}

router.get('/', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    const symbol = req.query && req.query.symbol;
    const body = calendar(symbol, process.env);
    if (!body || body.readable !== true) {
      return res.status(503).json({
        error: 'MarketClockUnreadable',
        readable: false,
        reason: body && typeof body.reason === 'string' ? body.reason : null,
      });
    }
    body.preview = access.preview === true;
    body.banner = access.preview === true ? BANNER : null;
    res.set('Cache-Control', 'private, no-store');
    return res.json(body);
  } catch (err) {
    console.error('[market-clock] read failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

module.exports = router;
