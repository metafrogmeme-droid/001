'use strict';
/**
 * The committed custody reading. This process does not recompute a score
 * and it does not sign. A missing file is not a zero score.
 */

const fs = require('node:fs');
const path = require('node:path');

const READING_FILE = path.join(__dirname, '..', '..', 'bot', 'qch', 'fixtures', 'published.json');

function className(err) {
  return err && err.constructor ? err.constructor.name : 'Error';
}

function load() {
  let raw;
  try {
    raw = fs.readFileSync(READING_FILE, 'utf8');
  } catch (err) {
    const reason = err && err.code === 'ENOENT' ? 'not_published' : 'unreadable';
    return {
      readable: false,
      reason,
      estimate: true,
      label: 'estimate',
      method: null,
      data_source: null,
      issuers: null,
      error: className(err),
    };
  }
  try {
    const doc = JSON.parse(raw);
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      return {
        readable: false,
        reason: 'unreadable',
        estimate: true,
        label: 'estimate',
        method: null,
        data_source: null,
        issuers: null,
      };
    }
    return doc;
  } catch (err) {
    return {
      readable: false,
      reason: 'unreadable',
      estimate: true,
      label: 'estimate',
      method: null,
      data_source: null,
      issuers: null,
      error: className(err),
    };
  }
}

function withBanner(body, preview) {
  if (preview === true) body.banner = 'Admin preview. Not public.';
  return body;
}

function scorePayload(opts) {
  const preview = opts && opts.preview === true;
  const doc = load();
  const scenarios = doc.simulator && doc.simulator.cases && typeof doc.simulator.cases === 'object'
    ? Object.keys(doc.simulator.cases)
    : null;
  return withBanner({
    readable: doc.readable === true,
    reason: doc.readable === true ? null : (doc.reason || 'unreadable'),
    product: doc.product || null,
    as_of: doc.as_of == null ? null : doc.as_of,
    as_of_unix: Number.isInteger(doc.as_of_unix) ? doc.as_of_unix : null,
    rule: typeof doc.rule === 'string' ? doc.rule : null,
    estimate: true,
    label: doc.label === 'estimate' ? 'estimate' : null,
    method: typeof doc.method === 'string' ? doc.method : null,
    policy_method: typeof doc.policy_method === 'string' ? doc.policy_method : null,
    data_source: typeof doc.data_source === 'string' ? doc.data_source : null,
    signs: false,
    key_held: false,
    network: doc.network === 'testnet' ? 'testnet' : null,
    parameters: doc.parameters && typeof doc.parameters === 'object' ? doc.parameters : null,
    issuers: Array.isArray(doc.issuers) ? doc.issuers : null,
    scenarios,
  }, preview);
}

function policyPayload(scenario, opts) {
  const preview = opts && opts.preview === true;
  const doc = load();
  const name = typeof scenario === 'string' ? scenario : '';
  const cases = doc.simulator && doc.simulator.cases;
  const known = cases && Object.prototype.hasOwnProperty.call(cases, name);
  if (!known) {
    return withBanner({
      readable: false,
      reason: 'unreadable',
      scenario: name || null,
      estimate: true,
      label: 'estimate',
      method: typeof doc.policy_method === 'string' ? doc.policy_method : null,
      data_source: null,
      decision: null,
    }, preview);
  }
  const decision = cases[name];
  return withBanner({
    readable: true,
    reason: null,
    scenario: name,
    estimate: decision && decision.estimate === true,
    label: decision && decision.label === 'estimate' ? 'estimate' : null,
    method: decision && typeof decision.method === 'string' ? decision.method : null,
    data_source: decision && typeof decision.data_source === 'string' ? decision.data_source : null,
    decision,
  }, preview);
}

module.exports = { READING_FILE, load, scorePayload, policyPayload };
