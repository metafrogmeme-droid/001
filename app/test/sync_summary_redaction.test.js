'use strict';
/**
 * The operator's equity was served to anyone who asked.
 *
 * THE DEFECT
 *
 * `GET /api/bot/sync/portfolio-summary` returned `equity` — the account
 * balance — and `net_pnl`, cumulative dollar P&L. Its DB fallback reads
 * `equity_snapshots` and `SUM(pnl) FROM trades` with no user scoping at all.
 *
 * `sync.js` DOES authenticate, via `router.use(botAuth)` on line ~160. This
 * route is declared on line 76. In Express `router.use(mw)` applies only to
 * routes declared AFTER it, so the middleware never ran for it — inside a
 * module whose header says "Authenticated via a shared secret".
 *
 * The comment on the route said "no auth required — shows synced data". That
 * describes where the numbers came from, not whether they are safe to publish,
 * and it is the whole reason this survived review.
 *
 * It is the §4 line the repo draws everywhere else: `sanitizeRecord`'s
 * dollar-key list names `equity` explicitly, `public_flight.js` "strips every
 * dollar figure", `/api/guardian/incidents` promises "percent/flags only", and
 * `public_letter.js` publishes equity as a PERCENT change. One endpoint served
 * the raw figure — and nothing in app/ or bot/ calls it, so it was exposure
 * with no consumer.
 *
 * HOW IT WAS FOUND
 *
 * Not by reading sync.js. By asking a different question of the linter: the
 * module-level Express rule shipped hours earlier marks a file guarded if
 * `authMiddleware` appears anywhere in it, which cannot distinguish "every
 * route authed" from "one authed, fourteen public". Making that rule
 * route-granular surfaced fifteen such routes across app/routes — all
 * deliberate — and the same question asked of sync.js (which uses its own
 * `botAuth`, so neither rule examines it) surfaced these two.
 *
 * THE SECOND HALF: THE SCRUBBER LET `net_pnl` THROUGH
 *
 * `scrub()` promises in its own comment that a new dollar field is "redacted by
 * default, not leaked". Its bare-name list held `pnl_abs`, so `pnl`, `net_pnl`,
 * `daily_pnl`, `realized_pnl` and `total_pnl` all passed through — only the
 * `_usd` suffix caught anything. Broadened to `pnl`, with a ratio allowlist so
 * `pnl_pct` still survives; percent and R-multiple outcomes are the entire
 * content of the public flight record, and stripping them would have "fixed"
 * the leak by deleting the feature.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const jwt = require('jsonwebtoken');

const { scrub, DOLLAR_KEY } = require('../lib/flight');
const syncSrc = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');

const SUMMARY = {
  equity: 10432.55, open_count: 3, net_pnl: 812.4, total_trades: 47,
  win_rate: 61.7, mode: 'LIVE', live_unavailable: false, updated_at: 'now',
};

// ── the scrubber ──────────────────────────────────────────────────────────

test('every bare pnl name is stripped, not just the _usd ones', () => {
  // The exact set that used to survive. `pnl_abs` was the only bare name
  // listed, so five real dollar fields walked past a control whose stated
  // design goal is redaction by default.
  const out = scrub({ pnl: 1, net_pnl: 2, daily_pnl: 3, realized_pnl: 4, total_pnl: 5, pnl_usd: 6 });
  assert.deepEqual(Object.keys(out), [],
    `these dollar fields survived the scrubber: ${Object.keys(out).join(', ')}`);
});

test('ratio keys survive — the public record is made of them', () => {
  // Stripping these would "fix" the leak by deleting the feature: percent and
  // R-multiple outcomes are the entire content of the public flight view.
  const kept = scrub({ pnl_pct: 1, change_pct: 2, win_rate: 3, alpha_pct: 4,
                       avg_bps: 5, r_multiple: 6, funding_rate: 7, open_count: 8 });
  assert.deepEqual(Object.keys(kept).sort(),
    ['alpha_pct', 'avg_bps', 'change_pct', 'funding_rate', 'open_count',
     'pnl_pct', 'r_multiple', 'win_rate']);
});

test('the ratio allowlist never rescues a currency key', () => {
  // Suffix-anchored, and a key naming a currency is exempt from the exemption —
  // otherwise the allowlist becomes the new hole.
  const out = scrub({ usd_ratio: 1, equity_pct: 2, cash_rate: 3, balance_bps: 4, pct_pnl_usd: 5 });
  assert.deepEqual(Object.keys(out), [],
    `the ratio allowlist let a currency field through: ${Object.keys(out).join(', ')}`);
});

test('DOLLAR_KEY still matches everything it used to', () => {
  // Broadening must not have narrowed anything. Guards against a regex edit
  // that trades one leak for another.
  for (const k of ['equity', 'balance', 'notional', 'margin', 'collateral',
                   'account_value', 'cash', 'funds', 'wallet_value', 'amount_usd']) {
    assert.ok(DOLLAR_KEY.test(k), `${k} is no longer treated as a dollar key`);
  }
});

// ── the endpoint ──────────────────────────────────────────────────────────

test('an anonymous summary carries no dollar amount', () => {
  const anon = scrub(SUMMARY);
  assert.equal(anon.equity, undefined, 'equity served to an anonymous caller');
  assert.equal(anon.net_pnl, undefined, 'net_pnl served to an anonymous caller');
  // ...and stays useful: a summary without counts is not a summary.
  assert.equal(anon.open_count, 3);
  assert.equal(anon.total_trades, 47);
  assert.equal(anon.win_rate, 61.7);
  assert.equal(anon.mode, 'LIVE');
});

test('the route identifies the caller and redacts on EVERY return path', () => {
  // Three of them: in-memory cache, circuit-breaker derivation, DB fallback.
  // The defect was one route missing what its siblings had; one path missing
  // what its siblings have is the same bug one level down.
  assert.match(syncSrc, /router\.get\('\/portfolio-summary', optionalAuth,/,
    'portfolio-summary no longer identifies the caller');
  const start = syncSrc.indexOf("router.get('/portfolio-summary'");
  const end = syncSrc.indexOf('// Auth middleware for bot sync', start);
  const handler = syncSrc.slice(start, end);
  const returns = (handler.match(/res\.json\(\{\s*portfolio:/g) || []).length;
  const redacted = (handler.match(/summaryFor\(req,/g) || []).length;
  const nulls = (handler.match(/res\.json\(\{ portfolio: null \}\)/g) || []).length;
  assert.ok(returns >= 4, `expected 4+ portfolio responses, found ${returns}`);
  assert.equal(redacted + nulls, returns,
    `${returns} response(s) but only ${redacted} redacted + ${nulls} empty — one serves raw`);
});

test('the redaction reuses the shared scrubber, not a hand-written key list', () => {
  // A local list of keys to drop would go stale the first time a field is added
  // to the summary. Reuse is what makes "redacted by default" true here too.
  assert.match(syncSrc, /require\('\.\.\/lib\/flight'\)/,
    'sync.js no longer uses the shared scrubber');
});

test('the misleading comment is gone', () => {
  assert.doesNotMatch(syncSrc, /no auth required — shows synced data/,
    'the comment that justified serving equity to anyone is back');
});

test('/scan stays public — the fix must not over-correct', () => {
  // Public market info, documented as such, and the dashboard depends on it.
  // Gating it would break the panel to solve a problem it does not have.
  assert.match(syncSrc, /router\.get\('\/scan', async/,
    '/scan gained a guard it does not need');
});

test('THE REAL summaryFor redacts for anonymous and not for authed', () => {
  // Calls the shipped function. The first version of this test reimplemented
  // `req.user ? s : scrub(s)` inline and passed with summaryFor reverted to
  // returning the raw summary — it proved my arithmetic, not the code's.
  const { summaryFor } = require('../routes/sync');
  const authed = summaryFor({ user: { user_id: 1 } }, SUMMARY);
  assert.equal(authed.equity, 10432.55, 'the authed view lost its equity');
  assert.equal(authed.net_pnl, 812.4);
  assert.equal(authed.disclosure, undefined, 'no anonymous disclosure on an authed response');

  const anon = summaryFor({}, SUMMARY);
  assert.equal(anon.equity, undefined, 'summaryFor served equity to an anonymous caller');
  assert.equal(anon.net_pnl, undefined, 'summaryFor served net_pnl to an anonymous caller');
  assert.equal(anon.open_count, 3, 'the anonymous view lost its counts');
  assert.match(anon.disclosure, /no dollar amounts/);

  // A null summary stays null rather than becoming an object of disclosure.
  assert.equal(summaryFor({}, null), null);
});
