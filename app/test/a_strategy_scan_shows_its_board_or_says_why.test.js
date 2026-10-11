'use strict';
/**
 * "Scan now" on a strategy's page shows the bot's board for a rule the live
 * bot does not run, or says why it could not — never an empty board.
 *
 * The route relays the bot's board (bot/core/strategy_scan.py) with the id as
 * a query parameter, keeps a minute's cache per id, refuses an id that is not
 * a slug, keeps the bot's exception class on a failed venue read, and holds a
 * tighter per-IP limit than the catalogue. The bundle paints each rule's
 * board, lists unread markets apart, and turns any failed read into a
 * sentence with its reason.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const Scan = require(path.join(APP, 'public', 'js', 'strategy-scan.js'));
const gateway = require('../lib/gateway');

const BOARD = {
  preset: 'alt sweep', label: 'ALT Sweep', command: '/altsweep', rule: 'ma_trend',
  bar_close: '20 Jan 10:00 UTC', places: false,
  rows: [{ symbol: 'ADA', fast: 1.2, slow: 1.1, side: 'LONG' }, { symbol: 'VET', fast: 1, slow: 1.1, side: 'SHORT' }],
  unread: [{ symbol: 'ZIL', why: 'TimeoutError' }],
  verdict: 'The averages name: long ADA; short VET.',
};

let calls, answer;

async function withRouter(fn) {
  // A fresh router per test: its limiter and cache are module state. The
  // limiter's count is shared since request limits moved into one table for
  // every web instance, and with no DATABASE_URL that table is the in-memory
  // database's, so the database and the limiter module are fresh too.
  for (const m of ['../routes/public_strategies', '../lib/rate_limit', '../db']) {
    delete require.cache[require.resolve(m)];
  }
  calls = [];
  gateway.isConfigured = () => true;
  gateway.getGateway = async (p) => { calls.push(p); return answer(p); };
  const app = express();
  app.use('/api/public/strategies', require('../routes/public_strategies'));
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (p) => new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method: 'GET' }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d || '{}') }));
    });
    r.on('error', reject);
    r.end();
  });
  try { await fn(get); } finally { server.close(); }
}

test('the route relays the board, with the id as a query parameter, and caches it a minute', async () => {
  answer = () => ({ status: 200, data: { public: true, board: BOARD } });
  await withRouter(async (get) => {
    const r = await get('/api/public/strategies/scan?id=alt-sweep');
    assert.equal(r.status, 200);
    assert.deepEqual(r.data.board, BOARD);
    assert.deepEqual(calls, ['/public/strategy-scan?id=alt-sweep']);
    const again = await get('/api/public/strategies/scan?id=alt-sweep');
    assert.equal(again.status, 200);
    assert.equal(calls.length, 1, 'the second ask inside a minute was the cache');
    await get('/api/public/strategies/scan?id=eth-ma-trend');
    assert.equal(calls.length, 2, 'another id is another read');
  });
});

test('an id that is not a slug is refused without asking the bot', async () => {
  answer = () => ({ status: 200, data: { board: BOARD } });
  await withRouter(async (get) => {
    for (const bad of ['', '../x', 'a%2Fb', 'x'.repeat(41)]) {
      const r = await get('/api/public/strategies/scan?id=' + bad);
      assert.equal(r.status, 400, bad);
    }
    assert.equal(calls.length, 0);
  });
});

test('a failed venue read keeps the bot\'s exception class; a house preset is the bot\'s 404', async () => {
  answer = (p) => (p.includes('full-scan')
    ? { status: 404, data: { error: 'no_scan', detail: 'No scan for this name' } }
    : { status: 502, data: { error: 'scan_failed', detail: 'TimeoutError' } });
  await withRouter(async (get) => {
    const r = await get('/api/public/strategies/scan?id=alt-sweep');
    assert.equal(r.status, 502);
    assert.deepEqual(r.data, { error: 'scan_failed', detail: 'TimeoutError' });
    const again = await get('/api/public/strategies/scan?id=alt-sweep');
    assert.equal(calls.length, 2, 'a failure is not cached');
    assert.equal(again.status, 502);
    const house = await get('/api/public/strategies/scan?id=full-scan');
    assert.equal(house.status, 404);
    assert.equal(house.data.error, 'no_scan');
  });
});

test('the scan route holds six asks a minute per address; the catalogue is untouched', async () => {
  answer = () => ({ status: 200, data: { board: BOARD } });
  await withRouter(async (get) => {
    const codes = [];
    for (let i = 0; i < 7; i++) codes.push((await get('/api/public/strategies/scan?id=alt-sweep')).status);
    assert.deepEqual(codes, [200, 200, 200, 200, 200, 200, 429]);
  });
});

// ── the bundle ─────────────────────────────────────────────────────────────

test('a moving-average board lists each side and names the unread market apart', () => {
  const html = Scan.boardHtml(BOARD);
  assert.match(html, /At the 20 Jan 10:00 UTC close\./);
  assert.match(html, /<b>ADA<\/b> <span class="chip"[^>]*>long<\/span>/);
  assert.match(html, /<b>VET<\/b> <span class="chip"[^>]*>short<\/span>/);
  assert.match(html, /Unread, so not on the board: ZIL \(TimeoutError\)\./);
  assert.ok(!/<b>ZIL<\/b>/.test(html), 'an unread market is never a row');
  assert.match(html, /Nothing is placed or queued/);
  assert.ok(!html.includes('$'));
});

test('each rule paints its own reading, and an unread number says so', () => {
  const mom = Scan.boardHtml({ rule: 'momentum_trend', bar_close: 'x', verdict: 'v', unread: [],
    rows: [{ symbol: 'BTC', momentum_pct: 1.234, reason: 'agree: long' },
      { symbol: 'PONS', momentum_pct: null, reason: 'unread: 3 closed bars, 22 needed' }] });
  assert.match(mom, /<b>BTC<\/b> momentum \+1\.23%/);
  assert.match(mom, /<b>PONS<\/b> momentum unread/);
  const rot = Scan.boardHtml({ rule: 'vol_rotation', bar_close: 'x', verdict: 'It would hold SOL', unread: [],
    rows: [{ symbol: 'SOL', momentum: 0.62, above_average: true, qualifies: true },
      { symbol: 'BTC', momentum: null, above_average: null, qualifies: false }] });
  assert.match(rot, /<b>SOL<\/b> momentum \+62\.00%, above its average, qualifies/);
  assert.match(rot, /<b>BTC<\/b> momentum unread, its average unread, does not qualify/);
  const lim = Scan.boardHtml({ rule: 'limit_entry', bar_close: 'x', verdict: 'v', unread: [],
    classes: [{ name: 'metals', leader: 'XAU', side: null, regime: "the leader's day could not be read",
      board: [], plan: null },
    { name: 'crypto', leader: 'BTC', side: 'LONG', regime: 'up and above VWAP',
      board: [{ symbol: 'ETH', side: 'LONG', points: 74, out_of: 80, skip: null }],
      plan: { symbol: 'ETH', side: 'LONG', limit: 2400.5, stop: 2350, target: 2520 } }] });
  assert.match(lim, /metals — XAU<\/b>: no side/);
  assert.match(lim, /ETH <span class="chip"[^>]*>long<\/span> 74\/80/);
  assert.match(lim, /Would rest a buy limit on <b>ETH<\/b> at 2400\.5, stop 2350, first target 2520/);
});

function sink() { return { innerHTML: '' }; }

test('a read paints the board; a failed read says why and paints no board', async () => {
  const ok = sink();
  await Scan.run('alt-sweep', ok, async (url) => {
    assert.equal(url, '/api/public/strategies/scan?id=alt-sweep');
    return { ok: true, status: 200, json: async () => ({ board: BOARD }) };
  });
  assert.match(ok.innerHTML, /The averages name: long ADA; short VET\./);

  const failed = sink();
  await Scan.run('alt-sweep', failed, async () => ({
    ok: false, status: 502, json: async () => ({ error: 'scan_failed', detail: 'TimeoutError' }) }));
  assert.match(failed.innerHTML, /The scan could not be read \(TimeoutError\)\. No board is shown/);
  assert.ok(!/Nothing is placed or queued/.test(failed.innerHTML), 'no board under a failed read');

  const garbled = sink();
  await Scan.run('alt-sweep', garbled, async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('x'); } }));
  assert.match(garbled.innerHTML, /could not be read \(HTTP 200\)/);

  const down = sink();
  await Scan.run('alt-sweep', down, async () => { throw new TypeError('Failed to fetch'); });
  assert.match(down.innerHTML, /could not be read \(Failed to fetch\)/);
});

test('the section is offered only on a card that names a scan door, and the pages wire it', () => {
  assert.equal(Scan.blockHtml({ id: 'full-scan', scan: null }), '');
  assert.match(Scan.blockHtml({ id: 'alt-sweep', scan: '/altsweep' }), /In the bot: <code>\/altsweep<\/code>/);
  const strat = codeOnly(fs.readFileSync(path.join(APP, 'public', 'strategy.html'), 'utf8'));
  assert.match(strat, /<script src="\/js\/strategy-scan\.js\?v=1"><\/script>/);
  assert.match(strat, /window\.StrategyScan\.blockHtml\(a\)/);
  assert.match(strat, /window\.StrategyScan\.run\(a\.id, document\.getElementById\('scanOut'\)/);
  const agents = codeOnly(fs.readFileSync(path.join(APP, 'public', 'agents.html'), 'utf8'));
  assert.match(agents, /a\.scan \? '<div class="go"[^>]*>Scan it now on its page, or '\s*\+ esc\(a\.scan\) \+ ' in the bot<\/div>' : ''/);
});
