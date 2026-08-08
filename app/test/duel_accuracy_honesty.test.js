'use strict';
/**
 * Daily Duel — "unreadable is never zero, and absent is never a measurement",
 * applied to a player's record.
 *
 * This is the file that exists because of CLAUDE.md's list of shapes. A duel
 * record has every one of the tempting ones available to it: a win rate over a
 * set that includes rows we could not price, a `losses = total - wins`
 * subtraction that swallows pushes, a `0%` standing in for "no calls yet", and
 * a marks total summed over rounds whose outcome is still unknown.
 *
 * Each test below drives one of those to the surface.
 */

const test = require('node:test');
const assert = require('node:assert');
const d = require('../lib/duel');

const DAY = '2026-08-08';

/** Build a round/pick pair with an explicit, readable settle. */
function pair(id, { pick, entry = 100, settle, agent = null, state, day = DAY }) {
  return {
    round: {
      id, day, idx: id - 1, symbol: 'BTCUSDT', entry_price: entry,
      settle_price: settle === undefined ? null : settle,
      settle_state: state || null,
      agent_direction: agent,
      locks_at: `${day}T12:00:00.000Z`, resolves_at: `${day}T23:59:59.000Z`,
    },
    pick: { round_id: id, pick, created_at: `${day}T01:00:00.000Z` },
  };
}

function scored(pairs) {
  return d.scoreEntries(pairs.map(p => p.round), pairs.map(p => p.pick));
}

// ── the central claim ───────────────────────────────────────────────────────

test('a player with no resolved calls has NO accuracy — null, never 0%', () => {
  const acc = d.accuracy([]);
  assert.strictEqual(acc.pct, null);
  assert.notStrictEqual(acc.pct, 0, '0% would report a measured failure where there is only absence');
});

test('a day where every settle failed reports null accuracy, not a wipeout', () => {
  // Three calls, three unreadable settles. The naive shapes render this as
  // "0% — 0W/3L", which is a confident negative built from three absences.
  const entries = scored([
    pair(1, { pick: 'long', settle: null }),
    pair(2, { pick: 'short', settle: null }),
    pair(3, { pick: 'long', settle: undefined }),
  ]);
  const acc = d.accuracy(entries);
  assert.strictEqual(acc.pct, null, 'unreadable rounds cannot produce a percentage');
  assert.equal(acc.unresolved, 3);
  assert.equal(acc.scored, 0);
  assert.equal(acc.wrong, 0, 'an unreadable round is not a loss');
  assert.equal(acc.correct, 0);
});

test('unresolved rounds stay OUT of the denominator', () => {
  // One correct call, plus two we could not price. The honest read is 100%
  // of what resolved, with the two absences reported beside it — not 33%.
  const entries = scored([
    pair(1, { pick: 'long', settle: 110 }),
    pair(2, { pick: 'long', settle: null }),
    pair(3, { pick: 'short', settle: null }),
  ]);
  const acc = d.accuracy(entries);
  assert.strictEqual(acc.pct, 100);
  assert.equal(acc.scored, 1);
  assert.equal(acc.unresolved, 2);
  assert.notStrictEqual(acc.pct, 33.3, 'the unreadable rounds must not dilute the rate');
});

test('losses are COUNTED, never derived by subtraction', () => {
  // total(4) - wins(1) = 3 "losses" — but one is a push and one was never
  // priced. Only ONE call was actually wrong.
  const entries = scored([
    pair(1, { pick: 'long', settle: 110 }),          // correct
    pair(2, { pick: 'long', settle: 90 }),           // wrong
    pair(3, { pick: 'long', settle: 100 }),          // flat -> push
    pair(4, { pick: 'long', settle: null }),         // unresolved
  ]);
  const acc = d.accuracy(entries);
  assert.equal(acc.correct, 1);
  assert.equal(acc.wrong, 1, 'subtraction would have reported 3');
  assert.equal(acc.push, 1);
  assert.equal(acc.unresolved, 1);
  assert.equal(acc.scored, 2, 'the denominator is hits + misses only');
  assert.strictEqual(acc.pct, 50);
});

test('flat is its own outcome and is never folded into loss', () => {
  const entries = scored([
    pair(1, { pick: 'long', settle: 100 }),
    pair(2, { pick: 'short', settle: 100.01 }),
  ]);
  const acc = d.accuracy(entries);
  assert.equal(acc.wrong, 0, 'a market that did not move did not prove anyone wrong');
  assert.equal(acc.push, 2);
  assert.strictEqual(acc.pct, null, 'two pushes are not a measured 0%');
});

test('a player who only ever passes has null accuracy, not 0%', () => {
  const entries = scored([
    pair(1, { pick: 'pass', settle: 110 }),
    pair(2, { pick: 'pass', settle: 90 }),
  ]);
  const acc = d.accuracy(entries);
  assert.equal(acc.push, 2);
  assert.strictEqual(acc.pct, null);
});

// ── marks: a partial total must travel with what it could not include ───────

test('marks report what is still pending rather than summing over unknowns', () => {
  const entries = scored([
    pair(1, { pick: 'long', settle: 110, agent: 'short' }),  // correct + beat = 2
    pair(2, { pick: 'long', settle: 110 }),                  // correct        = 1
    pair(3, { pick: 'long', settle: null }),                 // unknown
    pair(4, { pick: 'short', settle: null }),                // unknown
  ]);
  const m = d.computeMarks(entries);
  assert.equal(m.marks, 3);
  assert.equal(m.beat_agent, 1);
  // The point: 3 is a PARTIAL total. Printing it alone would present a
  // sum over a set that excluded two rows as though it were the whole.
  assert.equal(m.pending, 2);
});

test('marks over an all-unresolved set are zero AND fully pending', () => {
  const entries = scored([
    pair(1, { pick: 'long', settle: null }),
    pair(2, { pick: 'long', settle: null }),
  ]);
  const m = d.computeMarks(entries);
  assert.equal(m.marks, 0);
  assert.equal(m.pending, 2, 'a zero total with nothing pending would claim a measured zero');
});

test('a terminally unresolved round stays out of the record forever', () => {
  const entries = scored([
    pair(1, { pick: 'long', settle: 110, state: 'unresolved' }),
  ]);
  assert.equal(entries[0].state, 'unresolved');
  assert.strictEqual(d.accuracy(entries).pct, null);
});

// ── joining ─────────────────────────────────────────────────────────────────

test('scoreEntries drops a pick whose round is missing rather than scoring it', () => {
  const rounds = [pair(1, { pick: 'long', settle: 110 }).round];
  const picks = [
    { round_id: 1, pick: 'long', created_at: `${DAY}T01:00:00Z` },
    { round_id: 99, pick: 'long', created_at: `${DAY}T01:00:00Z` },
  ];
  const entries = d.scoreEntries(rounds, picks);
  assert.equal(entries.length, 1, 'a pick with no round cannot be scored against nothing');
});

test('scoreEntries carries the move only when the outcome was readable', () => {
  const entries = scored([
    pair(1, { pick: 'long', settle: 110 }),
    pair(2, { pick: 'long', settle: null }),
  ]);
  assert.ok(Math.abs(entries[0].move_pct - 10) < 1e-9);
  assert.strictEqual(entries[1].move_pct, null);
});

// ── streaks and quests ──────────────────────────────────────────────────────

test('duelStreak counts days on which a call was made, not days that resolved', () => {
  // A streak is about showing up. Tying it to resolution would let an
  // upstream data outage silently break a player's record.
  const now = new Date('2026-08-08T12:00:00Z');
  const picks = [
    { created_at: '2026-08-08T01:00:00Z' },
    { created_at: '2026-08-07T01:00:00Z' },
    { created_at: '2026-08-06T01:00:00Z' },
  ];
  const s = d.duelStreak(picks, now);
  assert.equal(s.current, 3);
  assert.equal(s.best, 3);
  assert.equal(s.active_today, true);
});

test('duelStreak on no picks is a zero streak, which is a real count', () => {
  const s = d.duelStreak([], new Date('2026-08-08T12:00:00Z'));
  assert.deepEqual(s, { current: 0, best: 0, active_today: false });
});

test('weekly duel quests are two, deterministic, and shared by everyone', () => {
  const now = new Date('2026-08-08T12:00:00Z');
  const a = d.weeklyDuelQuests([], now);
  const b = d.weeklyDuelQuests([], now);
  assert.equal(a.length, 2);
  assert.deepEqual(a, b, 'same week must mean the same quests for every player');
  for (const q of a) {
    assert.equal(q.have, 0);
    assert.equal(q.done, false);
    assert.ok(q.target > 0);
  }
});

test('quest progress counts only THIS week and never exceeds its target', () => {
  const now = new Date('2026-08-06T12:00:00Z');           // a Thursday
  const thisWeek = `2026-08-04`;                           // Monday of it
  const lastWeek = `2026-07-28`;
  const entries = scored([
    pair(1, { pick: 'long', settle: 110, day: thisWeek }),
    pair(2, { pick: 'long', settle: 110, day: thisWeek }),
    pair(3, { pick: 'long', settle: 110, day: thisWeek }),
    pair(4, { pick: 'long', settle: 110, day: lastWeek }),
  ]);
  for (const q of d.weeklyDuelQuests(entries, now)) {
    assert.ok(q.have <= q.target, `${q.key} overshot its target`);
  }
  const pool = Object.fromEntries(d.DUEL_QUEST_POOL.map(q => [q.key, q]));
  const week = entries.filter(e => e.day === thisWeek);
  assert.equal(pool.three_calls.have(week), 3, 'last week must not count');
});
