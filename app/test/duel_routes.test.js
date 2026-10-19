'use strict';
/**
 * Daily Duel — the authed API, exercised in-process against the real router.
 *
 * The properties worth their own suite are the anti-cheat ones, because each is
 * invisible in a screenshot and each is fatal to the board's credibility if it
 * regresses: a call must be impossible to revise once made, the agent's stance
 * must be impossible to read before committing, and the entry must be snapped
 * for the caller at the moment they call rather than shared across the day.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(64);
delete process.env.DATABASE_URL;            // force db.js's in-memory shim

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher } = require('../lib/candles');
const sportsFeed = require('../lib/sports_feed');
const duelRouter = require('../routes/duel');
const duel = require('../lib/duel');
const { pool } = require('../db');

let PRICES = {
  BTCUSDT: { price: 60000, volume: 900 },
  ETHUSDT: { price: 3000, volume: 800 },
  SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 },
  XRPUSDT: { price: 0.5, volume: 500 },
  ARBUSDT: { price: 1.2, volume: 50 },
};

let server, base;

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        let json = null;
        try { json = raw ? JSON.parse(raw) : null; } catch (e) { /* non-JSON */ }
        resolve({ status: res.statusCode, body: json, raw });
      });
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

// Registration is IP rate-limited (auth.js RATE_LIMIT_MAX = 10), so the suite
// registers a fixed pool once and each test names the member it needs. Burning
// a registration per test would trip the limiter and turn real failures into a
// wall of 429s. Members that must stay pick-free are marked below.
const POOL_SIZE = 8;
const U = [];

// The football feed, answered by a stand-in: today's match kicks off three
// hours from now, and nothing has finished unless a test says so.
const KICKOFF = new Date(Date.now() + 3 * 3600000).toISOString();
let MATCH_BODY = { status: 'TIMED', score: { fullTime: { home: null, away: null } } };
function matchRows() {
  return { matches: [{ id: 7001, utcDate: KICKOFF, status: 'TIMED',
    homeTeam: { name: 'Arsenal FC', shortName: 'Arsenal' }, awayTeam: { name: 'Chelsea FC', shortName: 'Chelsea' },
    competition: { name: 'Premier League', code: 'PL' } }] };
}

test.before(async () => {
  setTickerFetcher(async () => PRICES);
  setCandleFetcher(async () => []);           // nothing settles unless a test says so
  process.env.FOOTBALL_DATA_TOKEN = 'test-token';
  sportsFeed.setSportsTransport(async (path) => (path.startsWith('/matches/') ? MATCH_BODY : matchRows()));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/duel', duelRouter);
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
  for (let i = 0; i < POOL_SIZE; i++) {
    const res = await req('POST', '/api/auth/register', {
      body: { email: `duel${i}@example.com`, password: 'longenough1' },
    });
    assert.ok(res.body && res.body.token, `register ${i} failed: ${res.raw}`);
    U.push(res.body.token);
  }
});

test.after(() => {
  setTickerFetcher(null);
  setCandleFetcher(null);
  sportsFeed.setSportsTransport(null);
  delete process.env.FOOTBALL_DATA_TOKEN;
  if (server) server.close();
});

// ── the card ────────────────────────────────────────────────────────────────

test('GET /today opens the day with a full card, open for the whole day', async () => {
  const res = await req('GET', '/api/duel/today', { token: U[0] });
  assert.equal(res.status, 200);
  // The fixture prices the five majors and one alt: three classic rounds, a
  // range round on the fourth major, a mover on the last two; and the feed
  // deals the day's match.
  assert.deepEqual(res.body.rounds.map((r) => r.kind), ['direction', 'direction', 'direction', 'range', 'mover', 'sports']);
  assert.equal(res.body.rounds.length, duel.buildRounds(duel.dayKey(new Date()), [], PRICES, { event_id: 1,
    home: 'a', away: 'b', kickoff: KICKOFF }).length);
  assert.deepEqual(res.body.sources, { sports: 'dealt' });
  assert.equal(res.body.day, duel.dayKey(new Date()));
  assert.equal(res.body.horizon_hours, duel.HORIZON_HOURS);
  for (const r of res.body.rounds) {
    assert.ok(r.id > 0);
    assert.ok(r.symbol);
    // The card is callable for its whole UTC day. A mid-day lock would close
    // the game for most of the hours anybody is awake.
    assert.equal(r.callable, true);
    assert.ok(!('entry_price' in r), 'the entry belongs to the call, not the card');
    assert.deepEqual(r.options, duel.picksFor(r.kind), 'the card says which words the round takes');
  }
  const range = res.body.rounds.find((r) => r.kind === 'range');
  assert.equal(range.threshold_pct, duel.RANGE_BAND_PCT.major);
});

test('the card is created once and shared — a second reader sees the same rounds', async () => {
  const first = await req('GET', '/api/duel/today', { token: U[0] });
  const second = await req('GET', '/api/duel/today', { token: U[1] });
  assert.deepEqual(
    first.body.rounds.map((r) => r.id),
    second.body.rounds.map((r) => r.id),
    'everyone plays the same card, so the board compares like with like');
});

test('concurrent first reads do not duplicate the day', async () => {
  // The unique (day, idx) key is what makes lazy creation safe without a lock.
  const all = await Promise.all(
    [U[0], U[1], U[2], U[3]].map((t) => req('GET', '/api/duel/today', { token: t })));
  const ids = new Set();
  for (const r of all) {
    assert.equal(r.status, 200);
    r.body.rounds.forEach((x) => ids.add(x.id));
  }
  assert.equal(ids.size, duel.buildRounds(duel.dayKey(new Date()), [], PRICES, { event_id: 1,
    home: 'a', away: 'b', kickoff: KICKOFF }).length, 'a race must not mint a second card');
});

// ── the anti-cheat: the agent's stance ──────────────────────────────────────

test('the agent stance is ABSENT from the card until the caller commits', async () => {
  // U[2] has never called anything.
  const res = await req('GET', '/api/duel/today', { token: U[2] });
  for (const r of res.body.rounds) {
    assert.ok(!('agent_direction' in r),
      'the stance must not ride the pre-pick payload in any form');
  }
  // Belt and braces on the wire itself: no amount of client-side hiding helps
  // if the value is sitting in the JSON.
  assert.equal(/agent_direction/.test(res.raw), false);
});

test('committing a call reveals the stance, and only for that round', async () => {
  const token = U[4];
  const card = await req('GET', '/api/duel/today', { token });
  const target = card.body.rounds[0];

  const picked = await req('POST', '/api/duel/pick', {
    token, body: { round_id: target.id, pick: 'long' },
  });
  assert.equal(picked.status, 200);
  assert.ok('agent_direction' in picked.body, 'the stance is owed once you have committed');

  const after = await req('GET', '/api/duel/today', { token });
  const done = after.body.rounds.find((r) => r.id === target.id);
  const untouched = after.body.rounds.find((r) => r.id !== target.id);
  assert.ok('agent_direction' in done);
  assert.equal(done.my_call.pick, 'long');
  assert.ok(!('agent_direction' in untouched),
    'calling one round must not unlock the rest of the card');
});

// ── the entry belongs to the call ───────────────────────────────────────────

test('the entry is snapped for the caller at the moment they call', async () => {
  const token = U[4];                       // already called round 0 at 60000
  const card = await req('GET', '/api/duel/today', { token });
  const first = card.body.rounds.find((r) => r.my_call);
  assert.equal(first.my_call.entry_price, 60000);

  // The market moves, then a second player calls the SAME round.
  PRICES = { ...PRICES, BTCUSDT: { price: 66000, volume: 900 } };
  const other = U[5];
  const res = await req('POST', '/api/duel/pick', {
    token: other, body: { round_id: first.id, pick: 'short' },
  });
  assert.equal(res.status, 200);
  assert.equal(res.body.entry_price, 66000,
    'a later caller is scored from the price they were actually offered');
  PRICES = { ...PRICES, BTCUSDT: { price: 60000, volume: 900 } };
});

test('the horizon runs from the call, not from the card', async () => {
  const token = U[6];
  const card = await req('GET', '/api/duel/today', { token });
  const before = Date.now();
  const res = await req('POST', '/api/duel/pick', {
    token, body: { round_id: card.body.rounds[1].id, pick: 'pass' },
  });
  assert.equal(res.status, 200);
  const horizon = Date.parse(res.body.resolves_at);
  const expected = before + duel.HORIZON_HOURS * 3600000;
  assert.ok(Math.abs(horizon - expected) < 10000,
    'the window must start when the player committed');
});

test('an unreadable price refuses the call rather than inventing an entry', async () => {
  const token = U[7];
  const card = await req('GET', '/api/duel/today', { token });
  const id = card.body.rounds[0].id;
  const saved = PRICES;
  PRICES = {};                              // the feed goes dark
  const res = await req('POST', '/api/duel/pick', { token, body: { round_id: id, pick: 'long' } });
  PRICES = saved;
  assert.equal(res.status, 503);
  assert.equal(res.body.reason, 'prices_unreadable');
  // And nothing was recorded, so the player can try again.
  const retry = await req('POST', '/api/duel/pick', { token, body: { round_id: id, pick: 'long' } });
  assert.equal(retry.status, 200, 'a refused call must not silently occupy the slot');
});

// ── the anti-cheat: write-once ──────────────────────────────────────────────

test('a call cannot be revised once made', async () => {
  const token = U[7];                       // already called round 0 above
  const card = await req('GET', '/api/duel/today', { token });
  const id = card.body.rounds[0].id;

  const second = await req('POST', '/api/duel/pick', { token, body: { round_id: id, pick: 'short' } });
  assert.equal(second.status, 409, 'the second call must be refused, not silently applied');

  const after = await req('GET', '/api/duel/today', { token });
  assert.equal(after.body.rounds.find((r) => r.id === id).my_call.pick, 'long',
    'the original call must survive the attempt to change it');
});

test('two players calling the same round do not collide', async () => {
  // U[4] and U[5] both called round 0 above, from different entries.
  const a = await req('GET', '/api/duel/today', { token: U[4] });
  const b = await req('GET', '/api/duel/today', { token: U[5] });
  const ra = a.body.rounds.find((r) => r.my_call);
  const rb = b.body.rounds.find((r) => r.my_call);
  assert.equal(ra.id, rb.id, 'the same round');
  assert.equal(ra.my_call.pick, 'long');
  assert.equal(rb.my_call.pick, 'short');
  assert.notEqual(ra.my_call.entry_price, rb.my_call.entry_price,
    'each call keeps its own entry');
});

// ── refusals ────────────────────────────────────────────────────────────────

test('a call on a round outside today\'s card is refused', async () => {
  // Yesterday's round exists in the table but is not on the card any more.
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  await pool.execute(
    'INSERT IGNORE INTO duel_rounds (day, idx, symbol, agent_direction, signal_key)'
    + ' VALUES (?, ?, ?, ?, ?)', [yesterday, 0, 'BTCUSDT', 'long', null]);
  const [rows] = await pool.execute(
    'SELECT id, day, idx, symbol, agent_direction, signal_key FROM duel_rounds WHERE day = ? ORDER BY idx',
    [yesterday]);
  assert.ok(rows.length, 'fixture round exists');

  const res = await req('POST', '/api/duel/pick', {
    token: U[6], body: { round_id: rows[0].id, pick: 'long' },
  });
  assert.equal(res.status, 404);
});

test('a call on a round that does not exist is refused', async () => {
  const res = await req('POST', '/api/duel/pick', {
    token: U[6], body: { round_id: 999999, pick: 'long' },
  });
  assert.equal(res.status, 404);
});

test('an unreadable call is rejected rather than coerced to a direction', async () => {
  const card = await req('GET', '/api/duel/today', { token: U[6] });
  const id = card.body.rounds[0].id;
  for (const bad of ['maybe', '', null, 'up', 42]) {
    const res = await req('POST', '/api/duel/pick', { token: U[6], body: { round_id: id, pick: bad } });
    assert.equal(res.status, 400, `"${String(bad)}" must not become a call`);
  }
});

test('every verb needs a session', async () => {
  for (const [m, p] of [['GET', '/api/duel/today'], ['GET', '/api/duel/me'],
    ['GET', '/api/duel/season'], ['POST', '/api/duel/pick']]) {
    const res = await req(m, p, { body: m === 'POST' ? { round_id: 1, pick: 'long' } : undefined });
    assert.equal(res.status, 401, `${m} ${p} must require a token`);
  }
});

// ── the record ──────────────────────────────────────────────────────────────

test('a player who has called nothing has NO accuracy, not a 0% record', async () => {
  const res = await req('GET', '/api/duel/me', { token: U[3] });   // never called
  assert.equal(res.status, 200);
  assert.strictEqual(res.body.accuracy.pct, null,
    'a fresh player must not be shown a measured failure');
  assert.equal(res.body.marks.marks, 0);
  assert.equal(res.body.marks.pending, 0);
  assert.equal(res.body.streak.current, 0);
  assert.equal(res.body.history.length, 0);
});

test('an unsettled call is pending, and says so, rather than scoring as a miss', async () => {
  const me = await req('GET', '/api/duel/me', { token: U[4] });
  assert.equal(me.body.marks.pending, 1, 'an unsettled call is pending, not lost');
  assert.strictEqual(me.body.accuracy.pct, null);
  assert.equal(me.body.accuracy.unresolved, 1);
  assert.equal(me.body.accuracy.wrong, 0);
  assert.equal(me.body.streak.current, 1, 'showing up counts even before anything resolves');
});

test('the record states the window it covers rather than truncating in silence', async () => {
  const res = await req('GET', '/api/duel/me', { token: U[4] });
  assert.equal(res.body.window_days, duelRouter.RECORD_WINDOW_DAYS);
});

test('a call carries its sealed receipt', async () => {
  const res = await req('GET', '/api/duel/me', { token: U[4] });
  const card = await req('GET', '/api/duel/today', { token: U[4] });
  const mine = card.body.rounds.find((r) => r.my_call);
  assert.match(mine.my_call.seal, /^[0-9a-f]{64}$/, 'the call is committed to before the market answers');
  assert.equal(res.body.accuracy.resolved, 0);
});

test('the season standing is scoped to the calendar month', async () => {
  const res = await req('GET', '/api/duel/season', { token: U[4] });
  assert.equal(res.status, 200);
  assert.equal(res.body.season, new Date().toISOString().slice(0, 7));
  assert.strictEqual(res.body.accuracy.pct, null);
  assert.equal(res.body.marks.pending, 1);
});

// ── settlement through the route ────────────────────────────────────────────

/** The id behind a pooled token, read the way the app does. */
async function userIdFor(email) {
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
  assert.ok(rows[0], `no user for ${email}`);
  return rows[0].id;
}

test('a due call settles from the candle covering its own horizon', async () => {
  // U[2] has no calls, so plant one whose horizon has already passed — the
  // state the settler exists to handle.
  const token = U[2];
  const uid = await userIdFor('duel2@example.com');
  const card = await req('GET', '/api/duel/today', { token });
  const round = card.body.rounds[2];
  const resolvesAt = new Date(Date.now() - 3600000).toISOString();
  await pool.execute(
    'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
    + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [uid, round.id, 'pass', 100, resolvesAt, 'x'.repeat(64), '{}',
      new Date(Date.now() - 90000000).toISOString()]);

  // The market barely moved over that window, so declining it was the read.
  setCandleFetcher(async () => [[
    String(Math.floor(Date.parse(resolvesAt) / 3600000) * 3600000),
    '1', '2', '0', '100.01', '1', '1',
  ]]);
  const me = await req('GET', '/api/duel/me', { token });
  setCandleFetcher(async () => []);

  const scored = me.body.history.find((h) => h.pick === 'pass');
  assert.ok(scored, 'the settled call appears in the record');
  assert.equal(scored.outcome, 'flat');
  assert.equal(scored.state, 'correct', 'declining a market that went nowhere is a correct read');
  assert.equal(me.body.marks.marks, 1);
  assert.equal(me.body.marks.pending, 0);
  // PASS on a flat market is a correct call, so it does count — unlike a PASS
  // on a market that moved, which is a push and counts for neither side.
  assert.strictEqual(me.body.accuracy.pct, 100);
  assert.equal(me.body.accuracy.correct, 1);
  assert.equal(me.body.accuracy.scored, 1);
});

test('a due call with no candle stays unresolved rather than settling at a guess', async () => {
  const token = U[2];
  const uid = await userIdFor('duel2@example.com');
  const card = await req('GET', '/api/duel/today', { token });
  const round = card.body.rounds[1];
  await pool.execute(
    'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
    + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [uid, round.id, 'long', 100, new Date(Date.now() - 3600000).toISOString(),
      'y'.repeat(64), '{}', new Date(Date.now() - 90000000).toISOString()]);

  setCandleFetcher(async () => { throw new Error('upstream down'); });
  const me = await req('GET', '/api/duel/me', { token });
  setCandleFetcher(async () => []);

  const stuck = me.body.history.find((h) => h.pick === 'long');
  assert.equal(stuck.state, 'unresolved');
  assert.strictEqual(stuck.outcome, null);
  assert.equal(me.body.accuracy.unresolved, 1);
  assert.equal(me.body.accuracy.wrong, 0, 'an upstream outage is not a losing call');
  // The player already had one correct call. A round nobody could price must
  // leave that record exactly where it was — not dilute it towards 50%.
  assert.strictEqual(me.body.accuracy.pct, 100);
  assert.equal(me.body.accuracy.scored, 1);
  assert.equal(me.body.marks.pending, 1, 'and it is reported as still owed');
});

// ── §4 ──────────────────────────────────────────────────────────────────────

test('no amount of any currency rides the duel payloads', async () => {
  const card = await req('GET', '/api/duel/today', { token: U[4] });
  const me = await req('GET', '/api/duel/me', { token: U[4] });
  const season = await req('GET', '/api/duel/season', { token: U[4] });
  for (const raw of [card.raw, me.raw, season.raw]) {
    for (const forbidden of ['balance', 'equity', 'margin', 'pnl_usd', 'notional',
      'usd_value', 'stake', 'wager', 'vusdt']) {
      assert.equal(raw.toLowerCase().includes(forbidden), false,
        `"${forbidden}" must not appear on a duel payload`);
    }
  }
  // Market prices ARE public facts, and the entry the player was offered is
  // one of them.
  assert.ok(me.raw.includes('entry_price'));
});

// ── the categories ──────────────────────────────────────────────────────────

test('a range call is recorded with the round\'s own band, and a classic word on it is refused by name', async () => {
  const token = U[7];
  const card = await req('GET', '/api/duel/today', { token });
  const range = card.body.rounds.find((r) => r.kind === 'range');
  assert.equal(range.symbol, 'BNBUSDT');
  assert.equal(range.threshold_pct, duel.RANGE_BAND_PCT.major);
  assert.ok(!('agent_direction' in range), 'a range round holds no stance to hide');
  const wrong = await req('POST', '/api/duel/pick', { token, body: { round_id: range.id, pick: 'long' } });
  assert.equal(wrong.status, 400);
  assert.equal(wrong.body.error, 'Call BREAK, HOLD or PASS.');
  const res = await req('POST', '/api/duel/pick', { token, body: { round_id: range.id, pick: 'HOLD' } });
  assert.equal(res.status, 200, res.raw);
  assert.equal(res.body.kind, 'range');
  assert.equal(res.body.pick, 'hold');
  assert.equal(res.body.threshold_pct, duel.RANGE_BAND_PCT.major);
  assert.equal(res.body.entry_price, 600);
  assert.ok(!('agent_direction' in res.body), 'nothing to reveal on a range round');
  const after = await req('GET', '/api/duel/today', { token });
  const mine = after.body.rounds.find((r) => r.id === range.id);
  assert.equal(mine.my_call.pick, 'hold');
  assert.equal(mine.my_call.pending, true);
  assert.ok(!('agent_direction' in mine));
});

test('a mover call snaps BOTH entries at once and seals both; one unreadable price refuses it outright', async () => {
  const token = U[7];
  const card = await req('GET', '/api/duel/today', { token });
  const mover = card.body.rounds.find((r) => r.kind === 'mover');
  assert.equal(mover.symbol, 'XRPUSDT');
  assert.equal(mover.symbol_b, 'ARBUSDT');
  assert.deepEqual(mover.options, ['first', 'second', 'pass']);
  // The second leg's price is gone: no call, and the sentence names the leg.
  const savedB = PRICES.ARBUSDT;
  PRICES = { ...PRICES, ARBUSDT: { price: null, volume: 50 } };
  const refused = await req('POST', '/api/duel/pick', { token, body: { round_id: mover.id, pick: 'first' } });
  PRICES = { ...PRICES, ARBUSDT: savedB };
  assert.equal(refused.status, 503);
  assert.match(refused.body.error, /ARBUSDT/);
  const res = await req('POST', '/api/duel/pick', { token, body: { round_id: mover.id, pick: 'second' } });
  assert.equal(res.status, 200, res.raw);
  assert.equal(res.body.kind, 'mover');
  assert.equal(res.body.entry_price, 0.5);
  assert.equal(res.body.entry_price_b, 1.2);
  assert.equal(res.body.symbol_b, 'ARBUSDT');
  // The receipt commits to both prices the call was made at.
  const uid = await userIdFor('duel7@example.com');
  const [rows] = await pool.execute(
    'SELECT id, round_id, pick, entry_price, entry_price_b, seal, seal_payload FROM duel_picks WHERE user_id = ?', [uid]);
  const stored = rows.find((r) => Number(r.round_id) === Number(mover.id));
  assert.equal(stored.entry_price_b, 1.2);
  const payload = JSON.parse(stored.seal_payload);
  assert.equal(payload.category, 'mover');
  assert.equal(payload.symbol_b, 'ARBUSDT');
  assert.equal(payload.entry_price_b, 1.2);
  assert.equal(payload.entry_price, 0.5);
  const after = await req('GET', '/api/duel/today', { token });
  const mine = after.body.rounds.find((r) => r.id === mover.id);
  assert.equal(mine.my_call.entry_price_b, 1.2);
  assert.equal(mine.my_call.pending, true);
});

test('a mover settles only when BOTH closes can be read, and scores the bigger move', async () => {
  const token = U[5];
  const uid = await userIdFor('duel5@example.com');
  const card = await req('GET', '/api/duel/today', { token });
  const mover = card.body.rounds.find((r) => r.kind === 'mover');
  const resolvesAt = new Date(Date.now() - 3600000).toISOString();
  await pool.execute(
    'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, entry_price_b, resolves_at,'
    + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [uid, mover.id, 'second', 100, 50, resolvesAt, 'm'.repeat(64), '{}',
      new Date(Date.now() - 90000000).toISOString()]);
  const bucket = String(Math.floor(Date.parse(resolvesAt) / 3600000) * 3600000);
  // Only the first leg has a candle: nothing settles, nothing is guessed.
  setCandleFetcher(async (symbol) => (symbol === 'XRPUSDT' ? [[bucket, '1', '2', '0', '101', '1', '1']] : []));
  let me = await req('GET', '/api/duel/me', { token });
  let mine = me.body.history.find((h) => h.round_id === mover.id);
  assert.equal(mine.state, 'unresolved');
  assert.strictEqual(mine.outcome, null);
  assert.equal(me.body.accuracy.scored, 0, 'half a comparison must not score');
  // Both legs: XRP +1%, ARB +10% — the second moved more.
  setCandleFetcher(async (symbol) => [[bucket, '1', '2', '0', symbol === 'XRPUSDT' ? '101' : '55', '1', '1']]);
  me = await req('GET', '/api/duel/me', { token });
  setCandleFetcher(async () => []);
  mine = me.body.history.find((h) => h.round_id === mover.id);
  assert.equal(mine.outcome, 'second');
  assert.equal(mine.state, 'correct');
  assert.equal(mine.kind, 'mover');
  assert.equal(mine.move_pct_b, 10);
  assert.equal(mine.symbol_b, 'ARBUSDT');
  assert.equal(me.body.accuracy.correct, 1);
});

test('a word no round takes is refused before any round is read', async () => {
  const res = await req('POST', '/api/duel/pick', { token: U[6], body: { round_id: 999999, pick: 'sideways' } });
  assert.equal(res.status, 400);
  assert.match(res.body.error, /not a call/);
});

test('a football call is recorded with no entry, resolves at full time, and settles on the final score', async () => {
  const token = U[4];
  const uid = await userIdFor('duel4@example.com');
  const card = await req('GET', '/api/duel/today', { token });
  const match = card.body.rounds.find((r) => r.kind === 'sports');
  assert.equal(match.symbol, 'FD:7001');
  assert.deepEqual(match.options, ['home', 'draw', 'away', 'pass']);
  assert.equal(match.match.home_short, 'Arsenal');
  assert.equal(match.match.kickoff, KICKOFF);
  assert.equal(match.callable, true);
  const wrong = await req('POST', '/api/duel/pick', { token, body: { round_id: match.id, pick: 'long' } });
  assert.equal(wrong.status, 400);
  assert.equal(wrong.body.error, 'Call HOME, DRAW, AWAY or PASS.');
  const res = await req('POST', '/api/duel/pick', { token, body: { round_id: match.id, pick: 'home' } });
  assert.equal(res.status, 200, res.raw);
  assert.equal(res.body.kind, 'sports');
  assert.strictEqual(res.body.entry_price, null, 'a match has no entry price');
  assert.equal(res.body.resolves_at, new Date(Date.parse(KICKOFF) + duel.SPORTS_FULL_TIME_MS).toISOString());
  assert.equal(res.body.match.away, 'Chelsea FC');
  assert.ok(!('agent_direction' in res.body));
  const [rows] = await pool.execute(
    'SELECT id, round_id, entry_price, seal_payload FROM duel_picks WHERE user_id = ?', [uid]);
  const stored = rows.find((r) => Number(r.round_id) === Number(match.id));
  assert.strictEqual(stored.entry_price, null);
  const payload = JSON.parse(stored.seal_payload);
  assert.equal(payload.category, 'sports');
  assert.equal(payload.symbol, 'FD:7001');
  assert.strictEqual(payload.entry_price, null);
  // Not finished at the horizon: nothing settles, nothing is guessed.
  await pool.execute('UPDATE duel_picks SET settle_price = ?, settle_price_b = ?, settle_state = ?, settled_at = ? WHERE id = ?',
    [null, null, null, null, stored.id]);
  const picksRows = (await pool.execute('SELECT id FROM duel_picks WHERE user_id = ?', [uid]))[0];
  assert.ok(picksRows.length >= 1);
  // Pull the horizon into the past so it is due.
  const mem = pool.duelPicks.find((x) => Number(x.id) === Number(stored.id));
  mem.resolves_at = new Date(Date.now() - 60000).toISOString();
  MATCH_BODY = { status: 'IN_PLAY', score: { fullTime: { home: 1, away: 0 } } };
  let me = await req('GET', '/api/duel/me', { token });
  let mine = me.body.history.find((h) => h.round_id === match.id);
  assert.equal(mine.state, 'unresolved');
  assert.strictEqual(mine.score, null);
  // Finished: the final score settles it, and the call reports the score.
  MATCH_BODY = { status: 'FINISHED', score: { fullTime: { home: 2, away: 1 } } };
  sportsFeed._resetCache();
  me = await req('GET', '/api/duel/me', { token });
  mine = me.body.history.find((h) => h.round_id === match.id);
  assert.equal(mine.state, 'correct');
  assert.deepEqual(mine.score, { home: 2, away: 1 });
  assert.strictEqual(mine.entry_price, null);
  assert.strictEqual(mine.move_pct, null);
  const after = await req('GET', '/api/duel/today', { token });
  const shown = after.body.rounds.find((r) => r.id === match.id);
  assert.deepEqual(shown.my_call.score, { home: 2, away: 1 });
  assert.equal(shown.my_result.state, 'correct');
  MATCH_BODY = { status: 'TIMED', score: { fullTime: { home: null, away: null } } };
});

test('a match that kicked off is closed to calls', async () => {
  const token = U[5];
  const card = await req('GET', '/api/duel/today', { token });
  const match = card.body.rounds.find((r) => r.kind === 'sports');
  const mem = pool.duelRounds.find((x) => Number(x.id) === Number(match.id));
  const saved = mem.meta;
  mem.meta = JSON.stringify({ ...JSON.parse(saved), kickoff: new Date(Date.now() - 60000).toISOString() });
  try {
    const closed = await req('GET', '/api/duel/today', { token });
    assert.equal(closed.body.rounds.find((r) => r.id === match.id).callable, false);
    const res = await req('POST', '/api/duel/pick', { token, body: { round_id: match.id, pick: 'draw' } });
    assert.equal(res.status, 409);
    assert.match(res.body.error, /closed/);
  } finally {
    mem.meta = saved;
  }
});

test('the card says what the feed said: dealt, none, unreadable, off, unknown', async () => {
  const svc = require('../lib/duel_service');
  svc._resetSportsState();
  const noon = (day) => new Date(day + 'T08:00:00Z');
  try {
    // Unreadable: the market rounds stand, the match is missing, and it is SAID.
    sportsFeed.setSportsTransport(async () => { throw new Error('ECONNRESET'); });
    let rounds = await svc.ensureRounds('2031-01-05', noon('2031-01-05'));
    assert.ok(rounds.length >= 5, 'an unreadable feed must not cancel the day');
    assert.ok(!rounds.some((r) => duel.kindOf(r) === 'sports'));
    assert.equal(svc.sportsStateFor('2031-01-05', rounds), 'unreadable');
    // Readable, nothing on: the other sentence.
    sportsFeed.setSportsTransport(async () => ({ matches: [] }));
    rounds = await svc.ensureRounds('2031-01-06', noon('2031-01-06'));
    assert.ok(!rounds.some((r) => duel.kindOf(r) === 'sports'));
    assert.equal(svc.sportsStateFor('2031-01-06', rounds), 'none');
    // Dealt: the rounds answer for themselves, whatever this process remembers.
    sportsFeed.setSportsTransport(async () => ({ matches: [{ id: 7002, utcDate: '2031-01-07T19:00:00Z', status: 'TIMED',
      homeTeam: { name: 'Inter' }, awayTeam: { name: 'Milan' }, competition: { name: 'Serie A', code: 'SA' } }] }));
    rounds = await svc.ensureRounds('2031-01-07', noon('2031-01-07'));
    assert.ok(rounds.some((r) => duel.kindOf(r) === 'sports'));
    assert.equal(svc.sportsStateFor('2031-01-07', rounds), 'dealt');
    svc._resetSportsState();
    assert.equal(svc.sportsStateFor('2031-01-07', rounds), 'dealt');
    // A day this process did not deal: unknown, not none.
    assert.equal(svc.sportsStateFor('2031-01-08', []), 'unknown');
    // No token: off, and the feed is never asked.
    delete process.env.FOOTBALL_DATA_TOKEN;
    let asked = 0;
    sportsFeed.setSportsTransport(async () => { asked++; return { matches: [] }; });
    rounds = await svc.ensureRounds('2031-01-09', noon('2031-01-09'));
    assert.equal(asked, 0);
    assert.equal(svc.sportsStateFor('2031-01-09', rounds), 'off');
  } finally {
    process.env.FOOTBALL_DATA_TOKEN = 'test-token';
    sportsFeed.setSportsTransport(async (path) => (path.startsWith('/matches/') ? MATCH_BODY : matchRows()));
    svc._resetSportsState();
  }
});
