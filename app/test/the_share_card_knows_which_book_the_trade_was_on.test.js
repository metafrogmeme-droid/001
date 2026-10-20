'use strict';
/**
 * The share card prints a PAPER or LIVE chip only from a `book` the website
 * hands it (routes/share.js relays it; the renderer's own test holds the
 * chip to the word). The website's row carried none: the sync routes stored
 * no such column and the dashboard's share button sent no such field, so
 * every card claimed neither book, including the trades the bot knew.
 *
 * Now the bot sends 'paper' or 'live' with each row when its record says
 * (`website_sync._book_of`), the two sync routes store it, and the share
 * button forwards it. Three values on the row, never two: a row the bot
 * synced with no word is NULL, "the bot did not say", and the card claims
 * neither -- a default in either direction would print PAPER on a real-money
 * card or LIVE on a practice one.
 *
 * The routes are driven end to end on the in-memory database; the history
 * SELECT and the dashboard handler are scanned, because MemoryDB answers no
 * column-list SELECT on trades and the handler lives inside a 5,000-line
 * page module with no seam. Comments are stripped first (`code_only`), so a
 * comment that names the shape cannot stand in for the code.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base, n = 0;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

function post(route, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const r = http.request(`${base}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Content-Length': Buffer.byteLength(data),
                 'X-Bot-Secret': SECRET },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

const row = (symbol, extra = {}) => ({
  symbol, direction: 'LONG', entry_price: 100, exit_price: 105, size_usd: 100,
  pnl: 5, fees: 0, opened_at: '2026-10-17T20:12:00Z', closed_at: '2026-10-19T22:35:10Z',
  ...extra,
});
const stored = (symbol) => pool.trades.find((t) => t.user_id === 1 && t.symbol === symbol);

test('the replace-all sync stores the book the bot sent, and NULL when it sent none', async () => {
  const r = await post('/api/bot/sync', {
    equity: 1000,
    closed_trades: [
      row('PAPER/USDT', { book: 'paper' }),
      row('LIVE/USDT', { book: 'live' }),
      row('OLDBOT/USDT'),                       // a bot from before the field
      row('CASED/USDT', { book: 'Live' }),      // the word, however spelled
      row('JUNK/USDT', { book: 'margin' }),     // not a book: not a claim either
    ],
    positions: [
      { symbol: 'OPENP/USDT', direction: 'SHORT', entry_price: 10, size_usd: 50, fees: 0,
        opened_at: '2026-10-19T00:00:00Z', book: 'paper' },
      { symbol: 'OPENQ/USDT', direction: 'SHORT', entry_price: 10, size_usd: 50, fees: 0,
        opened_at: '2026-10-19T00:00:00Z' },
    ],
  });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(stored('PAPER/USDT').book, 'paper');
  assert.equal(stored('LIVE/USDT').book, 'live');
  assert.strictEqual(stored('OLDBOT/USDT').book, null, 'no word from the bot is NULL, not a default');
  assert.equal(stored('CASED/USDT').book, 'live');
  assert.strictEqual(stored('JUNK/USDT').book, null, 'an unknown word is no claim');
  assert.equal(stored('OPENP/USDT').book, 'paper', 'an open position carries it too');
  assert.strictEqual(stored('OPENQ/USDT').book, null);
});

test('the single trade-event wire stores the same claim on open and on close', async () => {
  const sym = 'EVT/USDT';
  let r = await post('/api/bot/sync/trade-event', {
    event: 'open', event_id: `ev-${++n}`, equity: 1000,
    trade: { symbol: sym, direction: 'LONG', entry_price: 100, size_usd: 100, fees: 0, book: 'live' },
  });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(stored(sym).book, 'live');
  r = await post('/api/bot/sync/trade-event', {
    event: 'close', event_id: `ev-${++n}`, equity: 1000,
    trade: row(sym, { book: 'live' }),
  });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  const closed = pool.trades.find((t) => t.symbol === sym && t.status === 'CLOSED');
  assert.ok(closed, 'the close was recorded');
  assert.equal(closed.book, 'live');

  const bare = 'EVB/USDT';
  r = await post('/api/bot/sync/trade-event', {
    event: 'open', event_id: `ev-${++n}`, equity: 1000,
    trade: { symbol: bare, direction: 'LONG', entry_price: 100, size_usd: 100, fees: 0 },
  });
  assert.equal(r.status, 200);
  assert.strictEqual(stored(bare).book, null, 'an event with no word stores NULL');
});

test('the history route hands the book to the page', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'trades.js'), 'utf8'));
  const m = /router\.get\('\/history'[\s\S]*?SELECT ([^`]*?)\s+FROM trades WHERE user_id = \? AND status = 'CLOSED'/.exec(src);
  assert.ok(m, 'the history SELECT was reshaped; re-anchor');
  const cols = m[1].split(',').map((c) => c.trim());
  assert.ok(cols.includes('book'), `history selects ${cols.join(', ')} and no book`);
  assert.ok(cols.includes('opened_at') && cols.includes('closed_at'), 'the instants still travel with it');
});

test('the dashboard share button carries the book and forwards it only when the row says', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  // The button template: the row's word, or nothing, beside the two instants.
  const button = /class="btn btn--sm share-trade"[^\n]*data-book="\$\{esc\(t\.book \|\| ''\)\}"/.exec(src);
  assert.ok(button, 'the share button does not carry data-book');
  // The handler: `book` read off the dataset and appended to the card query
  // under the same guard the instants use -- nothing invented for an empty
  // attribute, which is what an old row renders.
  const handler = /closest\('\.share-trade'\)[\s\S]*?const \{[^}]*\bbook\b[^}]*\} = btn\.dataset;[\s\S]*?\(book \? `&book=\$\{encodeURIComponent\(book\)\}` : ''\)/.exec(src);
  assert.ok(handler, 'the share handler does not forward the book under a guard');
  // Both arms of the guard: the segment is the ternary, so an empty book
  // appends nothing. Evaluate the shape the regex matched, not a copy of it.
  const seg = handler[0].slice(handler[0].lastIndexOf('(book ?'));
  const render = (book) => new Function('book', 'encodeURIComponent', `return ${seg};`)(book, encodeURIComponent);
  assert.equal(render('paper'), '&book=paper');
  assert.equal(render(''), '');
  assert.equal(render(undefined), '');
});

test('an INSERT that does not name the column reads NULL on the in-memory database too', async () => {
  // The schema's DEFAULT NULL, mirrored by the shim the way `venue` and
  // `status` are: a writer that predates the column (the website's own
  // paper-trade write-through names no book) must read "the bot did not
  // say", not `undefined`, on both backends.
  await pool.execute(
    "INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status) VALUES (?, ?, ?, ?, ?, ?, 'OPEN')",
    [1, 'NOCOL/USDT', 'LONG', 1, 10, 0]);
  const r = pool.trades.find((t) => t.symbol === 'NOCOL/USDT');
  assert.ok(r, 'the row was stored');
  assert.strictEqual(r.book, null);
});
