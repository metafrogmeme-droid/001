'use strict';
/**
 * The exchange line's fourth word. `cex` from the bot's `networth_reading`
 * has four words: no venue (connected false, no error), unreadable or timed
 * out (connected true, ok false, with its reason), read, and COULD NOT BE
 * ASKED (connected false with `error: cex_unavailable`, the credential store
 * raised). PR 494 made `networthChatCard` the one renderer for both surfaces
 * and gave it no branch for the fourth: anything not connected printed
 * "Exchange: none connected -- /connect in Telegram links one", which sends
 * a person whose keys are sitting in the store off to enter them again. The
 * engineering log recorded that exact sentence as fixed, under a renderer
 * that no longer existed. The gateway not answering, and the website having
 * no bot link configured, took the same wrong sentence.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const lib = path.join(__dirname, '..', 'lib');
function stub(abs, exports) { require.cache[abs] = { id: abs, filename: abs, loaded: true, exports }; }
let gw;
let configured = true;
stub(require.resolve(path.join(lib, 'gateway')), {
  isConfigured: () => configured,
  getGateway: async () => { if (gw instanceof Error) throw gw; return { status: 200, data: gw }; },
});
stub(require.resolve(path.join(lib, 'wallet')), {
  walletAddressOf: async () => null,
  getWalletPortfolio: async () => null,
});
stub(require.resolve(path.join(lib, 'opensea')), { getWalletNfts: async () => ({ available: false, reason: 'x' }) });
stub(require.resolve(path.join(__dirname, '..', 'db')), { pool: { execute: async () => [[]] } });
const { networthChatCard, exchangeUnreadLine } = require(path.join(lib, 'networth.js'));

const PAPER = { equity_usd: 100, total_pnl: 0, simulated: true };
const line = (html) => String(html).split('<br>').find((l) => l.startsWith('• Exchange') || l.includes('exchange'));

test('a credential store that raised is said to have not answered, not to hold no link', async () => {
  gw = { read_only: true, paper: PAPER, cex: { connected: false, error: 'cex_unavailable' } };
  const c = await networthChatCard('7', 42);
  const l = line(c.reply_html);
  assert.match(l, /could not be read just now/);
  assert.match(l, /credential store did not answer/);
  assert.match(l, /not a missing link/);
  assert.doesNotMatch(l, /none connected/);
});

test('the bot not answering, and no bot link configured, are each named', async () => {
  gw = new Error('ECONNREFUSED');
  let c = await networthChatCard('7', 42);
  let l = line(c.reply_html);
  assert.match(l, /the bot did not answer/);
  assert.doesNotMatch(l, /none connected/);
  configured = false;
  c = await networthChatCard('7', 42);
  l = line(c.reply_html);
  assert.match(l, /no bot link configured/);
  assert.doesNotMatch(l, /none connected/);
  configured = true;
});

test('the other arm: the bot answering that no venue is linked still reads none connected', async () => {
  gw = { read_only: true, paper: PAPER, cex: { connected: false } };
  const c = await networthChatCard('7', 42);
  assert.match(line(c.reply_html), /none connected — \/connect/);
  assert.equal(exchangeUnreadLine({ connected: false }), exchangeUnreadLine(null));
  assert.match(exchangeUnreadLine({ connected: false, error: 'something_else' }), /could not be read just now \(something_else\)/);
});
