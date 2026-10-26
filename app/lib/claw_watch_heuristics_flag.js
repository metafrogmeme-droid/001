'use strict';
/**
 * Claw Watch heuristics, behind CLAW_WATCH_HEURISTICS_ENABLED.
 *
 * Off unless the variable is "1" or "true". While it is off the public
 * gets 404. An operator still reaches the route, with the banner
 * "Admin preview. Not public."
 *
 * The door is flag_on_for (lib/admin_preview.js). The public reader is
 * flagOn.
 */

const FLAG = 'CLAW_WATCH_HEURISTICS_ENABLED';
const BANNER = 'Admin preview. Not public.';

function flagOn(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, BANNER, flagOn };
