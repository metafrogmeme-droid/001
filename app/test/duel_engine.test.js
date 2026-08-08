'use strict';
/**
 * Daily Duel — the scoring rules, exercised as behaviour.
 *
 * Every case here is a rule someone could plausibly "simplify" into a bug:
 * folding flat into a loss, treating an unreadable price as a break-even,
 * crediting a player with beating an agent that never had a view, or letting
 * marks go negative. The engine is pure, so these run the real functions
 * rather than matching their source.
 */

const test = require('node:test');
const assert = require('node:assert');
const d = require('../lib/duel');

// ── outcomeOf ────────────────────────────────────────────────────────────────

test('outcomeOf: a readable move classifies up / down / flat', () => {
  assert.equal(d.outcomeOf(100, 105), 'up');
  assert.equal(d.outcomeOf(100, 95), 'down');
  assert.equal(d.outcomeOf(100, 100), 'flat');
  // Inside the dead band the "direction" is noise, not a move.
  assert.equal(d.outcomeOf(100, 100.04), 'flat');
  assert.equal(d.outcomeOf(100, 99.96), 'flat');
  // ...and just outside it, it is a move.
  assert.equal(d.outcomeOf(100, 100.06), 'up');
});

test('outcomeOf: an unreadable price is null, never flat and never a zero', () => {
  for (const bad of [null, undefined, NaN, '', 'abc', 0, -1]) {
    assert.equal(d.outcomeOf(100, bad), null, `settle ${String(bad)} must be null`);
    assert.equal(d.outcomeOf(bad, 100), null, `entry ${String(bad)} must be null`);
  }
  // The trap this guards: 0 is falsy AND numerically "no change", so a
  // careless implementation reports a failed read as a flat market.
  assert.notEqual(d.outcomeOf(100, 0), 'flat');
});

test('roundOutcome: a terminally unresolved round answers null even with a price', () => {
  const settled = { entry_price: 100, settle_price: 110 };
  assert.equal(d.roundOutcome(settled), 'up');
  assert.equal(d.roundOutcome({ ...settled, settle_state: 'unresolved' }), null);
  assert.equal(d.roundOutcome({ entry_price: 100, settle_price: null }), null);
  assert.equal(d.roundOutcome(null), null);
});

// ── scorePick ────────────────────────────────────────────────────────────────

test('scorePick: a correct directional call scores one mark', () => {
  assert.deepEqual(d.scorePick('long', 'up', null),
    { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scorePick('short', 'down', null),
    { state: 'correct', marks: 1, beat_agent: false });
});

test('scorePick: beating the agent doubles it — but only if the agent had a view', () => {
  // Agent said short, market went up, player said long: beaten.
  assert.deepEqual(d.scorePick('long', 'up', 'short'),
    { state: 'correct', marks: 2, beat_agent: true });
  // Agent agreed and was also right: no bonus.
  assert.deepEqual(d.scorePick('long', 'up', 'long'),
    { state: 'correct', marks: 1, beat_agent: false });
  // Agent passed on this round. There is no opinion to beat, so crediting a
  // win over it would invent an opponent.
  assert.deepEqual(d.scorePick('long', 'up', null),
    { state: 'correct', marks: 1, beat_agent: false });
  // ...and the same holds for an unparseable stance.
  assert.equal(d.scorePick('long', 'up', 'sideways').beat_agent, false);
});

test('scorePick: PASS scores on flat and is a push otherwise — never a loss', () => {
  assert.deepEqual(d.scorePick('pass', 'flat', null),
    { state: 'correct', marks: 1, beat_agent: false });
  assert.equal(d.scorePick('pass', 'up', 'long').state, 'push');
  assert.equal(d.scorePick('pass', 'down', 'short').state, 'push');
  // A declined round is not a miss. Scoring it as one would punish the
  // discipline the product spends its whole surface area teaching.
  assert.notEqual(d.scorePick('pass', 'up', null).state, 'wrong');
});

test('scorePick: a directional call on a flat market is a push, not a miss', () => {
  assert.equal(d.scorePick('long', 'flat', null).state, 'push');
  assert.equal(d.scorePick('short', 'flat', null).state, 'push');
});

test('scorePick: an unreadable outcome is unresolved, not a loss', () => {
  const r = d.scorePick('long', null, 'short');
  assert.equal(r.state, 'unresolved');
  assert.equal(r.marks, 0);
  assert.equal(r.beat_agent, false);
});

test('scorePick: marks are never negative, on any input combination', () => {
  const picks = ['long', 'short', 'pass', 'nonsense', null];
  const outcomes = ['up', 'down', 'flat', null];
  const agents = ['long', 'short', null, 'garbage'];
  for (const p of picks) {
    for (const o of outcomes) {
      for (const a of agents) {
        const r = d.scorePick(p, o, a);
        assert.ok(r.marks >= 0, `marks went negative for ${p}/${o}/${a}`);
        assert.ok(r.marks <= 2, `marks exceeded the cap for ${p}/${o}/${a}`);
      }
    }
  }
});

test('normPick / normDirection refuse to guess a default', () => {
  assert.equal(d.normPick('LONG'), 'long');
  assert.equal(d.normPick(' Pass '), 'pass');
  assert.equal(d.normPick('maybe'), null);
  assert.equal(d.normPick(null), null);
  assert.equal(d.normDirection('BUY'), 'long');
  assert.equal(d.normDirection('sell'), 'short');
  assert.equal(d.normDirection(''), null);
});

// ── buildRounds ──────────────────────────────────────────────────────────────

const TICKERS = {
  BTCUSDT: { price: 60000, volume: 900 },
  ETHUSDT: { price: 3000, volume: 800 },
  SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 },
  XRPUSDT: { price: 0.5, volume: 500 },
  ARBUSDT: { price: 1.2, volume: 50 },
};

test('buildRounds: the agent\'s own calls lead, strongest conviction first', () => {
  const signals = [
    { symbol: 'SOLUSDT', direction: 'short', confidence: 0.6, signal_key: 'k2' },
    { symbol: 'ARBUSDT', direction: 'long', confidence: 0.9, signal_key: 'k1' },
  ];
  const rounds = d.buildRounds('2026-08-08', signals, TICKERS);
  assert.equal(rounds.length, 3);
  assert.equal(rounds[0].symbol, 'ARBUSDT');
  assert.equal(rounds[0].agent_direction, 'long');
  assert.equal(rounds[0].signal_key, 'k1');
  assert.equal(rounds[1].symbol, 'SOLUSDT');
  assert.equal(rounds[1].agent_direction, 'short');
  // Topped up from the majors by volume — and the agent has no view there.
  assert.equal(rounds[2].symbol, 'BTCUSDT');
  assert.equal(rounds[2].agent_direction, null);
  assert.equal(rounds[2].signal_key, null);
});

test('buildRounds: a signal with no readable price yields no round, not a guessed one', () => {
  const signals = [{ symbol: 'GHOSTUSDT', direction: 'long', confidence: 0.99 }];
  const rounds = d.buildRounds('2026-08-08', signals, TICKERS);
  assert.ok(!rounds.some(r => r.symbol === 'GHOSTUSDT'),
    'a symbol we cannot price must not become a round');
  for (const r of rounds) {
    assert.ok(Number.isFinite(r.entry_price) && r.entry_price > 0);
  }
});

test('buildRounds: unknown confidence ranks below every known one', () => {
  const signals = [
    { symbol: 'ARBUSDT', direction: 'long' },                      // no confidence
    { symbol: 'SOLUSDT', direction: 'short', confidence: 0.01 },   // tiny but known
  ];
  const rounds = d.buildRounds('2026-08-08', signals, TICKERS);
  assert.equal(rounds[0].symbol, 'SOLUSDT',
    'a measured 0.01 must outrank an absent confidence');
});

test('buildRounds: deterministic, capped, and never repeats a symbol', () => {
  const signals = [
    { symbol: 'BTCUSDT', direction: 'long', confidence: 0.9 },
    { symbol: 'BTCUSDT', direction: 'short', confidence: 0.8 },
  ];
  const a = d.buildRounds('2026-08-08', signals, TICKERS);
  const b = d.buildRounds('2026-08-08', signals, TICKERS);
  assert.deepEqual(a, b);
  assert.equal(a.length, d.ROUNDS_PER_DAY);
  assert.equal(new Set(a.map(r => r.symbol)).size, a.length);
  a.forEach((r, i) => assert.equal(r.idx, i));
});

test('buildRounds: lock precedes settle, both anchored to the UTC day', () => {
  const [r] = d.buildRounds('2026-08-08', [], TICKERS);
  assert.equal(r.locks_at, '2026-08-08T12:00:00.000Z');
  assert.equal(r.resolves_at, '2026-08-09T00:00:00.000Z');
  assert.ok(Date.parse(r.locks_at) < Date.parse(r.resolves_at));
});

test('buildRounds: a malformed day yields nothing rather than an Invalid Date round', () => {
  assert.deepEqual(d.buildRounds('not-a-day', [], TICKERS), []);
  assert.deepEqual(d.buildRounds(null, [], TICKERS), []);
});

// ── lock / settle predicates ────────────────────────────────────────────────

test('isLocked: an unknown lock time is treated as CLOSED, not open', () => {
  // Fail-closed: the alternative would accept picks on a round whose deadline
  // we cannot read — i.e. after the outcome may already be visible.
  assert.equal(d.isLocked({}, new Date('2026-08-08T00:00:00Z')), true);
  assert.equal(d.isLocked({ locks_at: 'garbage' }, new Date('2026-08-08T00:00:00Z')), true);
  assert.equal(d.isLocked({ locks_at: '2026-08-08T12:00:00Z' },
    new Date('2026-08-08T11:59:00Z')), false);
  assert.equal(d.isLocked({ locks_at: '2026-08-08T12:00:00Z' },
    new Date('2026-08-08T12:00:01Z')), true);
});

test('isDue / isAbandoned mark the two settlement boundaries', () => {
  const r = { resolves_at: '2026-08-09T00:00:00Z' };
  assert.equal(d.isDue(r, new Date('2026-08-08T23:59:00Z')), false);
  assert.equal(d.isDue(r, new Date('2026-08-09T00:00:01Z')), true);
  assert.equal(d.isAbandoned(r, new Date('2026-08-10T00:00:00Z')), false);
  assert.equal(d.isAbandoned(r, new Date('2026-08-17T00:00:00Z')), true);
});

// ── publicRound ─────────────────────────────────────────────────────────────

test('publicRound omits the agent stance entirely until it is revealed', () => {
  const round = {
    id: 1, day: '2026-08-08', idx: 0, symbol: 'BTCUSDT', entry_price: 60000,
    agent_direction: 'long', locks_at: '2026-08-08T12:00:00Z',
    resolves_at: '2026-08-09T00:00:00Z',
  };
  const hidden = d.publicRound(round, { revealAgent: false });
  // Absent, NOT null: a null field is indistinguishable from "the agent
  // passed", which is a real answer — so nulling it would leak one fact and
  // lie about another.
  assert.ok(!('agent_direction' in hidden), 'the stance must be absent, not null');
  assert.equal(JSON.stringify(hidden).includes('long'), false);

  const shown = d.publicRound(round, { revealAgent: true });
  assert.equal(shown.agent_direction, 'long');
});

test('publicRound: a due-but-unpriced round says unresolved, not flat', () => {
  const round = {
    id: 2, day: '2026-08-08', idx: 1, symbol: 'ETHUSDT', entry_price: 3000,
    settle_price: null, agent_direction: null,
    locks_at: '2026-08-08T12:00:00Z', resolves_at: '2026-08-09T00:00:00Z',
  };
  const v = d.publicRound(round, { revealAgent: true, now: new Date('2026-08-09T06:00:00Z') });
  assert.equal(v.outcome, null);
  assert.equal(v.unresolved, true);
  assert.ok(!('move_pct' in v), 'no move may be reported for a price we never read');
});
