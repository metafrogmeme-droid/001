'use strict';
/**
 * GET /api/limit-scanner/shadow
 *
 * Admin-only readout of the pre-registered forward shadow. Basis points,
 * counts and ratios. A missing file is not a measured zero. A summary whose
 * window is not strictly after 2026-10-23 cannot carry a survives stamp.
 */

const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const { BANNER, CUTOFF_MS, decisionFor } = require('../lib/limit_scanner_shadow_flag');

const router = express.Router();

const DROP = /usd|dollar|usdt|equity|notional|balance/i;

function optionalAuth(req, res, next) {
  let auth;
  try {
    auth = require('../auth');
  } catch (err) {
    return res.status(404).type('text/plain').send('Not found');
  }
  return auth.optionalAuth(req, res, next);
}

function closed(res) {
  return res.status(404).type('text/plain').send('Not found');
}

function summaryPath() {
  if (process.env.LIMIT_SCANNER_SHADOW_SUMMARY) return process.env.LIMIT_SCANNER_SHADOW_SUMMARY;
  return path.join(__dirname, '..', '..', 'benchmark', 'prereg', 'limit_scanner_forward_summary.json');
}

function loadSummary() {
  const file = summaryPath();
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return { readable: false, reason: 'summary_missing' };
    return { readable: false, reason: 'summary_unreadable' };
  }
  try {
    const doc = JSON.parse(text);
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      return { readable: false, reason: 'summary_unreadable' };
    }
    return { readable: true, doc: doc };
  } catch (err) {
    return { readable: false, reason: 'summary_unreadable' };
  }
}

function numOrNull(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return null;
}

function windowIsForward(doc) {
  const start = Date.parse(doc && doc.window_start);
  if (!Number.isFinite(start) || start < CUTOFF_MS) return false;
  const end = doc.window_end == null ? null : Date.parse(doc.window_end);
  if (end != null && (!Number.isFinite(end) || end <= CUTOFF_MS)) return false;
  const variants = doc.variants;
  if (!variants || typeof variants !== 'object') return false;
  const rows = Object.values(variants);
  for (let i = 0; i < rows.length; i++) {
    const trades = rows[i] && rows[i].trades;
    if (!Array.isArray(trades)) continue;
    for (let j = 0; j < trades.length; j++) {
      const fill = Number(trades[j] && trades[j].fill_ms);
      if (!(fill > CUTOFF_MS)) return false;
    }
  }
  return true;
}

function presentVariant(raw, forward) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const claimed = src.verdict === 'survives' && src.evaluable === true;
  const survives = forward === true && claimed === true;
  const out = {
    evaluable: survives ? true : (forward === true && src.evaluable === true),
    verdict: survives ? 'survives' : (forward ? (src.verdict || 'inconclusive') : 'refused'),
    survives: survives,
    n: Number.isInteger(src.n) && src.n >= 0 ? src.n : null,
    mean_net_bp: numOrNull(src.mean_net_bp),
    lower_bp: numOrNull(src.lower_bp),
    upper_bp: numOrNull(src.upper_bp),
    dsr: numOrNull(src.dsr),
    profit_factor: numOrNull(src.profit_factor),
    win_rate: numOrNull(src.win_rate),
    funding: src.funding === 'priced' ? 'priced' : 'unpriced',
    reason: typeof src.reason === 'string' ? src.reason : '',
  };
  if (!forward && claimed) {
    out.evaluable = false;
    out.verdict = 'refused';
    out.survives = false;
    out.reason = 'window is not after 2026-10-23';
  }
  if (!forward && out.verdict === 'survives') {
    out.verdict = 'refused';
    out.survives = false;
  }
  return out;
}

function present(loaded) {
  if (loaded.readable !== true) {
    return {
      readable: false,
      reason: loaded.reason,
      mean_net_bp: null,
      survives: false,
      variants: null,
      cutoff: '2026-10-23T00:00:00+00:00',
      label: 'Basis points per trade. Not a dollar amount.',
    };
  }
  const doc = loaded.doc;
  const forward = windowIsForward(doc);
  const variants = {};
  const raw = doc.variants && typeof doc.variants === 'object' ? doc.variants : {};
  Object.keys(raw).forEach(function (name) {
    variants[name] = presentVariant(raw[name], forward);
  });
  let survives = false;
  Object.keys(variants).forEach(function (name) {
    if (variants[name].survives === true) survives = true;
  });
  return {
    readable: true,
    window_start: typeof doc.window_start === 'string' ? doc.window_start : null,
    forward: forward,
    survives: survives,
    variants: variants,
    cutoff: '2026-10-23T00:00:00+00:00',
    label: 'Basis points per trade. Not a dollar amount.',
  };
}

function scrub(value) {
  if (Array.isArray(value)) return value.map(scrub);
  if (!value || typeof value !== 'object') return value;
  const out = {};
  Object.keys(value).forEach(function (key) {
    if (DROP.test(key)) return;
    out[key] = scrub(value[key]);
  });
  return out;
}

router.get('/', optionalAuth, function (req, res) {
  decisionFor(req).then(function (decision) {
    if (decision === 'deny') return closed(res);
    const body = scrub(present(loadSummary()));
    if (decision === 'preview') body.banner = BANNER;
    res.setHeader('Cache-Control', 'no-store');
    return res.json(body);
  }).catch(function () {
    if (!res.headersSent) closed(res);
  });
});

module.exports = router;
