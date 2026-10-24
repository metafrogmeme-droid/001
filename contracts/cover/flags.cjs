'use strict';

/**
 * Cover flags. Off unless the variable is exactly "1" or "true"
 * (case and surrounding space ignored).
 *
 * An empty object is a clean slate: it is not the process environment.
 * A missing value is off. It is not read as a default that happens to be on.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 */

const ADL_FLAG = 'COVER_ADL_ENABLED';
const STOPGAP_FLAG = 'COVER_STOPGAP_ENABLED';

function flagOn(name, env) {
  const src = (env === undefined || env === null) ? process.env : env;
  const raw = src[name];
  if (raw === undefined || raw === null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

function adlOn(env) {
  return flagOn(ADL_FLAG, env);
}

function stopGapOn(env) {
  return flagOn(STOPGAP_FLAG, env);
}

module.exports = {
  ADL_FLAG,
  STOPGAP_FLAG,
  flagOn,
  adlOn,
  stopGapOn,
};
