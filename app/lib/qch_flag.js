'use strict';
/**
 * Quantum custody haircut, behind QCH_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off.
 * A non-admin is 404 while the flag is off and 404 while it is on.
 * An admin with the flag off sees the preview banner.
 *
 * flag_on_for is the preview door. isOperator is required as well, so a
 * public-on flag does not open the page. Both are required lazily: this
 * module is registered from admin_preview, and requiring that file here
 * at load time would cycle. auth is not loaded at require time.
 */

const path = require('node:path');

const FLAG = 'QCH_ENABLED';
const BANNER = 'Admin preview. Not public.';

function flagOn(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'qch.html');
}

async function identify(req) {
  const preview = require('./admin_preview');
  if (!preview.hasCredential(req)) return;
  await preview.identify(req);
}

/** Admin, flag off or on. Fail closed when the check throws. */
async function accessFor(req) {
  const { flag_on_for } = require('./admin_preview');
  const { isOperator } = require('./operator_view');
  try {
    await identify(req);
    const door = await flag_on_for(req, FLAG);
    const admin = await isOperator(req);
    if (door !== true || admin !== true) return { ok: false, status: 404 };
    const preview = flagOn() !== true;
    return { ok: true, preview, banner: preview ? BANNER : null };
  } catch (err) {
    return { ok: false, status: 404, error: err && err.constructor ? err.constructor.name : 'Error' };
  }
}

/** The /qch document. 404 for anyone who is not an admin. */
async function sendPage(req, res) {
  try {
    const access = await accessFor(req);
    if (!access.ok) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-store');
    return res.sendFile(pageFile());
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    console.error('[qch] page failed:', name);
    return res.status(404).type('text/plain').send('Not found');
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /qch itself is
 * app.get('/qch') in server.js.
 */
async function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/qch.html' && p !== '/js/qch.js') return next();
  try {
    const access = await accessFor(req);
    if (!access.ok) return res.status(404).type('text/plain').send('Not found');
    if (p === '/js/qch.js') return next();
    res.setHeader('Cache-Control', 'no-store');
    return res.sendFile(pageFile());
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    console.error('[qch] page gate failed:', name);
    return res.status(404).type('text/plain').send('Not found');
  }
}

module.exports = { FLAG, BANNER, flagOn, accessFor, pageGate, pageFile, sendPage };
