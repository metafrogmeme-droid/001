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
