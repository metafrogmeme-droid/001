'use strict';
/**
 * The bot's /nft, /spot and /airdrops commands render the website chat's own
 * cards — the same function the web intercept answers with, fetched over the
 * bot-secret sync channel — so `GET /api/bot/sync/card/:name` must answer
 * BYTE-FOR-BYTE what the intercept answers for the same reader, and nothing
 * for a name the whitelist does not carry.
 *
 * A second formatter in Python would be a second answer about one reading
 * (the rule `web_reads.json` states for the notice's example sentence); this
 * pins that there is one. Driven against injected fetchers, never the network.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.OPENSEA_API_KEY;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const opensea = require('../lib/opensea');
const spot = require('../lib/spot');
const tickers = require('../lib/tickers');
const airdrops = require('../lib/airdrops');

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

const NFT_FETCHER = async (p) => (p.startsWith('/collections?')
  ? { collections: [{ collection: 'apes', name: 'Apes' }, { collection: 'rocks', name: 'Rocks' }] }
  : { total: { floor_price: 2.5, num_owners: 500 }, intervals: [{ interval: 'seven_day', volume: 321 }] });
const SPOT_RAW = { data: [
  { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.012', usdtVolume: '2000000000' },
  { symbol: 'ETHUSDT', lastPr: '4000', change24h: '-0.02', usdtVolume: '900000000' },
] };

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  opensea.setOpenSeaFetcher(null);
  spot.setSpotFetcher(null);
  tickers.setTickerFetcher(null);
});

test('every card needs the bot secret, and an unknown name is a 404 never a lookup', async () => {
  for (const p of ['/api/bot/sync/card/nft', '/api/bot/sync/card/spot', '/api/bot/sync/card/airdrops']) {
    assert.equal((await req(p)).status, 403, p);
    assert.equal((await req(p, { botSecret: 'wrong' })).status, 403, p);
  }
  for (const name of ['rwa', 'nope', '__proto__', 'constructor', 'hasOwnProperty', 'toString']) {
    const r = await req(`/api/bot/sync/card/${name}`, { botSecret: SECRET });
    assert.equal(r.status, 404, name);
    assert.equal(r.data.error, 'Unknown card');
  }
});

test('nft: the route answers the intercept\'s own card, byte for byte, and names the intent', async () => {
  opensea.setOpenSeaFetcher(NFT_FETCHER);
  const web = await opensea.maybeHandleNftChat(1, 'nft radar');
  const r = await req('/api/bot/sync/card/nft', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'nft' });
  assert.match(r.data.reply_html, /Apes.*floor 2\.5 ETH/);
  assert.match(r.data.reply_html, /never lists, bids, mints or trades/);
});

test('nft: an unconfigured OpenSea is the same honest card on both surfaces, not a 500', async () => {
  opensea.setOpenSeaFetcher(null);
  const web = await opensea.maybeHandleNftChat(1, 'nft radar');
  const r = await req('/api/bot/sync/card/nft', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, web.reply_html);
  assert.match(r.data.reply_html, /unavailable: the operator has not configured an OpenSea API key/);
});

test('spot: the route answers the intercept\'s own card, basis row included', async () => {
  spot.setSpotFetcher(async () => SPOT_RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1e9 } }));
  const web = await spot.maybeHandleSpotChat(1, 'spot market');
  const r = await req('/api/bot/sync/card/spot', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'spot' });
  assert.match(r.data.reply_html, /<b>BTC<\/b> \$100,000/);
  assert.match(r.data.reply_html, /Spot↔perp basis/);
});

test('airdrops: an unlinked telegram_id gets the public radar, and a linked one the user\'s own card', async () => {
  const pub = await airdrops.airdropChatCard(null);
  let r = await req('/api/bot/sync/card/airdrops?telegram_id=770001', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: pub.reply_html, intent: 'airdrops' });
  assert.match(r.data.reply_html, /Airdrop &amp; testnet radar/);
  assert.match(r.data.reply_html, /One human, one wallet/);
  assert.doesNotMatch(r.data.reply_html, /your wallet is ready/);

  // Linked, with a wallet: the per-person half — the testnet campaigns'
  // readiness hint — appears, and only for the caller the id maps to. The
  // wallet reader is injected so no RPC is touched.
  const reg = await register('card1@test.io');
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['770001', reg.user_id]);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', ['0x' + 'ab'.repeat(20), reg.user_id]);
  airdrops.setWalletReader(async () => ({ chains: [] }));
  try {
    const own = await airdrops.maybeHandleAirdropChat(reg.user_id, 'airdrop radar');
    r = await req('/api/bot/sync/card/airdrops?telegram_id=770001', { botSecret: SECRET });
    assert.equal(r.status, 200);
    assert.equal(r.data.reply_html, own.reply_html);
    assert.match(r.data.reply_html, /your wallet is ready/);
    assert.notEqual(r.data.reply_html, pub.reply_html);
    // Another id maps to nobody — the public card, never this user's hints.
    r = await req('/api/bot/sync/card/airdrops?telegram_id=770002', { botSecret: SECRET });
    assert.equal(r.data.reply_html, pub.reply_html);
  } finally {
    airdrops.setWalletReader(null);
  }

  // No telegram_id at all is a public read too — never a 400: the card is the
  // same reading for everybody, the hints are the only per-person part.
  r = await req('/api/bot/sync/card/airdrops', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, pub.reply_html);
});

test('a renderer that answers no card is a 500, never a 200 with nothing in it', async () => {
  // No renderer in the tree answers nothing today (each has its own honest
  // unavailable card), so the boundary is driven by patching the export the
  // route reads at call time — a 200 carrying no `reply_html` would reach
  // Telegram as "the channel did not answer", which is a different fact.
  const real = opensea.nftChatCard;
  try {
    opensea.nftChatCard = async () => ({});
    let r = await req('/api/bot/sync/card/nft', { botSecret: SECRET });
    assert.equal(r.status, 500);
    assert.equal(r.data.error, 'Card unavailable');
    opensea.nftChatCard = async () => undefined;
    r = await req('/api/bot/sync/card/nft', { botSecret: SECRET });
    assert.equal(r.status, 500);
  } finally {
    opensea.nftChatCard = real;
  }
});

test('the three intercepts still answer their own phrasings through the shared renderers', async () => {
  opensea.setOpenSeaFetcher(NFT_FETCHER);
  spot.setSpotFetcher(async () => SPOT_RAW);
  assert.equal(await opensea.maybeHandleNftChat(1, 'hello there'), null);
  assert.equal(await spot.maybeHandleSpotChat(1, 'hello there'), null);
  assert.equal(await airdrops.maybeHandleAirdropChat(1, 'hello there'), null);
  assert.ok((await opensea.maybeHandleNftChat(1, 'nft radar')).reply_html.includes('NFT radar'));
  assert.ok((await spot.maybeHandleSpotChat(1, 'spot market')).reply_html.includes('Spot market'));
  assert.equal((await airdrops.maybeHandleAirdropChat(1, 'airdrop radar')).intent, 'airdrops');
});
