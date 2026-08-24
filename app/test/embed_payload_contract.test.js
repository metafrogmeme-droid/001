'use strict';
/**
 * The embed board's readers, run against what the routes ACTUALLY send.
 *
 * THE BUG THIS FILE EXISTS FOR shipped, passed all fifteen gates, was reviewed,
 * and rendered the wrong thing on every load it ever served:
 *
 *     var sigs = (j && j.data && j.data.signals) || [];
 *
 * `/api/signals` sends `{signals:[...]}` at the top level. The `{ok,status,data}`
 * envelope belongs to `fetchJSON()` in app.js — the DASHBOARD's helper. The embed
 * page calls `fetch()` directly, so `j.data` was `undefined` on every response
 * the server has ever produced, `|| []` turned that into an empty list, and the
 * board announced **"No open signals right now."** — a confident claim that the
 * engine has found nothing, on the public page whose only job is to show that it
 * is working. The candle reader had the identical defect against the Bitget
 * relay, so every chart would have said "no candles" as well.
 *
 * WHY NOTHING CAUGHT IT. The renderer was thoroughly tested — `signal-chart.js`
 * has its own honesty suite and passes. The route was tested. The page was
 * source-scanned for its frame policy and its cache-busting. Every one of those
 * checks looked at ONE END. The defect lived in the sentence between them, and
 * a source scan cannot distinguish a reader that is present from a reader that
 * is correct. Only running the real body through the real reader can.
 *
 * That is #999 in CLAUDE.md, one layer over: code that is *present* and never
 * *reached* with real data. The cure is the same — make a seam, then drive it.
 *
 * So these tests boot the actual routers over actual HTTP, take the actual
 * bytes, and assert the reader sees what the route sent.
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const RD = require('../public/js/embed-read');

/** Mount one router, make one GET, return the parsed body. */
function serve(mountPath, router, urlPath) {
  return new Promise((resolve, reject) => {
    const app = express();
    app.use(mountPath, router);
    const srv = app.listen(0, '127.0.0.1', () => {
      http.get({ host: '127.0.0.1', port: srv.address().port, path: urlPath }, (r) => {
        let b = '';
        r.on('data', (c) => { b += c; });
        r.on('end', () => {
          srv.close();
          let j = null;
          try { j = JSON.parse(b); } catch (e) { /* leave null */ }
          resolve({ status: r.statusCode, raw: b, body: j });
        });
      }).on('error', (e) => { srv.close(); reject(e); });
    });
  });
}

// ── the contract: reader vs the real route ───────────────────────────────

test('the signal reader sees the signals the route actually sent', async () => {
  const { pool } = require('../db');
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NOW())`,
    ['contract-1', 'BTCUSDT', 'LONG', 0.71, 8, 'breakout', 'trend',
      64000, 62500, 67000, 2.0, 'contract test', 'open']);

  const r = await serve('/api/signals', require('../routes/signals'), '/api/signals?limit=8');
  assert.equal(r.status, 200, `route did not answer 200: ${r.raw.slice(0, 200)}`);

  const rows = RD.readSignals(r.body);
  assert.ok(rows.length > 0,
    'the route returned signals and the reader saw none — this is the exact '
    + `defect this file exists for. Body was: ${r.raw.slice(0, 200)}`);
  assert.ok(rows.some((s) => s.symbol === 'BTCUSDT'));
});

test('the OLD reader is proven wrong against the same real body', async () => {
  // Pinned deliberately. A future refactor that "simplifies" the reader back
  // toward the envelope shape fails here with the reason attached, rather than
  // silently restoring a board that says nothing is open.
  const r = await serve('/api/signals', require('../routes/signals'), '/api/signals?limit=8');
  const asItUsedToRead = (r.body && r.body.data && r.body.data.signals) || [];
  assert.equal(asItUsedToRead.length, 0,
    'the envelope shape is back — if this now finds rows the note below is stale');
  assert.ok(Array.isArray(r.body.signals),
    'the route stopped sending {signals:[...]} at the top level; the reader must move with it');
});

// ── three-valued: absent is not empty ────────────────────────────────────

test('an EMPTY signal list is a measurement and passes through', () => {
  // "The engine has nothing open" is a real, honest answer and must render as
  // "No open signals right now." — the reader must not confuse it with a fault.
  assert.deepEqual(RD.readSignals({ signals: [] }), []);
});

test('an ABSENT signals key throws rather than reading as none', () => {
  assert.throws(() => RD.readSignals({}), /unreadable signals/);
  assert.throws(() => RD.readSignals(null), /unreadable signals/);
  assert.throws(() => RD.readSignals('nope'), /unreadable signals/);
  assert.throws(() => RD.readSignals({ signals: 'soon' }), /unreadable signals/);
});

test('the exact body the old reader expected is now REFUSED', () => {
  // `{data:{signals:[]}}` is what the broken reader was written for. It is not
  // a shape this route produces, so accepting it would mean accepting a
  // payload from something that is not the API we think we are talking to.
  assert.throws(() => RD.readSignals({ data: { signals: [] } }), /unreadable signals/);
});

test('candles come from body.data, where the Bitget relay actually puts them', () => {
  // routes/market.js does `res.json(data)` with Bitget's own {code,msg,data}.
  const rows = [[1, '2', '3', '4', '5'], [6, '7', '8', '9', '10']];
  assert.deepEqual(RD.readCandles({ code: '00000', msg: 'success', data: rows }), rows);
  assert.deepEqual(RD.readCandles(rows), rows, 'a bare array is unambiguous');
});

test('an ABSENT candle list throws instead of rendering "no candles"', () => {
  // The distinction matters on screen: NO_CANDLES is a claim about the market,
  // UNREADABLE is a claim about the fetch. Only one of them is true here.
  assert.throws(() => RD.readCandles({ code: '00000' }), /unreadable candles/);
  assert.throws(() => RD.readCandles({ data: { data: [] } }), /unreadable candles/);
  assert.throws(() => RD.readCandles(null), /unreadable candles/);
});

test('an empty candle set is still a measurement', () => {
  assert.deepEqual(RD.readCandles({ code: '00000', data: [] }), []);
});

// ── the wiring, since a correct reader nobody calls is still broken ──────

test('embed-signals.js calls the readers and keeps no || [] fallback', () => {
  // #999: the reader being right is worth nothing if the page still holds its
  // own copy of the broken expression. Comments are stripped first — this file
  // and that one both QUOTE the defective line, and a scan that cannot tell a
  // quotation from code fails on the explanation rather than the mistake.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'embed-signals.js'), 'utf8'));

  assert.match(src, /RD\.readSignals\(/, 'the signal reader is not called');
  assert.match(src, /RD\.readCandles\(/, 'the candle reader is not called');
  assert.doesNotMatch(src, /j\.data\.signals/, 'the envelope misread is back');
  assert.doesNotMatch(src, /j\.data\.data/, 'the candle misread is back');
});

test('the embed page actually loads the reader module', () => {
  // The module can be perfect and unreachable: it is a separate <script>, and a
  // page that does not include it leaves window.RCEmbedRead undefined.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'embed.js'), 'utf8');
  assert.match(src, /embed-read\.js/,
    'routes/embed.js does not serve embed-read.js, so RCEmbedRead is undefined in the page');
});
