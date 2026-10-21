'use strict';
/**
 * /memeclaw in Chromium, with a stand-in wallet that records every request
 * the page makes of it, and the API answered by fixtures.
 *
 * What the page must do: hand the wallet exactly the transaction the server
 * planned (and nothing it wrote itself), ask for the chain the server
 * described, give the hash back to the server, draw a creator's words as text,
 * and say "no wallet", "you declined" and "not open yet" as what they are.
 */
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
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
const XSS = '<img src=x id="injected" onerror="window.__xss=1">wow';
const COIN = { address: TOKEN, name: 'Much Claw', symbol: 'CLAW', description: XSS, terms: 'recorded',
  terms_hash: '0x' + '0e'.repeat(32), creator: ALICE, launched_at: '2026-10-21T12:00:00.000Z', tx_hash: TX,
  block_number: 900, receipt: 'launch:' + 'c'.repeat(64), doge_reserve: String(10n ** 18n), token_reserve: String(9n * 10n ** 26n),
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
  await ctx.route('**/api/memeclaw/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const body = req.postData() ? JSON.parse(req.postData()) : null;
    seen.push({ method: req.method(), path: url.pathname, body });
    const json = (status, b) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(b) });
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
    await page.waitForSelector('.mc-row');
    assert.match(await page.textContent('.mc-row'), /CLAW/);

    // The coin page draws the creator's words as text.
    await page.click('.mc-row');
    await page.waitForSelector('.mc-desc');
    assert.equal(await page.textContent('.mc-desc'), XSS, 'the description is shown as written, as text');
    assert.equal(await page.$('#injected'), null, 'and nothing in it became markup');
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.match(await page.textContent('.mc-reading'), /Holders: not read/);

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
    await page.waitForFunction(() => /not open yet/.test(document.querySelector('#mc-root').textContent));
    assert.equal(await page.$('[data-act="deploy"]'), null, 'the deploy door is the operator\'s only');
    await ctx.close();
  } finally {
    await browser.close();
    server.close();
  }
});
