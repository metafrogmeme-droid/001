'use strict';
/**
 * Time-to-cash, behind T2C_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" stays off. Read on each
 * request. The page at /t2c is admin-only even when the flag is on; this
 * module answers 404 for everyone else, the same answer as a missing path.
 * /tools is the coming-soon list and is not a dashboard.
 */

const path = require('node:path');
const { optionalAuth } = require('../auth');
const { isOperator } = require('./operator_view');

const FLAG = 'T2C_ENABLED';

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function t2cPage() {
  return path.join(__dirname, '..', 'public', 't2c.html');
}

function toolsPage() {
  return path.join(__dirname, '..', 'public', 'tools.html');
}

function sendTools(res) {
  res.setHeader('Cache-Control', 'no-cache');
  return res.sendFile(toolsPage());
}

/** /t2c. 404 unless the caller is the operator, flag on or off. */
function sendT2C(req, res) {
  optionalAuth(req, res, () => {
    isOperator(req).then((ok) => {
      if (!ok) return res.status(404).type('text/plain').send('Not found');
      res.setHeader('Cache-Control', 'no-cache');
      return res.sendFile(t2cPage());
    }).catch((err) => {
      console.error('[t2c] page:', err && err.constructor ? err.constructor.name : 'Error');
      return res.status(404).type('text/plain').send('Not found');
    });
  });
}

/**
 * Blocks the on-disk names ahead of express.static. /t2c and /tools are
 * app.get routes, so a canonical can name them. /t2c.html is never the
 * canonical: it would skip the operator check.
 */
function pageGate(req, res, next) {
  const p = req.path;
  if (p === '/t2c.html') return res.status(404).type('text/plain').send('Not found');
  if (p === '/tools.html') return res.status(404).type('text/plain').send('Not found');
  return next();
}

module.exports = {
  FLAG, flag_on, pageGate, sendT2C, sendTools, t2cPage, toolsPage,
};
