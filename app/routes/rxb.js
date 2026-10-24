'use strict';
/**
 * GET /v1/node/:token.
 * Flag on: a public signal. Flag off: an operator can test it, and
 * everyone else gets the same 404 a missing path would.
 * A dollar amount in the reading is not published.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { readNode } = require('../lib/rxb_read');
const { access, dollarClaim } = require('../lib/rxb_access');

const router = express.Router();
router.use(optionalAuth);

router.get('/:token', async (req, res) => {
  const deps = Object.assign({}, req.app.get('rxb') || {});
  const gate = await access(req, deps);
  if (!gate.ok) return res.status(404).type('text/plain').send('Not found');
  const view = readNode(req.params.token);
  const body = Object.assign({}, view, { kind: 'signal' });
  if (dollarClaim(body)) {
    return res.status(500).type('text/plain').send('The reading could not be shown.');
  }
  return res.set('Cache-Control', 'no-store').json(body);
});

module.exports = router;
