'use strict';
// ER_WRONG_ARGUMENTS: Incorrect arguments to LIMIT
//
// That string, on the operator's screen, is why this file exists. mysql2's
// execute() sends JS numbers as DOUBLE in the binary protocol, and MySQL
// refuses a DOUBLE as a prepared LIMIT argument. So `LIMIT ?` works in the
// in-memory shim, works in every test, and throws on the first request that
// reaches real MySQL.
//
// One of the three `LIMIT ?` statements in this repo sat inside sweepFollows,
// which runs on every GET /api/arena/account once practice-follow is enabled.
// The operator enabled follow — and from that moment their paper account
// 500ed on every load while 1400 tests stayed green. The Arena diagnostic
// (PR-shipped one round earlier) surfaced the driver's own code and message,
// which is the only reason this was a one-round fix instead of another
// guessing game.
//
// Two guarantees, so the class dies rather than the instance:
//   1. No statement anywhere in the app binds a LIMIT parameter.
//   2. The shim REFUSES `LIMIT ?` with the same error production throws.
//      A shim that is more permissive than the database it stands in for is
//      not a test double, it is a blindfold — this is the third shim
//      divergence to cost a real production bug (follow upsert, sol_verified
//      params, now this), and the first where the shim gets to enforce.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = path.join(__dirname, '..');

function jsFilesUnder(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    // public/ is browser JS — no SQL there, but plenty of `x.limit ? a : b`
    // ternaries that a case-insensitive scan would false-positive on.
    if (entry.name === 'node_modules' || entry.name === 'test'
      || entry.name === 'public' || entry.name.startsWith('.')) continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...jsFilesUnder(p));
    else if (entry.name.endsWith('.js')) out.push(p);
  }
  return out;
}

const stripComments = (src) => src.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');

test('no SQL statement in the app binds a LIMIT parameter', () => {
  // Comments legitimately mention the pattern when explaining this very bug,
  // so the scan strips them and looks only at code.
  const offenders = [];
  for (const f of jsFilesUnder(APP)) {
    const src = stripComments(fs.readFileSync(f, 'utf8'));
    if (/LIMIT\s+\?/i.test(src)) offenders.push(path.relative(APP, f));
  }
  assert.deepEqual(offenders, [],
    `LIMIT ? is ER_WRONG_ARGUMENTS on real MySQL — inline a validated integer instead: ${offenders.join(', ')}`);
});

test('the shim refuses a bound LIMIT with the exact production error', async () => {
  // Force the in-memory implementation regardless of environment.
  delete require.cache[require.resolve('../db.js')];
  const prev = process.env.DB_HOST;
  delete process.env.DB_HOST;
  const { pool } = require('../db.js');
  if (prev !== undefined) process.env.DB_HOST = prev;

  await assert.rejects(
    () => pool.execute('SELECT id FROM signals ORDER BY id DESC LIMIT ?', [5]),
    (err) => {
      assert.equal(err.code, 'ER_WRONG_ARGUMENTS',
        'the code is what the diagnostic shows the operator — it must match production');
      assert.match(err.message, /Incorrect arguments to LIMIT/);
      return true;
    });

  // And the legal forms still work — the refusal must not overmatch.
  await assert.doesNotReject(() => pool.execute('SELECT id FROM signals ORDER BY id DESC LIMIT 5'));
  await assert.doesNotReject(() => pool.execute(
    'SELECT id, symbol, direction, stop_loss, take_profit FROM signals WHERE id > ? ORDER BY id ASC LIMIT 5', [0]));
});

test('the follow sweep — the query that took /account down — is fixed at the source', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'arena.js'), 'utf8');
  assert.match(src, /WHERE id > \? ORDER BY id ASC LIMIT 5/,
    'the sweep must inline its LIMIT');
  // Comments in the sweep legitimately name the banned pattern while
  // explaining this bug — strip them before matching.
  const inSweep = stripComments(src.slice(
    src.indexOf('async function sweepFollows'), src.indexOf('async function settleLiquidations')));
  assert.doesNotMatch(inSweep, /LIMIT \?/,
    'the account-path query is bound again — every follower 500s on real MySQL');
});

test('the strategies public list clamps to an integer before inlining', () => {
  // Inlining is only safe because the value cannot be attacker-shaped. The
  // SQL LIMIT is now the compile-time constant PUBLIC_LIST_LIMIT (caller input
  // clamps the post-filter slice instead), which is stronger still — pin both.
  const src = fs.readFileSync(path.join(APP, 'lib', 'user_strategies.js'), 'utf8');
  assert.match(src, /Math\.min\(PUBLIC_LIST_LIMIT, Math\.max\(1, Math\.floor\(Number\(limit\)\)/,
    'caller input is clamped to an integer before use');
  assert.match(src, /LIMIT \$\{PUBLIC_LIST_LIMIT\}/,
    'the inlined SQL LIMIT is a constant, never caller input');
  assert.doesNotMatch(src, /LIMIT \?/, 'and never a bound parameter');
});
