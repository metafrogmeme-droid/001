'use strict';
/**
 * Claw Guard v0.1, behind a flag.
 *
 * v0 (the /guard page, POST /api/guard/check, the v10 presign seal) ships on
 * its own. These two additions — a lookalike warning, and an Agent Audit
 * Trail export of a receipt that already exists — are a later release. Until
 * CLAW_GUARD_V01 is set to "1" or "true" they are not served: the check route
 * is untouched, and the new routes answer 404.
 *
 * The flag is read on each request. Nothing here is cached at load, so a
 * process that starts with the flag off stays off.
 */

const fs = require('node:fs');
const path = require('node:path');

const FLAG = 'CLAW_GUARD_V01';

function enabled(env) {
  const src = env || process.env;
  const v = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/**
 * The /guard document. With the flag off this is the file on disk, which is
 * what sendFile serves. With it on, the same document plus the two scripts
 * that draw the warning. The v0 script tag is left where it is.
 */
function guardPageHtml(fileHtml) {
  const html = String(fileHtml == null ? '' : fileHtml);
  const tags = '<script src="/js/address-poison.js?v=1"></script>\n'
    + '<script src="/js/guard-v01.js?v=1"></script>\n';
  if (html.includes('/js/guard-v01.js')) return html;
  const at = html.lastIndexOf('</body>');
  if (at < 0) return html + '\n' + tags;
  return html.slice(0, at) + tags + html.slice(at);
}

function guardPageFile() {
  return path.join(__dirname, '..', 'public', 'guard.html');
}

function readGuardPage() {
  return fs.readFileSync(guardPageFile(), 'utf8');
}

module.exports = { FLAG, enabled, guardPageHtml, guardPageFile, readGuardPage };
