'use strict';
/**
 * GET /api/fvb/board — the published fair-value board.
 *
 * The blend is not recomputed here. A missing file is "not published".
 * While FVB_ENABLED is off the public gets 404; an operator gets the
 * board with the preview banner. The door is flag_on_for. optionalAuth
 * stays in the handler list so the mixed-module guard sees it; flag_on_for
 * does not load auth again once the request already has a user.
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

// Lazy, like feed.js: importing auth at load exits when JWT_SECRET is absent,
// and the public-dollar scan loads every route without one. The name sits in
// the handler list so the mixed-module guard sees the middleware, not a call
// buried in the body.
function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(404).type('text/plain').send('Not found');
  }
  return auth.optionalAuth(req, res, next);
}

router.get('/board', optionalAuth, function (req, res) {
  const { flag_on_for } = require('../lib/admin_preview');
  const flag = require('../lib/fvb_flag');
  flag_on_for(req, flag.FLAG).then(function (on) {
    if (on !== true) return res.status(404).type('text/plain').send('Not found');
    const doc = readBoard();
    if (flag.flagOn() !== true) doc.banner = flag.BANNER;
    res.setHeader('Cache-Control', 'no-cache');
    res.json(doc);
  }).catch(function () {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
});

module.exports = router;
