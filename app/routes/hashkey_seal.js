'use strict';
/**
 * GET /api/hashkey-seal and POST /api/hashkey-seal/anchor.
 *
 * Admin-only. While HASHKEY_SEAL_ENABLED is off, flag_on_for admits an
 * operator and a POST does not sign. A non-operator gets 404, flag on
 * or off. The publisher this route runs is testnet-only. This file does
 * not sign.
 */

const express = require('express');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const { optionalAuth } = require('../auth');
const { accessFor, BANNER } = require('../lib/hashkey_seal_flag');

const ROOT = path.join(__dirname, '..', '..');
const router = express.Router();
router.use(optionalAuth);

const WORD = /^0x[0-9a-fA-F]{64}$/;

let runner = defaultRunner;

function defaultRunner(args) {
  return spawnSync('python3', ['-m', 'bot.hashkey_seal'].concat(args), {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 20000,
  });
}

function setRunnerForTests(fn) {
  runner = fn || defaultRunner;
}

function word(value) {
  return typeof value === 'string' && WORD.test(value.trim());
}

function closed(res) {
  return res.status(404).json({ error: 'Not found' });
}

function unreadable(res) {
  return res.status(503).json({
    error: 'HashKeySealUnreadable',
    readable: false,
    anchored: null,
    signed: false,
    broadcast: false,
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

function unreadReason(reason) {
  return reason === 'rpc_unreadable'
    || reason === 'chain_unreadable'
    || reason === 'result_unreadable';
}

router.get('/', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    const asked = req.query.hash;
    if (asked != null && String(asked) !== '' && !word(String(asked))) {
      return res.status(400).json({ error: 'bad_hash', readable: false, anchored: null });
    }
    const args = (asked == null || String(asked) === '')
      ? ['status']
      : ['read', String(asked).trim()];
    const body = parseRun(runner(args));
    if (!body) return unreadable(res);
    body.preview = access.preview;
    body.banner = access.preview ? BANNER : null;
    res.set('Cache-Control', 'private, no-store');
    if (unreadReason(body.reason)) return res.status(503).json(body);
    return res.json(body);
  } catch (err) {
    console.error('[hashkey-seal] read failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

router.post('/anchor', async (req, res) => {
  try {
    const access = await accessFor(req);
    if (!access.ok) return closed(res);
    if (access.preview) {
      return res.status(403).json({
        error: 'preview',
        signed: false,
        broadcast: false,
        banner: BANNER,
      });
    }
    const hash = req.body && req.body.hash;
    const kind = req.body && req.body.kind;
    if (!word(hash) || !word(kind)) {
      return res.status(400).json({ error: 'bad_hash', signed: false, broadcast: false });
    }
    const body = parseRun(runner(['publish', String(hash).trim(), String(kind).trim()]));
    if (!body) return unreadable(res);
    body.preview = false;
    body.banner = null;
    res.set('Cache-Control', 'private, no-store');
    if (unreadReason(body.reason)) return res.status(503).json(body);
    return res.json(body);
  } catch (err) {
    console.error('[hashkey-seal] publish failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

module.exports = router;
module.exports.setRunnerForTests = setRunnerForTests;
