'use strict';
/**
 * The shim answered questions it did not understand, with "nothing".
 *
 * `MemoryDB.query()` ended in a terminal `return [[], []]`. Any statement no
 * branch recognised got empty rows — indistinguishable, to every caller, from
 * a table that genuinely holds nothing.
 *
 * That is this repository's founding defect living inside its test
 * infrastructure. And the blast radius is the whole suite: ~2,400 web tests
 * run against this class, so an unimplemented statement did not fail. It
 * PASSED, and the surface above it was asserted correct while reading from a
 * table that was never written.
 *
 * MEASURED, NOT ASSUMED. Before changing anything I logged every fall-through
 * across the entire suite. Five hits, one table:
 *
 *     4 × REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)
 *     1 × SELECT flight_json FROM flight_cache WHERE id = 1
 *
 * The Guardian flight recorder wrote to nothing and read back nothing, and
 * every test over it agreed. That number is also why throwing is affordable:
 * implement the one missing table and the fall-through becomes unreachable,
 * so the throw costs nothing today and makes the next omission loud.
 *
 * WHAT THIS DOES NOT FIX. Only the "statement not implemented" half of M18.
 * A branch that IS implemented but diverges from MySQL's semantics still
 * passes here and still breaks in production — the audit records that having
 * already 500'd. That needs a CI job against real MySQL, which is a separate
 * and larger piece of work.
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const DB = path.join(__dirname, '..', 'db.js');

function freshDb() {
  const prev = process.env.DATABASE_URL;
  delete process.env.DATABASE_URL;
  delete require.cache[require.resolve(DB)];
  const mod = require(DB);
  if (prev === undefined) delete process.env.DATABASE_URL;
  else process.env.DATABASE_URL = prev;
  return mod;
}

// ── the defect ───────────────────────────────────────────────────────

test('an unimplemented statement throws instead of reporting "no rows"', async () => {
  const { pool } = freshDb();
  await assert.rejects(
    () => pool.execute('SELECT * FROM a_table_the_shim_never_heard_of WHERE id = ?', [1]),
    (err) => {
      assert.equal(err.code, 'ER_MEMORYDB_UNIMPLEMENTED');
      return true;
    },
    'the shim answered a statement it does not implement with empty rows — '
    + 'every test above it then passes while reading from nothing');
});

test('the error says which statement, so it is actionable', async () => {
  const { pool } = freshDb();
  const err = await pool.execute('SELECT x FROM nonexistent_widgets').then(
    () => null, (e) => e);
  assert.ok(err, 'no error raised');
  assert.match(err.message, /nonexistent_widgets/,
    'the failure does not name the statement, so nobody can act on it');
  assert.match(err.message, /MemoryDB/);
});

test('the error carries no values, only a verb and a table', async () => {
  // Same contract as `_lastStatement`: the descriptor is DDL this repo
  // authors. A shim error that echoed parameters would put user data — or a
  // token — into logs.
  const { pool } = freshDb();
  const err = await pool.execute(
    'SELECT * FROM unknown_table WHERE secret = ?', ['hunter2-do-not-log'],
  ).then(() => null, (e) => e);
  assert.ok(err);
  assert.ok(!err.message.includes('hunter2-do-not-log'),
    'the shim error echoed a parameter value into its message');
});

// ── the one table it was actually hiding ─────────────────────────────

test('flight_cache round-trips instead of silently discarding', async () => {
  // Four writes and a read, all previously answered with empty rows. The
  // Guardian flight recorder is the surface that lost them.
  const { pool } = freshDb();
  const payload = JSON.stringify({ records: [{ decision_id: 'T-1' }] });

  await pool.execute('REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)',
    [payload]);
  const [rows] = await pool.execute(
    'SELECT flight_json FROM flight_cache WHERE id = 1');

  assert.equal(rows.length, 1, 'the write went nowhere');
  assert.equal(rows[0].flight_json, payload, 'the read did not return the write');
});

test('flight_cache is empty before anything is written, not broken', async () => {
  // The genuine empty case must still be empty — "not written yet" and
  // "not implemented" are different answers and this change is about telling
  // them apart, not about making everything loud.
  const { pool } = freshDb();
  const [rows] = await pool.execute(
    'SELECT flight_json FROM flight_cache WHERE id = 1');
  assert.deepEqual(rows, []);
});

test('a REPLACE overwrites rather than accumulating', async () => {
  const { pool } = freshDb();
  await pool.execute('REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)', ['a']);
  await pool.execute('REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)', ['b']);
  const [rows] = await pool.execute('SELECT flight_json FROM flight_cache WHERE id = 1');
  assert.equal(rows.length, 1, 'single-row table grew a second row');
  assert.equal(rows[0].flight_json, 'b');
});

// ── the guard must not fire on real traffic ──────────────────────────

test('the statements the suite actually runs are all implemented', async () => {
  // The measurement, as an assertion: a representative slice of the shapes
  // the app issues must NOT throw. If this starts failing, a real query lost
  // its branch — which is precisely what the fall-through used to hide.
  const { pool } = freshDb();
  const shapes = [
    ['INSERT INTO users (email, password_hash) VALUES (?, ?)', ['a@b.c', 'h']],
    ['SELECT id, email, token_epoch FROM users WHERE email = ?', ['a@b.c']],
    ['UPDATE users SET token_epoch = token_epoch + 1 WHERE id = ?', [1]],
    ["SELECT COUNT(*) as open_count FROM trades WHERE user_id = ? AND status = ?", [1, 'OPEN']],
    ['SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1', [1]],
    ['REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)', ['{}']],
    ['SELECT scan_json, updated_at FROM scan_cache WHERE id = 1', []],
  ];
  for (const [sql, params] of shapes) {
    await assert.doesNotReject(() => pool.execute(sql, params),
      `the shim lost its handler for: ${sql}`);
  }
});

test('CREATE TABLE and the migrations still no-op quietly', async () => {
  // migrate() runs 80+ DDL statements the shim deliberately ignores. Throwing
  // on those would make the guard fire on every boot.
  const { pool } = freshDb();
  await assert.doesNotReject(
    () => pool.execute('CREATE TABLE IF NOT EXISTS whatever (id INT)'));
  await assert.doesNotReject(
    () => pool.execute('ALTER TABLE users ADD COLUMN some_new_thing INT'));
});
