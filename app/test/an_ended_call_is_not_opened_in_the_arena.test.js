'use strict';
// A call that has ended is not opened in the Arena, at any of its three doors.
//
// The bot walks every published signal on hourly candles and re-sends it with
// the word for what became of it (bot/core/signal_outcomes.py), so a signal
// row can read TARGET or STOP forty minutes after it was posted. None of the
// Arena's doors read that word. Driven on the real routes: the picker marked
// a stopped-out call "tradeable", /open-signal filled it at the live mark with
// its passed stop dropped (a stop-out re-opened as a position with no stop),
// and practice-follow mirrored it. Practice-follow is lazy -- it runs when the
// follower next reads their account -- and had no age rule at all, so a
// follower back after two days had a two-day-old call opened; and a read that
// found no fillable marks skipped every new call as `no_mark` and moved the
// cursor past it for good.
//
// `callBlock` is the one reading of "is this call still current", and each
// door is driven here rather than scanned: a SELECT that forgets the status
// column reads every row as NEW, which no scan of the planner can see.
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const st = require('../lib/arena_signal_trade');
const { planFollows } = require('../lib/arena_follow');
const SS = require('../public/js/signal-status-model.js');
const { SIGNAL_STATUSES } = require('../routes/signals');
const i18n = require('../public/js/i18n.js');
const { codeOnly } = require('./helpers/code_only');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const NOW = new Date('2026-09-28T12:00:00Z');
const ago = (ms) => new Date(NOW.getTime() - ms);
const sig = (over) => Object.assign({
  id: 7, symbol: 'BTCUSDT', direction: 'LONG', status: 'NEW',
  entry_price: 60000, stop_loss: 58000, take_profit: 66000, created_at: ago(10 * 60000),
}, over || {});
const plan = (over, ctx) => st.planSignalOpen(Object.assign({
  signal: sig(over), positions: [], balance: 10000,
  margin: 200, leverage: 2, mark: 60600, now: NOW,
}, ctx || {}));

// ── the reading ───────────────────────────────────────────────────────────

test('a pending call is current, and every final word ends it', () => {
  for (const w of SS.PENDING) assert.equal(st.callBlock(sig({ status: w }), NOW), null, w);
  for (const w of SS.TERMINAL) assert.equal(st.callBlock(sig({ status: w }), NOW), 'ended', w);
});

test('a row that states no word is NEW, which is the table default', () => {
  for (const s of [undefined, null, '', '   ']) {
    assert.equal(st.callBlock(sig({ status: s }), NOW), null, String(s));
  }
});

test('a word this server does not know is not a current call', () => {
  // The dashboard model reads an unknown word as FINAL; a newer bot's word
  // is more likely a new way of ending than a new way of staying open.
  assert.equal(st.callBlock(sig({ status: 'CLOSED_EARLY' }), NOW), 'ended');
  assert.equal(st.callBlock(sig({ status: ' stop ' }), NOW), 'ended', 'the word is read case- and space-insensitively');
});

test('the order is direction, then ended, then stale', () => {
  assert.equal(st.callBlock(sig({ direction: 'FLAT', status: 'STOP' }), NOW), 'direction');
  assert.equal(st.callBlock(sig({ status: 'STOP', created_at: ago(3 * 86400e3) }), NOW), 'ended',
    'an ended call is ended whatever its age');
  assert.equal(st.callBlock(sig({ created_at: ago(st.MAX_SIGNAL_AGE_MS + 1) }), NOW), 'stale');
  assert.equal(st.callBlock(sig({ created_at: ago(st.MAX_SIGNAL_AGE_MS) }), NOW), null,
    'the age limit itself is still open, as the open route has always had it');
});

// ── /open-signal's planner ───────────────────────────────────────────────

test('an ended call is refused by the open planner, naming what became of it', () => {
  const said = {
    TARGET: /reached its target/, STOP: /hit its stop/, EXPIRED: /not filled/,
    AMBIGUOUS: /both its target and its stop/, NO_EXIT: /neither exit/, UNSCORED: /could not be scored/,
  };
  for (const w of SS.TERMINAL) {
    const r = plan({ status: w });
    assert.equal(r.ok, false, w);
    assert.equal(r.code, 'ended', w);
    assert.match(r.error, said[w], w);
  }
  const unknown = plan({ status: 'CLOSED_EARLY' });
  assert.equal(unknown.code, 'ended');
  assert.match(unknown.error, /CLOSED_EARLY, a word this server does not know/);
});

test('a call whose entry was hit and has no exit yet still opens', () => {
  const r = plan({ status: 'OPEN' });
  assert.equal(r.ok, true, r.error);
  assert.equal(r.data.entry, 60600);
});

test('the picker marks an ended call as not openable, and says why', () => {
  const rows = st.decorateForPicker([
    sig({ id: 1 }), sig({ id: 2, symbol: 'ETHUSDT', status: 'STOP' }),
    sig({ id: 3, symbol: 'SOLUSDT', status: 'OPEN' }),
    sig({ id: 4, symbol: 'XRPUSDT', status: 'TARGET', created_at: ago(8 * 3600e3) }),
  ], { now: NOW });
  assert.deepEqual(rows.map((r) => [r.id, r.tradeable, r.blocked_reason]), [
    [1, true, null], [2, false, 'ended'], [3, true, null], [4, false, 'ended'],
  ]);
});

// ── practice-follow's planner ────────────────────────────────────────────

const MARKS = { BTCUSDT: { price: 100 }, ETHUSDT: { price: 50 }, SOLUSDT: { price: 20 } };
const PREFS = { margin: 200, leverage: 2 };
const follow = (signals, over) => planFollows(Object.assign({
  signals, positions: [], balance: 5000, prefs: PREFS, marks: MARKS, now: NOW,
}, over || {}));

test('practice-follow skips an ended call and a stale one, and moves past both', () => {
  const p = follow([
    sig({ id: 11, status: 'STOP' }),
    sig({ id: 12, symbol: 'ETHUSDT', created_at: ago(48 * 3600e3) }),
    sig({ id: 13, symbol: 'SOLUSDT' }),
  ]);
  assert.deepEqual(p.opens.map((o) => o.signal_id), [13]);
  assert.deepEqual(p.skips, [{ signal_id: 11, reason: 'ended' }, { signal_id: 12, reason: 'stale' }]);
  assert.equal(p.last_id, 13, 'a fact about the signal is final: the cursor moves past it');
});

test('practice-follow mirrors nothing, and holds its cursor, when the marks were not read', () => {
  const p = follow([sig({ id: 21 }), sig({ id: 22, symbol: 'ETHUSDT' })], { marks: {}, marksFresh: false });
  assert.deepEqual(p.opens, []);
  assert.deepEqual(p.skips, [], 'an unread map is a fact about this moment, not about any signal');
  assert.equal(p.last_id, 0);
  assert.equal(p.deferred, 'marks');
  // Even a map that happens to hold the symbol is not filled from when the
  // route said it is not fillable.
  assert.deepEqual(follow([sig({ id: 23 })], { marksFresh: false }).opens, []);
});

test('a fresh map that lacks a symbol is still a skip about that signal', () => {
  const p = follow([sig({ id: 31, symbol: 'NOPEUSDT' })], { marksFresh: true });
  assert.deepEqual(p.skips, [{ signal_id: 31, reason: 'no_mark' }]);
  assert.equal(p.last_id, 31);
  assert.equal(p.deferred, null);
});

test('the three doors give one answer about each call', () => {
  // One reading, three readers: the refusal code, the picker's reason and the
  // follow skip agree on every row of a corpus that crosses words and ages.
  const words = [...SS.PENDING, ...SS.TERMINAL, 'CLOSED_EARLY', undefined];
  const ages = [10 * 60000, st.MAX_SIGNAL_AGE_MS, st.MAX_SIGNAL_AGE_MS + 1, 3 * 86400e3];
  let id = 100;
  for (const w of words) {
    for (const a of ages) {
      for (const d of ['LONG', 'FLAT']) {
        const s = sig({ id: ++id, status: w, created_at: ago(a), direction: d });
        const expect = st.callBlock(s, NOW);
        const r = st.planSignalOpen({ signal: s, positions: [], balance: 10000,
          margin: 200, leverage: 2, mark: 60600, now: NOW });
        assert.equal(r.ok ? null : r.code, expect, `open ${w} ${a} ${d}`);
        assert.equal(st.decorateForPicker([s], { now: NOW })[0].blocked_reason, expect, `picker ${w} ${a} ${d}`);
        const f = follow([s], { marks: { BTCUSDT: { price: 100 } } });
        assert.equal(f.skips.length ? f.skips[0].reason : null, expect, `follow ${w} ${a} ${d}`);
      }
    }
  }
});

test('the follow planner has no second copy of the rule', () => {
  const src = codeOnly(read('lib', 'arena_follow.js'));
  assert.match(src, /require\('\.\/arena_signal_trade'\)/);
  assert.match(src, /callBlock\(s, now\)/);
  assert.doesNotMatch(src, /MAX_SIGNAL_AGE_MS|TERMINAL|'LONG'/,
    'practice-follow spells its own age, word or direction rule');
});

test('the server reads the outcome words off the one model', () => {
  assert.deepEqual(SIGNAL_STATUSES, [...SS.PENDING, ...SS.TERMINAL]);
  const src = codeOnly(read('routes', 'signals.js'));
  assert.doesNotMatch(src, /'NO_EXIT'|'UNSCORED'|'AMBIGUOUS'/, 'the route spells its own word list');
  assert.match(codeOnly(read('lib', 'arena_signal_trade.js')), /SignalStatus\.status\(s\)\.final/);
});

test('every Arena read of a signal selects the columns the reading needs', () => {
  // A SCAN, and it says so: the in-memory database hands back whole rows
  // whatever a SELECT names, so no drive here can see a dropped column -- and
  // a row read without `status` is NEW to the reading, which is the defect.
  const src = codeOnly(read('routes', 'arena.js'));
  const reads = src.match(/'SELECT [^']* FROM signals [^']*'/g) || [];
  const feeding = reads.filter((q) => /WHERE id = \?|WHERE signal_key = \?|WHERE id > \?|ORDER BY id DESC LIMIT 12/.test(q));
  assert.equal(feeding.length, 4, `the picker, the two open reads and the follow sweep: ${feeding.join(' | ')}`);
  for (const q of feeding) {
    const cols = q.slice('\'SELECT '.length, q.indexOf(' FROM signals')).split(',').map((c) => c.trim());
    for (const need of ['direction', 'status', 'created_at']) assert.ok(cols.includes(need), `${need} missing from ${q}`);
  }
});

// ── the routes, on the in-memory database ─────────────────────────────────

const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 },
  ETHUSDT: { price: 50, change: 0, volume: 1 }, SOLUSDT: { price: 20, change: 0, volume: 1 },
  XRPUSDT: { price: 2, change: 0, volume: 1 } };
let server, base;
test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}
let n = 0;
async function user() {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: `ended${Date.now()}${++n}@x.io`, password: 'longenough1' } });
  assert.ok(reg.data.token, 'registered');
  return reg.data.token;
}
async function push(key, symbol, status, createdAt) {
  await pool.execute(
    'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [key, symbol, 'LONG', 0.7, 1, 't', 'trend', 100, 95, 110, 2, 't', status,
      status === 'STOP' ? -1 : null, createdAt || new Date(), null]);
  const [rows] = await pool.execute('SELECT * FROM signals WHERE signal_key = ?', [key]);
  return rows[0];
}

test('/open-signal refuses a stopped-out call, by key and by id, and opens nothing', async () => {
  const token = await user();
  const row = await push('ended-open-1', 'BTC/USDT', 'STOP', new Date(Date.now() - 30 * 60000));
  for (const body of [{ signal_key: 'ended-open-1' }, { signal_id: row.id }]) {
    const r = await req('POST', '/api/arena/open-signal', { token, body: { ...body, margin: 200, leverage: 2 } });
    assert.equal(r.status, 400, JSON.stringify(r.data));
    assert.equal(r.data.code, 'ended');
    assert.match(r.data.error, /hit its stop/);
  }
  const a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0);
  assert.equal(a.data.balance, a.data.start_balance, 'no margin was taken');
});

test('the picker reads the status column and marks the call ended', async () => {
  const token = await user();
  await push('ended-pick-1', 'ETH/USDT', 'TARGET', new Date(Date.now() - 20 * 60000));
  const r = await req('GET', '/api/arena/signals', { token });
  assert.equal(r.status, 200);
  const row = r.data.signals.find((s) => s.symbol === 'ETHUSDT');
  assert.ok(row, 'the call is listed');
  assert.equal(row.tradeable, false);
  assert.equal(row.blocked_reason, 'ended');
});

test('practice-follow skips an ended and a stale call and opens the current one', async () => {
  const token = await user();
  assert.equal((await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } })).status, 200);
  await push('ended-follow-stop', 'SOLUSDT', 'STOP', new Date(Date.now() - 20 * 60000));
  await push('ended-follow-old', 'XRPUSDT', 'NEW', new Date(Date.now() - 48 * 3600e3));
  await push('ended-follow-new', 'ETHUSDT', 'NEW');
  const a = await req('GET', '/api/arena/account', { token });
  assert.deepEqual(a.data.positions.map((p) => p.symbol), ['ETHUSDT']);
});

test('practice-follow holds its cursor while the feed is down, and opens the call when it is back', async () => {
  const token = await user();
  await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } });
  await push('ended-follow-feed', 'BTCUSDT', 'NEW');
  setTickerFetcher(null);                                   // clear the warmed cache
  setTickerFetcher(async () => { throw new Error('feed down'); });
  try {
    const down = await req('GET', '/api/arena/account', { token });
    assert.equal(down.status, 200);
    assert.equal(down.data.positions.length, 0, 'nothing is filled without a live price');
  } finally {
    setTickerFetcher(async () => PRICES);
  }
  const back = await req('GET', '/api/arena/account', { token });
  assert.deepEqual(back.data.positions.map((p) => p.symbol), ['BTCUSDT'],
    'the call skipped while the feed was down is mirrored once it is back');
});

test('enabling follow over a stream that cannot be read changes nothing; disabling still works', async () => {
  const token = await user();
  const real = pool.execute;
  pool.execute = async function (sql, params) {
    if (/FROM signals ORDER BY id DESC LIMIT 1/i.test(sql)) throw new Error('stream down /tmp/secret.sock');
    return real.call(this, sql, params);
  };
  let on, off;
  try {
    on = await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } });
    off = await req('POST', '/api/arena/follow', { token, body: { enabled: false } });
  } finally {
    pool.execute = real;
  }
  assert.equal(on.status, 503);
  assert.equal(on.data.code, 'stream_unread');
  assert.doesNotMatch(JSON.stringify(on.data), /secret\.sock/, 'the driver text stays in the log');
  assert.equal(off.status, 200, 'a switch that cannot be turned off over a failed read is worse');
  const a = await req('GET', '/api/arena/account', { token });
  assert.ok(!a.data.follow || !a.data.follow.enabled, 'the refused enable wrote nothing');
});

test('follow starts after the newest signal by id, not by timestamp', async () => {
  // A row pushed late carries an older created_at and a higher id. Read by
  // timestamp, the cursor sat below it and the row was mirrored: a back-fill
  // on the one switch that promises never to back-fill.
  await push('ended-order-a', 'SOLUSDT', 'NEW', new Date(Date.now() - 10 * 60000));
  await push('ended-order-b', 'XRPUSDT', 'NEW', new Date(Date.now() - 70 * 60000));
  const token = await user();
  await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } });
  const a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0, 'a signal older than the switch was back-filled');
});

// ── the pages ────────────────────────────────────────────────────────────

test('both pages translate the ended refusal, in every language', () => {
  const entry = i18n.STRINGS['arena.sig_b_ended'];
  const rule = i18n.STRINGS['arena.follow_rule'];
  for (const { code } of i18n.LANGS) {
    assert.ok(entry && entry[code], `arena.sig_b_ended has no ${code}`);
    assert.ok(rule && rule[code], `arena.follow_rule has no ${code}`);
  }
  const arena = read('public', 'arena.html');
  assert.match(arena, /ended: \['arena\.sig_b_ended', 'Already over'\]/);
  assert.match(arena, /data-i18n="arena\.follow_rule"/);
  assert.match(read('public', 'js', 'dashboard.js'), /ended: \['arena\.sig_b_ended', 'Already over'\]/);
});
