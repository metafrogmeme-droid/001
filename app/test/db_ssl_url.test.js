'use strict';
/**
 * `?ssl=` in DATABASE_URL — the string format that presents as a database outage.
 *
 * mysql2 rejects `ssl=true` with "SSL profile must be an object, instead it's a
 * boolean" at pool-construction time, before any network call. `db.js` fails
 * closed and loud on that, which is right — but the operator then goes looking
 * at the database, because that is what the symptom looks like. It is a string.
 *
 * These tests drive the normaliser directly rather than standing up a pool,
 * because the whole point is that the failure happens before any connection.
 * The last test asserts the real mysql2 accepts what we produce — a normaliser
 * that satisfies only its own tests would be worth nothing.
 */

const test = require('node:test');
const assert = require('node:assert');

const { poolConfigFrom } = require('../db');

const BASE = 'mysql://user:s3cr3t@db.example.com:4000/runeclaw';

// ── the spelling that breaks ──────────────────────────────────────────────

test('ssl=true becomes the object mysql2 wanted', () => {
  const cfg = poolConfigFrom(`${BASE}?ssl=true`);
  assert.strictEqual(typeof cfg, 'object');
  assert.deepStrictEqual(cfg.ssl, { rejectUnauthorized: true });
  assert.ok(!/[?&]ssl=/.test(cfg.uri), 'the raw ssl param must be removed from the uri');
});

test('the boolean spellings are recognised in every casing', () => {
  for (const v of ['true', 'TRUE', 'True', ' true ', '1']) {
    const cfg = poolConfigFrom(`${BASE}?ssl=${encodeURIComponent(v)}`);
    assert.strictEqual(typeof cfg, 'object', v);
    assert.deepStrictEqual(cfg.ssl, { rejectUnauthorized: true }, v);
  }
});

test('normalising never weakens verification', () => {
  // The secure reading of a bare `ssl=true` is "verify the certificate". A
  // normaliser that quietly produced {rejectUnauthorized:false} would turn a
  // connection error into a silent downgrade, which is strictly worse.
  const cfg = poolConfigFrom(`${BASE}?ssl=true`);
  assert.strictEqual(cfg.ssl.rejectUnauthorized, true);
});

// ── everything else is passed through untouched ───────────────────────────

test('an explicit ssl object is left exactly as the operator wrote it', () => {
  const url = `${BASE}?ssl=${encodeURIComponent('{"rejectUnauthorized":false}')}`;
  assert.strictEqual(poolConfigFrom(url), url,
    'we must not override a TLS setting that was asked for deliberately');
});

test('a named CA profile is left alone', () => {
  const url = `${BASE}?ssl=Amazon%20RDS`;
  assert.strictEqual(poolConfigFrom(url), url);
});

test('a url with no ssl param is untouched', () => {
  assert.strictEqual(poolConfigFrom(BASE), BASE);
  assert.strictEqual(poolConfigFrom(`${BASE}?connectTimeout=9000`),
    `${BASE}?connectTimeout=9000`);
});

test('other query params survive normalisation', () => {
  const cfg = poolConfigFrom(`${BASE}?connectTimeout=9000&ssl=true&charset=utf8mb4`);
  assert.match(cfg.uri, /connectTimeout=9000/);
  assert.match(cfg.uri, /charset=utf8mb4/);
});

test('an unparseable url is handed to mysql2 unchanged', () => {
  // The driver owns the format; guessing at a typo is worse than letting the
  // thing that defines the grammar produce the error.
  for (const junk of ['not a url', '', 'mysql://', 'ssl=true']) {
    assert.strictEqual(poolConfigFrom(junk), junk, JSON.stringify(junk));
  }
});

// ── the credential never leaks ────────────────────────────────────────────

test('the normaliser is not a logger', () => {
  const src = require('node:fs')
    .readFileSync(require('node:path').join(__dirname, '..', 'db.js'), 'utf8');
  const fn = src.slice(src.indexOf('function poolConfigFrom'),
    src.indexOf('if (USE_MYSQL)'));
  const code = fn.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const banned of ['console.log', 'console.error', 'console.warn']) {
    assert.ok(!code.includes(banned),
      `${banned} in the normaliser would put the password in the log`);
  }
});

// ── and mysql2 actually accepts the result ────────────────────────────────

test('mysql2 accepts what the normaliser produces, and rejects what it fixes', () => {
  const mysql = require('mysql2/promise');
  const broken = `${BASE}?ssl=true`;

  // The bug, still reproducible: this is what db.js used to pass straight in.
  assert.throws(() => mysql.createPool(broken), /SSL profile must be an object/,
    'if mysql2 stops throwing here, this normaliser can be deleted');

  const pool = mysql.createPool(poolConfigFrom(broken));
  try {
    const c = pool.pool.config.connectionConfig;
    assert.strictEqual(c.host, 'db.example.com');
    assert.strictEqual(c.database, 'runeclaw');
    assert.strictEqual(c.port, 4000);
    assert.strictEqual(c.user, 'user');
    assert.deepStrictEqual(c.ssl, { rejectUnauthorized: true });
  } finally {
    pool.end().catch(() => {});
  }
});
