'use strict';
/**
 * Panic Button v0. Off unless PANIC_BUTTON is "1" or "true".
 *
 * Read on each request. A process that starts with the flag off stays off
 * until the environment says otherwise. This module sets no other flag.
 */

const FLAG = 'PANIC_BUTTON';
const NOT_AVAILABLE = 'not available yet';

function enabled(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/** The whole answer while the flag is off. No form, no read. */
function unavailableHtml() {
  return '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
    + '<title>Panic</title></head><body>not available yet</body></html>\n';
}

module.exports = { FLAG, NOT_AVAILABLE, enabled, unavailableHtml };
