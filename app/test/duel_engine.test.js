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

test('pickOutcome: a terminally unresolved call answers null even with a price', () => {
  const settled = { entry_price: 100, settle_price: 110 };
  assert.equal(d.pickOutcome(settled), 'up');
  assert.equal(d.pickOutcome({ ...settled, settle_state: 'unresolved' }), null);
  assert.equal(d.pickOutcome({ entry_price: 100, settle_price: null }), null);
  assert.equal(d.pickOutcome(null), null);
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
  // A symbol nobody could get a price for is a symbol nobody could call, so
  // it does not go on the card at all.
  assert.ok(!rounds.some(r => r.symbol === 'GHOSTUSDT'),
    'a symbol we cannot price must not become a round');
  assert.equal(rounds.length, d.ROUNDS_PER_DAY, 'the card is still filled from the majors');
  for (const r of rounds) assert.ok(TICKERS[r.symbol].price > 0);
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

test('buildRounds: the round carries no price and no horizon — those are the caller\'s', () => {
  // The fairness property, pinned. A shared entry snapped when the card opened
  // would hand every later player a free look at how the day had gone.
  const [r] = d.buildRounds('2026-08-08', [], TICKERS);
  assert.ok(!('entry_price' in r), 'the entry belongs to whoever calls, when they call');
  assert.ok(!('resolves_at' in r), 'the horizon runs from the call, not from the card');
  assert.ok(!('locks_at' in r));
  assert.deepEqual(Object.keys(r).sort(),
    ['agent_direction', 'day', 'idx', 'signal_key', 'symbol']);
});

test('horizonFor runs the stated window from the moment of the call', () => {
  assert.equal(d.horizonFor(new Date('2026-08-08T18:30:00Z')), '2026-08-09T18:30:00.000Z');
  assert.equal(d.horizonFor(new Date('2026-08-08T00:00:00Z')), '2026-08-09T00:00:00.000Z');
  assert.strictEqual(d.horizonFor('nonsense'), null);
});

test('buildRounds: a malformed day yields nothing rather than an Invalid Date round', () => {
  assert.deepEqual(d.buildRounds('not-a-day', [], TICKERS), []);
  assert.deepEqual(d.buildRounds(null, [], TICKERS), []);
});

// ── lock / settle predicates ────────────────────────────────────────────────

test('isCallable: a round is open for its whole day, and shut outside it', () => {
  const r = { day: '2026-08-08' };
  // The whole point of per-call entries: the card can stay open all day
  // because waiting buys nobody an advantage.
  assert.equal(d.isCallable(r, new Date('2026-08-08T00:00:01Z')), true);
  assert.equal(d.isCallable(r, new Date('2026-08-08T23:59:59Z')), true);
  assert.equal(d.isCallable(r, new Date('2026-08-09T00:00:01Z')), false);
  // Fail-closed on an unreadable day rather than accepting a call we cannot
  // place in time.
  assert.equal(d.isCallable({}, new Date('2026-08-08T12:00:00Z')), false);
  assert.equal(d.isCallable(null, new Date('2026-08-08T12:00:00Z')), false);
});

test('isDue / isAbandoned mark the two settlement boundaries of a call', () => {
  const p = { resolves_at: '2026-08-09T00:00:00Z' };
  assert.equal(d.isDue(p, new Date('2026-08-08T23:59:00Z')), false);
  assert.equal(d.isDue(p, new Date('2026-08-09T00:00:01Z')), true);
  assert.equal(d.isAbandoned(p, new Date('2026-08-10T00:00:00Z')), false);
  assert.equal(d.isAbandoned(p, new Date('2026-08-17T00:00:00Z')), true);
  // An absent horizon is never "due" — it is unknown, and acting on it would
  // settle a call against a time we never recorded.
  assert.equal(d.isDue({}, new Date('2030-01-01T00:00:00Z')), false);
});

// ── publicRound ─────────────────────────────────────────────────────────────

test('publicRound omits the agent stance entirely until it is revealed', () => {
  const round = { id: 1, day: '2026-08-08', idx: 0, symbol: 'BTCUSDT', agent_direction: 'long' };
  const at = new Date('2026-08-08T09:00:00Z');
  const hidden = d.publicRound(round, { revealAgent: false, now: at });
  // Absent, NOT null: a null field is indistinguishable from "the agent
  // passed", which is a real answer — so nulling it would leak one fact and
  // lie about another.
  assert.ok(!('agent_direction' in hidden), 'the stance must be absent, not null');
  assert.equal(JSON.stringify(hidden).includes('long'), false);

  const shown = d.publicRound(round, { revealAgent: true, now: at });
  assert.equal(shown.agent_direction, 'long');
});

test('publicPick keeps pending and unresolved distinct from each other', () => {
  const base = { pick: 'long', entry_price: 100, resolves_at: '2026-08-09T00:00:00Z' };

  // Horizon not reached: waiting, and it must not read as an outcome.
  const waiting = d.publicPick(base, { now: new Date('2026-08-08T18:00:00Z') });
  assert.equal(waiting.pending, true);
  assert.strictEqual(waiting.outcome, null);
  assert.ok(!('unresolved' in waiting));

  // Horizon reached, no price: unresolved — a different thing entirely.
  // Collapsing these two is how a waiting call starts reading as a loss.
  const dead = d.publicPick(base, { now: new Date('2026-08-09T06:00:00Z') });
  assert.equal(dead.unresolved, true);
  assert.strictEqual(dead.outcome, null);
  assert.ok(!('pending' in dead));
  assert.ok(!('move_pct' in dead), 'no move may be reported for a price we never read');

  // Settled: a real answer, with the move beside it.
  const done = d.publicPick({ ...base, settle_price: 110 },
    { now: new Date('2026-08-09T06:00:00Z') });
  assert.equal(done.outcome, 'up');
  assert.equal(done.move_pct, 10);
  assert.ok(!('pending' in done) && !('unresolved' in done));
});
