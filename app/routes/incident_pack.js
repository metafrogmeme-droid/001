'use strict';

/**
 * POST /api/incident-pack
 *
 * Panic Button follow-up: one address and a time window, public chain
 * data only. While INCIDENT_PACK_ENABLED is off, flag_on_for admits an
 * operator and the body says it is a preview. Anyone else gets 404.
 * When the flag is on, the same door admits everyone.
 *
 * optionalAuth identifies and does not 401. This route does not sign
 * and does not send. The Python module does the reads. Tests replace
 * the runner, so this file makes no network call of its own.
 */

const express = require('express');
const { spawn } = require('node:child_process');
const path = require('node:path');

const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { accessFor } = require('../lib/incident_pack_flag');

const REPO = path.join(__dirname, '..', '..');
const BODY_LIMIT = 8 * 1024;

function capBody(req, res, next) {
  const len = parseInt(req.get('content-length') || '', 10);
  if (Number.isFinite(len) && len > BODY_LIMIT) {
    res.set('Cache-Control', 'no-store');
    return res.status(413).json({ error: 'Payload too large' });
  }
  return express.json({ limit: BODY_LIMIT })(req, res, (err) => {
    if (err && (err.status === 413 || err.type === 'entity.too.large')) {
      res.set('Cache-Control', 'no-store');
      return res.status(413).json({ error: 'Payload too large' });
    }
    return next(err);
  });
}

const router = express.Router();
router.use(capBody);
router.use(optionalAuth);
router.use(rateLimit({ name: 'incident_pack', windowMs: 60000, max: 20, key: ipKey }));

function closed(res) {
  res.set('Cache-Control', 'no-store');
  return res.status(404).json({ error: 'Not found' });
}

function defaultRunner(body) {
  return new Promise((resolve) => {
    let child;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    try {
      child = spawn('python3', ['-m', 'bot.incident_pack'], {
        cwd: REPO, env: process.env, stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (err) {
      finish(null);
      return;
    }
    let out = '';
    const timer = setTimeout(() => {
      child.kill();
      finish(null);
    }, 55000);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', () => {});
    child.on('error', () => {
      clearTimeout(timer);
      finish(null);
    });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        const parsed = JSON.parse(out);
        finish(parsed && typeof parsed === 'object' ? parsed : null);
      } catch (err) {
        finish(null);
      }
    });
    child.stdin.end(JSON.stringify(body));
  });
}

let runner = defaultRunner;
function setRunner(fn) { runner = typeof fn === 'function' ? fn : defaultRunner; }

router.post('/', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const result = await runner({
      address: typeof body.address === 'string' ? body.address : '',
      start: typeof body.start === 'string' ? body.start : '',
      end: typeof body.end === 'string' ? body.end : '',
      chain: typeof body.chain === 'string' ? body.chain : '',
    });
    const unread = result && result.readable === false && result.ok !== true && !result.refused;
    if (!result || unread) {
      res.set('Cache-Control', 'private, no-store');
      return res.status(503).json({
        error: result && typeof result.error === 'string' ? result.error : 'IncidentSourceUnreadable',
        readable: false,
      });
    }
    if (result.refused || result.ok !== true) {
      res.set('Cache-Control', 'private, no-store');
      const payload = { error: result.error || 'IncidentRequestRefused', readable: false };
      if (typeof result.message === 'string') payload.message = result.message;
      return res.status(400).json(payload);
    }
    result.preview = access.preview === true;
    result.banner = access.banner;
    res.set('Cache-Control', 'private, no-store');
    return res.json(result);
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    console.error('[incident-pack]', name);
    if (!res.headersSent) {
      return res.status(503).json({ error: name, readable: false });
    }
  }
});

module.exports = router;
module.exports.setRunner = setRunner;
module.exports.capBody = capBody;
module.exports.bodyLimit = BODY_LIMIT;
