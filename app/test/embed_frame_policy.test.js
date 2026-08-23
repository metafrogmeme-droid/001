'use strict';
/**
 * The one carve-out in a deny-everything frame policy, and the blast radius it
 * must not have.
 *
 * `server.js` sets `frame-ancestors 'none'` and `X-Frame-Options: DENY` on
 * every response. That is right for the dashboard: it is authenticated, it
 * carries controls that move money, and a framable authenticated page is a
 * clickjacking target — an attacker overlays their own UI on an invisible
 * iframe of yours and the click lands on a control the user never saw.
 *
 * A widget and a Farcaster Mini App both need the opposite. So there is exactly
 * one framable surface, and these tests are about the two ways that goes wrong:
 *
 *   1. THE CARVE-OUT LEAKS. Widening the policy in `server.js` instead of in
 *      the router would make the whole authenticated app framable, which is
 *      strictly worse than never shipping the widget. Most of this file is
 *      that regression check.
 *
 *   2. THE EMBED GROWS TEETH. It is safe to frame because it is
 *      unauthenticated and actionless. A later edit adding a button, a cookie
 *      read, or a credentialed fetch removes the property that made it safe
 *      without touching a single header, so the property is asserted
 *      structurally rather than trusted.
 *
 * `X-Frame-Options` gets its own test because it is the legacy header and
 * browsers that honour it IGNORE `frame-ancestors`. Setting the CSP without
 * removing the old header yields a page that is framable per its policy and
 * blank for some viewers — a bug that only appears for part of the audience.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const embed = require('../routes/embed');
const SERVER = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
// Blanked for the same reason as the router below, and it caught the same
// mistake twice in one run: `embed-signals.js` explains in a comment WHY it
// passes `credentials: 'omit'`, so the count of omissions came out at 3 against
// 2 fetches and the file failed for documenting itself. String contents survive
// `codeOnly`, so the message assertions further down still read real text.
const EMBED_JS = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'embed-signals.js'), 'utf8'));
// COMMENTS BLANKED. `routes/embed.js` documents at length that it reads no
// cookie and no Authorization header, so a scan for those words matches the
// promise as readily as a breach of it. The first run of this file failed on
// exactly that — the fifth instance of the family CLAUDE.md records, written
// by someone who had just read the warning.
const EMBED_ROUTE = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'embed.js'), 'utf8'));

// ── the carve-out does not leak ──────────────────────────────────────────

test('the APP-WIDE policy still denies framing outright', () => {
  // The regression that matters most. If this fails, the dashboard became
  // framable and the widget is not worth it.
  assert.match(SERVER, /"frame-ancestors 'none'"/,
    'the global CSP no longer denies framing — every authenticated page is '
    + 'now clickjackable');
  assert.match(SERVER, /setHeader\('X-Frame-Options', 'DENY'\)/,
    'the global X-Frame-Options deny is gone');
});

test('the global CSP names no allowed frame ancestor', () => {
  // A subtler version of the same leak: `frame-ancestors 'self' https://x`
  // would still look restrictive while permitting exactly the framing this
  // policy exists to forbid.
  const csp = SERVER.slice(SERVER.indexOf('const CSP = ['), SERVER.indexOf("].join('; ')"));
  // The first draft matched /frame-ancestors[^"']*/ and captured the bare
  // directive NAME: the character class excludes the quote that opens 'none',
  // so it stopped one character in and compared "frame-ancestors" against
  // "frame-ancestors 'none'". The whole quoted entry is what to read.
  const fa = csp.match(/"frame-ancestors[^"]*"/);
  assert.ok(fa, 'no frame-ancestors directive in the global CSP at all');
  assert.equal(fa[0], `"frame-ancestors 'none'"`);
});

test('embed is mounted BEFORE express.static, or it never sets its headers', () => {
  // express.static would serve a matching file first and the router's headers
  // would never run. Ordering is load-bearing, so it is asserted.
  const mount = SERVER.indexOf("app.use('/embed'");
  const stat = SERVER.indexOf('express.static(');
  assert.ok(mount > -1, 'the embed router is not mounted');
  assert.ok(stat > -1);
  assert.ok(mount < stat,
    'the embed router mounts after express.static; a static file would answer '
    + 'first and the frame policy would never be applied');
});

// ── the embed policy itself ──────────────────────────────────────────────

test('the embed CSP permits framing', () => {
  assert.match(embed.embedCsp(), /frame-ancestors \*/);
});

test('an allowlist is honoured when one is configured', () => {
  const prev = process.env.EMBED_FRAME_ANCESTORS;
  process.env.EMBED_FRAME_ANCESTORS = 'https://warpcast.com https://partner.example';
  try {
    assert.match(embed.embedCsp(),
      /frame-ancestors https:\/\/warpcast\.com https:\/\/partner\.example/);
    assert.doesNotMatch(embed.embedCsp(), /frame-ancestors \*/);
  } finally {
    if (prev === undefined) delete process.env.EMBED_FRAME_ANCESTORS;
    else process.env.EMBED_FRAME_ANCESTORS = prev;
  }
});

test('a blank allowlist falls back to * rather than to an empty directive', () => {
  // `frame-ancestors ` with nothing after it is a malformed directive; browsers
  // treat a malformed CSP directive inconsistently and one reading of it is
  // "allow everything". An empty env var means unset, so it means the default.
  const prev = process.env.EMBED_FRAME_ANCESTORS;
  process.env.EMBED_FRAME_ANCESTORS = '   ';
  try {
    assert.match(embed.embedCsp(), /frame-ancestors \*$/);
  } finally {
    if (prev === undefined) delete process.env.EMBED_FRAME_ANCESTORS;
    else process.env.EMBED_FRAME_ANCESTORS = prev;
  }
});

test('the embed CSP is TIGHTER than the app CSP everywhere else', () => {
  const csp = embed.embedCsp();
  assert.match(csp, /default-src 'none'/,
    'a page running in a stranger\'s document should carry the least authority '
    + 'it can, not inherit the app\'s');
  assert.match(csp, /connect-src 'self'/);
  assert.match(csp, /form-action 'none'/);
  assert.match(csp, /base-uri 'none'/);
  assert.doesNotMatch(csp, /unsafe-inline/,
    'the embed allows inline script or style — the app itself does not allow '
    + 'inline script, and the framable page must not be the weak one');
  assert.doesNotMatch(csp, /unsafe-eval/);
});

test('the router REMOVES X-Frame-Options rather than only setting the CSP', () => {
  // Browsers honouring the legacy header ignore frame-ancestors entirely, so
  // leaving it set produces a page that is framable by policy and blank in
  // practice — for some viewers only.
  assert.match(EMBED_ROUTE, /removeHeader\('X-Frame-Options'\)/,
    'X-Frame-Options: DENY survives on the embed response');
});

// ── it is safe to frame because it can do nothing ────────────────────────

test('the embed page is unauthenticated: no cookie, session or auth header', () => {
  for (const banned of [/req\.cookies/, /requireAuth/, /authorization/i, /req\.user\b/]) {
    assert.doesNotMatch(EMBED_ROUTE, banned,
      `the embed router touches ${banned} — a framable page with authenticated `
      + 'content is exactly the clickjacking target the global deny exists for');
  }
});

test('the embed router exposes GET only — nothing to clickjack', () => {
  // A click landing on an invisible frame can only cause harm if there is an
  // action to trigger. There is not, and that is enforced rather than assumed.
  const verbs = EMBED_ROUTE.match(/router\.(get|post|put|patch|delete)\b/g) || [];
  const nonGet = verbs.filter((v) => !v.endsWith('get'));
  assert.deepEqual(nonGet, [],
    `the embed router defines a mutating route (${nonGet.join(', ')}). A framable `
    + 'page must not be able to act.');
});

test('the client script sends NO credentials with its fetches', () => {
  // Inside a frame the browser would otherwise attach whatever cookies the
  // viewer holds for this origin — an authenticated request issued from a page
  // a stranger controls.
  // Counted rather than sliced. The first draft matched /fetch\([^)]*\)/ and
  // stopped at the ')' inside encodeURIComponent(sym), so it checked a
  // truncated call that could not contain the option and failed on correct
  // code. Balanced-paren parsing is not worth writing here; equality of counts
  // says the same thing and cannot be fooled by a nested call.
  const calls = (EMBED_JS.match(/\bfetch\(/g) || []).length;
  const omits = (EMBED_JS.match(/credentials:\s*'omit'/g) || []).length;
  assert.ok(calls >= 2, 'the scan found no fetches; it is reading nothing');
  assert.equal(omits, calls,
    `${calls} fetch call(s) but only ${omits} omit credentials — inside a frame `
    + 'the browser attaches the viewer\'s cookies for this origin, making an '
    + 'authenticated request from a page a stranger controls');
});

test('the client script has no form, button, or write request', () => {
  const banned = [
    [/method:\s*['"]POST['"]/i, 'a POST'],
    [/<form/i, 'a form'],
    [/<button/i, 'a button'],
    [/localStorage|sessionStorage/, 'browser storage'],
  ];
  for (const [re, what] of banned) {
    assert.doesNotMatch(EMBED_JS, re,
      `the embed page contains ${what}; read-only and actionless is what makes `
      + 'it safe to frame');
  }
});

// ── it stays honest about failure, in someone else's page ────────────────

test('a failed signals read is not rendered as an empty board', () => {
  // The rule, one origin removed: the viewer may not know RUNECLAW exists, so
  // "no signals" and "we could not ask" must not look the same.
  assert.match(EMBED_JS, /if \(!r\.ok\) throw/,
    'a non-ok response falls through to the empty state');
  assert.match(EMBED_JS, /not a statement that there are none/);
});

test('the empty and error states say different things', () => {
  const empty = EMBED_JS.match(/No open signals right now/);
  const error = EMBED_JS.match(/could not be loaded/);
  assert.ok(empty, 'no empty state');
  assert.ok(error, 'no error state');
});

test('a failed chart keeps its slot and names the reason', () => {
  assert.match(EMBED_JS, /placeholderHtml/);
  assert.doesNotMatch(EMBED_JS, /style\.display\s*=\s*['"]none['"]/,
    'the embed hides a failed chart — the defect the renderer was written to '
    + 'replace, reintroduced one surface over');
});

test('the board names its source, so a framed reader knows whose claim it is', () => {
  assert.match(EMBED_JS, /RUNECLAW/);
});
