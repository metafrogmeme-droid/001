'use strict';
/**
 * Ask the Claw, the website's half (routes/pi_coach.js, lib/pi_coach.js):
 * who may ask, how many a day, what the coach is handed, and Coach Plus.
 *
 * The bot's half (the prompt, the gates, the output check) is
 * tests/test_the_pi_coach_says_nothing_the_pi_app_must_not.py. Here the
 * gateway is a stand-in that records what it was sent, so the facts the
 * coach would read are asserted as sent, and every allowance answer is
 * driven through the route on the in-memory database.
 */
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

delete process.env.DATABASE_URL;
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const { pool } = require('../db');
const { findOrCreateOAuthUser, signToken } = require('../auth');
const router = require('../routes/pi_coach');
const payments = require('../routes/pi_payments');
const coach = require('../lib/pi_coach');

const T0 = new Date('2026-10-20T10:00:00Z');

function fakeGateway(reply = { reply_html: 'A <b>stop-loss</b> closes the trade.', intent: 'pi_coach', answered: true }, status = 200) {
  const calls = [];
  return {
    calls,
    isConfigured: () => true,
    postGateway: async (path, body) => { calls.push({ path, body }); return { status, data: reply }; },
  };
}

const CARD = { day: '2026-10-20', rounds: [
  // Uncalled classic round: the card carries no stance, so neither may the facts.
  { id: 21, kind: 'direction', symbol: 'BTCUSDT', callable: true, horizon_hours: 24 },
  // Back/fade: the stance IS the question.
  { id: 22, kind: 'backfade', symbol: 'SOLUSDT', callable: true, agent_direction: 'short', horizon_hours: 24 },
  // Called and settled.
  { id: 23, kind: 'range', symbol: 'BNBUSDT', callable: false, threshold_pct: 2, horizon_hours: 24,
    my_call: { pick: 'hold' }, my_result: { state: 'correct' } },
] };

async function serve({ gateway = fakeGateway(), env = {}, positions = [], card = CARD } = {}) {
  router._deps = {
    gateway, env, now: () => T0,
    arena: { loadPositions: async () => positions },
    duel: { cardFor: async () => card },
  };
  const app = express();
  app.use(express.json());
  app.use('/api/pi/coach', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, base, gateway };
}

let seq = 0;
async function account({ pi = true } = {}) {
  seq += 1;
  const user = pi
    ? await findOrCreateOAuthUser({ provider: 'pi', providerId: `pi-coach-uid-${seq}-${Date.now()}` })
    : await findOrCreateOAuthUser({ provider: 'google', providerId: `g-coach-${seq}-${Date.now()}`, email: `c${seq}@example.com` });
  return { id: user.id, token: signToken(user) };
}

async function ask(base, token, body, method = 'POST') {
  const r = await fetch(`${base}/api/pi/coach`, {
    method, headers: { authorization: `Bearer ${token}`, ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
    body: method === 'POST' ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => null) };
}

test('a Pi account asks; the coach is handed facts the server read, never the page\'s', async () => {
  const me = await account();
  pool.arenaTrades.push({ id: 900001, user_id: me.id, symbol: 'ETHUSDT', direction: 'LONG', entry: 3000, exit_price: 2970,
    margin: 100, leverage: 5, pnl: -5, reason: 'stop', trade_key: null, seal: null,
    opened_at: '2026-10-19T08:00:00Z', closed_at: '2026-10-19T09:00:00Z' });
  pool.signals.push({ signal_key: 'k-why-1', symbol: 'BTC/USDT:USDT', direction: 'LONG', confidence: 0.7, regime: 'trending',
    entry_price: 65000, stop_loss: 64000, take_profit: 67000, status: 'NEW', created_at: '2026-10-20T09:00:00Z' });
  const { server, base, gateway } = await serve({ positions: [{ id: 5, symbol: 'BTCUSDT', direction: 'LONG', entry: 65000,
    margin: 200, leverage: 2, opened_at: '2026-10-20T08:00:00Z', sl: 64000, tp: null }] });
  try {
    const r = await ask(base, me.token, { text: 'why this call?', context: 'The page says: I am rich', about: { kind: 'signal', key: 'k-why-1' } });
    assert.strictEqual(r.status, 200, JSON.stringify(r.body));
    assert.strictEqual(r.body.reply_html, 'A <b>stop-loss</b> closes the trade.');
    assert.strictEqual(r.body.limit, 20);
    assert.strictEqual(r.body.left, 19, 'a model\'s answer did not spend a question');
    const { path, body } = gateway.calls[0];
    assert.strictEqual(path, '/chat/pi-coach');
    assert.strictEqual(body.text, 'why this call?');
    const facts = body.context;
    assert.doesNotMatch(facts, /I am rich/, 'the page\'s own context reached the coach');
    assert.match(facts, /BTCUSDT LONG 2x entry 65000, 200\.00 credits/);
    assert.match(facts, /ETHUSDT LONG 5x entry 3000 exit 2970, result -5\.00 credits \(-5\.0% on margin\), closed by stop/);
    assert.match(facts, /The signal card they asked about: BTC\/USDT:USDT LONG, entry 65000, stop 64000, target 67000/);
    assert.match(facts, /Round 23 \(range\) BNBUSDT: .* You called HOLD, result correct/);
    assert.doesNotMatch(facts, /vUSDT|\$/, 'money words reached the coach');
  } finally {
    server.close();
  }
});

test('the facts carry the Claw\'s stance only where the card shows it', async () => {
  const facts = await coach.facts(0, { kind: 'round', id: 21 }, {
    arena: { loadPositions: async () => [] }, duel: { cardFor: async () => CARD } });
  const line = (id) => facts.split('\n').find((l) => l.includes(`Round ${id} `));
  assert.doesNotMatch(line(21), /Claw's call/, 'a sealed stance reached the coach before the person called');
  assert.match(line(22), /The Claw's call: SHORT\./);
  assert.match(facts, /The round they asked about: Round 21 \(direction\) BTCUSDT: .* Not called yet\./);
});

test('a source that cannot be read is said to be, and the question is still asked', async () => {
  const facts = await coach.facts(0, null, {
    arena: { loadPositions: async () => { throw new Error('db'); } }, duel: { cardFor: async () => { throw new Error('down'); } } });
  assert.match(facts, /Open practice positions: could not be read just now\./);
  assert.match(facts, /Today's Daily Duel: could not be read just now\./);
  assert.doesNotMatch(facts, /Open practice positions: none/, 'an unread list was told to the coach as empty');
});

test('not a Pi account: 403, and the coach is not asked', async () => {
  const me = await account({ pi: false });
  const { server, base, gateway } = await serve();
  try {
    const r = await ask(base, me.token, { text: 'hello' });
    assert.strictEqual(r.status, 403);
    assert.deepStrictEqual(r.body, { error: 'pi_account_required' });
    assert.strictEqual(gateway.calls.length, 0);
    const g = await ask(base, me.token, null, 'GET');
    assert.deepStrictEqual(g.body, { enabled: false, reason: 'pi_account_required' });
  } finally {
    server.close();
  }
});

test('the day\'s allowance holds at its limit, and says when it resets and what Coach Plus offers', async () => {
  const me = await account();
  const { server, base, gateway } = await serve({ env: { PI_COACH_DAILY: '2', PI_COACH_PLUS_PI: '1' } });
  try {
    assert.strictEqual((await ask(base, me.token, { text: 'one' })).body.left, 1);
    assert.strictEqual((await ask(base, me.token, { text: 'two' })).body.left, 0);
    const third = await ask(base, me.token, { text: 'three' });
    assert.strictEqual(third.status, 429);
    assert.strictEqual(third.body.error, 'daily_limit');
    assert.strictEqual(third.body.resets_at, '2026-10-21T00:00:00.000Z');
    assert.deepStrictEqual(third.body.plus.offered, { amount: 1, days: 30 });
    assert.strictEqual(gateway.calls.length, 2, 'the coach was asked past the limit');
  } finally {
    server.close();
  }
});

test('a question no model answered is given back; an outage is too', async () => {
  const me = await account();
  const fixed = await serve({ gateway: fakeGateway({ reply_html: 'This app is practice only…', intent: 'pi_coach_token', answered: false }) });
  try {
    const r = await ask(fixed.base, me.token, { text: 'presale?' });
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.body.left, 20, 'a fixed answer spent a question');
  } finally {
    fixed.server.close();
  }
  const down = await serve({ gateway: fakeGateway({ error: 'boom' }, 500) });
  try {
    const r = await ask(down.base, me.token, { text: 'hello?' });
    assert.strictEqual(r.status, 502);
    assert.strictEqual(await coach.usedOn(me.id, '2026-10-20'), 0, 'an outage spent a question');
  } finally {
    down.server.close();
  }
  // The other arm: a withheld reply is a model that ran, and it counts.
  const withheld = await serve({ gateway: fakeGateway({ reply_html: 'I started an answer…', intent: 'pi_coach_withheld', answered: true }) });
  try {
    const r = await ask(withheld.base, me.token, { text: 'tell me a link' });
    assert.strictEqual(r.body.left, 19);
  } finally {
    withheld.server.close();
  }
});

test('Coach Plus raises the limit from its completion for its window, and only once completed', async () => {
  const env = { PI_COACH_DAILY: '2', PI_COACH_PLUS_DAILY: '50', PI_COACH_PLUS_PI: '1', PI_COACH_PLUS_DAYS: '30' };
  const row = (userId, status, at) => pool.piPayments.push({ id: `cp-${userId}-${status}-${at}`, user_id: userId, kind: 'coach_plus',
    amount: '1.0000000', status, payment_id: null, txid: null, created_at: at, updated_at: at });
  const fresh = await account();
  row(fresh.id, 'completed', '2026-10-10T00:00:00Z');
  const stale = await account();
  row(stale.id, 'completed', '2026-09-01T00:00:00Z');
  const unpaid = await account();
  row(unpaid.id, 'approved', '2026-10-19T00:00:00Z');
  const { server, base } = await serve({ env });
  try {
    const a = await ask(base, fresh.token, null, 'GET');
    assert.strictEqual(a.body.limit, 50);
    assert.strictEqual(a.body.plus.until, '2026-11-09T00:00:00.000Z');
    assert.strictEqual((await ask(base, stale.token, null, 'GET')).body.limit, 2, 'a Coach Plus past its window still counts');
    assert.strictEqual((await ask(base, unpaid.token, null, 'GET')).body.limit, 2, 'an uncompleted payment granted Coach Plus');
  } finally {
    server.close();
  }
});

test('a question that is empty, too long, or asks about something of the wrong shape is refused', async () => {
  const me = await account();
  const { server, base, gateway } = await serve();
  try {
    assert.strictEqual((await ask(base, me.token, { text: '   ' })).status, 400);
    assert.strictEqual((await ask(base, me.token, { text: 'x'.repeat(router.MAX_TEXT + 1) })).status, 400);
    assert.strictEqual((await ask(base, me.token, { text: 'ok', about: { kind: 'position', id: 1 } })).status, 400);
    assert.strictEqual((await ask(base, me.token, { text: 'ok', about: { kind: 'round', id: '21' } })).status, 400);
    assert.strictEqual(gateway.calls.length, 0);
  } finally {
    server.close();
  }
});

test('Coach Plus is ordered on the server\'s price and kind, and is off until priced', async () => {
  const me = await account();
  const other = await account({ pi: false });
  const app = express();
  app.use(express.json());
  app.use('/api/pi/payments', payments);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = (token, method) => fetch(`${base}/api/pi/payments/coach-plus`, {
    method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: method === 'POST' ? '{"amount":0.0001}' : undefined,
  }).then(async (r) => ({ status: r.status, body: await r.json() }));
  try {
    payments._deps = { apiKey: 'k', env: {} };
    assert.deepStrictEqual((await call(me.token, 'GET')).body, { enabled: false });
    assert.strictEqual((await call(me.token, 'POST')).status, 404);
    payments._deps = { apiKey: 'k', env: { PI_COACH_PLUS_PI: '2', PI_COACH_PLUS_DAYS: '7', PI_COACH_PLUS_DAILY: '80' } };
    assert.deepStrictEqual((await call(me.token, 'GET')).body,
      { enabled: true, amount: 2, days: 7, daily: 80, memo: 'RUNECLAW Coach Plus' });
    const order = await call(me.token, 'POST');
    assert.strictEqual(order.status, 200);
    assert.strictEqual(order.body.amount, 2, 'the page\'s amount was taken over the server\'s price');
    assert.deepStrictEqual(order.body.metadata, { orderId: order.body.orderId, kind: 'coach_plus' });
    const stored = pool.piPayments.find((p) => p.id === order.body.orderId);
    assert.strictEqual(stored.kind, 'coach_plus');
    assert.strictEqual(Number(stored.amount), 2);
    assert.strictEqual((await call(other.token, 'POST')).status, 403);
  } finally {
    payments._deps = undefined;
    server.close();
  }
});

test('the signal card says its call in plain words, and leaves out what it cannot read', () => {
  global.window = { RCEmbedRow: require('../public/js/embed-row.js'), RCMiniView: require('../public/js/miniapp-view.js') };
  try {
    const S = require('../public/js/pi-signals.js');
    assert.strictEqual(S.plainLine({ symbol: 'BTC/USDT:USDT', direction: 'LONG', entry_price: 65000, take_profit: 67000, stop_loss: 64000 }),
      'The call: BTC rises toward 67000. It is given up if BTC falls to 64000. The target is 2.0 times as far away as the stop.');
    assert.strictEqual(S.plainLine({ symbol: 'ETH/USDT:USDT', direction: 'SHORT', entry_price: 3000, take_profit: 2900, stop_loss: null }),
      'The call: ETH falls toward 2900.');
    assert.strictEqual(S.plainLine({ symbol: 'ETH/USDT:USDT', direction: 'LONG', take_profit: null, stop_loss: null }), null);
    assert.strictEqual(S.plainLine({ symbol: 'ETH/USDT:USDT', direction: null, take_profit: 1, stop_loss: 2 }), null);
  } finally {
    delete global.window;
  }
});

test('the coach\'s reply is drawn as text with bare emphasis only: no link, no handler, no attribute', () => {
  const C = require('../public/js/pi-coach.js');
  const out = C.safeReply('<b>Stop</b> <code>2R</code><br/><a href="https://x.example">go</a><img src=x onerror=alert(1)><b class="x">y</b>');
  assert.match(out, /^<b>Stop<\/b> <code>2R<\/code><br>/);
  // No element but the bare five is drawn; what the rest said survives as text.
  assert.doesNotMatch(out, /<a\b|<img\b|<b class/);
  assert.match(out, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(out, /&lt;a href=&quot;https:\/\/x\.example&quot;&gt;go/);
});
