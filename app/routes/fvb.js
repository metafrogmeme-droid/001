'use strict';
/**
 * GET /api/fvb/board — the published fair-value board.
 *
 * The blend is not recomputed here. A missing file is "not published".
 * While FVB_ENABLED is off the public gets 404; an operator gets the
 * board with the preview banner.
 */

const express = require('express');
const fs = require('node:fs');
const path = require('node:path');

const router = express.Router();
const LABEL = 'Public signals, not advice.';

function boardPath() {
  if (process.env.FVB_BOARD_PATH) return process.env.FVB_BOARD_PATH;
  return path.join(__dirname, '..', '..', 'data', 'fvb_board.json');
}

function readBoard() {
  let raw;
  try {
    raw = fs.readFileSync(boardPath(), 'utf8');
  } catch (e) {
    if (e && e.code === 'ENOENT') {
      return { available: false, reason: 'not_published', label: LABEL };
    }
    return { available: false, reason: 'unreadable', label: LABEL };
  }
  try {
    const doc = JSON.parse(raw);
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      return { available: false, reason: 'unreadable', label: LABEL };
    }
    return doc;
  } catch (e) {
    return { available: false, reason: 'unreadable', label: LABEL };
  }
}

router.get('/board', function (req, res) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(404).type('text/plain').send('Not found');
  }
  auth.optionalAuth(req, res, function () {
    const { isOperator } = require('../lib/operator_view');
    const flag = require('../lib/fvb_flag');
    Promise.resolve(isOperator(req)).then(function (op) {
      const decision = flag.gate(flag.flagOn(), !!op);
      if (decision === 'deny') return res.status(404).type('text/plain').send('Not found');
      const doc = readBoard();
      if (decision === 'preview') doc.banner = flag.BANNER;
      res.setHeader('Cache-Control', 'no-cache');
      res.json(doc);
    }).catch(function () {
      res.status(404).type('text/plain').send('Not found');
    });
  });
});

module.exports = router;
