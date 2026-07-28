'use strict';

/**
 * Connecting and responding deserve different patience.
 *
 * Production, 2026-07-28. The bot's aiohttp gateway was bound to
 * 127.0.0.1:8080, so packets from the website vanished — a DROP, not a
 * REJECT, which hangs instead of bouncing. The chat request then sat for the
 * full response budget, longer than the CDN in front of the app was willing
 * to wait, and the visitor got the edge's opaque plain-text "error code: 502"
 * instead of this app's {"error":"Chat unavailable"}.
 *
 * That mattered more than it sounds: with our own error masked, "cannot reach
 * the bot" was indistinguishable from "the web app is down", and the latter
 * was reported and investigated before /api/version showed the app serving
 * happily all along.
 *
 * A chat reply legitimately takes many seconds — there is a model behind it.
 * Getting a socket does not: on a reachable host it is milliseconds. Bounding
 * the connect phase separately means the honest error arrives first.
 */

const test = require('node:test');
const assert = require('node:assert');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'lib', 'gateway.js'), 'utf8');

/** Load gateway.js fresh with a given env. */
function loadGateway(env) {
  for (const [k, v] of Object.entries(env)) process.env[k] = v;
  delete require.cache[require.resolve('../lib/gateway')];
  return require('../lib/gateway');
}

test('an unreachable gateway fails fast, not on the response budget',
  { timeout: 30000 }, async () => {
    // 10.255.255.1 routes nowhere: the SYN is dropped and the connect hangs,
    // which is exactly the shape a firewall DROP produces.
    const g = loadGateway({
      BOT_GATEWAY_URL: 'http://10.255.255.1:8080',
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '1500',
    });
    const t0 = Date.now();
    await assert.rejects(
      () => g.postGateway('/chat', { text: 'hi' }, 45000),
      (e) => e.code === 'GATEWAY_UNREACHABLE',
    );
    const took = Date.now() - t0;
    assert.ok(took < 6000,
      `must fail on the CONNECT budget (~1500ms), not the response budget `
      + `(45000ms) — took ${took}ms`);
  });

test('a connected socket is never cut short by the connect timer',
  { timeout: 30000 }, async (t) => {
    // Accept the connection, then answer slowly — the connect timer must have
    // been cleared, or a legitimate model reply would be killed mid-flight.
    const srv = net.createServer((sock) => {
      sock.on('data', () => {
        setTimeout(() => {
          const body = '{"reply":"ok"}';
          sock.end('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
            + `Content-Length: ${body.length}\r\n\r\n${body}`);
        }, 1200);           // well past the 300ms connect budget below
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    t.after(() => srv.close());

    const g = loadGateway({
      BOT_GATEWAY_URL: `http://127.0.0.1:${srv.address().port}`,
      WEB_GATEWAY_SECRET: 'x'.repeat(40),
      GATEWAY_CONNECT_TIMEOUT_MS: '300',
    });
    const r = await g.postGateway('/chat', { text: 'hi' }, 20000);
    assert.equal(r.status, 200);
    assert.deepEqual(r.data, { reply: 'ok' });
  });

test('a refused connection still surfaces its own code', async () => {
  const g = loadGateway({
    BOT_GATEWAY_URL: 'http://127.0.0.1:1',
    WEB_GATEWAY_SECRET: 'x'.repeat(40),
    GATEWAY_CONNECT_TIMEOUT_MS: '3000',
  });
  await assert.rejects(() => g.postGateway('/chat', { text: 'hi' }, 20000),
    (e) => e.code === 'ECONNREFUSED');
});

// ── shape ─────────────────────────────────────────────────────────────────

test('the connect budget is separate, tunable, and disablable', () => {
  assert.match(SRC, /GATEWAY_CONNECT_TIMEOUT_MS \|\| 4000/,
    'a few seconds — a reachable host connects in milliseconds');
  assert.match(SRC, /if \(!CONNECT_TIMEOUT_MS\) return;/,
    '0 must disable it rather than mean "instant"');
});

test('the timer is cleared on connect AND on error', () => {
  const block = SRC.slice(SRC.indexOf("req.on('socket'"), SRC.indexOf("req.on('error'"));
  assert.match(block, /sock\.once\('connect', clear\)/,
    'a live socket must not be killed by the connect budget');
  assert.match(block, /sock\.once\('error', clear\)/,
    'a stray timer after an error would reject an already-settled promise');
});

test('the unreachable case is distinguishable from a timeout', () => {
  assert.match(SRC, /e\.code = 'GATEWAY_UNREACHABLE'/,
    '"never connected" and "connected but slow" are different faults');
  assert.match(SRC, /Gateway timeout/, 'the response timeout keeps its own message');
});
