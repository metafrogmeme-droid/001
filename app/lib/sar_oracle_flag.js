'use strict';
/**
 * SaR Oracle and the Tools page, behind one flag.
 *
 * Off unless SAR_ORACLE_ENABLED is "1" or "true". Read on each request.
 * A process that booted with the flag unset stays off until the
 * environment says otherwise, and a deploy that forgets the variable
 * serves 404 — the same answer as a path this server does not have.
 *
 * The page gate is mounted ahead of express.static. public/tools.html
 * is on disk so the page tests can read it; without this gate the file
 * would be served while the API is 404.
 *
 * While the flag is on, the HTML is the operator's. The check runs
 * only after the flag is on. While the flag is off, an admin may
 * preview the same HTML (`flag_on_for`); that path does not use the
 * injected operator seam, so a closed flag still does not count one.
 * The signed feed is a different door: it is not operator-gated, and
 * a preview does not open it.
 */

const path = require('node:path');
const { FLAG, flag_on } = require('./sar/flag');
const { flag_on_for, identify, withBanner } = require('./admin_preview');

function pageFile() {
  return path.join(__dirname, '..', 'public', 'tools.html');
}

function defaultOperator(req) {
  return require('./operator_view').isOperator(req);
}

/**
 * Whether this caller would receive the /tools document.
 *
 * The same decision `sendPage` uses, and nothing else. While the flag
 * is on, that is the operator. While it is off, that is an admin
 * preview (`flag_on_for`). A failed read is closed. The nav asks this
 * rather than a client-side flag, and the page route still answers
 * 404 on its own.
 */
async function access(req, deps) {
  const on = flag_on();
  if (on) {
    const injected = deps && deps.isOperator;
    const check = injected || defaultOperator;
    try {
      if (!injected) await identify(req);
      return { operator: (await check(req)) === true, preview: false };
    } catch (err) {
      console.error('operator check failed:', err && err.name ? err.name : 'Error');
      return { operator: false, preview: false };
    }
  }
  const preview = (await flag_on_for(req, FLAG)) === true;
  return { operator: preview === true, preview: preview === true };
}

async function openFor(req) {
  const gate = await access(req);
  return gate.operator === true;
}

/**
 * The /tools document. 404 while the flag is off for this caller.
 * While the flag is on, 404 unless the caller is the operator. An
 * admin, while the flag is off, gets the same operator HTML plus the
 * preview banner. That preview does not call `deps.isOperator`.
 */
async function sendPage(req, res, deps) {
  try {
    const gate = await access(req, deps);
    const operator = gate.operator === true;
    const preview = gate.preview === true;
    if (operator !== true) return res.status(404).type('text/plain').send('Not found');
    let html;
    try {
      html = require('./tools_catalog').renderFile(process.env, { operator: true, preview });
    } catch (err) {
      return res.status(500).type('text/plain').send(err && err.name ? err.name : 'Error');
    }
    if (preview) html = withBanner(html);
    res.setHeader('Cache-Control', preview ? 'private, no-store' : 'no-store');
    return res.type('html').send(html);
  } catch (err) {
    if (!res.headersSent) {
      res.status(500).type('text/plain').send(err && err.name ? err.name : 'Error');
    }
    return undefined;
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /tools itself is
 * app.get('/tools') in server.js, so a canonical can name it. This gate
 * keeps /tools.html and the page script from being served as static
 * files while the flag is off for this caller. An admin preview is
 * allowed through. While the flag is on, /tools.html is the rendered
 * catalog, not the placeholder file, and only for the operator. The
 * page script is not operator-gated once the caller may see the page:
 * it draws the catalog and fetches nothing.
 */
async function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/tools.html' && p !== '/js/tools-page.js') return next();
  try {
    if (!flag_on() && (await flag_on_for(req, FLAG)) !== true) {
      return res.status(404).type('text/plain').send('Not found');
    }
    if (p === '/js/tools-page.js') return next();
    return sendPage(req, res);
  } catch (err) {
    if (!res.headersSent) {
      res.status(500).type('text/plain').send(err && err.name ? err.name : 'Error');
    }
    return undefined;
  }
}

module.exports = { FLAG, flag_on, pageGate, pageFile, sendPage, openFor };
