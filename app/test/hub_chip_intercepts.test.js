'use strict';
/**
 * Hub chip → chat intercept contract (live incident, 2026-07-20): the Hub's
 * one-tap "Meme radar" chip sent 'meme radar', which the meme intercept's
 * regex did NOT match — the ask fell through to the bot LLM, which honestly
 * told the user it has no radar access. Every radar chip's exact ask phrase
 * must be answered by its own web-side intercept, never the LLM fallback.
 * "rwa radar", "airdrop radar", "meme radar", "nft radar" and "spot market"
 * are the exceptions: both doors route each to its shared seam, which
 * fetches the same card.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

test('radar chip phrases that left are not claimed by a local matcher', async () => {
  // Chip phrases as wired in dashboard.js (pinned there by meme_panel.test.js).
  // "rwa radar", "airdrop radar", "meme radar", "nft radar" and "spot market"
  // left this contract. Both doors route each to its shared seam, which
  // fetches the card. Spot is the last radar chip to leave: the module no
  // longer matches the sentence, and the card is what both doors fetch.
  const spot = require('../lib/spot');
  assert.equal(typeof spot.maybeHandleSpotChat, 'undefined');
  assert.equal(spot.CHAT_RE, undefined);
  spot.setSpotFetcher(async () => ({ data: [
    { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.01', usdtVolume: '1e9' }] }));
  require('../lib/tickers').setTickerFetcher(async () => ({
    BTCUSDT: { price: 99900, change: 1, volume: 1e9 } }));
  try {
    const card = await spot.spotChatCard();
    assert.equal(card.intent, 'spot');
    assert.ok(card.reply_html && card.reply_html.includes('Spot market'));
  } finally {
    spot.setSpotFetcher(null);
    require('../lib/tickers').setTickerFetcher(null);
  }
});

test('the chips wired in the dashboard stay in sync with this contract', () => {
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  for (const ask of ['rwa radar', 'airdrop radar', 'meme radar', 'nft radar', 'spot market']) {
    assert.ok(dash.includes(`'${ask}'`), `hub chip "${ask}" exists`);
  }
});
