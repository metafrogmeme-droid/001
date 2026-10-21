'use strict';
/**
 * Timed jobs run on one schedule, and remember across instances.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, with overlap. Every job was a setInterval in one process:
 *
 *   - the pattern watch (every 2 minutes, nothing at boot) never ran;
 *   - the board watch and the copy watch spend their first run recording a
 *     silent baseline, and no instance lived to a second run, so neither ever
 *     pushed anything;
 *   - the arena watch forgot which positions it had warned, so a new instance
 *     could warn again;
 *   - hourly sweeps ran at every boot, and the 5-minute on-chain alert read ran
 *     every minute.
 *
 * Each job now claims its turn in `job_runs` (lib/shared_schedule.js) and keeps
 * its memory in `job_state` (lib/job_state.js). "Another instance" is every
 * per-process module loaded again over the one database instances share.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const db = require('../db');

const PER_PROCESS = ['../lib/shared_schedule', '../lib/job_state', '../lib/board_watch', '../lib/copy_watch', '../lib/arena_watch'];

function freshInstance() {
  for (const m of PER_PROCESS) delete require.cache[require.resolve(m)];
  return {
    schedule: require('../lib/shared_schedule'),
    jobState: require('../lib/job_state'),
    board: require('../lib/board_watch'),
    copy: require('../lib/copy_watch'),
    arena: require('../lib/arena_watch'),
  };
}

test.beforeEach(() => {
  for (const k of Object.keys(db.pool.jobRuns)) delete db.pool.jobRuns[k];
  for (const k of Object.keys(db.pool.jobState)) delete db.pool.jobState[k];
  db.pool.arenaPositions.length = 0;
});

// ── the claim ─────────────────────────────────────────────────────────────

test('a due job is claimed by exactly one of two instances', async () => {
  const a = freshInstance().schedule;
  const b = freshInstance().schedule;
  const now = 1_800_000_000_000;
  const got = await Promise.all([a.claim('race', 60_000, now), b.claim('race', 60_000, now)]);
  assert.deepStrictEqual(got.filter(Boolean).length, 1, `claims: ${JSON.stringify(got)}`);
  // and once it exists, a second due moment is again won by one
  const again = await Promise.all([a.claim('race', 60_000, now + 60_000), b.claim('race', 60_000, now + 60_000)]);
  assert.deepStrictEqual(again.filter(Boolean).length, 1, `claims: ${JSON.stringify(again)}`);
});

test('a job is not due again on any instance until its interval has passed', async () => {
  const a = freshInstance().schedule;
  const b = freshInstance().schedule;
  const t0 = 1_800_000_000_000;
  assert.strictEqual(await a.claim('cadence', 60_000, t0), true);
  assert.strictEqual(await b.claim('cadence', 60_000, t0 + 59_999), false, 'another instance ran it early');
  assert.strictEqual(await b.claim('cadence', 60_000, t0 + 60_000), true, 'it was due and nobody ran it');
  assert.strictEqual(await a.claim('cadence', 60_000, t0 + 60_001), false);
});

test('while the database does not answer, an instance runs on its own clock', async () => {
  const s = freshInstance().schedule;
  const real = db.pool.execute;
  db.pool.execute = async (sql, params) => {
    if (/job_runs/i.test(sql)) throw Object.assign(new Error('down'), { code: 'ECONNREFUSED' });
    return real.call(db.pool, sql, params);
  };
  try {
    const t0 = 1_800_000_000_000;
    assert.strictEqual(await s.claim('offline', 60_000, t0), true, 'an outage stopped the job');
    assert.strictEqual(await s.claim('offline', 60_000, t0 + 1_000), false);
    assert.strictEqual(await s.claim('offline', 60_000, t0 + 60_000), true);
  } finally {
    db.pool.execute = real;
  }
});

test('a two-minute job runs every two minutes on 70-second instances', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 1_800_000_000_000 });
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
  const runs = [];
  const job = async () => { runs.push(Date.now()); };
  const start = Date.now();
  // Six instances in a row, each living 70 seconds: 7 minutes in all.
  for (let i = 0; i < 6; i++) {
    const stop = freshInstance().schedule.every('two_minutes', 120_000, job, { tickMs: 15_000, firstDelayMs: 5_000 });
    for (let s = 0; s < 70; s += 5) { t.mock.timers.tick(5_000); await flush(); }
    stop();
  }
  const offsets = runs.map((r) => Math.round((r - start) / 1000));
  // Before: no instance lived to its first tick at 120 s, so this was [].
  assert.ok(runs.length >= 3, `ran ${runs.length} times in 7 minutes: ${offsets}`);
  for (let i = 1; i < runs.length; i++) {
    assert.ok(runs[i] - runs[i - 1] >= 120_000, `ran twice inside two minutes: ${offsets}`);
  }
});

test('a job told not to run is not claimed', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 1_800_000_000_000 });
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
  let runs = 0;
  let allowed = false;
  const stop = freshInstance().schedule.every('gated', 60_000, async () => { runs += 1; },
    { tickMs: 15_000, firstDelayMs: 5_000, when: () => allowed });
  t.mock.timers.tick(5_000); await flush();
  assert.strictEqual(runs, 0);
  assert.strictEqual(db.pool.jobRuns.gated, undefined, 'a skipped tick claimed the job and pushed back its next run');
  allowed = true;
  t.mock.timers.tick(15_000); await flush();
  assert.strictEqual(runs, 1);
  stop();
});

// ── the memory ────────────────────────────────────────────────────────────

test('the board watch takes up the previous instance\'s baseline instead of starting a new one', async () => {
  const pushes = [];
  const notify = async (p) => { pushes.push(p); };
  const board = [{ handle: 'runefox', rank: 1 }, { handle: 'wolf_7', rank: 2 }];
  const moved = [{ handle: 'wolf_7', rank: 1 }, { handle: 'runefox', rank: 2 }];
  const a = freshInstance();
  assert.strictEqual(await a.board.sweepBoard(async () => board, notify), false, 'the first sweep anywhere is the baseline');
  const b = freshInstance();
  assert.strictEqual(await b.board.sweepBoard(async () => moved, notify), true,
    'the next instance treated its own first sweep as a baseline and pushed nothing');
  assert.match(pushes[0].body, /wolf_7 climbed #2→#1/);
  const c = freshInstance();
  assert.strictEqual(await c.board.sweepBoard(async () => moved, notify), false, 'an unchanged board pushed');
});

test('the copy watch announces a new pick on the instance after the baseline', async () => {
  const CATALOG = [{ id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯', copy_follow: true,
    scorecard: { gates: { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN' } } }];
  const SIG = (k) => ({ signal_key: k, symbol: 'BTC/USDT', confidence: 0.8, regime: 'TREND_DOWN', direction: 'LONG',
    expires_at: new Date(Date.now() + 5 * 60000) });
  const deps = (signals) => ({
    loadFollowedAgentIds: async () => ['dip-sniper'],
    loadSignals: async () => signals,
    loadCatalogue: async () => CATALOG,
    loadFollowers: async () => [1],
    loadOptIns: async () => new Set([1]),
  });
  const sends = [];
  const notify = async (p, ids) => { sends.push({ p, ids }); return ids.length; };
  assert.strictEqual(await freshInstance().copy.sweepCopy(deps([SIG('s1')]), notify), 0, 'baseline');
  assert.strictEqual(await freshInstance().copy.sweepCopy(deps([SIG('s1'), SIG('s2')]), notify), 1,
    'the next instance re-baselined and the new pick was never announced');
  assert.strictEqual(await freshInstance().copy.sweepCopy(deps([SIG('s1'), SIG('s2')]), notify), 0,
    'a pick already announced was announced again by the next instance');
});

test('the arena watch does not warn again on the next instance', async () => {
  const { liqPrice } = require('../lib/arena');
  const POS = { id: 1, user_id: 9, symbol: 'BTCUSDT', direction: 'LONG', entry: 100, margin: 100, leverage: 10 };
  db.pool.arenaPositions.push({ ...POS });
  const sent = [];
  const deps = (price) => ({
    push: { isConfigured: () => true, notifySubscribers: async (p, ids) => { sent.push(ids); } },
    getTickers: async () => ({ BTCUSDT: { price } }),
  });
  const hot = liqPrice(POS) * 1.02;
  const safe = 100;
  await freshInstance().arena.runOnce(deps(hot));
  assert.strictEqual(sent.length, 1, 'the first approach must warn');
  await freshInstance().arena.runOnce(deps(hot));
  assert.strictEqual(sent.length, 1, 'the next instance warned about the same position again');
  // the other arm: recovery re-arms it, across instances too
  await freshInstance().arena.runOnce(deps(safe));
  await freshInstance().arena.runOnce(deps(hot));
  assert.strictEqual(sent.length, 2, 'a recovered position that came back was not warned');
});

test('job memory: three values, and the process copy only while the database is down', async () => {
  const s = freshInstance().jobState;
  assert.deepStrictEqual(await s.load('j'), { found: false });
  await s.save('j', { a: 1 });
  assert.deepStrictEqual(await freshInstance().jobState.load('j'), { found: true, value: { a: 1 } });
  const real = db.pool.execute;
  db.pool.execute = async (sql, params) => {
    if (/job_state/i.test(sql)) throw Object.assign(new Error('down'), { code: 'ECONNREFUSED' });
    return real.call(db.pool, sql, params);
  };
  try {
    assert.deepStrictEqual(await s.load('j'), { found: true, value: { a: 1 }, local: true });
    assert.deepStrictEqual(await freshInstance().jobState.load('j'), { found: false, local: true });
  } finally {
    db.pool.execute = real;
  }
});

test('the five-minute on-chain alert read is not repeated by the next instance', async () => {
  const [ins] = await db.pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', ['hf-rotation@example.com', 'x']);
  const uid = ins.insertId;
  await db.pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', ['0x' + 'ab'.repeat(20), uid]);
  let reads = 0;
  let defi = require('../lib/defi');
  const provider = () => ({
    async getBalance() { return 0n; },
    async call(tx) {
      if (String(tx.to).toLowerCase() === defi.AAVE_POOLS.ethereum.toLowerCase()) {
        reads += 1;
        const w = (v) => BigInt(v).toString(16).padStart(64, '0');
        // a healthy position (HF 2.0): nothing fires, so the alert stays armed
        return '0x' + w(5000n * 10n ** 8n) + w(3000n * 10n ** 8n) + w(0) + w(8000) + w(7500) + w(2n * 10n ** 18n);
      }
      return '0x' + '0'.repeat(64);
    },
    async getNetwork() { return { chainId: 0n }; },
    async resolveName(n) { return n; },
  });
  process.env.WEB3_CHAINS = 'ethereum';
  // A new instance: the alert engine AND the DeFi reader (with its 60-second
  // position cache) loaded again, the fake chain installed on the new reader.
  const fresh = () => {
    for (const m of ['../lib/alerts', '../lib/defi', '../lib/shared_schedule']) delete require.cache[require.resolve(m)];
    defi = require('../lib/defi');
    defi.setProviderFactory(provider);
    return require('../lib/alerts');
  };
  try {
    const a = fresh();
    const r = await a.createAlert(uid, { base: 'DEFI', metric: 'health_factor', op: '<', threshold: 1.5, mode: 'once' });
    assert.ok(r.ok, r.error);
    await a.runOnce(async () => {});
    const first = reads;
    assert.ok(first > 0, 'the first pass did not read the chain');
    await fresh().runOnce(async () => {});
    assert.strictEqual(reads, first, 'the next instance read the chain again inside five minutes');
  } finally {
    delete process.env.WEB3_CHAINS;
  }
});

test('the pattern watch does not announce the same pattern again on the next instance', async () => {
  db.pool.arenaPositions.push({ id: 7, user_id: 21, symbol: 'BTCUSDT', direction: 'LONG', entry: 100, margin: 100, leverage: 5 });
  const sent = [];
  const deps = (confidence) => ({
    push: { isConfigured: () => true, notifySubscribers: async (p, ids) => { sent.push({ p, ids }); } },
    getLatestScan: async () => ({ deepscan: { hits: [{ symbol: 'BTC/USDT',
      chart_patterns: [{ name: 'Elliott 5-Wave Impulse', signal: 'bullish', confidence }] }] } }),
  });
  const fresh = () => { delete require.cache[require.resolve('../lib/pattern_watch')]; delete require.cache[require.resolve('../lib/job_state')]; return require('../lib/pattern_watch'); };
  await fresh().runOnce(deps(0.78));
  assert.strictEqual(sent.length, 1, 'the first sighting must be announced');
  assert.deepStrictEqual(sent[0].ids, [21]);
  await fresh().runOnce(deps(0.78));
  assert.strictEqual(sent.length, 1, 'the next instance announced the same pattern again');
  // the other arm: a pattern the next instance has NOT announced is, so the
  // silence above is the shared record, not an instance that never sends
  db.pool.arenaPositions.push({ id: 8, user_id: 22, symbol: 'ETHUSDT', direction: 'SHORT', entry: 100, margin: 100, leverage: 5 });
  await fresh().runOnce({ ...deps(0.4), getLatestScan: async () => ({ deepscan: { hits: [{ symbol: 'ETH/USDT',
    chart_patterns: [{ name: 'Head and Shoulders', signal: 'bearish', confidence: 0.4 }] }] } }) });
  assert.strictEqual(sent.length, 2);
  assert.deepStrictEqual(sent[1].ids, [22]);
});

test('every timed job the server starts is on the shared schedule', async (t) => {
  // The armed alert from the on-chain test above would send the engine to the network.
  db.pool.userAlerts.length = 0;
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 1_800_000_000_000 });
  const flush = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
  const JOBS = { '../lib/alerts': 'startAlertEngine', '../lib/letter': 'startLetterSweep', '../lib/board_watch': 'startBoardWatch',
    '../lib/arena_watch': 'startArenaWatch', '../lib/season_watch': 'startSeasonWatch', '../lib/pattern_watch': 'startPatternWatch',
    '../routes/arena': 'startFollowSweep', '../lib/memeclaw_indexer': 'startMemeclawIndexer' };
  for (const m of [...Object.keys(JOBS), '../lib/shared_schedule', '../lib/job_state']) delete require.cache[require.resolve(m)];
  for (const [m, start] of Object.entries(JOBS)) require(m)[start]();
  t.mock.timers.tick(5_000); await flush();
  assert.deepStrictEqual(Object.keys(db.pool.jobRuns).sort(),
    ['alert_engine', 'arena_follow', 'arena_watch', 'board_watch', 'letter_sweep', 'memeclaw_indexer', 'pattern_watch', 'season_watch'],
    'a job started outside the shared schedule runs once per instance, or never');
});
