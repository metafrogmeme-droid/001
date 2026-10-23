'use strict';
/**
 * GET /v1/node/:token. Admin only, whether or not the flag is on.
 * Everyone else gets the same 404 a missing path would.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const operatorView = require('../lib/operator_view');
const { readNode } = require('../lib/rxb_read');

const router = express.Router();
router.use(optionalAuth);

router.get('/:token', async (req, res) => {
  let allowed = false;
  try {
    allowed = await operatorView.isOperator(req) === true;
  } catch (err) {
    allowed = false;
  }
  if (!allowed) return res.status(404).type('text/plain').send('Not found');
  const view = readNode(req.params.token);
  return res.set('Cache-Control', 'no-store').json(view);
});

module.exports = router;
