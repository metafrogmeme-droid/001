'use strict';

/**
 * POST /api/fees.
 *
 * Admin only, and 404 unless SILENT_FEE_CHECK_ENABLED is 1 or true.
 * optionalAuth identifies and does not 401, so an anonymous caller is a
 * 404 rather than a login wall. A signed-in account that is not the
 * operator is the same 404, whether or not the flag is on. A failed
 * operator check is a 404.
 *
 * The body is one public address. This route does not sign, does not
 * approve a builder, and does not read a key. The Python module does
 * the info reads. Per-builder attribution stays behind
 * SILENT_FEE_EXPLORER_ENABLED, which this process does not turn on.
 */

const express = require('express');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { isOperator } = require('../lib/operator_view');
const { flag_on } = require('../lib/silent_fee_flag');

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
router.use(rateLimit({ name: 'silent_fee', windowMs: 60000, max: 20, key: ipKey }));

function refused(res) {
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
      child = spawn('python3', ['-m', 'bot.silent_fee'], {
        cwd: REPO, env: process.env, stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (err) {
      finish({ available: false });
      return;
    }
    let out = '';
    const timer = setTimeout(() => {
      child.kill();
      finish({ available: false });
    }, 55000);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', () => {});
    child.on('error', () => {
      clearTimeout(timer);
      finish({ available: false });
    });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        const parsed = JSON.parse(out);
        finish(parsed && typeof parsed === 'object' ? parsed : { available: false });
      } catch (err) {
        finish({ available: false });
      }
    });
    child.stdin.end(JSON.stringify(body));
  });
}

let runner = defaultRunner;
function setRunner(fn) { runner = typeof fn === 'function' ? fn : defaultRunner; }

router.post('/', async (req, res) => {
  try {
    if (!(await isOperator(req))) return refused(res);
  } catch (err) {
    return refused(res);
  }
  if (!flag_on()) return refused(res);
  const body = req.body && typeof req.body === 'object' ? req.body : {};
  const address = typeof body.address === 'string' ? body.address : '';
  const days = body.days;
  try {
    const result = await runner({ address: address, days: days });
    if (!result || result.available === false) return refused(res);
    if (result.refused) {
      res.set('Cache-Control', 'no-store');
      return res.status(400).json({ error: result.message || 'Bad request' });
    }
    res.set('Cache-Control', 'no-store');
    return res.json(result);
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    console.error('[silent-fee]', name);
    return res.status(500).json({ error: name });
  }
});

module.exports = router;
module.exports.setRunner = setRunner;
module.exports.capBody = capBody;
module.exports.bodyLimit = BODY_LIMIT;
