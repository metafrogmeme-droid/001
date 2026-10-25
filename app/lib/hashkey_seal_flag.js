'use strict';
/**
 * HashKey seal anchor, behind HASHKEY_SEAL_ENABLED.
 *
 * Off unless the variable is "1" or "true". The product stays admin-only
 * when the flag is on: a non-operator gets 404, the same answer as a
 * missing path. While the flag is off, flag_on_for is the door, and an
 * operator is told this is a preview. A preview does not sign.
 *
 * The public reader is flagOn. `enabled` is a name other gates already use.
 */

const { flag_on_for, identify, BANNER } = require('./admin_preview');
const { isOperator } = require('./operator_view');

const FLAG = 'HASHKEY_SEAL_ENABLED';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

async function accessFor(req) {
  let admitted = false;
  try {
    admitted = (await flag_on_for(req, FLAG)) === true;
  } catch (err) {
    admitted = false;
  }
  if (!admitted) return { ok: false, preview: false };
  try {
    await identify(req);
    if ((await isOperator(req)) !== true) return { ok: false, preview: false };
  } catch (err) {
    return { ok: false, preview: false };
  }
  if (flagOn() === true) return { ok: true, preview: false };
  return { ok: true, preview: true };
}

module.exports = { FLAG, BANNER, flagOn, accessFor };
