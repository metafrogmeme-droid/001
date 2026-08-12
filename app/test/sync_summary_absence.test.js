'use strict';
/**
 * The ingest overruled the bot about what it had measured.
 *
 * `sync_scan_ingest_null_equity.test.js` in this directory fixed exactly this
 * for ONE field. The bot flags an unreadable live balance and the ingest was
 * stamping it back to 0; the fix taught the ingest to honour the null, with a
 * comment stating the invariant. The two lines directly beneath it kept
 *
 *     net_pnl: cb.net_pnl || 0,
 *     win_rate: cb.win_rate || 0,
 *
 * which is the same defect, one line down — the failure `win_rate.py`'s own
 * docstring names ("the RATE was cured and the TOTAL printed beside it was
 * not"). It did not bite while the bot only ever sent zeros. It bites now:
 * the bot sends `null` for a record it could not price, and `|| 0` turns
 * "not measured" into "$0.00 net, 0% win rate" before the browser ever sees
 * it. `|| 0` also eats a genuine 0.0 — a real flat book and a real 0% win
 * rate — so it is wrong in both directions at once.
 *
 * Both writers of the in-memory summary are exercised, because the reason
 * the equity fix needed a second round was that only one of them was cured
 * and the other stamped over it seconds later.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

let server, base;
const SECRET = process.env.BOT_SYNC_SECRET;
const TOKEN = jwt.sign({ user_id: 1, email: 'op@test.dev' }, process.env.JWT_SECRET);

function req(method, p, body, { token } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(data ? { 'Content-Type': 'application/json',
                     'Content-Length': Buffer.byteLength(data) } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        'X-Bot-Secret': SECRET,
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}
const post = (p, b) => req('POST', p, b);
const get = (p, o) => req('GET', p, undefined, o);

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

const summary = async () => (await get('/api/bot/sync/portfolio-summary',
  { token: TOKEN })).data.portfolio;

test('an unpriceable record survives the scan ingest as null', async () => {
  const r = await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, equity: 884.31,
      // Twelve closes, none of them priceable — the bot says so.
      net_pnl: null, win_rate: null, total_trades: 12, open_count: 1,
    },
  });
  assert.equal(r.status, 200);
  const pf = await summary();
  assert.strictEqual(pf.net_pnl, null,
    'THE defect: "$0.00 net" on a record nothing could price');
  assert.strictEqual(pf.win_rate, null,
    'THE defect: "0% win rate" claims every trade lost');
  assert.strictEqual(pf.total_trades, 12, 'the count is real and must survive');
  assert.strictEqual(pf.equity, 884.31);
});

test('a genuinely flat book is NOT hidden by the same change', async () => {
  // The other direction. `|| 0` erased nulls; a fix that erases zeros is the
  // same defect facing the other way — 0.00 net over 8 closed trades is a
  // real, measured, break-even result.
  await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, equity: 1000, net_pnl: 0, win_rate: 0,
      total_trades: 8, open_count: 0,
    },
  });
  const pf = await summary();
  assert.strictEqual(pf.net_pnl, 0, 'a measured break-even was thrown away');
  assert.strictEqual(pf.win_rate, 0, 'eight closes, none won — that IS 0%');
  assert.strictEqual(pf.total_trades, 8);
});

test('an unreadable closed-trade record is flagged, not shown as zero trades', async () => {
  await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, equity: 884.31, net_pnl: null, win_rate: null,
      total_trades: 0, open_count: 0, record_unreadable: true,
    },
  });
  const pf = await summary();
  assert.strictEqual(pf.record_unreadable, true,
    '"0 trades at 0% win rate" reads as a measured record of failure rather '
    + 'than the absence of one');
  assert.strictEqual(pf.net_pnl, null);
});

test('the full-replace path counts the same way the scan path does', async () => {
  // POST / rebuilds the summary from the trade list itself, with its own
  // arithmetic. It used to sum `parseFloat(t.pnl) || 0` and divide wins by
  // the full count — both banned shapes, on adjacent lines.
  const r = await post('/api/bot/sync', {
    user_id: 1,
    equity: 500,
    positions: [],
    closed_trades: [
      { symbol: 'BTCUSDT', pnl: 40 },
      { symbol: 'ETHUSDT', pnl: -10 },
      { symbol: 'SOLUSDT', pnl: null },   // unpriced: neither side of the ratio
      { symbol: 'TAOUSDT', pnl: 0 },      // break-even: scored, not a win
    ],
  });
  assert.equal(r.status, 200);
  const pf = await summary();
  assert.strictEqual(pf.net_pnl, 30, 'the unpriced close was summed as a zero');
  assert.strictEqual(pf.win_rate, (1 / 3) * 100,
    'the unpriced close was scored as a loss');
  assert.strictEqual(pf.total_trades, 4, 'the count is of closes, not of prices');
  assert.strictEqual(pf.scored_trades, 3);
  assert.strictEqual(pf.unpriced_trades, 1);
});

test('the full-replace path reports null when it can price nothing', async () => {
  await post('/api/bot/sync', {
    user_id: 1, equity: 500, positions: [],
    closed_trades: [{ symbol: 'BTCUSDT', pnl: null }, { symbol: 'ETHUSDT' }],
  });
  const pf = await summary();
  assert.strictEqual(pf.net_pnl, null);
  assert.strictEqual(pf.win_rate, null);
  assert.strictEqual(pf.total_trades, 2);
});

test('an anonymous caller still sees the rate and never the dollars', async () => {
  // The redaction contract has to survive the new keys: `scored_trades` and
  // `unpriced_trades` are counts, `net_pnl` is money.
  await post('/api/bot/sync/scan', {
    symbols: {},
    circuit_breaker: {
      live_mode: true, equity: 884.31, net_pnl: 120.5, win_rate: 61.5,
      total_trades: 26, open_count: 2,
    },
  });
  const anon = (await get('/api/bot/sync/portfolio-summary')).data.portfolio;
  assert.ok(!('net_pnl' in anon) || anon.net_pnl == null,
    'a dollar amount reached an anonymous caller');
  assert.ok(!('equity' in anon) || anon.equity == null);
  assert.strictEqual(anon.win_rate, 61.5, 'rates are public; they were redacted');
  assert.strictEqual(anon.total_trades, 26);
});
