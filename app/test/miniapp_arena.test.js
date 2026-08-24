'use strict';
/**
 * The authenticated Mini App: the security contract, and the private view.
 *
 * This is the first framable page in the repo that can ACT, so the first
 * section is not about rendering at all — it is the clickjacking argument,
 * pinned. `/embed/*` is safe to frame because there is nothing to
 * authenticate; this page is safe to frame for a different and more fragile
 * reason, and fragile reasons are the ones that need tests.
 *
 * The argument: a click on an invisible frame can only cause harm if there is
 * a SESSION inside that frame. There cannot be one here, because the session
 * is a bearer token held in a JavaScript variable — no cookie, no storage — so
 * every fresh load starts signed out, and the only route back in is a SIWF
 * signature over a nonce our server issued moments ago, naming our domain. An
 * attacker framing this page controls the postMessage replies and still cannot
 * produce that.
 *
 * Every clause of that is a test below. If one stops being true, the page
 * stops being safe to frame and this file should say so loudly.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const APP_JS = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'miniapp-arena.js'), 'utf8'));
const ROUTE = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'miniapp.js'), 'utf8'));
const V = require('../public/js/miniapp-view');

// ── the clickjacking argument, clause by clause ───────────────────────────

test('the session is never written to storage or a cookie', () => {
  // THE LOAD-BEARING CLAUSE. A persisted token is a token that exists inside a
  // frame the viewer never opened, and the whole safety argument for framing
  // an authenticated page collapses the moment one does.
  for (const [re, what] of [
    [/localStorage/, 'localStorage'],
    [/sessionStorage/, 'sessionStorage'],
    [/document\.cookie/, 'document.cookie'],
    [/indexedDB/i, 'IndexedDB'],
  ]) {
    assert.doesNotMatch(APP_JS, re,
      `the Mini App persists its session in ${what}; a fresh frame on an `
      + 'attacker\'s site would then start SIGNED IN');
  }
});

test('the token is not hung on window or any global', () => {
  // `window.token = ...` would be readable by nothing cross-origin, but it
  // also outlives the closure and invites exactly the storage shortcut above.
  assert.doesNotMatch(APP_JS, /window\.\w*[Tt]oken\s*=/,
    'the session token was placed on a global');
});

test('every request omits cookie credentials and sends the token explicitly', () => {
  // Inside a frame the browser would otherwise attach whatever cookies the
  // viewer holds for this origin — an authenticated request issued by a page
  // a stranger controls. Authentication has to be something this page DOES.
  const calls = (APP_JS.match(/\bfetch\(/g) || []).length;
  const omits = (APP_JS.match(/credentials:\s*'omit'/g) || []).length;
  assert.ok(calls >= 1, 'the scan found no fetches; it is reading nothing');
  assert.equal(omits, calls,
    `${calls} fetch call(s) but ${omits} omit credentials`);
  assert.match(APP_JS, /authorization[^\n]*Bearer/i,
    'the token is never actually sent, so nothing would authenticate');
});

test('the page never mints or invents a nonce', () => {
  // A nonce the client chooses binds the signature to a value the caller
  // picked, which is the same as binding it to nothing.
  assert.doesNotMatch(APP_JS, /nonce\s*[:=]\s*(Math\.random|Date\.now|['"`])/,
    'the Mini App generates its own sign-in nonce');
  assert.match(APP_JS, /\/api\/farcaster\/nonce/,
    'the nonce is not being fetched from the server');
});

test('the route sends no cookie and forbids off-origin connections', () => {
  assert.match(ROUTE, /removeHeader\('Set-Cookie'\)/,
    'a cookie could reach a framable authenticated page');
  assert.match(ROUTE, /connect-src 'self'/,
    "without connect-src 'self' the session token could be posted to a third party");
  assert.match(ROUTE, /no-store/,
    'a signed-in view is cacheable — that caches somebody\'s session');
});

test('the miniapp router defines GET only', () => {
  // It serves a document. Anything that mutates belongs behind authMiddleware
  // in /api/arena, where the express-route-auth guard counts it.
  const verbs = ROUTE.match(/router\.(get|post|put|patch|delete)\b/g) || [];
  const nonGet = verbs.filter((v) => !v.endsWith('get'));
  assert.deepEqual(nonGet, [], `the miniapp router mutates: ${nonGet.join(', ')}`);
});

test('a 401 signs the page out rather than leaving stale numbers up', () => {
  // An expired session showing a live-looking account is numbers nobody can
  // act on, presented as if they could.
  assert.match(APP_JS, /status === 401/);
  assert.match(APP_JS, /token = null/);
});

// ── the private view: amounts ARE allowed here, and only here ─────────────

test('a virtual balance renders on the private view', () => {
  // §4 permits amounts on a private per-user surface, and these are virtual
  // anyway. The rule this file must not break is the other direction: nothing
  // here feeds a public payload.
  const html = V.accountHtml({ equity: 10345.22, balance: 9000, return_pct: 3.45 });
  assert.match(html, /10345\.22/);
  assert.match(html, /vUSDT/);
  assert.match(html, /\+3\.45%/);
});

test('an unreadable equity is an em dash, not zero', () => {
  const html = V.accountHtml({ equity: null, balance: null, return_pct: null });
  assert.match(html, /—/);
  assert.ok(!/0\.00/.test(html), 'an absent balance rendered as zero');
  assert.match(html, /a-flat/, 'an unreadable return was given a win colour');
});

test('a position whose mark could not be read says so, and shows no P&L', () => {
  // The account payload sends `mark: null` and `pnl: null` when the feed
  // failed. This is the number a person decides whether to CLOSE on, so a
  // fabricated 0.00 beside it is the +0.00% sin with a stake attached.
  const html = V.positionHtml({
    id: 7, symbol: 'BTCUSDT', direction: 'LONG', entry: 60000,
    mark: null, pnl: null, margin: 100, leverage: 5,
  });
  assert.match(html, /mark unavailable/,
    'a missing mark rendered as a bare dash with no explanation');
  assert.ok(!/>\s*\+?0\.00\s*</.test(html), 'an absent P&L rendered as break-even');
  assert.match(html, /a-flat/);
  assert.match(html, /BTCUSDT/, 'the position lost the market it is in');
  assert.match(html, /100\.00 margin/, 'a readable field was dropped with the unreadable one');
});

test('a real zero P&L is break-even, not unknown', () => {
  const html = V.positionHtml({ id: 1, symbol: 'X', direction: 'LONG', pnl: 0, mark: 5 });
  assert.match(html, /a-even/, 'a measured break-even was rendered as unreadable');
});

test('no open positions is a measurement and says so', () => {
  assert.match(V.positionsHtml([]), /No open positions/);
});

test('the open form takes its limits from the server, not from itself', () => {
  // A cap written into the UI drifts from the one that is enforced, and then
  // the form either rejects what the server allows or offers what it refuses.
  const html = V.openFormHtml({ min_margin: 10, max_leverage: 20 });
  assert.match(html, /min="10"/);
  assert.match(html, /max="20"/);

  // With no limits known, it must invent none — but the assertion has to name
  // WHICH ones. The first draft banned every `min=` and failed on the leverage
  // field's `min="1"`, which is not a server-derived cap at all: leverage
  // below 1× is not a policy this deployment chose, it is what leverage means.
  // Banning it would have removed a true constraint to satisfy a rule about
  // invented ones.
  const bare = V.openFormHtml(null);
  assert.ok(!/class="m-margin"[^>]*min=/.test(bare),
    'a margin minimum was invented with none supplied');
  assert.ok(!/max=/.test(bare), 'a leverage cap was invented with none supplied');
  assert.ok(!/min ['"]?\d/.test(bare.replace(/<[^>]*>/g, '')),
    'the note advertised a limit nobody supplied');
});

// ── a cancelled sign-in is a decision, not a fault ────────────────────────

test('cancelling sign-in is not reported as an error', () => {
  // The person was asked and said no. Describing their own decision back to
  // them as a failure is the same class of mistake as an outage rendering as
  // a measurement — the app asserting something that did not happen.
  const html = V.signInProblemHtml('rejected');
  assert.match(html, /cancelled/i);
  assert.match(html, /m-problem--note/, 'a cancellation was styled as a warning');
  assert.ok(!/error|failed|problem occurred/i.test(html));
});

test('the four sign-in outcomes are four different sentences', () => {
  const seen = new Set();
  for (const s of ['rejected', 'no-host', 'unknown', 'unavailable']) {
    const html = V.signInProblemHtml(s);
    assert.ok(html.length > 20, `${s} rendered nothing`);
    seen.add(html);
  }
  assert.equal(seen.size, 4, 'two distinct outcomes render identically');
});

test('a domain mismatch is named specifically', () => {
  // It means the signature was for another app. Worth saying, because the
  // generic message would send someone re-tapping a button that cannot work.
  assert.match(V.signInProblemHtml('refused', 'domain_mismatch'), /different app/i);
  assert.match(V.signInProblemHtml('refused', 'unknown_or_used_nonce'), /expired/i);
});

// ── the server's own error text survives to the screen ────────────────────

test('the server\'s message is shown rather than replaced', () => {
  // "Unknown symbol — use a listed USDT-M pair like BTCUSDT" is the most
  // useful thing this app can show. A generic "could not open" discards the
  // only part that says what to do differently.
  assert.match(APP_JS, /r\.body && r\.body\.error/,
    'the server\'s error message is discarded in favour of a generic one');
});

test('a handle-less account is told so, not left blank', () => {
  // null is a real answer: the account is invisible on the board until it
  // picks a handle, and that is worth saying at the moment someone starts
  // trading for a leaderboard they will not appear on.
  assert.match(APP_JS, /No leaderboard handle yet/);
});
