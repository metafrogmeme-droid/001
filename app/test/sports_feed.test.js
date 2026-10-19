'use strict';
/**
 * The Daily Duel's football feed (lib/sports_feed.js), driven without the network.
 *
 * What is dealt is a choice, and the choice is pinned: a match not yet
 * started, kicking off on the day with time to call it, the best competition
 * first, then the earliest kick-off. What is settled is a FINISHED match's
 * full-time score and nothing else: an in-play score, a missing score, a
 * cancelled match all answer null, because a result invented from any of
 * them would score a call against a match that was not over.
 *
 * And the two absences are kept apart: a feed that cannot be read THROWS,
 * a feed that read fine and had no match answers null. The service turns
 * those into "unreadable" and "none", which are different sentences.
 */
const test = require('node:test');
const assert = require('node:assert');
const feed = require('../lib/sports_feed');

const DAY = '2026-10-19';
const NOW = new Date('2026-10-19T10:00:00Z');
const row = (id, utc, code, home, away, status = 'TIMED') => ({
  id, utcDate: utc, status,
  homeTeam: { name: home + ' FC', shortName: home }, awayTeam: { name: away + ' FC', shortName: away },
  competition: { name: code + ' league', code },
});

test.afterEach(() => feed.setSportsTransport(null));

test('the day\'s match: not started, on the day, with time to call, best competition then earliest', () => {
  const rows = [
    row(1, '2026-10-19T19:00:00Z', 'PL', 'Arsenal', 'Chelsea'),
    row(2, '2026-10-19T10:30:00Z', 'CL', 'A', 'B'),             // too soon: 30 min away
    row(3, '2026-10-19T17:00:00Z', 'PD', 'Real', 'Barça'),
    row(4, '2026-10-20T19:00:00Z', 'CL', 'C', 'D'),             // tomorrow
    row(5, '2026-10-19T15:00:00Z', 'PL', 'Spurs', 'Villa', 'IN_PLAY'),
    row(6, '2026-10-19T20:00:00Z', 'PL', 'Everton', 'Fulham'),
  ];
  const m = feed.pickCandidate(rows, DAY, NOW);
  assert.equal(m.event_id, '1', 'the Premier League outranks La Liga; the earlier PL kick-off wins');
  assert.equal(m.home, 'Arsenal FC');
  assert.equal(m.home_short, 'Arsenal');
  assert.equal(m.away_short, 'Chelsea');
  assert.equal(m.competition, 'PL league');
  assert.equal(m.kickoff, '2026-10-19T19:00:00.000Z');
  // A Champions League match on the day, with time, beats every PL one.
  const cl = feed.pickCandidate(rows.concat([row(7, '2026-10-19T21:00:00Z', 'CL', 'E', 'F')]), DAY, NOW);
  assert.equal(cl.event_id, '7');
  // Nothing qualifying is null, not a guess.
  assert.strictEqual(feed.pickCandidate([row(2, '2026-10-19T10:30:00Z', 'CL', 'A', 'B')], DAY, NOW), null);
  assert.strictEqual(feed.pickCandidate([], DAY, NOW), null);
  assert.strictEqual(feed.pickCandidate(null, DAY, NOW), null);
  // A row without both teams, an id or a kick-off is not a match.
  assert.strictEqual(feed.pickCandidate([{ id: 9, utcDate: '2026-10-19T19:00:00Z', status: 'TIMED',
    homeTeam: { name: 'X' }, competition: { code: 'PL' } }], DAY, NOW), null);
});

test('a match an unlisted competition is dealt only when nothing preferred qualifies', () => {
  const rows = [row(1, '2026-10-19T12:00:00Z', 'XYZ', 'A', 'B'), row(2, '2026-10-19T20:00:00Z', 'SA', 'Inter', 'Milan')];
  assert.equal(feed.pickCandidate(rows, DAY, NOW).event_id, '2');
  assert.equal(feed.pickCandidate([rows[0]], DAY, NOW).event_id, '1');
});

test('a result is a FINISHED match\'s full-time score, as two numbers, and nothing else', () => {
  assert.deepEqual(feed.resultOf({ status: 'FINISHED', score: { fullTime: { home: 2, away: 1 } } }), { home: 2, away: 1 });
  assert.deepEqual(feed.resultOf({ status: 'FINISHED', score: { fullTime: { home: 0, away: 0 } } }), { home: 0, away: 0 },
    'a goalless draw is a score, not an absence');
  for (const bad of [
    { status: 'IN_PLAY', score: { fullTime: { home: 2, away: 1 } } },
    { status: 'PAUSED', score: { fullTime: { home: 1, away: 0 } } },
    { status: 'POSTPONED' }, { status: 'CANCELLED', score: { fullTime: { home: null, away: null } } },
    { status: 'FINISHED', score: { fullTime: { home: null, away: 1 } } },
    { status: 'FINISHED', score: { fullTime: { home: 'two', away: 1 } } },
    { status: 'FINISHED', score: { fullTime: { home: -1, away: 1 } } },
    { status: 'FINISHED' }, null, undefined,
  ]) assert.strictEqual(feed.resultOf(bad), null, JSON.stringify(bad));
});

test('candidateFor reads the day, and a body without a matches list is unreadable, not empty', async () => {
  const asked = [];
  feed.setSportsTransport(async (path) => {
    asked.push(path);
    return { matches: [row(1, '2026-10-19T19:00:00Z', 'PL', 'Arsenal', 'Chelsea')] };
  });
  const m = await feed.candidateFor(DAY, NOW);
  assert.equal(m.event_id, '1');
  assert.deepEqual(asked, ['/matches?dateFrom=2026-10-19&dateTo=2026-10-19']);
  feed.setSportsTransport(async () => ({ message: 'Your API token is invalid.', errorCode: 400 }));
  await assert.rejects(() => feed.candidateFor(DAY, NOW), /without a matches list/);
  feed.setSportsTransport(async () => { throw new Error('ECONNRESET'); });
  await assert.rejects(() => feed.candidateFor(DAY, NOW), /ECONNRESET/);
  feed.setSportsTransport(async () => ({ matches: [] }));
  assert.strictEqual(await feed.candidateFor(DAY, NOW), null);
});

test('finalScore asks for the match by its event id, with or without the round\'s prefix', async () => {
  const asked = [];
  feed.setSportsTransport(async (path) => { asked.push(path); return { status: 'FINISHED', score: { fullTime: { home: 3, away: 3 } } }; });
  assert.deepEqual(await feed.finalScore('FD:441'), { home: 3, away: 3 });
  assert.deepEqual(await feed.finalScore(441), { home: 3, away: 3 });
  assert.deepEqual(asked, ['/matches/441'], 'the second read came from the cache');
  assert.strictEqual(await feed.finalScore('not-an-id'), null);
  assert.strictEqual(await feed.finalScore(null), null);
  feed.setSportsTransport(async () => { throw new Error('Timeout'); });
  await assert.rejects(() => feed.finalScore('FD:442'), /Timeout/);
});

test('the feed is configured by its token alone, and the token is never in a request path', async () => {
  assert.equal(feed.isConfigured({}), false);
  assert.equal(feed.isConfigured({ FOOTBALL_DATA_TOKEN: '  ' }), false);
  assert.equal(feed.isConfigured({ FOOTBALL_DATA_TOKEN: 'abc' }), true);
  const asked = [];
  feed.setSportsTransport(async (path) => { asked.push(path); return { matches: [] }; });
  await feed.candidateFor(DAY, NOW);
  assert.ok(asked.every((p) => !/token|abc/i.test(p)));
});
