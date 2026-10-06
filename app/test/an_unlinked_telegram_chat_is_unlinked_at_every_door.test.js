'use strict';
/**
 * /unlink closes every per-user door, and the net-worth door still serves
 * the book the BOT holds.
 *
 * The website's unlink route clears `telegram_linked` and keeps
 * `telegram_id` (it doubles as the Telegram OAuth identity) on the stated
 * contract that every consumer requires the pair, which routes/credentials.js
 * and routes/controls.js honour. `webUserFor` (routes/sync.js), a month
 * younger, mapped a Telegram id by the id alone, and six doors were stacked
 * on it: after /unlink the same chat still read the account's wallet, DeFi,
 * exposure and idle-yield dollars, still armed alerts on the account, and
 * `pendingTelegramTrips` still delivered the account's tripped alerts to
 * it -- while the bot had said "Unlinked from <email>". Driven here through
 * the real auth.js, routes/sync.js, lib/alerts.js and lib/identity.js on the
 * in-memory DB, linking with the statement auth.js runs and unlinking
 * through the route the bot calls.
 *
 * Net worth is the one card with a half the bot holds for a Telegram chat
 * regardless of any web account (its paper book and the exchange it
 * connected on Telegram), so an unmapped Telegram caller gets that half and
 * a sentence about the missing web account, not `unlinked`; an unmapped
 * `web:<uid>` is still unlinked.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.WEB3_CHAINS;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const express = require('express');

// The gateway half of the net-worth card is stubbed: the bot answers the
// paper book and a connected exchange for the Telegram id it is asked about.
const gatewayPath = require.resolve(path.join(__dirname, '..', 'lib', 'gateway'));
require.cache[gatewayPath] = {
  id: gatewayPath, filename: gatewayPath, loaded: true,
  exports: {
    isConfigured: () => true,
    getGateway: async (p) => ({
      status: 200,
      data: { read_only: true, paper: { equity_usd: 100, total_pnl: 0, simulated: true },
              cex: { connected: true, ok: true, venue: 'bitget', equity_usd: 2500.5 },
              asked: p },
    }),
    postGateway: async () => ({ status: 200, data: {} }),
  },
};
const openseaPath = require.resolve(path.join(__dirname, '..', 'lib', 'opensea'));
require.cache[openseaPath] = { id: openseaPath, filename: openseaPath, loaded: true,
  exports: { getWalletNfts: async () => ({ available: false, reason: 'off' }) } };

const authModule = require('../auth');
const { pool } = require('../db');
const alerts = require('../lib/alerts');
const wallet = require('../lib/wallet');
const { resolveBotIdentity } = require('../lib/identity');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base;

function req(method, p, { botSecret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
const card = (name, tg, extra = '') =>
  req('GET', `/api/bot/sync/card/${name}?telegram_id=${encodeURIComponent(tg)}${extra}`, { botSecret: SECRET });

function register(email) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ email, password: 'x'.repeat(12) });
    const rq = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve(JSON.parse(d)));
    });
    rq.on('error', reject);
    rq.write(body);
    rq.end();
  });
}

class FakeProvider {
  async getBalance() { return 2n * 10n ** 18n; }
  async call() { return '0x' + '0'.repeat(64); }
  async getNetwork() { return { chainId: 0n }; }
  async resolveName(n) { return n; }
}

test.before(async () => {
  alerts.setTickerFetcher(async () => ({ BTCUSDT: { price: 98_000, change: -2.5 } }));
  wallet.setProviderFactory(() => new FakeProvider());
  wallet.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 1, volume: 1e9 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());


async function linkedUser(email, chat) {
  const u = await register(email);
  const uid = Number(u.user_id || u.id || (u.user && u.user.id));
  assert.ok(uid > 0, JSON.stringify(u));
  // The statement auth.js runs when a /link token is consumed.
  await pool.execute(
    'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
    [chat, uid]);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
    ['0xabababababababababababababababababababab', uid]);
  return uid;
}

test('after /unlink the chat is unlinked at the wallet, DeFi, exposure, idle-yield and alert doors', async () => {
  const CHAT = '555001';
  const uid = await linkedUser('victim@example.com', CHAT);
  // Linked: the doors serve the account.
  for (const name of ['wallet', 'defi', 'exposure', 'idleyield']) {
    const r = await card(name, CHAT);
    assert.equal(r.status, 200, name);
    assert.notEqual(r.data.unlinked, true, `${name} serves a linked chat`);
  }
  let r = await card('alerts', CHAT, '&text=' + encodeURIComponent('tell me when BTC drops below 100k'));
  assert.equal(r.status, 200);
  assert.notEqual(r.data.unlinked, true);
  const armedBefore = (await alerts.listAlerts(uid)).length;
  // The bot's /unlink calls this route.
  const un = await req('POST', '/api/bot/sync/telegram-unlink',
    { botSecret: SECRET, body: { user_id: uid, chat_id: CHAT } });
  assert.equal(un.status, 200, JSON.stringify(un.data));
  // The website's own identity resolver reads the account as unlinked.
  const ident = await resolveBotIdentity({ user: { user_id: uid } });
  assert.equal(ident.linked, false);
  // Unlinked: every per-user door answers unlinked, and arms nothing.
  for (const name of ['wallet', 'defi', 'exposure', 'idleyield']) {
    const after = await card(name, CHAT);
    assert.equal(after.status, 200, name);
    assert.equal(after.data.unlinked, true, `${name} is closed to the disconnected chat`);
    assert.equal(after.data.reply_html, null);
  }
  r = await card('alerts', CHAT, '&text=' + encodeURIComponent('tell me when SOL rises above 200'));
  assert.equal(r.data.unlinked, true, 'the alert door is closed');
  assert.equal((await alerts.listAlerts(uid)).length, armedBefore, 'nothing armed on the account');
});

test('a tripped alert is not delivered to a chat the account has disconnected', async () => {
  const CHAT = '555002';
  const uid = await linkedUser('tripped@example.com', CHAT);
  const armed = await card('alerts', CHAT, '&text=' + encodeURIComponent('tell me when BTC drops below 100k'));
  assert.notEqual(armed.data.unlinked, true);
  // Trip it (BTC is at 98k) while still linked: the chat is on the queue.
  await alerts.runOnce(async () => {});
  const linkedTrips = await alerts.pendingTelegramTrips();
  assert.ok(linkedTrips.some((t) => String(t.telegram_id) === CHAT), 'a linked chat is on the queue');
  // Disconnect: the undelivered trip no longer names the chat.
  await req('POST', '/api/bot/sync/telegram-unlink', { botSecret: SECRET, body: { user_id: uid, chat_id: CHAT } });
  const after = await alerts.pendingTelegramTrips();
  assert.ok(!after.some((t) => String(t.telegram_id) === CHAT), 'a disconnected chat gets no trip');
  const pending = await req('GET', '/api/bot/sync/alerts/pending', { botSecret: SECRET });
  assert.ok(!(pending.data.trips || []).some((t) => String(t.telegram_id) === CHAT));
});

test('net worth still serves the book the bot holds for an unmapped Telegram chat, and says the web half is missing', async () => {
  // A Telegram user the website has never seen.
  const r = await card('networth', '777001');
  assert.equal(r.status, 200);
  assert.notEqual(r.data.unlinked, true, 'a Telegram chat is not unlinked from its own book');
  const html = String(r.data.reply_html);
  assert.match(html, /BITGET/);
  assert.match(html, /\$2,500\.5/);
  assert.match(html, /no web account is linked to this Telegram/);
  assert.match(html, /Paper portfolio/);
  // After /unlink the same holds for the disconnected chat.
  const CHAT = '555003';
  const uid = await linkedUser('networth@example.com', CHAT);
  await req('POST', '/api/bot/sync/telegram-unlink', { botSecret: SECRET, body: { user_id: uid, chat_id: CHAT } });
  const after = await card('networth', CHAT);
  assert.notEqual(after.data.unlinked, true);
  assert.match(String(after.data.reply_html), /no web account is linked to this Telegram/);
  assert.doesNotMatch(String(after.data.reply_html), /0xabab/, 'the account\'s wallet is not read for the disconnected chat');
  // The other arm: a web id the website cannot map is unlinked.
  const web = await card('networth', 'web:999999');
  assert.equal(web.data.unlinked, true);
});
