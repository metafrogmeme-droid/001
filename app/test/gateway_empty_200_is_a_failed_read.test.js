'use strict';
/**
 * An EMPTY 200 from the bot gateway is a failed read, not an empty answer.
 *
 * Both gateway transports did `JSON.parse(data || '{}')`, each beside a
 * correct reject branch that the `|| '{}'` diverted exactly one input away
 * from: a 200 with nothing in it became `{}` and was relayed as a success.
 * Every gateway route answers through json_response, so that body is the
 * edge's, not the bot's — and traced downstream `{}` reached the chat drawer
 * as `reply_html || '…'` (the bot's answer rendered as an ellipsis) and the
 * swap page as a planner verdict. A truncated body already threw; the hole
 * was only the empty one, and it fed all eleven gateway-backed route files.
 *
 * `decodeGatewayBody` is the one reading now. These tests drive it directly,
 * then drive both transports through the real chat router against a mock
 * gateway that answers whatever the test says next.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

// What the mock gateway says on its NEXT request. Every test sets it.
let answer = { status: 200, body: '', type: 'application/json' };
let mockGateway;
let appServer;
let base;
let token;
let gateway;

function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      req.on('data', () => {});
      req.on('end', () => {
        res.statusCode = answer.status;
        res.setHeader('Content-Type', answer.type);
        res.end(answer.body);
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

// Raw text kept beside the parse: the whole subject is bodies that do not parse.
function request(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let text = '';
      res.on('data', (d) => text += d);
      res.on('end', () => {
        let data;
        try { data = JSON.parse(text); } catch (e) { data = undefined; }
        resolve({ status: res.statusCode, type: String(res.headers['content-type'] || ''), text, data });
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

// The `final` frame of an SSE body, or null when none arrived.
function finalFrame(sseText) {
  for (const raw of sseText.split('\n\n')) {
    let event = 'message';
    const dataLines = [];
    for (const line of raw.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
    }
    if (event === 'final' && dataLines.length) return JSON.parse(dataLines.join('\n'));
  }
  return null;
}

test.before(async () => {
  const gwPort = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;
  gateway = require('../lib/gateway');

  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  await pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['linked@empty.io', 'x', 'Linked']);
  const [rows] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['linked@empty.io']);
  const linked = rows[0];
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['778', linked.id]);
  // MemoryDB's UPDATE sets telegram_id but not telegram_linked; the chat
  // router refuses an unlinked caller with a 409 before any gateway call.
  const [lrows] = await pool.execute('SELECT * FROM users WHERE id = ?', [linked.id]);
  lrows[0].telegram_linked = true;
  token = jwt.sign({ user_id: linked.id, email: linked.email }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => {
  if (appServer) appServer.close();
  if (mockGateway) mockGateway.close();
});

// ── the reading itself ──────────────────────────────────────────────────────

test('an empty or unparseable 2xx is a failed read; an empty non-2xx keeps its status', () => {
  const g = require('../lib/gateway');
  const d = g.decodeGatewayBody;
  // Empty 2xx — the hole. 204 included: no gateway route answers one.
  for (const status of [200, 201, 204]) {
    for (const body of ['', '   ', '\n', null, undefined]) {
      assert.deepStrictEqual(d(status, body),
        { status: 502, data: { error: 'Bot gateway error' }, unreadable: 'empty' },
        `${status} ${JSON.stringify(body)}`);
    }
  }
  // Unparseable 2xx — already honest before, still honest.
  assert.deepStrictEqual(d(200, '{'),
    { status: 502, data: { error: 'Bot gateway error' }, unreadable: 'unparseable' });
  assert.deepStrictEqual(d(200, '<html>edge</html>'),
    { status: 502, data: { error: 'Bot gateway error' }, unreadable: 'unparseable' });
  // A body that parses is the reading, whatever it holds — the JSON literal
  // null included, which is a real answer and not an absence.
  assert.deepStrictEqual(d(200, '{"reply_html":"pong"}'),
    { status: 200, data: { reply_html: 'pong' }, unreadable: null });
  assert.deepStrictEqual(d(200, 'null'), { status: 200, data: null, unreadable: null });
  assert.deepStrictEqual(d(403, '{"error":"not_proposer"}'),
    { status: 403, data: { error: 'not_proposer' }, unreadable: null });
  // An empty NON-2xx: the status is the fact; `{}` with the status kept.
  assert.deepStrictEqual(d(404, ''), { status: 404, data: {}, unreadable: null });
  assert.deepStrictEqual(d(500, ''), { status: 500, data: {}, unreadable: null });
  // An unparseable non-2xx keeps its status and says the body was junk.
  assert.deepStrictEqual(d(403, 'nope'),
    { status: 403, data: { error: 'Bot gateway error' }, unreadable: 'unparseable' });
});

// ── the JSON transport ──────────────────────────────────────────────────────

test('postGateway REJECTS an empty 200 rather than resolving {}', async () => {
  answer = { status: 200, body: '', type: 'application/json' };
  await assert.rejects(gateway.postGateway('/chat', { text: 'hi' }, 2000), (err) => {
    assert.match(err.message, /Invalid JSON from gateway/);
    assert.strictEqual(err.reason, 'empty');
    return true;
  });
});

test('an empty 200 behind POST /api/chat is a 502, never a 200 with an empty object', async () => {
  answer = { status: 200, body: '', type: 'application/json' };
  const r = await request('POST', '/api/chat', { text: 'what is my pnl' });
  assert.strictEqual(r.status, 502, r.text);
  assert.ok(r.data && typeof r.data.error === 'string' && r.data.error.length > 0,
    `a 502 with no sentence: ${r.text}`);
  assert.notDeepStrictEqual(r.data, {});
});

test('an unparseable 200 behind POST /api/chat is still a 502 (the branch that always worked)', async () => {
  answer = { status: 200, body: '<html>edge interstitial</html>', type: 'text/html' };
  const r = await request('POST', '/api/chat', { text: 'what is my pnl' });
  assert.strictEqual(r.status, 502, r.text);
});

test('a real answer still relays as the reading it is', async () => {
  answer = { status: 200, body: JSON.stringify({ reply_html: 'pong', intent: 'chat' }), type: 'application/json' };
  const r = await request('POST', '/api/chat', { text: 'hello' });
  assert.strictEqual(r.status, 200, r.text);
  assert.strictEqual(r.data.reply_html, 'pong');
});

test('an empty NON-2xx still relays its status — the fix did not widen', async () => {
  answer = { status: 404, body: '', type: 'application/json' };
  const r = await request('GET', '/api/chat/history');
  assert.strictEqual(r.status, 404, r.text);
  assert.deepStrictEqual(r.data, {});
});

// ── the stream transport ────────────────────────────────────────────────────

test('an empty 200 on the stream route arrives as a `final` frame with status 502', async () => {
  answer = { status: 200, body: '', type: 'application/json' };
  const r = await request('POST', '/api/chat/stream', { text: 'what is my pnl' });
  assert.match(r.type, /text\/event-stream/, r.text);
  const fin = finalFrame(r.text);
  assert.ok(fin, `no final frame in: ${r.text}`);
  assert.strictEqual(fin.status, 502, r.text);
  assert.strictEqual(fin.body.error, 'Bot gateway error');
});

test('a plain JSON refusal on the stream route keeps its own status and body', async () => {
  answer = { status: 403, body: JSON.stringify({ error: 'not_allowlisted' }), type: 'application/json' };
  const r = await request('POST', '/api/chat/stream', { text: 'what is my pnl' });
  const fin = finalFrame(r.text);
  assert.ok(fin, r.text);
  assert.strictEqual(fin.status, 403);
  assert.deepStrictEqual(fin.body, { error: 'not_allowlisted' });
});

// ── the wiring ──────────────────────────────────────────────────────────────

test('both transports read through decodeGatewayBody, and the or-empty-object parse is gone', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'gateway.js'), 'utf8'));
  assert.ok(!/JSON\.parse\([^)]*\|\|\s*'\{\}'\)/.test(src), 'JSON.parse(data || \'{}\') is back');
  const calls = (src.match(/decodeGatewayBody\(/g) || []).length;
  // One definition, two call sites — requestJSON and relayStream's plain-JSON branch.
  assert.ok(calls >= 3, `decodeGatewayBody is defined but reached from ${calls - 1} site(s), not both transports`);
  assert.strictEqual((src.match(/JSON\.parse\(/g) || []).length, 1,
    'a second JSON.parse of a gateway body is a second answer about what an empty one means');
});
