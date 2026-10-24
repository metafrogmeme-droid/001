'use strict';
/**
 * Exit-adjusted NAV.
 *
 * GET /api/xnav/rank, GET /api/xnav/quote, GET /api/xnav/:vault,
 * POST /api/xnav/mcp (tool xnav_quote only).
 *
 * Admin-only. While XNAV_ENABLED is off, flag_on_for admits an operator
 * and the body carries the preview banner. A non-operator gets 404,
 * flag on or off. Basis points only.
 */

const express = require('express');

const { loadSnapshot, rank, detail, isAddress } = require('../lib/xnav/index');
const { quote, TOOL } = require('../lib/xnav/quote');

const router = express.Router();

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (err) {
    return res.status(404).type('text/plain').send('Not found');
  }
  return auth.optionalAuth(req, res, next);
}

function closed(res) {
  return res.status(404).type('text/plain').send('Not found');
}

function withDoor(req, res, onOpen) {
  const flag = require('../lib/xnav_flag');
  flag.decisionFor(req).then(function (decision) {
    if (decision === 'deny') return closed(res);
    const body = onOpen();
    if (decision === 'preview' && body && typeof body === 'object') body.banner = flag.BANNER;
    res.setHeader('Cache-Control', 'no-store');
    return res.json(body);
  }).catch(function () {
    if (!res.headersSent) closed(res);
  });
}

router.get('/rank', optionalAuth, function (req, res) {
  withDoor(req, res, function () {
    const loaded = loadSnapshot();
    if (loaded.readable !== true) {
      return {
        readable: false,
        reason: loaded.reason,
        ranked: null,
        unreadable_vaults: null,
        label: 'Basis points of account value. Not a dollar amount.',
      };
    }
    const board = rank(loaded.doc);
    board.label = 'Basis points of account value. Not a dollar amount.';
    return board;
  });
});

router.get('/quote', optionalAuth, function (req, res) {
  withDoor(req, res, function () {
    const loaded = loadSnapshot();
    if (loaded.readable !== true) {
      return { readable: false, reason: loaded.reason, haircut_bps: null, swing_bps: null, stress_bps: null };
    }
    return quote(loaded.doc, req.query.vault, req.query.size, req.query.flow);
  });
});

router.post('/mcp', optionalAuth, function (req, res) {
  const flag = require('../lib/xnav_flag');
  flag.decisionFor(req).then(function (decision) {
    if (decision === 'deny') return closed(res);
    const msg = req.body;
    if (!msg || msg.jsonrpc !== '2.0' || msg.method == null) {
      return res.json({ jsonrpc: '2.0', id: msg ? msg.id : null, error: { code: -32600, message: 'Invalid Request' } });
    }
    if (msg.method === 'tools/list') {
      return res.json({ jsonrpc: '2.0', id: msg.id, result: { tools: [TOOL] } });
    }
    if (msg.method !== 'tools/call') {
      return res.json({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Method not found' } });
    }
    const params = msg.params || {};
    if (params.name !== TOOL.name) {
      return res.json({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'Unknown tool' } });
    }
    const args = params.arguments || {};
    const loaded = loadSnapshot();
    const body = loaded.readable !== true
      ? { readable: false, reason: loaded.reason, haircut_bps: null, swing_bps: null, stress_bps: null }
      : quote(loaded.doc, args.vault, args.size, args.flow);
    if (decision === 'preview') body.banner = flag.BANNER;
    return res.json({ jsonrpc: '2.0', id: msg.id, result: body });
  }).catch(function () {
    if (!res.headersSent) closed(res);
  });
});

router.get('/:vault', optionalAuth, function (req, res) {
  if (!isAddress(req.params.vault)) return closed(res);
  withDoor(req, res, function () {
    const loaded = loadSnapshot();
    if (loaded.readable !== true) {
      return { readable: false, reason: loaded.reason, haircut_bps: null, stress_bps: null };
    }
    return detail(loaded.doc, req.params.vault);
  });
});

module.exports = router;
