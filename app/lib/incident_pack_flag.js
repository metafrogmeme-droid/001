'use strict';

/**
 * Incident pack, behind INCIDENT_PACK_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off.
 * While it is off, flag_on_for admits an operator and the answer says
 * so. A caller who is not an operator gets the same 404 as a missing
 * path. When the flag is on, flag_on_for admits everyone.
 *
 * flagOn is the public reader. `enabled` is a name other gates already use.
 */

const { flag_on_for, BANNER } = require('./admin_preview');

const FLAG = 'INCIDENT_PACK_ENABLED';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/** Admitted, and whether this call is an admin preview. Fail closed. */
async function accessFor(req) {
  try {
    const admitted = (await flag_on_for(req, FLAG)) === true;
    if (!admitted) return { ok: false, preview: false, banner: null };
    const preview = flagOn() !== true;
    return { ok: true, preview, banner: preview ? BANNER : null };
  } catch (err) {
    return { ok: false, preview: false, banner: null };
  }
}

module.exports = { FLAG, BANNER, flagOn, accessFor };
