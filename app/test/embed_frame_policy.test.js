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
// The card markup moved out of embed-signals.js, so the scans that ask what the
// page RENDERS have to read the file that renders it. Left pointed at the old
// one they would have gone on passing over a file with no markup in it at all —
// a scan that finds nothing reads exactly like one that checked and was happy.
/**
 * EVERY script the embed router serves, discovered rather than listed.
 *
 * The frame-safety rules below were written against embed-signals.js because
 * it was the only page. Adding /embed/arena would have slipped past all of
 * them — a new page with a form, a credentialed fetch or a POST would have
 * been waved through by a suite that still reported full coverage, because
 * every assertion was reading a file that had not changed.
 *
 * That is CLAUDE.md's own warning about the cache-buster ratchet, which for
 * its whole life matched only `/js/*.js` and therefore never saw styles.css:
 * "a guard with a blind spot over the biggest asset is not a smaller guard —
 * it reads as coverage while providing none." Globbed, so page three is
 * covered on the day it is written and not on the day somebody remembers.
 */
const EMBED_SCRIPTS = fs.readdirSync(path.join(__dirname, '..', 'public', 'js'))
  .filter((f) => /^embed[-.]/.test(f) && f.endsWith('.js'))
  .map((f) => ({
    name: f,
    src: codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', f), 'utf8')),
  }));

const EMBED_ROW = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'embed-row.js'), 'utf8'));

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

test('NO embed script has a form or write request', () => {
  // Across every embed script, not just the first one written.
  const banned = [
    [/method:\s*['"]POST['"]/i, 'a POST'],
    [/<form/i, 'a form'],
    [/localStorage|sessionStorage/, 'browser storage'],
  ];
  const bad = [];
  for (const { name, src } of EMBED_SCRIPTS) {
    for (const [re, what] of banned) if (re.test(src)) bad.push(`${name}: ${what}`);
  }
  assert.deepEqual(bad, [],
    'read-only and actionless is what makes these pages safe to frame:\n  '
    + bad.join('\n  '));
});

test('no /embed page loads a script from the AUTHENTICATED Mini App', () => {
  // The two framable namespaces have opposite contracts. /embed/* is safe to
  // frame because there is nothing to authenticate; /miniapp/* is safe for a
  // narrower reason argued in its own router. Pulling a miniapp module into an
  // embed page would move a session-bearing surface into the namespace whose
  // whole guarantee is that it has none — and every assertion in this file
  // would still pass, because they scan `embed-*.js` and the smuggled code
  // lives in a file named otherwise.
  assert.doesNotMatch(EMBED_ROUTE, /\/js\/miniapp[-.]/,
    'routes/embed.js serves a Mini App script; the actionless guarantee is gone');
  for (const { name, src } of EMBED_SCRIPTS) {
    assert.doesNotMatch(src, /RCMiniView|miniapp-/,
      `${name} reaches into the authenticated Mini App code`);
  }
});

test('the scan is actually reading the embed scripts', () => {
  // A glob that matches nothing passes every assertion above it in silence —
  // the failure mode this file exists to prevent, one level up. Both known
  // pages must be in the set, so a rename that empties the glob fails here.
  const names = EMBED_SCRIPTS.map((s) => s.name);
  assert.ok(names.length >= 2, `the embed script scan found ${names.length} files`);
  for (const required of ['embed-signals.js', 'embed-arena.js']) {
    assert.ok(names.includes(required), `${required} is not being scanned`);
  }
});

test('NO embed script sends credentials with a fetch', () => {
  // Per file, because one page getting this right does not protect the others.
  // Inside a frame the browser would attach the viewer's cookies for this
  // origin, making an authenticated request from a page a stranger controls.
  const bad = [];
  for (const { name, src } of EMBED_SCRIPTS) {
    const calls = (src.match(/\bfetch\(/g) || []).length;
    if (!calls) continue;
    const omits = (src.match(/credentials:\s*'omit'/g) || []).length;
    if (omits !== calls) bad.push(`${name}: ${calls} fetch(es), ${omits} omit credentials`);
  }
  assert.deepEqual(bad, [], 'a framed page issues a credentialed request:\n  ' + bad.join('\n  '));
});

/*
 * `<button>` USED TO BE ON THAT LIST, AND REMOVING IT WAS A SECURITY DECISION.
 * Recorded here rather than in a commit message nobody re-reads.
 *
 * The rule this file enforces is not "no buttons" — it is that a click landing
 * on an invisible frame must not exercise the viewer's authority. Every clause
 * that actually delivers that is untouched: no cookies, no credentialed fetch,
 * GET-only routes, no storage, `form-action 'none'`.
 *
 * The share button exercises no authority. Its whole effect is one postMessage
 * to `window.parent`. In a clickjacking attack the parent IS the attacker, so
 * the complete result is that our page tells the attacker's page that somebody
 * clicked — no cookie, no request to us, nothing changed anywhere. The cast is
 * composed in Warpcast's own UI, behind a confirmation we neither see nor
 * control, and it cannot be posted without the person deliberately sending it.
 *
 * So the guard is narrowed rather than deleted: the button may exist, and the
 * tests below pin that it stays the only interactive element and that it still
 * cannot reach the network, storage, or a mutating route.
 */

test('the only interactive element is the share button', () => {
  // A second control appearing would not be caught by the narrowed rule above,
  // which is exactly the weakness of relaxing a blanket ban. Counted instead:
  // one button, and it is the share one.
  const buttons = EMBED_ROW.match(/<button/gi) || [];
  assert.equal(buttons.length, 1,
    `the card renders ${buttons.length} buttons; the frame-safety argument was `
    + 'made about exactly one, and covers no others');
  assert.match(EMBED_ROW, /class="e-share"/, 'the one button is not the share button');
});

test('the share handler can only talk to the host — no fetch, no storage', () => {
  // The click path, isolated. If a future edit makes the share button POST a
  // "share count" somewhere, the clickjacking argument above stops being true
  // and this fails.
  const handler = EMBED_JS.slice(EMBED_JS.indexOf('function onShareClick'));
  assert.ok(handler.length > 100, 'the handler scan found nothing; it is reading nothing');
  const body = handler.slice(0, handler.indexOf('\n  }') + 4);
  for (const [re, what] of [
    [/\bfetch\(/, 'a fetch'],
    [/XMLHttpRequest/, 'an XHR'],
    [/localStorage|sessionStorage|document\.cookie/, 'storage or cookies'],
    [/location\s*=|location\.href\s*=/, 'a navigation'],
  ]) {
    assert.doesNotMatch(body, re,
      `the share handler performs ${what} — the frame-safety argument for `
      + 'allowing this button assumed it only posts a message to the host');
  }
  assert.match(body, /composeCast\(/, 'the handler no longer opens a composer');
});

test('no share button is rendered when there is no host to share to', () => {
  // An affordance that silently does nothing when tapped asserts a capability
  // that is not there — and on a plain website embed there is no cast composer
  // in existence. It also keeps the button off every framed copy that is not a
  // Mini App host.
  const ROW = require('../public/js/embed-row');
  const sig = { symbol: 'BTC/USDT', direction: 'LONG', confidence: '0.7' };
  assert.ok(!ROW.rowHtml(sig, { canShare: false }).includes('<button'),
    'a share button was drawn with no host to receive it');
  assert.ok(!ROW.rowHtml(sig, {}).includes('<button'),
    'the share button defaults to present; it must default to absent');
  assert.ok(ROW.rowHtml(sig, { canShare: true }).includes('e-share'),
    'the button never appears even when a host is present');
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
