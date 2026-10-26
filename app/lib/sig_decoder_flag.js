'use strict';
/**
 * Signature decoder, behind SIG_DECODER_ENABLED.
 *
 * Off unless the variable is "1" or "true". "yes" and "on" stay off.
 * While the flag is off, flag_on_for admits an operator and refuses
 * everyone else. The decoder does not sign and does not call a network.
 *
 * flagOn is the public reader. `enabled` is a name other gates already use.
 */

const FLAG = 'SIG_DECODER_ENABLED';
const DISCLAIMER = 'AI-assisted. Public signals, not a verdict.';

function flagOn(env) {
  const src = env || process.env;
  const raw = src[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

module.exports = { FLAG, DISCLAIMER, flagOn };
