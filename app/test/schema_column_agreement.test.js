'use strict';
/**
 * Every column the app reads or writes is a column the schema declares.
 *
 * THE WHOLE SUITE RUNS ON A DATABASE THAT IS NOT THE DATABASE. `migrate()`
 * returns early without `DATABASE_URL`, so all 2900-odd tests exercise
 * MemoryDB, which matches SQL with regexes. A query naming a column no
 * `CREATE TABLE` declares is fine there and `ER_BAD_FIELD_ERROR` in
 * production — passing every check on the way out.
 *
 * That is not hypothetical here. `migration_ddl.test.js` was written after a
 * missing opening parenthesis meant the migration "had NEVER succeeded against
 * a real MySQL server", surviving because nothing executed it. This is the same
 * gap one step along: that file proves the DDL would PARSE, and nothing yet
 * proves the queries AGREE with it.
 *
 * MEASURED FIRST, and it is a clean negative — 35 tables, 45 INSERT sites, 73
 * UPDATE sites and 233 single-table SELECTs all agree today. The guard exists
 * because nothing was holding them there, and because the only thing that could
 * catch a drift is a MySQL server no test run has.
 *
 * WHAT THIS DOES NOT COVER, stated because a checker that reports a subset as
 * the whole is the defect this repo spends most of its guards preventing:
 *
 *   - `SELECT *`, and multi-table joins — a bare name in a join is ambiguous
 *     about which table owns it, and guessing would produce false failures
 *     against correct code.
 *   - qualified names (`t.col`), function calls and aliases, skipped as
 *     expressions rather than columns.
 *   - SQL assembled at runtime from variables.
 *   - column TYPES, nullability, lengths and indexes. A `VARCHAR(64)` handed a
 *     128-character value is a production failure this cannot see.
 *
 * The counts are asserted with floors below, so a refactor that quietly stops
 * the extractors matching fails here instead of reporting agreement over
 * nothing — which is how a checker like this actually breaks.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');

/** table -> Set(column), from the CREATE TABLE + ALTER TABLE ADD in db.js. */
function schema() {
  // Comments stripped first: db.js describes its own tables in prose above
  // them, and a comment quoting a dropped column is indistinguishable from the
  // DDL declaring one.
  const src = codeOnly(fs.readFileSync(path.join(APP, 'db.js'), 'utf8'));
  const tables = new Map();
  const re = /CREATE TABLE IF NOT EXISTS\s+`?(\w+)`?\s*\(([\s\S]*?)\n\s*\)/gi;
  let m;
  while ((m = re.exec(src)) !== null) {
    const cols = new Set();
    for (const part of splitTopLevel(m[2])) {
      const line = part.trim();
      if (!line) continue;
      if (/^(PRIMARY|UNIQUE|KEY|INDEX|CONSTRAINT|FOREIGN|FULLTEXT)\b/i.test(line)) continue;
      const c = line.match(/^`?(\w+)`?/);
      if (c) cols.add(c[1].toLowerCase());
    }
    tables.set(m[1].toLowerCase(), cols);
  }
  // Back-fills on pre-existing deployments are part of the schema.
  const alter = /ALTER TABLE\s+`?(\w+)`?\s+ADD (?:COLUMN )?(?:IF NOT EXISTS )?`?(\w+)`?/gi;
  let a;
  while ((a = alter.exec(src)) !== null) {
    const t = a[1].toLowerCase();
    if (tables.has(t)) tables.get(t).add(a[2].toLowerCase());
  }
  return tables;
}

/** Split a column definition list on commas that are not inside DECIMAL(18,8). */
function splitTopLevel(body) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of body) {
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) { out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}

/** Every non-test JS file in the app, comments stripped, as [relPath, code]. */
function sources() {
  const out = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'test' || e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith('.js')) {
        out.push([path.relative(APP, p), codeOnly(fs.readFileSync(p, 'utf8'))]);
      }
    }
  }(APP));
  return out;
}

/** A bare, unqualified column name — anything else is an expression. */
function bareColumn(raw) {
  const c = raw.trim().replace(/`/g, '').toLowerCase();
  return c && !/[^a-z0-9_]/.test(c) ? c : null;
}

/**
 * Walk every checkable statement, calling back with each (file, table, column).
 * Returns the per-kind site counts so coverage can be asserted, not assumed.
 */
function eachColumnUse(files, tables, onUse) {
  const counts = { insert: 0, update: 0, select: 0 };
  for (const [file, src] of files) {
    let m;

    const ins = /INSERT\s+(?:IGNORE\s+)?INTO\s+`?(\w+)`?\s*\(([^)]*)\)/gi;
    while ((m = ins.exec(src)) !== null) {
      const t = m[1].toLowerCase();
      if (!tables.has(t)) continue;
      counts.insert += 1;
      for (const raw of m[2].split(',')) {
        const c = bareColumn(raw);
        if (c) onUse(file, t, c, 'INSERT');
      }
    }

    // Bounded to 400 chars and stopped at WHERE or the end of the template so a
    // long statement cannot bleed into the next one.
    const upd = /UPDATE\s+`?(\w+)`?\s+SET\s+([\s\S]{0,400}?)(?:\bWHERE\b|`|'|")/gi;
    while ((m = upd.exec(src)) !== null) {
      const t = m[1].toLowerCase();
      if (!tables.has(t)) continue;
      counts.update += 1;
      const assign = /(?:^|,)\s*`?(\w+)`?\s*=/g;
      let a;
      while ((a = assign.exec(m[2])) !== null) onUse(file, t, a[1].toLowerCase(), 'UPDATE');
    }

    // Single-table only: the FROM must be followed by whitespace or the end of
    // the literal, so `FROM a JOIN b` is skipped rather than half-checked.
    const sel = /SELECT\s+((?!\*)[^;`'"]{1,300}?)\s+FROM\s+`?(\w+)`?(\s|`|'|")/gi;
    while ((m = sel.exec(src)) !== null) {
      const t = m[2].toLowerCase();
      if (!tables.has(t)) continue;
      counts.select += 1;
      for (const raw of m[1].split(',')) {
        const c = bareColumn(raw);
        if (c) onUse(file, t, c, 'SELECT');
      }
    }
  }
  return counts;
}

function findMismatches(files, tables) {
  const bad = [];
  eachColumnUse(files, tables, (file, t, c, kind) => {
    if (!tables.get(t).has(c)) bad.push(`${kind} ${t}.${c}  (${file})`);
  });
  return [...new Set(bad)].sort();
}

// ── The checker must be shown to work before its verdict means anything ────

test('the schema parser sees the real tables and columns', () => {
  const tables = schema();
  assert.ok(tables.size >= 25,
    `only ${tables.size} tables parsed out of db.js — the DDL moved and this `
    + 'file is now agreeing with an empty schema');
  const users = tables.get('users');
  assert.ok(users, 'the users table is gone from the parsed schema');
  for (const c of ['id', 'email', 'telegram_id']) {
    assert.ok(users.has(c), `users.${c} was not parsed`);
  }
});

test('an ALTER TABLE back-fill counts as part of the schema', () => {
  // db.js adds columns to already-deployed tables this way. Missing them would
  // make this file accuse correct queries — the shape CLAUDE.md warns about,
  // where a checker manufactures the accusation it exists to prevent.
  const users = schema().get('users');
  const src = codeOnly(fs.readFileSync(path.join(APP, 'db.js'), 'utf8'));
  const added = [...src.matchAll(/ALTER TABLE\s+`?users`?\s+ADD (?:COLUMN )?(?:IF NOT EXISTS )?`?(\w+)`?/gi)]
    .map((m) => m[1].toLowerCase());
  assert.ok(added.length > 0, 'no users back-fill found — the anchor moved');
  for (const c of added) assert.ok(users.has(c), `back-filled users.${c} is missing`);
});

test('the statement extractors reach real query sites', () => {
  const counts = eachColumnUse(sources(), schema(), () => {});
  // Floors well under today's 45 / 73 / 233. A refactor that stops these
  // matching must fail here rather than report agreement over nothing.
  assert.ok(counts.insert >= 30, `only ${counts.insert} INSERT sites seen`);
  assert.ok(counts.update >= 50, `only ${counts.update} UPDATE sites seen`);
  assert.ok(counts.select >= 150, `only ${counts.select} SELECT sites seen`);
});

test('a column the schema does not declare is actually detected', () => {
  // Anti-vacuity: every assertion below is satisfied by an extractor that
  // finds nothing. This one is not.
  const tables = schema();
  const planted = [
    ['planted.js', "pool.execute('INSERT INTO users (email, no_such_column) VALUES (?,?)')"],
    ['planted.js', "pool.execute('UPDATE users SET no_such_column = ? WHERE id = ?')"],
    ['planted.js', "pool.execute('SELECT id, no_such_column FROM users WHERE id = ?')"],
  ];
  const found = findMismatches(planted, tables);
  assert.equal(found.length, 3, `expected all three kinds caught, got ${found.join(' | ')}`);
  for (const kind of ['INSERT', 'UPDATE', 'SELECT']) {
    assert.ok(found.some((f) => f.startsWith(kind)), `${kind} not detected`);
  }
});

// ── The verdict ───────────────────────────────────────────────────────────

test('no query names a column the schema does not declare', () => {
  const bad = findMismatches(sources(), schema());
  assert.deepStrictEqual(bad, [],
    'These columns are read or written but never declared in db.js. MemoryDB '
    + 'answers happily and every test passes; MySQL raises ER_BAD_FIELD_ERROR '
    + 'the first time the query runs in production:\n  ' + bad.join('\n  '));
});
