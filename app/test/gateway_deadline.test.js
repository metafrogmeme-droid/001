'use strict';

/**
 * A bound that only fires on one failure mode is not a bound.
 *
 * `timeout:` on http.request is an INACTIVITY timeout. Node arms it on the
 * socket and every byte that arrives resets it. So a gateway that sends its
 * headers and then trickles — or a proxy that keeps the connection warm — can
 * hold a request open forever while never once being idle long enough to
 * trip. Callers pass 20000 and read it as "at most 20 seconds". It was not.
 *
 * That matters here specifically because there is a CDN in front of this app
 * with its own patience. Any time our budget can exceed the edge's, the
 * visitor stops getting our honest {"error": ...} and starts getting the
 * edge's opaque 502 — which is how tonight's chat outage presented, and why
 * "cannot reach the bot" was investigated for hours as "the web app is down".
 *
 * Same shape as the engine's hung analyze phase earlier the same day:
 * retrying, catching and resetting all share the property that they only help
 * if the thing can finish. The inactivity timeout stays — it still catches a
 * dead socket sooner — and an ABSOLUTE deadline now sits over it.
 *
 * Second defect, same file: the connect fast-fail was added to requestJSON
 * only, so requestBinary still carried the exact bug that change existed to
 * kill. Two copies of a request path is how one of them stays broken. Both
 * now share armTimers.
 */

const test = require('node:test');
const assert = require('node:assert');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'lib', 'gateway.js'), 'utf8');

function loadGateway(env) {
  for (const [k, v] of Object.entries(env)) process.env[k] = v;
  delete require.cache[require.resolve('../lib/gateway')];
  return require('../lib/gateway');
}

/**
 * A server that answers with headers and then DRIPS body bytes forever.
 * Never idle, so the socket-inactivity timeout never fires. This is the
 * failure the deadline exists for.
 */
function dripServer(intervalMs = 100) {
  const timers = new Set();
  const srv = net.createServer((sock) => {
    sock.on('data', () => {
      // Chunked encoding, so there is no Content-Length to end on.
      sock.write('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
        + 'Transfer-Encoding: chunked\r\n\r\n');
      const h = setInterval(() => {
        try { sock.write('1\r\n \r\n'); } catch { /* closed */ }
      }, intervalMs);
      timers.add(h);
      sock.on('close', () => { clearInterval(h); timers.delete(h); });
    });
    sock.on('error', () => {});
  });
  srv.stopAll = () => { for (const h of timers) clearInterval(h); };
  return srv;
}

// ── behaviour ─────────────────────────────────────────────────────────────

test('a trickling gateway is cut at the deadline, not held forever',
  { timeout: 30000 }, async (t) => {
    const srv = dripServer(100);
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    t.after(() => { srv.stopAll(); srv.close(); });

    const g = loadGateway({
      BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '2000',
    });

    const t0 = Date.now();
    await assert.rejects(
      () => g.postGateway('/chat', { text: 'hi' }, 1200),
      (e) => e.code === 'GATEWAY_DEADLINE',
      'a socket that is never idle must still hit an absolute cap');
    const took = Date.now() - t0;
    // The drip is every 100ms, so the inactivity timeout would never fire.
    assert.ok(took < 4000, `must end near the 1200ms deadline — took ${took}ms`);
  });

test('the binary path is bounded the same way', { timeout: 30000 }, async (t) => {
  const srv = dripServer(100);
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  t.after(() => { srv.stopAll(); srv.close(); });

  const g = loadGateway({
    BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
    WEB_GATEWAY_SECRET: 'x'.repeat(40),
    GATEWAY_CONNECT_TIMEOUT_MS: '2000',
  });
  await assert.rejects(() => g.getGatewayBinary('/share-card', 1000),
    (e) => e.code === 'GATEWAY_DEADLINE');
});

test('the binary path also fails fast on an unreachable gateway',
  { timeout: 30000 }, async () => {
    // The regression this consolidation exists for: requestBinary never got
    // the connect fast-fail, so it waited its whole response budget for a
    // connection that was never coming.
    const g = loadGateway({
      BOT_GATEWAY_URL: 'http://10.255.255.1:8080',
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '1500',
    });
    const t0 = Date.now();
    await assert.rejects(() => g.getGatewayBinary('/share-card', 45000),
      (e) => e.code === 'GATEWAY_UNREACHABLE');
    const took = Date.now() - t0;
    assert.ok(took < 6000,
      `must fail on the connect budget, not the 45000ms response budget — took ${took}ms`);
  });

test('a prompt response is unaffected by the deadline',
  { timeout: 30000 }, async (t) => {
    const srv = net.createServer((sock) => {
      sock.on('data', () => {
        const body = '{"reply":"ok"}';
        sock.end('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
          + `Content-Length: ${body.length}\r\n\r\n${body}`);
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    t.after(() => srv.close());

    const g = loadGateway({
      BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '2000',
    });
    const r = await g.postGateway('/chat', { text: 'hi' }, 5000);
    assert.equal(r.status, 200);
    assert.deepEqual(r.data, { reply: 'ok' });
  });

test('a slow-but-finishing reply still succeeds inside its budget',
  { timeout: 30000 }, async (t) => {
    // The deadline must bound the pathological case, not the merely slow one:
    // there is a model behind a chat reply.
    const srv = net.createServer((sock) => {
      sock.on('data', () => {
        setTimeout(() => {
          const body = '{"reply":"slow but done"}';
          sock.end('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
            + `Content-Length: ${body.length}\r\n\r\n${body}`);
        }, 1500);
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    t.after(() => srv.close());

    const g = loadGateway({
      BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '500',
    });
    const r = await g.postGateway('/chat', { text: 'hi' }, 8000);
    assert.deepEqual(r.data, { reply: 'slow but done' });
  });

test('the binary path returns bytes and content-type unharmed',
  { timeout: 30000 }, async (t) => {
    const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const srv = net.createServer((sock) => {
      sock.on('data', () => {
        sock.write('HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n'
          + `Content-Length: ${png.length}\r\n\r\n`);
        sock.end(png);
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    t.after(() => srv.close());

    const g = loadGateway({
      BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '2000',
    });
    const r = await g.getGatewayBinary('/share-card', 5000);
    assert.equal(r.status, 200);
    assert.equal(r.contentType, 'image/png');
    assert.ok(r.body.equals(png), 'binary must not be re-encoded or parsed');
  });

// ── shape ─────────────────────────────────────────────────────────────────

test('both request paths share one timer implementation', () => {
  assert.equal((SRC.match(/const timers = armTimers\(req, timeoutMs, reject\);/g) || []).length, 2,
    'requestJSON and requestBinary must both arm the same timers — the '
    + 'connect fast-fail existing in only one of them is what left the '
    + 'binary path carrying the bug it was written to kill');
});

test('the deadline is absolute, not reset by activity', () => {
  const block = SRC.slice(SRC.indexOf('function armTimers'),
                          SRC.indexOf('function requestJSON'));
  assert.match(block, /deadlineTimer = setTimeout\(/);
  // It must be armed once, up front — anything keyed off a data event would
  // be another inactivity timer wearing a different name.
  assert.ok(!/on\('data'[\s\S]*deadlineTimer/.test(block),
    'a deadline rearmed on activity is not a deadline');
});

test('the deadline is distinguishable from the other two failures', () => {
  assert.match(SRC, /e\.code = 'GATEWAY_DEADLINE'/);
  assert.match(SRC, /e\.code = 'GATEWAY_UNREACHABLE'/);
  // never connected / connected then stalled / connected then overran are
  // three different operator actions.
  assert.ok(SRC.indexOf('GATEWAY_DEADLINE') !== SRC.indexOf('GATEWAY_UNREACHABLE'));
});

test('a late timer cannot tear down a settled request', () => {
  const block = SRC.slice(SRC.indexOf('function armTimers'),
                          SRC.indexOf('function requestJSON'));
  assert.match(block, /if \(settled\) return;/,
    'destroying the socket after resolve would break a response we returned');
  assert.match(SRC, /if \(timers\.settled\(\)\) return;/,
    'and the response handler must not resolve after the deadline rejected');
});

test('a zero deadline disables it rather than meaning instant', () => {
  const block = SRC.slice(SRC.indexOf('function armTimers'),
                          SRC.indexOf('function requestJSON'));
  assert.match(block, /if \(timeoutMs > 0\)/);
});

test('the inactivity timeout is kept, not replaced', () => {
  // It still catches a dead socket sooner than the deadline would.
  assert.match(SRC, /timeout: timeoutMs/);
  assert.match(SRC, /req\.on\('timeout'/);
});
