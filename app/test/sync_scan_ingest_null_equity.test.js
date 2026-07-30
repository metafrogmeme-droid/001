'use strict';
/**
 * The careful null lived on the path that almost never runs.
 *
 * THE DEFECT
 *
 * When the bot cannot read the live exchange balance it flags the account
 * UNAVAILABLE in the scan payload: `circuit_breaker.live_unavailable: true`,
 * `equity: null`. The GET /portfolio-summary cold-start path honors that
 * ("Preserve null … never coerce it to 0"), because a live account rendering
 * "$0.00" reads as "account wiped" — a fabricated and alarming number.
 *
 * But the summary is cached in memory, and the cache is written by TWO paths.
 * The scan-ingest path (POST /scan) — which runs on EVERY bot scan sync —
 * built it with `equity: cb.equity || 0` and dropped `live_unavailable` and
 * `mode` entirely. So the cold path's careful null was stamped back to 0
 * seconds later by the hot path, and the dashboard's "live account
 * unavailable" state (which keys off `live_unavailable`) could never show
 * from ingested data.
 *
 * Same bug one level down from the redaction fix in this directory: one path
 * missing what its sibling has, where the sibling's comment states the
 * invariant the other path silently violates.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const syncSrc = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');

let server, base;
const SECRET = process.env.BOT_SYNC_SECRET;
const TOKEN = jwt.sign({ user_id: 1, email: 'op@test.dev' }, process.env.JWT_SECRET);

function post(p, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const r = http.request(`${base}${p}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data),
        'X-Bot-Secret': SECRET,
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

function get(p, { token } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, {
      method: 'GET',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('an unavailable live balance survives the scan-ingest path as null', async () => {
  // The exact payload the bot sends when the exchange balance is unreadable.
  // The scan payload is the POST body itself (see the bot's sync client) —
  // not wrapped in a {scan: ...} envelope.
  const r = await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, live_unavailable: true, equity: null,
      net_pnl: 0, win_rate: 0, total_trades: 12, open_count: 2,
    },
  });
  assert.equal(r.status, 200);

  const s = await get('/api/bot/sync/portfolio-summary', { token: TOKEN });
  assert.equal(s.status, 200);
  const pf = s.data.portfolio;
  assert.ok(pf, 'ingest should have produced a summary');
  // THE defect: this was 0.
  assert.equal(pf.equity, null,
    `an unreadable live balance must be null, got ${pf.equity} — ` +
    '"$0.00" on a live account reads as "account wiped"');
  assert.equal(pf.live_unavailable, true,
    'the dashboard keys its "live account unavailable" state off this flag');
  assert.equal(pf.mode, 'LIVE');
  // The counts are real and must survive — a summary is made of them.
  assert.equal(pf.total_trades, 12);
  assert.equal(pf.open_count, 2);
});

test('a readable live balance still flows through unchanged', async () => {
  const r = await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, live_unavailable: false, equity: 884.31,
      net_pnl: 12.5, win_rate: 55.0, total_trades: 20, open_count: 1,
    },
  });
  assert.equal(r.status, 200);
  const s = await get('/api/bot/sync/portfolio-summary', { token: TOKEN });
  const pf = s.data.portfolio;
  assert.equal(pf.equity, 884.31);
  assert.equal(pf.live_unavailable, false);
  assert.equal(pf.mode, 'LIVE');
});

test('both cache writers build the same shape', () => {
  // The defect was divergence: the GET cold path preserved null while the
  // POST ingest path coerced to 0. Pin that BOTH now carry the guard, so a
  // future edit to one can't silently reopen the gap in the other.
  const guards = syncSrc.match(/cb\.live_unavailable \? null : \(cb\.equity \|\| 0\)/g) || [];
  assert.equal(guards.length, 2,
    `expected the null-preserving equity guard on both cache writers, found ${guards.length}`);
});
