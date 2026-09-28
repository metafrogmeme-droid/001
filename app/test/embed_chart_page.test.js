'use strict';
/**
 * The live chart page a Telegram signal links to (`/embed/chart`).
 *
 * The bot builds the link (`chart_renderer.live_chart_url`) and the page reads
 * it, so this file drives the reading end: what the page makes of a link it can
 * read, and of one it cannot. A symbol it cannot place is a link to no market,
 * and must never reach the candle route or the page as markup. A level that is
 * not a positive number is not a level, and must not be drawn at zero. A level
 * that IS one is printed as the signal published it: the axis precision alone
 * rounded 15.0885 to 15.088 under a line saying these were the published
 * levels.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const E = require('../public/js/embed-chart.js');
const TV = require('../public/js/tv-chart.js');

test('a link the page can read names the market, the timeframe and each level', () => {
  const P = E.readParams('?s=LINKUSDT&tf=4h&e=15.0885&sl=14.271&tp=16.3148&d=LONG');
  assert.equal(P.ok, true);
  assert.equal(P.symbol, 'LINKUSDT');
  assert.equal(P.tf, '4h');
  assert.equal(P.gran, '4h');
  assert.deepEqual(P.geo, { entry: 15.0885, stop: 14.271, target: 16.3148, direction: 'LONG' });
});

test('the symbol is read the way the bot spells it, and a spelling it cannot place is refused', () => {
  const sym = (s) => E.readParams('?s=' + encodeURIComponent(s));
  assert.equal(sym('link/usdt').symbol, 'LINKUSDT');
  assert.equal(sym('BTC/USDT:USDT').symbol, 'BTCUSDT');
  for (const bad of ['', 'A', '<img>', 'BTC USDT', 'X'.repeat(25), 'ÄBC']) {
    const P = sym(bad);
    assert.equal(P.ok, false, `"${bad}" was read as a market`);
    assert.equal(P.reason, 'symbol');
  }
  assert.deepEqual(E.readParams(''), { ok: false, reason: 'symbol' });
});

test('a timeframe the page does not draw is drawn on its default, and the page knows it was asked for another', () => {
  const P = E.readParams('?s=BTCUSDT&tf=5m');
  assert.equal(P.tf, E.DEFAULT_TF);
  assert.equal(P.tfAsked, '5m');
  assert.equal(P.gran, E.TIMEFRAMES[E.DEFAULT_TF]);
  // The four the bot links are all drawn as named.
  for (const tf of ['15m', '1h', '4h', '1d']) assert.equal(E.readParams(`?s=BTCUSDT&tf=${tf}`).tf, tf);
  assert.equal(E.readParams('?s=BTCUSDT&tf=15m').gran, '15min');
});

test('a level that is not a positive number is left out, never drawn at zero', () => {
  for (const v of ['0', '-1', 'abc', 'Infinity', 'NaN', '', '1e999']) {
    const P = E.readParams(`?s=BTCUSDT&e=${encodeURIComponent(v)}&sl=${encodeURIComponent(v)}&tp=${encodeURIComponent(v)}`);
    assert.equal(P.geo.entry, null, `entry "${v}" was read as a level`);
    assert.equal(P.geo.stop, null);
    assert.equal(P.geo.target, null);
  }
  const P = E.readParams('?s=BTCUSDT&e=63000');
  assert.equal(P.geo.entry, 63000);
  assert.equal(P.geo.stop, null);
});

test('a direction is LONG, SHORT or not stated', () => {
  assert.equal(E.readParams('?s=BTCUSDT&d=long').geo.direction, 'LONG');
  assert.equal(E.readParams('?s=BTCUSDT&d=SHORT').geo.direction, 'SHORT');
  for (const d of ['buy', 'sell', '', '<b>']) assert.equal(E.readParams(`?s=BTCUSDT&d=${encodeURIComponent(d)}`).geo.direction, null);
});

test('a level is printed with every decimal the link carried, and never fewer than the axis', () => {
  assert.equal(E.fmt(15.0885, TV), '15.0885');
  assert.equal(E.fmt(16.3148, TV), '16.3148');
  // No finer than it was published, no coarser than the chart draws it.
  assert.equal(E.fmt(63000, TV), (63000).toFixed(TV.precisionFor(63000)));
  assert.equal(E.fmt(0.00001234, TV).startsWith('0.00001234'), true);
  assert.equal(E.fmt(null, TV), '—');
});

async function serve() {
  const app = express();
  app.use('/embed', require('../routes/embed'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('the route serves the page under the embed CSP and echoes nothing from the link', async () => {
  const { server, base } = await serve();
  try {
    const r = await fetch(`${base}/embed/chart?s=%3Cscript%3Ealert(1)%3C/script%3E&e=%22%3E%3Cimg%3E`);
    assert.equal(r.status, 200);
    const csp = r.headers.get('content-security-policy') || '';
    assert.match(csp, /default-src 'none'/);
    assert.match(csp, /script-src 'self'/);
    const html = await r.text();
    assert.doesNotMatch(html, /alert\(1\)|<img>/, 'the route put the link into the page');
    // The library loads before the chart module, the chart module before the page.
    const order = ['lightweight-charts.standalone', '/js/tv-chart.js', '/js/signal-chart.js', '/js/embed-chart.js']
      .map((s) => html.indexOf(s));
    assert.ok(order.every((i) => i >= 0), `a script is missing from the page: ${order}`);
    assert.deepEqual([...order].sort((a, b) => a - b), order, 'the scripts load out of order');
  } finally {
    server.close();
  }
});

test('the live chart draws the window it asked for; a signal row keeps its 60-bar mini chart', () => {
  const SC = require('../public/js/signal-chart.js');
  const HOUR = 3600e3;
  const rows = [];
  let p = 100;
  for (let i = 0; i < 150; i++) {
    const o = p; p = p * (1 + Math.sin(i / 7) * 0.004);
    rows.push([String(1.7e12 + i * HOUR), String(o), String(Math.max(o, p) * 1.002),
      String(Math.min(o, p) * 0.998), String(p), '10']);
  }
  const full = E.specFor(rows, E.readParams('?s=BTCUSDT&tf=1h'), SC);
  assert.equal(full.ok, true);
  assert.equal(full.bars.length, 150, 'the live chart page was cut to the mini chart window');
  assert.ok(E.BARS >= 150, 'the page asks for fewer bars than this drive hands it');
  const mini = SC.tvSpec(rows, {}, {});
  assert.equal(mini.bars.length, SC.MAX_BARS);
});

test('the page builds its chart spec in one place, so the window it draws is the one tested', () => {
  // A source scan, stated as one: `draw()` is a browser callback behind a
  // fetch and the library, and a second `tvSpec` call there would draw the
  // thumbnail window while the drive above still passed.
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'public', 'js', 'embed-chart.js'), 'utf8'));
  assert.equal((src.match(/\.tvSpec\(/g) || []).length, 1, 'a second spec is built outside specFor');
  assert.match(src, /var spec = specFor\(rows, P, SC\);/);
});
