'use strict';
/**
 * Every request limit names its own shared bucket, once.
 *
 * Since 2026-10-11 the per-route limits (lib/rate_limit.js) count in one table
 * shared by every web instance, keyed by the limiter's `name`. Two call sites
 * under one name would silently share a budget; a limiter built inside a
 * handler would start a fresh window on every request. Both are measured here
 * from two readings that must agree: the limiters the route files construct
 * when loaded, and the constructor calls their source contains.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');
const { routes } = require('./helpers/public_routes');

const ROUTES = path.join(__dirname, '..', 'routes');

test('every limiter the routes construct has a name of its own', () => {
  routes(); // loads every router file once, as the server does
  const names = require('../lib/rate_limit')._names();
  const shared = [...names].filter(([, n]) => n !== 1);
  assert.deepStrictEqual(shared, [], `names constructed more than once: ${JSON.stringify(shared)}`);

  // The construct-time count and the source count are one quantity read twice.
  let calls = 0;
  for (const f of fs.readdirSync(ROUTES).filter((x) => x.endsWith('.js'))) {
    const src = codeOnly(fs.readFileSync(path.join(ROUTES, f), 'utf8'));
    calls += (src.match(/\b(?:rateLimit|slidingWindow)\(\s*\{/g) || []).length;
  }
  assert.strictEqual(names.size, calls,
    `${calls} limiter calls in routes/, ${names.size} constructed at load: one is built lazily or in a loop`);

  // The five hand-rolled per-IP windows and the MCP chat window are on the shared count.
  for (const n of ['insight', 'lab', 'macro', 'market', 'patterns', 'mcp', 'mcp.ask']) {
    assert.ok(names.has(n), `no limiter named ${n}`);
  }
});
