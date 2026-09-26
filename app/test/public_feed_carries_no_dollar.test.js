'use strict';
/**
 * The agent mind-stream is public on every reader, so it carries no dollar
 * amount of the account.
 *
 * Driven on 2026-09-26: the bot emitted every operator close as
 * `Closed BTC/USDT -$41.20` with `data: {pnl: -41.2}`, under a docstring
 * saying realized P&L "is already public on the track-record page". It is
 * not: the public track record is percent, ratio and count only
 * (routes/track.js) and indexes its curve to 100 so no account size escapes.
 * POST /events stored the title and data as sent, broadcast them on the
 * unauthenticated /api/stream, and pushed "RUNECLAW — Closed BTC/USDT
 * -$41.20" to every subscriber; GET /api/feed/recent (no auth) and the MCP
 * tool get_agent_feed served them back. public_no_dollars.test.js reads keys
 * in route files and could not see a dollar inside stored TEXT.
 *
 * The producer tells a close in percent of margin now (bot/core/agent_feed
 * close_event). This file drives the RECEIVER: `publicFeedEvent` runs at the
 * ingest and again at both readers, because the ring already holds rows
 * written before it existed. Prices are public market facts and stay, which
 * is why the rule is per field and per event type rather than a blanket
 * `$` strip.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const { publicFeedEvent } = require('../lib/public_feed');

let server, base;
const pushes = [];

function req(method, path, { botSecret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, text: d,
        data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const DOLLAR = /\$\s?[-+]?\d/;
// The exact event the bot's close producer emitted before the fix.
const OLD_CLOSE = {
  event_type: 'trade_close', severity: 'warning', symbol: 'BTC/USDT',
  title: 'Closed BTC/USDT -$41.20', body: 'Exit: SL HIT',
  data: { pnl: -41.2, reason: 'SL HIT' },
};
// An alert whose BODY carries an amount: the close's body carries none, so
// without this row a mutation that stored and pushed the raw body could not
// be seen. A warning alert is pushed, so it reaches the push too.
const IDLE_ALERT = {
  event_type: 'alert', severity: 'warning', symbol: '',
  title: 'Idle cash', body: '$250.00 of free margin sits idle',
  data: { idle_usd: 250, idle_pct: 25 },
};

test.before(async () => {
  // Capture the web push instead of sending it. The route requires the
  // module inside the handler, so replacing the export is what it reads.
  require('../lib/push').notifySubscribers = async (p) => { pushes.push(p); };
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/feed', require('../routes/feed'));
  app.use('/api/stream', require('../routes/stream').router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('a dollar P&L sent to the ingest is stored, streamed and pushed without it', async () => {
  const chunks = [];
  const sse = await new Promise((resolve, reject) => {
    const r = http.get(`${base}/api/stream`, (res) => {
      res.on('data', c => chunks.push(c.toString()));
      resolve(res);
    });
    r.on('error', reject);
  });
  const warned = [];
  const realWarn = console.warn;
  console.warn = (...a) => { warned.push(a.join(' ')); };
  let posted;
  try {
    // A null and a string in the batch are skipped, not a 500 for the batch:
    // the route used to read them through `ev?.title` and must still.
    posted = await req('POST', '/api/bot/sync/events', {
      botSecret: process.env.BOT_SYNC_SECRET,
      body: { events: [null, 'junk', OLD_CLOSE, IDLE_ALERT] },
    });
    await new Promise(res => setTimeout(res, 150));
    await new Promise(res => setImmediate(res));
  } finally {
    console.warn = realWarn;
    // An open stream keeps the server from closing, so a failed assertion
    // above would hang the run instead of reporting.
    sse.destroy();
  }
  assert.equal(posted.status, 200);
  assert.equal(posted.data.inserted, 2);

  // The producer that composed private text is named in the log, by type.
  assert.ok(warned.some(w => /removed a dollar amount from a trade_close event/.test(w)),
    `the scrub says which producer it corrected: ${JSON.stringify(warned)}`);
  assert.ok(warned.some(w => /from a alert event/.test(w)), 'the alert too');

  const raw = chunks.join('');
  assert.ok(raw.includes('event: activity'), 'the event still rides the stream');
  assert.ok(raw.includes('Closed BTC/USDT'), 'with its title');
  assert.ok(raw.includes('free margin sits idle'), 'and the alert with its words');
  assert.ok(!DOLLAR.test(raw), `no dollar figure on the public stream: ${raw}`);
  assert.ok(!raw.includes('41.2'), 'no P&L figure in any spelling');
  assert.ok(raw.includes('idle_pct'), 'a ratio in the data rides the stream');

  const push = pushes.find(p => /Closed BTC\/USDT/.test(p.title));
  assert.ok(push, 'the close is still pushed');
  assert.ok(!DOLLAR.test(push.title) && !DOLLAR.test(push.body),
    `no dollar figure in the web push: ${JSON.stringify(push)}`);
  const alertPush = pushes.find(p => /Idle cash/.test(p.title));
  assert.ok(alertPush, 'the warning alert is pushed');
  assert.ok(!DOLLAR.test(alertPush.body), `no dollar in its body: ${alertPush.body}`);

  // What was STORED, read straight out of the ring rather than through a
  // reader that scrubs again: a reader-side scrub would hide a raw write.
  const { pool } = require('../db');
  const [stored] = await pool.execute(
    'SELECT event_type, title, body, data_json FROM agent_events ORDER BY id DESC LIMIT 20');
  const close = stored.find(r => r.event_type === 'trade_close');
  const alert = stored.find(r => r.event_type === 'alert');
  assert.equal(close.title, 'Closed BTC/USDT ⋯');
  assert.deepEqual(JSON.parse(close.data_json), { reason: 'SL HIT' });
  assert.ok(!DOLLAR.test(alert.body), `stored alert body: ${alert.body}`);
  assert.deepEqual(JSON.parse(alert.data_json), { idle_pct: 25 });

  const read = await req('GET', '/api/feed/recent?limit=5');
  const ev = read.data.events.find(e => e.event_type === 'trade_close');
  assert.equal(ev.title, 'Closed BTC/USDT ⋯');
  assert.equal(ev.body, 'Exit: SL HIT');
  assert.deepEqual(ev.data, { reason: 'SL HIT' }, 'the pnl key is dropped');
});

test('a row stored before the ingest scrubbed is scrubbed on both readers', async () => {
  // Written straight into the ring, the way every close was stored before
  // this fix: the ingest cannot clean what is already there.
  const { pool } = require('../db');
  await pool.execute(
    `INSERT INTO agent_events (event_type, severity, symbol, title, body, data_json, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ['trade_close', 'success', 'ETH/USDT', 'Closed ETH/USDT +$1,234.56', null,
      JSON.stringify({ pnl: 1234.56, reason: 'TP HIT' }), new Date(Date.now() + 60_000)]);

  // The feed route micro-caches for 5s by limit, so ask a limit no earlier
  // read used.
  const read = await req('GET', '/api/feed/recent?limit=7');
  assert.equal(read.status, 200);
  const ev = read.data.events[0];
  assert.equal(ev.symbol, 'ETH/USDT');
  assert.equal(ev.title, 'Closed ETH/USDT ⋯');
  assert.deepEqual(ev.data, { reason: 'TP HIT' });
  assert.ok(!DOLLAR.test(read.text), `no dollar on /api/feed/recent: ${read.text}`);

  const { TOOLS } = require('../routes/mcp');
  const out = await TOOLS.get_agent_feed.handler({ limit: 3 });
  const top = out.events[0];
  assert.equal(top.title, 'Closed ETH/USDT ⋯');
  assert.equal(top.body, null, 'an absent body stays absent, never the word "null"');
  assert.ok(!DOLLAR.test(JSON.stringify(out)), 'no dollar on the MCP tool');
});

test('prices are public market facts and stay; a signed amount beside them does not', () => {
  const open = publicFeedEvent({
    event_type: 'trade_open', title: 'Opened LONG BTC/USDT',
    body: 'Entry $63,000.0000 · SL $62,000.0000 · TP $65,000.0000',
    data: { direction: 'LONG', confidence: 0.72 },
  });
  assert.equal(open.body, 'Entry $63,000.0000 · SL $62,000.0000 · TP $65,000.0000');
  assert.deepEqual(open.data, { direction: 'LONG', confidence: 0.72 });

  const move = publicFeedEvent({
    event_type: 'sl_move', title: 'Trailing stop moved — BTC/USDT',
    body: '$62,000.0000 → $62,500.0000', data: { old_sl: 62000, new_sl: 62500 },
  });
  assert.equal(move.body, '$62,000.0000 → $62,500.0000');
  assert.deepEqual(move.data, { old_sl: 62000, new_sl: 62500 });

  // A thesis names levels, and a range written with a hyphen keeps both
  // prices; a SIGNED dollar figure is the shape of a P&L and goes.
  const thesis = publicFeedEvent({
    event_type: 'thesis', title: 'LONG BTC/USDT — confidence 72%',
    body: 'Support $60,000-$61,000; last trade -$41.20 and +$7, then $+3 before that',
    data: { entry: 63000, sl: 62000, tp: 65000 },
  });
  assert.equal(thesis.body, 'Support $60,000-$61,000; last trade ⋯ and ⋯, then ⋯ before that');
  assert.deepEqual(thesis.data, { entry: 63000, sl: 62000, tp: 65000 });
});

test('every dollar figure is refused where no producer publishes a price', () => {
  // The title never carries a price in any producer, and the bodies of these
  // types never do either, so an unsigned figure there is an amount too.
  for (const type of ['trade_close', 'scan', 'alert', 'stance', 'info', 'arena_season']) {
    const ev = publicFeedEvent({ event_type: type, title: `Idle cash $250.00 of $1,000`,
      body: 'Balance $980.12', data: { equity_usd: 980.12, balance: 980, margin_pct: 12.5 } });
    assert.equal(ev.title, 'Idle cash ⋯ of ⋯', type);
    assert.equal(ev.body, 'Balance ⋯', type);
    assert.deepEqual(ev.data, { margin_pct: 12.5 }, `${type}: amounts dropped, the ratio kept`);
  }
  // A price-carrying type's TITLE is still a title.
  const ev = publicFeedEvent({ event_type: 'trade_open', title: 'Opened $500 LONG', body: '' });
  assert.equal(ev.title, 'Opened ⋯ LONG');
});

test('an array of rows is scrubbed like an object, and a scalar is left alone', () => {
  const arr = publicFeedEvent({ event_type: 'info', title: 't',
    data: [{ pnl_usd: 5, pnl_pct: 1.2 }] });
  assert.deepEqual(arr.data, [{ pnl_pct: 1.2 }]);
  const scalar = publicFeedEvent({ event_type: 'info', title: 't', data: 'plain' });
  assert.equal(scalar.data, 'plain');
  const none = publicFeedEvent({ event_type: 'info', title: 't' });
  assert.ok(!('data' in none) && !('body' in none), 'absent fields stay absent');
});
