'use strict';
/**
 * A production deployment that quietly became an amnesiac.
 *
 * `db.js` opened with:
 *
 *     if (USE_MYSQL) {
 *       try {
 *         const mysql = require('mysql2/promise');
 *         pool = mysql.createPool(process.env.DATABASE_URL);
 *         ...
 *       } catch (err) {
 *         console.error('mysql2 not available, falling back to in-memory:', ...);
 *         USE_MYSQL = false;
 *       }
 *     }
 *
 * Setting `DATABASE_URL` IS the operator saying "use this database". If the
 * pool could not be created the app did not stop — it demoted itself to an
 * empty in-memory store and kept answering **200**. Every panel then rendered
 * "no trades", "no positions", "no data": each one truthful about the store it
 * was reading, and every one a lie about the account.
 *
 * That is this repository's founding rule at infrastructure scale. A failed
 * read must not render as an empty result — and here the failed read was the
 * whole database, so *every* surface rendered empty at once, with nothing
 * anywhere saying why.
 *
 * `createPool` sits inside the same try, so a malformed URL — a typo — took
 * the identical silent path. And `mysql2` is a declared dependency, so an
 * import failure means a broken install, not a missing optional extra. There
 * was no case in which the fallback was the right answer.
 *
 * A 500 is a bad minute. A 200-serving amnesiac is a user concluding their
 * positions are gone.
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const DB = path.join(__dirname, '..', 'db.js');

/** Load a fresh copy of db.js under a given DATABASE_URL. */
function loadDb(databaseUrl) {
  const prev = process.env.DATABASE_URL;
  if (databaseUrl === undefined) delete process.env.DATABASE_URL;
  else process.env.DATABASE_URL = databaseUrl;
  delete require.cache[require.resolve(DB)];
  try {
    return { mod: require(DB), threw: null };
  } catch (err) {
    return { mod: null, threw: err };
  } finally {
    if (prev === undefined) delete process.env.DATABASE_URL;
    else process.env.DATABASE_URL = prev;
    delete require.cache[require.resolve(DB)];
  }
}

test('a broken DATABASE_URL refuses to start instead of inventing a database', () => {
  // A URL mysql2 cannot parse — the typo case, which took the same silent
  // path as a missing module and is far likelier to actually happen.
  const { mod, threw } = loadDb('not-a-valid-mysql-url');
  assert.ok(threw, 'db.js loaded anyway — a deployment with a broken '
    + 'DATABASE_URL is now serving empty results as real ones');
  assert.match(String(threw.message), /database unavailable/i);
  assert.equal(mod, null);
});

test('the refusal names the cause and the deliberate way out', () => {
  // An operator who sees this needs to know it was the database, and that
  // in-memory is available on purpose if that is what they meant. A bare
  // stack trace sends them looking at the app.
  const { threw } = loadDb('not-a-valid-mysql-url');
  assert.ok(threw);
  const text = `${threw.message}`;
  assert.ok(/unavailable/i.test(text), `unhelpful message: ${text}`);
});

test('no DATABASE_URL still runs in memory, deliberately', () => {
  // The fallback is not being removed — it is being made a CHOICE rather than
  // a consolation prize. Dev and the whole test suite depend on this path.
  const { mod, threw } = loadDb(undefined);
  assert.equal(threw, null, `in-memory mode broke: ${threw && threw.message}`);
  assert.ok(mod.pool, 'no pool in memory mode');
  assert.equal(mod.backend(), 'memory');
});

test('the live backend is reportable, not just logged once at boot', () => {
  // Before this the only trace was a console.log at startup, so "am I on the
  // real database?" was answered by grepping a log — or by noticing the data
  // was missing.
  const { mod } = loadDb(undefined);
  assert.strictEqual(typeof mod.backend, 'function');
  assert.ok(['mysql', 'memory'].includes(mod.backend()));
});

test('backend() reports whichever store this load actually chose', () => {
  // There was a test here named "not a value frozen at import", scanning for
  // `function backend()`. A mutation that froze the value INSIDE that function
  // passed it — the shape was present and the property was not, which is the
  // same presence-vs-behaviour gap that let an unreachable `stop()` call pass
  // in H7.
  //
  // Worse, the property it named cannot bite today: `USE_MYSQL` is set once
  // from the environment and, now that the silent fallback is gone, nothing
  // reassigns it — so a frozen read is genuinely equivalent. The function form
  // is kept because it is the right shape if that ever stops being true, not
  // because a test can tell the difference. Saying so beats a check that
  // implies coverage it does not have.
  //
  // What IS assertable is that the report tracks the load: memory mode must
  // say memory, which kills a hardcoded 'mysql'.
  const { mod } = loadDb(undefined);
  assert.equal(mod.backend(), 'memory');
});

test('the fallback assignment is gone from the MySQL failure path', () => {
  // The specific line: `USE_MYSQL = false` inside the catch is what turned a
  // failed database into a silent empty one.
  const src = require('node:fs').readFileSync(DB, 'utf8');
  const i = src.indexOf("require('mysql2/promise')");
  assert.ok(i > 0, 'the mysql2 require moved — re-point this check');
  const block = src.slice(i, i + 2200);
  const code = block.split('\n')
    .map((ln) => ln.split('//', 1)[0])          // the comment explains the bug
    .join('\n');
  assert.ok(!/USE_MYSQL\s*=\s*false/.test(code),
    'the MySQL failure path demotes to in-memory again');
  assert.match(code, /throw new Error/,
    'the MySQL failure path no longer fails closed');
});
