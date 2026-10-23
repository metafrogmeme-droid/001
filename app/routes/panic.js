'use strict';
/**
 * Panic Button API. Read-only. Off unless PANIC_BUTTON is 1 or true.
 *
 * The address arrives in the POST body and leaves in the response the caller
 * is already looking at. It is not cached, not logged, and not written.
 * Revoke transactions are attached unsigned. This route does not sign them
 * and does not broadcast them.
 */

const express = require('express');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { enabled, NOT_AVAILABLE } = require('../lib/panic_flag');
const { attachRevokes } = require('../lib/panic_tx');

const REPO = path.join(__dirname, '..', '..');
const router = express.Router();
router.use(rateLimit({ name: 'panic', windowMs: 60000, max: 20, key: ipKey }));

function failedRead() {
  return {
    available: true,
    read_failed: true,
    items: [],
    unread_chains: [],
    exploit_list_read: false,
    warning: 'If you entered your seed phrase anywhere, revoking is not enough. Move your funds to a new wallet.',
    never_ask: 'We never ask for your seed phrase or private key.',
    read_line: 'These are the approvals we could read.',
    message: 'The approval lists could not be read just now, so there is no list. This is not a clean result.',
  };
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
      child = spawn('python3', ['-m', 'bot.panic_button'], {
        cwd: REPO, env: process.env, stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (e) {
      finish(failedRead());
      return;
    }
    let out = '';
    const timer = setTimeout(() => {
      child.kill();
      finish(failedRead());
    }, 55000);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', () => {});
    child.on('error', () => {
      clearTimeout(timer);
      finish(failedRead());
    });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        const parsed = JSON.parse(out);
        finish(parsed && typeof parsed === 'object' ? parsed : failedRead());
      } catch (e) {
        finish(failedRead());
      }
    });
    child.stdin.end(JSON.stringify(body));
  });
}

let runner = defaultRunner;
function setRunner(fn) { runner = typeof fn === 'function' ? fn : defaultRunner; }

function off(res) {
  res.set('Cache-Control', 'no-store');
  return res.type('text/plain').send(NOT_AVAILABLE);
}

router.post('/receipt', async (req, res) => {
  if (!enabled()) return off(res);
  try {
    const hashes = req.body && req.body.hashes;
    const result = await runner({ op: 'receipt', hashes: hashes });
    if (!result || result.error || !result.cleaned_at || !Array.isArray(result.hashes)) {
      return res.status(400).json({ message: 'bad hashes' });
    }
    res.set('Cache-Control', 'no-store');
    res.json({ cleaned_at: result.cleaned_at, hashes: result.hashes });
  } catch (e) {
    console.error('Panic receipt error:', e && e.name ? e.name : 'Error');
    res.status(500).json({ message: 'Panic read unavailable' });
  }
});

router.post('/', async (req, res) => {
  if (!enabled()) return off(res);
  const address = req.body && typeof req.body.address === 'string' ? req.body.address : '';
  try {
    const result = await runner({ address: address });
    if (!result || result.available === false) return off(res);
    if (result.refused) {
      res.set('Cache-Control', 'no-store');
      return res.status(400).json({ message: result.message });
    }
    const body = attachRevokes(result);
    res.set('Cache-Control', 'no-store');
    res.json(body);
  } catch (e) {
    console.error('Panic read error:', e && e.name ? e.name : 'Error');
    res.status(500).json({ message: 'Panic read unavailable' });
  }
});

module.exports = router;
module.exports.setRunner = setRunner;
