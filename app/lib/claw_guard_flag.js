'use strict';

/**
 * CLAW_GUARD_ENABLED. Off unless the value is 1 or true.
 *
 * The public reader is flagOn. A reader named enabled would share a name
 * the reachability sweep cannot attribute.
 */
const FLAG = 'CLAW_GUARD_ENABLED';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  const v = String(raw == null ? '' : raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, flagOn };
