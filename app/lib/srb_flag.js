'use strict';
/**
 * Sandwich restitution, behind SRB_ENABLED.
 *
 * Off unless the variable is "1" or "true". While it is off, the public
 * gets the same 404 a missing path gets, and an operator gets the page
 * with the shared admin-preview banner. The decision is flag_on_for
 * (lib/admin_preview.js): the public reader is flag_on, and a failed
 * operator read is not an operator.
 *
 * The page gate is mounted ahead of express.static. public/srb.html is on
 * disk so the page tests can read it; without this gate the file would be
 * served while the flag is off.
 */

const path = require('node:path');

const { flag_on_for, BANNER } = require('./admin_preview');
const { isOperator } = require('./operator_view');

const FLAG = 'SRB_ENABLED';
const PREVIEW = BANNER;

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

/**
 * Whether this caller may see the shadow page.
 *
 * flag_on_for is the gate. While the flag is off that is the operator
 * only, and the client is told this is a preview. While the flag is on
 * the page is public, and the admin block stays with the operator.
 */
async function access(req) {
  let on = false;
  try {
    on = await flag_on_for(req, FLAG);
  } catch (err) {
    on = false;
  }
  if (on !== true) return { ok: false, preview: false, admin: false };
  if (flag_on() === true) {
    let admin = false;
    try {
      admin = (await isOperator(req)) === true;
    } catch (err) {
      admin = false;
    }
    return { ok: true, preview: false, admin };
  }
  return { ok: true, preview: true, admin: true };
}

function sendPage(req, res) {
  access(req).then((decision) => {
    if (!decision.ok) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', decision.preview ? 'private, no-store' : 'no-cache');
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
  access(req).then((decision) => {
    if (!decision.ok) return res.status(404).type('text/plain').send('Not found');
    if (p === '/js/srb.js') return next();
    res.setHeader('Cache-Control', decision.preview ? 'private, no-store' : 'no-cache');
    return res.sendFile(pageFile());
  }).catch(() => {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
}

module.exports = { FLAG, PREVIEW, access, flag_on, pageFile, pageGate, sendPage };
