'use strict';
/**
 * Claw Firewall, behind DRAIN_FIREWALL_ENABLED.
 *
 * Off unless the variable is "1" or "true". The public gets 404. An operator
 * (plan admin, read from the database) still gets the page, with the banner
 * "Admin preview. Not public." When the flag is on, the page is public.
 *
 * The door is flag_on_for (lib/admin_preview.js). The public reader is flagOn.
 */

const fs = require('node:fs');
const path = require('node:path');

const { flag_on_for } = require('./admin_preview');

const FLAG = 'DRAIN_FIREWALL_ENABLED';
const BANNER = 'Admin preview. Not public.';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function gate(flagIsOn, isOperator) {
  if (flagIsOn) return 'open';
  if (isOperator) return 'preview';
  return 'deny';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'claw-firewall.html');
}

function deliver(res, decision) {
  const banner = decision === 'preview'
    ? '<p class="cf-banner" role="status">Admin preview. Not public.</p>'
    : '';
  const html = fs.readFileSync(pageFile(), 'utf8').replace('<!--CF_BANNER-->', banner);
  res.setHeader('Cache-Control', 'no-store');
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
  if (p !== '/claw-firewall.html') return next();
  decide(req, res, function (decision) { deliver(res, decision); });
}

module.exports = { FLAG, BANNER, flagOn, gate, pageGate, pageFile, sendPage, deliver };
