'use strict';
/**
 * The five public Arena routes, made safe to point a Mini App at.
 *
 * These are the endpoints a competition Mini App would read, and they were the
 * uncapped half of a path whose other half has been capped since it was
 * written: `routes/embed.js` limits itself to 120/min per IP, and the board it
 * serves refreshes every 30 seconds. A board somebody leaves open on a phone is
 * a poller, not a visitor, and there were no limiters here at all.
 *
 * Three defects, all on surfaces that answer strangers:
 *
 *   1. No rate limiting on /leaderboard, /tape, /trader/:handle, /season,
 *      /seasons.
 *   2. /leaderboard runs FIVE unbounded queries per request — every account,
 *      every open position, every opted-in handle, and two GROUP BYs over the
 *      whole trade table.
 *   3. `SELECT ... FROM arena_seasons LIMIT 1` with no ORDER BY. Correct only
 *      while exactly one season has ever existed. Genesis ends 2026-09-24.
 *
 * WHY THE FIX FOR (2) IS A CACHE AND NOT A LIMIT. A leaderboard ranks by
 * comparing everybody. Capping the input silently drops traders and returns a
 * ranking that is confidently wrong — nobody reading rank 3 would know it came
 * from a truncated field. A cache changes how OFTEN the honest answer is
 * computed and never what it says. That distinction is the whole design and it
 * is what the tests below pin.
 */

// Set before requiring the router: routes/arena pulls in ../auth, which refuses
// to load without a secret of its own. Same preamble as test/arena.test.js.
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const arenaRoute = require('../routes/arena');
const { pickCurrentSeason } = arenaRoute;

// ── (3) which season is "the" season ──────────────────────────────────────

const S = (name, starts, ends) => ({
  id: name, name, starts_at: new Date(starts), ends_at: new Date(ends), rules: null,
});
const NOW = new Date('2026-08-24T12:00:00Z');

test('the live season wins, whatever order the database returns rows in', () => {
  // The defect exactly: MySQL may return any row for an unordered LIMIT 1, and
  // the in-memory shim used by tests sorts newest-first — so the two disagree
  // BY CONSTRUCTION and neither is wrong. Both orders must give one answer.
  const genesis = S('Genesis', '2026-07-24', '2026-09-24');   // live at NOW
  const ended = S('Alpha', '2026-05-01', '2026-06-01');
  const upcoming = S('Season2', '2026-09-24', '2026-11-24');

  for (const order of [
    [genesis, ended, upcoming],
    [upcoming, ended, genesis],
    [ended, upcoming, genesis],
  ]) {
    assert.equal(pickCurrentSeason(order, NOW).name, 'Genesis',
      'a public board named a season that is not the live one');
  }
});

test('an upcoming season does not displace the season being played', () => {
  // The scheduled case, and the one that breaks first: an admin authors season
  // 2 before Genesis ends. "Newest by start date" would hand the board a season
  // nobody has traded in yet, with empty standings, while a live competition is
  // running underneath it.
  const genesis = S('Genesis', '2026-07-24', '2026-09-24');
  const next = S('Season2', '2026-09-24', '2026-11-24');
  assert.equal(pickCurrentSeason([next, genesis], NOW).name, 'Genesis');
});

test('with nothing live, the most recent season shows rather than nothing', () => {
  // An ended season keeps showing until its successor begins. Blinking to null
  // in the gap would read as "there is no competition" when the truth is
  // "the last one finished".
  const a = S('Alpha', '2026-01-01', '2026-02-01');
  const b = S('Beta', '2026-05-01', '2026-06-01');
  assert.equal(pickCurrentSeason([a, b], NOW).name, 'Beta');
  assert.equal(pickCurrentSeason([b, a], NOW).name, 'Beta');
});

test('no seasons at all is null, not a fabricated one', () => {
  assert.equal(pickCurrentSeason([], NOW), null);
  assert.equal(pickCurrentSeason(null, NOW), null);
  assert.equal(pickCurrentSeason(undefined, NOW), null);
});

// ── (2) the board is NOT cached, and that is the finding ──────────────────

test('the leaderboard is computed fresh on every request', () => {
  /*
   * THIS IS A RECORD OF A FIX THAT WAS WRITTEN AND THEN REVERTED, kept because
   * the reasoning is the valuable part and the next person will have the same
   * idea.
   *
   * Five unbounded queries per request is a real cost, and a 20s TTL cache is
   * the obvious answer. It was implemented — and `test/arena.test.js` failed on
   * the one sequence that matters most to a competition:
   *
   *     GET /leaderboard        -> memoised
   *     POST /leaderboard/opt-in
   *     GET /leaderboard        -> the memo, which predates the opt-in
   *
   * A person who has just joined is told they are not on the board, at the
   * exact moment the product is trying to recruit them. That is not a stale
   * read, it is a wrong answer to "did it work".
   *
   * Invalidating correctly needs the opt-in path in routes/leaderboard.js to
   * reach into the arena module — real coupling, bought for a load problem
   * nothing has demonstrated. The rate limiter is what was actually needed.
   */
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8'));

  assert.doesNotMatch(src, /cached\s*\(\s*['"]arena:/,
    'the leaderboard is memoised again — a trader who just opted in will be '
    + 'told they are not on the board for the length of the TTL');
  assert.match(src, /res\.json\(await computeLeaderboard\(\)\)/,
    'the leaderboard route no longer computes the board it serves');
});

// ── (1) the limiter is actually attached, to every public route ───────────

test('every public arena route is rate limited', () => {
  // Reachability, not shape. A limiter that exists and is wired to four of five
  // routes leaves the fifth as the one an abusive client finds — and a scan
  // that only checks "is rateLimit imported" would pass on exactly that.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8'));

  const PUBLIC = ['/leaderboard', '/tape', '/trader/:handle', '/season', '/seasons'];
  const missing = [];
  for (const r of PUBLIC) {
    // The GET handler for this path must name a limiter before its callback.
    const re = new RegExp(`router\\.get\\('${r.replace(/[/:]/g, '\\$&')}'\\s*,\\s*publicBoardLimit`);
    if (!re.test(src)) missing.push(r);
  }
  assert.deepEqual(missing, [],
    `these public routes answer strangers with no limiter: ${missing.join(', ')}`);
});

test('the limiter buckets by IP, not by user', () => {
  // userKey falls back to ipKey when there is no user, so bucketing these by
  // user would LOOK correct and silently be per-IP anyway — except it would
  // also share one bucket with the authenticated trade limiter. These routes
  // have no user by design; say so explicitly.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8'));
  assert.match(src, /publicBoardLimit\s*=\s*rateLimit\(\{[^}]*key:\s*ipKey/,
    'the public board limiter is not bucketed by IP');
});

test('the write routes keep their own tighter limit', () => {
  // The new limiter must not have replaced tradeLimit on the routes that act.
  // 120/min is right for reading a board and far too loose for opening trades.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8'));
  assert.match(src, /tradeLimit\s*=\s*rateLimit\(\{[^}]*max:\s*20/,
    'the trade limiter was loosened or removed');
  assert.match(src, /router\.post\('\/open',\s*authMiddleware,\s*tradeLimit/,
    'POST /open lost its trade limiter');
});

// ── the ranking itself is still computed from everybody ──────────────────

test('no LIMIT was added to the leaderboard inputs', () => {
  // The fix that would have been wrong. A capped input produces a ranking that
  // is confidently incorrect, and it would look exactly like a working board.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  const fn = src.slice(src.indexOf('async function computeLeaderboard'));
  const body = fn.slice(0, fn.indexOf('\n}\n'));
  assert.ok(!/LIMIT\s+\d+/i.test(body),
    'computeLeaderboard now caps its input — the ranking it returns is drawn '
    + 'from a truncated field and rank is no longer meaningful');
});
