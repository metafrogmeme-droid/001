'use strict';
/**
 * A column the full DDL adds below migrate()'s fast path reaches a database
 * whose tables are already current ONLY through the fast path's own
 * reconcile lists (REQUIRED_SIGNAL_COLUMNS, REQUIRED_USER_COLUMNS).
 *
 * 2026-10-11 the fast path arrived; 2026-10-12 net_rr and rr_fee_losing were
 * added to the CREATE TABLE, to the ALTER list beneath the fast path, and to
 * the reader's SELECT. Production had every table, took the fast path on each
 * deploy, never ran the ALTER, and GET /api/signals answered 503 for a column
 * the reader selected — for two days, until the reconcile lists existed.
 * migration_ddl.test.js proves the lists are applied; nothing tied the lists
 * to the ALTERs below them, so the next column added only there would be
 * skipped again, silently. This is that tie.
 *
 * TWO-WAY, against a dated baseline of the ALTER-added columns production
 * already carries (fast_path_column_baseline.txt): a new ALTER-added column
 * must be in a REQUIRED_* table or in the baseline (a reviewer's decision,
 * made in the same commit); a baseline row whose ALTER is gone must go. And
 * every REQUIRED_* column must still be an ALTER below the fast path, so the
 * fast path and the full DDL never disagree about what a table holds.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
const CODE = codeOnly(SRC);
const BASELINE = path.join(__dirname, 'fast_path_column_baseline.txt');
const FAST_PATH_MARK = 'Schema already current';

// The reconcile lists, as the module exports them. Keyed by the table each
// list reconciles; a third list must be registered here to be checked.
const {
  REQUIRED_SIGNAL_COLUMNS, REQUIRED_USER_COLUMNS,
  REQUIRED_DUEL_ROUND_COLUMNS, REQUIRED_DUEL_PICK_COLUMNS,
} = require('../db');
const REQUIRED = {
  signals: REQUIRED_SIGNAL_COLUMNS, users: REQUIRED_USER_COLUMNS,
  duel_rounds: REQUIRED_DUEL_ROUND_COLUMNS, duel_picks: REQUIRED_DUEL_PICK_COLUMNS,
};

function alterColumnsBelowTheFastPath() {
  const mark = SRC.indexOf(FAST_PATH_MARK);
  assert.ok(mark > 0, 'the fast path marker is gone — this check is reading nothing');
  const out = new Set();
  const re = /ALTER TABLE\s+`?(\w+)`?\s+ADD COLUMN\s+`?(\w+)`?/g;
  let m;
  while ((m = re.exec(CODE)) !== null) {
    if (m.index > mark) out.add(`${m[1]} ${m[2]}`);
  }
  return out;
}

function baseline() {
  const rows = fs.readFileSync(BASELINE, 'utf8').split('\n')
    .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
  for (const r of rows) assert.match(r, /^\w+ \w+$/, `malformed baseline row: ${r}`);
  return new Set(rows);
}

function required() {
  const out = new Set();
  for (const [table, cols] of Object.entries(REQUIRED)) {
    for (const c of Object.keys(cols)) out.add(`${table} ${c}`);
  }
  return out;
}

test('the scan sees the real ALTER list', () => {
  const alters = alterColumnsBelowTheFastPath();
  assert.ok(alters.size >= 60, `only ${alters.size} ALTER-added columns found — the matcher drifted`);
  assert.ok(alters.has('signals net_rr') && alters.has('users pi_uid'));
});

test('a column added below the fast path is reconciled by it, or recorded as already everywhere', () => {
  const alters = alterColumnsBelowTheFastPath();
  const known = new Set([...baseline(), ...required()]);
  const unguarded = [...alters].filter((a) => !known.has(a)).sort();
  assert.deepEqual(unguarded, [],
    'these ALTER TABLE ... ADD COLUMN statements sit below the fast path, so a database '
    + 'whose tables are all present never runs them. Add each column to the table\'s '
    + 'REQUIRED_*_COLUMNS list in app/db.js (the fast path then adds it), or, only if every '
    + 'production database already carries it, to app/test/fast_path_column_baseline.txt:\n  '
    + unguarded.join('\n  '));
});

test('the baseline names no column the migration no longer adds', () => {
  const alters = alterColumnsBelowTheFastPath();
  const stale = [...baseline()].filter((b) => !alters.has(b)).sort();
  assert.deepEqual(stale, [], `baseline rows with no ALTER behind them — delete them:\n  ${stale.join('\n  ')}`);
});

test('every reconciled column is also an ALTER below the fast path, and not in the baseline', () => {
  const alters = alterColumnsBelowTheFastPath();
  const base = baseline();
  for (const r of required()) {
    assert.ok(alters.has(r), `${r} is reconciled on the fast path but the full DDL never adds it: the two paths disagree`);
    assert.ok(!base.has(r), `${r} is both reconciled and baselined — one reading, not two`);
  }
});

test('the reconcile lists registered here are the ones db.js exports', () => {
  const exported = Object.keys(require('../db')).filter((k) => /^REQUIRED_\w+_COLUMNS$/.test(k)).sort();
  assert.deepEqual(exported, ['REQUIRED_DUEL_PICK_COLUMNS', 'REQUIRED_DUEL_ROUND_COLUMNS',
    'REQUIRED_SIGNAL_COLUMNS', 'REQUIRED_USER_COLUMNS'],
    'a new REQUIRED_*_COLUMNS list must be registered in REQUIRED above so this check reads it');
});
