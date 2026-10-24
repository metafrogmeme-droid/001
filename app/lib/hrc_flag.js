'use strict';
/**
 * Kink Cap, behind HRC_ENABLED, and still operator-only when the flag is on.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off. Read
 * on each request. A deploy that forgets the variable serves 404 to the
 * public, the same answer as a path this server does not have.
 *
 * The public stays on that 404 after the flag is turned on. An operator
 * (users.plan === admin, re-read from the database) can open the page
 * either way. While the flag is off the page says it is a preview.
 *
 * The operator check is allow(). A later shared preview helper replaces
 * that one function.
 *
 * Not named enabled. That name is already a method, and a second one is
 * dropped by the ambiguity sweep.
 *
 * Auth is required inside the request, not at module load, so a unit test
 * can read flag_on without a JWT secret.
 */

const fs = require('node:fs');
const path = require('node:path');

const FLAG = 'HRC_ENABLED';

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'hrc.html');
}

function identify(req, res) {
  const { optionalAuth } = require('../auth');
  return new Promise((resolve, reject) => {
    optionalAuth(req, res, (err) => (err ? reject(err) : resolve()));
  });
}

async function allow(req, res) {
  if (!req.user) await identify(req, res);
  const { isOperator } = require('./operator_view');
  return isOperator(req);
}

function htmlFor(preview) {
  const banner = preview
    ? '<p class="hrc-preview">Admin preview. Not public.</p>'
    : '';
  return fs.readFileSync(pageFile(), 'utf8').replace('<!--PREVIEW-->', banner);
}

async function sendPage(req, res) {
  try {
    if (!(await allow(req, res))) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-cache');
    return res.type('html').send(htmlFor(!flag_on()));
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(500).type('text/plain').send(name);
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /hrc itself is
 * app.get('/hrc'). This gate keeps /hrc.html and the page script from
 * being files a non-operator can open.
 */
function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/hrc.html' && p !== '/js/hrc.js') return next();
  allow(req, res).then((ok) => {
    if (!ok) return res.status(404).type('text/plain').send('Not found');
    if (p === '/js/hrc.js') return next();
    res.setHeader('Cache-Control', 'no-cache');
    return res.type('html').send(htmlFor(!flag_on()));
  }).catch((err) => {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    res.status(500).type('text/plain').send(name);
  });
}

module.exports = { FLAG, flag_on, pageGate, pageFile, sendPage, allow };
