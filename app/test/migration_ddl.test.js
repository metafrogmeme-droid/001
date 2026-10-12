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
      sent.push(sql.includes('information_schema') ? 'CHECK' : (sql.match(/CREATE TABLE IF NOT EXISTS (\\w+)/) || [, 'OTHER'])[1]);
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

test('migrate with every table present sends the check and nothing else', () => {
  const { EXPECTED_TABLES } = require('../db');
  const r = migrateAgainst(`async (sql) => sql.includes('information_schema')
    ? [${JSON.stringify(EXPECTED_TABLES.map((t) => ({ t })))}] : [[], []]`);
  assert.deepEqual(r.outcomes, ['fast']);
  assert.deepEqual(r.sent, ['CHECK']);
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
