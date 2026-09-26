'use strict';
/**
 * /api/today called every season "upcoming", and named whichever row the
 * database returned first.
 *
 *     const [srows] = await pool.execute('SELECT ... FROM arena_seasons');
 *     const s = srows[0];
 *     parts.season = { name: s.name, status: seasons.seasonStatus(s), ... };
 *
 * `seasonStatus(s)` with no clock computes `Number(undefined)`, which is NaN,
 * and `NaN >= start` is false -- so a season running from three days ago to
 * ten days ahead came back "upcoming" (driven 2026-09-26). And `srows[0]` of an
 * unordered SELECT is the `LIMIT 1` defect `pickCurrentSeason` records one
 * route over: MySQL may return either row once a second season exists.
 *
 * The digest asks the Arena's own picker now, at a clock, and `seasonStatus`
 * RAISES on a missing clock rather than answering "upcoming".
 *
 * THE SHIM HIDES HALF OF THIS. The in-memory database sorts seasons
 * newest-by-id first, so a fixture that authors the live season LAST passes
 * the old `srows[0]` read. Every case here authors the live season FIRST, and
 * one case hands the rows over in both orders, as MySQL may.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const { pool } = require('../db');
const seasons = require('../lib/arena_seasons');
const { fetchToday } = require('../lib/daily_rune');

const DAY = 864e5;
let nextId = 1000;
function author(name, startOffsetDays, endOffsetDays) {
  const now = Date.now();
  const row = { id: nextId++, name, rules: null, created_at: new Date(),
    starts_at: new Date(now + startOffsetDays * DAY), ends_at: new Date(now + endOffsetDays * DAY) };
  pool.arenaSeasons.push(row);
  return row;
}

test.beforeEach(() => { pool.arenaSeasons.length = 0; });

test('a running season is live on the digest, not upcoming', async () => {
  author('September Cup', -3, 10);
  const d = await fetchToday();
  assert.equal(d.season.name, 'September Cup');
  assert.equal(d.season.status, 'live',
    'seasonStatus was called with no clock and compared against NaN');
});

test('the live season is named even when a later-authored one comes back first', async () => {
  // Authored live-first, so the shim's newest-first order puts the upcoming
  // row at [0] -- the row the old read took.
  author('September Cup', -3, 10);
  author('October Open', 12, 40);
  const [raw] = await pool.execute('SELECT id, name, starts_at, ends_at FROM arena_seasons');
  assert.equal(raw[0].name, 'October Open', 'fixture: the upcoming row must come back first');
  const d = await fetchToday();
  assert.equal(d.season.name, 'September Cup',
    'the digest named whichever row the database returned first');
  assert.equal(d.season.status, 'live');
});

test('row order does not change the answer', async () => {
  const live = author('September Cup', -3, 10);
  const next = author('October Open', 12, 40);
  const real = pool.execute.bind(pool);
  for (const order of [[live, next], [next, live]]) {
    pool.execute = async (sql, params) => (/FROM arena_seasons/i.test(sql)
      ? [order.map((r) => ({ ...r })), []] : real(sql, params));
    try {
      const d = await fetchToday();
      assert.equal(d.season.name, 'September Cup', order.map((r) => r.name).join(' then '));
      assert.equal(d.season.status, 'live');
    } finally { pool.execute = real; }
  }
});

test('an ended season with no successor running reads ended', async () => {
  author('August Sprint', -60, -30);
  const d = await fetchToday();
  assert.equal(d.season.name, 'August Sprint');
  assert.equal(d.season.status, 'ended');
});

test('no season authored is no season on the digest, not an invented one', async () => {
  const d = await fetchToday();
  assert.equal(d.season, undefined);
});

test('seasonStatus refuses a missing clock rather than answering upcoming', () => {
  const s = { starts_at: new Date(Date.now() - 3 * DAY), ends_at: new Date(Date.now() + 10 * DAY) };
  assert.equal(seasons.seasonStatus(s, new Date()), 'live');
  assert.equal(seasons.seasonStatus(s, Date.now()), 'live', 'a millisecond clock is a clock');
  for (const bad of [undefined, null, NaN, 'not a time', new Date('nope')]) {
    assert.throws(() => seasons.seasonStatus(s, bad), TypeError, String(bad));
  }
});
