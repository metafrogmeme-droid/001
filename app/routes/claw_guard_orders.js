'use strict';

/**
 * POST /api/claw-guard/check.
 *
 * A per-order check. Off unless CLAW_GUARD_ENABLED is 1 or true, except
 * for an operator. The body is evaluated by the bot module. This process
 * does not sign and it does not read a trade key.
 *
 * optionalAuth is lazy. Loading auth at require time exits when
 * JWT_SECRET is absent, and a route scan loads this file without one.
 */

const express = require('express');
const { spawn } = require('node:child_process');
const path = require('path');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { flagOn } = require('../lib/claw_guard_flag');

const REPO = path.join(__dirname, '..', '..');
const BANNER = 'Admin preview. Not public.';

const router = express.Router();

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(401).json({ error: name });
  }
  return auth.optionalAuth(req, res, next);
}

router.use(optionalAuth);
router.use(rateLimit({ name: 'claw-guard', windowMs: 60000, max: 30, key: ipKey }));

function failed(name) {
  return { ok: false, error: name || 'Error' };
}

function runPython(body) {
  return new Promise((resolve) => {
    let child;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    try {
      child = spawn('python3', ['-m', 'bot.claw_guard.service'], {
        cwd: REPO, env: process.env, stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (e) {
      finish(failed(e && e.name));
      return;
    }
    let out = '';
    const timer = setTimeout(() => {
      child.kill();
      finish(failed('Timeout'));
    }, 15000);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', () => {});
    child.on('error', (e) => {
      clearTimeout(timer);
      finish(failed(e && e.name));
    });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        const parsed = JSON.parse(out);
        finish(parsed && typeof parsed === 'object' ? parsed : failed('SyntaxError'));
      } catch (e) {
        finish(failed('SyntaxError'));
      }
    });
    try {
      child.stdin.end(JSON.stringify(body));
    } catch (e) {
      clearTimeout(timer);
      finish(failed(e && e.name));
    }
  });
}

let runner = runPython;
function setRunner(fn) { runner = typeof fn === 'function' ? fn : runPython; }

router.post('/check', async (req, res) => {
  res.set('Cache-Control', 'no-store');
  if (!req.user) return res.status(401).json({ error: 'Missing token' });
  let open = false;
  let preview = false;
  try {
    open = flagOn() === true;
    preview = open !== true;
    if (!open) {
      const { isOperator } = require('../lib/operator_view');
      open = (await isOperator(req)) === true;
    }
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(500).json({ error: name });
  }
  if (!open) return res.status(404).json({ error: 'Not found' });
  const body = req.body && typeof req.body === 'object' ? req.body : {};
  try {
    const out = await runner(body);
    const payload = out && typeof out === 'object' ? Object.assign({}, out) : failed('Error');
    if (preview) payload.banner = BANNER;
    return res.json(payload);
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    console.error('[claw-guard]', name);
    return res.status(500).json({ error: name });
  }
});

module.exports = router;
module.exports.setRunner = setRunner;
module.exports.run = runPython;
