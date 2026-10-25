'use strict';
/**
 * Exit-adjusted NAV, behind XNAV_ENABLED.
 *
 * Off unless the variable is "1" or "true". The product stays admin-only
 * when the flag is on: a non-operator gets 404, the same answer as a
 * missing path. While the flag is off, flag_on_for is the door, and an
 * operator gets the page with the banner "Admin preview. Not public."
 *
 * The public reader is flagOn. `enabled` is the name the RWA gate uses.
 */

const fs = require('node:fs');
const path = require('node:path');

const { flag_on_for, identify } = require('./admin_preview');

const FLAG = 'XNAV_ENABLED';
const BANNER = 'Admin preview. Not public.';

function flagOn(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function gate(flagIsOn, isOperator) {
  if (!isOperator) return 'deny';
  if (flagIsOn) return 'open';
  return 'preview';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'xnav.html');
}

function decisionFor(req) {
  return flag_on_for(req, FLAG).then(function (on) {
    if (on !== true) return 'deny';
    // The page route has no auth middleware. When the flag is on,
    // flag_on_for returns before it identifies anyone, so the operator
    // check would see an anonymous request and answer 404.
    return identify(req).then(function () {
      const { isOperator } = require('./operator_view');
      return isOperator(req).then(function (admin) {
        if (admin !== true) return 'deny';
        if (flagOn() === true) return 'open';
        return 'preview';
      });
    });
  }).catch(function () {
    return 'deny';
  });
}

function deliver(res, decision) {
  let html;
  try {
    html = fs.readFileSync(pageFile(), 'utf8');
  } catch (err) {
    return res.status(500).type('text/plain').send('The page could not be read');
  }
  const banner = decision === 'preview'
    ? '<p class="xnav-banner" role="status">Admin preview. Not public.</p>'
    : '';
  res.setHeader('Cache-Control', 'no-cache');
  res.type('html').send(html.replace('<!--XNAV_BANNER-->', banner));
}

function sendPage(req, res) {
  decisionFor(req).then(function (decision) {
    if (decision === 'deny') return res.status(404).type('text/plain').send('Not found');
    return deliver(res, decision);
  }).catch(function () {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
}

function pageGate(req, res, next) {
  if (req.path !== '/xnav.html') return next();
  return sendPage(req, res);
}

module.exports = { FLAG, BANNER, flagOn, gate, decisionFor, pageGate, pageFile, sendPage, deliver };
