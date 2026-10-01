'use strict';
/**
 * Meme & AI-token radar (public/../lib/meme.js) — pure core: normalization,
 * the safety risk-read, dedupe, ranking, and aggregates. Network fetch is not
 * exercised (injectable + best-effort). Website chat does not render the
 * radar itself: those words wait for the shared meme_radar door.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const meme = require('../lib/meme');

const HOUR = 3_600_000;
const NOW = 1_700_000_000_000;

function pair(over) {
  return Object.assign({
    chainId: 'solana', dexId: 'raydium', url: 'https://dexscreener.com/x',
    baseToken: { address: 'Mint' + Math.random(), name: 'Doge Killer', symbol: 'DOGEK' },
    quoteToken: { symbol: 'SOL' },
    priceUsd: '0.0004', priceChange: { h24: 42 }, volume: { h24: 250000 },
    liquidity: { usd: 120000 }, fdv: 900000,
    txns: { h24: { buys: 300, sells: 210 } },
    pairCreatedAt: NOW - 10 * 24 * HOUR,
  }, over || {});
}

test('normalizePair maps DEXScreener fields; drops price-less pairs', () => {
  const t = meme.normalizePair(pair());
  assert.equal(t.symbol, 'DOGEK');
  assert.equal(t.chain_label, 'Solana');
  assert.equal(t.price_usd, 0.0004);
  assert.equal(t.volume_24h_usd, 250000);
  assert.equal(t.liquidity_usd, 120000);
  assert.equal(meme.normalizePair({ baseToken: { symbol: 'X' } }), null); // no price
});

test('riskRead escalates to extreme on the danger signals', () => {
  assert.equal(meme.riskRead(120000, 200, 300, 210).tier, 'high');       // seasoned, liquid
  assert.equal(meme.riskRead(5000, 200, 10, 10).tier, 'extreme');        // very-low liq
  assert.equal(meme.riskRead(120000, 5, 30, 5).tier, 'extreme');         // <24h old
  const noSells = meme.riskRead(120000, 200, 40, 0);
  assert.equal(noSells.tier, 'extreme');
  assert.ok(noSells.flags.includes('no-sells-yet'));                     // honeypot-ish
});

test('buildRadar dedupes by chain+token, ranks by volume, computes age', () => {
  const dup = pair({ baseToken: { address: 'SAME', name: 'A', symbol: 'AAA' }, volume: { h24: 100 } });
  const dup2 = pair({ baseToken: { address: 'SAME', name: 'A', symbol: 'AAA' }, volume: { h24: 999 } });
  const big = pair({ baseToken: { address: 'BIG', name: 'B', symbol: 'BBB' }, volume: { h24: 5_000_000 } });
  const r = meme.buildRadar([dup, dup2, big], NOW);
  assert.equal(r.summary.tokens, 2);                 // SAME collapsed to one
  assert.equal(r.tokens[0].symbol, 'BBB');           // ranked by volume desc
  assert.equal(r.read_only, true);
  assert.equal(r.tokens[1].age_hours, 240);          // 10 days
});

test('buildRadar counts extreme-risk tokens and groups by chain', () => {
  const safe = pair({ baseToken: { address: 'S', name: 's', symbol: 'SAFE' } });
  const danger = pair({ chainId: 'base', baseToken: { address: 'D', name: 'd', symbol: 'RUG' },
                        liquidity: { usd: 2000 }, pairCreatedAt: NOW - 2 * HOUR });
  const r = meme.buildRadar([safe, danger], NOW);
  assert.equal(r.summary.extreme_risk, 1);
  const chains = r.chains.map(c => c.chain).sort();
  assert.deepEqual(chains, ['base', 'solana']);
  assert.match(r.disclaimer, /high risk/i);
});

test('buildRadar tolerates junk input', () => {
  const r = meme.buildRadar([null, {}, { priceUsd: 'x' }, 42], NOW);
  assert.equal(r.summary.tokens, 0);
  assert.deepEqual(r.tokens, []);
});

// ── The card, and the sentence the website no longer answers itself ────────

let server, base;

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

test.before(async () => {
  meme.setPairFetcher(async () => [pair({
    baseToken: { address: 'MINT', name: 'Foo', symbol: 'FOO' },
  })]);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/market', require('../routes/market'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  meme.setPairFetcher(null);
});

test('the card names the radar, and this module no longer matches the sentence', async () => {
  const card = await meme.memeChatCard();
  assert.equal(card.intent, 'meme_radar');
  assert.match(card.reply_html, /Meme &amp; AI-token radar/);
  assert.match(card.reply_html, /never launches tokens/);
  assert.equal(typeof meme.maybeHandleMemeChat, 'undefined');
  assert.equal(meme.CHAT_RE, undefined);
});

test('chat: "meme radar" waits for the bot; the market route still serves', async () => {
  const reg = await req('POST', '/api/auth/register', {
    body: { email: 'meme-chat@test.io', password: 'x'.repeat(12) },
  });
  const token = reg.data.token;
  for (const text of ['meme radar', 'dexscreener', 'degen', 'pump.fun', 'ai agent tokens']) {
    const waiting = await req('POST', '/api/chat', { token, body: { text } });
    assert.equal(waiting.status, 503, text);
    assert.equal(waiting.data.intent, undefined, text);
  }
  const pub = await req('GET', '/api/market/meme');
  assert.equal(pub.status, 200);
  assert.equal(pub.data.read_only, true);
  assert.equal(pub.data.summary.tokens, 1);
  assert.equal(pub.data.tokens[0].symbol, 'FOO');
  const other = await req('POST', '/api/chat', { token, body: { text: 'how is bitcoin?' } });
  assert.equal(other.status, 503);
});
