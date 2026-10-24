'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Operator summary of the adoption rungs that do not sign: the alert
 * evaluation, the HIP-3 cap suggestion, and the archive replay status.
 * Built from the public snapshot, so a dollar field is not an input.
 */

const { evaluate } = require('./alerts');
const { suggest } = require('./hip3');
const { archiveStatus } = require('./replay');

function view(snapshot, env) {
  const src = env || {};
  return {
    alerts: evaluate(snapshot),
    hip3: suggest(src, snapshot),
    replay: archiveStatus(src.SAR_ORACLE_OCT10_DIR),
  };
}

module.exports = { view };
