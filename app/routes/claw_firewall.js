'use strict';
/**
 * Claw Firewall scan and one-transaction revoke batch.
 *
 * While DRAIN_FIREWALL_ENABLED is off the public gets 404 and an operator
 * gets the preview. While it is on the route is public. optionalAuth is in
 * the handler list so the mixed-module guard sees it. The child process is
 * marked DRAIN_FIREWALL_FROM_ROUTE=1 only after that door has opened. The
 * child does not sign.
 *
 * Pre-existing approvals are not stopped by the hook. They have to be revoked
 * or explicitly kept. Revoke stays available, including while an account is frozen.
 */

const express = require('express');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const tx = require('../lib/claw_firewall_tx');

const REPO = path.join(__dirname, '..', '..');
const BODY_LIMIT_BYTES = 16 * 1024;

function capBody(req, res, next) {
  const len = parseInt(req.get('content-length') || '', 10);
  if (Number.isFinite(len) && len > BODY_LIMIT_BYTES) {
    res.set('Cache-Control', 'no-store');
    return res.status(413).json({ error: 'Payload too large' });
  }
  return express.json({ limit: BODY_LIMIT_BYTES })(req, res, (err) => {
    if (err && (err.status === 413 || err.type === 'entity.too.large')) {
      res.set('Cache-Control', 'no-store');
      return res.status(413).json({ error: 'Payload too large' });
    }
    return next(err);
  });
}

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(404).type('text/plain').send('Not found');
  }
  return auth.optionalAuth(req, res, next);
}

const router = express.Router();
router.use(capBody);
router.use(rateLimit({ name: 'claw-firewall', windowMs: 60000, max: 20, key: ipKey }));

function failedRead() {
  return {
    available: true,
    read_failed: true,
    items: [],
    unread_chains: [],
    exploit_list_read: false,
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
    const env = Object.assign({}, process.env, { DRAIN_FIREWALL_FROM_ROUTE: '1' });
    try {
      child = spawn('python3', ['-m', 'bot.drain_firewall'], {
        cwd: REPO, env: env, stdio: ['pipe', 'pipe', 'pipe'],
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

function deny(res) {
  res.set('Cache-Control', 'no-store');
  return res.status(404).type('text/plain').send('Not found');
}

function open(req, res, onOpen) {
  const { flag_on_for } = require('../lib/admin_preview');
  const flag = require('../lib/claw_firewall_flag');
  flag_on_for(req, flag.FLAG).then(function (on) {
    if (on !== true) return deny(res);
    return onOpen(flag.flagOn() === true ? 'open' : 'preview');
  }).catch(function (e) {
    console.error('Claw Firewall error:', e && e.name ? e.name : 'Error');
    if (!res.headersSent) deny(res);
  });
}

function limits() {
  return {
    pre_existing: 'An approval that is already open is not stopped by the hook. Revoke it, or leave it in place on purpose.',
    revoke_open: 'Revoke stays available, including while the account is frozen.',
    unread: 'An unread chain is not a clean result.',
    ecrecover_first: 'DAI, OpenZeppelin ERC20Permit and USDS call ecrecover before ERC-1271. This module does not gate those permits.',
    plain_signature: 'A facilitator that only accepts a plain v, r, s signature fails closed. isValidSignature does not recover one.',
    install_side_effect: 'Installing the gate makes outstanding unused Permit2 and USDC v2.2 signatures fail until they are registered.',
    root_key: 'Someone who holds the 7702 root key bypasses the module with a direct transfer or by pointing the delegation at another contract or at the zero address.',
    fuji: 'Avalanche Fuji is an ERC-7579 smart account. It has no EIP-7702.',
    delegation_test: 'The 7702 path is a test of whether the wallet allows delegation, on Base Sepolia, and only while this flag is on.',
  };
}

router.post('/scan', optionalAuth, function (req, res) {
  open(req, res, function (decision) {
    const address = req.body && typeof req.body.address === 'string' ? req.body.address : '';
    runner({ address: address }).then(function (result) {
      if (!result || result.available === false) return deny(res);
      res.set('Cache-Control', 'no-store');
      const body = Object.assign({}, result, { limits: limits() });
      if (decision === 'preview') body.banner = require('../lib/claw_firewall_flag').BANNER;
      res.json(body);
    }).catch(function (e) {
      console.error('Claw Firewall error:', e && e.name ? e.name : 'Error');
      if (!res.headersSent) res.status(500).json({ message: 'Claw Firewall scan unavailable' });
    });
  });
});

router.post('/batch', optionalAuth, function (req, res) {
  open(req, res, function (decision) {
    const address = req.body && typeof req.body.address === 'string' ? req.body.address : '';
    const keep = req.body && Array.isArray(req.body.keep) ? req.body.keep.map(String) : [];
    runner({ address: address }).then(function (result) {
      if (!result || result.available === false) return deny(res);
      if (result.refused) {
        res.set('Cache-Control', 'no-store');
        return res.status(400).json({ message: result.message });
      }
      const items = Array.isArray(result.items) ? result.items : [];
      const keys = items
        .filter(function (item) { return item.kind !== 'delegation_7702'; })
        .map(tx.pairKey);
      const prepared = tx.prepareRevokes(items, keep);
      const guardian = req.body && typeof req.body.guardian === 'string' ? req.body.guardian : '';
      const vault = req.body && typeof req.body.vault === 'string' ? req.body.vault : '';
      let init = null;
      if (/^0x[0-9a-fA-F]{40}$/.test(guardian) && /^0x[0-9a-fA-F]{40}$/.test(vault)) {
        init = {
          data: tx.encodeInit(guardian, vault),
          value: '0x0',
          note: 'Send this from the account itself. The server does not sign it. The signature field is empty because the caller is the account.',
        };
      }
      res.set('Cache-Control', 'no-store');
      const body = {
        available: true,
        read_failed: result.read_failed === true,
        unread_chains: result.unread_chains || [],
        message: result.message,
        partial_line: result.partial_line || null,
        pairs_hash: tx.pairsHash(keys),
        empty_is_not_everything_revoked: true,
        kept: prepared.kept,
        kept_line: prepared.kept_line,
        items: prepared.individual,
        authorizations: prepared.authorizations,
        permit2_calls: prepared.permit2_calls,
        execute_batch: prepared.execute_batch,
        init: init,
        delegation_test_chain: prepared.delegation_test_chain,
        limits: limits(),
      };
      if (decision === 'preview') body.banner = require('../lib/claw_firewall_flag').BANNER;
      res.json(body);
    }).catch(function (e) {
      console.error('Claw Firewall error:', e && e.name ? e.name : 'Error');
      if (!res.headersSent) res.status(500).json({ message: 'Claw Firewall batch unavailable' });
    });
  });
});

module.exports = router;
module.exports.setRunner = setRunner;
module.exports.capBody = capBody;
module.exports.bodyLimitBytes = BODY_LIMIT_BYTES;
