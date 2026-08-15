'use strict';
/**
 * M14 — `script-src` carried `'unsafe-inline'` while the session token sat in
 * localStorage.
 *
 * That combination is what turns one HTML-escaping slip into full session
 * theft: an injected inline block executes (nothing in CSP objects), reads the
 * bearer token (`app.js` resolveToken), and leaves via a top-level navigation
 * (connect-src is `'self'`, but nothing restricts navigating away). Removing
 * `'unsafe-inline'` breaks the chain at the first step.
 *
 * The pages are static — `express.static` and `res.sendFile` off disk — so
 * there is no render step in which to mint a nonce. Hashes need none, and are
 * computed from the same bytes that get served, so the policy cannot drift
 * from the pages it authorises.
 *
 * WHAT THESE TESTS ARE REALLY GUARDING. A CSP mistake here fails SILENTLY and
 * only in a browser: the server returns 200, the HTML is intact, the tests
 * pass, and the script simply never runs. Nothing in a Node test suite notices
 * that. So each test below pins one way the policy could stop matching the
 * pages:
 *
 *   * a hash missing for a block that ships   → that page's script dies
 *   * an inline on* handler coming back       → that control dies (no hash form)
 *   * a data-act with no dispatcher entry     → that button dies
 *
 * All three are the same failure — code present, never reached — which is the
 * one CLAUDE.md says a source scan cannot distinguish. Here the scan IS the
 * subject: the question is whether two artefacts agree, not whether one runs.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const APP = path.join(__dirname, '..');
const csp = require('../lib/csp');

const read = (...p) => fs.readFileSync(path.join(APP, ...p), 'utf8');

/**
 * Source with comments removed.
 *
 * CLAUDE.md: "Strip comments first. A comment that quotes the string it forbids
 * is indistinguishable from the code doing it, and this has produced four false
 * failures." It produced a fifth while this file was being written — the
 * dispatcher's own comment explains that it replaces `onclick="fn()"`, and both
 * scans below duly reported the explanation as the offence.
 *
 * Block and HTML comments only, plus FULL-LINE `//`. A general `//` rule would
 * eat `https://…` and any string containing a slash pair, and stripping too
 * much turns a real hit into a silent pass — the worse direction for a scan
 * whose whole job is to find things.
 */
function codeOnly(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^[ \t]*\/\/[^\n]*$/gm, '');
}

function htmlFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...htmlFiles(full));
    else if (e.name.endsWith('.html')) out.push(full);
  }
  return out;
}

const PAGES = htmlFiles(csp.PUBLIC_DIR);

// ── the policy itself ──────────────────────────────────────────────────────

test('script-src no longer allows arbitrary inline script', () => {
  const src = csp.scriptSrc();
  assert.ok(!src.includes("'unsafe-inline'"),
    "'unsafe-inline' makes an XSS a session theft rather than a defacement");
  // 'unsafe-hashes' would re-admit inline event handlers — the weaker form of
  // the same hole, and the reason the on* handlers were converted instead.
  assert.ok(!src.includes("'unsafe-hashes'"));
  assert.ok(src.startsWith("'self'"));
  assert.ok(src.includes('https://telegram.org'), 'the login widget still loads');
});

test('server.js builds its policy from the computed hashes', () => {
  // Wiring: a correct lib that server.js does not call protects nothing.
  const server = read('server.js');
  assert.match(server, /script-src \$\{require\('\.\/lib\/csp'\)\.scriptSrc\(\)\}/);
  assert.ok(!/script-src[^`]*'unsafe-inline'/.test(server),
    'a hard-coded unsafe-inline survived beside the computed one');
});

test('the hash is the SHA-256 of the block body, base64, CSP-quoted', () => {
  const body = "\n  console.log('hi');\n";
  const expected = "'sha256-" + crypto.createHash('sha256')
    .update(body, 'utf8').digest('base64') + "'";
  assert.strictEqual(csp.hashOf(body), expected);
  // …and that is what the extractor produces for a real element.
  assert.deepStrictEqual(
    csp.hashesForHtml(`<script>${body}</script>`), [expected]);
});

// ── the extractor's edges ──────────────────────────────────────────────────

test('an external script contributes no hash', () => {
  // Hashing a <script src> would add the hash of the EMPTY string, which
  // admits <script></script> from anywhere — a hole opened by the fix.
  assert.deepStrictEqual(csp.hashesForHtml('<script src="/js/app.js"></script>'), []);
  assert.deepStrictEqual(
    csp.hashesForHtml('<script src="/js/a.js" defer></script>'), []);
  assert.strictEqual(csp.hashesForHtml('<script></script>').length, 1,
    'a genuinely empty inline block is still an inline block');
});

test('a non-executable block needs no hash', () => {
  assert.deepStrictEqual(
    csp.hashesForHtml('<script type="application/json">{"a":1}</script>'), []);
  // type="module" IS executed and must still be hashed.
  assert.strictEqual(
    csp.hashesForHtml('<script type="module">export{}</script>').length, 1);
});

test('the policy is byte-stable across calls', () => {
  // An unstable header would move /api/version's build hash on a restart that
  // changed nothing, and that pair is how a deploy is diagnosed.
  assert.strictEqual(csp.scriptSrc(), csp.scriptSrc());
  assert.deepStrictEqual(csp.scriptHashes(), csp.scriptHashes().slice().sort());
});

// ── the policy must match every page actually served ───────────────────────

test('every inline block on every served page is in the policy', () => {
  const policy = csp.scriptSrc();
  const missing = [];
  for (const file of PAGES) {
    for (const h of csp.hashesForHtml(fs.readFileSync(file, 'utf8'))) {
      if (!policy.includes(h)) missing.push(path.basename(file));
    }
  }
  assert.deepStrictEqual([...new Set(missing)], [],
    'these pages ship a script the policy would block — silently, in browsers only');
  assert.ok(PAGES.length > 20, `only ${PAGES.length} pages scanned — walker drifted?`);
});

test('no served page carries an inline event handler', () => {
  // There is no hash form for these. Under the new policy they do not throw,
  // do not log, and do not run — the control just stops working.
  const offenders = [];
  for (const file of PAGES) {
    const html = codeOnly(fs.readFileSync(file, 'utf8'));
    const hits = html.match(/\son(click|change|input|submit|load|error|keyup|keydown|focus|blur)\s*=/gi);
    if (hits) offenders.push(`${path.basename(file)} (${hits.length})`);
  }
  assert.deepStrictEqual(offenders, [],
    'convert to a delegated listener, as index.html did — CSP cannot allow these');
});

// ── the dispatcher that replaced them ──────────────────────────────────────

test('every data-act on the landing page has a dispatcher entry', () => {
  // The failure this catches is a dead button: the attribute renames cleanly,
  // the page loads, and the click does nothing at all.
  const html = codeOnly(read('public', 'index.html'));
  const used = new Set(
    [...html.matchAll(/data-act="([A-Za-z]+)"/g)].map((m) => m[1]));
  assert.ok(used.size >= 15, `only ${used.size} actions found — did the rename revert?`);

  // The allowlist array itself, not "any quoted word in the file".
  const arr = html.match(/var ALLOWED = \[([\s\S]*?)\]/);
  assert.ok(arr, 'the dispatcher allowlist is gone');
  const listed = new Set(
    [...arr[1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]));
  for (const name of used) {
    if (name === 'copySelf') continue;      // handled explicitly in the dispatcher
    assert.ok(listed.has(name), `data-act="${name}" is in no allowlist entry`);
  }
});

test('every dispatched action is a real top-level function', () => {
  const html = codeOnly(read('public', 'index.html'));
  const used = [...new Set(
    [...html.matchAll(/data-act="([A-Za-z]+)"/g)].map((m) => m[1]))];
  for (const name of used) {
    if (name === 'copySelf') continue;
    const defined = new RegExp(
      `^(async )?function ${name}\\(|^\\s*window\\.${name}\\s*=`, 'm').test(html);
    assert.ok(defined, `${name} is dispatched but defined nowhere — a dead control`);
  }
});

test('the dispatcher is delegated, not bound per element', () => {
  // getToken() injects the token box and its two buttons after load; a
  // per-element binding at DOMContentLoaded would miss exactly those.
  const html = read('public', 'index.html');
  assert.match(html, /document\.addEventListener\('click'/);
  assert.match(html, /closest\('\[data-act\]'\)/);
  // The anchors that used to end in `return false` must still suppress the jump.
  assert.match(html, /data-prevent/);
  assert.match(html, /hasAttribute\('data-prevent'\)/);
});
