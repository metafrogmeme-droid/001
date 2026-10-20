'use strict';
/**
 * The Flovia client (lib/flovia.js) answers read, or not read with a reason,
 * and never throws; the read cache (lib/flovia_reads.js) remembers a success
 * for its horizon and a failure, reason kept, for a short while.
 *
 * The transport is a stub: no network in a test, and the handshake and both
 * reply shapes (a JSON body, a text/event-stream body) are exercised here
 * because the live server may answer either.
 */
delete process.env.DATABASE_URL;
const test = require('node:test');
const assert = require('node:assert');
const flovia = require('../lib/flovia');
const reads = require('../lib/flovia_reads');

function reply(status, body, { contentType = 'application/json', sessionId } = {}) {
  const headers = new Map([['content-type', contentType]]);
  if (sessionId) headers.set('mcp-session-id', sessionId);
  return { status, ok: status < 400, headers: { get: (k) => headers.get(String(k).toLowerCase()) || null },
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) };
}

/** A stub server: initialize, initialized, then tools/call answered by `answer`. */
function stub(answer, { sessionId = 'sess-1', initStatus = 200 } = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    const msg = JSON.parse(init.body);
    calls.push({ url, headers: init.headers, msg });
    if (msg.method === 'initialize') {
      if (initStatus !== 200) return reply(initStatus, { error: 'unauthorized' });
      return reply(200, { jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2025-06-18', capabilities: {}, serverInfo: { name: 'flovia' } } }, { sessionId });
    }
    if (msg.method === 'notifications/initialized') return reply(202, '');
    return answer(msg, calls);
  };
  return { fetch, calls };
}

test.beforeEach(() => { process.env.FLOVIA_KEY = 'flv_test_key_not_real'; flovia.setFetch(null); });

test('unconfigured: no key, no call, the reason says so', async () => {
  delete process.env.FLOVIA_KEY;
  const s = stub(() => { throw new Error('must not be called'); });
  flovia.setFetch(s.fetch);
  assert.deepStrictEqual(await flovia.call('flovia_status', {}), { read: false, reason: 'unconfigured' });
  assert.equal(s.calls.length, 0);
});

test('a JSON reply: handshake once, bearer key and session on every call, the tool text parsed', async () => {
  const s = stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: JSON.stringify({ five_hour: { used_pct: 3 } }) }] } }));
  flovia.setFetch(s.fetch);
  const a = await flovia.call('flovia_status', {});
  assert.deepStrictEqual(a, { read: true, data: { five_hour: { used_pct: 3 } } });
  const b = await flovia.call('flovia_status', {});
  assert.equal(b.read, true);
  const methods = s.calls.map((c) => c.msg.method);
  assert.deepStrictEqual(methods, ['initialize', 'notifications/initialized', 'tools/call', 'tools/call'], 'one handshake per process');
  for (const c of s.calls) {
    assert.equal(c.headers.authorization, 'Bearer flv_test_key_not_real');
    assert.match(c.url, /^https:\/\/api\.flovia\.dev\/mcp$/);
  }
  assert.equal(s.calls[2].headers['mcp-session-id'], 'sess-1', 'the session the server issued travels back');
  assert.deepStrictEqual(s.calls[2].msg.params, { name: 'flovia_status', arguments: {} });
});

test('an event-stream reply is read the same way', async () => {
  const s = stub((msg) => reply(200,
    `event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n`
    + `event: message\ndata: ${JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: '{"ok":1}' }] } })}\n\n`,
    { contentType: 'text/event-stream' }));
  flovia.setFetch(s.fetch);
  assert.deepStrictEqual(await flovia.call('flovia_coingecko', { path: '/global' }), { read: true, data: { ok: 1 } });
});

test('a refused key, a dead server and a tool error are three different absences', async () => {
  flovia.setFetch(stub(() => null, { initStatus: 401 }).fetch);
  let r = await flovia.call('flovia_status', {});
  assert.equal(r.read, false); assert.equal(r.reason, 'refused');

  flovia.setFetch(stub((msg) => reply(503, 'down')).fetch);
  r = await flovia.call('flovia_status', {});
  assert.equal(r.read, false); assert.equal(r.reason, 'unreachable');

  flovia.setFetch(stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ explanation: { meaning: 'your free window is used up until 13:00' } }) }] } })).fetch);
  r = await flovia.call('flovia_nansen', {});
  assert.equal(r.read, false); assert.equal(r.reason, 'refused');
  assert.match(r.detail, /free window/);
  assert.ok(!('status' in r) && !('executed' in r), "Flovia's own refusal names no provider answer");

  // A provider's HTTP answer, delivered as a tool error: the status, the
  // code and who answered ride with it, so a reader can tell CoinGecko's
  // "not found" from a refusal to look.
  flovia.setFetch(stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, status: 404, response: '{"status":{"error_code":404,"error_message":"Not found"}}', explanation: { by: 'coingecko', code: 'http_404', executed: 'yes', meaning: 'CoinGecko answered HTTP 404; see response.' } }) }] } })).fetch);
  r = await flovia.call('flovia_coingecko', {});
  assert.deepStrictEqual([r.read, r.reason, r.status, r.code, r.by, r.executed], [false, 'refused', 404, 'http_404', 'coingecko', true]);
  assert.match(r.detail, /HTTP 404/);
  // "executed: no" is carried as false, never as a provider's answer.
  flovia.setFetch(stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, status: 404, explanation: { by: 'coingecko', code: 'not_sent', executed: 'no', meaning: 'not sent' } }) }] } })).fetch);
  r = await flovia.call('flovia_coingecko', {});
  assert.deepStrictEqual([r.read, r.reason, r.status, r.executed], [false, 'refused', 404, false]);

  // Flovia's own structured refusal, delivered as a normal result.
  flovia.setFetch(stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: JSON.stringify({ explanation: { executed: false, meaning: 'too large' } }) }] } })).fetch);
  r = await flovia.call('flovia_nansen', {});
  assert.deepStrictEqual([r.read, r.reason, r.detail], [false, 'refused', 'too large']);

  flovia.setFetch(stub(() => { const e = new Error('aborted'); e.name = 'TimeoutError'; throw e; }).fetch);
  r = await flovia.call('flovia_status', {});
  assert.deepStrictEqual([r.read, r.reason, r.detail], [false, 'unreachable', 'TimeoutError']);

  flovia.setFetch(stub((msg) => reply(200, 'not json at all')).fetch);
  r = await flovia.call('flovia_status', {});
  assert.equal(r.reason, 'malformed');
});

test('a forgotten session is reopened once, then the answer stands', async () => {
  let lost = true;
  const s = stub((msg, calls) => {
    if (lost) { lost = false; return reply(404, { error: 'session not found' }); }
    return reply(200, { jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: '{"after":"reopen"}' }] } });
  });
  flovia.setFetch(s.fetch);
  assert.deepStrictEqual(await flovia.call('flovia_status', {}), { read: true, data: { after: 'reopen' } });
  const methods = s.calls.map((c) => c.msg.method);
  assert.deepStrictEqual(methods, ['initialize', 'notifications/initialized', 'tools/call', 'initialize', 'notifications/initialized', 'tools/call']);
});

test('the key never appears in a result', async () => {
  flovia.setFetch(stub((msg) => reply(200, { jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: 'bad' } })).fetch);
  const r = await flovia.call('flovia_status', {});
  assert.equal(r.read, false);
  assert.doesNotMatch(JSON.stringify(r), /flv_test_key_not_real/);
});

test('the cache remembers a success for its horizon and a failure with its reason, and peek never produces', async () => {
  let produced = 0;
  const once = async () => { produced++; return { read: true, data: { n: produced } }; };
  const a = await reads.remember('t:one', 60_000, once);
  const b = await reads.remember('t:one', 60_000, once);
  assert.equal(produced, 1, 'the second read came from the table');
  assert.deepStrictEqual([a.read, a.data, b.read, b.data, b.remembered], [true, { n: 1 }, true, { n: 1 }, true]);

  let failed = 0;
  const failing = async () => { failed++; return { read: false, reason: 'refused', detail: 'window spent' }; };
  const f1 = await reads.remember('t:fail', 60_000, failing);
  const f2 = await reads.remember('t:fail', 60_000, failing);
  assert.equal(failed, 1, 'a refusal is not re-asked at once');
  assert.deepStrictEqual([f2.read, f2.reason, f2.detail], [false, 'refused', 'window spent']);
  assert.equal(f1.reason, f2.reason);

  let unconf = 0;
  const unconfigured = async () => { unconf++; return { read: false, reason: 'unconfigured' }; };
  await reads.remember('t:unconf', 60_000, unconfigured);
  await reads.remember('t:unconf', 60_000, unconfigured);
  assert.equal(unconf, 2, 'unconfigured is a fact about this deployment, never stored');

  assert.equal(await reads.peek('t:never'), null);
  const thrower = async () => { throw new TypeError('boom'); };
  const t = await reads.remember('t:throw', 60_000, thrower);
  assert.deepStrictEqual([t.read, t.reason, t.detail], [false, 'unreachable', 'TypeError']);

  // An expired row reads as absent and the producer runs again.
  const short = await reads.remember('t:short', 1000, once);
  assert.equal(short.data.n, 2);
  const { pool } = require('../db');
  const row = pool.floviaReads.get('t:short');
  row.expires_at = new Date(Date.now() - 1);
  const again = await reads.remember('t:short', 1000, once);
  assert.equal(again.data.n, 3, 'past its horizon the read is made again');
  await reads.sweep();
  assert.ok(!pool.floviaReads.has('t:short') || pool.floviaReads.get('t:short').expires_at > new Date());
});
