'use strict';

/**
 * The migration DDL must be well-formed — checkable without a database.
 *
 * Production, 2026-07-28: every migration failed with ER_PARSE_ERROR, and it
 * took most of a day to reach the cause because the error named no statement.
 * When it finally did, the fault was a missing opening parenthesis:
 *
 *     CREATE TABLE IF NOT EXISTS arena_seasons
 *       id INT AUTO_INCREMENT PRIMARY KEY,
 *
 * A plain syntax error, present since long before that day, which means this
 * migration had NEVER succeeded against a real MySQL server.
 *
 * It survived because nothing ever executed it. migrate() returns early
 * without DATABASE_URL, so the entire test suite — every run, every CI
 * check — exercised the in-memory path and skipped all 32 statements. The
 * DDL had no coverage of any kind.
 *
 * These checks need no database. They read the statements as text and assert
 * the structural properties a parser would reject, which is exactly the class
 * of fault that reached production: unbalanced parentheses, a missing opening
 * paren, a trailing comma before the close. Cheap, and they would have caught
 * it the day it was written.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');

/** Every CREATE TABLE the migration runs, as raw SQL. */
function statements() {
  const out = [];
  const re = /pool\.(?:query|execute)\(\s*`(\s*CREATE TABLE[^`]*)`/gi;
  let m;
  while ((m = re.exec(SRC)) !== null) {
    const sql = m[1];
    const name = /CREATE TABLE(?:\s+IF NOT EXISTS)?\s+`?(\w+)`?/i.exec(sql);
    out.push({ name: name ? name[1] : '<unnamed>', sql });
  }
  return out;
}

test('the migration has statements to check', () => {
  const all = statements();
  assert.ok(all.length >= 30,
    `expected the full migration, found ${all.length} — if this drops, the `
    + 'matcher has drifted and these checks are silently covering nothing');
});

test('every CREATE TABLE opens its column list', () => {
  // The exact production fault: the table name was followed by a newline and
  // the first column, with no "(" between them.
  for (const { name, sql } of statements()) {
    assert.match(sql, /CREATE TABLE(?:\s+IF NOT EXISTS)?\s+`?\w+`?\s*\(/i,
      `${name}: no opening parenthesis after the table name`);
  }
});

test('every CREATE TABLE balances its parentheses', () => {
  for (const { name, sql } of statements()) {
    const open = (sql.match(/\(/g) || []).length;
    const close = (sql.match(/\)/g) || []).length;
    assert.equal(open, close,
      `${name}: ${open} "(" vs ${close} ")" — a parser rejects this`);
  }
});

test('no CREATE TABLE has a trailing comma before its closing paren', () => {
  for (const { name, sql } of statements()) {
    assert.ok(!/,\s*\)\s*$/.test(sql.trim()),
      `${name}: trailing comma before the closing parenthesis`);
  }
});

test('every CREATE TABLE names a table and declares at least one column', () => {
  for (const { name, sql } of statements()) {
    assert.notEqual(name, '<unnamed>', 'a CREATE TABLE with no parseable name');
    const body = sql.slice(sql.indexOf('('));
    assert.ok(/\w+\s+(?:VARCHAR|CHAR|TEXT|INT|BIGINT|SMALLINT|TINYINT|DECIMAL|FLOAT|DOUBLE|BOOLEAN|BOOL|DATE|DATETIME|TIMESTAMP|JSON|BLOB|ENUM)\b/i.test(body),
      `${name}: no column definition found`);
  }
});

test('DDL runs on the text protocol, and only DDL was moved there', () => {
  // execute() is the binary prepared-statement path; server support for
  // preparing DDL is not universal. Meanwhile everything carrying a user value
  // must STAY prepared — that is the injection boundary.
  assert.equal((SRC.match(/pool\.execute\(\s*`\s*CREATE\b/gi) || []).length, 0,
    'no DDL may be prepared');
  assert.equal((SRC.match(/pool\.query\(\s*`[^`]*\?[^`]*`/g) || []).length, 0,
    'no placeholder query may have been moved off execute()');
});

test('the arena_seasons regression specifically', () => {
  // Named, because this is the one that reached production and cost a day.
  const s = statements().find((x) => x.name === 'arena_seasons');
  assert.ok(s, 'arena_seasons must still be in the migration');
  assert.match(s.sql, /arena_seasons\s*\(/,
    'arena_seasons lost its opening parenthesis again');
});

// ── the fast path ─────────────────────────────────────────────────────────

test('EXPECTED_TABLES matches the DDL exactly', () => {
  // These two must not drift. A new CREATE TABLE without a new entry here
  // would make the fast path skip it forever — the schema would silently stop
  // being created on any database that already has the older tables.
  const { EXPECTED_TABLES } = require('../db');
  const inDdl = [...new Set(statements().map((s) => s.name))];
  assert.deepEqual([...EXPECTED_TABLES].sort(), inDdl.sort(),
    'EXPECTED_TABLES and the migration have diverged');
});

test('the fast path requires EVERY table, not merely some', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  const fn = src.slice(src.indexOf('async function schemaIsCurrent'),
    src.indexOf('async function migrate()'));
  assert.match(fn, /EXPECTED_TABLES\.every\(/,
    'a PARTIAL schema must still run the full DDL');
  assert.ok(!/\.some\(/.test(fn), 'some() would skip a half-created schema');
});

// THREE ANSWERS (2026-10-11). An unanswered check used to read as "a table is
// missing", which ran the 33 statements: measured live, an instance whose first
// database contact was slow spent its first attempt there until the 180 s cap
// and was not ready for 3.5 minutes. It now fails the attempt (retried a second
// later on a fresh connection), and the DDL still runs after three in a row.
test('the database answering decides the path: every table present is fast, anything missing runs the DDL', async () => {
  const db = require('../db');
  db._resetSchemaPath();
  assert.equal(await db.schemaPath(async () => true), 'fast');
  assert.equal(await db.schemaPath(async () => false), 'ddl');
});

test('an unanswered check never skips the migration: it fails the attempt, and the third in a row runs the DDL', async () => {
  const db = require('../db');
  db._resetSchemaPath();
  const unanswered = async () => { const e = new Error('x'); e.code = 'ETIMEDOUT'; throw e; };
  const n = db.UNREADABLE_CHECKS_BEFORE_DDL;
  assert.ok(n >= 2, 'one unanswered check must not run the DDL');
  for (let i = 1; i < n; i += 1) {
    await assert.rejects(db.schemaPath(unanswered), /x/, `unanswered check ${i} must fail the attempt`);
  }
  assert.equal(await db.schemaPath(unanswered), 'ddl', `unanswered check ${n} must run the DDL`);
  // and the count starts again: the next unanswered check fails the attempt
  await assert.rejects(db.schemaPath(unanswered));
});

test('an answered check starts the unanswered count again', async () => {
  const db = require('../db');
  db._resetSchemaPath();
  const unanswered = async () => { throw new Error('x'); };
  for (let i = 1; i < db.UNREADABLE_CHECKS_BEFORE_DDL; i += 1) await assert.rejects(db.schemaPath(unanswered));
  assert.equal(await db.schemaPath(async () => true), 'fast');
  await assert.rejects(db.schemaPath(unanswered), /x/,
    'after an answer, one more unanswered check is the first of a new run, not the last of the old one');
});

test('the check carries its own time limit, shorter than any migration attempt', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  const fn = src.slice(src.indexOf('async function schemaIsCurrent'), src.indexOf('async function schemaPath'));
  assert.match(fn, /timeout:\s*SCHEMA_CHECK_TIMEOUT_MS/, 'a connection that answers nothing would hold the instance not-ready');
  const ms = Number((src.match(/const SCHEMA_CHECK_TIMEOUT_MS = (\d+);/) || [])[1]);
  assert.ok(ms > 0 && ms <= 15000, `${ms} ms`);
  assert.ok(!/catch\s*\(/.test(fn), 'the check must not turn an unanswered query into an answer');
});

test('the check reads only table names — no schema or credential detail', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  const fn = src.slice(src.indexOf('async function schemaIsCurrent'),
    src.indexOf('async function migrate()'));
  assert.match(fn, /information_schema\.TABLES/);
  assert.match(fn, /TABLE_SCHEMA = DATABASE\(\)/,
    'scoped to this database, never a server-wide listing');
});

test('migrate consults the fast path before running any DDL', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  const body = src.slice(src.indexOf('async function migrate()'));
  const guard = body.indexOf('await schemaPath()');
  const firstDdl = body.indexOf('CREATE TABLE IF NOT EXISTS');
  assert.ok(guard > 0 && guard < firstDdl,
    'the check must come before the statements it exists to avoid');
});

// ── migrate() itself, against a database that records what it is sent ─────

/**
 * Run db.migrate() in a child with DATABASE_URL set (so the MySQL path runs)
 * and pool.query replaced by `answer`, a function body taking the statement
 * text. Returns what each call to migrate() did and the statements sent.
 */
function migrateAgainst(answer, calls = 1) {
  const { execFileSync } = require('node:child_process');
  const script = `
    const db = require(${JSON.stringify(path.join(__dirname, '..', 'db.js'))});
    const sent = [];
    db.pool.query = async (q) => {
      const sql = typeof q === 'string' ? q : q.sql;
      const kind = sql.includes('information_schema.TABLES') ? 'CHECK'
        : sql.includes('information_schema.COLUMNS')
          ? (sql.includes("TABLE_NAME = 'users'") ? 'USER_COLUMNS'
            : sql.includes("TABLE_NAME = 'duel_rounds'") ? 'DUEL_ROUND_COLUMNS'
              : sql.includes("COLUMN_NAME = 'pick'") ? 'DUEL_PICK_WIDTH'
                : sql.includes("COLUMN_NAME = 'entry_price'") ? 'DUEL_PICK_ENTRY_NULL'
                  : sql.includes("TABLE_NAME = 'duel_picks'") ? 'DUEL_PICK_COLUMNS' : 'SIGNAL_COLUMNS')
          : sql.startsWith('ALTER TABLE signals') ? 'ALTER_SIGNALS'
            : sql.startsWith('ALTER TABLE users') ? 'ALTER_USERS'
              : sql.startsWith('ALTER TABLE duel_rounds') ? 'ALTER_DUEL_ROUNDS'
                : sql.startsWith('ALTER TABLE duel_picks MODIFY COLUMN pick ') ? 'WIDEN_DUEL_PICK'
                : sql.startsWith('ALTER TABLE duel_picks MODIFY COLUMN entry_price') ? 'RELAX_DUEL_ENTRY'
                  : sql.startsWith('ALTER TABLE duel_picks') ? 'ALTER_DUEL_PICKS'
                    : sql.startsWith('CREATE UNIQUE INDEX idx_users') ? 'INDEX_USERS'
                      : (sql.match(/CREATE TABLE IF NOT EXISTS (\\w+)/) || [, 'OTHER'])[1];
      sent.push(kind);
      return (${answer})(sql);
    };
    db.pool.execute = async () => [{ affectedRows: 0 }, []];
    (async () => {
      const outcomes = [];
      for (let i = 0; i < ${calls}; i += 1) {
        try { outcomes.push(await db.migrate()); } catch (e) { outcomes.push('threw:' + (e.code || e.message)); }
      }
      process.stdout.write(JSON.stringify({ outcomes, sent }));
    })();`;
  const out = execFileSync(process.execPath, ['-e', script], {
    env: { ...process.env, DATABASE_URL: 'mysql://u:p@127.0.0.1:9/never', JWT_SECRET: 'j'.repeat(48) },
    encoding: 'utf8',
  });
  return JSON.parse(out.slice(out.indexOf('{')));
}

/**
 * An answer function: every table there, and the named column sets as given.
 * The duel tables answer complete by default (every column, `pick` at the
 * width the categories need); `duel` narrows them for the tests below.
 */
function fastAnswer(signalCols, userCols, duel = {}) {
  const db = require('../db');
  const tables = JSON.stringify(db.EXPECTED_TABLES.map((t) => ({ t })));
  const sig = JSON.stringify(signalCols.map((c) => ({ c })));
  const usr = JSON.stringify(userCols.map((c) => ({ c })));
  const rounds = JSON.stringify((duel.rounds || Object.keys(db.REQUIRED_DUEL_ROUND_COLUMNS)).map((c) => ({ c })));
  const picks = JSON.stringify((duel.picks || Object.keys(db.REQUIRED_DUEL_PICK_COLUMNS)).map((c) => ({ c })));
  const width = JSON.stringify(duel.width === null ? [] : [{ n: duel.width == null ? db.DUEL_PICK_WIDTH : duel.width }]);
  const nullable = JSON.stringify(duel.entryNullable === null ? [] : [{ n: duel.entryNullable == null ? 'YES' : duel.entryNullable }]);
  return `async (sql) => sql.includes('information_schema.TABLES') ? [${tables}]
    : sql.includes('information_schema.COLUMNS')
      ? (sql.includes("TABLE_NAME = 'users'") ? [${usr}]
        : sql.includes("TABLE_NAME = 'duel_rounds'") ? [${rounds}]
          : sql.includes("COLUMN_NAME = 'pick'") ? [${width}]
            : sql.includes("COLUMN_NAME = 'entry_price'") ? [${nullable}]
              : sql.includes("TABLE_NAME = 'duel_picks'") ? [${picks}] : [${sig}])
      : [[], []]`;
}
/** The fast path's reads, in order, when every table and column is present. */
const FAST_READS = ['CHECK', 'SIGNAL_COLUMNS', 'USER_COLUMNS', 'DUEL_ROUND_COLUMNS', 'DUEL_PICK_COLUMNS',
  'DUEL_PICK_WIDTH', 'DUEL_PICK_ENTRY_NULL'];

test('migrate with every table and required column present skips full DDL', () => {
  const { REQUIRED_SIGNAL_COLUMNS, REQUIRED_USER_COLUMNS } = require('../db');
  const r = migrateAgainst(fastAnswer(
    Object.keys(REQUIRED_SIGNAL_COLUMNS), Object.keys(REQUIRED_USER_COLUMNS)));
  assert.deepEqual(r.outcomes, ['fast']);
  assert.deepEqual(r.sent, FAST_READS);
});

test('the fast path adds missing signal columns instead of skipping them forever', () => {
  const { REQUIRED_USER_COLUMNS } = require('../db');
  const r = migrateAgainst(fastAnswer([], Object.keys(REQUIRED_USER_COLUMNS)));
  assert.deepEqual(r.outcomes, ['fast']);
  assert.deepEqual(r.sent, ['CHECK', 'SIGNAL_COLUMNS', 'ALTER_SIGNALS', 'USER_COLUMNS',
    'DUEL_ROUND_COLUMNS', 'DUEL_PICK_COLUMNS', 'DUEL_PICK_WIDTH', 'DUEL_PICK_ENTRY_NULL']);
});

test('the fast path adds a missing user column, with its unique index', () => {
  // The farcaster_fid lesson: a column added on its own never ran anywhere
  // the schema was already current, because the fast path skips every DDL.
  const { REQUIRED_SIGNAL_COLUMNS } = require('../db');
  const r = migrateAgainst(fastAnswer(Object.keys(REQUIRED_SIGNAL_COLUMNS), []));
  assert.deepEqual(r.outcomes, ['fast']);
  assert.deepEqual(r.sent, ['CHECK', 'SIGNAL_COLUMNS', 'USER_COLUMNS', 'ALTER_USERS', 'INDEX_USERS',
    'DUEL_ROUND_COLUMNS', 'DUEL_PICK_COLUMNS', 'DUEL_PICK_WIDTH', 'DUEL_PICK_ENTRY_NULL']);
});

test('the fast path adds the duel categories\' columns and widens pick, and leaves a current table alone', () => {
  // The net_rr lesson a third time: every production database already had
  // both duel tables, so the ALTERs below the fast path would never have run
  // there and a mover's 'second' would have met a 5-character column.
  const db = require('../db');
  const r = migrateAgainst(fastAnswer(Object.keys(db.REQUIRED_SIGNAL_COLUMNS), Object.keys(db.REQUIRED_USER_COLUMNS),
    { rounds: [], picks: ['entry_price_b'], width: 5 }));
  assert.deepEqual(r.outcomes, ['fast']);
  assert.deepEqual(r.sent, ['CHECK', 'SIGNAL_COLUMNS', 'USER_COLUMNS',
    'DUEL_ROUND_COLUMNS', 'ALTER_DUEL_ROUNDS', 'DUEL_PICK_COLUMNS', 'ALTER_DUEL_PICKS',
    'DUEL_PICK_WIDTH', 'WIDEN_DUEL_PICK', 'DUEL_PICK_ENTRY_NULL']);
  // Already wide enough: no rewrite of the table.
  const wide = migrateAgainst(fastAnswer(Object.keys(db.REQUIRED_SIGNAL_COLUMNS), Object.keys(db.REQUIRED_USER_COLUMNS),
    { width: 16 }));
  assert.deepEqual(wide.sent, FAST_READS);
  // A width that could not be read is left alone, never widened on a guess.
  const unread = migrateAgainst(fastAnswer(Object.keys(db.REQUIRED_SIGNAL_COLUMNS), Object.keys(db.REQUIRED_USER_COLUMNS),
    { width: null }));
  assert.deepEqual(unread.outcomes, ['fast']);
  assert.deepEqual(unread.sent, FAST_READS);
  // A football call has no entry: entry_price must take NULL. A NOT NULL
  // column is relaxed; a nullable one and an unreadable answer are left alone.
  const strict = migrateAgainst(fastAnswer(Object.keys(db.REQUIRED_SIGNAL_COLUMNS), Object.keys(db.REQUIRED_USER_COLUMNS),
    { entryNullable: 'NO' }));
  assert.deepEqual(strict.sent, FAST_READS.concat(['RELAX_DUEL_ENTRY']));
  const unreadNull = migrateAgainst(fastAnswer(Object.keys(db.REQUIRED_SIGNAL_COLUMNS), Object.keys(db.REQUIRED_USER_COLUMNS),
    { entryNullable: null }));
  assert.deepEqual(unreadNull.sent, FAST_READS);
});

test('migrate with a table missing runs the DDL', () => {
  const { EXPECTED_TABLES } = require('../db');
  const some = EXPECTED_TABLES.slice(1).map((t) => ({ t }));
  const r = migrateAgainst(`async (sql) => sql.includes('information_schema') ? [${JSON.stringify(some)}] : [[], []]`);
  assert.deepEqual(r.outcomes, ['ddl']);
  assert.ok(r.sent.includes(EXPECTED_TABLES[0]), `the missing table was not created: ${r.sent.join(',')}`);
});

test('migrate whose check is not answered fails the attempt without DDL, and the third runs it', () => {
  const r = migrateAgainst(`async (sql) => {
    if (sql.includes('information_schema')) { const e = new Error('no answer'); e.code = 'PROTOCOL_SEQUENCE_TIMEOUT'; throw e; }
    return [[], []];
  }`, 3);
  const { UNREADABLE_CHECKS_BEFORE_DDL: n } = require('../db');
  assert.equal(n, 3, 'this test is written for three');
  assert.deepEqual(r.outcomes, ['threw:PROTOCOL_SEQUENCE_TIMEOUT', 'threw:PROTOCOL_SEQUENCE_TIMEOUT', 'ddl']);
  const ddlAt = r.sent.findIndex((s) => s !== 'CHECK');
  assert.equal(ddlAt, 3, `DDL was sent before the third unanswered check: ${r.sent.slice(0, 5).join(',')}`);
});
