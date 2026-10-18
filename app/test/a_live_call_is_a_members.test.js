'use strict';
/**
 * A live call is a signed-in member's; a resolved call is everyone's.
 *
 * The engine's calls were public. Nine anonymous surfaces handed out a live
 * call's side, entry, stop and target the moment it was made: the signal
 * stream, the sealed-call receipt, the scan's entry cards, the mind-stream
 * feed and its live stream, the MCP and ERC-8257 tool doors, the embeddable
 * board, a strategy's "scan now" plan and the flight records. A push to a
 * phone carried the levels too, and a push subscription outlives a sign-out.
 *
 * One reading decides it (lib/signal_access.js) and every route asks it, so
 * this file drives every route twice: once as a visitor, who must not read
 * the live call's levels anywhere in the answer, and once as a member, who
 * must. The fixture's live call is a SHORT at prices that occur nowhere else
 * (171.2345 / 175.6789 / 160.1357) with a thesis word that occurs nowhere
 * else, so an absence is an absence of THAT call and a presence is THAT call.
 * The resolved call beside it is a LONG, published in full to anyone.
 *
 * THE SAMPLE (2026-10-18). The two lists a visitor browses, the signal
 * stream and the scan's setup cards, show a visitor the newest TEASER_COUNT
 * live calls in full. The fixture's live call is the newest, so it is in
 * the sample there; FOURTH, the fourth-newest live call, carries its own
 * fingerprints and is what those two doors must keep from a visitor. Every
 * other door still keeps the live call itself.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const A = require('../lib/signal_access');

// Every push the ingest sends, recorded instead of delivered. Installed
// before the sync route loads it (it is required lazily, per event).
const pushes = [];
const pushPath = require.resolve('../lib/push');
require.cache[pushPath] = { id: pushPath, filename: pushPath, loaded: true,
  exports: { notifySubscribers: async (payload) => { pushes.push(payload); return 1; } } };

const gateway = require('../lib/gateway');

const LIVE = {
  signal_key: 'live:sol:1', symbol: 'SOL/USDT', direction: 'SHORT',
  confidence: 0.83, entry_price: 171.2345, stop_loss: 175.6789,
  take_profit: 160.1357, rr: 2.4, thesis: 'Thesis LIVEXYZ. Against: none.',
  status: 'NEW', created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 3600e3).toISOString(),
};
const DONE = {
  signal_key: 'done:eth:1', symbol: 'ETH/USDT', direction: 'LONG',
  confidence: 0.7, entry_price: 2345.6781, stop_loss: 2300.5, take_profit: 2450.25,
  rr: 2.3, thesis: 'Thesis DONEABC.', status: 'TARGET', pnl: 2.3,
  created_at: new Date(Date.now() - 86400e3).toISOString(),
  resolved_at: new Date().toISOString(),
};
const older = (s, key, sym, px, word) => ({
  signal_key: key, symbol: sym, direction: 'LONG', confidence: 0.6,
  entry_price: px[0], stop_loss: px[1], take_profit: px[2], rr: 2,
  thesis: `Thesis ${word}.`, status: 'NEW', created_at: new Date(Date.now() - s * 1000).toISOString(),
  expires_at: new Date(Date.now() + 3600e3).toISOString(),
});
const EXTRA1 = older(10, 'live:avax:2', 'AVAX/USDT', [21.1113, 20.5557, 22.9991], 'EXTRAONE');
const EXTRA2 = older(20, 'live:link:3', 'LINK/USDT', [14.2229, 13.8883, 15.4447], 'EXTRATWO');
const FOURTH = older(30, 'live:dot:4', 'DOT/USDT', [31.4159, 29.7182, 34.9792], 'FOURTHQRS');
// The live call's fingerprints. None may reach a visitor; all reach a member.
const SECRETS = ['171.2345', '175.6789', '160.1357', 'LIVEXYZ'];
// The fourth-newest live call's: outside the sample, so a visitor reads none.
const FOURTH_SECRETS = ['31.4159', '29.7182', '34.9792', 'FOURTHQRS'];
const LIST_DOORS = new Set(['the signal stream', 'the scan']);
const BOT = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };
const MEMBER = { Authorization: 'Bearer ' + jwt.sign(
  { user_id: 4242, email: 'member@test.dev' }, process.env.JWT_SECRET) };

let server, base;

function req(method, p, { body, headers } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: { ...(payload ? { 'Content-Type': 'application/json' } : {}), ...(headers || {}) },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(d); } catch (_) { /* not JSON */ }
        resolve({ status: res.statusCode, data: j, raw: d, cache: res.headers['cache-control'] || '' });
      });
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

function leaked(raw, secrets = SECRETS) {
  return secrets.filter((s) => String(raw).includes(s));
}

test.before(async () => {
  gateway.isConfigured = () => true;
  gateway.getGateway = async () => ({ status: 200, data: { public: true, board: {
    preset: 'sol trend', label: 'SOL trend', command: '/soltrend', rule: 'ma_trend',
    bar_close: '10:00 UTC', read: ['SOL'], unread: [], places: false,
    rows: [{ symbol: 'SOL', fast: 171.2345, slow: 175.6789, side: 'SHORT' }],
    verdict: 'The averages name: short SOL at 171.2345.',
  } } });
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/signals', require('../routes/signals'));
  app.use('/api/signal', require('../routes/signal_page'));
  app.use('/api/call', require('../routes/call'));
  app.use('/api/feed', require('../routes/feed'));
  app.use('/api/stream', require('../routes/stream').router);
  app.use('/api/public/flight', require('../routes/public_flight'));
  app.use('/api/guardian', require('../routes/guardian'));
  app.use('/api/public/strategies', require('../routes/public_strategies'));
  app.use('/mcp', require('../routes/mcp'));
  app.use('/', require('../routes/tool8257'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;

  const s = await req('POST', '/api/bot/sync/signals', { headers: BOT, body: { signals: [LIVE, EXTRA1, EXTRA2, FOURTH, DONE] } });
  assert.equal(s.status, 200);
  const sc = await req('POST', '/api/bot/sync/scan', { headers: BOT, body: {
    regime: 'trend', timestamp: new Date().toISOString(),
    symbols: { SOLUSDT: { price: 170, rsi: 61, direction: 'SHORT', score: 0.9,
      status: 'setup', status_label: 'SETUP' } },
    entry_cards: [{ symbol: 'SOL', direction: 'SHORT', entry: '171.2345',
      stop_loss: '175.6789', tp1: '160.1357', thesis: 'SHORT bias LIVEXYZ' },
    { symbol: 'AVAX', direction: 'LONG', entry: '21.1113', stop_loss: '20.5557', tp1: '22.9991' },
    { symbol: 'LINK', direction: 'LONG', entry: '14.2229', stop_loss: '13.8883', tp1: '15.4447' },
    { symbol: 'DOT', direction: 'LONG', entry: '31.4159', stop_loss: '29.7182', tp1: '34.9792',
      thesis: 'LONG bias FOURTHQRS' }],
    entry_cards_read: { results: 4, above_floor: 4, considered: 4, cards: 4 },
    deepscan: { tf: '4h', count: 5, hits: ['BTC', 'ETH', 'SOL', 'DOGE', 'PEPE'].map((b, i) => ({
      symbol: `${b}/USDT`, price: 100 + i, chart_patterns: [{ name: `Pattern${b}DSQ`, signal: 'bullish', confidence: 60 }] })) },
    circuit_breaker: { open_positions: [{ symbol: 'SOLUSDT', direction: 'SHORT',
      entry_price: 171.2345, stop_loss: 175.6789 }], open_count: 1 },
  } });
  assert.equal(sc.status, 200);
  const fl = await req('POST', '/api/bot/sync/flight', { headers: BOT, body: {
    records: [
      { decision_id: 'd-live', symbol: 'SOL/USDT', outcome: 'EXECUTED_LIVE', result: null,
        idea: { direction: 'SHORT', entry: 171.2345, sl: 175.6789, tp: 160.1357, reasoning: 'LIVEXYZ' },
        explanation: { narrative: 'Shorted SOL at 171.2345' } },
      { decision_id: 'd-rej', symbol: 'BTC/USDT', outcome: 'REJECTED_RISK', result: null,
        idea: { direction: 'LONG', entry: 99111.5, sl: 98000, tp: 101000, reasoning: 'refused REJXYZ' } },
      { decision_id: 'd-done', symbol: 'ETH/USDT', outcome: 'EXECUTED_LIVE',
        result: { pnl_pct: 1.2, reason: 'take_profit' },
        idea: { direction: 'LONG', entry: 2345.6781, reasoning: 'closed DONEABC' } },
    ],
    chain: { ok: true, length: 3 },
  } });
  assert.equal(fl.status, 200);
});
test.after(() => { if (server) server.close(); });

// ── the reading ─────────────────────────────────────────────────────────

test('only `true` opens a live call: a truthy stand-in does not', () => {
  for (const notTrue of [1, 'yes', {}, { user_id: 1 }, null, undefined]) {
    const out = A.signalFor(notTrue, LIVE);
    assert.equal(out.locked, true, `${JSON.stringify(notTrue)} opened a live call`);
    assert.deepEqual(leaked(JSON.stringify(out)), []);
  }
  assert.equal(A.signalFor(true, LIVE).entry_price, 171.2345);
});

test('resolved is a terminal word this build knows, or an outcome read', () => {
  assert.equal(A.signalResolved({ status: 'TARGET' }), true);
  assert.equal(A.signalResolved({ status: 'expired' }), true, 'case is the model\'s, not the row\'s');
  assert.equal(A.signalResolved({ status: 'NEW', pnl: -1 }), true, 'an outcome read is a resolution');
  assert.equal(A.signalResolved({ status: 'NEW', pnl: 0 }), true, 'a measured break-even is an outcome');
  assert.equal(A.signalResolved({ status: 'NEW' }), false);
  assert.equal(A.signalResolved({ status: 'OPEN', pnl: null }), false);
  assert.equal(A.signalResolved({ status: 'SOMETHING_NEW' }), false, 'an unknown word stays locked');
  assert.equal(A.signalResolved({}), false);
});

test('a locked row names its market and time, and nothing a filter could read', () => {
  const out = A.signalFor(false, LIVE);
  assert.deepEqual(Object.keys(out).sort(),
    ['created_at', 'expires_at', 'locked', 'outcome', 'signal_key', 'status', 'symbol'].sort());
  assert.equal(A.signalFor(false, DONE).entry_price, 2345.6781, 'a resolved call is everyone\'s');
});

test('a refused decision was never a call; an executed one without a result is live', () => {
  const live = A.flightRecordFor(false, { outcome: 'EXECUTED_LIVE', result: null, idea: { entry: 1 } });
  assert.deepEqual(live.idea, { locked: true });
  const failed = A.flightRecordFor(false, { outcome: 'EXECUTION_FAILED', idea: { entry: 1 } });
  assert.deepEqual(failed.idea, { locked: true }, 'a failed attempt leaves its idea pending');
  const refused = A.flightRecordFor(false, { outcome: 'REJECTED_RISK', result: null, idea: { entry: 1 } });
  assert.equal(refused.idea.entry, 1);
  const closed = A.flightRecordFor(false, { outcome: 'EXECUTED_LIVE', result: { r: 1 }, idea: { entry: 1 } });
  assert.equal(closed.idea.entry, 1);
});

test('a feed event names its kind and market; only a live call\'s kinds are locked', () => {
  for (const t of ['thesis', 'trade_open', 'sl_move']) {
    const out = A.feedEventFor(false, { event_type: t, symbol: 'SOL/USDT',
      title: 'SHORT SOL/USDT 171.2345', body: '175.6789', data: { entry: 160.1357 } });
    assert.equal(out.locked, true, t);
    assert.deepEqual(leaked(JSON.stringify(out)), [], t);
    assert.ok(!/SHORT/.test(JSON.stringify(out)), `${t} told a visitor the side`);
    assert.match(out.title, /SOL\/USDT/);
  }
  const close = { event_type: 'trade_close', title: 'Closed SOL/USDT +1.20% on margin', data: {} };
  assert.deepEqual(A.feedEventFor(false, close), close, 'a close is history');
});

// ── every door, both arms ───────────────────────────────────────────────

const DOORS = [
  ['the signal stream', '/api/signals?limit=50'],
  ['the sealed receipt', '/api/call/' + encodeURIComponent(LIVE.signal_key)],
  ['the front door\'s receipt', '/api/call/latest'],
  ['the thesis page', '/api/signal?key=' + encodeURIComponent(LIVE.signal_key)],
  ['the scan', '/api/bot/sync/scan'],
  ['the public flight recorder', '/api/public/flight'],
  ['one public flight record', '/api/public/flight/d-live'],
  ['the guardian flight view', '/api/guardian/flight'],
  ['one guardian flight record', '/api/guardian/flight/d-live'],
  ['a strategy\'s scan', '/api/public/strategies/scan?id=sol-trend'],
];

for (const [name, url] of DOORS) {
  // A list door samples the newest live calls; the call it must keep is the
  // one past the sample. Every other door keeps the live call itself.
  const secrets = LIST_DOORS.has(name) ? FOURTH_SECRETS : SECRETS;
  test(`${name}: a visitor reads no live call past the sample; a member reads it`, async () => {
    const visitor = await req('GET', url);
    assert.equal(visitor.status, 200, `${name} answered ${visitor.status}: ${visitor.raw.slice(0, 200)}`);
    assert.deepEqual(leaked(visitor.raw, secrets), [], `${name} handed a visitor the live call`);
    assert.match(visitor.cache, /private|no-cache/, `${name}'s answer depends on the viewer`);
    assert.ok(!/public/.test(visitor.cache), `${name} lets a shared cache keep a viewer's answer`);
    const member = await req('GET', url, { headers: MEMBER });
    assert.equal(member.status, 200);
    assert.ok(leaked(member.raw, secrets).length > 0, `${name} withheld the live call from a member`);
  });
}

// ── the sample a visitor is shown ───────────────────────────────────────

test('the stream: a visitor sees the newest three live calls in full and the rest locked', async () => {
  const v = await req('GET', '/api/signals?limit=50');
  const byKey = Object.fromEntries(v.data.signals.map((r) => [r.signal_key, r]));
  for (const k of [LIVE.signal_key, EXTRA1.signal_key, EXTRA2.signal_key]) {
    assert.equal(byKey[k].teaser, true, `${k} is in the sample`);
    assert.ok(byKey[k].entry_price != null && byKey[k].direction, `${k} is shown in full`);
  }
  assert.equal(byKey[FOURTH.signal_key].locked, true);
  assert.equal(byKey[DONE.signal_key].teaser, undefined, 'a resolved call is everyone\'s, not a sample');
  assert.deepEqual(v.data.teaser, { shown: 3, locked: 1, of: A.TEASER_COUNT, join: A.JOIN_HREF });
  const m = await req('GET', '/api/signals?limit=50', { headers: MEMBER });
  assert.equal(m.data.teaser, undefined, 'a member is shown everything, not a sample');
  assert.ok(!m.raw.includes('"teaser":true') && !m.raw.includes('"locked":true'));
});

test('a filter does not choose the sample: the call past it stays locked when asked for by name', async () => {
  const v = await req('GET', '/api/signals?symbol=' + encodeURIComponent(FOURTH.symbol));
  const row = v.data.signals.find((r) => r.signal_key === FOURTH.signal_key);
  assert.ok(row, 'the filter returned the call it named');
  assert.equal(row.locked, true);
  assert.deepEqual(leaked(v.raw, FOURTH_SECRETS), []);
});

test('the scan: the first three setup cards are the sample, and so are the first three pattern reads', async () => {
  const v = (await req('GET', '/api/bot/sync/scan')).data.scan;
  assert.deepEqual(v.entry_cards.map((c) => c.teaser === true), [true, true, true, false]);
  assert.deepEqual(v.entry_cards[3], { symbol: 'DOT', locked: true });
  assert.deepEqual(v.entry_cards_teaser, { shown: 3, locked: 1, of: 3, join: A.JOIN_HREF });
  assert.deepEqual(v.deepscan.hits.map((h) => h.locked === true), [false, false, false, true, true]);
  assert.ok(!JSON.stringify(v.deepscan.hits.slice(3)).includes('DSQ'), 'a locked read names its market only');
  assert.deepEqual(v.deepscan.teaser, { shown: 3, locked: 2, of: 3, join: A.JOIN_HREF });
  assert.equal(v.symbols.SOLUSDT.direction, 'SHORT', 'a sampled card\'s market keeps the side its card shows');
  const m = (await req('GET', '/api/bot/sync/scan', { headers: MEMBER })).data.scan;
  assert.equal(m.entry_cards.length, 4);
  assert.ok(m.entry_cards.every((c) => c.locked !== true && c.teaser !== true));
  assert.ok(m.deepscan.hits.every((h) => h.locked !== true && h.teaser !== true));
});

test('the feed: a visitor reads that a call was made; a member reads the call', async () => {
  const ev = await req('POST', '/api/bot/sync/events', { headers: BOT, body: { events: [
    { event_type: 'thesis', symbol: 'SOL/USDT', title: 'SHORT SOL/USDT — confidence 83',
      body: 'Thesis LIVEXYZ', data: { direction: 'SHORT', entry: 171.2345, sl: 175.6789, tp: 160.1357 } },
  ] } });
  assert.equal(ev.status, 200);
  const visitor = await req('GET', '/api/feed/recent?limit=10');
  assert.deepEqual(leaked(visitor.raw), []);
  const said = visitor.data.events.find((e) => e.event_type === 'thesis');
  assert.equal(said.title, 'New call on SOL/USDT');
  const member = await req('GET', '/api/feed/recent?limit=10', { headers: MEMBER });
  assert.ok(leaked(member.raw).includes('171.2345'));
});

test('a filter on a hidden field answers over resolved calls only, for a visitor', async () => {
  for (const q of ['direction=SHORT', 'direction=LONG', 'min_confidence=0.8', 'min_confidence=0.1']) {
    const visitor = await req('GET', '/api/signals?' + q);
    const keys = visitor.data.signals.map((s) => s.signal_key);
    assert.ok(!keys.includes(LIVE.signal_key), `?${q} told a visitor something about the live call`);
  }
  const longs = await req('GET', '/api/signals?direction=LONG');
  assert.ok(longs.data.signals.some((s) => s.signal_key === DONE.signal_key), 'resolved calls still filter');
  const member = await req('GET', '/api/signals?direction=SHORT', { headers: MEMBER });
  assert.ok(member.data.signals.some((s) => s.signal_key === LIVE.signal_key), 'a member\'s filter is whole');
  const all = await req('GET', '/api/signals');
  const row = all.data.signals.find((s) => s.signal_key === FOURTH.signal_key);
  assert.equal(row.locked, true, 'unfiltered, a visitor sees that the call past the sample exists');
  assert.equal(row.symbol, 'DOT/USDT');
  // A hidden-field filter answers over resolved calls only, sample or not.
  for (const q of ['direction=SHORT', 'min_confidence=0.1']) {
    const v = await req('GET', '/api/signals?' + q);
    assert.ok(v.data.signals.every((s) => s.teaser !== true), `?${q} answered with a live call`);
  }
});

test('the MCP door: a keyed caller reads the live call; a keyless one does not', async () => {
  const { handleRpc } = require('../routes/mcp');
  for (const [tool, args] of [['get_signals', {}], ['get_flight_record', {}],
    ['get_agent_feed', {}], ['verify_call', { key: LIVE.signal_key }]]) {
    const call = (ctx) => handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/call',
      params: { name: tool, arguments: args } }, ctx);
    const visitor = JSON.stringify(await call({ userId: null, ip: '127.0.0.1' }));
    assert.deepEqual(leaked(visitor), [], `${tool} handed a keyless caller the live call`);
    const member = JSON.stringify(await call({ userId: 7, ip: '127.0.0.1' }));
    assert.ok(leaked(member).length > 0, `${tool} withheld the live call from a keyed caller`);
  }
});

test('the ERC-8257 door is public and reads as a visitor', async () => {
  for (const tool of ['get_signals', 'get_flight_record', 'get_agent_feed']) {
    const r = await req('POST', '/api/tool/invoke', { body: { tool, args: {} }, headers: MEMBER });
    assert.equal(r.status, 200, tool);
    assert.deepEqual(leaked(r.raw), [], `${tool} on the public dispatcher handed out the live call`);
  }
});

function openStream(headers) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}/api/stream`, { headers: { Accept: 'text/event-stream', ...(headers || {}) } },
      (res) => {
        const got = { text: '', res };
        res.setEncoding('utf8');
        res.on('data', (c) => { got.text += c; });
        resolve(got);
      });
    r.on('error', reject);
    r.end();
  });
}

test('the live stream: a visitor\'s connection gets the locked event, a member\'s the call', async () => {
  const visitor = await openStream();
  const member = await openStream(MEMBER);
  await new Promise((r) => setTimeout(r, 50));
  pushes.length = 0;
  const ev = await req('POST', '/api/bot/sync/events', { headers: BOT, body: { events: [
    { event_type: 'trade_open', symbol: 'SOL/USDT', title: 'Opened SHORT SOL/USDT',
      body: 'Entry 171.2345 · SL 175.6789 · TP 160.1357', severity: 'success',
      data: { direction: 'SHORT', entry: 171.2345, sl: 175.6789, tp: 160.1357 } },
  ] } });
  assert.equal(ev.status, 200);
  await new Promise((r) => setTimeout(r, 100));
  visitor.res.destroy();
  member.res.destroy();
  assert.match(visitor.text, /event: activity/, 'the visitor was told something happened');
  assert.deepEqual(leaked(visitor.text), [], 'the stream handed a visitor the levels');
  assert.ok(!/SHORT/.test(visitor.text), 'the stream told a visitor the side');
  assert.ok(leaked(member.text).includes('171.2345'), 'the stream withheld the call from a member');
  // The push to a phone carries the market, never the side or the levels: a
  // subscription outlives a sign-out, and a lock screen is anyone's.
  await new Promise((r) => setImmediate(r));
  const opened = pushes.find((p) => /SOL\/USDT/.test(p.title));
  assert.ok(opened, 'the open was pushed');
  assert.deepEqual(leaked(JSON.stringify(opened)), []);
  assert.ok(!/SHORT/.test(JSON.stringify(opened)));
});

test('a resolved call is everyone\'s, on every door that lists it', async () => {
  const stream = await req('GET', '/api/signals');
  const done = stream.data.signals.find((s) => s.signal_key === DONE.signal_key);
  assert.equal(done.entry_price, 2345.6781);
  assert.equal(done.direction, 'LONG');
  const receipt = await req('GET', '/api/call/' + encodeURIComponent(DONE.signal_key));
  assert.ok(receipt.data.seal_payload, 'a resolved receipt is revealed, so anyone can check it');
  const flight = await req('GET', '/api/public/flight/d-done');
  assert.equal(flight.data.record.idea.entry, 2345.6781);
  const refused = await req('GET', '/api/public/flight/d-rej');
  assert.equal(refused.data.record.idea.entry, 99111.5, 'a refused idea was never a call');
});
