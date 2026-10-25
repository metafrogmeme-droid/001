'use strict';
/**
 * GET /api/grounded-reputation/evidence
 * POST /api/grounded-reputation/classify
 *
 * Admin-only. While GROUNDED_REPUTATION_ENABLED is off, every caller gets
 * 404. A signed-in caller who is not the operator gets 404 while the flag
 * is on. The classifier does not sign and does not move funds.
 *
 * optionalAuth is lazy. Loading auth at require time exits when JWT_SECRET
 * is absent, and the public-route scan loads this file without one.
 *
 * Agent Check mounts lib/grounded_reputation_tab.js. This route is the door.
 */

const express = require('express');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const { accessFor } = require('../lib/grounded_reputation_flag');

const ROOT = path.join(__dirname, '..', '..');
const router = express.Router();

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (err) {
    return res.status(404).json({ error: 'Not found' });
  }
  return auth.optionalAuth(req, res, next);
}

router.use(optionalAuth);

let runner = defaultRunner;

function defaultRunner(args, stdin) {
  return spawnSync('python3', ['-m', 'bot.grounded_reputation'].concat(args), {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 20000,
    input: stdin,
  });
}

function setRunnerForTests(fn) {
  runner = fn || defaultRunner;
}

function closed(res) {
  return res.status(404).json({ error: 'Not found' });
}

function unreadable(res) {
  return res.status(503).json({
    error: 'GroundedReputationUnreadable',
    readable: false,
    counts: null,
    indexed: null,
  });
}

function parseRun(run) {
  if (!run || run.status !== 0 || typeof run.stdout !== 'string' || run.stdout.trim() === '') {
    return null;
  }
  try {
    const body = JSON.parse(run.stdout);
    if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
    return body;
  } catch (err) {
    return null;
  }
}

function whole(value) {
  if (typeof value !== 'string' || !/^[0-9]{1,78}$/.test(value.trim())) return null;
  return value.trim();
}

router.get('/evidence', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    const chain = whole(req.query.chain_id == null ? '' : String(req.query.chain_id));
    const agent = whole(req.query.agent_id == null ? '' : String(req.query.agent_id));
    if (chain == null || agent == null) {
      return res.status(400).json({ error: 'bad_agent', readable: false, counts: null });
    }
    const body = parseRun(runner(['evidence', chain, agent], ''));
    if (!body) return unreadable(res);
    if (body.error === 'flag_off' || (body.ok === false && body.readable === false && body.error === 'chain_refused')) {
      return closed(res);
    }
    res.set('Cache-Control', 'private, no-store');
    if (body.readable === false) return res.status(503).json(body);
    return res.json(body);
  } catch (err) {
    console.error('[grounded-reputation] evidence failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

router.post('/classify', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    const payload = req.body;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return res.status(400).json({ error: 'bad_payload', readable: false, counts: null });
    }
    const body = parseRun(runner(['classify'], JSON.stringify(payload)));
    if (!body) return unreadable(res);
    if (body.error === 'flag_off') return closed(res);
    res.set('Cache-Control', 'private, no-store');
    if (body.readable === false && body.error === 'chain_refused') return closed(res);
    if (body.readable === false) return res.status(503).json(body);
    return res.json(body);
  } catch (err) {
    console.error('[grounded-reputation] classify failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

module.exports = router;
module.exports.setRunnerForTests = setRunnerForTests;
