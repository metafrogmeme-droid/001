'use strict';
/**
 * The phrasing the bot tells a caller to type on the website is one the
 * website's intercept really claims.
 *
 * bot/nlp/web_reads.json is the one table: the bot's notice for a read only
 * the website answers ("ask it there in the same words: …") quotes each
 * row's `example`, and a phrasing that drifted out of an intercept's regex
 * would have failed in a user's chat, one turn after the notice invited it.
 * Driven against each library's own CHAT_RE (the alerts parser for the one
 * intercept that has no regex) rather than against a copy of it here.
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
  if (row.lib === 'alerts') {
    const { parseAlertCommand } = require('../lib/alerts');
    const parsed = parseAlertCommand(text);
    return Boolean(parsed && parsed.kind && parsed.kind !== 'error');
  }
  const lib = require(`../lib/${row.lib}`);
  assert.ok(lib.CHAT_RE instanceof RegExp, `${row.lib} exports no CHAT_RE`);
  return lib.CHAT_RE.test(text);
}

test('the table names nine reads, each on a row the intercept table has', () => {
  assert.equal(Object.keys(TABLE).length, 9);
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
