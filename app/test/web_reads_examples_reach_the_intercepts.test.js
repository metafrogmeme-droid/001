'use strict';
/**
 * The phrasing the bot tells a caller to type on the website is one the
 * website's intercept really claims.
 *
 * bot/nlp/web_reads.json is the one table: the bot's notice for a read only
 * the website answers ("ask it there in the same words: …") quotes each
 * row's `example`, and a phrasing that drifted out of an intercept's regex
 * would have failed in a user's chat, one turn after the notice invited it.
 * Driven against each library's own CHAT_RE rather than against a copy of it
 * here. (The alerts row, driven through its parser while it was a door, is a
 * Telegram command now — app/test/sync_card_route_arms_price_alerts.test.js.)
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const TABLE = JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', 'bot', 'nlp', 'web_reads.json'), 'utf8'));
const CHAT_SRC = fs.readFileSync(path.join(__dirname, '..', 'routes', 'chat.js'), 'utf8');
const ROWS = [...CHAT_SRC.matchAll(/^\s*\['([a-z]+)',/gm)].map((m) => m[1]);

function claims(row, text) {
  const lib = require(`../lib/${row.lib}`);
  assert.ok(lib.CHAT_RE instanceof RegExp, `${row.lib} exports no CHAT_RE`);
  return lib.CHAT_RE.test(text);
}

test('the table names one read, on a row the intercept table has', () => {
  // Nine until the website's cards became Telegram commands
  // (bot/skills/market_commands.py, portfolio_commands.py); eight route to a
  // command now, and the price alert — a WRITE the website's alert engine
  // holds — is /price_alert since the bot polls its trips. A row here would
  // be a door notice over a read that exists. The idle-yield read is the
  // website's optimiser over the wallet the caller signed in with, where the
  // bot's /idleyield is the operator's account.
  assert.equal(Object.keys(TABLE).length, 1);
  for (const gone of ['nft', 'spot', 'airdrops', 'replay', 'letter', 'defi', 'venue_router', 'meme_radar', 'price_alert']) {
    assert.equal(gone in TABLE, false, gone);
  }
  for (const [intent, row] of Object.entries(TABLE)) {
    assert.ok(ROWS.includes(row.row), `${intent}: no intercept row named ${row.row}`);
    assert.ok(fs.existsSync(path.join(__dirname, '..', 'lib', `${row.lib}.js`)), `${intent}: no lib ${row.lib}`);
  }
});

for (const [intent, row] of Object.entries(TABLE)) {
  test(`${intent}: "${row.example}" is a phrasing the ${row.row} intercept claims`, () => {
    assert.ok(claims(row, row.example), `${row.lib} does not claim "${row.example}"`);
  });
}

test('a phrasing no intercept claims is claimed by none of them', () => {
  for (const row of Object.values(TABLE)) {
    assert.equal(claims(row, 'hello there, how are you'), false, row.lib);
  }
});
