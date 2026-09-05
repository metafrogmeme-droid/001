'use strict';
/**
 * The streaming chat routes relay the bot's event stream frame for frame.
 *
 * The drawer used to fake streaming: a complete answer paced out in word
 * batches. POST /api/chat/stream (and /api/public/chat/stream) now answer as
 * text/event-stream — `delta` frames as the model produces text, `tool`
 * frames while it reads something, and ONE `final` frame carrying exactly
 * the JSON the plain route returns. Three things this pins:
 *
 *   1. frames from the bot reach the browser unchanged, in order;
 *   2. an answer decided locally (an intercept hit) is ONE final frame, so
 *      the browser's reader needs no second code path;
 *   3. a gateway that answers plain JSON (an older deploy, a 4xx) is wrapped
 *      as a final frame rather than parsed mid-stream.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');

// An intercept that answers "exposure" locally, so the local-hit path can be
// driven without a wallet.
const abs = require.resolve(path.join(__dirname, '..', 'lib', 'exposure'));
require.cache[abs] = { id: abs, filename: abs, loaded: true, exports: {
  maybeHandleExposureChat: async (uid, text) => (
    /exposure/i.test(text) ? { reply_html: '<b>Exposure</b> flat', intent: 'exposure' } : null),
} };

const seen = [];
let mockGateway, appServer, base;

function sse(res, frames) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8' });
  for (const [event, data] of frames) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  res.end();
}

function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let body = '';
      req.on('data', (d) => body += d);
      req.on('end', () => {
        seen.push({ url: req.url, body: body ? JSON.parse(body) : null, accept: req.headers.accept });
        if (req.url === '/gateway/chat/stream') {
          if (seen[seen.length - 1].body.text === 'plain please') {
            res.writeHead(403, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ reply_html: 'Your role cannot use that.', error: 'insufficient_permissions' }));
          }
          return sse(res, [
            ['attempt', { provider: 'grok', model: 'grok-4.3' }],
            ['tool', { name: 'get_portfolio', phase: 'start' }],
            ['tool', { name: 'get_portfolio', phase: 'done', ok: true }],
            ['delta', { text: 'You hold ' }],
            ['delta', { text: 'nothing open.' }],
            ['final', { status: 200, body: { reply_html: 'You hold nothing open.', intent: 'chat', model: 'grok-4.3', tools: [{ name: 'get_portfolio', ok: true, ms: 3 }] } }],
          ]);
        }
        if (req.url === '/gateway/chat/public/stream') {
          return sse(res, [
            ['delta', { text: 'RUNECLAW is ' }],
            ['delta', { text: 'a trading agent.' }],
            ['final', { status: 200, body: { reply_html: 'RUNECLAW is a trading agent.', intent: 'chat' } }],
          ]);
        }
        if (req.url === '/gateway/chat/record') {
          res.setHeader('Content-Type', 'application/json');
          return res.end(JSON.stringify({ ok: true }));
        }
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify({ reply_html: 'pong', intent: 'chat' }));
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

function requestRaw(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
        Accept: 'text/event-stream',
      },
    }, (res) => {
      let data = '';
      res.on('data', (d) => data += d);
      res.on('end', () => resolve({ status: res.statusCode, type: res.headers['content-type'] || '', text: data }));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function frames(text) {
  return text.split('\n\n').filter(Boolean).map((raw) => {
    const out = { event: 'message', data: null };
    for (const line of raw.split('\n')) {
      if (line.startsWith('event:')) out.event = line.slice(6).trim();
      else if (line.startsWith('data:')) out.data = JSON.parse(line.slice(5));
    }
    return out;
  });
}

let token;
test.before(async () => {
  const gwPort = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;
  const jwt = require('jsonwebtoken');
  const db = require('../db');
  await db.pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['stream@test.io', 'x', 'Streamer']);
  const [rows] = await db.pool.execute('SELECT id, email FROM users WHERE email = ?', ['stream@test.io']);
  token = jwt.sign({ user_id: rows[0].id, email: rows[0].email }, process.env.JWT_SECRET);
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/chat', require('../routes/chat'));
  app.use('/api/public/chat', require('../routes/public_chat'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});
test.after(() => { if (appServer) appServer.close(); if (mockGateway) mockGateway.close(); });

test('the stream route relays every frame, in order, as text/event-stream', async () => {
  seen.length = 0;
  const r = await requestRaw('POST', '/api/chat/stream', { token, body: { text: 'what do I hold?' } });
  assert.equal(r.status, 200);
  assert.match(r.type, /text\/event-stream/);
  const fr = frames(r.text);
  assert.deepEqual(fr.map((f) => f.event), ['attempt', 'tool', 'tool', 'delta', 'delta', 'final']);
  assert.equal(fr[3].data.text + fr[4].data.text, 'You hold nothing open.');
  assert.equal(fr[5].data.status, 200);
  assert.equal(fr[5].data.body.reply_html, 'You hold nothing open.');
  assert.deepEqual(fr[5].data.body.tools, [{ name: 'get_portfolio', ok: true, ms: 3 }]);
  const call = seen.find((s) => s.url === '/gateway/chat/stream');
  assert.ok(call, 'the gateway stream route was asked');
  assert.match(call.body.telegram_id, /^web:\d+$/);
  assert.equal(call.accept, 'text/event-stream');
});

test('a local intercept hit on the stream route is one final frame', async () => {
  seen.length = 0;
  const r = await requestRaw('POST', '/api/chat/stream', { token, body: { text: 'what is my exposure?' } });
  assert.equal(r.status, 200);
  assert.match(r.type, /text\/event-stream/);
  const fr = frames(r.text);
  assert.deepEqual(fr.map((f) => f.event), ['final']);
  assert.deepEqual(fr[0].data, { status: 200, body: { reply_html: '<b>Exposure</b> flat', intent: 'exposure' } });
  assert.ok(!seen.some((s) => s.url === '/gateway/chat/stream'), 'the model was never asked');
});

test('a plain JSON refusal from the gateway is wrapped as a final frame with its status', async () => {
  seen.length = 0;
  const r = await requestRaw('POST', '/api/chat/stream', { token, body: { text: 'plain please' } });
  assert.equal(r.status, 200, 'the stream itself opened');
  const fr = frames(r.text);
  assert.deepEqual(fr.map((f) => f.event), ['final']);
  assert.equal(fr[0].data.status, 403);
  assert.equal(fr[0].data.body.error, 'insufficient_permissions');
});

test('validation failures stay plain JSON, before any stream opens', async () => {
  const r = await requestRaw('POST', '/api/chat/stream', { token, body: { text: '' } });
  assert.equal(r.status, 400);
  assert.match(r.type, /application\/json/);
  const nope = await requestRaw('POST', '/api/chat/stream', { body: { text: 'hi' } });
  assert.equal(nope.status, 401);
});

test('the public stream route relays without any identity', async () => {
  seen.length = 0;
  const r = await requestRaw('POST', '/api/public/chat/stream', { body: { text: 'what is runeclaw', lang: 'es' } });
  assert.equal(r.status, 200);
  const fr = frames(r.text);
  assert.deepEqual(fr.map((f) => f.event), ['delta', 'delta', 'final']);
  const call = seen.find((s) => s.url === '/gateway/chat/public/stream');
  assert.deepEqual(call.body, { text: 'what is runeclaw', lang: 'es' });
});

test('the plain routes still answer JSON exactly as before', async () => {
  const r = await requestRaw('POST', '/api/chat', { token, body: { text: 'hi' } });
  assert.equal(r.status, 200);
  assert.match(r.type, /application\/json/);
  assert.deepEqual(JSON.parse(r.text), { reply_html: 'pong', intent: 'chat' });
});
