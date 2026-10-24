'use strict';
/**
 * FVB_ENABLED, off by default.
 *
 * The public gets 404, the same answer as a path this server does not
 * have. An operator still gets the page, with the banner
 * "Admin preview. Not public." With the flag on, a stranger gets the
 * page and the banner is gone.
 *
 * The board the page reads is the file the publisher wrote. A missing
 * file is "not published". A file that cannot be parsed is "unreadable".
 * Neither is a zero price.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
delete process.env.FVB_ENABLED;
delete process.env.FVB_BOARD_PATH;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const flag = require('../lib/fvb_flag');
const fvbRoutes = require('../routes/fvb');

const OPERATOR = 41;
const STRANGER = 42;
pool.users.push({ id: OPERATOR, email: 'fvb-op@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: STRANGER, email: 'fvb-free@example.com', plan: 'free', token_epoch: 0 });
const OP = jwt.sign({ user_id: OPERATOR, email: 'fvb-op@example.com' }, process.env.JWT_SECRET);
const FREE = jwt.sign({ user_id: STRANGER, email: 'fvb-free@example.com' }, process.env.JWT_SECRET);
const LABEL = 'Public signals, not advice.';
const BANNER = 'Admin preview. Not public.';

function flagOnCases() {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ FVB_ENABLED: '' }), false);
  assert.equal(flag.flagOn({ FVB_ENABLED: '0' }), false);
  assert.equal(flag.flagOn({ FVB_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ FVB_ENABLED: '1' }), true);
  assert.equal(flag.flagOn({ FVB_ENABLED: ' TRUE ' }), true);
  assert.equal(flag.gate(false, false), 'deny');
  assert.equal(flag.gate(false, true), 'preview');
  assert.equal(flag.gate(true, false), 'open');
  assert.equal(flag.gate(true, true), 'open');
}

test('the flag reads only 1 and true', flagOnCases);

async function serve() {
  const app = express();
  app.use(flag.pageGate);
  app.get('/fvb', (req, res) => { flag.sendPage(req, res); });
  app.get('/js/fvb-page.js', (_req, res) => { res.type('js').send('/* fvb page */'); });
  app.use('/api/fvb', fvbRoutes);
  const server = await new Promise((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

function call(base, p, token) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${p}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({
        status: res.statusCode,
        type: String(res.headers['content-type'] || ''),
        cache: String(res.headers['cache-control'] || ''),
        body: d,
      }));
    });
    req.on('error', reject);
    req.end();
  });
}

test('off hides the page from the public and shows it to an operator', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fvb-'));
  const board = path.join(dir, 'board.json');
  const savedFlag = process.env.FVB_ENABLED;
  const savedPath = process.env.FVB_BOARD_PATH;
  process.env.FVB_BOARD_PATH = board;
  const { server, base } = await serve();
  try {
    delete process.env.FVB_ENABLED;
    const anon = await call(base, '/fvb');
    assert.equal(anon.status, 404);
    assert.equal(anon.body, 'Not found');
    assert.equal((await call(base, '/fvb', FREE)).status, 404);
    assert.equal((await call(base, '/fvb.html', FREE)).status, 404);
    assert.equal((await call(base, '/js/fvb-page.js')).status, 404);
    assert.equal((await call(base, '/api/fvb/board', FREE)).status, 404);

    const preview = await call(base, '/fvb', OP);
    assert.equal(preview.status, 200);
    assert.match(preview.type, /html/);
    assert.equal(preview.cache, 'no-cache');
    assert.match(preview.body, /<p class="fvb-banner" role="status">Admin preview\. Not public\.<\/p>/);
    assert.ok(preview.body.includes(LABEL));
    assert.ok(preview.body.includes('Ethereum Sepolia'));
    assert.ok(preview.body.includes('Robinhood Chain testnet'));
    assert.ok(!preview.body.includes('Base'));
    assert.ok(!preview.body.includes('Arbitrum'));
    const apiPreview = await call(base, '/api/fvb/board', OP);
    assert.equal(apiPreview.status, 200);
    const missing = JSON.parse(apiPreview.body);
    assert.equal(missing.available, false);
    assert.equal(missing.reason, 'not_published');
    assert.equal(missing.label, LABEL);
    assert.equal(missing.banner, BANNER);
    assert.equal(missing.fv, undefined);
    assert.ok(!Object.prototype.hasOwnProperty.call(missing, 'pnl'));

    fs.writeFileSync(board, '{', 'utf8');
    const bad = JSON.parse((await call(base, '/api/fvb/board', OP)).body);
    assert.equal(bad.available, false);
    assert.equal(bad.reason, 'unreadable');
    assert.equal(bad.fv, undefined);

    fs.writeFileSync(board, JSON.stringify({
      available: true,
      label: LABEL,
      v: 1,
      rows: [{ symbol: 'SPY', fv: 100, confirmed: true, session: 'regular' }],
    }), 'utf8');

    process.env.FVB_ENABLED = '1';
    const open = await call(base, '/fvb', FREE);
    assert.equal(open.status, 200);
    assert.ok(!open.body.includes(BANNER));
    assert.ok(!open.body.includes('<!--FVB_BANNER-->'));
    assert.ok(open.body.includes(LABEL));
    assert.equal((await call(base, '/js/fvb-page.js', FREE)).status, 200);
    const live = JSON.parse((await call(base, '/api/fvb/board')).body);
    assert.equal(live.available, true);
    assert.equal(live.banner, undefined);
    assert.equal(live.rows[0].symbol, 'SPY');
    assert.equal(live.rows[0].fv, 100);
    assert.ok(!Object.prototype.hasOwnProperty.call(live, 'pnl'));
  } finally {
    server.close();
    if (savedFlag === undefined) delete process.env.FVB_ENABLED;
    else process.env.FVB_ENABLED = savedFlag;
    if (savedPath === undefined) delete process.env.FVB_BOARD_PATH;
    else process.env.FVB_BOARD_PATH = savedPath;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
