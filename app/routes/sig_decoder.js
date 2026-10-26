'use strict';
/**
 * POST /api/guard/sigdecode
 *
 * Part of Claw Guard. While SIG_DECODER_ENABLED is off, a non-operator
 * gets 404 and an operator gets the decode with admin_preview set.
 * With the flag on, a caller who passed optionalAuth gets the decode.
 *
 * optionalAuth is lazy. Loading auth at require time exits when
 * JWT_SECRET is absent, and the public-route scan loads this file
 * without one.
 *
 * The body is { kind, input }. The answer is public signals. It is
 * labelled AI-assisted. signals null means the decode did not run.
 * Nothing is signed. The child process is local and makes no network call.
 */

const express = require('express');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const { flag_on_for } = require('../lib/admin_preview');
const { FLAG, DISCLAIMER, flagOn } = require('../lib/sig_decoder_flag');

const ROOT = path.join(__dirname, '..', '..');
const router = express.Router();
const MAX_CHARS = 65536;
const KINDS = new Set(['auto', 'typed', '7702', '3009', 'hl', 'solana', 'message', 'calldata']);

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

function defaultRunner(stdin) {
  return spawnSync('python3', ['-m', 'bot.sigdecoder'], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 15000,
    input: stdin,
    maxBuffer: 1024 * 1024,
  });
}

function setRunnerForTests(fn) {
  runner = fn || defaultRunner;
}

function closed(res) {
  return res.status(404).json({ error: 'Not found' });
}

function labelled(extra) {
  return Object.assign({
    assisted: 'ai',
    disclaimer: DISCLAIMER,
    signals: null,
    reads_network: false,
  }, extra);
}

function unreadable(res) {
  return res.status(503).json(labelled({ error: 'SigDecoderUnreadable' }));
}

function parseRun(run) {
  if (!run || (run.status !== 0 && run.status !== 2)) return null;
  if (typeof run.stdout !== 'string' || run.stdout.trim() === '') return null;
  try {
    const body = JSON.parse(run.stdout);
    if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
    return body;
  } catch (err) {
    return null;
  }
}

router.post('/', async (req, res) => {
  try {
    const on = await flag_on_for(req, FLAG);
    if (!on) return closed(res);
    const preview = flagOn() !== true;
    const body = req.body;
    if (!body || typeof body !== 'object' || Array.isArray(body) || !Object.prototype.hasOwnProperty.call(body, 'input')) {
      return res.status(400).json(labelled({ error: 'bad_payload' }));
    }
    const kind = body.kind == null ? 'auto' : String(body.kind);
    if (!KINDS.has(kind)) return res.status(400).json(labelled({ error: 'bad_kind' }));
    let packed;
    try {
      packed = JSON.stringify({ kind: kind, input: body.input });
    } catch (err) {
      return res.status(400).json(labelled({ error: 'bad_payload' }));
    }
    if (packed.length > MAX_CHARS) return res.status(400).json(labelled({ error: 'too_large' }));
    const decoded = parseRun(runner(packed));
    if (!decoded || decoded.signals == null || !Array.isArray(decoded.signals)) {
      if (decoded && decoded.error === 'too_large') return res.status(400).json(labelled({ error: 'too_large' }));
      return unreadable(res);
    }
    if (decoded.disclaimer !== DISCLAIMER || decoded.assisted !== 'ai') return unreadable(res);
    decoded.admin_preview = preview;
    if (preview) decoded.banner = 'Admin preview. Not public.';
    res.set('Cache-Control', 'private, no-store');
    return res.json(decoded);
  } catch (err) {
    console.error('[sigdecode] failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unreadable(res);
  }
});

module.exports = router;
module.exports.setRunnerForTests = setRunnerForTests;
