'use strict';
/**
 * Market Clock, behind MARKET_CLOCK_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off.
 * A non-operator is 404 while the flag is off and 404 while it is on.
 * An operator with the flag off sees the preview banner.
 *
 * flag_on_for is the preview door. isOperator is required as well, so a
 * public-on flag does not open the route. The reader is registered lazily
 * from admin_preview.js.
 */

const { flag_on_for, identify, BANNER } = require('./admin_preview');
const { isOperator } = require('./operator_view');

const FLAG = 'MARKET_CLOCK_ENABLED';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/** Operator, flag off or on. Fail closed when the check throws. */
async function accessFor(req) {
  try {
    await identify(req);
    const door = await flag_on_for(req, FLAG);
    const admin = await isOperator(req);
    if (door !== true || admin !== true) return { ok: false, preview: false };
    const preview = flagOn() !== true;
    return { ok: true, preview, banner: preview ? BANNER : null };
  } catch (err) {
    return { ok: false, preview: false };
  }
}

module.exports = { FLAG, BANNER, flagOn, accessFor };
