'use strict';
/**
 * The /token page's pool panel (lib/token_pool.js, routes/token.js,
 * public/js/token-page.js): the $RCLAW DEX pool as CoinGecko reports it
 * through Flovia, with three answers that get three sentences. "Not listed
 * yet" is CoinGecko's fact; "could not be read" is a fact about the read;
 * neither is a zero liquidity. Flovia is a stub; the route and the renderer
 * run for real.
 */
delete process.env.DATABASE_URL;
process.env.FLOVIA_KEY = 'flv_test';
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const flovia = require('../lib/flovia');
const { tokenPool, poolsOf } = require('../lib/token_pool');
const rclaw = require('../lib/rclaw_token');
const tp = require('../public/js/token-page.js');
const { pool } = require('../db');

let answer = null;     // CoinGecko's reply body, or 'refuse'
let asked = [];
function reply(msg, data, isError) {
  return { status: 200, ok: true, headers: { get: (k) => (k === 'content-type' ? 'application/json' : k === 'mcp-session-id' ? 's' : null) },
    text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { isError: !!isError, content: [{ type: 'text', text: JSON.stringify(data) }] } }) };
}
flovia.setFetch(async (url, init) => {
  const msg = JSON.parse(init.body);
  if (msg.method === 'initialize') return { ...reply(msg, {}), text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2025-06-18' } }) };
  if (msg.method === 'notifications/initialized') return { status: 202, ok: true, headers: { get: () => null }, text: async () => '' };
  asked.push(msg.params.arguments);
  if (answer === 'refuse') return reply(msg, { explanation: { meaning: 'window spent' } }, true);
  if (answer && answer.toolError) return reply(msg, answer.toolError, true);
  return reply(msg, answer);
});
const MINT = rclaw.readRecord().mint;
const forget = () => pool.floviaReads.delete(`cg:pool:${MINT}`);
test.beforeEach(() => { asked = []; forget(); process.env.FLOVIA_KEY = 'flv_test'; });

const POOL = (addr, reserve, vol, price, name = 'RCLAW / SOL', dex = 'raydium') => ({
  id: 'solana_' + addr, type: 'pool',
  attributes: { address: addr, name, reserve_in_usd: String(reserve), volume_usd: { h24: String(vol) }, base_token_price_usd: String(price) },
  relationships: { dex: { data: { id: dex, type: 'dex' } } },
});

test('a pool: the largest on the page, its market facts, the credit, and the mint in the path', async () => {
  answer = { data: [POOL('AAAA', 12000, 3400, 0.0021), POOL('BBBB', 250000.5, 90000, 0.0022, 'RCLAW / USDC', 'orca')] };
  const r = await tokenPool();
  assert.equal(r.listed, true);
  assert.equal(r.pool.address, 'BBBB', 'the largest liquidity on the page, not the first row');
  assert.deepStrictEqual([r.pool.dex, r.pool.reserve_usd, r.pool.volume_24h_usd, r.pool.price_usd], ['orca', 250000.5, 90000, 0.0022]);
  assert.equal(r.pool.url, 'https://www.geckoterminal.com/solana/pools/BBBB');
  assert.equal(r.credit, 'Data provided by CoinGecko');
  assert.match(r.note, /first page/);
  assert.equal(asked[0].path, `/onchain/networks/solana/tokens/${MINT}/pools`);
  const again = await tokenPool();
  assert.equal(asked.length, 1, 'remembered for ten minutes');
  assert.equal(again.pool.address, 'BBBB');
});

test('no pool is a fact CoinGecko reported; a refusal and no key are reads that failed', async () => {
  answer = { data: [] };
  let r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [false, 'no_pool']);
  forget();
  answer = 'refuse';
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'refused']);
  forget();
  delete process.env.FLOVIA_KEY;
  const before = asked.length;
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'unconfigured']);
  assert.equal(asked.length, before, 'no key, no lookup');
});

/** CoinGecko's own HTTP answer, as Flovia delivers it (a tool error that names who answered). */
function providerAnswer(status, by = 'coingecko', executed = 'yes') {
  return { toolError: { ok: false, status, response: '{"status":{"error_code":' + status + ',"error_message":"Not found"}}',
    explanation: { by, code: 'http_' + status, executed, meaning: `${by} answered HTTP ${status}; see response.` } } };
}

test("CoinGecko's 404 for the mint is 'no pool', a read; a 5xx and Flovia's own 404 are not", async () => {
  // The first live read (2026-10-20): the mint is not indexed, CoinGecko
  // answered 404, and the panel said "could not be read" about a token that
  // is simply not listed yet.
  answer = providerAnswer(404);
  let r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [false, 'no_pool']);
  assert.match(r.detail, /404/);
  // Remembered as a read: no second lookup inside the window.
  const before = asked.length;
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason, asked.length], [false, 'no_pool', before]);
  // A 5xx says nothing about the pool.
  forget(); answer = providerAnswer(500);
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'refused']);
  // Flovia's own 404 (an unknown path, nothing executed) is not CoinGecko's.
  forget(); answer = providerAnswer(404, 'flovia', 'no');
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'refused']);
  // A 404 CoinGecko is named on but did not answer (nothing executed) is
  // not its answer either: only an executed lookup's 404 is "no pool".
  forget(); answer = providerAnswer(404, 'coingecko', 'no');
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'refused']);
  // A 404 that no provider claims is not read as an answer either.
  forget(); answer = { toolError: { ok: false, status: 404, explanation: { meaning: 'not found' } } };
  r = await tokenPool();
  assert.deepStrictEqual([r.listed, r.reason], [null, 'refused']);
});

test('a row without an address or with unreadable figures is read as such, never as zero', () => {
  const rows = poolsOf({ data: [
    { id: 'solana_CCCC', attributes: { reserve_in_usd: 'n/a', volume_usd: {}, base_token_price_usd: null }, relationships: {} },
    { id: 'x', attributes: {}, relationships: {} },
    null,
  ] });
  assert.equal(rows.length, 2);
  assert.deepStrictEqual([rows[0].address, rows[0].reserve_usd, rows[0].volume_24h_usd, rows[0].price_usd, rows[0].dex], ['CCCC', null, null, null, null]);
});

test('the route: 200 with a pool, 200 not listed, 503 could not be read', async () => {
  const app = express();
  app.use('/api/token', require('../routes/token'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = () => new Promise((resolve, reject) => {
    http.get(`${base}/api/token/pool`, (res) => { let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(d) })); }).on('error', reject);
  });
  try {
    answer = { data: [POOL('AAAA', 12000, 3400, 0.0021)] };
    let r = await get();
    assert.equal(r.status, 200); assert.equal(r.body.listed, true); assert.equal(r.body.pool.address, 'AAAA');
    forget(); answer = { data: [] };
    r = await get();
    assert.equal(r.status, 200); assert.deepStrictEqual([r.body.listed, r.body.reason], [false, 'no_pool']);
    forget(); answer = 'refuse';
    r = await get();
    assert.equal(r.status, 503); assert.deepStrictEqual([r.body.error, r.body.listed, r.body.reason], ['pool_unreadable', null, 'refused']);
    forget(); answer = providerAnswer(404);
    r = await get();
    assert.equal(r.status, 200); assert.deepStrictEqual([r.body.listed, r.body.reason], [false, 'no_pool']);
  } finally { server.close(); }
});

test('the panel paints three sentences, and a figure the answer lacks is unreadable', () => {
  const listed = tp.poolHtml({ listed: true, read_at: new Date(Date.now() - 120000).toISOString(),
    pool: { address: 'BBBB', name: 'RCLAW / USDC', dex: 'orca', reserve_usd: 250000.5, volume_24h_usd: 90000, price_usd: 0.0022, url: 'https://www.geckoterminal.com/solana/pools/BBBB' } });
  assert.match(listed, /Where it trades/);
  assert.match(listed, /RCLAW \/ USDC · orca/);
  assert.match(listed, /\$250\.0K/); assert.match(listed, /\$90\.0K/); assert.match(listed, /\$0\.0022/);
  assert.match(listed, /geckoterminal\.com\/solana\/pools\/BBBB/);
  assert.match(listed, /read 2 minutes ago/);
  assert.match(listed, /Data provided by CoinGecko/);
  const partial = tp.poolHtml({ listed: true, pool: { address: 'X', reserve_usd: null, volume_24h_usd: 5, price_usd: null, url: 'https://evil.example/' } });
  assert.doesNotMatch(partial, /evil\.example/, 'a link is printed only to GeckoTerminal');
  assert.equal((partial.match(/tok-na/g) || []).length, 2, 'two unreadable figures, never two zeros');
  assert.doesNotMatch(partial, /\$0\.00/);
  const none = tp.poolHtml({ listed: false, reason: 'no_pool' });
  assert.match(none, /Not listed on a DEX yet/);
  assert.doesNotMatch(none, /could not be read/);
  for (const bad of [null, undefined, { listed: null, reason: 'refused' }, { error: 'pool_unreadable' }, 'junk']) {
    const html = tp.poolHtml(bad);
    assert.match(html, /could not be read/, JSON.stringify(bad));
    assert.doesNotMatch(html, /Not listed/);
  }
  assert.deepStrictEqual([tp.compactUsd(1234567), tp.compactUsd(0.5), tp.compactUsd(-1), tp.compactUsd('x'), tp.compactUsd(null), tp.compactUsd(undefined), tp.compactUsd(0)], ['$1.23M', '$0.5000', '', '', '', '', '$0.0000']);
});

test('render leaves a slot the panel boots into, and the strings exist in fourteen languages', () => {
  const html = tp.render(rclaw.publicRecord(rclaw.readRecord()));
  assert.match(html, /id="tok-pool"/);
  const i18n = require('fs').readFileSync(require('path').join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const k of ['tok.pool_h', 'tok.pool_none', 'tok.pool_unread', 'tok.pool_liq', 'tok.pool_vol', 'tok.pool_price', 'tok.pool_pool', 'tok.pool_note', 'tok.pool_open', 'tok.pool_credit']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${k}':`));
    assert.ok(line, k);
    for (const lang of ['en', 'es', 'zh', 'pt', 'fr', 'de', 'nl', 'ja', 'ko', 'ru', 'tr', 'it', 'hi', 'ar']) assert.ok(line.includes(` ${lang}: '`), `${k} lacks ${lang}`);
  }
});
