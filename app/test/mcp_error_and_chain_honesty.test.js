'use strict';
/**
 * Two defects on the PUBLIC, unauthenticated /mcp surface.
 *
 * 1. RAW DRIVER TEXT. `routes/mcp.js` rendered a failing tool as
 *
 *        `Tool failed: ${String(e.message || e).slice(0, 200)}`
 *
 *    which is the exact expression `lib/safe_error.js` quotes in its own
 *    docblock as the bug it was written for. Its sibling `/api/tool/invoke`
 *    was wired to `safeErrorText`; `/mcp` never was. These handlers do
 *    `pool.execute` and `getGateway` calls, so a database error carries
 *    connection and schema detail and a gateway error carries the internal URL
 *    it tried. Truncating to 200 characters bounds the SIZE of a leak, not
 *    whether one happens.
 *
 * 2. AN INTEGRITY VERDICT INVENTED FROM NO DATA. `get_flight_record` returned
 *
 *        verified: chain.ok !== false
 *
 *    With no flight synced, `chain` is `{}`, `chain.ok` is `undefined`, and
 *    `undefined !== false` is **true**. The tamper-evident audit chain
 *    reported itself verified having read nothing at all — absent rendered as
 *    a positive verdict, on the integrity claim itself.
 *
 *    The two lines beside it already do it correctly: `entries: chain.length ??
 *    null` and `tip_hash: chain.tip_hash ?? null` both yield null when absent.
 *    Only the verdict turned an unknown into a yes.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

// Stub ./sync BEFORE mcp.js requires it, so getLatestFlight is ours.
let FLIGHT = null;
const syncPath = require.resolve('../routes/sync');
require.cache[syncPath] = {
  id: syncPath,
  filename: syncPath,
  loaded: true,
  exports: {
    getLatestFlight: async () => FLIGHT,
    router: require('express').Router(),
  },
};

const mcp = require('../routes/mcp');

// ── 2. the chain verdict ─────────────────────────────────────────────────────

async function chainOf(flight) {
  FLIGHT = flight;
  const out = await mcp.TOOLS.get_flight_record.handler({}, {});
  return out.chain;
}

test('no flight synced: the chain is UNKNOWN, never verified', async () => {
  const chain = await chainOf(null);
  assert.notEqual(chain.verified, true,
    'the tamper-evident chain reported itself verified having read nothing');
  assert.equal(chain.verified, null);
  assert.equal(chain.entries, null);
  assert.equal(chain.tip_hash, null);
});

test('a flight with no chain block is UNKNOWN too', async () => {
  const chain = await chainOf({ records: [] });
  assert.equal(chain.verified, null);
});

test('a chain that reports ok:true is verified', async () => {
  const chain = await chainOf({
    records: [], chain: { ok: true, length: 42, tip_hash: '0xabc' },
  });
  assert.equal(chain.verified, true);
  assert.equal(chain.entries, 42);
  assert.equal(chain.tip_hash, '0xabc');
});

test('a chain that reports ok:false is NOT verified — and that is not null', async () => {
  // The distinction that matters: a broken chain and an unread one must not
  // collapse into the same answer.
  const chain = await chainOf({ records: [], chain: { ok: false, length: 9 } });
  assert.equal(chain.verified, false);
});

test('a non-boolean ok is unknown, not truthy-coerced', async () => {
  for (const ok of ['yes', 1, {}, [], 'false']) {
    const chain = await chainOf({ records: [], chain: { ok } });
    assert.equal(chain.verified, null, `ok=${JSON.stringify(ok)}`);
  }
});

// ── 1. the error text ────────────────────────────────────────────────────────

/** Drive the JSON-RPC tools/call path with a handler that throws. */
async function callFailing(message) {
  const name = Object.keys(mcp.TOOLS)[0];
  const tool = mcp.TOOLS[name];
  const realHandler = tool.handler;
  const realSchema = tool.inputSchema;
  tool.handler = async () => { throw new Error(message); };
  tool.inputSchema = { type: 'object', properties: {} };
  try {
    const res = await mcp.handleRpc(
      { jsonrpc: '2.0', id: 1, method: 'tools/call',
        params: { name, arguments: {} } }, {});
    return JSON.stringify(res);
  } finally {
    tool.handler = realHandler;
    tool.inputSchema = realSchema;
  }
}

test('a database DSN in a driver error does not reach an anonymous caller', async () => {
  const body = await callFailing(
    'connect ECONNREFUSED mysql://runeclaw_app:sup3rs3cret@db.internal.prod:3306/runeclaw');
  assert.ok(!body.includes('sup3rs3cret'), 'the DB password went out on /mcp');
  assert.ok(!body.includes('db.internal.prod'), 'the internal DB host went out on /mcp');
  assert.match(body, /Tool failed/, 'the caller still learns the tool failed');
});

test('a filesystem path in an error does not reach an anonymous caller', async () => {
  const body = await callFailing('ENOENT: no such file, open /home/deploy/.env');
  assert.ok(!body.includes('/home/deploy/.env'), 'a deploy path went out on /mcp');
});

test('a real validation message still reaches the caller', async () => {
  // Scrubbed, not blanked — tools throw genuine errors a caller needs to see.
  const body = await callFailing('text is required');
  assert.match(body, /text is required/);
});

test('/mcp uses the same scrubber as its sibling endpoint', () => {
  // The sibling /api/tool/invoke was wired to safeErrorText and /mcp was not.
  // Pinned structurally because the property is "these two agree", which no
  // single response can demonstrate.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'mcp.js'), 'utf8');
  assert.match(src, /safeErrorText/,
    'mcp.js does not use the scrubber that exists for exactly this');
  assert.ok(!/String\(e\.message \|\| e\)\.slice\(0, 200\)/.test(src),
    'the raw render safe_error.js was written to replace is still here');
});
