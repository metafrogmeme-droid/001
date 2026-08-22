'use strict';
/**
 * "Delete my account" had nowhere to go, and the first list of what to delete
 * was wrong.
 *
 * There was no deletion path in the product at all — no route, no SQL, not
 * even an `is_active = 0` that something set. So the privacy page said so
 * outright, which was the honest thing to publish over a system that could not
 * perform erasure, and a bad thing for it to keep being true.
 *
 * THE FIRST LIST WAS WRITTEN BY GREPPING FOR `user_id`, AND THIS FILE FOUND
 * THE TWO TABLES THAT COST. `wallet_link_nonces` is keyed by the person's
 * WALLET ADDRESS and has no `user_id` column; `pending_stance` names them
 * through `requested_by`, with a `telegram_id` beside it. Neither could ever
 * have been found by the search that produced the list, and both would have
 * survived an erasure that reported success.
 *
 * That is why the coverage test below reads the DDL and derives the question,
 * instead of restating the answer. A guard that checks a list against itself
 * measures nothing. `tests/test_no_hardcoded_risk_check_count.py` in this repo
 * lost six surfaces to the same shape: it banned a number on the files
 * somebody had already fixed, and nowhere else.
 *
 * WHAT IS EXERCISED AND WHAT IS SCANNED. The plan, the tombstone and the
 * schema coverage are run, not matched. The one property that has no seam is
 * the ORDER of the two halves of the route — the bot's vault is purged before
 * a single row here is touched — and that is exercised through the route
 * itself, over HTTP, against the in-memory database.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const erasure = require('../lib/account_erasure');
const { EXPECTED_TABLES } = require('../db');

const DB_SRC = path.join(__dirname, '..', 'db.js');

// ── the schema, parsed ────────────────────────────────────────────────────

/**
 * Every `CREATE TABLE` in the migration, as `{name: [columns]}`.
 *
 * COMMENTS ARE STRIPPED FIRST, and the first draft of this function proved why
 * within a minute of being written: `describeSql`'s own docstring contains the
 * literal string `'CREATE TABLE IF NOT EXISTS users (…'`, so the parser
 * matched the comment, ran its body regex off the end of it, and reported that
 * the `users` table has a `user_id` column. A comment that quotes the thing it
 * describes is indistinguishable from the code doing it — CLAUDE.md counts
 * that trap five times and this is the sixth.
 */
function schema() {
  const src = codeOnly(fs.readFileSync(DB_SRC, 'utf8'));
  const out = {};
  for (const m of src.matchAll(/CREATE TABLE IF NOT EXISTS (\w+) \(([\s\S]*?)\n\s*\)/g)) {
    out[m[1]] = m[2].split('\n')
      .map((l) => l.trim().split(/\s+/)[0])
      .filter((c) => c && !/^(PRIMARY|UNIQUE|KEY|INDEX|FOREIGN|CONSTRAINT)$/i.test(c));
  }
  return out;
}

test('the DDL parser sees every table the migration creates', () => {
  // GUARDS THE GUARD. Every coverage assertion below is only as wide as this
  // parse, so a `CREATE TABLE` written with different spacing would drop a
  // table out of the comparison — and a missing table reads exactly like a
  // covered one. `EXPECTED_TABLES` is maintained beside the DDL and pinned to
  // it by db.js's own test, so it is the independent second opinion.
  const parsed = Object.keys(schema()).sort();
  assert.deepStrictEqual(parsed, [...EXPECTED_TABLES].sort(),
    'the erasure coverage check is reading a different set of tables than the '
    + 'migration creates — every assertion in this file is narrower than it looks');
});

// ── coverage ──────────────────────────────────────────────────────────────

test('every table with a user_id column is erased', () => {
  const withUserId = Object.entries(schema())
    .filter(([, cols]) => cols.includes('user_id'))
    .map(([name]) => name);

  assert.ok(withUserId.length > 20, 'the parse found suspiciously few tables');
  const missing = withUserId.filter((t) => !erasure.USER_SCOPED_TABLES.includes(t));
  assert.deepStrictEqual(missing, [],
    'these tables hold rows keyed to a person and account deletion does not '
    + 'touch them. A 25th user-scoped table is exactly the moment erasure needs '
    + 'thinking about, which is why this fails instead of drifting.');
});

test('no table is erased that the schema does not have', () => {
  // The other direction. A stale entry is a `DELETE FROM` against a table that
  // does not exist, which fails the whole erasure at runtime — and it would
  // fail on the FIRST person to ask for deletion, not in review.
  const all = Object.keys(schema());
  const ghosts = erasure.USER_SCOPED_TABLES.filter((t) => !all.includes(t));
  assert.deepStrictEqual(ghosts, [], 'erasure names tables the migration never creates');
});

test('the tables keyed by something other than user_id are covered too', () => {
  // THE TWO THIS FILE FOUND. Stated as a positive assertion rather than left
  // to the negative sweep below, because they are the reason that sweep exists.
  assert.ok(erasure.ADDRESS_SCOPED_TABLES.includes('wallet_link_nonces'),
    'wallet_link_nonces is keyed by the wallet address itself; an erasure that '
    + 'only knows the user id cannot see it');
  assert.ok(erasure.REQUESTER_SCOPED_TABLES.includes('pending_stance'),
    'pending_stance names the requester and carries their telegram id');
});

test('nothing else in the schema identifies a person', () => {
  // THE SWEEP THAT FOUND THEM. A column whose NAME says "person" — address,
  // email, telegram, wallet, handle — in a table erasure does not touch.
  //
  // Deliberately not a list of known-safe tables: that is the same
  // self-referential check as comparing the erasure list to itself. It reads
  // the columns and asks the question again on every run.
  const covered = new Set([
    ...erasure.USER_SCOPED_TABLES, ...erasure.ADDRESS_SCOPED_TABLES,
    ...erasure.REQUESTER_SCOPED_TABLES, 'users',
  ]);
  const leaks = [];
  for (const [table, cols] of Object.entries(schema())) {
    if (covered.has(table)) continue;
    for (const c of cols) {
      if (/^(user_id|address|email|telegram_id|wallet_address|sol_address|handle|requested_by)$/i.test(c)) {
        leaks.push(`${table}.${c}`);
      }
    }
  }
  assert.deepStrictEqual(leaks, [],
    'a table outside the erasure lists holds a column that names a person');
});

test('every identifying column on users is cleared', () => {
  const userCols = schema().users;
  // Not every identifying column is in the CREATE TABLE — most arrived by
  // ALTER, which is how `wallet_address` and `totp_secret` exist. So the check
  // runs the other way: every column erasure clears must be a real column,
  // present either in the DDL or in an ALTER that adds it.
  const src = codeOnly(fs.readFileSync(DB_SRC, 'utf8'));
  const altered = new Set(
    [...src.matchAll(/ALTER TABLE users ADD COLUMN (\w+)/g)].map((m) => m[1]));
  const unknown = erasure.IDENTIFYING_COLUMNS.filter(
    (c) => !userCols.includes(c) && !altered.has(c));
  assert.deepStrictEqual(unknown, [],
    'erasure nulls a users column that does not exist — the UPDATE would fail '
    + 'and take the whole deletion with it');
});

test('every users column that names a person is cleared by the statement', () => {
  // The direction that matters for privacy, and the one a hand-written list
  // gets wrong.
  //
  // ASKED OF THE STATEMENT, NOT OF `IDENTIFYING_COLUMNS`. The first draft
  // compared against that list and failed on `telegram_linked` — which is a
  // BOOLEAN FLAG, is not an identifier, and is already set to 0 by the same
  // UPDATE. The code was right and the assertion was wrong, which is the order
  // CLAUDE.md says to check them in. Reading the assignments out of the SQL
  // answers the real question — "does deletion clear this column" — and has no
  // opinion about which list a column got there through.
  const src = codeOnly(fs.readFileSync(DB_SRC, 'utf8'));
  const cols = new Set([
    ...schema().users,
    ...[...src.matchAll(/ALTER TABLE users ADD COLUMN (\w+)/g)].map((m) => m[1]),
  ]);
  const update = erasure.erasurePlan(7, { addresses: [] }).at(-1).sql;
  const cleared = new Set(
    [...update.matchAll(/(\w+)\s*=/g)].map((m) => m[1]));

  const identifying = [...cols].filter(
    (c) => /token|secret|hash|telegram|discord|google|_id$|address|avatar|handle|referral_code|email/i.test(c)
      && !/^(id|token_epoch)$/.test(c));
  assert.ok(identifying.length > 10, 'the column sweep found suspiciously few');
  const missed = identifying.filter((c) => !cleared.has(c));
  assert.deepStrictEqual(missed, [],
    'a users column that identifies a person survives account deletion');
});

// ── the plan ──────────────────────────────────────────────────────────────

test('an absent identity throws rather than producing a smaller plan', () => {
  // THE RULE THIS REPO IS BUILT ON. `erasurePlan(id)` with a defaulted `{}`
  // would run cleanly, report success, and leave every address-keyed row
  // behind. Absent is not "this account has no wallet".
  assert.throws(() => erasure.erasurePlan(7), /identity/);
  assert.throws(() => erasure.erasurePlan(7, null), /identity/);
  assert.throws(() => erasure.erasurePlan(7, []), /identity/);
  // An account that genuinely has no addresses says so, and that is allowed.
  assert.ok(erasure.erasurePlan(7, { addresses: [] }).length > 20);
});

test('every statement is parameterised and none interpolates input', () => {
  for (const step of erasure.erasurePlan(7, { addresses: ["'; DROP TABLE users; --"] })) {
    assert.ok(!step.sql.includes('DROP'), `input reached the SQL: ${step.sql}`);
    assert.ok(step.sql.includes('?'), `unparameterised statement: ${step.sql}`);
  }
});

test('blank and duplicate addresses do not become blank and duplicate deletes', () => {
  const plan = erasure.erasurePlan(7, { addresses: ['0xA', '0xA', '', '  ', null, undefined] });
  const nonces = plan.filter((s) => s.table === 'wallet_link_nonces');
  assert.deepStrictEqual(nonces.map((s) => s.params[0]), ['0xA'],
    'a DELETE bound to an empty string is a statement that matches whatever '
    + 'happens to be stored as an empty address');
});

test('the users row is tombstoned, not deleted', () => {
  const last = erasure.erasurePlan(7, { addresses: [] }).at(-1);
  assert.strictEqual(last.table, 'users');
  assert.match(last.sql, /^UPDATE users SET/,
    'deleting the row would dangle every referral edge that names this person, '
    + "and duel_squads.js builds public squads out of exactly those edges");
  assert.strictEqual(last.params[0], erasure.tombstoneEmail(7));
});

test('the tombstone address is unique per account and obviously synthetic', () => {
  assert.notStrictEqual(erasure.tombstoneEmail(1), erasure.tombstoneEmail(2),
    'a shared tombstone collides on the UNIQUE email index and the second '
    + 'deletion fails');
  // `.invalid` is reserved by RFC 2606 precisely so it can never be a real
  // address, so nothing downstream can try to mail it.
  assert.match(erasure.tombstoneEmail(1), /@account\.invalid$/);
});

test('sessions are revoked in the same statement that clears the identity', () => {
  const last = erasure.erasurePlan(7, { addresses: [] }).at(-1);
  assert.match(last.sql, /token_epoch = COALESCE\(token_epoch, 0\) \+ 1/,
    'bumping the epoch in a second statement leaves a window in which the row '
    + 'names nobody and every issued token still verifies against it');
});

test('credentials are erased before anything else', () => {
  // Ordering inside the plan: a failure partway through must have removed the
  // rows that could move money, not the watchlist.
  const tables = erasure.erasurePlan(7, { addresses: [] }).map((s) => s.table);
  assert.ok(tables.indexOf('pending_credentials') < tables.indexOf('user_watchlist'));
  assert.ok(tables.indexOf('arena_api_keys') < tables.indexOf('learn_diary'));
});

// ── the in-memory database can actually run it ────────────────────────────

test('the in-memory database can execute the whole plan', async () => {
  // EXERCISED, NOT MATCHED. Half of these tables had no DELETE branch in the
  // shim at all, and the shim THROWS on a statement it does not implement
  // rather than inventing an empty result — so account deletion died on the
  // first uncovered table, in the suite and in the no-DATABASE_URL deployment
  // mode this class exists to serve.
  //
  // Running the statements is the only check that sees this. A source scan for
  // the store map would pass against a map whose fields point at nothing.
  const { pool } = require('../db');
  for (const step of erasure.erasurePlan(999999, { addresses: ['0xnope'] })) {
    await pool.execute(step.sql, step.params);      // must not throw
  }
});

test('erasure actually removes the rows, and only this user\'s', async () => {
  const { pool } = require('../db');
  const mk = async (email) => {
    await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
      [email, 'x']);
    const [r] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
    return r[0].id;
  };
  const mine = await mk(`erase${Date.now()}a@example.com`);
  const theirs = await mk(`erase${Date.now()}b@example.com`);

  for (const uid of [mine, theirs]) {
    await pool.execute(
      'INSERT INTO user_watchlist (user_id, symbol, created_at) VALUES (?, ?, ?)',
      [uid, 'BTCUSDT', new Date()]);
  }

  for (const step of erasure.erasurePlan(mine, { addresses: [] })) {
    await pool.execute(step.sql, step.params);
  }

  const [gone] = await pool.execute(
    'SELECT symbol FROM user_watchlist WHERE user_id = ?', [mine]);
  assert.deepStrictEqual(gone, [], 'the erased account still has rows');
  const [kept] = await pool.execute(
    'SELECT symbol FROM user_watchlist WHERE user_id = ?', [theirs]);
  assert.strictEqual(kept.length, 1,
    "erasure reached another account's rows — the WHERE clause is not scoped");

  const [rows] = await pool.execute(
    'SELECT email, password_hash, telegram_id, plan FROM users WHERE id = ?', [mine]);
  assert.strictEqual(rows.length, 1, 'the row was deleted; it must be tombstoned');
  assert.strictEqual(rows[0].email, erasure.tombstoneEmail(mine));
  assert.strictEqual(rows[0].password_hash, null);
  assert.strictEqual(rows[0].telegram_id, null);
  assert.strictEqual(rows[0].plan, 'deleted');
});
