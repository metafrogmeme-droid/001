'use strict';
/**
 * The web chat's local intercepts: order, first-hit-wins, and MEMORY.
 *
 * routes/chat.js answers a dozen shapes of question without a bot round-trip.
 * Until now each answered and vanished — the bot's conversation store, which
 * both surfaces read history from, never heard the question or the answer,
 * so a follow-up two turns later reached a model that had never seen the
 * first. Every hit is now recorded via POST /gateway/chat/record, off the
 * reply path. None of the fourteen intercepts had a test; the table they now
 * live in is what makes their ORDER assertable at all.
 *
 * The intercept libraries are replaced in require.cache BEFORE the route is
 * loaded, so each one answers exactly what this file tells it to and nothing
 * here depends on a wallet, a DeFi read or a ticker feed.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const express = require('express');

// ── stub every intercept library ────────────────────────────────────────────
const calls = [];            // [name] in the order the route consulted them
const answers = {};          // name -> reply object to return (null = miss)

function stub(rel, exportsObj) {
  const abs = require.resolve(path.join(__dirname, '..', rel));
  require.cache[abs] = { id: abs, filename: abs, loaded: true, exports: exportsObj };
}
function intercept(name, fnName, withIdent = false) {
  return {
    [fnName]: async (...args) => {
      calls.push(name);
      if (withIdent) assert.ok(args[0] && typeof args[0].id === 'string', `${name} got no identity`);
      return answers[name] || null;
    },
  };
}
stub('lib/alerts', intercept('alerts', 'maybeHandleAlertChat'));
stub('lib/replay', intercept('replay', 'maybeHandleReplayChat'));
stub('lib/letter', intercept('letter', 'maybeHandleLetterChat'));
stub('lib/rwa', intercept('rwa', 'maybeHandleRwaChat'));
stub('lib/airdrops', intercept('airdrops', 'maybeHandleAirdropChat'));
stub('lib/venue_router', intercept('venues', 'maybeHandleVenueRouterChat'));
stub('lib/meme', intercept('meme', 'maybeHandleMemeChat'));
stub('lib/opensea', intercept('nft', 'maybeHandleNftChat'));
stub('lib/spot', intercept('spot', 'maybeHandleSpotChat'));
stub('lib/wallet', intercept('wallet', 'maybeHandleWalletChat'));
stub('lib/defi', intercept('defi', 'maybeHandleDefiChat'));
stub('lib/exposure', intercept('exposure', 'maybeHandleExposureChat'));
stub('lib/research', intercept('research', 'maybeHandleResearchChat'));
stub('lib/networth', intercept('networth', 'maybeHandleNetWorthChat', true));
stub('lib/idle_yield', intercept('idleyield', 'maybeHandleIdleYieldChat', true));

// A fake bot gateway that records what it was told.
const posted = [];
let recordStatus = 200;
stub('lib/gateway', {
  isConfigured: () => true,
  relay: (res, r) => res.status(r.status).json(r.data),
  postGateway: async (p, body) => {
    posted.push({ path: p, body });
    if (p === '/chat/record') {
      if (recordStatus === 'throw') throw new Error('gateway down');
      return { status: recordStatus, data: { ok: recordStatus === 200 } };
    }
    return { status: 200, data: { reply_html: 'model answered', intent: 'chat' } };
  },
  getGateway: async () => ({ status: 200, data: { messages: [] } }),
  getGatewayBinary: async () => ({ status: 404 }),
});

const authModule = require('../auth');
const chat = require('../routes/chat');

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/chat', chat);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `icpt${seq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

const flush = () => new Promise((r) => setTimeout(r, 30));

function reset() {
  calls.length = 0;
  posted.length = 0;
  for (const k of Object.keys(answers)) delete answers[k];
  recordStatus = 200;
}

// ── the table ───────────────────────────────────────────────────────────────

test('the routing table is the documented order', () => {
  assert.deepEqual(chat.INTERCEPTS.map(([n]) => n), [
    'alerts', 'replay', 'letter', 'rwa', 'airdrops', 'venues', 'meme', 'nft',
    'spot', 'wallet', 'defi', 'exposure', 'research', 'networth', 'idleyield',
  ]);
});

test('a miss consults every intercept in order, then the model', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'hello there' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  // The two identity-bound intercepts gate on their own pattern first, so a
  // sentence matching neither never consults them (and never resolves the
  // identity for them).
  assert.deepEqual(calls, chat.INTERCEPTS.map(([n]) => n).slice(0, 13));
  await flush();
  assert.deepEqual(posted.map((p) => p.path), ['/chat']);
});

test('the first hit answers and nothing below it runs', async () => {
  reset();
  answers.rwa = { reply_html: '<b>RWA radar</b> 3 sectors', intent: 'rwa' };
  answers.defi = { reply_html: 'never', intent: 'defi' };
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'rwa radar' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, '<b>RWA radar</b> 3 sectors');
  assert.deepEqual(calls, ['alerts', 'replay', 'letter', 'rwa']);
});

// ── memory ──────────────────────────────────────────────────────────────────

test('a hit is recorded into the shared conversation memory, as tool output', async () => {
  reset();
  answers.exposure = { reply_html: '<b>Exposure</b> long $400 BTC', intent: 'exposure' };
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: "what's my total exposure?" } });
  assert.equal(r.status, 200);
  await flush();
  const rec = posted.find((p) => p.path === '/chat/record');
  assert.ok(rec, 'the answer must reach /gateway/chat/record');
  assert.match(rec.body.telegram_id, /^web:\d+$/);
  assert.equal(rec.body.text, "what's my total exposure?");
  assert.equal(rec.body.reply, '<b>Exposure</b> long $400 BTC');
  assert.equal(rec.body.intent, 'exposure');
  assert.ok(!posted.some((p) => p.path === '/chat'), 'a local hit never asks the model');
});

test('the identity-bound intercepts record too, and resolve the identity once', async () => {
  reset();
  answers.networth = { reply_html: 'Net worth ~$12k', intent: 'networth' };
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'what is my net worth' } });
  assert.equal(r.data.reply_html, 'Net worth ~$12k');
  assert.ok(calls.includes('networth'));
  await flush();
  const rec = posted.find((p) => p.path === '/chat/record');
  assert.equal(rec.body.intent, 'networth');
});

test('a refused or failed memory write never touches the reply', async () => {
  const token = await newUser();
  for (const mode of [500, 'throw']) {
    reset();
    recordStatus = mode;
    answers.wallet = { reply_html: 'wallet mirror', intent: 'wallet' };
    const r = await req('POST', '/api/chat', { token, body: { text: 'my wallet' } });
    assert.equal(r.status, 200, `mode ${mode}`);
    assert.equal(r.data.reply_html, 'wallet mirror');
    await flush();
    assert.ok(posted.some((p) => p.path === '/chat/record'), 'the write was attempted');
  }
});

test('a reply without html records nothing', async () => {
  reset();
  answers.alerts = { pending_trade: { trade_id: 'x' } };
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'tell me when' } });
  assert.equal(r.status, 200);
  await flush();
  assert.ok(!posted.some((p) => p.path === '/chat/record'));
});
