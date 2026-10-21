'use strict';
/**
 * /memeclaw in Chromium, with a stand-in wallet that records every request
 * the page makes of it, and the API answered by fixtures.
 *
 * What the page must do: hand the wallet exactly the transaction the server
 * planned (and nothing it wrote itself), ask for the chain the server
 * described, give the hash back to the server, draw a creator's words as text,
 * and say "no wallet", "you declined" and "not open yet" as what they are.
 *
 * Practice mode, the page's first: it asks no wallet anything, keeps its key
 * in this browser and sends it as a header, and confirms a trade no lower
 * than 1% under the quote it showed. A launch receipt is checked on the page.
 */
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const { pw, CHROMIUM, SKIP } = require('./helpers/pi_app_harness');

const PUBLIC = path.join(__dirname, '..', 'public');
const ALICE = '0x' + 'a1'.repeat(20);
const FACTORY = '0x' + 'fa'.repeat(20);
const TOKEN = '0x' + '70'.repeat(20);
const TX = '0x' + 'ab'.repeat(32);
const CHAIN = { key: 'dogeos-testnet', chain_id: 6281971, name: 'DogeOS testnet', testnet: true,
  rpcs: ['https://rpc.testnet.dogeos.com/'], explorer_tx: 'https://dogeos-testnet.l2scan.co/tx/',
  native: { name: 'Dogecoin', symbol: 'DOGE', decimals: 18 }, faucet: 'https://faucet.testnet.dogeos.com' };
const CONFIG = { enabled: true, reason: null, factory: FACTORY, chain: CHAIN,
  curve: { supply: String(10n ** 27n), virtual_doge: String(10n ** 19n), virtual_tokens: String(25n * 10n ** 25n), fee_bps: 0 },
  limits: { name_bytes: 32, symbol_bytes: 10, description: 280 } };
// A sealed launch whose seal is the SHA-256 of its payload, as the server stores it.
const SEALED = JSON.stringify({ v: 8, kind: 'memeclaw_launch', chain_id: 6281971, factory: FACTORY, token: TOKEN, creator: ALICE,
  name: 'Much Claw', symbol: 'CLAW', description: '', terms_hash: '0x' + '0e'.repeat(32), tx: TX, block_number: 900,
  launched_at: '2026-10-21T12:00:00.000Z', creator_bought: '0' });
const SEAL = crypto.createHash('sha256').update(SEALED).digest('hex');
const XSS = '<img src=x id="injected" onerror="window.__xss=1">wow';
const COIN = { address: TOKEN, name: 'Much Claw', symbol: 'CLAW', description: XSS, terms: 'recorded',
  terms_hash: '0x' + '0e'.repeat(32), creator: ALICE, launched_at: '2026-10-21T12:00:00.000Z', tx_hash: TX,
  block_number: 900, receipt: 'launch:' + SEAL, doge_reserve: String(10n ** 18n), token_reserve: String(9n * 10n ** 26n),
  sold_bps: 1000, price_doge: '0.00000001', max_buy: '1', trade_count: 1, last_trade_at: null, read_to_block: 900 };
const DETAIL = { coin: COIN, trades: [{ tx_hash: TX, log_index: 1, trader: ALICE, side: 'buy', doge_amount: String(10n ** 18n),
  token_amount: String(10n ** 26n), block_number: 900, block_time: null }],
  reading: { made_by: 'the Memeclaw factory', supply: String(10n ** 27n), creator_bought_at_launch_bps: 1000,
    creator_net_from_curve_bps: 1000, creator_coins_launched: 1, holders: null, holders_note: 'not read: trades only' } };
const PLAN = { chain_id: 6281971, from: ALICE, to: FACTORY, value: '0x0', data: '0x5c1c1a7f' + '00'.repeat(64),
  terms_hash: '0x' + '0e'.repeat(32), dev_buy: '0', expected_tokens: '0', deadline: 1 };

const WALLET = (opts) => `
window.__w = { calls: [] };
window.ethereum = { request: function (a) {
  window.__w.calls.push({ method: a.method, params: a.params || null });
  var o = ${JSON.stringify(opts || {})};
  if (a.method === 'eth_requestAccounts') return Promise.resolve(['${ALICE}']);
  if (a.method === 'wallet_switchEthereumChain') return Promise.reject({ code: 4902, message: 'Unrecognized chain' });
  if (a.method === 'wallet_addEthereumChain') return Promise.resolve(null);
  if (a.method === 'eth_sendTransaction') return o.decline ? Promise.reject({ code: 4001, message: 'User rejected' }) : Promise.resolve('${TX}');
  return Promise.reject(new Error('unexpected ' + a.method));
} };`;

async function serve() {
  delete process.env.DATABASE_URL;
  process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
  const app = express();
  app.use(express.static(PUBLIC));
  app.use('/memeclaw', require('../routes/memeclaw_page'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, { wallet = true, decline = false, config = CONFIG } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
  if (wallet) await ctx.addInitScript(WALLET({ decline }));
  const seen = [];
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
  await ctx.route('**/api/call/**', (route) => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(route.request().url().endsWith(SEAL) ? { seal: SEAL, sealed_at: '2026-10-21T12:00:05.000Z', seal_payload: SEALED } : {}) }));
  await ctx.route('**/api/memeclaw/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const body = req.postData() ? JSON.parse(req.postData()) : null;
    seen.push({ method: req.method(), path: url.pathname, body });
    const json = (status, b) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(b) });
    if (url.pathname.startsWith('/api/memeclaw/paper/')) return json(503, { error: 'not in this test' });
    if (url.pathname === '/api/memeclaw/config') return json(200, config);
    if (url.pathname === '/api/memeclaw/deploy-plan') return json(401, { error: 'Not authenticated' });
    if (url.pathname === '/api/memeclaw/coins' && req.method() === 'GET') return json(200, { coins: [COIN], count: 1 });
    if (url.pathname === `/api/memeclaw/coins/${TOKEN}`) return json(200, DETAIL);
    if (url.pathname === '/api/memeclaw/launch-plan') return json(200, PLAN);
    if (url.pathname === '/api/memeclaw/coins' && req.method() === 'POST') return json(200, { coin: COIN, sealed: true });
    return json(404, { error: 'not in this test' });
  });
  return { ctx, seen };
}

test('a launch hands the wallet the server\'s transaction, on the server\'s chain, and the hash back to the server', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    const { ctx, seen } = await context(browser);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`${base}/memeclaw`);
    await page.click('[data-mode="testnet"]');
    await page.waitForSelector('.mc-row');
    assert.match(await page.textContent('.mc-row'), /CLAW/);

    // The coin page draws the creator's words as text.
    await page.click('.mc-row');
    await page.waitForSelector('.mc-desc');
    assert.equal(await page.textContent('.mc-desc'), XSS, 'the description is shown as written, as text');
    assert.equal(await page.$('#injected'), null, 'and nothing in it became markup');
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.match(await page.textContent('.mc-reading'), /Holders: not read/);

    // The launch receipt is checked here, in this browser, not on another site.
    await page.click('[data-act="receipt"]');
    await page.waitForFunction(() => /Verified in your browser/.test(document.querySelector('#mc-receipt').textContent));
    assert.match(await page.textContent('#mc-receipt'), /CLAW · Much Claw/);
    assert.match(await page.textContent('#mc-receipt'), /Not yet in a daily root/);

    // Launch.
    await page.click('[data-view="launch"]');
    await page.fill('#mc-name', 'Much Claw');
    await page.fill('#mc-symbol', 'claw');
    assert.equal(await page.inputValue('#mc-symbol'), 'CLAW', 'the symbol is typed in capitals');
    await page.click('[data-act="launch"]');
    await page.waitForFunction(() => /Launched/.test(document.body.textContent) || document.querySelector('.mc-desc'));

    const calls = await page.evaluate(() => window.__w.calls);
    const methods = calls.map((c) => c.method);
    assert.deepEqual(methods, ['eth_requestAccounts', 'wallet_switchEthereumChain', 'wallet_addEthereumChain', 'eth_sendTransaction']);
    assert.deepEqual(calls[1].params, [{ chainId: '0x5fdaf3' }], 'the chain the server described');
    assert.equal(calls[2].params[0].chainName, 'DogeOS testnet');
    assert.deepEqual(calls[2].params[0].rpcUrls, CHAIN.rpcs);
    assert.deepEqual(calls[3].params, [{ from: ALICE, to: FACTORY, value: '0x0', data: PLAN.data }],
      'exactly the planned transaction, nothing the page wrote');
    const planReq = seen.find((s) => s.path === '/api/memeclaw/launch-plan');
    assert.equal(planReq.body.creator, ALICE);
    assert.equal(planReq.body.symbol, 'CLAW');
    const record = seen.find((s) => s.path === '/api/memeclaw/coins' && s.method === 'POST');
    assert.deepEqual(record.body, { tx_hash: TX }, 'the hash goes back to the server, which reads the chain');
    assert.deepEqual(errors, []);
    await ctx.close();
  } finally {
    await browser.close();
    server.close();
  }
});

test('no wallet, a declined send and a closed launchpad are each said as what they are', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    // No wallet in the browser.
    let { ctx } = await context(browser, { wallet: false });
    let page = await ctx.newPage();
    await page.goto(`${base}/memeclaw`);
    await page.click('[data-mode="testnet"]');
    await page.waitForSelector('.mc-row');
    assert.match(await page.textContent('#mc-wallet'), /No wallet in this browser/);
    await page.click('[data-view="launch"]');
    await page.fill('#mc-name', 'Much Claw');
    await page.fill('#mc-symbol', 'CLAW');
    await page.click('[data-act="launch"]');
    await page.waitForFunction(() => /has no wallet/.test(document.querySelector('#mc-launch-note').textContent));
    await ctx.close();

    // Declined in the wallet: no warning colour, and nothing is recorded.
    let seen;
    ({ ctx, seen } = await context(browser, { decline: true }));
    page = await ctx.newPage();
    await page.goto(`${base}/memeclaw`);
    await page.click('[data-mode="testnet"]');
    await page.click('[data-view="launch"]');
    await page.fill('#mc-name', 'Much Claw');
    await page.fill('#mc-symbol', 'CLAW');
    await page.click('[data-act="launch"]');
    await page.waitForFunction(() => /You declined/.test(document.querySelector('#mc-launch-note').textContent));
    assert.equal(await page.getAttribute('#mc-launch-note', 'class'), 'mc-note', 'a no is not a warning');
    assert.ok(!seen.some((s) => s.path === '/api/memeclaw/coins' && s.method === 'POST'), 'nothing to record');
    await ctx.close();

    // Not open yet.
    ({ ctx } = await context(browser, { config: { ...CONFIG, enabled: false, reason: 'not_deployed', factory: undefined } }));
    page = await ctx.newPage();
    await page.goto(`${base}/memeclaw`);
    await page.click('[data-mode="testnet"]');
    await page.waitForFunction(() => /not open yet/.test(document.querySelector('#mc-root').textContent));
    assert.equal(await page.$('[data-act="deploy"]'), null, 'the deploy door is the operator\'s only');
    await ctx.close();
  } finally {
    await browser.close();
    server.close();
  }
});

test('practice asks no wallet anything, keeps its key here, and confirms no lower than 1% under the quote', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  const KEY = 'e'.repeat(64);
  const PCOIN = { mode: 'practice', id: 'p' + '1'.repeat(16), name: 'Pup One', symbol: 'PUP', description: '', creator: 'p' + '2'.repeat(16),
    created_at: '2026-10-21T12:00:00.000Z', doge_reserve: '0', token_reserve: String(10n ** 27n), sold_bps: 0, price_doge: '0.00000004',
    max_buy: String(40n * 10n ** 18n), trade_count: 0, last_trade_at: null };
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
    await ctx.addInitScript(WALLET({}));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
    const seen = [];
    let coins = [];
    let meFails = false;
    await ctx.route('**/api/memeclaw/**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const body = req.postData() ? JSON.parse(req.postData()) : null;
      seen.push({ method: req.method(), path: url.pathname, body, key: req.headers()['x-memeclaw-practice'] || null });
      const json = (status, b) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(b) });
      const p = url.pathname.replace('/api/memeclaw', '');
      if (p === '/config') return json(200, CONFIG);
      if (p === '/deploy-plan') return json(401, { error: 'Not authenticated' });
      if (p === '/paper/players') return json(200, { key: KEY, player: { id: 'p' + '2'.repeat(16), practice_doge: String(100n * 10n ** 18n) } });
      if (p === '/paper/me' && meFails) return json(503, { error: 'player_unavailable', exception: 'Error' });
      if (p === '/paper/me') return json(200, { player: { id: 'p' + '2'.repeat(16), practice_doge: String(100n * 10n ** 18n) }, holdings: [],
        refill: { below: String(10n ** 18n), to: String(100n * 10n ** 18n), next_at: null }, launches_left_today: 5 });
      if (p === '/paper/coins') return json(200, { coins, count: coins.length });
      if (p === '/paper/launch') { coins = [PCOIN]; return json(200, { coin: PCOIN }); }
      if (p === `/paper/coins/${PCOIN.id}`) return json(200, { coin: PCOIN, trades: [] });
      if (p === '/paper/quote') return json(200, { gets: '1000000' });
      if (p === '/paper/trade') return json(200, { ok: true, gets: '1000000', practice_doge: '1', held: '1000000' });
      return json(404, { error: 'not in this test' });
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`${base}/memeclaw`);
    assert.equal(await page.getAttribute('[data-mode="practice"]', 'aria-pressed'), 'true', 'practice is the first mode');
    await page.waitForFunction(() => /No practice coin has been launched yet/.test(document.querySelector('#mc-root').textContent));
    await page.click('[data-act="paper-start"]');
    await page.waitForFunction(() => /100 practice DOGE/.test(document.querySelector('#mc-wallet').textContent));
    assert.equal(await page.evaluate(() => window.localStorage.getItem('memeclaw.practice.key')), KEY);

    await page.click('[data-view="launch"]');
    assert.match(await page.textContent('[data-act="launch"]'), /Launch as practice/);
    await page.fill('#mc-name', 'Pup One');
    await page.fill('#mc-symbol', 'pup');
    await page.click('[data-act="launch"]');
    await page.waitForSelector('#mc-buy-amt');
    await page.fill('#mc-buy-amt', '1');
    await page.click('[data-act="buy"]');
    await page.waitForSelector('[data-act="paper-confirm"]');
    await page.click('[data-act="paper-confirm"]');
    await page.waitForFunction(() => /Done\./.test(document.querySelector('#mc-buy-note').textContent));
    assert.match(await page.textContent('.mc-kv'), /You hold0/, 'read, and none held: a measured 0');

    // The player cannot be read: what it holds is "not read", never 0.
    meFails = true;
    await page.click('[data-view="coins"]');
    await page.click('.mc-row');
    await page.waitForFunction(() => /You hold/.test((document.querySelector('.mc-kv') || {}).textContent || ''));
    await page.waitForFunction(() => /not read/.test(document.querySelector('#mc-wallet').textContent));
    assert.match(await page.textContent('.mc-kv'), /You holdnot read/);
    assert.match(await page.textContent('#mc-wallet'), /Practice balance not read/);

    const launch = seen.find((x) => x.path === '/api/memeclaw/paper/launch');
    assert.equal(launch.key, KEY, 'the key goes as a header');
    assert.equal(launch.body.symbol, 'PUP');
    const trade = seen.find((x) => x.path === '/api/memeclaw/paper/trade');
    assert.deepEqual(trade.body, { coin: PCOIN.id, action: 'buy', amount: String(10n ** 18n), min_out: '990000' },
      'the trade is the one quoted, refused below 1% under the quote');
    assert.equal(trade.key, KEY);
    assert.ok(!seen.some((x) => !x.path.startsWith('/api/memeclaw/paper/') && x.method === 'POST'), 'practice posts nothing to the testnet routes');
    assert.deepEqual(await page.evaluate(() => window.__w.calls), [], 'practice asks the wallet nothing');
    assert.deepEqual(errors, []);
    await ctx.close();
  } finally {
    await browser.close();
    server.close();
  }
});

test('the operator\'s starter coins: only the ones this wallet has not launched, and nothing when the list cannot be read', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  const STARTERS = [{ name: 'Much Claw', symbol: 'CLAW', description: 'd' }, { name: 'Testnet Woof', symbol: 'WOOF', description: 'd' },
    { name: 'Paw Print', symbol: 'PAW', description: 'd' }];
  const run = async (coinsAfterFirstRead) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
    await ctx.addInitScript(WALLET({}));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
    const planned = [];
    let reads = 0;
    let last = null;
    await ctx.route('**/api/memeclaw/**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const body = req.postData() ? JSON.parse(req.postData()) : null;
      const json = (status, b) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(b) });
      const p = url.pathname.replace('/api/memeclaw', '');
      if (p.startsWith('/paper/')) return json(503, { error: 'not in this test' });
      if (p === '/config') return json(200, CONFIG);
      if (p === '/deploy-plan') return json(200, { data: '0x60', value: '0x0', starters: STARTERS });
      if (p === '/coins' && req.method() === 'GET') {
        reads += 1;
        return reads === 1 ? json(200, { coins: [COIN], count: 1 }) : coinsAfterFirstRead(json);
      }
      if (p === '/launch-plan') { planned.push(body.symbol); last = body; return json(200, PLAN); }
      if (p === '/coins' && req.method() === 'POST') return json(200, { coin: { ...COIN, symbol: last.symbol, name: last.name }, sealed: true });
      return json(404, { error: 'not in this test' });
    });
    const page = await ctx.newPage();
    await page.goto(`${base}/memeclaw`);
    await page.click('[data-mode="testnet"]');
    await page.waitForSelector('[data-act="starters"]');
    await page.click('[data-act="starters"]');
    await page.waitForFunction(() => /Launched:|could not be read/.test(document.querySelector('#mc-starter-note').textContent));
    const out = { planned, note: await page.textContent('#mc-starter-note'), sends: (await page.evaluate(() => window.__w.calls)).filter((c) => c.method === 'eth_sendTransaction').length };
    await ctx.close();
    return out;
  };
  try {
    // CLAW is already this wallet's (COIN's creator is ALICE): only WOOF and PAW are launched.
    const fine = await run((json) => json(200, { coins: [COIN], count: 1 }));
    assert.deepEqual(fine.planned, ['WOOF', 'PAW']);
    assert.equal(fine.sends, 2, 'one wallet prompt for each');
    assert.match(fine.note, /Launched: WOOF, PAW\./);
    // The list cannot be read: which coins this wallet has is unknown, so nothing is sent.
    const blind = await run((json) => json(500, { error: 'coins_unavailable' }));
    assert.deepEqual(blind.planned, []);
    assert.equal(blind.sends, 0);
    assert.match(blind.note, /could not be read.*Nothing was sent/);
  } finally {
    await browser.close();
    server.close();
  }
});
