'use strict';
/**
 * POST /api/claw-watch-heuristics/score
 * POST /api/claw-watch-heuristics/session
 *
 * Scores a window, or a list of session events, the caller already has.
 * This process does not open a chain socket and does not sign. While the
 * flag is off a non-operator gets 404. An operator gets the preview banner.
 */

const express = require('express');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();
const DISCLAIMER = 'Public on-chain signal only. This is not a verdict.';
const SESSION_DISCLAIMER = 'Public account signal only. This is not a verdict.';

function authMiddleware(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (e) {
    return res.status(401).json({ error: 'Missing token' });
  }
  return auth.authMiddleware(req, res, next);
}

const limit = rateLimit({
  name: 'claw-watch-heuristics', windowMs: 60000, max: 20, key: userKey,
});

function defaultRunner(payload) {
  const proc = spawnSync('python3', ['-m', 'bot.claw_watch_heuristics'], {
    input: JSON.stringify(payload || {}),
    encoding: 'utf8',
    timeout: 15000,
    cwd: path.join(__dirname, '..', '..'),
  });
  if (proc.error) {
    const name = proc.error.name || 'Error';
    return { findings: null, chain_allowed: false, error_class: name, disclaimer: DISCLAIMER };
  }
  if (proc.status !== 0 || !proc.stdout) {
    return { findings: null, chain_allowed: false, error_class: 'ScorerError', disclaimer: DISCLAIMER };
  }
  try {
    return JSON.parse(proc.stdout);
  } catch (e) {
    return { findings: null, chain_allowed: false, error_class: 'SyntaxError', disclaimer: DISCLAIMER };
  }
}

let runner = defaultRunner;

function setRunnerForTests(fn) {
  runner = typeof fn === 'function' ? fn : defaultRunner;
}

function defaultSessionRunner(payload) {
  const proc = spawnSync('python3', ['-m', 'bot.claw_watch_heuristics.session'], {
    input: JSON.stringify(payload || {}),
    encoding: 'utf8',
    timeout: 15000,
    cwd: path.join(__dirname, '..', '..'),
  });
  if (proc.error) {
    const name = proc.error.name || 'Error';
    return { alerts: null, scored: false, error_class: name, disclaimer: SESSION_DISCLAIMER };
  }
  if (proc.status !== 0 || !proc.stdout) {
    return { alerts: null, scored: false, error_class: 'ScorerError', disclaimer: SESSION_DISCLAIMER };
  }
  try {
    return JSON.parse(proc.stdout);
  } catch (e) {
    return { alerts: null, scored: false, error_class: 'SyntaxError', disclaimer: SESSION_DISCLAIMER };
  }
}

let sessionRunner = defaultSessionRunner;

function setSessionRunnerForTests(fn) {
  sessionRunner = typeof fn === 'function' ? fn : defaultSessionRunner;
}

function presentSession(out, preview) {
  const body = out && typeof out === 'object' ? out : {};
  const alerts = body.alerts == null ? null : body.alerts;
  const response = {
    enabled: body.enabled !== false,
    scored: body.scored === true,
    alerts: Array.isArray(alerts) ? alerts.map(function (alert) {
      if (!alert || typeof alert !== 'object') return alert;
      return {
        code: alert.code,
        level: alert.level,
        ts: alert.ts,
        detail: alert.detail && typeof alert.detail === 'object' ? alert.detail : {},
        text: typeof alert.text === 'string' ? alert.text : '',
      };
    }) : null,
    disclaimer: typeof body.disclaimer === 'string' ? body.disclaimer : SESSION_DISCLAIMER,
  };
  if (typeof body.sentence === 'string') response.sentence = body.sentence;
  if (body.error_class) response.error_class = String(body.error_class);
  if (preview) response.banner = require('../lib/claw_watch_heuristics_flag').BANNER;
  return response;
}

function stripFinding(finding) {
  if (!finding || typeof finding !== 'object') return finding;
  const revokes = Array.isArray(finding.revokes) ? finding.revokes.map((rev) => {
    if (!rev || typeof rev !== 'object') return rev;
    const copy = Object.assign({}, rev);
    copy.signature = null;
    if (copy.authorization && typeof copy.authorization === 'object') {
      copy.authorization = Object.assign({}, copy.authorization, { signature: null });
      delete copy.authorization.r;
      delete copy.authorization.s;
      delete copy.authorization.v;
    }
    return copy;
  }) : [];
  return {
    chain_id: finding.chain_id,
    block: finding.block == null ? null : finding.block,
    tx_hash: finding.tx_hash || '',
    subjects: finding.subjects || [],
    counterparties: finding.counterparties || [],
    signals: finding.signals || [],
    score: finding.score,
    signal_strength: finding.signal_strength,
    stage: finding.stage,
    disclaimer: finding.disclaimer || DISCLAIMER,
    revokes,
  };
}

function present(out, preview) {
  const body = out && typeof out === 'object' ? out : {};
  const findings = body.findings == null ? null : body.findings;
  const response = {
    chain_allowed: body.chain_allowed === true,
    chain_id: body.chain_id == null ? null : body.chain_id,
    chain_label: typeof body.chain_label === 'string' ? body.chain_label : null,
    findings: Array.isArray(findings) ? findings.map(stripFinding) : null,
    disclaimer: typeof body.disclaimer === 'string' ? body.disclaimer : DISCLAIMER,
    sentence: typeof body.sentence === 'string' ? body.sentence : null,
    internal: body.internal && typeof body.internal === 'object' ? {
      status: typeof body.internal.status === 'string' ? body.internal.status : 'off',
      reason: typeof body.internal.reason === 'string' ? body.internal.reason : null,
    } : { status: 'off', reason: null },
    code_status: typeof body.code_status === 'string' ? body.code_status : null,
    transfer_gap: body.transfer_gap === true,
    memory_status: typeof body.memory_status === 'string' ? body.memory_status : null,
  };
  if (body.error_class) response.error_class = String(body.error_class);
  if (preview) response.banner = require('../lib/claw_watch_heuristics_flag').BANNER;
  return response;
}

router.post('/score', authMiddleware, limit, function (req, res) {
  const { flag_on_for } = require('../lib/admin_preview');
  const flag = require('../lib/claw_watch_heuristics_flag');
  flag_on_for(req, flag.FLAG).then(function (on) {
    if (on !== true) return res.status(404).type('text/plain').send('Not found');
    const body = req.body;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return res.status(400).json({ findings: null, error_class: 'Payload', disclaimer: DISCLAIMER });
    }
    let out;
    try {
      out = runner(body);
    } catch (e) {
      return res.status(500).json({
        findings: null,
        error_class: e && e.name ? e.name : 'Error',
        disclaimer: DISCLAIMER,
      });
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json(present(out, flag.flagOn() !== true));
  }).catch(function () {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
});

router.post('/session', authMiddleware, limit, function (req, res) {
  const { flag_on_for } = require('../lib/admin_preview');
  const flag = require('../lib/claw_watch_heuristics_flag');
  flag_on_for(req, flag.FLAG).then(function (on) {
    if (on !== true) return res.status(404).type('text/plain').send('Not found');
    const body = req.body;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return res.status(400).json({ alerts: null, error_class: 'Payload', disclaimer: SESSION_DISCLAIMER });
    }
    let out;
    try {
      out = sessionRunner(body);
    } catch (e) {
      return res.status(500).json({
        alerts: null,
        error_class: e && e.name ? e.name : 'Error',
        disclaimer: SESSION_DISCLAIMER,
      });
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json(presentSession(out, flag.flagOn() !== true));
  }).catch(function () {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
});

module.exports = router;
module.exports.setRunnerForTests = setRunnerForTests;
module.exports.setSessionRunnerForTests = setSessionRunnerForTests;
