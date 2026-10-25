'use strict';
/**
 * Grounded Reputation, behind GROUNDED_REPUTATION_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off.
 * The route requires the flag and an operator. While the flag is off every
 * caller is refused, including an operator. There is no preview.
 *
 * flagOn is the public reader. `enabled` is a name other gates already use.
 */

const FLAG = 'GROUNDED_REPUTATION_ENABLED';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/** Operator and flag on. Fail closed when the flag is off or the check throws. */
async function accessFor(req) {
  if (flagOn() !== true) return { ok: false };
  const { identify } = require('./admin_preview');
  const { isOperator } = require('./operator_view');
  try {
    await identify(req);
    if ((await isOperator(req)) !== true) return { ok: false };
    return { ok: true };
  } catch (err) {
    return { ok: false };
  }
}

module.exports = { FLAG, flagOn, accessFor };
