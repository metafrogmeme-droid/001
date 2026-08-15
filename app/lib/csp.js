'use strict';
/**
 * Content-Security-Policy script hashes for the static pages.
 *
 * THE FINDING (M14). `script-src` was `'self' 'unsafe-inline' https://telegram.org`
 * while the session bearer token lived in localStorage, so CSP offered no
 * backstop at all against injected inline script: any one innerHTML sink
 * reached with unescaped attacker data executes, reads the token, and
 * exfiltrates it by navigating away. `'unsafe-inline'` is what makes that
 * chain a single bug rather than two.
 *
 * WHY HASHES AND NOT NONCES. A nonce has to be minted per response and written
 * into the HTML, which requires rendering it. These pages are STATIC — served
 * by `express.static` and `res.sendFile` straight off disk — so there is no
 * render step to inject anything into. Retrofitting a template engine across
 * 33 pages to buy a nonce is a far larger change than the finding warrants,
 * and it would put a rendering layer in front of every page on the site.
 *
 * Hashes need no per-response work: CSP admits an inline block whose SHA-256
 * matches one in the policy. Computing them here, from the same bytes the
 * static handler serves, means the policy cannot drift from the pages — the
 * failure mode of a hand-maintained hash list, where an edited script silently
 * stops executing in production and nowhere else.
 *
 * WHAT THIS DOES NOT COVER. Inline event handlers (`onclick="..."`) are not
 * reachable by hash at all — they need `'unsafe-hashes'`, which re-opens a
 * weaker version of the same hole. The 20 of them on the landing page were
 * converted to delegated listeners instead; `app/test/csp_no_unsafe_inline.test.js`
 * fails if one comes back, because it would break silently under this policy
 * rather than loudly.
 *
 * CACHING. Computed once at require time. The pages cannot change under a
 * running process — a deploy restarts it — and re-reading 33 files per request
 * to defend against an edit that cannot happen would be a real cost for none.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PUBLIC_DIR = path.join(__dirname, '..', 'public');

// An inline <script>: no `src` attribute, any other attributes allowed. The
// negative lookahead is the whole correctness of this file — a `<script src>`
// carries no inline body to hash, and matching one would add a hash for the
// empty string, which admits `<script></script>` from anywhere.
const INLINE_SCRIPT = /<script(?![^>]*\ssrc\s*=)[^>]*>([\s\S]*?)<\/script>/gi;

// Blocks the browser never executes, so they need no hash. `type="module"` IS
// executed and is deliberately absent from this list.
const NON_EXECUTABLE = /\stype\s*=\s*["']?(application\/(ld\+)?json|text\/(template|html|x-template))/i;

/** `'sha256-<base64>'`, the exact token CSP expects. */
function hashOf(source) {
  return "'sha256-" + crypto.createHash('sha256')
    .update(source, 'utf8').digest('base64') + "'";
}

/** Every inline-script hash in one HTML document, in document order. */
function hashesForHtml(html) {
  const out = [];
  let m;
  INLINE_SCRIPT.lastIndex = 0;
  while ((m = INLINE_SCRIPT.exec(html)) !== null) {
    if (NON_EXECUTABLE.test(m[0].slice(0, m[0].indexOf('>') + 1))) continue;
    out.push(hashOf(m[1]));
  }
  return out;
}

function* walkHtml(dir) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;                     // a missing public dir is not this file's problem
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) yield* walkHtml(full);
    else if (e.name.endsWith('.html')) yield full;
  }
}

/**
 * Every inline-script hash across the served pages, deduplicated and sorted.
 *
 * Sorted so the CSP header is byte-stable between boots — an unstable header
 * would make `/api/version`'s build hash move on a restart that changed
 * nothing, and that pair is the deploy diagnosis.
 */
function scriptHashes(dir = PUBLIC_DIR) {
  const seen = new Set();
  for (const file of walkHtml(dir)) {
    for (const h of hashesForHtml(fs.readFileSync(file, 'utf8'))) seen.add(h);
  }
  return [...seen].sort();
}

let _cached = null;

/** The `script-src` value: this origin, the Telegram widget, and our own inline blocks. */
function scriptSrc() {
  if (_cached === null) _cached = scriptHashes();
  // No 'unsafe-inline'. Note that a browser supporting hashes IGNORES
  // 'unsafe-inline' when any hash or nonce is present, so leaving it in "for
  // older browsers" would not even do what it appears to — it would only
  // mislead the next reader.
  return ["'self'", ...(_cached), 'https://telegram.org'].join(' ');
}

module.exports = { scriptSrc, scriptHashes, hashesForHtml, hashOf, PUBLIC_DIR };
