'use strict';
/**
 * Duel standings and Squads.
 *
 * The failure this file is really about: a board is the most tempting place in
 * the product to turn an absence into a number. A player with two calls has no
 * measurable accuracy, a squad nobody has played in has no rate at all, and
 * both are one `|| 0` away from being published as a confident 0% next to
 * everyone else's real figures.
 */

const test = require('node:test');
const assert = require('node:assert');
const s = require('../lib/duel_squads');

/** N scored entries, `hits` of them correct. */
function entries(n, hits, { unresolved = 0, push = 0, beat = 0 } = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const correct = i < hits;
    out.push({
      state: correct ? 'correct' : 'wrong',
      marks: correct ? (i < beat ? 2 : 1) : 0,
      beat_agent: correct && i < beat,
    });
  }
  for (let i = 0; i < unresolved; i++) out.push({ state: 'unresolved', marks: 0, beat_agent: false });
  for (let i = 0; i < push; i++) out.push({ state: 'push', marks: 0, beat_agent: false });
  return out;
}

// ── one player ──────────────────────────────────────────────────────────────

test('a player below the floor has NO rate, not a zero one', () => {
  const st = s.playerStanding('rookie', entries(2, 0));
  assert.equal(st.readable, false);
  assert.strictEqual(st.accuracy_pct, null,
    'two wrong calls is not enough to say someone is bad at this');
});

test('a player at the floor becomes readable', () => {
  const st = s.playerStanding('steady', entries(s.MIN_SCORED, 3));
  assert.equal(st.readable, true);
  assert.equal(st.accuracy_pct, 60);
  assert.equal(st.correct, 3);
  assert.equal(st.calls, s.MIN_SCORED);
});

test('unresolved calls neither count toward the floor nor damage the rate', () => {
  const st = s.playerStanding('unlucky', entries(s.MIN_SCORED, 5, { unresolved: 4 }));
  assert.equal(st.accuracy_pct, 100, 'four dead reads must not dilute a perfect record');
  assert.equal(st.unresolved, 4);
  assert.equal(st.readable, true);

  // ...and on their own they cannot make anyone readable.
  const ghost = s.playerStanding('ghost', entries(0, 0, { unresolved: 9 }));
  assert.equal(ghost.readable, false);
  assert.strictEqual(ghost.accuracy_pct, null);
});

test('a player with nothing at all is reported, not scored', () => {
  const st = s.playerStanding('nobody', []);
  assert.strictEqual(st.accuracy_pct, null);
  assert.equal(st.calls, 0);
  assert.equal(st.marks, 0);
  assert.equal(st.readable, false);
});

// ── the board ───────────────────────────────────────────────────────────────

test('the board ranks on accuracy, breaking ties on marks', () => {
  const board = s.buildBoard([
    s.playerStanding('a', entries(10, 6)),
    s.playerStanding('b', entries(10, 8)),
    s.playerStanding('c', entries(10, 8, { beat: 8 })),   // same rate, more marks
  ]);
  assert.deepEqual(board.rows.map((r) => r.handle), ['c', 'b', 'a']);
  assert.deepEqual(board.rows.map((r) => r.rank), [1, 2, 3]);
});

test('volume alone cannot buy a rank', () => {
  const board = s.buildBoard([
    s.playerStanding('grinder', entries(100, 40)),   // 40%, huge volume
    s.playerStanding('sniper', entries(6, 5)),       // 83%, barely readable
  ]);
  assert.equal(board.rows[0].handle, 'sniper');
});

test('players below the floor are COUNTED, not ranked and not dropped', () => {
  const board = s.buildBoard([
    s.playerStanding('ready', entries(10, 7)),
    s.playerStanding('new1', entries(1, 1)),
    s.playerStanding('new2', entries(0, 0)),
  ]);
  assert.equal(board.rows.length, 1);
  assert.equal(board.ranked_total, 1);
  // "1 player ranked" on its own would misreport how many people are playing.
  assert.equal(board.warming_up, 2);
  assert.equal(board.min_calls, s.MIN_SCORED);
  assert.ok(!board.rows.some((r) => r.handle === 'new1'),
    'an unreadable record must never appear with a number beside it');
});

test('an empty board is empty, and says nothing about anybody', () => {
  const board = s.buildBoard([]);
  assert.deepEqual(board.rows, []);
  assert.equal(board.ranked_total, 0);
  assert.equal(board.warming_up, 0);
});

// ── squads ──────────────────────────────────────────────────────────────────

const MEMBERS = [
  { id: 1, handle: 'captain', referred_by: null },
  { id: 2, handle: 'crew_a', referred_by: 1 },
  { id: 3, handle: 'crew_b', referred_by: 1 },
  { id: 9, handle: 'loner', referred_by: null },
];

function standingsFrom(map) {
  return (id) => map[String(id)];
}

test('a squad is the captain plus everyone who signed up on their code', () => {
  const table = s.buildSquads(MEMBERS, standingsFrom({
    1: s.playerStanding('captain', entries(10, 8)),
    2: s.playerStanding('crew_a', entries(10, 6)),
    3: s.playerStanding('crew_b', entries(10, 4)),
    9: s.playerStanding('loner', entries(10, 9)),
  }));
  const cap = table.rows.find((r) => r.captain === 'captain');
  assert.equal(cap.size, 3, 'the captain counts in their own squad');
  assert.equal(cap.accuracy_pct, 60, 'the mean of 80, 60 and 40');
  const loner = table.rows.find((r) => r.captain === 'loner');
  assert.equal(loner.size, 1, 'a player with no referrals is a squad of one');
});

test('a squad nobody has played enough in has NO rate', () => {
  const table = s.buildSquads(MEMBERS, standingsFrom({
    1: s.playerStanding('captain', entries(1, 0)),
    2: s.playerStanding('crew_a', entries(2, 1)),
    3: s.playerStanding('crew_b', []),
    9: s.playerStanding('loner', entries(10, 5)),
  }));
  assert.ok(!table.rows.some((r) => r.captain === 'captain'),
    'an unreadable squad is not ranked');
  assert.equal(table.warming_up, 1, '...but it is counted');
  assert.equal(table.ranked_total, 1);
});

test('only readable members contribute to a squad rate', () => {
  // One strong readable member, two who have barely started. The squad reads
  // as its readable member — averaging in a 0 for the others would publish a
  // rate nobody earned.
  const table = s.buildSquads(MEMBERS, standingsFrom({
    1: s.playerStanding('captain', entries(10, 9)),
    2: s.playerStanding('crew_a', entries(1, 0)),
    3: s.playerStanding('crew_b', []),
    9: s.playerStanding('loner', entries(10, 1)),
  }));
  const cap = table.rows.find((r) => r.captain === 'captain');
  assert.equal(cap.accuracy_pct, 90);
  assert.equal(cap.readable_members, 1);
  assert.equal(cap.size, 3, 'the newcomers still count as squad members');
});

test('marks and beats accumulate across the whole squad', () => {
  const table = s.buildSquads(MEMBERS, standingsFrom({
    1: s.playerStanding('captain', entries(10, 5, { beat: 5 })),   // 5 x 2 marks
    2: s.playerStanding('crew_a', entries(10, 5)),                 // 5 x 1 mark
    3: s.playerStanding('crew_b', entries(10, 0)),
    9: s.playerStanding('loner', []),
  }));
  const cap = table.rows.find((r) => r.captain === 'captain');
  assert.equal(cap.marks, 15);
  assert.equal(cap.beat_agent, 5);
});

test('a referred player who recruits captains a squad of their own too', () => {
  const chain = [
    { id: 1, handle: 'top', referred_by: null },
    { id: 2, handle: 'middle', referred_by: 1 },
    { id: 3, handle: 'bottom', referred_by: 2 },
  ];
  const table = s.buildSquads(chain, standingsFrom({
    1: s.playerStanding('top', entries(10, 8)),
    2: s.playerStanding('middle', entries(10, 6)),
    3: s.playerStanding('bottom', entries(10, 4)),
  }));
  const top = table.rows.find((r) => r.captain === 'top');
  const mid = table.rows.find((r) => r.captain === 'middle');
  assert.equal(top.size, 2, 'top + middle');
  assert.equal(mid.size, 2, 'middle + bottom');
});

test('a referrer who never opted in does not strand their recruits', () => {
  // The captain is not on the opted-in list, so their recruits fall back to
  // captaining themselves rather than vanishing from the standings.
  const orphans = [
    { id: 2, handle: 'crew_a', referred_by: 77 },
    { id: 3, handle: 'crew_b', referred_by: 77 },
  ];
  const table = s.buildSquads(orphans, standingsFrom({
    2: s.playerStanding('crew_a', entries(10, 8)),
    3: s.playerStanding('crew_b', entries(10, 6)),
  }));
  assert.equal(table.ranked_total, 2);
  assert.deepEqual(table.rows.map((r) => r.captain), ['crew_a', 'crew_b']);
});

test('no squad or board row carries an identifier or an amount', () => {
  const table = s.buildSquads(MEMBERS, standingsFrom({
    1: s.playerStanding('captain', entries(10, 8)),
    2: s.playerStanding('crew_a', entries(10, 6)),
    3: s.playerStanding('crew_b', entries(10, 4)),
    9: s.playerStanding('loner', entries(10, 9)),
  }));
  const board = s.buildBoard([s.playerStanding('captain', entries(10, 8))]);
  const flat = JSON.stringify(table) + JSON.stringify(board);
  for (const banned of ['user_id', '"id"', 'email', 'balance', 'equity', 'margin',
    'pnl', 'referred_by', 'usd']) {
    assert.equal(flat.includes(banned), false, `${banned} must not ride a public standing`);
  }
});
