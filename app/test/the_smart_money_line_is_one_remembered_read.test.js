'use strict';
/**
 * The smart-money line (lib/smart_money.js): Nansen's 24h net flow for a
 * card's symbol, read through Flovia, remembered for a day, printed as a
 * line and never as a verdict.
 *
 * Flovia is a stub MCP server here that answers CoinGecko's search and coin
 * lookups and Nansen's netflow from fixtures, so both lookups, the native
 * and the contract cases, the uncovered coin, the missing row and a spent
 * window are each driven end to end. Then the bot-secret route, the duel
 * card's attachment (a peek, never a lookup) and the member feed.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;
process.env.FLOVIA_KEY = 'flv_test_only';

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const flovia = require('../lib/flovia');
const sm = require('../lib/smart_money');
const ctx = require('../lib/duel_context');
const { pool } = require('../db');

const COINS = {
  SOL: [{ id: 'solana', symbol: 'SOL', market_cap_rank: 6 }, { id: 'solana-bridged', symbol: 'SOL', market_cap_rank: null }],
  ARB: [{ id: 'arbitrum', symbol: 'ARB', market_cap_rank: 50 }],
  BTC: [{ id: 'bitcoin', symbol: 'BTC', market_cap_rank: 1 }],
  ZZZ: [],
};
const PLATFORMS = {
  arbitrum: { ethereum: '0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1', 'arbitrum-one': '0x912ce59144191c1204e64559fe8253a0e49e6548' },
  bitcoin: {},
};
let netflowRows = [];
let refuseNansen = false;
let lookups = [];

function answer(msg, data, isError) {
  return { status: 200, ok: true, headers: { get: (k) => (k === 'content-type' ? 'application/json' : k === 'mcp-session-id' ? 's' : null) },
    text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { isError: !!isError, content: [{ type: 'text', text: JSON.stringify(data) }] } }) };
}
flovia.setFetch(async (url, init) => {
  const msg = JSON.parse(init.body);
  if (msg.method === 'initialize') return answer(msg, {}) && { ...answer(msg, {}), text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2025-06-18' } }) };
  if (msg.method === 'notifications/initialized') return { status: 202, ok: true, headers: { get: () => null }, text: async () => '' };
  const { name, arguments: a } = msg.params;
  lookups.push({ name, a });
  if (name === 'flovia_coingecko' && a.path === '/search') return answer(msg, { coins: COINS[a.query.query] || [] });
  if (name === 'flovia_coingecko' && a.path.startsWith('/coins/')) return answer(msg, { id: a.path.slice(7), platforms: PLATFORMS[a.path.slice(7)] || {} });
  if (name === 'flovia_nansen') {
    if (refuseNansen) return answer(msg, { explanation: { meaning: 'your free week is used up' } }, true);
    return answer(msg, { data: netflowRows });
  }
  return answer(msg, { error: 'unknown tool' }, true);
});

test('a native coin: searched, matched by rank, asked for with the native flag, remembered for a day', async () => {
  netflowRows = [{ chain: 'solana', token_symbol: 'SOL', is_native: true, net_flow_24h_usd: 1234567.8 }];
  const r = await sm.smartMoneyReading('SOL/USDT:USDT');
  assert.equal(r.read, true);
  assert.equal(r.net_flow_usd, 1234567.8);
  assert.equal(r.chain, 'solana');
  assert.equal(r.credit, 'Data provided by Nansen');
  const nansen = lookups.find((l) => l.name === 'flovia_nansen');
  assert.deepStrictEqual(nansen.a.body.chains, ['solana']);
  assert.deepStrictEqual(nansen.a.body.filters, { include_native_tokens: true });
  assert.equal(nansen.a.endpoint, '/api/v1/smart-money/netflow');
  const before = lookups.length;
  const again = await sm.smartMoneyReading('SOL');
  assert.equal(again.read, true);
  assert.equal(lookups.length, before, 'the second read came from the table');
  assert.equal(again.remembered, true);
});

test('a contract token: the first chain Nansen tracks, by address; the row matched by address', async () => {
  lookups = [];
  // Another token's row FIRST, then THIS address on another chain (a bridged
  // token shares its address): a reader that took the first row would print
  // 99, one that matched the address alone would print 77, and a fixture
  // with ARB's ethereum row first could tell neither apart.
  netflowRows = [
    { chain: 'ethereum', token_address: '0xother', net_flow_24h_usd: 99 },
    { chain: 'arbitrum', token_address: '0xb50721bcf8d664c30412cfbc6cf7a15145234ad1', net_flow_24h_usd: 77 },
    { chain: 'ethereum', token_address: '0xb50721bcf8d664c30412cfbc6cf7a15145234ad1', net_flow_24h_usd: -340000 },
  ];
  const r = await sm.smartMoneyReading('ARB');
  assert.equal(r.read, true);
  assert.equal(r.net_flow_usd, -340000, 'the row for THIS address on THIS chain, not the first row nor the first address match');
  assert.equal(r.chain, 'ethereum');
  const nansen = lookups.find((l) => l.name === 'flovia_nansen');
  assert.deepStrictEqual(nansen.a.body.filters, { token_address: '0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1' });
});

test('a coin with no tracked chain, an unknown ticker, a missing row and a spent window are four reasons, none a zero', async () => {
  let r = await sm.smartMoneyReading('BTC');
  assert.deepStrictEqual([r.read, r.reason], [false, 'not covered']);
  r = await sm.smartMoneyReading('ZZZ');
  assert.deepStrictEqual([r.read, r.reason], [false, 'not covered']);
  // A token Nansen knows but did not list in its answer.
  pool.floviaReads.delete('nansen:netflow:ARB');
  netflowRows = [{ chain: 'ethereum', token_address: '0xsomeone-else', net_flow_24h_usd: 5 }];
  r = await sm.smartMoneyReading('ARB');
  assert.deepStrictEqual([r.read, r.reason], [false, 'absent']);
  pool.floviaReads.delete('nansen:netflow:ARB');
  refuseNansen = true;
  r = await sm.smartMoneyReading('ARB');
  assert.deepStrictEqual([r.read, r.reason], [false, 'refused']);
  assert.match(r.detail, /free week/);
  refuseNansen = false;
  // The refusal is remembered briefly: no second lookup at once.
  const n = lookups.length;
  r = await sm.smartMoneyReading('ARB');
  assert.equal(r.reason, 'refused');
  assert.equal(lookups.length, n);
  pool.floviaReads.delete('nansen:netflow:ARB');
  // No key: unconfigured, before any lookup.
  const k = process.env.FLOVIA_KEY; delete process.env.FLOVIA_KEY;
  r = await sm.smartMoneyReading('ARB');
  assert.deepStrictEqual(r, { read: false, reason: 'unconfigured' });
  process.env.FLOVIA_KEY = k;
});

test('peek never looks anything up, and the duel view carries only what was remembered', async () => {
  lookups = [];
  assert.equal(await sm.peekSmartMoney('NEVER'), null);
  assert.equal(lookups.length, 0);
  const sol = await sm.peekSmartMoney('SOL');
  assert.equal(sol.read, true);
  const view = await ctx.attachSmartMoney({ id: 1 }, { symbol: 'SOL', symbol_b: 'NEVER', kind: 'mover' });
  assert.equal(view.smart_money.net_flow_usd, 1234567.8);
  assert.ok(!('smart_money_b' in view), 'nothing remembered, nothing claimed');
  const bare = await ctx.attachSmartMoney({ id: 3 }, { symbol: 'NEVER', kind: 'direction' });
  assert.deepStrictEqual(Object.keys(bare), ['id'], 'a round with nothing remembered carries no key at all, not a not-read');
  const sports = await ctx.attachSmartMoney({ id: 2 }, { kind: 'sports', symbol: null });
  assert.ok(!('smart_money' in sports));
  assert.equal(lookups.length, 0, 'attaching peeks; it never spends');
  // Priming reads each market symbol once, and is not awaited by a deal.
  netflowRows = [{ chain: 'ethereum', token_address: '0xb50721bcf8d664c30412cfbc6cf7a15145234ad1', net_flow_24h_usd: 7 }];
  pool.floviaReads.delete('nansen:netflow:ARB');
  await ctx.primeSmartMoney([{ kind: 'direction', symbol: 'ARB' }, { kind: 'mover', symbol: 'ARB', symbol_b: 'SOL' }, { kind: 'sports' }]);
  assert.deepStrictEqual(ctx.symbolsOf([{ kind: 'direction', symbol: 'ARB' }, { kind: 'mover', symbol: 'ARB', symbol_b: 'SOL' }, { kind: 'sports' }]), ['ARB', 'SOL']);
  assert.equal((await sm.peekSmartMoney('ARB')).net_flow_usd, 7);
});

test('the member feed attaches its lines in ONE read for the whole page, only read lines; a visitor gets none', async () => {
  // Remembered: SOL (read, from the first test), ARB (read, primed here) and
  // a refusal for XYZ, which is remembered so it is not re-asked and must
  // not become a line. NEVER was never asked.
  netflowRows = [{ chain: 'ethereum', token_address: '0xb50721bcf8d664c30412cfbc6cf7a15145234ad1', net_flow_24h_usd: 7 }];
  pool.floviaReads.delete('nansen:netflow:ARB');
  await sm.smartMoneyReading('ARB');
  const { remember } = require('../lib/flovia_reads');
  await remember('nansen:netflow:XYZ', 60000, async () => ({ read: false, reason: 'refused' }));
  lookups = [];
  const realExecute = pool.execute.bind(pool);
  let cacheReads = 0;
  pool.execute = async (sql, params) => {
    if (/FROM flovia_reads/i.test(String(sql))) cacheReads += 1;
    return realExecute(sql, params);
  };
  try {
    const lines = await sm.peekSmartMoneyMany(['SOL/USDT:USDT', 'ARB', 'SOL', 'XYZ', 'NEVER', null, 'BTC/USDT']);
    assert.equal(cacheReads, 1, 'seven symbols, one read');
    assert.deepStrictEqual([...lines.keys()].sort(), ['ARB', 'SOL']);
    assert.equal(lines.get('SOL').net_flow_usd, 1234567.8);
    assert.equal(lines.get('ARB').net_flow_usd, 7);
    assert.ok(!lines.has('XYZ'), 'a remembered refusal is not a line');
    assert.equal(lookups.length, 0, 'peeking spends nothing');

    // The feed itself: five live rows over four tickers, read by a member and by a visitor.
    const app = express();
    app.use(express.json());
    app.use('/api/auth', require('../auth').router);
    app.use('/api/signals', require('../routes/signals'));
    const server = http.createServer(app);
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    const base = `http://127.0.0.1:${server.address().port}`;
    const req = (method, p, { token, body } = {}) => new Promise((resolve, reject) => {
      const payload = body ? JSON.stringify(body) : null;
      const r = http.request(`${base}${p}`, { method, headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
        let d = ''; res.on('data', (c) => { d += c; });
        res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
      });
      r.on('error', reject); if (payload) r.write(payload); r.end();
    });
    try {
      const at = new Date();
      for (const [key, symbol] of [['sm-feed-1', 'SOL/USDT'], ['sm-feed-2', 'ARB/USDT'], ['sm-feed-3', 'SOL/USDT'],
        ['sm-feed-4', 'XYZ/USDT'], ['sm-feed-5', 'NEVER/USDT']]) {
        await pool.execute(
          'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          [key, symbol, 'LONG', 0.7, 1, 't', 'trend', 100, 95, 110, 2, 't', 'NEW', null, at, null, new Date(at.getTime() + 600000)]);
      }
      const reg = await req('POST', '/api/auth/register', { body: { email: `sm${Date.now()}@x.io`, password: 'longenough1' } });
      assert.ok(reg.data.token, 'registered');
      cacheReads = 0;
      const member = await req('GET', '/api/signals?limit=200', { token: reg.data.token });
      assert.equal(member.status, 200);
      assert.equal(cacheReads, 1, `five rows, four tickers: one read, not ${cacheReads}`);
      const by = Object.fromEntries(member.data.signals.filter((s) => String(s.signal_key).startsWith('sm-feed-')).map((s) => [s.signal_key, s]));
      assert.equal(Object.keys(by).length, 5, 'all five rows listed');
      assert.equal(by['sm-feed-1'].smart_money.net_flow_usd, 1234567.8);
      assert.equal(by['sm-feed-3'].smart_money.net_flow_usd, 1234567.8, 'the second SOL row carries the same line');
      assert.equal(by['sm-feed-2'].smart_money.net_flow_usd, 7);
      assert.ok(!('smart_money' in by['sm-feed-4']), 'a remembered refusal is no line on the feed');
      assert.ok(!('smart_money' in by['sm-feed-5']), 'never asked, nothing claimed');
      assert.equal(lookups.length, 0, 'the feed spends nothing');
      cacheReads = 0;
      const visitor = await req('GET', '/api/signals?limit=200');
      assert.equal(visitor.status, 200);
      assert.equal(cacheReads, 0, "a visitor's page peeks nothing");
      for (const s of visitor.data.signals) assert.ok(!('smart_money' in s), "Nansen's data is shown to members, not republished");
    } finally { server.close(); }
  } finally { pool.execute = realExecute; }
});

test('the bot-secret route answers the reading as it is, and refuses without the secret', async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (path, secret) => new Promise((resolve, reject) => {
    http.get(`${base}${path}`, { headers: secret ? { 'X-Bot-Secret': secret } : {} }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
    }).on('error', reject);
  });
  try {
    assert.equal((await get('/api/bot/sync/context/smart-money?symbol=SOL')).status, 403);
    const ok = await get('/api/bot/sync/context/smart-money?symbol=SOL', process.env.BOT_SYNC_SECRET);
    assert.equal(ok.status, 200);
    assert.equal(ok.body.reading.read, true);
    assert.equal(ok.body.reading.net_flow_usd, 1234567.8);
    const btc = await get('/api/bot/sync/context/smart-money?symbol=BTC', process.env.BOT_SYNC_SECRET);
    assert.deepStrictEqual([btc.status, btc.body.reading.read, btc.body.reading.reason], [200, false, 'not covered']);
    assert.equal((await get('/api/bot/sync/context/smart-money', process.env.BOT_SYNC_SECRET)).status, 400);
  } finally { server.close(); }
});

test('the shared round renderer prints the line only from a read value, as a line', () => {
  const D = require('../public/js/duel.js');
  assert.equal(D.compactUsd(1234567.8), '+$1.2M');
  assert.equal(D.compactUsd(-340000), '-$340K');
  assert.equal(D.compactUsd(12), '+$12');
  assert.equal(D.compactUsd('x'), '');
  assert.equal(D.compactUsd(null), '', 'Number(null) is 0; absence is not a flow');
  assert.equal(D.smartMoneyText({ read: true, net_flow_usd: null }, (k, fb) => fb), '', 'a read with no figure is no line');
  const tr = (k, fb, vars) => Object.entries(vars || {}).reduce((s, [a, b]) => s.replace('{' + a + '}', b), fb);
  assert.equal(D.smartMoneyText({ read: true, net_flow_usd: 1234567.8, provider: 'Nansen' }, tr), 'Smart money 24h: +$1.2M in · Nansen');
  assert.equal(D.smartMoneyText({ read: true, net_flow_usd: -5000 }, tr), 'Smart money 24h: -$5K out · Nansen');
  assert.equal(D.smartMoneyText({ read: false, reason: 'refused' }, tr), '', 'not read is no line, never 0');
  assert.equal(D.smartMoneyText({ read: false, reason: 'refused', net_flow_usd: 5 }, tr), '', 'a figure beside read:false is not a reading');
  assert.equal(D.smartMoneyText({ read: true, net_flow_usd: 0 }, tr), 'Smart money 24h: +$0 in · Nansen', 'a measured zero is printed');
  assert.equal(D.smartMoneyHtml({ symbol: 'ARB' }, tr), '');
  const html = D.smartMoneyHtml({ symbol: 'ARB', symbol_b: 'SOL', smart_money: { read: true, net_flow_usd: 7 }, smart_money_b: { read: true, net_flow_usd: -8 } }, tr);
  assert.match(html, /^<p class="d-ctx">ARB: Smart money 24h: \+\$7 in · Nansen<br>SOL: Smart money 24h: -\$8 out · Nansen<\/p>$/);
});
