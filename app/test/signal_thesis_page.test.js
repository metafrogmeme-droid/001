'use strict';
/**
 * The per-signal page shows the thesis and its counter-case, and nothing
 * it was not given.
 *
 * The stored column is the model's reasoning. The counter-case is the
 * "Against:" that column already contains. A blank, a provenance tag and a
 * missing column are not a thesis. A failed read is not "no thesis".
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');
const SP = require('../public/js/signal-page');
const { dollarKeys } = require('../lib/public_signal');
const { codeOnly } = require('./helpers/code_only');

const TAG = '[gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up] ';

function server(rows, { explode = false } = {}) {
  const pool = {
    execute: async (sql, params) => {
      if (explode) {
        const err = new Error('secret driver detail');
        err.name = 'QueryFailed';
        throw err;
      }
      assert.match(sql, /SELECT signal_key, symbol, direction, thesis FROM signals/);
      assert.ok(!/pnl|entry_price|seal/.test(sql), 'the thesis read selects a later sentence');
      const key = params && params[0];
      return [rows.filter((r) => r.signal_key === key)];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'signal_page.js'))];
  const app = express();
  app.use('/api/signal', require(path.join(APP, 'routes', 'signal_page.js')));
  return http.createServer(app);
}

function get(rows, url, opts) {
  return new Promise((resolve, reject) => {
    const s = server(rows, opts);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: url }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          let body = {};
          try { body = JSON.parse(b || '{}'); } catch (e) { body = { _raw: b }; }
          resolve({ status: res.statusCode, body });
        });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

const row = (over) => Object.assign({
  signal_key: 'TI-abc', symbol: 'BTC/USDT', direction: 'LONG', thesis: null,
}, over);

test('a present thesis and a present counter-case are the stored sentences', async () => {
  const { status, body } = await get(
    [row({ thesis: TAG + 'RSI 58 over VWAP. Against: funding is crowded.' })],
    '/api/signal?key=TI-abc');
  assert.strictEqual(status, 200);
  assert.strictEqual(body.thesis, 'RSI 58 over VWAP.');
  assert.strictEqual(body.counter_case, 'funding is crowded.');
  assert.strictEqual(body.symbol, 'BTC/USDT');
  assert.strictEqual(body.direction, 'LONG');
  assert.deepStrictEqual(dollarKeys(body), []);
  assert.ok(!('pnl' in body) && !('entry_price' in body) && !('seal' in body));
});

test('a missing thesis is null, and so is a tag, a blank, and an empty counter-case', async () => {
  const cases = [null, '', '   ', TAG + ' '];
  for (const thesis of cases) {
    const { status, body } = await get(
      [row({ signal_key: 'k', thesis })], '/api/signal?key=k');
    assert.strictEqual(status, 200, JSON.stringify(thesis));
    assert.strictEqual(body.thesis, null, JSON.stringify(thesis));
    assert.strictEqual(body.counter_case, null, JSON.stringify(thesis));
  }
  const only = await get(
    [row({ signal_key: 'k', thesis: 'Against: only a counter.' })],
    '/api/signal?key=k');
  assert.strictEqual(only.body.thesis, null);
  assert.strictEqual(only.body.counter_case, 'only a counter.');
  const bare = await get(
    [row({ signal_key: 'k', thesis: 'RSI 58. Against:' })],
    '/api/signal?key=k');
  assert.strictEqual(bare.body.thesis, 'RSI 58.');
  assert.strictEqual(bare.body.counter_case, null);
});

test('a slash in the id arrives as a query parameter and names that row', async () => {
  const { status, body } = await get(
    [row({ signal_key: 'BTC/USDT', thesis: 'Held the range. Against: thin book.' })],
    '/api/signal?key=' + encodeURIComponent('BTC/USDT'));
  assert.strictEqual(status, 200);
  assert.strictEqual(body.signal_key, 'BTC/USDT');
  assert.strictEqual(body.thesis, 'Held the range.');
  assert.strictEqual(body.counter_case, 'thin book.');
});

test('an unknown id is not on record, and a missing id is not a lookup', async () => {
  const missing = await get([row()], '/api/signal');
  assert.strictEqual(missing.status, 400);
  assert.strictEqual(missing.body.error, 'signal_key_required');
  const blank = await get([row()], '/api/signal?key=%20%20');
  assert.strictEqual(blank.status, 400);
  const gone = await get([row()], '/api/signal?key=nope');
  assert.strictEqual(gone.status, 404);
  assert.strictEqual(gone.body.error, 'signal_not_found');
  const long = await get([row()], '/api/signal?key=' + 'k'.repeat(129));
  assert.strictEqual(long.status, 404, 'a longer id must not be trimmed onto another row');
});

test('an unreadable record names the fault, not the driver, and carries no thesis', async () => {
  const { status, body } = await get([], '/api/signal?key=TI-abc', { explode: true });
  assert.strictEqual(status, 503);
  assert.strictEqual(body.error, 'signal_unavailable');
  const raw = JSON.stringify(body);
  assert.ok(!/secret driver|QueryFailed/.test(raw), raw);
  assert.ok(!('thesis' in body) && !('counter_case' in body));
});

test('the page says the four facts apart', () => {
  const present = SP.render({
    symbol: 'ETH/USDT', direction: 'SHORT',
    thesis: 'Lower high.', counter_case: 'A squeeze would invalidate it.',
  });
  assert.match(present, /Lower high\./);
  assert.match(present, /A squeeze would invalidate it\./);
  assert.match(present, /ETH\/USDT/);
  assert.ok(!present.includes(SP.NO_THESIS));
  assert.ok(!present.includes(SP.NO_COUNTER));
  assert.ok(!present.includes(SP.UNAVAILABLE));

  const neither = SP.render({ symbol: 'ETH/USDT', direction: 'SHORT', thesis: null, counter_case: null });
  assert.match(neither, new RegExp(SP.NO_THESIS));
  assert.match(neither, new RegExp(SP.NO_COUNTER.replace('.', '\\.')));
  assert.ok(!neither.includes(SP.UNAVAILABLE), 'a stored absence is not an unread record');

  const blank = SP.render({ thesis: '   ', counter_case: '' });
  assert.match(blank, new RegExp(SP.NO_THESIS));
  assert.match(blank, new RegExp(SP.NO_COUNTER.replace('.', '\\.')));

  const hostile = SP.render({
    thesis: '<script>alert(1)</script>', counter_case: 'ok',
  });
  assert.ok(!hostile.includes('<script>'), 'the stored sentence is escaped');
  assert.match(hostile, /&lt;script&gt;/);

  const unread = SP.render({ error: 'secret driver detail' });
  assert.match(unread, new RegExp(SP.UNAVAILABLE.replace('.', '\\.')));
  assert.ok(!unread.includes(SP.NO_THESIS), 'an unread payload must not say the thesis is absent');
  assert.ok(!unread.includes('secret driver'));

  const half = SP.render({ thesis: 'Only the thesis arrived.' });
  assert.match(half, new RegExp(SP.UNAVAILABLE.replace('.', '\\.')));
  assert.ok(!half.includes('Only the thesis arrived'),
    'a payload missing the counter-case reading is not a thesis with no counter');
});

test('the link is a query, and a row with no id does not invent one', () => {
  assert.strictEqual(SP.href('BTC/USDT'), '/signal?key=BTC%2FUSDT');
  assert.strictEqual(SP.href(''), null);
  assert.strictEqual(SP.href(null), null);
  assert.strictEqual(SP.href('   '), null);
  assert.strictEqual(
    SP.streamLink({ signal_key: 'BTC/USDT', symbol: 'ETH/USDT' }, SP.render ? ((s) => s) : String, 'Thesis'),
    ' · <a href="/signal?key=BTC%2FUSDT">Thesis</a>');
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  assert.strictEqual(
    SP.streamLink({ signal_key: 'a<b', symbol: 'ETH/USDT' }, esc, '<Thesis>'),
    ' · <a href="/signal?key=a%3Cb">Thesis</a>'.replace('Thesis', '&lt;Thesis&gt;'));
  assert.strictEqual(SP.streamLink({ symbol: 'ETH/USDT' }, esc, 'Thesis'), '');
  assert.strictEqual(SP.streamLink({ signal_key: '' }, esc, 'Thesis'), '');
  assert.strictEqual(SP.streamLink(null, esc, 'Thesis'), '');
});

test('the page reads the query before the path, and a bad encoding is unreadable', () => {
  assert.deepStrictEqual(SP.keyFrom('?key=BTC%2FUSDT', '/signal/other'), { key: 'BTC/USDT' });
  assert.deepStrictEqual(SP.keyFrom('', '/signal/TI-abc'), { key: 'TI-abc' });
  assert.deepStrictEqual(SP.keyFrom('', '/signal.html'), { missing: true });
  assert.deepStrictEqual(SP.keyFrom('?key=', '/signal/TI-abc'), { missing: true });
  assert.ok(SP.keyFrom('', '/signal/%E0%A4%A').unreadable);
});

test('the stream and the home card both open the page, and the chart click stays on the symbol', () => {
  const dash = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
  const code = codeOnly(dash);
  const calls = code.match(/SignalPage\.streamLink\(/g) || [];
  assert.strictEqual(calls.length, 2, 'home and the signal stream each open the page');
  assert.match(code, /<b>\$\{esc\(s\.symbol\)\}<\/b>/,
    'the symbol stays the chart control; the thesis link is separate');
  const html = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const pageAt = html.indexOf('/js/signal-page.js?v=');
  const dashAt = html.indexOf('/js/dashboard.js?v=');
  assert.ok(pageAt > 0 && pageAt < dashAt, 'the page module loads before the stream that calls it');
  const page = fs.readFileSync(path.join(APP, 'public', 'signal.html'), 'utf8');
  assert.match(page, /id="sig-root"/);
  assert.ok(!/tv-chart|entry_price|seal/.test(codeOnly(page)),
    'this page does not draw levels or a timeframe chart');
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.match(server, /app\.get\('\/signal'/);
  assert.match(server, /app\.get\('\/signal\/:key'/);
  assert.match(server, /app\.use\('\/api\/signal'/);
});
