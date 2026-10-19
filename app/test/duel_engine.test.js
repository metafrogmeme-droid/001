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

// ── the other categories ────────────────────────────────────────────────────

test('rangeOutcome: a move at or past the band breaks it, under it holds; no band, no answer', () => {
  assert.equal(d.rangeOutcome(100, 102, 2), 'break');
  assert.equal(d.rangeOutcome(100, 98, 2), 'break');
  assert.equal(d.rangeOutcome(100, 101.99, 2), 'hold');
  assert.equal(d.rangeOutcome(100, 100, 2), 'hold');
  for (const bad of [null, undefined, 0, -2, 'abc']) {
    assert.strictEqual(d.rangeOutcome(100, 110, bad), null, `band ${String(bad)} cannot settle a range`);
  }
  assert.strictEqual(d.rangeOutcome(100, null, 2), null);
});

test('moverOutcome: the bigger absolute move wins, within the dead band is a tie, half a comparison is nothing', () => {
  assert.equal(d.moverOutcome(100, 103, 50, 51), 'first');     // +3% vs +2%
  assert.equal(d.moverOutcome(100, 97, 50, 52), 'second');     // -3% vs +4%
  assert.equal(d.moverOutcome(100, 102, 50, 51), 'tie');       // +2% vs +2%
  assert.equal(d.moverOutcome(100, 102.04, 50, 51), 'tie');    // 0.04 pp apart
  assert.equal(d.moverOutcome(100, 102.06, 50, 51), 'first');  // 0.06 pp apart
  assert.strictEqual(d.moverOutcome(100, 103, 50, null), null);
  assert.strictEqual(d.moverOutcome(100, null, 50, 51), null);
});

test('scoreCall: a range call is right when it names the outcome, and a PASS on it is always declined', () => {
  assert.deepEqual(d.scoreCall('range', 'break', 'break', null), { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scoreCall('range', 'hold', 'break', null), { state: 'wrong', marks: 0, beat_agent: false });
  assert.equal(d.scoreCall('range', 'pass', 'hold', null).state, 'push');
  assert.equal(d.scoreCall('range', 'pass', 'break', null).state, 'push');
  // The Claw holds no view on a range, so there is nobody to beat.
  assert.equal(d.scoreCall('range', 'break', 'break', 'long').beat_agent, false);
  assert.equal(d.scoreCall('range', 'break', null, null).state, 'unresolved');
  // A classic pick on a range round is not a call on it.
  assert.equal(d.scoreCall('range', 'long', 'break', null).state, 'invalid');
});

test('scoreCall: a mover call names a side; a tie is a push for a side and a correct PASS', () => {
  assert.deepEqual(d.scoreCall('mover', 'first', 'first', null), { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scoreCall('mover', 'second', 'first', null), { state: 'wrong', marks: 0, beat_agent: false });
  assert.equal(d.scoreCall('mover', 'first', 'tie', null).state, 'push');
  assert.deepEqual(d.scoreCall('mover', 'pass', 'tie', null), { state: 'correct', marks: 1, beat_agent: false });
  assert.equal(d.scoreCall('mover', 'pass', 'second', null).state, 'push');
  assert.equal(d.scoreCall('mover', 'first', null, null).state, 'unresolved');
});

test('scoreCall: backing the Claw correctly scores one, fading it correctly scores two and beats it', () => {
  assert.deepEqual(d.scoreCall('backfade', 'back', 'down', 'short'), { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scoreCall('backfade', 'fade', 'up', 'short'), { state: 'correct', marks: 2, beat_agent: true });
  assert.deepEqual(d.scoreCall('backfade', 'back', 'up', 'short'), { state: 'wrong', marks: 0, beat_agent: false });
  assert.deepEqual(d.scoreCall('backfade', 'fade', 'down', 'short'), { state: 'wrong', marks: 0, beat_agent: false });
  assert.equal(d.scoreCall('backfade', 'back', 'flat', 'long').state, 'push');
  assert.deepEqual(d.scoreCall('backfade', 'pass', 'flat', 'long'), { state: 'correct', marks: 1, beat_agent: false });
  assert.equal(d.scoreCall('backfade', 'pass', 'up', 'long').state, 'push');
  // A back/fade round with no stance could never have been asked.
  assert.equal(d.scoreCall('backfade', 'back', 'up', null).state, 'invalid');
  assert.equal(d.scoreCall('backfade', 'back', null, 'long').state, 'unresolved');
});

test('scoreCall: marks are never negative and never above two, over every category', () => {
  const outcomes = ['up', 'down', 'flat', 'break', 'hold', 'first', 'second', 'tie', null];
  const agents = ['long', 'short', null, 'garbage'];
  for (const kind of d.KIND_NAMES) {
    for (const p of d.ALL_PICKS.concat(['nonsense', null])) {
      for (const o of outcomes) {
        for (const a of agents) {
          const r = d.scoreCall(kind, p, o, a);
          assert.ok(r.marks >= 0 && r.marks <= 2, `${kind}/${p}/${o}/${a}: ${r.marks}`);
          assert.ok(['correct', 'wrong', 'push', 'unresolved', 'invalid'].includes(r.state));
        }
      }
    }
  }
});

test('scoreEntries scores each pick under its round\'s category', () => {
  const rounds = [
    { id: 1, day: '2026-08-08', idx: 0, symbol: 'BTCUSDT', agent_direction: 'long' },          // classic, no kind stored
    { id: 2, day: '2026-08-08', idx: 3, kind: 'backfade', symbol: 'SOLUSDT', agent_direction: 'short' },
    { id: 3, day: '2026-08-08', idx: 4, kind: 'range', symbol: 'BNBUSDT', threshold_pct: 2 },
    { id: 4, day: '2026-08-08', idx: 5, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT' },
    { id: 5, day: '2026-08-08', idx: 6, kind: 'weather', symbol: 'BTCUSDT' },
  ];
  const picks = [
    { round_id: 1, pick: 'short', entry_price: 100, settle_price: 90 },
    { round_id: 2, pick: 'fade', entry_price: 100, settle_price: 110 },
    { round_id: 3, pick: 'hold', entry_price: 100, settle_price: 101 },
    { round_id: 4, pick: 'second', entry_price: 100, settle_price: 101, entry_price_b: 50, settle_price_b: 55 },
    { round_id: 4, pick: 'first', entry_price: 100, settle_price: 101, entry_price_b: 50, settle_price_b: null },
    { round_id: 5, pick: 'long', entry_price: 100, settle_price: 110 },
  ];
  const e = d.scoreEntries(rounds, picks);
  assert.deepEqual(e.map((x) => [x.kind, x.state, x.marks, x.beat_agent]), [
    ['direction', 'correct', 2, true],
    ['backfade', 'correct', 2, true],
    ['range', 'correct', 1, false],
    ['mover', 'correct', 1, false],
    ['mover', 'unresolved', 0, false],
  ], 'and the round of no known kind is dropped, not scored as a classic');
  assert.equal(e[3].symbol_b, 'ARBUSDT');
  assert.equal(e[3].outcome, 'second');
  assert.equal(e[3].move_pct_b, 10);
  assert.strictEqual(e[4].move_pct_b, null, 'no move is reported for a price never read');
  assert.equal(e[2].threshold_pct, 2);
});

test('normPick takes a category, and refuses a word another category takes', () => {
  assert.equal(d.normPick('BREAK', 'range'), 'break');
  assert.equal(d.normPick('break', 'direction'), null);
  assert.equal(d.normPick('Second', 'mover'), 'second');
  assert.equal(d.normPick('fade', 'backfade'), 'fade');
  assert.equal(d.normPick('fade', 'mover'), null);
  assert.equal(d.normPick('pass', 'range'), 'pass');
  // null: any category's word, for a first look before the round is known.
  assert.equal(d.normPick('hold', null), 'hold');
  assert.equal(d.normPick('maybe', null), null);
  assert.deepEqual(d.ALL_PICKS, ['long', 'short', 'pass', 'back', 'fade', 'break', 'hold', 'first', 'second',
    'home', 'draw', 'away']);
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

/** The card the fixture prices: three classic rounds, a range, a mover. */
const FULL_FIXTURE_CARD = d.CARD.direction + d.CARD.range + d.CARD.mover;

test('buildRounds: the agent\'s strongest call leads the classic rounds; its second is the back/fade round', () => {
  const signals = [
    { symbol: 'SOLUSDT', direction: 'short', confidence: 0.6, signal_key: 'k2' },
    { symbol: 'ARBUSDT', direction: 'long', confidence: 0.9, signal_key: 'k1' },
  ];
  const rounds = d.buildRounds('2026-08-08', signals, TICKERS);
  const classic = rounds.filter((r) => r.kind === 'direction');
  assert.equal(classic.length, d.CARD.direction);
  assert.equal(classic[0].symbol, 'ARBUSDT');
  assert.equal(classic[0].agent_direction, 'long');
  assert.equal(classic[0].signal_key, 'k1');
  // Topped up from the pool by volume — and the agent has no view there.
  assert.equal(classic[1].symbol, 'BTCUSDT');
  assert.equal(classic[1].agent_direction, null);
  assert.equal(classic[1].signal_key, null);
  assert.equal(classic[2].symbol, 'ETHUSDT');
  // The second call is the question of the back/fade round, stance and all.
  const bf = rounds.find((r) => r.kind === 'backfade');
  assert.ok(bf, 'two agent calls make a back/fade round');
  assert.equal(bf.symbol, 'SOLUSDT');
  assert.equal(bf.agent_direction, 'short');
  assert.equal(bf.signal_key, 'k2');
  // ...and that symbol is on NO classic round, whose hidden stance it would print.
  assert.ok(!classic.some((r) => r.symbol === 'SOLUSDT'));
  // The classic rounds come first, then the rest.
  assert.deepEqual(rounds.map((r) => r.kind), ['direction', 'direction', 'direction', 'backfade', 'range']);
  // Six symbols priced, six used: nothing is left for the mover's pair.
  assert.ok(!rounds.some((r) => r.kind === 'mover'), 'one symbol alone is no comparison');
});

test('buildRounds: one agent call is a classic round, and there is no back/fade round to fade', () => {
  const rounds = d.buildRounds('2026-08-08',
    [{ symbol: 'ARBUSDT', direction: 'long', confidence: 0.9, signal_key: 'k1' }], TICKERS);
  assert.ok(!rounds.some((r) => r.kind === 'backfade'));
  assert.equal(rounds[0].symbol, 'ARBUSDT');
  assert.equal(rounds[0].kind, 'direction');
});

test('buildRounds: the range round carries its band, the mover its pair, and no other round carries either', () => {
  const rounds = d.buildRounds('2026-08-08', [], TICKERS);
  assert.deepEqual(rounds.map((r) => r.kind), ['direction', 'direction', 'direction', 'range', 'mover']);
  const range = rounds.find((r) => r.kind === 'range');
  assert.equal(range.symbol, 'BNBUSDT');
  assert.equal(range.threshold_pct, d.RANGE_BAND_PCT.major, 'a major asks the major band');
  assert.equal(d.rangeBandFor('ARBUSDT'), d.RANGE_BAND_PCT.other);
  const mover = rounds.find((r) => r.kind === 'mover');
  assert.equal(mover.symbol, 'XRPUSDT');
  assert.equal(mover.symbol_b, 'ARBUSDT');
  for (const r of rounds) {
    if (r.kind !== 'range') assert.strictEqual(r.threshold_pct, null, r.kind + ' carries a band');
    if (r.kind !== 'mover') assert.strictEqual(r.symbol_b, null, r.kind + ' carries a pair');
    assert.deepEqual(d.picksFor(r.kind).length, 3);
  }
});

test('buildRounds: a signal with no readable price yields no round, not a guessed one', () => {
  const signals = [{ symbol: 'GHOSTUSDT', direction: 'long', confidence: 0.99 }];
  const rounds = d.buildRounds('2026-08-08', signals, TICKERS);
  // A symbol nobody could get a price for is a symbol nobody could call, so
  // it does not go on the card at all.
  assert.ok(!rounds.some(r => r.symbol === 'GHOSTUSDT'),
    'a symbol we cannot price must not become a round');
  assert.equal(rounds.length, FULL_FIXTURE_CARD, 'the card is still filled from the pool');
  for (const r of rounds) {
    assert.ok(TICKERS[r.symbol].price > 0);
    if (r.symbol_b) assert.ok(TICKERS[r.symbol_b].price > 0);
  }
});

test('buildRounds: a pool symbol without a price costs a round, never a guessed entry', () => {
  const thin = { BTCUSDT: { price: 60000, volume: 900 }, ETHUSDT: { price: 3000, volume: 800 },
    SOLUSDT: { price: null, volume: 700 } };
  const rounds = d.buildRounds('2026-08-08', [], thin);
  assert.deepEqual(rounds.map((r) => r.kind), ['direction', 'direction'],
    'two priced symbols make two classic rounds and nothing else');
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
  assert.equal(a.length, FULL_FIXTURE_CARD);
  assert.ok(a.length <= d.ROUNDS_PER_DAY);
  const syms = a.flatMap((r) => (r.symbol_b ? [r.symbol, r.symbol_b] : [r.symbol]));
  assert.equal(new Set(syms).size, syms.length, 'a symbol is on the card once, the mover\'s pair included');
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
    ['agent_direction', 'day', 'idx', 'kind', 'meta', 'signal_key', 'symbol', 'symbol_b', 'threshold_pct']);
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
  assert.equal(JSON.stringify(hidden).includes('agent_direction'), false);
  // The picks the round takes are not the stance: 'long' is on offer, not held.
  assert.deepEqual(hidden.options, ['long', 'short', 'pass']);
  assert.equal(hidden.kind, 'direction');

  const shown = d.publicRound(round, { revealAgent: true, now: at });
  assert.equal(shown.agent_direction, 'long');
});

test('publicRound: a back/fade round shows the stance from the start, because it IS the question', () => {
  const round = { id: 2, day: '2026-08-08', idx: 3, kind: 'backfade', symbol: 'SOLUSDT', agent_direction: 'short' };
  const at = new Date('2026-08-08T09:00:00Z');
  const view = d.publicRound(round, { revealAgent: false, now: at });
  assert.equal(view.agent_direction, 'short');
  assert.deepEqual(view.options, ['back', 'fade', 'pass']);
});

test('publicRound: a range round carries its band and a mover its pair; a round of no known kind is open to nobody', () => {
  const at = new Date('2026-08-08T09:00:00Z');
  const range = d.publicRound({ id: 3, day: '2026-08-08', idx: 3, kind: 'range', symbol: 'BNBUSDT', threshold_pct: 2 }, { now: at });
  assert.equal(range.threshold_pct, 2);
  assert.ok(!('symbol_b' in range));
  assert.deepEqual(range.options, ['break', 'hold', 'pass']);
  const mover = d.publicRound({ id: 4, day: '2026-08-08', idx: 4, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT' }, { now: at, revealAgent: true });
  assert.equal(mover.symbol_b, 'ARBUSDT');
  assert.ok(!('threshold_pct' in mover));
  assert.deepEqual(mover.options, ['first', 'second', 'pass']);
  // Neither round ever held a stance, so neither reveals one, even after a call.
  assert.ok(!('agent_direction' in mover), 'a mover has no stance to reveal');
  assert.ok(!('agent_direction' in d.publicRound({ id: 3, day: '2026-08-08', idx: 3, kind: 'range', symbol: 'BNBUSDT', threshold_pct: 2 }, { now: at, revealAgent: true })));
  // A stored round without a kind predates the categories and is a classic round.
  assert.equal(d.publicRound({ id: 5, day: '2026-08-08', idx: 0, symbol: 'BTCUSDT' }, { now: at }).kind, 'direction');
  // A kind this code does not know: no picks, not callable, and no guess at what it meant.
  const odd = d.publicRound({ id: 6, day: '2026-08-08', idx: 0, kind: 'weather', symbol: 'BTCUSDT' }, { now: at });
  assert.strictEqual(odd.kind, null);
  assert.deepEqual(odd.options, []);
  assert.equal(odd.callable, false);
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

// ── football (2026-10-19) ───────────────────────────────────────────────────

const MATCH = { provider: 'football-data', event_id: '441', home: 'Arsenal FC', away: 'Chelsea FC',
  home_short: 'Arsenal', away_short: 'Chelsea', competition: 'Premier League', kickoff: '2026-08-08T19:00:00.000Z' };

test('buildRounds deals the match last, with its teams and kick-off on the round', () => {
  const rounds = d.buildRounds('2026-08-08', [], TICKERS, MATCH);
  assert.deepEqual(rounds.map((r) => r.kind), ['direction', 'direction', 'direction', 'range', 'mover', 'sports']);
  const s = rounds[5];
  assert.equal(s.symbol, 'FD:441');
  assert.equal(s.meta.home, 'Arsenal FC');
  assert.equal(s.meta.kickoff, '2026-08-08T19:00:00.000Z');
  assert.strictEqual(s.agent_direction, null, 'the Claw holds no view on football');
  // No match, or a match without a kick-off or a team, deals no round.
  assert.ok(!d.buildRounds('2026-08-08', [], TICKERS, null).some((r) => r.kind === 'sports'));
  assert.ok(!d.buildRounds('2026-08-08', [], TICKERS, { ...MATCH, kickoff: 'soon' }).some((r) => r.kind === 'sports'));
  assert.ok(!d.buildRounds('2026-08-08', [], TICKERS, { ...MATCH, away: '' }).some((r) => r.kind === 'sports'));
  // The other rounds carry no meta.
  for (const r of rounds.slice(0, 5)) assert.strictEqual(r.meta, null);
});

test('a football round is open until kick-off, on its day, and to nobody when the kick-off cannot be read', () => {
  const r = { id: 9, day: '2026-08-08', idx: 5, kind: 'sports', symbol: 'FD:441', meta: JSON.stringify(MATCH) };
  assert.equal(d.isCallable(r, new Date('2026-08-08T18:59:59Z')), true);
  assert.equal(d.isCallable(r, new Date('2026-08-08T19:00:00Z')), false, 'kick-off closes it');
  assert.equal(d.isCallable(r, new Date('2026-08-07T23:00:00Z')), false, 'not before its day');
  assert.equal(d.isCallable({ ...r, meta: JSON.stringify({ ...MATCH, kickoff: null }) }, new Date('2026-08-08T12:00:00Z')), false);
  assert.equal(d.isCallable({ ...r, meta: '{not json' }, new Date('2026-08-08T12:00:00Z')), false);
  assert.equal(d.sportsHorizonFor(r), '2026-08-08T21:30:00.000Z', 'full time: 150 minutes after kick-off');
  assert.strictEqual(d.sportsHorizonFor({ ...r, meta: null }), null);
  assert.equal(d.metaOf(r).home_short, 'Arsenal');
  assert.deepEqual(d.metaOf({ meta: MATCH }), MATCH, 'an object is taken as it is');
  assert.strictEqual(d.metaOf({ meta: '' }), null);
});

test('sportsOutcome reads a final score, and never a score that is not two numbers', () => {
  assert.equal(d.sportsOutcome(2, 1), 'home');
  assert.equal(d.sportsOutcome(0, 3), 'away');
  assert.equal(d.sportsOutcome(1, 1), 'draw');
  assert.equal(d.sportsOutcome(0, 0), 'draw', 'goalless is a draw, not an absence');
  for (const [h, a] of [[null, 1], [1, null], [undefined, undefined], ['x', 1], [-1, 0], [NaN, 2]]) {
    assert.strictEqual(d.sportsOutcome(h, a), null, `${h}-${a}`);
  }
});

test('scoreCall on football: the pick names the winner or the draw; a PASS is declined; nobody beats the Claw', () => {
  assert.deepEqual(d.scoreCall('sports', 'home', 'home', null), { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scoreCall('sports', 'draw', 'draw', null), { state: 'correct', marks: 1, beat_agent: false });
  assert.deepEqual(d.scoreCall('sports', 'away', 'home', null), { state: 'wrong', marks: 0, beat_agent: false });
  assert.equal(d.scoreCall('sports', 'pass', 'draw', null).state, 'push');
  assert.equal(d.scoreCall('sports', 'home', null, null).state, 'unresolved');
  assert.equal(d.scoreCall('sports', 'long', 'home', null).state, 'invalid');
  assert.equal(d.scoreCall('sports', 'home', 'home', 'long').beat_agent, false);
});

test('a football call carries the score and no price; its round shows the match and no stance', () => {
  const round = { id: 9, day: '2026-08-08', idx: 5, kind: 'sports', symbol: 'FD:441', meta: JSON.stringify(MATCH) };
  const at = new Date('2026-08-08T12:00:00Z');
  const view = d.publicRound(round, { revealAgent: true, now: at });
  assert.deepEqual(view.options, ['home', 'draw', 'away', 'pass']);
  assert.equal(view.match.home_short, 'Arsenal');
  assert.equal(view.match.kickoff, '2026-08-08T19:00:00.000Z');
  assert.ok(!('agent_direction' in view));
  const settled = { pick: 'away', entry_price: null, settle_price: 0, settle_price_b: 2, resolves_at: '2026-08-08T21:30:00.000Z' };
  const pub = d.publicPick(settled, { now: new Date('2026-08-08T22:00:00Z'), round });
  assert.strictEqual(pub.entry_price, null, 'no entry price is reported, not a zero');
  assert.equal(pub.outcome, 'away');
  assert.deepEqual(pub.score, { home: 0, away: 2 });
  assert.ok(!('move_pct' in pub), 'a score is not a move');
  const pending = d.publicPick({ ...settled, settle_price: null, settle_price_b: null }, { now: new Date('2026-08-08T20:00:00Z'), round });
  assert.equal(pending.pending, true);
  assert.ok(!('score' in pending));
  const [e] = d.scoreEntries([round], [{ round_id: 9, ...settled }]);
  assert.equal(e.kind, 'sports');
  assert.equal(e.state, 'correct');
  assert.strictEqual(e.entry_price, null);
  assert.strictEqual(e.move_pct, null);
  assert.deepEqual(e.score, { home: 0, away: 2 });
  assert.equal(e.match.home, 'Arsenal FC');
  // Half a score settles nothing.
  const [half] = d.scoreEntries([round], [{ round_id: 9, pick: 'away', entry_price: null, settle_price: 1, settle_price_b: null }]);
  assert.equal(half.state, 'unresolved');
  assert.strictEqual(half.score, null);
});

test('a score is not a move, whatever price a football row happens to carry', () => {
  // placePick writes no entry for a match; a row that carries one anyway (an
  // older writer, a hand edit) must still not be scored as a move: the
  // guard is the kind, not the absence of the field.
  const round = { id: 9, day: '2026-08-08', idx: 5, kind: 'sports', symbol: 'FD:441', meta: JSON.stringify(MATCH) };
  const [e] = d.scoreEntries([round], [{ round_id: 9, pick: 'home', entry_price: 1, settle_price: 2, settle_price_b: 0 }]);
  assert.equal(e.state, 'correct');
  assert.deepEqual(e.score, { home: 2, away: 0 });
  assert.strictEqual(e.move_pct, null);
});
