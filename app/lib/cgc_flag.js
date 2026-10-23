'use strict';
/**
 * Conservation-brake status, behind a flag, and admin-only either way.
 *
 * CGC_ENABLED unset is off. The words that count as on are 1, true, on
 * and yes, the same set bot/cgc.py reads. This function is flag_on. A
 * module-level function named enabled is a name the method-ambiguity
 * sweep already uses, and this file does not add another.
 *
 * A non-admin is answered 404 whether the flag is off or on. There is
 * no public dashboard. An admin with the flag off sees the preview
 * banner. An admin with the flag on sees the same status without it.
 *
 * The page gate is mounted ahead of express.static. public/cgc.html is
 * on disk so the page tests can read it; without this gate the file
 * would be served while the API is 404.
 */

const path = require('node:path');
const { isOperator } = require('./operator_view');
const { optionalAuth } = require('../auth');

const FLAG = 'CGC_ENABLED';
const BANNER = 'Admin preview. Not public.';
const ON = new Set(['1', 'true', 'on', 'yes']);

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return ON.has(v);
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'cgc.html');
}

/** Not an admin, whatever the flag says. Fail closed when the check throws. */
async function accessFor(req) {
  let admin = false;
  try {
    admin = await isOperator(req);
  } catch (err) {
    console.error('[cgc] admin check failed:', err && err.constructor ? err.constructor.name : 'Error');
    admin = false;
  }
  if (!admin) return { ok: false, status: 404 };
  const preview = !flag_on();
  return { ok: true, preview, banner: preview ? BANNER : null };
}

function identify(req, res) {
  return new Promise((resolve, reject) => {
    optionalAuth(req, res, (err) => (err ? reject(err) : resolve()));
  });
}

/** The /cgc document. 404 for anyone who is not an admin. */
async function sendPage(req, res) {
  try {
    await identify(req, res);
    const access = await accessFor(req);
    if (!access.ok) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-store');
    return res.sendFile(pageFile());
  } catch (err) {
    console.error('[cgc] page failed:', err && err.constructor ? err.constructor.name : 'Error');
    return res.status(404).type('text/plain').send('Not found');
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /cgc itself is
 * app.get('/cgc') in server.js. This gate keeps /cgc.html and the page
 * script from being served as static files to a non-admin.
 */
async function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/cgc.html' && p !== '/js/cgc.js') return next();
  try {
    await identify(req, res);
    const access = await accessFor(req);
    if (!access.ok) return res.status(404).type('text/plain').send('Not found');
    if (p === '/js/cgc.js') return next();
    res.setHeader('Cache-Control', 'no-store');
    return res.sendFile(pageFile());
  } catch (err) {
    console.error('[cgc] page gate failed:', err && err.constructor ? err.constructor.name : 'Error');
    return res.status(404).type('text/plain').send('Not found');
  }
}

module.exports = { FLAG, BANNER, flag_on, accessFor, pageGate, pageFile, sendPage };
