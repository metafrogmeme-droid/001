'use strict';
/**
 * The endpoint documented as "the authed one" had no auth.
 *
 * THE DEFECT
 *
 * `public_flight.js` states the boundary in its own header:
 *
 *     §4-safe by construction: every record is passed through sanitizeRecord(),
 *     which strips every dollar figure … The authed /api/guardian/flight keeps
 *     the full (dollar-carrying) record.
 *
 * That sentence described a boundary that did not exist. `routes/guardian.js`
 * never applied `authMiddleware` — while its two siblings,
 * `guardian_readiness.js` and `guardian_review.js`, both do — so an anonymous
 * GET to /api/guardian/flight returned records straight out of
 * `outcome_event_payload`: `pnl_usd`, `entry_price`, `exit_price`, plus any
 * `*_usd` field inside `idea`/`risk`.
 *
 * So the redaction existed, worked, and was applied on exactly one of the two
 * routes that serve the same records. The endpoint named in the comment as the
 * protected one was the way around it.
 *
 * /incidents had the same shape one level down: its own docstring promises
 * "Percent/flags only, no dollar amounts", and nothing enforced that. The
 * incidents array arrives unscrubbed from the bot, and the derived fallback
 * copies `risk.reason` and `checks_failed[0]` verbatim — risk reasons routinely
 * name a dollar cap.
 *
 * HOW IT WAS FOUND
 *
 * A guard_lint rule written the same afternoon. `app/` is a second HTTP
 * surface — 228 Express routes, larger than the aiohttp gateway — and no
 * structural check had ever looked at it. The rule flagged two modules; this
 * was one of them.
 *
 * WHY NOT JUST ADD authMiddleware
 *
 * `dashboard.js` fetches both /flight and /incidents with `auth: false`, so a
 * 401 would break two panels for every visitor. The endpoint must stay
 * anonymously reachable; what had to change is what an anonymous caller GETS.
 * `optionalAuth` identifies without refusing, and every payload is scrubbed
 * unless `req.user` is set — which finally makes public_flight.js's sentence
 * true rather than aspirational.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const jwt = require('jsonwebtoken');

const ROUTES = path.join(__dirname, '..', 'routes');
const guardianSrc = fs.readFileSync(path.join(ROUTES, 'guardian.js'), 'utf8');

// A record shaped exactly like assemble_flight_records() emits, with the dollar
// fields outcome_event_payload() really carries.
const RECORD = {
  decision_id: 'T-1', symbol: 'SOL/USDT', timestamp: '2026-07-27T10:00:00Z',
  outcome: 'APPROVED', is_paper: false,
  idea: { entry_price: 71.4, stop_loss: 70.0, position_size_usd: 500 },
  risk: { verdict: 'APPROVED', reason: 'sized to $500 of 10000 equity', notional_usd: 500 },
  result: { pnl_usd: 12.5, exit_price: 76.4, entry_price: 71.4, close_reason: 'tp' },
  chain: { sequence: 7, entry_hash: 'a'.repeat(64), prev_hash: 'b'.repeat(64) },
};

const FLIGHT = {
  records: [RECORD],
  chain: { verified: true },
  policy: { max_notional_usd: 1000, max_loss_pct: 2 },
  incidents: [{ id: 'i1', kind: 'block', category: 'Risk-gate rejection', severity: 'high',
                symbol: 'SOL/USDT', detail: 'notional $500 over the $250 cap',
                blocked_usd: 500 }],
  guardian_status: 'ok',
  updated_at: '2026-07-27T10:00:01Z',
};

/** Serve routes/guardian.js with ./sync stubbed to return FLIGHT. */
function makeServer() {
  const syncPath = require.resolve(path.join(ROUTES, 'sync.js'));
  require.cache[syncPath] = { id: syncPath, filename: syncPath, loaded: true,
                              exports: { getLatestFlight: async () => FLIGHT } };
  delete require.cache[require.resolve(path.join(ROUTES, 'guardian.js'))];
  const router = require(path.join(ROUTES, 'guardian.js'));
  const app = express();
  app.use('/api/guardian', router);
  return http.createServer(app);
}

function get(server, url, token) {
  return new Promise((resolve, reject) => {
    const { port } = server.address();
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    http.get({ port, path: url, headers }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }));
    }).on('error', reject);
  });
}

/** Every dollar-ish string found anywhere in a JSON payload. */
function dollarLeaks(value, trail = '$') {
  const DOLLAR_KEY = /(usd|equity|balance|notional|margin|collateral|dollars?|account_value|pnl_abs|cash|funds|wallet_value)/i;
  const out = [];
  if (value == null) return out;
  if (Array.isArray(value)) {
    value.forEach((v, i) => out.push(...dollarLeaks(v, `${trail}[${i}]`)));
    return out;
  }
  if (typeof value === 'object') {
    for (const k of Object.keys(value)) {
      if (DOLLAR_KEY.test(k)) out.push(`${trail}.${k} = ${JSON.stringify(value[k])}`);
      out.push(...dollarLeaks(value[k], `${trail}.${k}`));
    }
    return out;
  }
  if (typeof value === 'string' && /\$\s?-?\d/.test(value)) out.push(`${trail} = ${JSON.stringify(value)}`);
  return out;
}

async function withServer(fn) {
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  try { await fn(server); } finally { server.close(); }
}

test('the fixture itself carries dollars — otherwise every test below is vacuous', () => {
  const leaks = dollarLeaks(FLIGHT);
  assert.ok(leaks.length >= 6,
    `the fixture only has ${leaks.length} dollar fields; it no longer reproduces the record ` +
    'shape outcome_event_payload emits, so a passing redaction test would prove nothing');
});

test('ANONYMOUS /flight leaks no dollar figure', async () => {
  await withServer(async (server) => {
    const { status, body } = await get(server, '/api/guardian/flight');
    assert.equal(status, 200, 'the endpoint must stay reachable — dashboard.js uses auth:false');
    const leaks = dollarLeaks(body);
    assert.deepEqual(leaks, [], `anonymous /flight leaked:\n  ${leaks.join('\n  ')}`);
    assert.equal(body.records.length, 1, 'redaction must not drop the record');
    assert.equal(body.records[0].decision_id, 'T-1');
    assert.match(body.disclosure, /no dollar amounts/);
  });
});

test('ANONYMOUS /flight/:id and /incidents leak no dollar figure either', async () => {
  await withServer(async (server) => {
    for (const url of ['/api/guardian/flight/T-1', '/api/guardian/incidents']) {
      const { status, body } = await get(server, url);
      assert.equal(status, 200, `${url} must stay reachable`);
      const leaks = dollarLeaks(body);
      assert.deepEqual(leaks, [], `anonymous ${url} leaked:\n  ${leaks.join('\n  ')}`);
    }
  });
});

test('the free-text dollar inside a risk reason is scrubbed, not just the keys', async () => {
  // "sized to $500 of 10000 equity" carries the figure in prose. A key-name
  // filter alone would pass it through, which is why scrub() also rewrites
  // strings — pinned here because that is the half people forget.
  await withServer(async (server) => {
    const { body } = await get(server, '/api/guardian/flight/T-1');
    assert.doesNotMatch(JSON.stringify(body), /\$\s?\d/);
  });
});

test('an AUTHENTICATED caller still gets the full record', async () => {
  // The other half of the boundary. Redacting for everyone would "fix" the leak
  // by deleting the feature, and would quietly make public_flight.js's header
  // false in the opposite direction.
  await withServer(async (server) => {
    const token = jwt.sign({ user_id: 1, email: 'a@b.c' }, process.env.JWT_SECRET);
    const { status, body } = await get(server, '/api/guardian/flight', token);
    assert.equal(status, 200);
    assert.equal(body.records[0].result.pnl_usd, 12.5, 'the authed view lost its dollar figures');
    assert.equal(body.disclosure, undefined, 'no anonymous disclosure on an authed response');
  });
});

test('an INVALID token degrades to the public view, it does not 401', async () => {
  // optionalAuth identifies, it never refuses. An expired session on a page
  // that also serves the public should show the public view.
  await withServer(async (server) => {
    const { status, body } = await get(server, '/api/guardian/flight', 'not-a-jwt');
    assert.equal(status, 200);
    assert.deepEqual(dollarLeaks(body), []);
  });
});

test('the chain check still reports a verified window after redaction', async () => {
  // A regression check on the OUTPUT, and that is all it is. Mutation-testing
  // this honestly: swapping `inspectWindow(records)` for
  // `inspectWindow(records.map(publicSafe))` does not fail, because scrub()
  // provably never touches `chain.sequence` or `chain.entry_hash` — no dollar
  // key matches them. The two forms are equivalent today, so no behavioural
  // test can distinguish them, and claiming this one does would be exactly the
  // overstatement this audit keeps finding. The ordering is pinned at the
  // source level in the test below instead.
  await withServer(async (server) => {
    const { body } = await get(server, '/api/guardian/flight');
    assert.equal(body.window.records_checked, 1);
    assert.equal(body.window.well_formed_hashes, 1);
  });
});

test('the window is derived from the raw records, at the source', () => {
  // Why it matters even though it is currently equivalent: the scrubber's key
  // test is deliberately BROAD ("anything with usd, plus a handful of bare
  // names") so a new dollar field is redacted by default. A future chain field
  // — `cash_hash`, say, or anything the regex happens to match — would start
  // being stripped, and a window derived from the scrubbed copy would then
  // report a healthy chain over records it could no longer check. Pinning the
  // argument keeps the verification upstream of the redaction rather than
  // downstream of it.
  assert.match(guardianSrc, /window: inspectWindow\(records\)/,
    'the chain window is no longer derived from the unredacted records');
});

test('every handler in this router goes through the one redaction helper', () => {
  // The defect was one route missing what a sibling had. A single `publicSafe`
  // used by every handler is what stops the next endpoint added here from
  // repeating it — so the count is pinned rather than trusted.
  const handlers = (guardianSrc.match(/router\.get\(/g) || []).length;
  const guarded = (guardianSrc.match(/publicSafe\(req/g) || []).length;
  assert.ok(handlers >= 3, 'fewer handlers than expected — re-derive this check');
  assert.ok(guarded >= handlers,
    `${handlers} handler(s) but only ${guarded} publicSafe() call(s) — one serves raw records`);
  assert.match(guardianSrc, /router\.use\(optionalAuth\)/,
    'the router no longer identifies the caller, so publicSafe would redact for everyone');
});
