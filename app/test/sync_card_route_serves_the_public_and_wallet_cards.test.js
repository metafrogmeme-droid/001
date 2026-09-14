'use strict';
/**
 * Six more of the website chat's own cards reach the bot's sync route —
 * the four public reads (replay, letter, venue router, meme radar) and the
 * two wallet reads (wallet, DeFi) — each byte-for-byte the intercept's own
 * card for the same reader and arguments, with the intercept's own argument
 * parser feeding the query parameter the route takes.
 *
 * Two things this file pins that the slice-4 file could not: a caller nobody
 * can map to a web account gets `unlinked` from the wallet cards and never a
 * guessed wallet; and every card leaves with only the tags Telegram's HTML
 * parser renders (<b>, <i>, <code>, <br>), third-party text escaped — a
 * DEXScreener token named `<b` is a name, not markup, on both surfaces.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.WEB3_CHAINS;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const replay = require('../lib/replay');
const letter = require('../lib/letter');
const venueRouter = require('../lib/venue_router');
const meme = require('../lib/meme');
const wallet = require('../lib/wallet');
const defi = require('../lib/defi');
const dex = require('../lib/dex');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base;

function req(path, { botSecret } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${path}`, {
      method: 'GET',
      headers: { ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}) },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

function register(email) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ email, password: 'x'.repeat(12) });
    const rq = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    rq.on('error', reject);
    rq.write(body);
    rq.end();
  });
}

// Per-chain fake providers: ethereum holds 2 ETH, arbitrum's RPC is DOWN, the
// rest are empty — the shape wallet_multichain.test.js drives.
class FakeProvider {
  constructor(chainKey, { down = false, native = 0n } = {}) {
    this.chainKey = chainKey; this.down = down; this.native = native;
  }
  async getBalance() { if (this.down) throw new Error('rpc down'); return this.native; }
  async call() { if (this.down) throw new Error('rpc down'); return '0x' + '0'.repeat(64); }
  async getNetwork() { return { chainId: 0n }; }
  async resolveName(n) { return n; }
}
const PROVIDERS = {};
for (const key of ['ethereum', 'base', 'arbitrum', 'optimism', 'bnb', 'avalanche', 'polygon']) {
  PROVIDERS[key] = new FakeProvider(key, key === 'ethereum' ? { native: 2n * 10n ** 18n }
    : key === 'arbitrum' ? { down: true } : {});
}

const FUNDING = { rows: [
  { base: 'BTC', rates: { bitget: 8.2, bybit: 10.9, bingx: -2.1 }, spread_apr: 13.0 },
  { base: 'SOL', rates: { bitget: 3.0, bybit: 4.0 }, spread_apr: 1.0 },
] };
const PAIR = (symbol) => ({
  chainId: 'base', dexId: 'uniswap',
  baseToken: { symbol, name: 'Foo', address: '0x' + '11'.repeat(20) },
  quoteToken: { symbol: 'WETH' }, priceUsd: '1', url: 'https://example.org',
  liquidity: { usd: 500000 }, volume: { h24: 100000 },
  priceChange: { h24: 2 }, pairCreatedAt: Date.now() - 30 * 86400000,
  txns: { h24: { buys: 300, sells: 280 } },
});
const TAG_RE = /<\/?([a-zA-Z][a-zA-Z0-9]*)[^<>]*>/g;
const telegramTagsOnly = (html) => {
  const bad = [...String(html).matchAll(TAG_RE)].map(m => m[1].toLowerCase())
    .filter(t => !['b', 'i', 'code', 'br'].includes(t));
  return bad;
};
const cards = [];   // every reply_html the route answered, for the tag sweep

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
  // The operator's closed-trade history the replay mirrors (user 1).
  for (const [sym, dir, entry, exit, size, pnl, closedAt] of [
    ['BTC/USDT', 'LONG', 100, 110, 1000, 100, '2026-07-01T10:00:00Z'],
    ['SOL/USDT', 'SHORT', 200, 210, 1000, -50, '2026-07-02T10:00:00Z'],
  ]) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
        size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [1, sym, dir, entry, exit, size, pnl, 1, null, closedAt, closedAt]);
  }
  await pool.execute('REPLACE INTO reports_cache (id, reports_json) VALUES (1, ?)',
    [JSON.stringify({ funding: FUNDING, received_at: new Date().toISOString() })]);
  dex.setTickerFetcher(async () => ({ BTCUSDT: { price: 100000, change: 0, volume: 1e9 } }));
  dex.setMidsFetcher(async () => ({ BTC: '100050' }));
  meme.setPairFetcher(async () => [PAIR('<b'), PAIR('FOO')]);
  wallet.setProviderFactory((chain) => PROVIDERS[chain.key]);
  wallet.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 1, volume: 1e9 } }));
  defi.setProviderFactory((chain) => PROVIDERS[chain.key]);
  defi.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 1, volume: 1e9 } }));
});

test.after(() => {
  if (server) server.close();
  dex.setTickerFetcher(null); dex.setMidsFetcher(null);
  meme.setPairFetcher(null);
  wallet.setProviderFactory(null); wallet.setTickerFetcher(null);
  defi.setProviderFactory(null); defi.setTickerFetcher(null);
});

async function card(path) {
  const r = await req(path, { botSecret: SECRET });
  if (r.status === 200 && typeof r.data.reply_html === 'string') cards.push(r.data.reply_html);
  return r;
}

test('replay: the route answers the intercept\'s card for the same stake, default and junk included', async () => {
  const web = await replay.maybeHandleReplayChat(1, 'replay every signal with $500');
  let r = await card('/api/bot/sync/card/replay?stake=500');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'replay' });
  assert.match(r.data.reply_html, /\$500 on every agent trade/);
  const dflt = await replay.maybeHandleReplayChat(1, 'replay every signal');
  for (const q of ['', '?stake=abc', '?stake=-5', '?stake=0']) {
    r = await card('/api/bot/sync/card/replay' + q);
    assert.equal(r.data.reply_html, dflt.reply_html, q || 'no stake');
  }
  assert.match(dflt.reply_html, /\$1,000 on every agent trade/);
});

test('letter: the last completed week, the same card', async () => {
  const web = await letter.maybeHandleLetterChat(1, "this week's letter");
  const r = await card('/api/bot/sync/card/letter');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'letter' });
  assert.match(r.data.reply_html, /The Agent Letter/);
});

test('venue router: the top five, or one asset, the way the intercept narrows', async () => {
  const all = await venueRouter.maybeHandleVenueRouterChat(1, 'venue router');
  let r = await card('/api/bot/sync/card/venue_router');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: all.reply_html, intent: 'venue_router' });
  const btc = await venueRouter.maybeHandleVenueRouterChat(1, 'best venue for BTC');
  r = await card('/api/bot/sync/card/venue_router?base=btc');
  assert.equal(r.data.reply_html, btc.reply_html);
  assert.match(r.data.reply_html, /<b>BTC<\/b>: long on <b>bingx<\/b>/);
  assert.doesNotMatch(r.data.reply_html, /<b>SOL<\/b>/);
  // An asset the scan does not carry is said, and junk in the parameter is
  // stripped to the top five rather than echoed.
  r = await card('/api/bot/sync/card/venue_router?base=DOGE');
  assert.match(r.data.reply_html, /No cross-venue funding data for <b>DOGE<\/b>/);
  r = await card('/api/bot/sync/card/venue_router?base=%3Cb%3E');
  assert.equal(r.data.reply_html, all.reply_html);
});

test('meme radar: the feed\'s token symbol is text, not markup, on both surfaces', async () => {
  const web = await meme.maybeHandleMemeChat(1, 'meme radar');
  const r = await card('/api/bot/sync/card/meme_radar');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'meme_radar' });
  assert.match(r.data.reply_html, /<b>&lt;b<\/b> \(Base\)/);
  assert.doesNotMatch(r.data.reply_html, /<b><b/);
});

test('wallet and defi: unlinked is a fact, never a guessed wallet; linked is the caller\'s own card', async () => {
  for (const name of ['wallet', 'defi']) {
    const r = await card(`/api/bot/sync/card/${name}?telegram_id=880001`);
    assert.equal(r.status, 200, name);
    assert.deepEqual(r.data, { reply_html: null, intent: name, unlinked: true });
    const none = await card(`/api/bot/sync/card/${name}`);
    assert.deepEqual(none.data, { reply_html: null, intent: name, unlinked: true });
  }
  const reg = await register('cards5@test.io');
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['880001', reg.user_id]);
  // Linked to a web account that has no wallet: the renderer's own sentence.
  let r = await card('/api/bot/sync/card/wallet?telegram_id=880001');
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, (await wallet.maybeHandleWalletChat(reg.user_id, 'my wallet')).reply_html);
  assert.match(r.data.reply_html, /No wallet is linked to your account yet/);
  r = await card('/api/bot/sync/card/defi?telegram_id=880001');
  assert.match(r.data.reply_html, /No wallet is linked yet/);
  // With a wallet: the mirror, the chain filter, and the unreadable chain in
  // <i>, never a <span> Telegram would refuse.
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', ['0x' + 'ab'.repeat(20), reg.user_id]);
  const own = await wallet.maybeHandleWalletChat(reg.user_id, 'my wallet');
  r = await card('/api/bot/sync/card/wallet?telegram_id=880001');
  assert.deepEqual(r.data, { reply_html: own.reply_html, intent: 'wallet' });
  assert.match(r.data.reply_html, /<b>ETH<\/b> 2 — \$5,000/);
  assert.match(r.data.reply_html, /<i>Arbitrum unreadable right now \(RPC\)\.<\/i>/);
  assert.doesNotMatch(r.data.reply_html, /<span/);
  const onBase = await wallet.maybeHandleWalletChat(reg.user_id, 'my wallet on base');
  r = await card('/api/bot/sync/card/wallet?telegram_id=880001&chain=base');
  assert.equal(r.data.reply_html, onBase.reply_html);
  assert.match(r.data.reply_html, /no balances found on Base/);
  const nowhere = await wallet.maybeHandleWalletChat(reg.user_id, 'my wallet on mars');
  r = await card('/api/bot/sync/card/wallet?telegram_id=880001&chain=mars');
  assert.equal(r.data.reply_html, nowhere.reply_html);
  assert.match(r.data.reply_html, /I don't mirror <b>mars<\/b> yet/);
  const positions = await defi.maybeHandleDefiChat(reg.user_id, 'my defi positions');
  r = await card('/api/bot/sync/card/defi?telegram_id=880001');
  assert.deepEqual(r.data, { reply_html: positions.reply_html, intent: 'defi' });
});

test('the identity the website hands the bot for a web-only account maps to that account, on every per-person read', async () => {
  // lib/identity.js resolves a web-only account to `web:<uid>` — "the
  // caller by construction" — and the first draft of the mapper looked it
  // up as a Telegram id, so a web-only account was `unlinked` to its own
  // wallet card. One mapper for the router's per-person reads now.
  const reg = await register('cards5web@test.io');
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', ['0x' + 'cd'.repeat(20), reg.user_id]);
  const own = await wallet.maybeHandleWalletChat(reg.user_id, 'my wallet');
  let r = await card(`/api/bot/sync/card/wallet?telegram_id=web:${reg.user_id}`);
  assert.deepEqual(r.data, { reply_html: own.reply_html, intent: 'wallet' });
  assert.match(r.data.reply_html, /<b>ETH<\/b> 2 — \$5,000/);
  const positions = await defi.maybeHandleDefiChat(reg.user_id, 'my defi positions');
  r = await card(`/api/bot/sync/card/defi?telegram_id=web:${reg.user_id}`);
  assert.deepEqual(r.data, { reply_html: positions.reply_html, intent: 'defi' });
  // An identity that names no account, or is not one, is unlinked — never
  // somebody else's row.
  for (const id of ['web:999999', 'web:abc', 'web:', `web:${reg.user_id}x`, `web:-${reg.user_id}`]) {
    r = await card(`/api/bot/sync/card/wallet?telegram_id=${encodeURIComponent(id)}`);
    assert.deepEqual(r.data, { reply_html: null, intent: 'wallet', unlinked: true }, id);
  }
  // The router's other per-person reads ask the same mapper.
  r = await req(`/api/bot/sync/exposure?telegram_id=web:${reg.user_id}`, { botSecret: SECRET });
  assert.equal(r.status, 200);
  r = await req('/api/bot/sync/exposure?telegram_id=web:999999', { botSecret: SECRET });
  assert.equal(r.status, 404);
});

test('every card the route answered carries only the tags Telegram renders', () => {
  assert.ok(cards.length >= 12, `only ${cards.length} cards rendered`);
  for (const html of cards) {
    assert.deepEqual(telegramTagsOnly(html), [], html.slice(0, 120));
  }
});

test('the door that stays a door is not a card, and neither is the admin read', async () => {
  // `alerts` left this list when the price alert became a Telegram command:
  // the route answers it now (sync_card_route_arms_price_alerts.test.js drives
  // that), so the door that stays a door is the idle-yield read alone, and
  // `price_alert` is the intent name nothing serves as a card.
  for (const name of ['price_alert', 'idleyield', 'exposure']) {
    const r = await req(`/api/bot/sync/card/${name}`, { botSecret: SECRET });
    assert.equal(r.status, 404, name);
  }
  const r = await req('/api/bot/sync/card/alerts', { botSecret: SECRET });
  assert.equal(r.status, 200, 'alerts is a card since the price-alert slice');
});
