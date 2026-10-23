'use strict';
/**
 * Sandwich restitution, behind SRB_ENABLED.
 *
 * Off unless the variable is "1" or "true". Read on each request. While it
 * is off, a caller who is not the operator gets the same 404 a missing path
 * gets. The operator still gets the page, and the client is told to show
 * "Admin preview. Not public."
 *
 * The page gate is mounted ahead of express.static. public/srb.html is on
 * disk so the page tests can read it; without this gate the file would be
 * served while the flag is off.
 */

const path = require('node:path');

const { optionalAuth } = require('../auth');
const { isOperator } = require('./operator_view');

const FLAG = 'SRB_ENABLED';
const PREVIEW = 'Admin preview. Not public.';

function flag_on(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'srb.html');
}

function identify(req, res) {
  return new Promise((resolve) => {
    optionalAuth(req, res, () => resolve());
  });
}

/**
 * Whether this caller may see the shadow page.
 *
 * The flag on: everyone. The flag off: the operator only, and the answer
 * is marked preview. A failed operator read is not an operator.
 */
async function access(req, res) {
  await identify(req, res);
  const on = flag_on();
  let admin = false;
  try {
    admin = await isOperator(req);
  } catch (err) {
    admin = false;
  }
  if (!on && !admin) return { ok: false, preview: false, admin: false };
  return { ok: true, preview: !on, admin: admin };
}

function sendPage(req, res) {
  access(req, res).then((decision) => {
    if (!decision.ok) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-cache');
    return res.sendFile(pageFile());
  }).catch(() => {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
}

/**
 * Blocks the on-disk names ahead of express.static. /srb itself is
 * app.get('/srb') in server.js, so a canonical can name it.
 */
function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/srb.html' && p !== '/js/srb.js') return next();
  access(req, res).then((decision) => {
    if (!decision.ok) return res.status(404).type('text/plain').send('Not found');
    if (p === '/js/srb.js') return next();
    res.setHeader('Cache-Control', 'no-cache');
    return res.sendFile(pageFile());
  }).catch(() => {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
}

module.exports = { FLAG, PREVIEW, access, flag_on, pageFile, pageGate, sendPage };
