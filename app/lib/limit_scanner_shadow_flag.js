'use strict';
/**
 * Limit Entry Scanner forward shadow, behind LIMIT_SCANNER_SHADOW.
 *
 * Off unless the variable is "1" or "true". The readout stays admin-only
 * when the flag is on: a non-operator gets 404. While the flag is off,
 * flag_on_for is the door, and an operator still has to be the operator.
 */

const { flag_on_for, identify } = require('./admin_preview');

const FLAG = 'LIMIT_SCANNER_SHADOW';
const BANNER = 'Admin preview. Not public.';
const CUTOFF_MS = Date.parse('2026-10-23T00:00:00.000Z');

function flagOn(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function decisionFor(req) {
  return flag_on_for(req, FLAG).then(function (on) {
    if (on !== true) return 'deny';
    return identify(req).then(function () {
      const { isOperator } = require('./operator_view');
      return isOperator(req).then(function (admin) {
        if (admin !== true) return 'deny';
        if (flagOn() === true) return 'open';
        return 'preview';
      });
    });
  }).catch(function () {
    return 'deny';
  });
}

module.exports = { FLAG, BANNER, CUTOFF_MS, flagOn, decisionFor };
