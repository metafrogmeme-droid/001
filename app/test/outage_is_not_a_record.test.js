'use strict';
/**
 * Four read failures that rendered as measurements.
 *
 * `GET /api/signals/stats` was fixed to 503 on a DB failure, with a comment
 * explaining that an outage is not a record of zero. The three endpoints
 * around it — two in the SAME FILE — kept failing soft:
 *
 *     GET /api/signals            catch → res.json({ signals: [] })
 *     GET /api/signals/analytics  catch → res.json(EMPTY_ANALYTICS)
 *     GET /api/feed/recent        catch → res.json({ events: [] })
 *     GET /api/bot/sync/portfolio-summary
 *                                 catch → res.json({ portfolio: null })
 *
 * Each is HTTP 200, so `mustRead()` passes and the panel renders its EMPTY
 * state — sentences that are claims about the world:
 *
 *   "No signals yet. They stream in as the engine scans the market."
 *   "0% win rate over 0 resolved"           (EMPTY_ANALYTICS.overall)
 *   the agent mind-stream, showing nothing
 *   "no portfolio yet"                      (byte-identical to the real one)
 *
 * The last is the worst of the four: its catch returned the SAME BYTES as the
 * genuine cold-start branch twelve lines above it, and logged nothing at all,
 * so there was no way to tell the two apart from either end.
 *
 * These assertions drive the failure rather than matching the source, because
 * the defect was never the spelling of the catch — it was the status code the
 * caller sees.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');

/** Mount one route module over a pool whose every query throws. */
function deadServer(mount, routeFile) {
  const pool = {
    execute: async () => { throw new Error('ER_LOCK_WAIT_TIMEOUT: deadlock'); },
    query: async () => { throw new Error('ER_LOCK_WAIT_TIMEOUT: deadlock'); },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  const authPath = require.resolve(path.join(APP, 'auth.js'));
  const pass = (req, _res, next) => { req.user = { user_id: 7 }; next(); };
  require.cache[authPath] = { id: authPath, filename: authPath, loaded: true,
    exports: { authMiddleware: pass, optionalAuth: pass } };
  delete require.cache[require.resolve(path.join(APP, 'routes', routeFile))];
  const app = express();
  app.use(express.json());
  app.use(mount, require(path.join(APP, 'routes', routeFile)));
  return http.createServer(app);
}

function get(mount, routeFile, urlPath) {
  return new Promise((resolve, reject) => {
    const s = deadServer(mount, routeFile);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: urlPath }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          let body = {};
          try { body = JSON.parse(b || '{}'); } catch (e) { /* non-JSON */ }
          resolve({ status: res.statusCode, body, raw: b });
        });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

// Silence the console.error each handler now emits — the log is asserted
// separately below, and 4 stack traces make a passing run look broken.
let logged = [];
const realError = console.error;
test.before(() => { console.error = (...a) => { logged.push(a.join(' ')); }; });
test.after(() => { console.error = realError; });

const CASES = [
  { name: 'the signal stream',
    mount: '/api/signals', file: 'signals.js', url: '/api/signals?limit=40',
    code: 'signal_stream_unavailable',
    lie: 'signals', lieText: '"No signals yet" — a claim about the market' },
  { name: 'signal analytics',
    mount: '/api/signals', file: 'signals.js', url: '/api/signals/analytics',
    code: 'signal_analytics_unavailable',
    lie: 'overall', lieText: 'a measured 0% win rate over 0 resolved' },
  { name: 'the agent mind-stream',
    mount: '/api/feed', file: 'feed.js', url: '/api/feed/recent?limit=8',
    code: 'feed_unavailable',
    lie: 'events', lieText: 'an agent that has thought nothing' },
  { name: 'the portfolio summary',
    mount: '/api/bot/sync', file: 'sync.js', url: '/api/bot/sync/portfolio-summary',
    code: 'portfolio_summary_unavailable',
    lie: 'portfolio', lieText: '"no portfolio yet", byte-identical to the real one' },
  // ── found by re-running the search AFTER the four above were fixed ───────
  // Neither was named by the audit. Both are the same defect, and reports.js
  // is M13 exactly: a catch returning the same bytes as the honest-empty
  // branch directly above it.
  { name: 'the intelligence reports',
    mount: '/api/reports', file: 'reports.js', url: '/api/reports',
    code: 'reports_unavailable',
    lie: 'reports', lieText: 'an hourly scan that found nothing' },
  { name: 'the copy-follow list',
    mount: '/api/copy', file: 'copy.js', url: '/api/copy',
    code: 'copy_list_unavailable',
    lie: 'following', lieText: 'an account that follows nobody' },
];

for (const c of CASES) {
  test(`${c.name}: a failed read is a 503, not ${c.lieText}`, async () => {
    const { status, body } = await get(c.mount, c.file, c.url);
    assert.strictEqual(status, 503,
      `${c.url} returned ${status} — the caller cannot tell an outage from data`);
    assert.strictEqual(body.error, c.code,
      'the failure must be nameable by the caller, from a fixed vocabulary');
    assert.ok(!(c.lie in body),
      `the error payload still carries a "${c.lie}" figure to render`);
  });
}

test('every error code is coarse and leaks no driver detail', async () => {
  // Same rule /readyz follows: a fixed vocabulary, never the driver message.
  for (const c of CASES) {
    const { body } = await get(c.mount, c.file, c.url);
    assert.ok(!/ER_LOCK_WAIT|deadlock|mysql|Error:|at Object/i.test(JSON.stringify(body)),
      `${c.url} leaked driver detail into the response`);
  }
});

test('the portfolio summary leaves a trace to find the outage by', async () => {
  // It swallowed silently: no console.error, unlike every sibling catch in
  // sync.js. An operator watching a "no portfolio yet" dashboard had nothing
  // in the log to contradict it.
  logged = [];
  await get('/api/bot/sync', 'sync.js', '/api/bot/sync/portfolio-summary');
  assert.ok(logged.some((l) => /Portfolio summary error/.test(l)),
    'the catch logged nothing at all');
  assert.ok(logged.some((l) => /ER_LOCK_WAIT|deadlock/.test(l)),
    'the log must carry the driver detail the RESPONSE must not');
});
