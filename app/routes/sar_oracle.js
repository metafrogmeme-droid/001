'use strict';
/**
 * GET /api/sar-oracle/packets — the latest SaR Oracle reading.
 *
 * Public and read-only. Ratios and scores, no account, no prices and no
 * dollar amounts. 404 while SAR_ORACLE_ENABLED is off, the same answer as
 * a path this server does not have. Rate limited by IP. The process signs
 * only on Base Sepolia and only when a key is configured; the key is never
 * in this repository. There is no write route in this file. A POST here
 * would sit inside the express-route-auth exemption, so the test asserts
 * this file registers none.
 *
 * GET /api/sar-oracle/admin/packets is the operator's copy. The flag is
 * checked first, then the operator, then the rate limit. A closed flag
 * does not ask who the caller is and does not spend a rate-limit slot.
 *
 *   200  the reading, with null where a field was not read
 *   400  the market query is not one this feed asks about
 *   404  the flag is off, or the admin caller is not the operator
 *   429  the caller is over the per-IP cap
 *   503  the reading could not be built; the body names the exception class
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { flag_on } = require('../lib/sar_oracle_flag');
const indexer = require('../lib/sar/indexer');
const { HL_COINS } = require('../lib/sar/chains');

const router = express.Router();
const limit = rateLimit({
  name: 'sar.packets',
  windowMs: 60000,
  max: 30,
  key: ipKey,
  message: 'Too many requests, slow down.',
});
const adminLimit = rateLimit({
  name: 'sar.admin',
  windowMs: 60000,
  max: 30,
  key: ipKey,
  message: 'Too many requests, slow down.',
});

const MARKETS = new Set(HL_COINS);

let operatorCheck = null;

function setOperatorCheckForTests(fn) {
  operatorCheck = fn;
}

function checkOperator(req) {
  if (operatorCheck) return operatorCheck(req);
  return require('../lib/operator_view').isOperator(req);
}

function marketFilter(body, asked) {
  if (asked == null || String(asked) === '') return body;
  return { ...body, markets: body.markets.filter((m) => m.market === String(asked)) };
}

function sendReading(res, body) {
  res.setHeader('Cache-Control', 'no-store');
  return res.json(body);
}

function badMarket(asked) {
  return asked != null && String(asked) !== '' && !MARKETS.has(String(asked));
}

router.get('/packets', (req, res) => {
  if (!flag_on()) return res.status(404).type('text/plain').send('Not found');
  if (badMarket(req.query.market)) return res.status(400).json({ error: 'bad_market' });
  return limit(req, res, () => {
    try {
      const body = indexer.snapshot(Date.now());
      return sendReading(res, marketFilter(body, req.query.market));
    } catch (err) {
      return res.status(503).json({ error: 'could_not_read', exception: err && err.name ? err.name : 'Error' });
    }
  });
});

router.get('/admin/packets', (req, res) => {
  if (!flag_on()) return res.status(404).type('text/plain').send('Not found');
  Promise.resolve()
    .then(() => checkOperator(req))
    .then((ok) => {
      if (ok !== true) return res.status(404).type('text/plain').send('Not found');
      if (badMarket(req.query.market)) return res.status(400).json({ error: 'bad_market' });
      return adminLimit(req, res, () => {
        try {
          const body = indexer.operatorSnapshot(Date.now());
          return sendReading(res, marketFilter(body, req.query.market));
        } catch (err) {
          return res.status(503).json({
            error: 'could_not_read',
            exception: err && err.name ? err.name : 'Error',
          });
        }
      });
    })
    .catch((err) => {
      console.error('operator check failed:', err && err.name ? err.name : 'Error');
      if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
    });
});

module.exports = router;
module.exports.setOperatorCheckForTests = setOperatorCheckForTests;
