'use strict';
/**
 * The Pi coach is handed live prices for what the question is about.
 *
 * Asked "why did the agent call LTC SHORT, and what would prove it wrong?",
 * the coach could only say it had no live data. The website now reads the
 * coins the question is about (the card's, ones it names, open positions',
 * today's rounds') from the same ticker the Arena prices trades with
 * (lib/tickers.js), and hands the coach a line that says when it was read.
 * A read that failed is said, never a zero; a coin the venue does not list
 * is said; nothing is invented to fill the line.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const { pool } = require('../db');
const coach = require('../lib/pi_coach');

const NOW = Date.parse('2026-10-22T17:30:00Z');
const MAP = {
  LTCUSDT: { price: 88.42, change: -1.3, volume: 1 },
  BTCUSDT: { price: 65210.5, change: null, volume: 1 },
  ETHUSDT: { price: 2410.2, change: 0.5, volume: 1 },
};
const reader = (read) => ({ getTickersWithin: async () => read });
const deps = (read, over = {}) => ({
  arena: { loadPositions: async () => [] }, duel: { cardFor: async () => ({ day: '2026-10-22', rounds: [] }) },
  tickers: reader(read), now: () => NOW, ...over });
const liveLine = (facts) => facts.slice(facts.indexOf(coach.LIVE_PRICES_HEAD));

test('the card\'s coin is priced first, with its 24h change and the time it was read', async () => {
  pool.signals.push({ signal_key: 'k-live-ltc', symbol: 'LTC/USDT:USDT', direction: 'SHORT', confidence: 0.6, regime: 'NEUTRAL',
    entry_price: 88.1, stop_loss: 90.4, take_profit: 83.5, status: 'NEW', created_at: '2026-10-22T16:00:00Z' });
  // Asked as the Why? button asks: the question names no coin, so only the
  // card can say which one this is about.
  const facts = await coach.facts(1, { kind: 'signal', key: 'k-live-ltc' }, deps({ map: MAP, ageMs: 0 }),
    'Waarom callde de agent hier deze call?');
  const live = liveLine(facts);
  assert.ok(live.startsWith(coach.LIVE_PRICES_HEAD + '2026-10-22 17:30 UTC:'), live);
  const lines = live.split('\n').slice(1);
  assert.deepStrictEqual(lines, ['- LTCUSDT 88.42, 24h -1.30%']);
});

test('coins the question names, open positions and today\'s rounds are priced too, at most six', async () => {
  const card = { day: '2026-10-22', rounds: [{ id: 1, kind: 'mover', symbol: 'SOLUSDT', symbol_b: 'XRPUSDT', callable: true },
    { id: 2, kind: 'sports', match: { home: 'A', away: 'B' }, callable: true }] };
  const map = { ...MAP, SOLUSDT: { price: 140, change: 2 }, XRPUSDT: { price: 0.5, change: 1 }, DOGEUSDT: { price: 0.2, change: 0 } };
  const facts = await coach.facts(1, null, deps({ map, ageMs: 0 }, {
    arena: { loadPositions: async () => [{ symbol: 'ETHUSDT', direction: 'long', entry: 2400, margin: 10, opened_at: NOW }] },
    duel: { cardFor: async () => card } }), 'how are btc and doge doing?');
  const syms = liveLine(facts).split('\n').slice(1).map((l) => l.split(' ')[1]);
  assert.deepStrictEqual(syms, ['BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']);
  assert.match(liveLine(facts), /BTCUSDT 65210\.5, 24h change not read/, 'an unread change is said, not printed as 0%');
  assert.match(liveLine(facts), /DOGEUSDT 0\.2, 24h \+0\.00%/, 'a measured flat change is still printed');
  const many = await coach.facts(1, null, deps({ map: Object.fromEntries('ABCDEFGHI'.split('').map((c) => [`${c}${c}USDT`, { price: 1, change: 1 }])), ageMs: 0 }),
    'AA BB CC DD EE FF GG HH II');
  assert.strictEqual(liveLine(many).split('\n').length - 1, coach.LIVE_MAX);
});

test('a read that failed is said, and never written under the prices head', async () => {
  for (const read of [{ map: {}, ageMs: Infinity }, null]) {
    const facts = await coach.facts(1, null, deps(read), 'price of BTCUSDT?');
    assert.match(facts, /Live prices: could not be read just now\./);
    assert.ok(!facts.includes(coach.LIVE_PRICES_HEAD), 'a failed read carried the head the gate opens on');
  }
  const thrown = await coach.facts(1, null, deps(null, { tickers: { getTickersWithin: async () => { throw new Error('x'); } } }), 'BTCUSDT');
  assert.match(thrown, /Live prices: could not be read just now\./);
});

test('a stale read says how old it is, and a coin the venue does not list is said', async () => {
  const facts = await coach.facts(1, null, deps({ map: MAP, ageMs: 5 * 60000 }), 'what about LTCUSDT and FOOUSDT?');
  assert.match(liveLine(facts), new RegExp(`^${coach.LIVE_PRICES_HEAD.replace(/[()]/g, '\\$&')}2026-10-22 17:25 UTC \\(5 min old\\):`));
  // FOOUSDT is not a coin the map knows, so the question names only LTC.
  assert.deepStrictEqual(liveLine(facts).split('\n').slice(1), ['- LTCUSDT 88.42, 24h -1.30%']);
  const unlisted = await coach.livePrices(['FOOUSDT'], '', { tickers: reader({ map: MAP, ageMs: 0 }), now: () => NOW });
  assert.match(unlisted, /- FOOUSDT: no price read/);
});

test('a question about nothing priced adds no line', async () => {
  const facts = await coach.facts(1, null, deps({ map: MAP, ageMs: 0 }), 'what is a stop-loss?');
  assert.ok(!facts.includes('Live prices'), facts);
});

test('a stored symbol is the ticker map\'s spelling', () => {
  assert.strictEqual(coach.tickerSymbol('BTC/USDT:USDT'), 'BTCUSDT');
  assert.strictEqual(coach.tickerSymbol('ltcusdt'), 'LTCUSDT');
  assert.deepStrictEqual(coach.namedSymbols('ltc and Eth, not USDT itself', MAP), ['LTCUSDT', 'ETHUSDT']);
});

test('the head the bot opens its gate on is the one the website writes', () => {
  const py = fs.readFileSync(path.join(__dirname, '..', '..', 'bot', 'web', 'pi_coach.py'), 'utf8');
  const m = py.match(/^LIVE_PRICES_HEAD = "([^"]+)"/m);
  assert.ok(m, 'bot/web/pi_coach.py has no LIVE_PRICES_HEAD');
  assert.strictEqual(m[1], coach.LIVE_PRICES_HEAD);
});

test('the reader is the Arena\'s own ticker, not a second one', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'pi_coach.js'), 'utf8');
  assert.match(src, /require\('\.\/tickers'\)/);
  assert.doesNotMatch(src, /https?:\/\//, 'the coach\'s facts reach a venue of their own');
});
