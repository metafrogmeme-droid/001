'use strict';
// #1015 made the trade sync retryable and said plainly what its guard did not
// cover: an in-process Map, per container, good for the retry window and not a
// durable ledger. A restart landing between an attempt and its retry could
// still double-insert. This closes that.
//
// THE ORDER OF THE TWO STATEMENTS IS THE WHOLE SAFETY ARGUMENT.
//
// The close branch used to run:
//
//     DELETE ... WHERE status='OPEN' LIMIT 1
//     INSERT ... status='CLOSED'
//
// Add a unique constraint to that order and a replayed close becomes strictly
// WORSE than the duplicate it prevents: the DELETE has already removed a live
// position by the time the INSERT is rejected, so the position vanishes and no
// close replaces it. Insert first and a rejection costs nothing — the open row
// is still sitting there untouched.
//
// A transaction would also solve it. It is not used because the in-memory pool
// this app falls back to without DATABASE_URL has no transaction support, and
// a safety property that holds on only one of two backends is not a safety
// property. Ordering holds on both.
//
// The residual failure — process dies between insert and delete — leaves the
// open row beside its close. The next full portfolio sync is replace-all and
// heals it. A lingering open row is visible and self-correcting; a deleted
// position is neither. That trade is deliberate.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const sync = fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
const db = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');

function closeBranch() {
  const i = sync.indexOf("} else if (event === 'close') {");
  assert.ok(i > 0, 'close branch not found');
  return sync.slice(i, i + 2600);
}

// ── The migration ───────────────────────────────────────────────────────────

test('event_id is added as a nullable column with a unique index', () => {
  assert.match(db, /ALTER TABLE trades ADD COLUMN event_id VARCHAR\(64\) NULL/);
  assert.match(db, /ALTER TABLE trades ADD UNIQUE INDEX idx_trades_event_id \(event_id\)/);
});

test('the column is NULLABLE, which is what makes it additive', () => {
  // MySQL permits many NULLs in a UNIQUE index. Every pre-existing row keeps
  // working untouched and only rows carrying an id are constrained — no
  // backfill, no rewrite of history. NOT NULL here would fail the migration
  // on the first existing row.
  const i = db.indexOf('ADD COLUMN event_id');
  assert.match(db.slice(i, i + 60), /NULL/);
  assert.doesNotMatch(db.slice(i, i + 60), /NOT NULL/);
});

test('the migration follows the repo idiom for re-runnable DDL', () => {
  const i = db.indexOf('ALTER TABLE trades ADD COLUMN event_id');
  const line = db.slice(i - 40, i + 120);
  assert.match(line, /try \{/, 'must tolerate already-applied migrations');
});

// ── The ordering that makes the constraint safe ─────────────────────────────

test('the close branch INSERTs before it DELETEs', () => {
  const b = closeBranch();
  const ins = b.indexOf('INSERT INTO trades');
  const del = b.indexOf('DELETE FROM trades');
  assert.ok(ins > 0 && del > 0, 'both statements must be present');
  assert.ok(ins < del,
    'DELETE-then-INSERT plus a unique constraint deletes a live position and '
    + 'then fails to replace it — data loss worse than the duplicate');
});

test('the open branch carries the id too', () => {
  const i = sync.indexOf("if (event === 'open') {");
  const b = sync.slice(i, i + 900);
  assert.match(b, /event_id\)/, 'the column must be in the INSERT');
  assert.match(b, /eid\]/, 'and the value must be bound');
});

test('the id is length-bounded to the column width', () => {
  assert.match(sync, /String\(event_id\)\.slice\(0, 64\)/,
    'a longer id would be truncated by the database and two distinct events '
    + 'could collide on their first 64 characters');
});

test('a missing id stores NULL rather than a placeholder', () => {
  // Every '' or 'unknown' would collide with every other one, and the second
  // real event from an older bot would be rejected as a duplicate.
  assert.match(sync, /const eid = event_id \? String\(event_id\)[^;]*: null;/);
});

// ── Recognising the database's answer ───────────────────────────────────────

test('a unique violation is reported as a duplicate, not an error', () => {
  const i = sync.indexOf('if (_isDuplicateKey(err))');
  assert.ok(i > 0, 'the handler must consult the predicate');
  const b = sync.slice(i, i + 220);
  assert.match(b, /ok: true/);
  assert.match(b, /duplicate: true/);
  assert.match(b, /durable: true/,
    'the response should distinguish the durable catch from the in-process '
    + 'one — they answer the same question with different confidence');
});

test('the predicate matches both the code and the errno', () => {
  const i = sync.indexOf('function _isDuplicateKey');
  const b = sync.slice(i, i + 300);
  assert.match(b, /ER_DUP_ENTRY/);
  assert.match(b, /1062/);
});

test('any other database error still throws', () => {
  // Swallowing an unknown error as "duplicate" would report success for a
  // trade that was never written — the exact failure this path exists to
  // prevent, inverted.
  const i = sync.indexOf('if (_isDuplicateKey(err))');
  const b = sync.slice(i, i + 300);
  assert.match(b, /throw err;/);
});

test('the in-process guard is kept as the fast path', () => {
  // It answers without a database round trip for the common case, and the
  // constraint is the authority behind it. Removing it would be correct and
  // slower; removing the constraint would be fast and wrong.
  assert.match(sync, /_seenEvent\(event_id\)/);
  const memAt = sync.indexOf('_seenEvent(event_id)');
  const dbAt = sync.indexOf('if (_isDuplicateKey(err))');
  assert.ok(memAt < dbAt, 'the cheap check runs first');
});

// ── The predicate itself, exercised ─────────────────────────────────────────

test('_isDuplicateKey behaves', () => {
  const start = sync.indexOf('function _isDuplicateKey');
  const end = sync.indexOf('function _seenEvent');
  const mod = { exports: {} };
  // eslint-disable-next-line no-new-func
  new Function('module', `${sync.slice(start, end)}\nmodule.exports = _isDuplicateKey;`)(mod);
  const isDup = mod.exports;
  assert.strictEqual(isDup({ code: 'ER_DUP_ENTRY' }), true);
  assert.strictEqual(isDup({ errno: 1062 }), true);
  assert.strictEqual(isDup({ code: 'ER_NO_SUCH_TABLE' }), false);
  assert.strictEqual(isDup({ errno: 1146 }), false);
  assert.strictEqual(isDup(new Error('boom')), false);
  assert.strictEqual(isDup(null), false);
  assert.strictEqual(isDup(undefined), false);
});
