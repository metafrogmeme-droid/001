'use strict';
/**
 * RXB_ENABLED. Off unless the value is "1" or "true".
 * The function is flag_on, not enabled.
 */

const FLAG = 'RXB_ENABLED';

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, flag_on };
