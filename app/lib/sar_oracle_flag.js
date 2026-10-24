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
 * only after the flag is on, so a closed flag never opens the database.
 * The signed feed is a different door: it is not operator-gated.
 */

const path = require('node:path');
const { FLAG, flag_on } = require('./sar/flag');

function pageFile() {
  return path.join(__dirname, '..', 'public', 'tools.html');
}

function defaultOperator(req) {
  return require('./operator_view').isOperator(req);
}

/**
 * The /tools document. 404 while the flag is off, before any operator
 * check. While the flag is on, 404 unless the caller is the operator.
 */
async function sendPage(req, res, deps) {
  try {
    if (!flag_on()) return res.status(404).type('text/plain').send('Not found');
    const check = deps && deps.isOperator ? deps.isOperator : defaultOperator;
    let operator = false;
    try {
      operator = await check(req);
    } catch (err) {
      console.error('operator check failed:', err && err.name ? err.name : 'Error');
      operator = false;
    }
    if (operator !== true) return res.status(404).type('text/plain').send('Not found');
    let html;
    try {
      html = require('./tools_catalog').renderFile(process.env, { operator: true });
    } catch (err) {
      return res.status(500).type('text/plain').send(err && err.name ? err.name : 'Error');
    }
    res.setHeader('Cache-Control', 'no-store');
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
 * files while the flag is off. While the flag is on, /tools.html is the
 * rendered catalog, not the placeholder file, and only for the operator.
 * The page script is not operator-gated: it draws the catalog and
 * fetches nothing.
 */
function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/tools.html' && p !== '/js/tools-page.js') return next();
  if (!flag_on()) return res.status(404).type('text/plain').send('Not found');
  if (p === '/js/tools-page.js') return next();
  return sendPage(req, res);
}

module.exports = { FLAG, flag_on, pageGate, pageFile, sendPage };
