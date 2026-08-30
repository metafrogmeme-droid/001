'use strict';
/**
 * A global connection cap bounds the SERVER and not the CALLER.
 *
 * routes/stream.js had MAX_CLIENTS = 500 and nothing else. That answers "can
 * the process survive this?" and never answers "can anyone else still use it?"
 * — one client opening 500 SSE streams took every slot, and every other
 * visitor got a 503 from a server that was working perfectly.
 *
 * The second defect is smaller and the same shape: broadcast() dropped a
 * client whose write threw, while the heartbeat caught the identical error and
 * did NOT drop it. So a socket that died between broadcasts stayed in the set,
 * holding its slot, until a 'close' event that a half-open TCP connection may
 * never deliver. Two cleanup paths that disagree is one cleanup path that is
 * wrong.
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const stream = require('../routes/stream');

function boot() {
  const app = express();
  app.set('trust proxy', false);
  app.use('/api/stream', stream.router);
  const server = http.createServer(app);
  return new Promise((r) => server.listen(0, '127.0.0.1', () => r(server)));
}

/** Open an SSE connection and leave it open. Resolves with its status. */
function openStream(port, sockets) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port, path: '/api/stream', headers: { Accept: 'text/event-stream' } },
      (res) => {
        res.resume();          // drain, never end
        sockets.push(res);
        resolve(res.statusCode);
      });
    req.on('error', reject);
    req.end();
    sockets.push(req);
  });
}

test('one client cannot take every SSE slot', async () => {
  const server = await boot();
  const port = server.address().port;
  const open = [];
  try {
    const statuses = [];
    // One more than the per-IP allowance. All of these come from the same
    // loopback address, which is the whole point.
    for (let i = 0; i < stream.MAX_PER_IP + 3; i++) {
      statuses.push(await openStream(port, open));
    }
    const accepted = statuses.filter((s) => s === 200).length;
    const refused = statuses.filter((s) => s === 429).length;

    assert.strictEqual(accepted, stream.MAX_PER_IP,
      `${accepted} streams accepted from one address; the per-client cap is `
      + `${stream.MAX_PER_IP}. Without it, one caller takes all ${stream.MAX_CLIENTS} `
      + 'global slots and every other visitor gets a 503.');
    assert.ok(refused >= 3, `expected the excess to be refused, got ${statuses}`);
    assert.ok(accepted < stream.MAX_CLIENTS,
      'the per-client cap must bite well before the global one');
  } finally {
    for (const s of open) { try { s.destroy(); } catch (_) { /* closing */ } }
    server.close();
  }
});

test('the refusal says WHOSE problem it is', async () => {
  // 429 (you have too many open) and 503 (the server is full) are different
  // facts, and only one of them is the caller's to fix.
  const server = await boot();
  const port = server.address().port;
  const open = [];
  try {
    for (let i = 0; i < stream.MAX_PER_IP; i++) await openStream(port, open);
    const status = await openStream(port, open);
    assert.strictEqual(status, 429,
      'a client over its own limit got a 503, which blames the server');
  } finally {
    for (const s of open) { try { s.destroy(); } catch (_) { /* closing */ } }
    server.close();
  }
});

test('a closed stream gives its slot back', async () => {
  // If disconnects did not decrement, a client that reconnected normally would
  // lock itself out after MAX_PER_IP page loads.
  const server = await boot();
  const port = server.address().port;
  const open = [];
  try {
    for (let i = 0; i < stream.MAX_PER_IP; i++) await openStream(port, open);
    assert.strictEqual(await openStream(port, open), 429, 'precondition: at the cap');

    for (const s of open) { try { s.destroy(); } catch (_) { /* closing */ } }
    open.length = 0;
    await new Promise((r) => setTimeout(r, 250));   // let 'close' land

    const after = [];
    try {
      assert.strictEqual(await openStream(port, after), 200,
        'slots were not released on disconnect — a client would lock itself '
        + 'out after a handful of ordinary page reloads');
    } finally {
      for (const s of after) { try { s.destroy(); } catch (_) { /* closing */ } }
    }
  } finally {
    for (const s of open) { try { s.destroy(); } catch (_) { /* closing */ } }
    server.close();
  }
});

test('broadcast to a dead socket does not leave it holding a slot', async () => {
  const server = await boot();
  const port = server.address().port;
  const open = [];
  try {
    for (let i = 0; i < stream.MAX_PER_IP; i++) await openStream(port, open);
    assert.strictEqual(await openStream(port, open), 429, 'precondition: at the cap');

    // Kill them at the socket level, then broadcast: the write throws and the
    // cleanup path has to release the per-IP counter, not just the Set.
    for (const s of open) { try { s.destroy(); } catch (_) { /* closing */ } }
    open.length = 0;
    stream.broadcast('test', { n: 1 });
    await new Promise((r) => setTimeout(r, 250));

    const after = [];
    try {
      assert.strictEqual(await openStream(port, after), 200,
        'a failed broadcast write removed the client from the Set but left its '
        + 'per-IP slot counted — the two cleanup paths disagree');
    } finally {
      for (const s of after) { try { s.destroy(); } catch (_) { /* closing */ } }
    }
  } finally {
    server.close();
  }
});

test('the module still exports its caps so the limits are assertable', () => {
  assert.ok(Number.isInteger(stream.MAX_CLIENTS) && stream.MAX_CLIENTS > 0);
  assert.ok(Number.isInteger(stream.MAX_PER_IP) && stream.MAX_PER_IP > 0);
  assert.ok(stream.MAX_PER_IP < stream.MAX_CLIENTS,
    'a per-client cap at or above the global one is not a cap');
});
