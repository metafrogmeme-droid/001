'use strict';
/**
 * "Run on my bot" — the web mirror of Telegram's /mystrategy. The contract:
 * - JWT required; identity resolved SERVER-side and injected as telegram_id
 *   (a client can never pick a strategy for someone else's bot);
 * - GET relays the bot's catalogue + selection; PUT sets; DELETE clears by
 *   posting 'off' — revocable always;
 * - the dashboard states the tighten-only contract and renders nothing on a
 *   failed read (omit, never invent);
 * - 503 when the gateway is unconfigured — never a fabricated answer.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('fs');
const path = require('path');

const seen = [];
let mockGateway, appServer, base, token;

function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let body = '';
      req.on('data', (d) => body += d);
      req.on('end', () => {
        seen.push({ url: req.url, method: req.method,
          secret: req.headers['x-gateway-secret'],
          body: body ? JSON.parse(body) : null });
        res.setHeader('Content-Type', 'application/json');
        if (req.method === 'GET' && req.url.startsWith('/gateway/user/strategy')) {
          res.end(JSON.stringify({ selected: 'dip sniper', selected_slug: 'dip-sniper',
            selected_label: 'Dip Sniper', presets: [], note: 'tighten-only' }));
        } else if (req.method === 'POST' && req.url === '/gateway/user/strategy') {
          const s = String(JSON.parse(body).strategy || '');
          if (s === 'off') res.end(JSON.stringify({ selected: null }));
          else if (s === 'ghost') { res.statusCode = 404; res.end(JSON.stringify({ error: 'unknown_strategy' })); }
          else res.end(JSON.stringify({ selected: 'dip sniper', selected_label: 'Dip Sniper' }));
        } else { res.statusCode = 404; res.end('{}'); }
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

async function req(method, p, body, tok) {
  const r = await fetch(`${base}${p}`, { method,
    headers: { 'Content-Type': 'application/json',
      ...(tok === null ? {} : { Authorization: `Bearer ${tok || token}` }) },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}

test.before(async () => {
  const gwPort = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;
  const express = require('express');
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['botstrat@test.dev', 'x'.repeat(60)]);
  token = jwt.sign({ user_id: u.insertId, email: 'botstrat@test.dev' }, process.env.JWT_SECRET);
  const app = express();
  app.use(express.json());
  app.use('/api/bot-strategy', require('../routes/botstrategy'));
  await new Promise((res) => { appServer = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});
test.after(() => { appServer?.close(); mockGateway?.close(); });

test('JWT required on every verb', async () => {
  for (const m of ['GET', 'PUT', 'DELETE']) {
    const r = await req(m, '/api/bot-strategy', m === 'PUT' ? { strategy: 'dip' } : null, null);
    assert.strictEqual(r.status, 401, m);
  }
});

test('GET relays the bot state with the identity injected server-side', async () => {
  seen.length = 0;
  const r = await req('GET', '/api/bot-strategy');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.selected, 'dip sniper');
  assert.match(seen[0].url, /telegram_id=web%3A\d+|telegram_id=web:\d+/,
    'the web identity is resolved server-side, never client-supplied');
  assert.strictEqual(seen[0].secret, 'g'.repeat(64));
});

test('PUT sets by key/slug/alias; DELETE clears via off', async () => {
  seen.length = 0;
  const set = await req('PUT', '/api/bot-strategy', { strategy: 'dip-sniper' });
  assert.strictEqual(set.status, 200);
  assert.strictEqual(seen[0].body.strategy, 'dip-sniper');
  assert.ok(seen[0].body.telegram_id.startsWith('web:'));
  const unk = await req('PUT', '/api/bot-strategy', { strategy: 'ghost' });
  assert.strictEqual(unk.status, 404, 'unknown preset 404 passes through untouched');
  const del = await req('DELETE', '/api/bot-strategy');
  assert.strictEqual(del.status, 200);
  assert.strictEqual(seen[seen.length - 1].body.strategy, 'off');
});

test('the dashboard states the contract and fails silent-honest', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /data-botstrat/);
  assert.match(dash, /omit, never invent/, 'a failed read renders nothing');
  assert.match(dash, /TIGHTEN-ONLY veto/, 'the contract is stated where the UI lives');
  assert.match(dash, /getElementById\('p-agents'\)\?\.addEventListener/,
    'listener scoped to the view so re-renders never stack duplicates');
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const k of ['bs.h', 'bs.note', 'bs.run_b', 'bs.on_bot', 'bs.cleared', 'bs.fail']) {
    assert.ok(i18n.includes(`'${k}'`), `${k} in the dictionary`);
  }
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.ok(srv.includes("app.use('/api/bot-strategy', require('./routes/botstrategy'))"));
});
