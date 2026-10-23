'use strict';
/**
 * ADL re-entry cover and parametric stop-gap cover.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * Cash-settled cover on a price outcome is a derivative or insurance under
 * MiFID II. No EU customer may use either draft until an AFM licence is in
 * force. Both flags are off unless the environment says "1" or "true". A
 * route whose flag is off answers 404, the same answer as a path this server
 * does not have. A route whose flag is on still reports offered:false: the
 * flag is a testnet seam, not a customer offer.
 *
 * The server holds no key and signs nothing. The page gate is mounted ahead
 * of express.static so the files on disk are not served while the flags are off.
 */

const path = require('node:path');
const flags = require('../../contracts/cover/flags.cjs');

const HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';
const CHAINS = Object.freeze(['hyperevm-testnet', 'base-sepolia']);

const PAGES = Object.freeze({
  adl: 'cover-adl.html',
  stopgap: 'cover-stopgap.html',
});

function flagOn(kind, env) {
  if (kind === 'adl') return flags.adlOn(env);
  if (kind === 'stopgap') return flags.stopGapOn(env);
  return false;
}

function publicStatus() {
  return {
    offered: false,
    reason: 'licence_review_pending',
    chains: CHAINS.slice(),
    notice: HOLD,
  };
}

function pageFile(kind) {
  const name = PAGES[kind];
  if (!name) return null;
  return path.join(__dirname, '..', 'public', name);
}

function sendPage(res, kind) {
  const file = pageFile(kind);
  if (!file || !flagOn(kind)) return res.status(404).type('text/plain').send('Not found');
  res.setHeader('Cache-Control', 'no-cache');
  return res.sendFile(file);
}

/**
 * Blocks the on-disk names ahead of express.static. /cover-adl and
 * /cover-stopgap are app.get routes in server.js. This gate keeps the .html
 * files and the page script from being served while the flags are off.
 * The script is available when either flag is on, because both pages use it.
 */
function pageGate(req, res, next) {
  const p = req.path;
  if (p === '/js/cover.js') {
    if (!flagOn('adl') && !flagOn('stopgap')) return res.status(404).type('text/plain').send('Not found');
    return next();
  }
  const kind = p === '/cover-adl.html' ? 'adl' : (p === '/cover-stopgap.html' ? 'stopgap' : null);
  if (!kind) return next();
  return sendPage(res, kind);
}

module.exports = {
  HOLD,
  CHAINS,
  flagOn,
  publicStatus,
  pageFile,
  sendPage,
  pageGate,
};
