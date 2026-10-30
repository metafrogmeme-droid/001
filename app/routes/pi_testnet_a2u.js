'use strict';
/**
 * Temporary Testnet-only App-to-User payout (lib/pi_testnet_a2u.js).
 *
 *   GET  /api/pi/testnet-a2u           operator. Whether it is on, the amount,
 *                                      how many payments have completed, the cap.
 *   GET  /api/pi/testnet-a2u/signins   operator. Uids that signed in to the
 *                                      Testnet app, newest first.
 *   POST /api/pi/testnet-a2u/pay       operator. { uid } pays that one uid.
 *
 * Both gates: a session (authMiddleware) and the operator plan
 * (lib/operator_view.js). A signed-in person who is not the operator is
 * refused. The seed and the API key are never written into a response or a
 * log line; a failure is a code.
 */
const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { isOperator } = require('../lib/operator_view');
const a2u = require('../lib/pi_testnet_a2u');

const router = express.Router();
const limit = rateLimit({ name: 'pi_testnet_a2u', windowMs: 60000, max: 30, key: userKey });

router.use(authMiddleware);
router.use(limit);

function deps() { return router._deps || {}; }
function env() { return deps().env || process.env; }

async function operator(req, res) {
  if (await isOperator(req)) return true;
  res.status(403).json({ error: 'operator_only' });
  return false;
}

function fail(res, err) {
  const code = err && err.code && a2u.STATUSES[err.code] ? err.code : 'failed';
  console.error('Pi testnet A2U:', code);
  return res.status(a2u.STATUSES[code] || 503).json({ error: code });
}

router.get('/', async (req, res) => {
  if (!(await operator(req, res))) return undefined;
  const e = env();
  let rows;
  try {
    rows = await a2u.listPayments({ ...deps(), env: e });
  } catch (err) {
    console.error('Pi testnet A2U list failed:', err && err.name);
    return res.status(503).json({ error: 'unreadable' });
  }
  const amt = a2u.amountOf(e);
  return res.json({
    enabled: a2u.enabled(e),
    amount: amt.ok ? amt.amount : null,
    paid: rows.filter((r) => r.status === 'completed').length,
    cap: a2u.MAX_PAYMENTS,
    network: a2u.TESTNET_PASSPHRASE,
  });
});

router.get('/signins', async (req, res) => {
  if (!(await operator(req, res))) return undefined;
  try {
    const rows = await a2u.listSignins({ ...deps(), env: env() });
    return res.json({
      uids: rows.map((r) => ({
        uid: r.uid,
        signed_in_at: r.signed_in_at instanceof Date ? r.signed_in_at.toISOString() : r.signed_in_at,
      })),
    });
  } catch (err) {
    console.error('Pi testnet A2U signins failed:', err && err.name);
    return res.status(503).json({ error: 'unreadable' });
  }
});

router.post('/pay', async (req, res) => {
  if (!(await operator(req, res))) return undefined;
  try {
    const result = await a2u.pay(req.body && req.body.uid, { ...deps(), env: env() });
    return res.json(result);
  } catch (err) {
    return fail(res, err);
  }
});

module.exports = router;
