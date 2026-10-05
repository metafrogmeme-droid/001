'use strict';
/**
 * Unified net worth (PR X, web side): gateway CEX + SIWE wallet combine,
 * the honesty rule (paper equity listed but NEVER counted into the real
 * total), fail-soft sections, and the card both doors fetch. Website chat
 * no longer answers the sentence itself.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB3_CHAINS = 'ethereum';   // single-chain FakeProvider — see multichain test
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(40);   // set BEFORE requiring routes
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
// lib/gateway captures BOT_GATEWAY_URL at require time — pin the fake
// upstream's port BEFORE any route module loads.
const GW_PORT = 39877;
process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${GW_PORT}`;
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

// Fake bot gateway (the /gateway/networth upstream).
let gwServer;
let gwResponse = {
  read_only: true,
  paper: { equity_usd: 10250.5, total_pnl: 250.5, simulated: true },
  cex: { connected: true, ok: true, venue: 'bybit', currency: 'USDT',
         equity_usd: 512.34, detail: '512.34 USDT total' },
};
let gwStatus = 200;

test.before(async () => {
  gwServer = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.statusCode = gwStatus;
    res.end(JSON.stringify(gwResponse));
  });
  await new Promise((r) => gwServer.listen(GW_PORT, '127.0.0.1', r));
});

const authModule = require('../auth');
const { pool } = require('../db');
const wallet = require('../lib/wallet');

// Fake chain: 1 ETH; priced at $2,500.
const USDC_ADDR = wallet.TOKENS.find(t => t.symbol === 'USDC').address;
class FakeProvider {
  async getBalance() { return 10n ** 18n; }
  async call(tx) {
    const raw = String(tx.to).toLowerCase() === USDC_ADDR.toLowerCase()
      ? 500n * 10n ** 6n : 0n;                       // 500 USDC
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  async getNetwork() { return { chainId: 1n }; }
  async resolveName(n) { return n; }
}

let server, base;

test.before(async () => {
  wallet.setProviderFactory(() => new FakeProvider());
  wallet.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 0, volume: 1 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/networth', require('../routes/networth'));
  app.use('/api/chat', require('../routes/chat'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); if (gwServer) gwServer.close(); });

function req(method, path, { token, body, botSecret } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `nw${seq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

const ADDR = '0x' + 'cd'.repeat(20);
async function linkWallet(token) {
  const me = await req('GET', '/api/auth/me', { token });
  const uid = me.data.id ?? me.data.user_id;
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, uid]);
}

test('networth: real total = CEX + wallet; paper listed but NEVER counted', async () => {
  const token = await newUser();
  await linkWallet(token);
  const r = await req('GET', '/api/networth', { token });
  assert.equal(r.status, 200);
  const d = r.data;
  assert.equal(d.read_only, true);
  assert.equal(d.sections.cex.equity_usd, 512.34);
  // Wallet: 1 ETH @2500 + 500 USDC = 3000.
  assert.equal(d.sections.wallet.total_usd, 3000);
  assert.equal(d.sections.paper.equity_usd, 10250.5);
  // The honesty rule: 512.34 + 3000, NOT +10250.5.
  assert.equal(d.total_real_usd, 3512.34);
  assert.equal(d.sources_counted, 2);
  assert.match(d.note, /never included/i);
});

test('networth: unlinked wallet + gateway down still answers per-section', async () => {
  const token = await newUser();                     // no wallet link
  gwStatus = 503;
  const r = await req('GET', '/api/networth', { token });
  gwStatus = 200;
  assert.equal(r.status, 200);
  assert.equal(r.data.sections.wallet.linked, false);
  assert.equal(r.data.sections.cex.available, false);
  assert.equal(r.data.total_real_usd, null);         // nothing real readable
  const anon = await req('GET', '/api/networth');
  assert.equal(anon.status, 401);
});

test('the card both doors fetch is the read; chat no longer answers the sentence', async () => {
  const networth = require('../lib/networth');
  const token = await newUser();
  await linkWallet(token);
  const me = await req('GET', '/api/auth/me', { token });
  const uid = me.data.id ?? me.data.user_id;
  const card = await networth.networthChatCard(`web:${uid}`, uid);
  assert.equal(card.intent, 'networth');
  assert.match(card.reply_html, /Net worth — everywhere/);
  assert.match(card.reply_html, /BYBIT/);
  assert.match(card.reply_html, /\$512\.34/);
  assert.match(card.reply_html, /\$3,000/);
  assert.match(card.reply_html, /\$3,512\.34/);
  assert.match(card.reply_html, /simulated, not counted/i);
  assert.match(card.reply_html, /never move them/);
  assert.equal(typeof networth.maybeHandleNetWorthChat, 'undefined');
  assert.equal(networth.CHAT_RE, undefined);
  // An unread equity is not a measured zero.
  assert.doesNotMatch(card.reply_html, /\$0\.00/);

  const via = await req('GET', `/api/bot/sync/card/networth?telegram_id=web:${uid}`,
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.equal(via.status, 200);
  assert.equal(via.data.reply_html, card.reply_html);
  assert.equal(via.data.intent, 'networth');

  // A web id the website cannot map is unlinked. A Telegram id it cannot
  // map is a chat with no web account, whose exchange and paper book the
  // BOT still holds: that half is rendered, and the wallet line says why
  // there is no wallet half.
  const unlinked = await req('GET', '/api/bot/sync/card/networth?telegram_id=web:999999',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.equal(unlinked.status, 200);
  assert.equal(unlinked.data.unlinked, true);
  assert.equal(unlinked.data.reply_html, null);
  assert.equal(unlinked.data.intent, 'networth');
  const unmapped = await req('GET', '/api/bot/sync/card/networth?telegram_id=999999',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.equal(unmapped.status, 200);
  assert.notEqual(unmapped.data.unlinked, true);
  assert.match(unmapped.data.reply_html, /no web account is linked to this Telegram/);
  assert.doesNotMatch(unmapped.data.reply_html, /\$3,000/);

  // Website chat no longer matches the sentence. The turn is proxied.
  const missed = await req('POST', '/api/chat', { token, body: { text: "what's my net worth?" } });
  assert.notEqual(missed.data && missed.data.intent, 'networth');
  assert.ok(!/Net worth — everywhere/.test((missed.data && missed.data.reply_html) || ''));

  // A connected venue whose equity could not be read is said so, never $0.
  const saved = gwResponse;
  let unread;
  try {
    gwResponse = {
      read_only: true, paper: null,
      cex: { connected: true, ok: false, venue: 'bybit', equity_usd: null,
             detail: 'credentials unreadable' },
    };
    unread = await networth.networthChatCard(`web:${uid}`, uid);
  } finally {
    gwResponse = saved;
  }
  assert.match(unread.reply_html, /unreadable right now \(credentials unreadable\)/);
  assert.doesNotMatch(unread.reply_html, /\$0\.00/);
  assert.doesNotMatch(unread.reply_html, /\$NaN/);
  // The wallet was still read. The exchange was not, so the total says so.
  assert.match(unread.reply_html, /incomplete|could not be read/i);
});
