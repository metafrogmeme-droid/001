'use strict';
/**
 * /api/portfolio names, per figure, where the number came from.
 *
 * The flags the payload already carried each answer a different question —
 * `stale` is the equity row's age on the operator path and `false` by
 * construction on the other two, `live_unavailable` is about the venue,
 * `unconfigured` is about the deployment — and no client reading them
 * together could recover "this number is the last thing this site stored".
 * `provenance` is one closed vocabulary across the three branches, and
 * `as_of` is the figure's own time or null: a time nobody recorded is never
 * manufactured from the request clock.
 *
 * Driven through the real route with helpers/portfolio_route.js.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { get, scanRow, FRESH, OLD } = require('./helpers/portfolio_route');

const WORDS = ['gateway', 'scan_cache', 'sync_rows', 'snapshot', 'db_rows', 'never_stored', 'unread', 'absent'];
const FIGURES = ['equity', 'open_positions', 'daily_pnl'];
const iso = (v) => typeof v === 'string' && Number.isFinite(new Date(v).getTime()) && new Date(v).toISOString() === v;

const AT = '2026-09-14T10:00:00.000Z';
const bot = (extra) => ({ mode: 'PAPER', equity: 10000, daily_pnl: 1, open_positions: [], updated_at: AT, ...extra });

const CASES = {
  op_scan:           () => get({ scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }), snapAt: OLD }),
  op_snapshot:       () => get({ scan: scanRow({ live_mode: false }) }),
  op_unavailable:    () => get({ scan: scanRow({ live_mode: true, equity: null, live_unavailable: true }), snapAt: OLD }),
  op_never:          () => get({ scan: scanRow({ live_mode: false }), snapshots: false }),
  op_mode_unread:    () => get({ scan: [] }),
  op_scan_throws:    () => get({ scan: () => { throw new Error('ECONNRESET'); } }),
  user_gateway:      () => get({ operator: false, gateway: { data: bot() } }),
  user_unstamped:    () => get({ operator: false, gateway: { data: bot({ updated_at: 12345 }) } }),
  user_503_stored:   () => get({ operator: false, gateway: { status: 503 } }),
  user_503_snaponly: () => get({ operator: false, gateway: { status: 503 }, stored: false }),
  user_503_rowsonly: () => get({ operator: false, gateway: { status: 503 }, snapshots: false }),
  user_503_never:    () => get({ operator: false, gateway: { status: 503 }, snapshots: false, stored: false }),
  user_threw:        () => get({ operator: false, gateway: { throws: true } }),
  user_nobot_stored: () => get({ operator: false, gateway: { configured: false } }),
  user_nobot_never:  () => get({ operator: false, gateway: { configured: false }, snapshots: false, stored: false }),
};

const bodies = {};
test('every branch publishes a provenance from one closed vocabulary and an as_of that is ISO or null', async () => {
  for (const [name, call] of Object.entries(CASES)) {
    const { status, body } = await call();
    assert.equal(status, 200, `${name}: ${status}`);
    bodies[name] = body;
    assert.deepEqual(Object.keys(body.provenance).sort(), [...FIGURES].sort(), `${name}: one word per figure`);
    for (const f of FIGURES) assert.ok(WORDS.includes(body.provenance[f]), `${name}.${f} = ${body.provenance[f]}`);
    assert.ok(body.as_of && typeof body.as_of === 'object', `${name} carries as_of`);
    for (const [f, v] of Object.entries(body.as_of)) {
      assert.ok(v === null || iso(v), `${name}.as_of.${f} = ${JSON.stringify(v)} is neither ISO nor null`);
      assert.ok(FIGURES.includes(f), `${name}.as_of.${f} names a figure`);
    }
  }
});

test('the operator path: the scan cache is a reading, the snapshot a memory, the synced rows the bot\'s last push', () => {
  assert.deepEqual(bodies.op_scan.provenance, { equity: 'scan_cache', open_positions: 'sync_rows', daily_pnl: 'absent' });
  assert.ok(iso(bodies.op_scan.as_of.equity), 'the scan\'s received_at travels');
  assert.ok(Math.abs(Date.now() - new Date(bodies.op_scan.as_of.equity).getTime()) < 60 * 1000, 'and it is the scan\'s time, not the snapshot\'s (two hours old)');
  assert.equal(bodies.op_scan.as_of.open_positions, null, 'the sync records no time per row');

  assert.deepEqual(bodies.op_snapshot.provenance, { equity: 'snapshot', open_positions: 'sync_rows', daily_pnl: 'absent' });
  assert.ok(iso(bodies.op_snapshot.as_of.equity), 'the snapshot\'s own time travels');

  // An unread mode does not change where the figures came from.
  for (const k of ['op_mode_unread', 'op_scan_throws']) {
    assert.equal(bodies[k].mode, null, k);
    assert.deepEqual(bodies[k].provenance, bodies.op_snapshot.provenance, k);
  }
});

test('LIVE with no readable balance is UNREAD with no number and no time, and never a stored memory in its place', () => {
  const b = bodies.op_unavailable;
  assert.equal(b.equity, null);
  assert.equal(b.live_unavailable, true);
  assert.equal(b.provenance.equity, 'unread');
  assert.equal(b.as_of.equity, null, 'the two-hour-old snapshot\'s time must not be attached to an equity that is null');
  assert.equal(b.provenance.open_positions, 'sync_rows', 'the synced rows are still the bot\'s push');
});

test('nothing stored is NEVER_STORED — with no number and no time — on every branch that can reach it', () => {
  for (const k of ['op_never', 'user_503_never', 'user_nobot_never']) {
    assert.equal(bodies[k].equity, null, k);
    assert.equal(bodies[k].provenance.equity, 'never_stored', k);
    assert.equal(bodies[k].as_of.equity, null, k);
  }
  for (const k of ['user_503_never', 'user_nobot_never']) {
    assert.equal(bodies[k].provenance.open_positions, 'never_stored', `${k}: an empty table nobody wrote to is not a stored empty book`);
    assert.deepEqual(bodies[k].open_positions, [], `${k}: the list itself is unchanged — the WORD is what changed`);
  }
  // The operator's open rows are the sync's even when nothing else is stored.
  assert.equal(bodies.op_never.provenance.open_positions, 'sync_rows');
});

test('a fallback distinguishes "ever synced" by either trace — a snapshot OR a stored row', () => {
  // A snapshot alone: the gateway once answered, and the open rows were
  // rewritten wholesale at that moment, so [] is a stored empty book.
  assert.deepEqual(bodies.user_503_snaponly.provenance, { equity: 'snapshot', open_positions: 'db_rows', daily_pnl: 'absent' });
  // Rows alone (a closed trade stored, no equity ever snapshotted): the rows
  // are a memory and the equity was never stored.
  assert.deepEqual(bodies.user_503_rowsonly.provenance, { equity: 'never_stored', open_positions: 'db_rows', daily_pnl: 'absent' });
  assert.equal(bodies.user_503_rowsonly.as_of.equity, null);
  assert.deepEqual(bodies.user_503_stored.provenance, { equity: 'snapshot', open_positions: 'db_rows', daily_pnl: 'absent' });
  assert.deepEqual(bodies.user_threw.provenance, bodies.user_503_stored.provenance, 'a thrown read and a 503 are the same absence');
});

test('the unconfigured branch stamps stale:false over a MEMORY, and the provenance is what says so', () => {
  const b = bodies.user_nobot_stored;
  assert.equal(b.unconfigured, true);
  assert.equal(b.mode, 'PAPER', 'the deployment\'s own state, not a guess');
  assert.equal(b.stale, false, 'about the deployment, not the figures');
  assert.deepEqual(b.provenance, { equity: 'snapshot', open_positions: 'db_rows', daily_pnl: 'absent' });
  assert.ok(iso(b.as_of.equity));
});

test('the gateway branch: every figure is the bot\'s answer, stamped with the bot\'s own time — never the request clock', () => {
  const b = bodies.user_gateway;
  assert.deepEqual(b.provenance, { equity: 'gateway', open_positions: 'gateway', daily_pnl: 'gateway' });
  assert.deepEqual(b.as_of, { equity: AT, open_positions: AT, daily_pnl: AT });
  assert.equal(b.stale, false);
  // A payload whose stamp is not a string carries no time at all.
  const u = bodies.user_unstamped;
  assert.deepEqual(u.provenance, b.provenance);
  assert.deepEqual(u.as_of, { equity: null, open_positions: null, daily_pnl: null });
});

test('as_of never names a time for a figure whose provenance is not a dated source', () => {
  for (const [name, b] of Object.entries(bodies)) {
    const dated = { equity: ['gateway', 'scan_cache', 'snapshot'], open_positions: ['gateway'], daily_pnl: ['gateway'] };
    for (const f of FIGURES) {
      const v = b.as_of[f];
      if (v !== null && v !== undefined) {
        assert.ok(dated[f].includes(b.provenance[f]), `${name}.${f}: a time on a ${b.provenance[f]} figure`);
      }
    }
    // And a null equity never carries a time: a time on nothing is a claim
    // that something was read then.
    if (b.equity === null) assert.equal(b.as_of.equity, null, `${name}: as_of on a null equity`);
  }
  assert.ok(FRESH);
});
