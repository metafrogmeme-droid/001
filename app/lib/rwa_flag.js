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

const path = require('node:path');

const FLAG = 'RWA_PRECHECK_ENABLED';

function enabled(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'rwa.html');
}

function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/rwa' && p !== '/rwa.html' && p !== '/js/rwa-precheck.js') return next();
  if (!enabled()) return res.status(404).type('text/plain').send('Not found');
  if (p === '/js/rwa-precheck.js') return next();
  res.setHeader('Cache-Control', 'no-cache');
  return res.sendFile(pageFile());
}

module.exports = { FLAG, enabled, pageGate, pageFile };
