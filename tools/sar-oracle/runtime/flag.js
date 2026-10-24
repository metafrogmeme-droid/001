'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Off unless SAR_ORACLE_ENABLED is "1" or "true".
 */

const FLAG = 'SAR_ORACLE_ENABLED';

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, flag_on };
