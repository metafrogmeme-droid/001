'use strict';
/**
 * Claw Watch, behind DRAIN_WATCH_ENABLED.
 *
 * The public reader uses the same on and off words as bot/config.py
 * _env_switch. Unset is off. An unrecognised word is off. While the flag
 * is off the public gets 404. An operator still gets the page, with the
 * banner "Admin preview. Not public."
 *
 * The door is flag_on_for. This file's reader is flagOn. A second
 * module-level function named enabled is not how this flag is read.
 */

const fs = require('node:fs');
const path = require('node:path');

const { flag_on_for, withBanner, BANNER } = require('./admin_preview');

const FLAG = 'DRAIN_WATCH_ENABLED';
const OFF = new Set(['off', 'false', '0', 'no', 'disabled']);
const ON = new Set(['on', 'true', '1', 'yes', 'enabled']);

function flagOn(env) {
  const src = env || process.env;
  if (!Object.prototype.hasOwnProperty.call(src, FLAG)) return false;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  if (v === '' || OFF.has(v)) return false;
  if (ON.has(v)) return true;
  return false;
}

function gate(flagIsOn, isOperator) {
  if (flagIsOn) return 'open';
  if (isOperator) return 'preview';
  return 'deny';
}

function pageFile() {
  return path.join(__dirname, '..', 'public', 'claw-watch.html');
}

function deliver(res, decision) {
  let html = fs.readFileSync(pageFile(), 'utf8');
  if (decision === 'preview') html = withBanner(html);
  res.setHeader('Cache-Control', decision === 'preview' ? 'private, no-store' : 'no-cache');
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
  if (p !== '/claw-watch.html' && p !== '/js/claw-watch.js') return next();
  decide(req, res, function (decision) {
    if (p === '/js/claw-watch.js') return next();
    deliver(res, decision);
  });
}

module.exports = {
  FLAG: FLAG,
  BANNER: BANNER,
  flagOn: flagOn,
  gate: gate,
  pageGate: pageGate,
  pageFile: pageFile,
  sendPage: sendPage,
  deliver: deliver,
};
