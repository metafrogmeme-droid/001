'use strict';
/**
 * The web chat's local intercepts: order, first-hit-wins, and MEMORY.
 *
 * routes/chat.js answers nothing from its own intercepts. Idle yield was
 * the last row and left for the shared door.
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

test('the routing table is empty', () => {
  assert.deepEqual(chat.INTERCEPTS.map(([n]) => n), []);
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'alerts'), false,
    'price alerts are the shared price_alert door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'letter'), false,
    'the weekly letter is the shared letter door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'wallet'), false,
    'the wallet mirror is the shared wallet door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'replay'), false,
    'the what-if replay is the shared replay door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'rwa'), false,
    'the tokenized-asset radar is the shared rwa door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'airdrops'), false,
    'the airdrop radar is the shared airdrops door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'venues'), false,
    'the venue router is the shared venue_router door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'meme'), false,
    'the meme radar is the shared meme_radar door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'nft'), false,
    'the NFT radar is the shared nft door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'spot'), false,
    'the spot market is the shared spot door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'defi'), false,
    'DeFi positions are the shared defi door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'exposure'), false,
    'cross-venue exposure is the shared exposure door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'research'), false,
    'the research dossier is the shared research door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'networth'), false,
    'net worth is the shared networth door, not a private intercept');
  assert.equal(chat.INTERCEPTS.some(([n]) => n === 'idleyield'), false,
    'idle yield is the shared idleyield door, not a private intercept');
});

test('every row says what it does, in words a person reads', () => {
  // The third column is the cable for `capability_answer(extras=...)`. That
  // parameter had no production caller for its whole life — its docstring
  // says it exists for "the web client's own intercepts", which live HERE —
  // so the card built to stop the bot overstating what it can do was
  // understating it by every row below.
  const says = chat.interceptSays();
  assert.equal(says.length, chat.INTERCEPTS.length,
    'a row grew a handler and no sentence; the card would omit it silently');
  for (const [name, , text] of chat.INTERCEPTS) {
    assert.equal(typeof text, 'string', name);
    assert.ok(text.trim().length > 10, `${name}: "${text}"`);
    // Written for a PERSON, not for the model and not for a developer: the
    // card renders these beside `SKILL_SAYS` rows, which a sibling test in
    // tests/test_the_bot_can_say_what_it_does.py holds to the same rule.
    assert.ok(/^[a-z]/.test(text), `${name} should start lowercase: "${text}"`);
    assert.ok(!/[<>]/.test(text), `${name} carries markup: "${text}"`);
  }
  assert.equal(new Set(says).size, says.length, 'two rows claim the same thing');
});

test('the turn carries what this client answers for itself', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'hello there' } });
  assert.equal(r.status, 200);
  const sent = posted.find((p) => p.path === '/chat');
  assert.ok(sent, 'nothing reached the bot');
  assert.deepEqual(sent.body.client_capabilities, chat.interceptSays());
  // Sent on EVERY turn, not only a capability ask: this route does not
  // classify the message, the bot does, and a field that only sometimes
  // arrives is a field that is sometimes missing for reasons nobody can
  // reconstruct. The table is empty, so the list is empty — absent rows,
  // not a missing field.
  assert.deepEqual(sent.body.client_capabilities, []);
});

test('a miss consults every intercept in order, then the model', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'hello there' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.deepEqual(calls, []);
  await flush();
  assert.deepEqual(posted.map((p) => p.path), ['/chat']);
});

test('"where can i earn" is not a local intercept', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'where can i earn' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.deepEqual(calls, []);
});

test('"spot market" and "my defi positions" are not local intercepts', async () => {
  reset();
  const token = await newUser();
  let r = await req('POST', '/api/chat', { token, body: { text: 'spot market' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('spot'));
  reset();
  r = await req('POST', '/api/chat', { token, body: { text: 'my defi positions' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('defi'));
  reset();
  r = await req('POST', '/api/chat', { token, body: { text: "what's my total exposure?" } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('exposure'));
  reset();
  r = await req('POST', '/api/chat', { token, body: { text: 'research SOL' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('research'));
  reset();
  r = await req('POST', '/api/chat', { token, body: { text: 'what is my net worth' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('networth'));
  reset();
  r = await req('POST', '/api/chat', { token, body: { text: 'my idle usdc' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('idleyield'));
});

test('"nft radar" is not a local intercept', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'nft radar' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('nft'));
});

test('"meme radar" is not a local intercept', async () => {
  reset();
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'meme radar' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, 'model answered');
  assert.ok(!calls.includes('meme'));
});

// ── memory ──────────────────────────────────────────────────────────────────

test('rememberIntercept still records a card, and a reply without html records nothing', async () => {
  reset();
  const token = await newUser();
  // The table is empty, so nothing on the route hits the recorder. The
  // function stays: a later row must not have to reinvent it. Drive it.
  await req('POST', '/api/chat', { token, body: { text: 'hello there' } });
  chat.rememberIntercept(async () => ({ id: 'web:7' }), 'my idle usdc',
    { reply_html: '<b>Idle yield</b>' }, 'idleyield');
  chat.rememberIntercept(async () => ({ id: 'web:7' }), 'my idle usdc',
    { pending_trade: { trade_id: 'x' } }, 'idleyield');
  await flush();
  const recs = posted.filter((p) => p.path === '/chat/record');
  assert.equal(recs.length, 1);
  assert.equal(recs[0].body.telegram_id, 'web:7');
  assert.equal(recs[0].body.text, 'my idle usdc');
  assert.equal(recs[0].body.reply, '<b>Idle yield</b>');
  assert.equal(recs[0].body.intent, 'idleyield');
});

test('a refused or failed memory write never throws into the caller', async () => {
  for (const mode of [500, 'throw']) {
    reset();
    recordStatus = mode;
    chat.rememberIntercept(async () => ({ id: 'web:7' }), 'my idle usdc',
      { reply_html: 'idle yield card' }, 'idleyield');
    await flush();
    assert.ok(posted.some((p) => p.path === '/chat/record'), `mode ${mode}`);
  }
});
