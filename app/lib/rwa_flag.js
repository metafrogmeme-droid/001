'use strict';
/**
 * RWA pre-check, behind a flag.
 *
 * Off unless RWA_PRECHECK_ENABLED is "1" or "true". Read on each request:
 * a process that booted with the flag unset stays off until the environment
 * says otherwise, and a deploy that forgets the variable serves 404 — the
 * same answer as a path this server does not have.
 *
 * The page gate is mounted ahead of express.static. public/rwa.html is on
 * disk so the page tests can read it; without this gate the file would be
 * served while the API is 404.
 */

const fs = require('node:fs');
const path = require('node:path');

const FLAG = 'RWA_PRECHECK_ENABLED';
const { flag_on_for, withBanner } = require('./admin_preview');

function enabled(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'rwa.html');
}

function asRequest(reqOrRes, maybeRes) {
  if (maybeRes) return { req: reqOrRes, res: maybeRes };
  return { req: null, res: reqOrRes };
}

/**
 * The /rwa document. 404 while the flag is off for this caller, the same
 * answer as a missing path. An admin still receives it, with the preview
 * banner. `sendPage(res)` stays the public-flag-only call.
 */
async function sendPage(reqOrRes, maybeRes) {
  const { req, res } = asRequest(reqOrRes, maybeRes);
  try {
    if (enabled()) {
      res.setHeader('Cache-Control', 'no-cache');
      return res.sendFile(pageFile());
    }
    if (!(await flag_on_for(req, FLAG))) {
      return res.status(404).type('text/plain').send('Not found');
    }
    res.setHeader('Cache-Control', 'private, no-store');
    return res.type('html').send(withBanner(fs.readFileSync(pageFile(), 'utf8')));
  } catch (e) {
    if (!res.headersSent) res.status(500).type('text/plain').send('Not found');
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /rwa itself is
 * app.get('/rwa') in server.js, the same shape as the other pages, so a
 * canonical can name it. This gate only keeps /rwa.html and the page script
 * from being served as static files while the flag is off.
 */
async function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/rwa.html' && p !== '/js/rwa-precheck.js') return next();
  try {
    if (!enabled() && !(await flag_on_for(req, FLAG))) {
      return res.status(404).type('text/plain').send('Not found');
    }
    if (p === '/js/rwa-precheck.js') return next();
    if (!enabled()) {
      res.setHeader('Cache-Control', 'private, no-store');
      return res.type('html').send(withBanner(fs.readFileSync(pageFile(), 'utf8')));
    }
    res.setHeader('Cache-Control', 'no-cache');
    return res.sendFile(pageFile());
  } catch (e) {
    if (!res.headersSent) res.status(500).type('text/plain').send('Not found');
  }
}

module.exports = { FLAG, enabled, pageGate, pageFile, sendPage };
