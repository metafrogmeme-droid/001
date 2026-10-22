'use strict';
/**
 * A front end hosted on another origin (the DogeOS hackathon entry on Noah AI)
 * can call the Daily Duel's API, and nothing else gains a cross-origin door
 * (lib/api_cors.js).
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const { codeOnly } = require('./helpers/code_only');
const apiCors = require('../lib/api_cors');

const NOAH = 'https://runeclaw-duel.noah.example';
const ENV = { API_CORS_ORIGINS: `${NOAH}, https://*.preview.noah.example, http://insecure.example, not a url` };

async function serve(env) {
  const app = express();
  app.use(apiCors.cors({ env }));
  app.use(express.json());
  app.get('/api/duel/today', (req, res) => res.json({ rounds: [] }));
  app.post('/api/auth/wallet/verify', (req, res) => res.json({ token: 't' }));
  app.get('/api/roots', (req, res) => res.json({ roots: [] }));
  app.get('/api/arena/account', (req, res) => res.json({ equity: 1 }));
  app.post('/api/auth/login', (req, res) => res.json({ ok: true }));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const ask = (base, p, { method = 'GET', origin, headers = {} } = {}) => fetch(base + p, {
  method, headers: { ...(origin ? { origin } : {}), ...headers },
});

test('an allowed origin may call the duel, and is never handed the cookie', async () => {
  const { server, base } = await serve(ENV);
  try {
    let r = await ask(base, '/api/duel/today', { origin: NOAH });
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.headers.get('access-control-allow-origin'), NOAH);
    assert.match(r.headers.get('vary') || '', /Origin/);
    assert.strictEqual(r.headers.get('access-control-allow-credentials'), null, 'cookies never cross');

    // A preflight for a POST with a Bearer header and JSON is answered, and stops there.
    r = await ask(base, '/api/auth/wallet/verify', { method: 'OPTIONS', origin: NOAH,
      headers: { 'access-control-request-method': 'POST', 'access-control-request-headers': 'authorization, content-type' } });
    assert.strictEqual(r.status, 204);
    assert.strictEqual(r.headers.get('access-control-allow-origin'), NOAH);
    assert.match(r.headers.get('access-control-allow-methods'), /POST/);
    assert.match(r.headers.get('access-control-allow-headers'), /authorization/);
    assert.match(r.headers.get('access-control-allow-headers'), /content-type/);

    // A subdomain under a `*.` entry is allowed; the bare domain is not.
    r = await ask(base, '/api/roots', { origin: 'https://abc.preview.noah.example' });
    assert.strictEqual(r.headers.get('access-control-allow-origin'), 'https://abc.preview.noah.example');
  } finally { server.close(); }
});

test('any other origin, route, scheme or setting gets no cross-origin header', async () => {
  const { server, base } = await serve(ENV);
  try {
    const none = async (p, origin, method) => {
      const r = await ask(base, p, { origin, method });
      assert.strictEqual(r.headers.get('access-control-allow-origin'), null, `${origin} on ${p} got a door`);
      return r;
    };
    await none('/api/duel/today', 'https://evil.example');
    await none('/api/duel/today', 'https://evilruneclaw-duel.noah.example');
    await none('/api/roots', 'https://preview.noah.example', 'GET');
    await none('/api/roots', 'https://xpreview.noah.example', 'GET');
    await none('/api/duel/today', 'http://insecure.example');
    // A subdomain under a `*.` entry, over plain http, is still not https.
    await none('/api/roots', 'http://abc.preview.noah.example', 'GET');
    // The allowed origin, on routes a duel front end does not call.
    await none('/api/arena/account', NOAH);
    await none('/api/auth/login', NOAH, 'POST');
    // A refused preflight is not answered as a preflight.
    const pre = await none('/api/duel/today', 'https://evil.example', 'OPTIONS');
    assert.notStrictEqual(pre.status, 204);
  } finally { server.close(); }
  // Unset: nothing at all, even for an origin that is named elsewhere.
  const unset = await serve({});
  try {
    const r = await ask(unset.base, '/api/duel/today', { origin: NOAH });
    assert.strictEqual(r.headers.get('access-control-allow-origin'), null);
  } finally { unset.server.close(); }
});

test('the routes it opens are the ones the duel\'s own scripts call', () => {
  // The duel, proof, anchor and receipt scripts /doge loads: a hosted front end
  // makes the same calls, so every one must be inside a prefix.
  const PUBLIC = path.join(__dirname, '..', 'public', 'js');
  const files = ['doge-app.js', 'doge-proof.js', 'anchor_cell.js', 'duel.js', 'pi-duel.js', 'pi-receipt.js'];
  const calls = new Set();
  for (const f of files) {
    const src = codeOnly(fs.readFileSync(path.join(PUBLIC, f), 'utf8'));
    for (const m of src.matchAll(/['"`](\/api\/[a-zA-Z0-9_/.:-]+)/g)) calls.add(m[1]);
  }
  assert.ok(calls.size >= 8, `only ${calls.size} API calls found; the scan is not seeing them`);
  for (const c of calls) {
    const p = c.endsWith('/') ? c + 'x' : c;
    assert.ok(apiCors.covered(p), `${c} is called by the duel's scripts but has no cross-origin door`);
  }
});

test('the server mounts it before the body parsers, and nothing else sends a cross-origin header', () => {
  const server = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
  const at = server.indexOf("require('./lib/api_cors').cors()");
  assert.ok(at > 0, 'the CORS middleware is not mounted');
  assert.ok(at < server.indexOf('express.json('), 'mounted after a body parser: a preflight would wait on a body');
  for (const dir of ['lib', 'routes']) {
    for (const f of fs.readdirSync(path.join(__dirname, '..', dir)).filter((n) => n.endsWith('.js'))) {
      if (dir === 'lib' && f === 'api_cors.js') continue;
      const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', dir, f), 'utf8'));
      assert.ok(!/Access-Control-Allow-Origin/i.test(src), `${dir}/${f} sends its own cross-origin header`);
    }
  }
});
