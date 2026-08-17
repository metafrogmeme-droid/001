'use strict';
/**
 * Every public page must be reachable from the landing page — checked against
 * the routes the SERVER actually mounts, not against a list somebody typed.
 *
 * `discoverability.test.js` already pins five routes by name. That is the
 * shape that rots: it protects exactly the five surfaces a past audit found
 * orphaned, and says nothing about the twenty-two shipped since. The failure
 * it cannot see is the one this repo keeps re-learning — `token_dossier` was
 * pure, correct, heavily tested and imported by nothing; #999 built a card
 * that rendered zero times. A page nobody can navigate to is the same defect
 * with a URL: it works, it is tested, and it does not exist to a visitor.
 *
 * So the route list is DERIVED. `server.js` is the authority on what is
 * served, and a page added there without a link fails here on the same commit
 * — which is the only moment anybody remembers why it was added.
 *
 * It caught one immediately: `/gas`, a live gas tracker across seven chains,
 * whose own meta description promises "unreadable chains omitted, never
 * invented". It followed the house rule and was reachable from nowhere.
 *
 * The three genuine exceptions live in `unlinked_routes.json` with reasons,
 * and that file is a ratchet in both directions: a new entry means somebody
 * just orphaned a page, and a stale entry must go in the commit that links it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(APP, ...p), 'utf8');

const SERVER = read('server.js');
const LANDING = read('public', 'index.html');
const BASELINE = JSON.parse(read('test', 'unlinked_routes.json'));

/**
 * Routes that are not pages a visitor navigates to. Deliberately a tiny,
 * explicit list: anything NOT named here is treated as a public page and must
 * be reachable, so forgetting to categorise a new route fails loudly rather
 * than silently excusing it.
 */
const NOT_A_PAGE = new Set([
  '/',          // the landing page itself
  '/healthz',   // liveness probe
  '/readyz',    // readiness probe
  '/diagz',     // operator diagnostics
  '/agent',     // parameterised: /agent/:address — no static destination
]);

/** Every `app.get('/x', …)` page route the server mounts. */
function servedRoutes() {
  const found = new Set();
  for (const m of SERVER.matchAll(/app\.get\(\s*'(\/[a-z0-9-]*)'/g)) found.add(m[1]);
  return [...found].sort();
}

const PAGES = servedRoutes().filter((r) => !NOT_A_PAGE.has(r));

// ── the sweep ─────────────────────────────────────────────────────────────

test('the route list is derived from the server, not transcribed', () => {
  // A floor, not an equality: the point is that this test reads server.js at
  // all. If the regex ever stops matching, every assertion below passes
  // vacuously — a guard that cannot fail, which is worse than no guard.
  assert.ok(PAGES.length >= 20,
    `only ${PAGES.length} page routes parsed from server.js — the matcher broke`);
  for (const known of ['/arena', '/guardian', '/provable']) {
    assert.ok(PAGES.includes(known), `${known} should have been parsed`);
  }
});

test('every public page is reachable from the landing page', () => {
  const orphans = PAGES
    .filter((r) => !BASELINE.routes[r])
    .filter((r) => !LANDING.includes(`href="${r}"`));
  assert.deepStrictEqual(orphans, [],
    'these pages are served but linked from nowhere on the landing page:\n'
    + orphans.map((r) => `  ${r}`).join('\n')
    + '\nLink each one, or add it to test/unlinked_routes.json with the reason.');
});

// ── the ratchet, both directions ──────────────────────────────────────────

test('a baselined route that IS linked must leave the baseline', () => {
  // The half people drop. Without it the file only ever grows, and it stops
  // describing anything — the same reason a passing entry in
  // known_failures.txt is a hard failure rather than a shrug.
  const stale = Object.keys(BASELINE.routes)
    .filter((r) => LANDING.includes(`href="${r}"`));
  assert.deepStrictEqual(stale, [],
    `${stale.join(', ')} is linked now — delete it from unlinked_routes.json `
    + 'in this commit');
});

test('a baselined route that no longer exists must leave the baseline', () => {
  const gone = Object.keys(BASELINE.routes).filter((r) => !servedRoutes().includes(r));
  assert.deepStrictEqual(gone, [],
    `${gone.join(', ')} is no longer served — delete it from unlinked_routes.json`);
});

test('every exception carries a reason someone can argue with', () => {
  for (const [route, why] of Object.entries(BASELINE.routes)) {
    assert.strictEqual(typeof why, 'string', route);
    assert.ok(why.length >= 40,
      `${route}'s reason is too short to be a reason: ${why}`);
    assert.ok(!/^(n\/a|tbd|todo|internal|skip)\.?$/i.test(why.trim()),
      `${route} is excused by a placeholder, not a reason`);
  }
});

// ── the specific orphan this was written for ──────────────────────────────

test('/gas is linked, and is not excused instead', () => {
  // Pinned by name because it is the one the sweep found. If a future refactor
  // drops the link, the sweep above catches it — unless somebody quiets the
  // sweep by baselining it, which this forbids.
  assert.ok(LANDING.includes('href="/gas"'), '/gas lost its link');
  assert.ok(!BASELINE.routes['/gas'],
    '/gas is a public market-facts page — link it, do not excuse it');
});

// ── and the older, narrower guard still holds ─────────────────────────────

test('the nav-and-footer routes still appear in both places', () => {
  // discoverability.test.js asserts this too. Kept here as well because that
  // file checks five hand-picked routes and this one checks the set: if the
  // two ever disagree, the disagreement is the finding.
  for (const href of ['/dashboard', '/track', '/proof', '/leaderboard', '/letter']) {
    const hits = LANDING.split(`href="${href}"`).length - 1;
    assert.ok(hits >= 2, `${href} appears ${hits}× — expected topnav and footer`);
  }
});
