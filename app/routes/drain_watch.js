'use strict';
/**
 * POST /api/drain-watch/check
 *
 * The same signals an alert carries, read from the calldata the caller
 * sent. No verdict. No stored address. No stored transaction. A revoke,
 * when one applies, is the unsigned transaction Panic Button already
 * builds. The caller's wallet signs it. This route does not.
 */

const express = require('express');

const panic = require('../lib/panic_tx');
const txray = require('../public/js/txray-model');
const poison = require('../public/js/address-poison');
const claw = require('../lib/drain_watch_claw');
const cancel = require('../lib/drain_watch_cancel');
const { FLAG, flagOn, BANNER } = require('../lib/drain_watch_flag');

const router = express.Router();
const PAGE = 'https://www.humanoid-traders.com/claw-watch';
const ADVISORY = 'Claw Watch is advisory only. It is fail-open: if indexing lags or this bot is down, there is no alert. It cannot stop a drain on its own.';

router.use(express.json({ limit: '16kb' }));

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(404).type('text/plain').send('Not found');
  }
  return auth.optionalAuth(req, res, next);
}

function closed(res) {
  return res.status(404).type('text/plain').send('Not found');
}

function hex64(value) {
  return typeof value === 'string' && /^(0x)?[0-9a-fA-F]{64}$/.test(value.trim())
    && !/^0x[0-9a-fA-F]{40}$/.test(value.trim());
}

function looksSecret(body) {
  const texts = [];
  function walk(node, depth) {
    if (depth > 6 || node == null) return;
    if (typeof node === 'string') {
      texts.push(node);
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(function (item) { walk(item, depth + 1); });
      return;
    }
    if (typeof node === 'object') {
      Object.keys(node).forEach(function (key) { walk(node[key], depth + 1); });
    }
  }
  walk(body, 0);
  for (let i = 0; i < texts.length; i++) {
    const text = texts[i].trim();
    if (hex64(text)) return true;
    const words = text.split(/\s+/);
    if (words.length >= 12 && words.every(function (w) { return /^[A-Za-z]+$/.test(w); })) return true;
    if (/\b(seed phrase|private key|secret key|mnemonic)\b/i.test(text)
        && /\b(send|paste|enter|share|give)\b/i.test(text)) return true;
  }
  return false;
}

function chainIdOf(raw) {
  if (typeof raw === 'number' && Number.isSafeInteger(raw) && raw > 0) return raw;
  if (typeof raw === 'string' && /^[0-9]+$/.test(raw.trim())) {
    const n = Number(raw.trim());
    if (Number.isSafeInteger(n) && n > 0) return n;
  }
  return null;
}

function addressOf(raw) {
  if (typeof raw !== 'string') return null;
  const m = /^0x([0-9a-fA-F]{40})$/.exec(raw.trim());
  return m ? '0x' + m[1].toLowerCase() : null;
}

function publicActions(decoded) {
  const actions = decoded && Array.isArray(decoded.actions) ? decoded.actions : [];
  return actions.map(function (action) {
    const params = {};
    const src = action && action.params && typeof action.params === 'object' ? action.params : {};
    Object.keys(src).forEach(function (key) {
      const value = src[key];
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        params[key] = value;
      } else if (Array.isArray(value)) {
        params[key] = value.length;
      }
    });
    return { tid: action && action.tid ? String(action.tid) : 'unread', params: params };
  });
}

function unlimitedOf(actions) {
  let seen = false;
  let yes = false;
  let no = false;
  actions.forEach(function (action) {
    if (action.tid === 'txr.a_forall_on') { seen = true; yes = true; }
    if (action.params && action.params.unlimited === true) { seen = true; yes = true; }
    if (action.params && action.params.unlimited === false) { seen = true; no = true; }
  });
  if (!seen) return 'unread';
  if (yes && no) return 'unread';
  if (yes) return 'yes';
  if (no) return 'no';
  return 'unread';
}

function revokeFor(to, chainId, actions) {
  if (!to || chainId == null) return null;
  for (let i = 0; i < actions.length; i++) {
    const action = actions[i];
    const spender = action.params && (action.params.spender || action.params.operator);
    if (!spender) continue;
    if (action.tid === 'txr.a_approve' || action.tid === 'txr.a_increase' || action.tid === 'txr.a_permit') {
      return panic.buildRevoke({ kind: 'erc20', token: to, spender: spender, chain_id: chainId });
    }
    if (action.tid === 'txr.a_forall_on') {
      return panic.buildRevoke({
        kind: 'approval_for_all', token: to, spender: spender, chain_id: chainId,
      });
    }
  }
  return null;
}

function unsignedFields(tx) {
  if (!tx || typeof tx !== 'object') return null;
  if (tx.kind === 'authorization') {
    const auth = tx.authorization || {};
    return {
      kind: 'authorization',
      chain_id: tx.chain_id,
      address: auth.address || null,
      unsigned: true,
    };
  }
  return {
    kind: 'transaction',
    to: tx.to,
    data: tx.data,
    value: tx.value == null ? null : String(tx.value),
    chain_id: tx.chain_id,
    unsigned: true,
  };
}

function poisonSignals(body) {
  const out = { lookalike: 'not_checked', dust: 'not_checked', alert: false };
  if (!addressOf(body.destination)) return out;
  const hasList = Array.isArray(body.counterparties);
  const hasInbound = Array.isArray(body.inbound);
  let checked;
  try {
    checked = poison.checkAddressPoisoning({
      destination: body.destination,
      counterparties: hasList ? body.counterparties : [],
      inbound: hasInbound ? body.inbound : undefined,
      now: Date.now(),
    });
  } catch (err) {
    return { lookalike: 'unread', dust: 'unread', alert: false };
  }
  if (!checked || checked.ok !== true || !checked.result) {
    return { lookalike: 'unread', dust: 'unread', alert: false };
  }
  const result = checked.result;
  if (!hasList || body.counterparties.length === 0) out.lookalike = 'not_checked';
  else if (result.reason === 'prefix_suffix_match') {
    out.lookalike = 'alert';
    out.alert = true;
  } else out.lookalike = 'none';
  if (!hasInbound) out.dust = 'not_checked';
  else if (result.dust === 'flagged') out.dust = 'hide';
  else if (result.dust === 'unread') out.dust = 'unread';
  else if (result.dust === 'none' || result.dust === 'logs_clear') out.dust = 'none';
  else out.dust = 'unread';
  return out;
}

router.post('/check', optionalAuth, function (req, res) {
  const { flag_on_for } = require('../lib/admin_preview');
  flag_on_for(req, FLAG).then(function (on) {
    if (on !== true) return closed(res);
    const body = req.body;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return res.status(400).json({ error: 'Body' });
    }
    if (looksSecret(body)) {
      return res.status(400).json({ error: 'refused', stored: false });
    }
    let decoded;
    try {
      const value = body.value == null || body.value === '' ? '0' : String(body.value);
      decoded = txray.decodeTx({ data: body.data == null ? '' : String(body.data), value: value });
    } catch (err) {
      decoded = { actions: [], flags: [], unknown: true, invalid: true };
    }
    const actions = publicActions(decoded);
    const chainId = chainIdOf(body.chain_id);
    const to = addressOf(body.to);
    let tx = null;
    try {
      tx = unsignedFields(revokeFor(to, chainId, actions));
    } catch (err) {
      tx = null;
    }
    const watched = poisonSignals(body);
    const guard = claw.publicSignals();
    const userId = req.user && req.user.user_id;
    const reply = {
      product: 'Claw Watch',
      advisory: ADVISORY,
      preview: flagOn() !== true,
      banner: flagOn() !== true ? BANNER : null,
      decoded: decoded && decoded.invalid === true ? 'unreadable' : 'read',
      actions: actions,
      signals: {
        exploit_list: 'unread',
        approval_age_days: 'unread',
        unlimited: unlimitedOf(actions),
        delegate_family: 'unread',
        claw_guard: guard && guard.status ? guard.status : 'unread',
        claw_guard_reason: guard && guard.reason ? guard.reason : 'unread',
        lookalike: watched.lookalike,
        dust: watched.dust,
      },
      alert: watched.alert === true,
      ai_summary: null,
      ai_summary_label: null,
      cancel: cancel.cancelLink(),
      tx: tx,
      page: PAGE,
      stored: false,
    };
    const finish = function (sealed) {
      reply.sealed = sealed;
      res.setHeader('Cache-Control', 'no-store');
      res.json(reply);
    };
    if (!userId) return finish({ sealed: false, reason: 'anonymous' });
    let seal;
    try {
      seal = require('../lib/scan_seal');
    } catch (err) {
      return finish({ sealed: false, reason: 'unreadable' });
    }
    const input = JSON.stringify({
      actions: actions.map(function (a) { return a.tid; }),
      lookalike: watched.lookalike,
      dust: watched.dust,
      claw_guard: reply.signals.claw_guard,
    });
    seal.sealIfKeyed({
      tool: 'claw_watch_check',
      input: input,
      result: { actions: actions, flags: [], unknown: !!(decoded && decoded.unknown) },
      deterministic: true,
      ctx: { userId: userId },
    }).then(function (sealed) {
      finish(sealed && typeof sealed === 'object' ? sealed : { sealed: false, reason: 'unreadable' });
    }).catch(function () {
      finish({ sealed: false, reason: 'seal_failed' });
    });
  }).catch(function () {
    if (!res.headersSent) closed(res);
  });
});

module.exports = router;
