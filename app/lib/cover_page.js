'use strict';
/**
 * ADL re-entry cover and parametric stop-gap cover.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * Cash-settled cover on a price outcome is a derivative or insurance under
 * MiFID II. No EU customer may use either draft until an AFM licence is in
 * force. Both flags are off unless the environment says "1" or "true". A
 * route whose flag is off answers 404 to the public, the same answer as a
 * path this server does not have. An admin can preview that route: the
 * check is flag_on_for, which re-reads users.plan. A route whose flag is
 * on is public and still reports offered:false. The flag is a testnet
 * seam, not a customer offer.
 *
 * The server holds no key and signs nothing. The page gate is mounted ahead
 * of express.static so the files on disk are not served while the flags are
 * off for this caller.
 */

const fs = require('node:fs');
const path = require('node:path');
const flags = require('../../contracts/cover/flags.cjs');
const { flag_on_for, withBanner } = require('./admin_preview');

const HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';
const CHAINS = Object.freeze(['hyperevm-testnet', 'base-sepolia']);

const PAGES = Object.freeze({
  adl: 'cover-adl.html',
  stopgap: 'cover-stopgap.html',
});

const FLAG_NAME = Object.freeze({
  adl: 'COVER_ADL_ENABLED',
  stopgap: 'COVER_STOPGAP_ENABLED',
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

/** public when the flag is on, preview for an admin, closed otherwise. */
async function visibility(req, kind) {
  if (flagOn(kind)) return 'public';
  const name = FLAG_NAME[kind];
  if (!name) return 'closed';
  try {
    if ((await flag_on_for(req, name)) === true) return 'preview';
  } catch (e) {
    return 'closed';
  }
  return 'closed';
}

async function sendPage(req, res, kind) {
  const file = pageFile(kind);
  try {
    if (!file) return res.status(404).type('text/plain').send('Not found');
    const who = await visibility(req, kind);
    if (who === 'closed') return res.status(404).type('text/plain').send('Not found');
    if (who === 'preview') {
      res.setHeader('Cache-Control', 'private, no-store');
      return res.type('html').send(withBanner(fs.readFileSync(file, 'utf8')));
    }
    res.setHeader('Cache-Control', 'no-cache');
    return res.sendFile(file);
  } catch (e) {
    if (!res.headersSent) res.status(500).type('text/plain').send('Not found');
  }
}

/**
 * Blocks the on-disk names ahead of express.static. /cover-adl and
 * /cover-stopgap are app.get routes in server.js. This gate keeps the .html
 * files and the page script from being served while the flags are off for
 * this caller. The script is available when either product is visible,
 * because both pages use it.
 */
async function pageGate(req, res, next) {
  try {
    const p = req.path;
    if (p === '/js/cover.js') {
      const adl = await visibility(req, 'adl');
      const stop = await visibility(req, 'stopgap');
      if (adl === 'closed' && stop === 'closed') {
        return res.status(404).type('text/plain').send('Not found');
      }
      return next();
    }
    const kind = p === '/cover-adl.html' ? 'adl' : (p === '/cover-stopgap.html' ? 'stopgap' : null);
    if (!kind) return next();
    return sendPage(req, res, kind);
  } catch (e) {
    if (!res.headersSent) res.status(500).type('text/plain').send('Not found');
  }
}

module.exports = {
  HOLD,
  CHAINS,
  FLAG_NAME,
  flagOn,
  publicStatus,
  pageFile,
  visibility,
  sendPage,
  pageGate,
};
