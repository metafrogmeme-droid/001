'use strict';
/**
 * The weekly-letter push goes to every subscriber, so it carries the public
 * headline: percent and count, never the operator's account in dollars.
 *
 * `sweepLetters` announced a new week with the STORED letter's headline,
 * which is the operator's private letter ("$1,234.56 net — 67% winners"),
 * and `notifySubscribers(payload, null)` sends it to every push subscription
 * on the site. The public letter (`composePublicLetter`) already said the
 * same week in percent; the push now says that one. Driven on the in-memory
 * database with a real winning week, because an empty week reads "A flat
 * week, by choice" in both letters and could not tell them apart.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.BOT_USER_ID;

const test = require('node:test');
const assert = require('node:assert/strict');
const { pool } = require('../db');
const letter = require('../lib/letter');

async function closedTrade(pnl, at) {
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, pnl, fees, size_usd,
                         status, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?)`,
    [1, 'BTC/USDT', 'LONG', 60000, 61000, pnl, 1.5, 1000, at, at]);
}

test('the push names the week in percent, never the operator\'s dollars', async () => {
  const week = letter.lastCompletedWeek();
  const mid = new Date(week.start.getTime() + 2 * 86400e3);
  await closedTrade(812.34, mid);
  await closedTrade(-120.5, new Date(mid.getTime() + 3600e3));
  await closedTrade(410.11, new Date(mid.getTime() + 7200e3));

  // The fixture can produce the state it names: the private letter for this
  // week carries a dollar figure, and the public one does not.
  const priv = letter.composeLetter(week, await letter.loadWeekData(week.start, week.end));
  assert.match(priv.headline, /\$/, 'the fixture did not produce a dollar headline to leak');
  const pub = await letter.getPublicLetter(week.key);
  assert.doesNotMatch(pub.headline, /\$/);

  const pushes = [];
  const created = await letter.sweepLetters(async (payload, ids) => { pushes.push({ payload, ids }); });
  assert.equal(created, true);
  assert.equal(pushes.length, 1);
  assert.equal(pushes[0].ids, null, 'it goes to every subscriber');
  const body = pushes[0].payload.body;
  assert.doesNotMatch(body, /\$|1,?222|812/, 'the operator\'s dollars went to every subscriber');
  assert.equal(body, `${week.key}: ${pub.headline}`);
  assert.match(body, /% winners/);
});
