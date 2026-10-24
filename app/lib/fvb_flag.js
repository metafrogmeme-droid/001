'use strict';
/**
 * Fair-value bridge, behind FVB_ENABLED.
 *
 * Off unless the variable is "1" or "true". The public gets 404, the same
 * answer as a path this server does not have. An operator (plan admin, read
 * from the database) still gets the page, with the banner
 * "Admin preview. Not public."
 *
 * The door is flag_on_for (lib/admin_preview.js). The public reader is
 * flagOn. `enabled` is the name the RWA gate uses, and a second module-level
 * function of that name is not how this one is read.
 *
 * flag_on_for loads auth only when the request carries a credential.
 * Requiring auth at load time exits the process when JWT_SECRET is unset,
 * and the public-route scan loads every router without that variable.
 */

const fs = require('node:fs');
const path = require('node:path');

const { flag_on_for } = require('./admin_preview');

const FLAG = 'FVB_ENABLED';
const BANNER = 'Admin preview. Not public.';

function flagOn(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function gate(flagIsOn, isOperator) {
  if (flagIsOn) return 'open';
  if (isOperator) return 'preview';
  return 'deny';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'fvb.html');
}

function deliver(res, decision) {
  const banner = decision === 'preview'
    ? '<p class="fvb-banner" role="status">Admin preview. Not public.</p>'
    : '';
  const html = fs.readFileSync(pageFile(), 'utf8').replace('<!--FVB_BANNER-->', banner);
  res.setHeader('Cache-Control', 'no-cache');
  res.type('html').send(html);
}

function decisionFor(req) {
  return flag_on_for(req, FLAG).then(function (on) {
    if (on !== true) return 'deny';
    if (flagOn() === true) return 'open';
    return 'preview';
  }).catch(function () {
    return 'deny';
  });
}

function decide(req, res, onOpen) {
  decisionFor(req).then(function (decision) {
    if (decision === 'deny') return res.status(404).type('text/plain').send('Not found');
    return onOpen(decision);
  }).catch(function () {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
}

function sendPage(req, res) {
  decide(req, res, function (decision) { deliver(res, decision); });
}

function pageGate(req, res, next) {
  const p = req.path;
  if (p !== '/fvb.html' && p !== '/js/fvb-page.js') return next();
  decide(req, res, function (decision) {
    if (p === '/js/fvb-page.js') return next();
    deliver(res, decision);
  });
}

module.exports = { FLAG, BANNER, flagOn, gate, pageGate, pageFile, sendPage, deliver };
