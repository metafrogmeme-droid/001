'use strict';
/**
 * Exit-adjusted NAV. Admin only, flag off by default. Basis points.
 * The depth walk is the SaR cost step. A missing snapshot is not an
 * empty ranking. An omitted epoch flow is not a swing of zero.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.XNAV_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const flag = require('../lib/xnav_flag');
const depth = require('../lib/xnav/depth');
const swing = require('../lib/xnav/swing');
const { loadPlaceholders, l1ReadGas } = require('../lib/xnav/config');
const { rank, loadSnapshot, publicRow } = require('../lib/xnav/index');
const sar = require('../lib/sar/metrics');

let _serial = Promise.resolve();
function serial(name, fn) {
  test(name, () => {
    const run = _serial.then(fn, fn);
    _serial = run.then(() => {}, () => {});
    return run;
  });
}

const ADMIN_ID = 91041;
const USER_ID = 91042;
pool.users.push({ id: ADMIN_ID, email: 'xnav-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'xnav-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'xnav-admin@example.com');
const userToken = tokenFor(USER_ID, 'xnav-user@example.com');

const DRK = '0xc179e03922afe8fa9533d3f896338b9fb87ce0c8';
const FIXTURE = path.join(__dirname, '..', '..', 'bot', 'xnav', 'fixtures', 'snapshot_sample.json');
const XMR = path.join(__dirname, '..', '..', 'bot', 'xnav', 'fixtures', 'xmr_top.json');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}
function close(server) {
  return new Promise((resolve) => server.close(resolve));
}
async function hit(server, url, { token, method, body } = {}) {
  const headers = { accept: 'application/json' };
  if (token) headers.authorization = 'Bearer ' + token;
  let payload;
  if (body != null) {
    payload = JSON.stringify(body);
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(`http://127.0.0.1:${server.address().port}${url}`, {
    method: method || 'GET', headers, body: payload,
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (e) { parsed = raw; }
  return { status: res.status, raw, body: parsed, cache: res.headers.get('cache-control') };
}

function appFor() {
  const app = express();
  app.use(express.json());
  app.use('/api/xnav', require('../routes/xnav'));
  app.get('/xnav', (req, res) => flag.sendPage(req, res));
  return app;
}

test('placeholders are labelled and the gas formula is the file', () => {
  const ph = loadPlaceholders();
  assert.equal(ph.label.startsWith('Placeholder'), true);
  assert.equal(ph.staleness_seconds, 60);
  assert.equal(ph.max_swing_bps, 1500);
  assert.equal(ph.l1_read_gas_base, 2000);
  assert.equal(ph.l1_read_gas_per_word, 65);
  assert.equal(l1ReadGas(2, 1), 2000 + 65 * 3);
  assert.equal(flag.flagOn({ XNAV_ENABLED: '1' }), true);
  assert.equal(flag.flagOn({ XNAV_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.gate(true, false), 'deny');
  assert.equal(flag.gate(true, true), 'open');
  assert.equal(flag.gate(false, true), 'preview');
});

serial('the door is admin-only in both flag states', async () => {
  const previous = process.env.XNAV_SNAPSHOT_PATH;
  process.env.XNAV_SNAPSHOT_PATH = FIXTURE;
  const server = await listen(appFor());
  try {
    delete process.env.XNAV_ENABLED;
    const stranger = await hit(server, '/api/xnav/rank');
    assert.equal(stranger.status, 404);
    const user = await hit(server, '/api/xnav/rank', { token: userToken });
    assert.equal(user.status, 404);
    const admin = await hit(server, '/api/xnav/rank', { token: adminToken });
    assert.equal(admin.status, 200);
    assert.equal(admin.body.banner, flag.BANNER);
    assert.equal(admin.cache, 'no-store');
    const page = await hit(server, '/xnav', { token: adminToken });
    assert.equal(page.status, 200);
    assert.equal(page.raw.includes('Admin preview. Not public.'), true);
    assert.equal(page.raw.includes('$'), false);

    process.env.XNAV_ENABLED = '1';
    const stillOut = await hit(server, '/api/xnav/rank', { token: userToken });
    assert.equal(stillOut.status, 404);
    const open = await hit(server, '/api/xnav/rank', { token: adminToken });
    assert.equal(open.status, 200);
    assert.equal(open.body.banner, undefined);
    const openPage = await hit(server, '/xnav', { token: adminToken });
    assert.equal(openPage.raw.includes('Admin preview. Not public.'), false);
    assert.equal(openPage.status, 200);
  } finally {
    delete process.env.XNAV_ENABLED;
    if (previous == null) delete process.env.XNAV_SNAPSHOT_PATH;
    else process.env.XNAV_SNAPSHOT_PATH = previous;
    await close(server);
  }
});

serial('the ranking is the fixture, and a missing file is not an empty board', async () => {
  const previous = process.env.XNAV_SNAPSHOT_PATH;
  process.env.XNAV_SNAPSHOT_PATH = FIXTURE;
  process.env.XNAV_ENABLED = '1';
  const server = await listen(appFor());
  try {
    const board = await hit(server, '/api/xnav/rank', { token: adminToken });
    assert.equal(board.body.readable, true);
    assert.equal(board.body.ranked[0].name, 'drkmttr');
    assert.equal(board.body.ranked[0].haircut_bps, 4560);
    assert.equal(board.body.ranked[0].address, DRK);
    const ge500 = board.body.ranked.filter((row) => row.haircut_bps >= 500);
    const ge100 = board.body.ranked.filter((row) => row.haircut_bps >= 100);
    assert.equal(ge500.length, 4);
    assert.equal(ge100.length, 6);
    const link = board.body.ranked.find((row) => row.name === 'Long LINK Short XRP');
    assert.equal(link.unfilled_bps, 0);
    const dumped = JSON.stringify(board.body);
    assert.equal(dumped.includes('$'), false);
    assert.equal(/_usd/.test(dumped), false);
    const bad = await hit(server, '/api/xnav/not-an-address', { token: adminToken });
    assert.equal(bad.status, 404);
    const one = await hit(server, '/api/xnav/' + DRK, { token: adminToken });
    assert.equal(one.body.haircut_bps, 4560);
    assert.equal(one.body.positions[0].coin, 'XMR');
    assert.equal(one.body.positions[0].cost_bps, 4256);
  } finally {
    delete process.env.XNAV_ENABLED;
    if (previous == null) delete process.env.XNAV_SNAPSHOT_PATH;
    else process.env.XNAV_SNAPSHOT_PATH = previous;
    await close(server);
  }

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xnav-'));
  const missing = path.join(dir, 'absent.json');
  process.env.XNAV_SNAPSHOT_PATH = missing;
  process.env.XNAV_ENABLED = '1';
  const server2 = await listen(appFor());
  try {
    const gone = await hit(server2, '/api/xnav/rank', { token: adminToken });
    assert.equal(gone.body.readable, false);
    assert.equal(gone.body.reason, 'not_published');
    assert.equal(gone.body.ranked, null);
  } finally {
    delete process.env.XNAV_ENABLED;
    if (previous == null) delete process.env.XNAV_SNAPSHOT_PATH;
    else process.env.XNAV_SNAPSHOT_PATH = previous;
    await close(server2);
  }
});

serial('a quote without flow leaves the swing unreadable and keeps the haircut', async () => {
  const previous = process.env.XNAV_SNAPSHOT_PATH;
  process.env.XNAV_SNAPSHOT_PATH = FIXTURE;
  process.env.XNAV_ENABLED = '1';
  const server = await listen(appFor());
  try {
    const tooBig = await hit(server, '/api/xnav/quote?vault=' + DRK + '&size=2', { token: adminToken });
    assert.equal(tooBig.body.reason, 'size_unreadable');
    assert.equal(tooBig.body.swing_bps, null);
    const dollars = await hit(server, '/api/xnav/quote?vault=' + DRK + '&size=1000', { token: adminToken });
    assert.equal(dollars.body.reason, 'size_unreadable');
    const noFlow = await hit(server, '/api/xnav/quote?vault=' + DRK + '&size=0.25', { token: adminToken });
    assert.equal(noFlow.body.readable, true);
    assert.equal(noFlow.body.reason, 'epoch_flow_unreadable');
    assert.equal(noFlow.body.swing_bps, null);
    assert.equal(noFlow.body.haircut_bps, 4560);
    const withFlow = await hit(server, '/api/xnav/quote?vault=' + DRK + '&size=0.25&flow=0', { token: adminToken });
    assert.equal(typeof withFlow.body.swing_bps, 'number');
    const ph = loadPlaceholders();
    const expected = swing.previewRedeem(
      10000n, 10000n, 2500n, 0n,
      BigInt(ph.flow_threshold_bps), 4560n, BigInt(ph.max_swing_bps),
    );
    assert.equal(withFlow.body.swing_bps, Number(expected.swingBps));
    assert.equal(JSON.stringify(withFlow.body).includes('$'), false);
  } finally {
    delete process.env.XNAV_ENABLED;
    if (previous == null) delete process.env.XNAV_SNAPSHOT_PATH;
    else process.env.XNAV_SNAPSHOT_PATH = previous;
    await close(server);
  }
});

test('the exit cost of a full book is the SaR walk, and a short book is not zero', () => {
  const book = {
    bids: [{ px: '100', sz: '6', n: 1 }, { px: '90', sz: '100', n: 1 }],
    asks: [{ px: '100', sz: '10', n: 1 }],
  };
  const got = depth.exitCost(book, '10', 'long');
  assert.equal(got.readable, true);
  assert.equal(got.fullyFilled, true);
  assert.equal(got.haircutBps, 400n);
  const bids = sar.parseSide(book.bids, 'bid');
  const asks = sar.parseSide(book.asks, 'ask');
  const mid = (bids.levels[0].px + asks.levels[0].px) / 2n;
  const walked = sar.walkSide(bids.levels, mid, sar.parseDec('10'), 'bid');
  assert.equal(walked.reason, null);
  assert.equal(got.haircutBps, sar.bpsFromWad(walked.wad));

  const short = depth.exitCost(book, '1000', 'long');
  assert.equal(short.readable, true);
  assert.equal(short.reason, 'unfilled_remainder');
  assert.equal(short.fullyFilled, false);
  assert.notEqual(short.haircutBps, 0n);
  const shortWalk = sar.walkSide(bids.levels, mid, sar.parseDec('1000'), 'bid');
  assert.equal(shortWalk.reason, 'book_shorter_than_q');

  const crossed = depth.exitCost({
    bids: [{ px: '110', sz: '10', n: 1 }],
    asks: [{ px: '100', sz: '10', n: 1 }],
  }, '1', 'long');
  assert.equal(crossed.readable, false);
  assert.equal(crossed.reason, 'crossed_book');
  assert.equal(crossed.haircutBps, null);

  const top = JSON.parse(fs.readFileSync(XMR, 'utf8'));
  const small = depth.exitCost(top, '1', 'long');
  assert.equal(small.fullyFilled, true);
  assert.equal(small.readable, true);
});

test('median, TWAP, delta cap and precision choice do not invent a number', () => {
  const even = depth.medianBps([1n, 4n, 2n, 3n]);
  assert.equal(even.bps, 3n);
  assert.equal(depth.medianBps([1n, null]).readable, false);
  assert.equal(depth.twapBps([10n, null, 30n], 3).readable, false);
  assert.equal(depth.twapBps([10n, 20n], 3).reason, 'window_short');
  assert.equal(depth.twapBps([10n, 20n, 30n], 3).bps, 20n);
  const first = depth.deltaCap(null, 10n, 5n);
  assert.equal(first.capped, false);
  assert.equal(first.bps, 10n);
  const fromZero = depth.deltaCap(0n, 10n, 5n);
  assert.equal(fromZero.capped, true);
  assert.equal(fromZero.bps, 5n);
  const readings = [
    { precision: 'full', readable: true, fullyFilled: false, filledWad: 1n, haircutBps: 10n },
    { precision: '4', readable: true, fullyFilled: true, filledWad: 5n, haircutBps: 20n },
    { precision: '3', readable: true, fullyFilled: true, filledWad: 5n, haircutBps: 30n },
    { precision: '2', readable: true, fullyFilled: true, filledWad: 9n, haircutBps: 40n },
  ];
  assert.equal(depth.choosePrecision(readings).chosen, '4');
  const uncovered = readings.map((row) => ({ ...row, fullyFilled: false }));
  const best = depth.choosePrecision(uncovered);
  assert.equal(best.chosen, '2');
  assert.equal(best.reason, 'unfilled_remainder');
  assert.equal(depth.choosePrecision(readings.slice(0, 3)).reason, 'precision_missing');

  const book = {
    bids: [{ px: '100', sz: '100', n: 1 }],
    asks: [{ px: '100', sz: '100', n: 1 }],
  };
  const measured = depth.measurePosition('10', 'long', { full: book, 4: book, 3: book, 2: book });
  assert.equal(measured.chosen.haircutBps, 0n);
  assert.equal(measured.stress.readable, true);
  assert.equal(measured.stress.haircutBps >= measured.chosen.haircutBps, true);
  const hole = depth.measurePosition('10', 'long', { full: book, 3: book, 2: book });
  assert.equal(hole.median.readable, false);
  assert.equal(hole.median.reason, 'precision_unreadable');
});

test('a row with no haircut is left out, and zero is a measured zero', () => {
  const board = rank({
    vaults: [
      { address: '0x' + 'aa'.repeat(20), name: 'blank', haircut_bps: null, unfilled_bps: 0 },
      { address: '0x' + 'bb'.repeat(20), name: 'flat', haircut_bps: 0, unfilled_bps: 0 },
    ],
  });
  assert.equal(board.unreadable_vaults, 1);
  assert.equal(board.ranked.length, 1);
  assert.equal(board.ranked[0].haircut_bps, 0);
  assert.equal(publicRow({ haircut_bps: 0, address: '0x' + 'bb'.repeat(20) }).readable, true);
  const missing = loadSnapshot(path.join(os.tmpdir(), 'xnav-does-not-exist.json'));
  assert.equal(missing.readable, false);
  assert.equal(missing.reason, 'not_published');
  assert.equal(missing.doc, null);
});

serial('the MCP surface lists one tool and is not the thirty-four catalogue', async () => {
  const previous = process.env.XNAV_SNAPSHOT_PATH;
  process.env.XNAV_SNAPSHOT_PATH = FIXTURE;
  process.env.XNAV_ENABLED = '1';
  const server = await listen(appFor());
  try {
    const listed = await hit(server, '/api/xnav/mcp', {
      token: adminToken,
      method: 'POST',
      body: { jsonrpc: '2.0', id: 1, method: 'tools/list' },
    });
    assert.equal(listed.body.result.tools.length, 1);
    assert.equal(listed.body.result.tools[0].name, 'xnav_quote');
    const called = await hit(server, '/api/xnav/mcp', {
      token: adminToken,
      method: 'POST',
      body: {
        jsonrpc: '2.0', id: 2, method: 'tools/call',
        params: { name: 'xnav_quote', arguments: { vault: DRK, size: '0.1' } },
      },
    });
    assert.equal(called.body.result.haircut_bps, 4560);
    assert.equal(called.body.result.swing_bps, null);
    assert.equal(called.body.result.reason, 'epoch_flow_unreadable');
    const stranger = await hit(server, '/api/xnav/mcp', {
      method: 'POST',
      body: { jsonrpc: '2.0', id: 3, method: 'tools/list' },
    });
    assert.equal(stranger.status, 404);
    const catalogue = fs.readFileSync(path.join(__dirname, '..', 'routes', 'mcp.js'), 'utf8');
    assert.equal(catalogue.includes('xnav_quote'), false);
    const py = fs.readFileSync(path.join(__dirname, '..', '..', 'bot', 'mcp', 'server.py'), 'utf8');
    assert.equal(py.includes('xnav_quote'), false);
  } finally {
    delete process.env.XNAV_ENABLED;
    if (previous == null) delete process.env.XNAV_SNAPSHOT_PATH;
    else process.env.XNAV_SNAPSHOT_PATH = previous;
    await close(server);
  }
});

test('server.js mounts the page and the API, and the page stays unlinked', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.equal(server.includes("app.use('/api/xnav'"), true);
  assert.equal(server.includes("app.get('/xnav'"), true);
  assert.equal(server.includes("require('./lib/xnav_flag').pageGate"), true);
  const unlinked = JSON.parse(fs.readFileSync(path.join(__dirname, 'unlinked_routes.json'), 'utf8'));
  assert.equal(typeof unlinked.routes['/xnav'], 'string');
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'xnav.html'), 'utf8');
  assert.equal(page.includes('$'), false);
  assert.equal(page.includes('xnav_quote'), false);
  const scripts = [...page.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<script\b([^>]*)>/gi)].map((m) => m[1]);
  assert.match(scripts[0], /src="\/js\/api-retry\.js\?v=\d+"/);
});
