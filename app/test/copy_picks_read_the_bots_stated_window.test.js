'use strict';
// Copy picks read the window the bot states, not a status nothing writes.
//
// `/api/copy/picks` and the copy push sweep selected `status = 'OPEN'`, and no
// producer ever wrote OPEN: the engine and the scan both push NEW, and nothing
// resolves a signal afterwards. So a follower's "live picks" panel read "No
// live signal matches this agent's gates right now" off a filter no row could
// satisfy, and the push sweep never sent a pick. The shim's catch-all for
// `FROM signals` ignored the WHERE, which is why every copy test passed.
//
// A signal is live while the bot would still take it: its creation plus the
// bot's PENDING_IDEA_TTL, which the PRODUCER states as `expires_at`. The
// ingest stores it beside the seal (a display window, not a decision fact),
// and both readers select `expires_at > now`. A row with no window is never
// live.
process.env.BOT_SYNC_SECRET = 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const { sealCall } = require('../lib/callseal');
const watch = require('../lib/copy_watch');

let server, base;
const BOT = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function post(p, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(base + p, { method: 'POST',
      headers: { 'Content-Type': 'application/json', ...BOT } }, (res) => {
      let d = ''; res.on('data', c => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); r.write(payload); r.end();
  });
}

const soon = () => new Date(Date.now() + 5 * 60 * 1000).toISOString();
const row = (k, extra = {}) => ({
  signal_key: k, symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.8, score: 0.8,
  pattern: 'x', regime: 'TREND_UP', entry_price: 100, stop_loss: 95, take_profit: 110,
  rr: 2, thesis: 't', status: 'NEW', pnl: null,
  created_at: new Date().toISOString(), resolved_at: '', ...extra,
});
const stored = (k) => pool.signals.find(s => s.signal_key === k);

test('the ingest stores the stated window', async () => {
  const exp = soon();
  const r = await post('/api/bot/sync/signals', { signals: [row('w1', { expires_at: exp })] });
  assert.equal(r.status, 200);
  assert.equal(new Date(stored('w1').expires_at).toISOString(), exp);
});

test('a window that does not parse is no window, and an absent one is none', async () => {
  await post('/api/bot/sync/signals', { signals: [
    row('w2', { expires_at: 'soonish' }), row('w3', {})] });
  assert.equal(stored('w2').expires_at, null);
  assert.equal(stored('w3').expires_at, null);
});

test('the window is stored beside the seal, never inside it', async () => {
  const created = new Date().toISOString();
  await post('/api/bot/sync/signals', { signals: [
    row('w4', { created_at: created, expires_at: soon() }),
    row('w5', { created_at: created })] });
  assert.ok(!/expires/.test(stored('w4').seal_payload));
  // Same decision facts, one with a window and one without: the SAME seal but
  // for the key, which is part of the sealed facts.
  const reseal = (k) => sealCall({
    signal_key: k, symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.8,
    entry_price: 100, stop_loss: 95, take_profit: 110, pattern: 'x',
    regime: 'TREND_UP', thesis: 't', created_at: new Date(created) }).seal;
  assert.equal(stored('w4').seal, reseal('w4'));
  assert.equal(stored('w5').seal, reseal('w5'));
});

test('a re-sync does not move the window', async () => {
  const first = soon();
  await post('/api/bot/sync/signals', { signals: [row('w6', { expires_at: first })] });
  await post('/api/bot/sync/signals', { signals: [row('w6', {
    expires_at: new Date(Date.now() + 99 * 60 * 1000).toISOString(), status: 'CLOSED' })] });
  assert.equal(new Date(stored('w6').expires_at).toISOString(), first);
});

test('the push sweep reads the live window through its real database read', async () => {
  watch.resetCopyWatch();
  const CAT = [{ id: 'dip', name: 'Dip', icon: 'd',
    scorecard: { gates: { confidence_threshold: 0.7 } } }];
  const sent = [];
  const deps = {
    loadFollowedAgentIds: async () => ['dip'],
    loadCatalogue: async () => CAT,
    loadFollowers: async () => [7],
    loadOptIns: async () => new Set([7]),
    // loadSignals is NOT injected: the sweep's own query is the subject.
  };
  const notify = async (payload, ids) => { sent.push({ payload, ids }); };
  await watch.sweepCopy(deps, notify);              // baseline: never notifies
  assert.equal(sent.length, 0);

  const ins = (k, sym, expires, status = 'NEW') => pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
       created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [k, sym, 'LONG', 0.9, 0.9, 'x', 'TREND_UP', 100, 95, 110, 2, '', status, null,
     new Date().toISOString(), null, expires]);
  await ins('p-live', 'ARB/USDT', soon());
  await ins('p-gone', 'OP/USDT', new Date(Date.now() - 1000).toISOString());
  await ins('p-open', 'SUI/USDT', null, 'OPEN');

  await watch.sweepCopy(deps, notify);
  const text = JSON.stringify(sent);
  assert.equal(sent.length, 1, text);
  assert.match(text, /ARB/);
  assert.doesNotMatch(text, /OP\b|SUI/);
});

test('both readers select on the window and neither on a status', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  for (const f of ['routes/copy.js', 'lib/copy_watch.js']) {
    const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'));
    assert.match(src, /FROM signals WHERE expires_at > \?/, f);
    assert.doesNotMatch(src, /status = \?/, f);
  }
});

test('the push sweep runs well inside the live window it reads', () => {
  // A signal is live for the bot's idea TTL (5 minutes by default). A sweep as
  // long as that window can miss a signal to timer drift; once a minute cannot.
  const realSetInterval = global.setInterval;
  const realSetTimeout = global.setTimeout;
  let every = null;
  global.setInterval = (fn, ms) => { every = ms; return 0; };
  global.setTimeout = () => 0;
  try {
    watch.startCopyWatch();
  } finally {
    global.setInterval = realSetInterval;
    global.setTimeout = realSetTimeout;
  }
  assert.ok(every !== null && every <= 60 * 1000, `swept every ${every}ms`);
});
