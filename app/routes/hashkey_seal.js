'use strict';
/**
 * GET /api/hashkey-seal and POST /api/hashkey-seal/anchor.
 *
 * Admin-only. While HASHKEY_SEAL_ENABLED is off, flag_on_for admits an
 * operator and a POST does not sign. A non-operator gets 404, flag on
 * or off. The publisher this route runs is testnet-only. This file does
 * not sign.
 *
 * The publisher runs as a child process off the event loop, inside its
 * own deadline (HASHKEY_SEAL_DEADLINE_SEC). The parent waits longer than
 * that, so it never kills a child that may already have sent. A child
 * that dies anyway, or prints nothing usable, is an unknown, not a
 * negative: signed, broadcast and anchored are null, and the hash the
 * child wrote to stderr before sending is passed on when it is there.
 */

const express = require('express');
const { execFile } = require('node:child_process');
const path = require('node:path');

const { optionalAuth } = require('../auth');
const { accessFor, BANNER } = require('../lib/hashkey_seal_flag');

const ROOT = path.join(__dirname, '..', '..');
const router = express.Router();
router.use(optionalAuth);

const WORD = /^0x[0-9a-fA-F]{64}$/;
const REPORTED_HASH = /tx_hash=(0x[0-9a-fA-F]{64})/;

// bot/hashkey_seal/publish.py DEFAULT_DEADLINE_SEC: the child's own budget.
// tests/test_hashkey_seal.py holds the two equal.
const DEFAULT_CHILD_DEADLINE_SEC = 14;
// What the parent adds on top for interpreter start-up and the JSON line.
const PARENT_MARGIN_SEC = 6;

function childDeadlineSec(env) {
  const raw = (env || process.env).HASHKEY_SEAL_DEADLINE_SEC;
  if (raw == null || String(raw).trim() === '') return DEFAULT_CHILD_DEADLINE_SEC;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function parentTimeoutMs(env) {
  const child = childDeadlineSec(env);
  const budget = child == null ? DEFAULT_CHILD_DEADLINE_SEC : child;
  return Math.ceil((budget + PARENT_MARGIN_SEC) * 1000);
}

let runner = defaultRunner;

function defaultRunner(args) {
  return new Promise((resolve) => {
    execFile('python3', ['-m', 'bot.hashkey_seal'].concat(args), {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: parentTimeoutMs(process.env),
      maxBuffer: 1 << 20,
    }, (error, stdout, stderr) => {
      resolve({
        status: error ? (typeof error.code === 'number' ? error.code : null) : 0,
        signal: error && error.signal ? error.signal : null,
        timedOut: Boolean(error && error.killed),
        stdout: stdout == null ? '' : String(stdout),
        stderr: stderr == null ? '' : String(stderr),
      });
    });
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

/** A read that did not come back. Nothing was signed on a read path. */
function unreadable(res) {
  return res.status(503).json({
    error: 'HashKeySealUnreadable',
    readable: false,
    anchored: null,
  });
}

/** A publish whose child did not answer. Three unknowns, never three noes. */
function unknownPublish(res, run) {
  const reported = run && typeof run.stderr === 'string' ? run.stderr.match(REPORTED_HASH) : null;
  return res.status(503).json({
    error: 'HashKeySealUnreadable',
    reason: run && run.timedOut ? 'timed_out' : 'child_failed',
    readable: null,
    anchored: null,
    signed: null,
    broadcast: null,
    tx_hash: reported ? reported[1] : null,
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
    const body = parseRun(await runner(args));
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
    const run = await runner(['publish', String(hash).trim(), String(kind).trim()]);
    const body = parseRun(run);
    if (!body) return unknownPublish(res, run);
    body.preview = false;
    body.banner = null;
    res.set('Cache-Control', 'private, no-store');
    if (unreadReason(body.reason)) return res.status(503).json(body);
    // ok null: signed and sent, the chain has not answered. 202, not 200.
    if (body.ok === null) return res.status(202).json(body);
    return res.json(body);
  } catch (err) {
    console.error('[hashkey-seal] publish failed:', err && err.constructor ? err.constructor.name : 'Error');
    if (!res.headersSent) return unknownPublish(res, null);
  }
});

module.exports = router;
module.exports.setRunnerForTests = setRunnerForTests;
module.exports.childDeadlineSec = childDeadlineSec;
module.exports.parentTimeoutMs = parentTimeoutMs;
module.exports.DEFAULT_CHILD_DEADLINE_SEC = DEFAULT_CHILD_DEADLINE_SEC;
