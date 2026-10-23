'use strict';
/**
 * Fair-value bridge, behind FVB_ENABLED.
 *
 * Off unless the variable is "1" or "true". The public gets 404, the same
 * answer as a path this server does not have. An operator (plan admin, read
 * from the database) still gets the page, with the banner
 * "Admin preview. Not public."
 *
 * The function is flagOn. `enabled` is the name the RWA gate uses, and a
 * second module-level function of that name is not how this one is read.
 *
 * Auth is required inside the request. Requiring it at load time exits the
 * process when JWT_SECRET is unset, and the public-route scan loads every
 * router without that variable.
 */

const fs = require('node:fs');
const path = require('node:path');

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

function decide(req, res, onOpen) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(404).type('text/plain').send('Not found');
  }
  auth.optionalAuth(req, res, function () {
    const { isOperator } = require('./operator_view');
    Promise.resolve(isOperator(req)).then(function (op) {
      const decision = gate(flagOn(), !!op);
      if (decision === 'deny') return res.status(404).type('text/plain').send('Not found');
      return onOpen(decision);
    }).catch(function () {
      res.status(404).type('text/plain').send('Not found');
    });
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
