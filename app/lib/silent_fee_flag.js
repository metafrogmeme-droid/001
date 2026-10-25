'use strict';

/**
 * Silent Fee Check. Off unless SILENT_FEE_CHECK_ENABLED is "1" or "true".
 *
 * The same two strings as bot/silent_fee/flag.py. Unset, empty, "yes",
 * "on" and "0" stay off. This module sets no other flag. The explorer
 * flag and the mainnet-read flag are read only in the Python module.
 *
 * Not named enabled. A module-level function of that name is the one the
 * method-ambiguity note already warns about.
 */

const FLAG = 'SILENT_FEE_CHECK_ENABLED';

function flag_on(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, flag_on };
