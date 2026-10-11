'use strict';
/**
 * The public chat's daily count is kept per visitor, and a visitor is an
 * opaque key, never an address.
 *
 * Anonymous chat had six messages a minute per address and no daily limit,
 * and spent from the chat budget signed-in users answer from. The bot now
 * counts each visitor's model-answered questions per UTC day
 * (bot/web/public_chat_quota.py); this side sends the key it counts against.
 * Both doors into that chat send it — the website route and the MCP
 * `ask_runeclaw` tool — and they must send the SAME key for the same address,
 * or the tool is a second daily allowance through the back door.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const { visitorKey } = require('../lib/visitor_key');

const seen = [];
let mockGateway;
let appServer;
let base;
let mcp;

function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let data = '';
      req.on('data', (d) => { data += d; });
      req.on('end', () => {
        const body = data ? JSON.parse(data) : {};
        seen.push({ url: req.url, body });
        res.setHeader('Content-Type', 'application/json');
        if (body.text === 'one too many') {
          return res.end(JSON.stringify({
            reply_html: 'This public preview answers 10 questions a day for each visitor.',
            intent: 'public_daily_limit' }));
        }
        res.end(JSON.stringify({ reply_html: 'an answer', intent: 'chat' }));
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

function post(path, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = http.request(`${base}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let data = '';
      res.on('data', (d) => { data += d; });
      res.on('end', () => resolve({ status: res.statusCode, data: data ? JSON.parse(data) : {} }));
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

test.before(async () => {
  const port = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${port}`;
  mcp = require('../routes/mcp');
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/public/chat', require('../routes/public_chat'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});
test.after(() => {
  if (mockGateway) mockGateway.close();
  if (appServer) appServer.close();
});

test('one address is one key, two addresses are two, and the key is not the address', () => {
  const a = visitorKey('203.0.113.7');
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.equal(visitorKey('203.0.113.7'), a);
  assert.notEqual(visitorKey('203.0.113.8'), a);
  assert.ok(!a.includes('.'), 'the address is not in it');
  // Not a bare hash either: the IPv4 space is small enough to hash whole, so
  // an unkeyed digest would be the address by another name.
  const bare = require('crypto').createHash('sha256').update('203.0.113.7').digest('hex');
  assert.notEqual(a, bare.slice(0, 32));
});

test('no address is no key, never one shared key for every unknown caller', () => {
  assert.equal(visitorKey(''), null);
  assert.equal(visitorKey('unknown'), null);
  assert.equal(visitorKey(undefined), null);
});

test('the website route sends the key for the caller it serves, the same on every turn', async () => {
  seen.length = 0;
  await post('/api/public/chat', { text: 'hello' });
  await post('/api/public/chat', { text: 'hello again' });
  const keys = seen.map((s) => s.body.visitor);
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.ok([visitorKey('127.0.0.1'), visitorKey('::ffff:127.0.0.1')].includes(keys[0]),
    'the key is the HMAC of the address this server saw');
});

test('the MCP tool sends the website\'s key for the same address, so it is not a second allowance', async () => {
  seen.length = 0;
  await mcp.handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'ask_runeclaw', arguments: { question: 'hello' } } }, { ip: '198.51.100.4' });
  assert.equal(seen[0].body.visitor, visitorKey('198.51.100.4'));
});

test('the bot\'s daily-limit reply reaches the visitor as an answer that says so', async () => {
  const r = await post('/api/public/chat', { text: 'one too many' });
  assert.equal(r.status, 200, 'a 429 would be drawn as "give it a few seconds"');
  assert.equal(r.data.intent, 'public_daily_limit');
  assert.match(r.data.reply_html, /public preview/);
  const t = await mcp.handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'ask_runeclaw', arguments: { question: 'one too many' } } }, { ip: '198.51.100.5' });
  const out = JSON.parse(t.result.content[0].text);
  assert.equal(out.intent, 'public_daily_limit');
  const desc = mcp.TOOLS.ask_runeclaw.description;
  assert.match(desc, /public_daily_limit/);
});
