'use strict';
/**
 * Kink Cap stays off the public site.
 *
 * The flag accepts only "1" and "true". An operator can read the page and
 * the rate points while it is off, and the page says so. A signed-in
 * stranger and an anonymous caller get 404, including when the flag is on.
 * The shock is a dollar amount and it is not on the dashboard or the page.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { flag_on, pageGate, sendPage } = require('../lib/hrc_flag');
const { renderPage, loadCatalog } = require('../lib/tools_page');
const hrcRouter = require('../routes/hrc');
const M = require('../lib/hrc_model');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const PAGE = fs.readFileSync(path.join(APP, 'public', 'hrc.html'), 'utf8');
const PAGE_JS = fs.readFileSync(path.join(APP, 'public', 'js', 'hrc.js'), 'utf8');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function get(server, urlPath, token) {
  return new Promise((resolve, reject) => {
    const headers = {};
    if (token) headers.authorization = 'Bearer ' + token;
    const req = http.request({
      port: server.address().port, path: urlPath, headers,
    }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => resolve({ status: res.statusCode, body: b, type: res.headers['content-type'] || '' }));
    });
    req.on('error', reject);
    req.end();
  });
}

function sign(id, email) {
  return jwt.sign({ user_id: id, email }, process.env.JWT_SECRET);
}

test('only 1 and true turn the flag on', () => {
  assert.equal(flag_on({ HRC_ENABLED: '1' }), true);
  assert.equal(flag_on({ HRC_ENABLED: 'true' }), true);
  assert.equal(flag_on({ HRC_ENABLED: 'TRUE' }), true);
  assert.equal(flag_on({ HRC_ENABLED: 'yes' }), false);
  assert.equal(flag_on({ HRC_ENABLED: 'on' }), false);
  assert.equal(flag_on({ HRC_ENABLED: '0' }), false);
  assert.equal(flag_on({}), false);
  const src = fs.readFileSync(path.join(APP, 'lib', 'hrc_flag.js'), 'utf8');
  assert.equal(codeOnly(src).includes('function enabled'), false);
});

test('the curve matches the pinned checkpoints and derives the crossover', () => {
  const src = fs.readFileSync(path.join(APP, 'lib', 'hrc_model.js'), 'utf8');
  assert.equal(src.includes('0.8125'), false);
  assert.equal(src.includes('0.814'), false);
  assert.equal(M.fmt(M.borrowApy(M.parseDec('0.85'))), '0.2875');
  assert.equal(M.fmt(M.borrowApy(M.parseDec('0.90'))), '0.525');
  assert.equal(M.fmt(M.borrowApy(M.parseDec('1'))), '1');
  assert.equal(M.fmt(M.borrowApy(M.parseDec('1.2'))), '1');
  assert.equal(M.fmt(M.borrowApy(M.parseDec('0.5'))), '0.05');
  assert.equal(M.fmt(M.borrowApy(M.parseDec('0'))), '0.05');
  assert.equal(M.borrowApy(null), null);
  assert.equal(M.fmt(M.TOLERANCE), '0.000575');
  const funding = M.parseDec('0.1095');
  const got = M.crossoverUtilization(funding);
  const naive = M.utilizationForBorrowApy(funding);
  assert.equal(M.fmt(got), M.fmt(M.utilizationForBorrowApy(M.borrowApyMatchingFunding(funding))));
  assert.notEqual(M.fmt(naive), M.fmt(got));
  assert.equal(M.percentTenth(got), '81.4');

  const supplied = 55236235337690993n;
  const borrowed = 42938185433855173n;
  assert.notEqual((borrowed * 10000n) % supplied, 0n);
  assert.equal((borrowed * 10000n) / supplied, 7773n);
  const util = M.utilizationOf(supplied, borrowed);
  assert.equal(M.quoteMatches(util, 500n), true);
  assert.equal(M.fmt(M.borrowApy(util)), '0.05');
  const above = M.utilizationOf(1000000n, 820099n);
  assert.equal(M.quoteMatches(above, 1450n), true);
  assert.equal(M.quoteMatches(above, 1448n), false);

  const low = M.parseDec('0.05');
  const high = M.parseDec('0.525');
  const agree = M.chooseSettlement(low, M.parseDec('0.05005'));
  assert.equal(agree.source, 'precompile');
  const lower = M.chooseSettlement(low, high);
  assert.equal(lower.source, 'lower_of');
  assert.equal(M.fmt(lower.rate), '0.05');
  const none = M.chooseSettlement(null, null);
  assert.equal(none.payoutZero, true);
  assert.equal(none.rate, null);
});

test('the page source has no dollars and does not ask for the shock', () => {
  assert.equal(PAGE.includes('$'), false);
  assert.equal(PAGE_JS.includes('$'), false);
  assert.equal(PAGE.includes('cap spread'), true);
  assert.equal(PAGE.includes('40%'), true);
  assert.equal(PAGE.includes('rate_shock'), false);
  assert.equal(PAGE_JS.includes('rate_shock'), false);
  assert.equal(PAGE.includes('data-i18n'), false);
  assert.match(PAGE, /styles\.css\?v=52/);
  assert.match(PAGE, /src="\/js\/api-retry\.js\?v=3"/);
  assert.match(PAGE, /src="\/js\/hrc\.js\?v=2"/);
  assert.ok(PAGE.indexOf('/js/api-retry.js') < PAGE.indexOf('/js/hrc.js'));
  assert.equal((PAGE.match(/<script\b/g) || []).length, 2);
  assert.match(PAGE_JS, /AbortSignal\.timeout\(/);
  assert.match(PAGE_JS, /credentials:\s*'same-origin'/);
  assert.equal(PAGE.match(/property="og:site_name" content="([^"]*)"/)[1], 'Humanoid Traders');
  assert.match(PAGE, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/hrc"/);
  const catalog = loadCatalog();
  assert.equal(catalog.readable, true);
  assert.equal(catalog.tools.length, 1);
  const card = catalog.tools[0];
  assert.equal(card.id, 'kink-cap');
  assert.deepEqual(card.chains, ['hyperliquid']);
  assert.equal(card.route, '/hrc');
  assert.equal(card.status, 'runtime');
  assert.equal(card.flag, 'HRC_ENABLED');
  assert.equal(card.description.includes('$'), false);
  assert.match(card.description, /synthetic/);
  const hidden = renderPage(false);
  assert.equal(hidden.includes('href="/hrc"'), false);
  assert.match(hidden, /Coming soon/);
  assert.equal(/\b20\d\d\b/.test(hidden), false);
  const shown = renderPage(true);
  assert.match(shown, /href="\/hrc"/);
  assert.match(shown, /Coming soon/);
  assert.equal(/\b20\d\d\b/.test(shown), false);
});

test('the public is 404 and an operator sees rates without the shock', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hrc-'));
  const history = path.join(dir, 'history.json');
  fs.writeFileSync(history, JSON.stringify({
    readable: true,
    network: 'testnet',
    synthetic: true,
    source: 'info_api',
    unit: 'human',
    supplied: '1000000',
    borrowed: '850000',
    oracle: '1',
    funding_apr: '0.1095',
    strike: '0.05',
    rates: ['0.05', '0.05', '0.05', '0.05', '0.05', '0.2875'],
  }));
  const prevHist = process.env.HRC_HISTORY_PATH;
  const prevFlag = process.env.HRC_ENABLED;
  process.env.HRC_HISTORY_PATH = history;
  delete process.env.HRC_ENABLED;

  const { pool } = require('../db');
  pool.users.push({ id: 88001, email: 'hrc-admin@example.com', plan: 'admin' });
  pool.users.push({ id: 88002, email: 'hrc-stranger@example.com', plan: 'free' });
  const admin = sign(88001, 'hrc-admin@example.com');
  const stranger = sign(88002, 'hrc-stranger@example.com');

  const app = express();
  app.use(pageGate);
  app.use('/js', express.static(path.join(APP, 'public', 'js')));
  app.get('/hrc', (req, res) => { sendPage(req, res); });
  app.get('/tools', (req, res) => { require('../lib/tools_page').sendPage(req, res); });
  app.use('/api/hrc', hrcRouter);
  const server = await listen(app);
  try {
    const anonPage = await get(server, '/hrc');
    assert.equal(anonPage.status, 404);
    const anonFile = await get(server, '/hrc.html');
    assert.equal(anonFile.status, 404);
    const anonJs = await get(server, '/js/hrc.js');
    assert.equal(anonJs.status, 404);
    const anonApi = await get(server, '/api/hrc');
    assert.equal(anonApi.status, 404);
    const strangerPage = await get(server, '/hrc', stranger);
    assert.equal(strangerPage.status, 404);
    const strangerApi = await get(server, '/api/hrc', stranger);
    assert.equal(strangerApi.status, 404);

    const preview = await get(server, '/hrc', admin);
    assert.equal(preview.status, 200);
    assert.match(preview.body, /Admin preview\. Not public\./);
    assert.equal(preview.body.includes('$'), false);
    assert.equal(preview.body.includes('rate_shock'), false);

    const dash = await get(server, '/api/hrc', admin);
    assert.equal(dash.status, 200);
    const view = JSON.parse(dash.body);
    assert.equal(view.readable, true);
    assert.equal(view.preview, true);
    assert.equal(view.synthetic, true);
    assert.equal(view.utilization, '0.85');
    assert.equal(view.borrow_apy, '0.2875');
    assert.equal(view.kind, 'spread');
    assert.equal(view.ceiling, '0.40');
    assert.ok(view.points_to_crossover.length < 20, view.points_to_crossover);
    assert.ok(view.trailing_mean.length < 20, view.trailing_mean);
    assert.equal(Object.hasOwn(view, 'rate_shock_loss_24h'), false);
    assert.equal(JSON.stringify(view).includes('$'), false);
    assert.equal(view.replays.scenario_a.readable, false);

    const shock = await get(server, '/api/hrc/shock', admin);
    const packet = JSON.parse(shock.body);
    assert.equal(shock.status, 200);
    assert.equal(typeof packet.rate_shock_loss_24h, 'string');
    assert.notEqual(packet.rate_shock_loss_24h, '0');
    assert.equal(packet.borrow_apy, '0.2875');

    process.env.HRC_ENABLED = 'yes';
    const stillOff = await get(server, '/api/hrc', admin);
    assert.equal(JSON.parse(stillOff.body).preview, true);
    const publicStill = await get(server, '/api/hrc');
    assert.equal(publicStill.status, 404);

    process.env.HRC_ENABLED = '1';
    const live = await get(server, '/hrc', admin);
    assert.equal(live.status, 200);
    assert.equal(live.body.includes('Admin preview. Not public.'), false);
    const liveApi = JSON.parse((await get(server, '/api/hrc', admin)).body);
    assert.equal(liveApi.preview, false);
    const strangerOn = await get(server, '/hrc', stranger);
    assert.equal(strangerOn.status, 404);
    const anonOn = await get(server, '/api/hrc/shock');
    assert.equal(anonOn.status, 404);

    const toolsAnon = await get(server, '/tools');
    assert.equal(toolsAnon.status, 404);
    const tools = await get(server, '/tools', admin);
    assert.equal(tools.status, 200);
    assert.match(tools.body, /Coming soon/);
    assert.match(tools.body, /synthetic/);
    assert.match(tools.body, /href="\/hrc"/);
    assert.equal(/\b20\d\d\b/.test(tools.body), false);
    assert.equal(tools.body.includes('$'), false);

    fs.writeFileSync(history, JSON.stringify({
      readable: true, network: 'mainnet', synthetic: false, source: 'precompile',
      unit: 'human', supplied: '100', borrowed: '0', oracle: '1',
    }));
    const zeroed = JSON.parse((await get(server, '/api/hrc/shock', admin)).body);
    assert.equal(zeroed.rate_shock_loss_24h, '0');
    assert.equal(zeroed.borrow_apy, '0.05');

    fs.writeFileSync(history, JSON.stringify({
      readable: true, network: 'mainnet', synthetic: false, source: 'precompile',
      unit: 'human', supplied: '100', borrowed: '50',
    }));
    const unread = JSON.parse((await get(server, '/api/hrc/shock', admin)).body);
    assert.equal(unread.rate_shock_loss_24h, null);
    assert.equal(unread.borrow_apy, '0.05');
  } finally {
    server.close();
    if (prevHist == null) delete process.env.HRC_HISTORY_PATH;
    else process.env.HRC_HISTORY_PATH = prevHist;
    if (prevFlag == null) delete process.env.HRC_ENABLED;
    else process.env.HRC_ENABLED = prevFlag;
  }
});

test('paint writes the series and the ceiling, and a missing one stays unreadable', async () => {
  const vm = require('node:vm');

  function mount(packet) {
    const out = {
      children: [],
      replaceChildren() { this.children = []; },
      appendChild(child) { this.children.push(child); },
    };
    function createElement() {
      return {
        className: '',
        textContent: '',
        children: [],
        appendChild(child) { this.children.push(child); },
      };
    }
    const context = vm.createContext({
      document: {
        getElementById() { return out; },
        createElement,
      },
      fetch() {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(packet) });
      },
      AbortSignal,
    });
    vm.runInContext(PAGE_JS, context);
    return out;
  }

  function rowsOf(out) {
    return Object.fromEntries(out.children
      .filter((node) => node.className === 'hrc-row')
      .map((row) => row.children.map((child) => child.textContent)));
  }

  const painted = mount({
    readable: true,
    synthetic: true,
    network: 'testnet',
    source: 'info_api',
    utilization: '0.85',
    borrow_apy: '0.2875',
    kind: 'spread',
    ceiling: '0.40',
    points_to_kink: '0',
    points_to_crossover: '1',
    borrow_cap_ratio: '0.2',
    trailing_mean: '0.05',
    premium: '0.001',
    cap_hit: false,
    replays: { scenario_a: { readable: false }, scenario_b: { readable: false } },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const rows = rowsOf(painted);
  assert.equal(rows.Series, 'spread');
  assert.equal(rows.Ceiling, '0.40');
  assert.equal(rows.scenario_a, 'unreadable');
  assert.equal(JSON.stringify(rows).includes('$'), false);

  const missing = mount({ readable: true, kind: null, ceiling: '' });
  await new Promise((resolve) => setImmediate(resolve));
  const blank = rowsOf(missing);
  assert.equal(blank.Series, 'unreadable');
  assert.equal(blank.Ceiling, 'unreadable');
});

test('the gate is mounted ahead of static', () => {
  const serverSrc = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  const gateAt = serverSrc.indexOf("require('./lib/hrc_flag').pageGate");
  const staticAt = serverSrc.indexOf('express.static');
  assert.ok(gateAt > 0 && gateAt < staticAt);
  assert.match(serverSrc, /app\.get\('\/hrc'/);
  assert.match(serverSrc, /app\.get\('\/tools'/);
  assert.match(serverSrc, /app\.use\('\/api\/hrc'/);
});
