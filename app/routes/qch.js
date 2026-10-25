'use strict';
/**
 * GET /api/qch/score and GET /api/qch/policy.
 *
 * Admin-only. A non-admin is 404 while QCH_ENABLED is off and 404 while
 * it is on. The body is the committed reading: sats and bps, each score
 * labelled an estimate. This process does not sign and it holds no key.
 *
 * optionalAuth is lazy. Loading auth at require time exits when
 * JWT_SECRET is absent, and the public-route scan loads this file
 * without one.
 */

const express = require('express');

const router = express.Router();

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(404).json({ error: name });
  }
  return auth.optionalAuth(req, res, next);
}

function refused(res) {
  return res.status(404).json({ error: 'Not found' });
}

function failed(res, err) {
  const name = err && err.constructor ? err.constructor.name : 'Error';
  console.error('[qch]', name);
  if (!res.headersSent) res.status(500).json({ error: name });
}

router.get('/score', optionalAuth, function (req, res) {
  const { accessFor } = require('../lib/qch_flag');
  const { scorePayload } = require('../lib/qch_reading');
  accessFor(req).then(function (access) {
    if (!access.ok) return refused(res);
    res.set('Cache-Control', 'no-store').json(scorePayload({ preview: access.preview }));
  }).catch(function (err) {
    failed(res, err);
  });
});

router.get('/policy', optionalAuth, function (req, res) {
  const { accessFor } = require('../lib/qch_flag');
  const { policyPayload } = require('../lib/qch_reading');
  const scenario = typeof req.query.scenario === 'string' ? req.query.scenario : '';
  accessFor(req).then(function (access) {
    if (!access.ok) return refused(res);
    res.set('Cache-Control', 'no-store').json(policyPayload(scenario, { preview: access.preview }));
  }).catch(function (err) {
    failed(res, err);
  });
});

module.exports = router;
