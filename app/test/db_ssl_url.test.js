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
  // From `looseObject`, not from `poolConfigFrom` — the helper sits above it
  // and handles the same credential-bearing string. A window that started at
  // the caller would have left the helper unchecked.
  const fn = src.slice(src.indexOf('function looseObject'),
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

// ── the object-literal form, and why it is not simply "starts with {" ──────
//
// mysql2's URL parser runs JSON.parse over every query parameter and falls
// back to a plain string when that throws (connection_config.js). So the
// value's JSON VALIDITY, not its meaning, decides what the driver receives:
//
//   {"rejectUnauthorized":true}   valid   → an object. Already correct.
//   {rejectUnauthorized:true}     invalid → a string → "Unknown SSL profile".
//
// A normaliser that keys on `startsWith('{')` cannot tell those apart, and
// handling both means re-deriving the options it just discarded. That was
// tried (a906eac) and it broke "an explicit ssl object is left exactly as the
// operator wrote it" above — visibly, in the suite — because the re-derived
// object is always {rejectUnauthorized:true} whatever was actually asked for.

test('a bare-key object literal is normalised to what the operator wrote', () => {
  const url = `${BASE}?ssl=${encodeURIComponent('{rejectUnauthorized:true}')}`;
  const cfg = poolConfigFrom(url);
  assert.strictEqual(typeof cfg, 'object', 'mysql2 would call this a profile name');
  assert.deepStrictEqual(cfg.ssl, { rejectUnauthorized: true });
  assert.ok(!/[?&]ssl=/.test(cfg.uri));
});

test('normalising a bare-key literal carries its VALUES, not a fixed pair', () => {
  // The distinction the "starts with {" shortcut loses. Both of these are
  // deliberate operator settings and neither may be replaced with a default.
  const off = poolConfigFrom(`${BASE}?ssl=${encodeURIComponent('{rejectUnauthorized:false}')}`);
  assert.strictEqual(off.ssl.rejectUnauthorized, false,
    'an explicit opt-out was silently upgraded — the operator asked for something else');

  const withCa = poolConfigFrom(`${BASE}?ssl=${encodeURIComponent('{rejectUnauthorized:true,ca:"PEM"}')}`);
  assert.strictEqual(withCa.ssl.ca, 'PEM', 'a custom CA must survive normalisation');
});

test('a valid-JSON object still reaches mysql2 with every field intact', () => {
  // Not a restatement of the pass-through test above: that one asserts the
  // string is returned unchanged, this one asserts the DRIVER ends up with the
  // custom CA. A pass-through that mysql2 then mangled would satisfy the first
  // and still lose the field that matters.
  const mysql = require('mysql2/promise');
  const url = `${BASE}?ssl=${encodeURIComponent('{"rejectUnauthorized":true,"ca":"PEMDATA"}')}`;
  const pool = mysql.createPool(poolConfigFrom(url));
  try {
    assert.deepStrictEqual(pool.pool.config.connectionConfig.ssl,
      { rejectUnauthorized: true, ca: 'PEMDATA' });
  } finally {
    pool.end().catch(() => {});
  }
});

test('an unparseable object literal is handed to mysql2, not guessed at', () => {
  for (const junk of ['{', '{,,}', '{rejectUnauthorized}', '{"a":}']) {
    const url = `${BASE}?ssl=${encodeURIComponent(junk)}`;
    assert.strictEqual(poolConfigFrom(url), url, junk);
  }
});

test('the loose parser never executes what it parses', () => {
  // The string comes from the environment. Looking like an object literal is
  // not a licence to run it, and `eval`/`new Function` here would turn a
  // connection string into arbitrary code execution at boot.
  const src = require('node:fs')
    .readFileSync(require('node:path').join(__dirname, '..', 'db.js'), 'utf8');
  const fn = src.slice(src.indexOf('function looseObject'),
    src.indexOf('function poolConfigFrom'));
  const code = fn.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const banned of ['eval(', 'new Function', 'require(']) {
    assert.ok(!code.includes(banned), `${banned} in the loose parser is RCE from DATABASE_URL`);
  }
  // And prove it, rather than only scanning for the shape.
  global.__ssl_pwned = false;
  const attack = '{a:(global.__ssl_pwned=true)}';
  poolConfigFrom(`${BASE}?ssl=${encodeURIComponent(attack)}`);
  assert.strictEqual(global.__ssl_pwned, false, 'the payload ran');
  delete global.__ssl_pwned;
});

// ── the fail-OPEN spelling: TLS asked for, plaintext delivered ─────────────
//
// mysql2 does not know `ssl-mode`, `sslmode` or `sslaccept`. It prints
// "Ignoring invalid configuration option" to stderr and connects UNENCRYPTED.
// Every other wrong spelling in this file fails closed — the pool refuses to
// build and somebody investigates. This one succeeds, carries the password in
// the clear, and reports nothing to any surface a person looks at. It is the
// house rule at the network layer: a request that could not be honoured must
// not render as success.

const REQUIRES_TLS = ['ssl-mode=REQUIRED', 'ssl-mode=VERIFY_CA',
  'ssl-mode=VERIFY_IDENTITY', 'sslmode=require', 'sslmode=verify-full',
  'sslaccept=strict'];

test('a URL that requires TLS never yields an unencrypted connection', () => {
  const mysql = require('mysql2/promise');
  for (const q of REQUIRES_TLS) {
    // The bug, still reproducible: this is what the driver does unaided.
    const bare = mysql.createPool(`${BASE}?${q}`);
    try {
      assert.strictEqual(bare.pool.config.connectionConfig.ssl, false,
        `mysql2 stopped ignoring ${q} — this normaliser can drop that key`);
    } finally { bare.end().catch(() => {}); }

    const pool = mysql.createPool(poolConfigFrom(`${BASE}?${q}`));
    try {
      assert.deepStrictEqual(pool.pool.config.connectionConfig.ssl,
        { rejectUnauthorized: true }, `${q} still connects in plaintext`);
    } finally { pool.end().catch(() => {}); }
  }
});

test('the ignored key is removed, so no warning survives on a healthy boot', () => {
  // mysql2 prints its "Ignoring invalid configuration option" line to stderr.
  // Leaving the key in place would keep an alarming line in the boot log of a
  // deploy that is now entirely correct — a false signal on a passing run.
  for (const q of REQUIRES_TLS) {
    const cfg = poolConfigFrom(`${BASE}?${q}`);
    assert.strictEqual(typeof cfg, 'object', q);
    assert.ok(!/ssl-?mode=|sslaccept=/i.test(cfg.uri), `${q} left in the uri`);
  }
});

test('a mode that merely PREFERS tls is left alone', () => {
  // `PREFERRED` and `allow` permit plaintext, so forcing TLS would invent an
  // intent the operator did not express — and break a server without it.
  for (const q of ['ssl-mode=PREFERRED', 'sslmode=prefer', 'sslmode=allow',
    'sslaccept=accept_invalid_certs']) {
    const url = `${BASE}?${q}`;
    assert.strictEqual(poolConfigFrom(url), url, q);
  }
});

test('an explicit ssl param outranks the mode spelling', () => {
  // Both present: `ssl` is the one mysql2 actually reads, so it decides.
  const url = `${BASE}?ssl=${encodeURIComponent('{"rejectUnauthorized":false}')}&sslmode=require`;
  assert.strictEqual(poolConfigFrom(url), url);
});

test('credentials and database survive every rewriting path', () => {
  // Each path that reconstructs the URI runs it through `new URL().toString()`.
  // A round trip that re-encoded the password would produce an auth failure
  // that looks exactly like a wrong secret.
  const mysql = require('mysql2/promise');
  const url = 'mysql://u%3Aser:p%40ss%3Aw%2Frd%231@db.example.com:4000/rune%5Fclaw';
  for (const q of ['ssl=true', 'ssl={rejectUnauthorized:true}', 'sslmode=require']) {
    const pool = mysql.createPool(poolConfigFrom(`${url}?${q}`));
    try {
      const c = pool.pool.config.connectionConfig;
      assert.strictEqual(c.user, 'u:ser', q);
      assert.strictEqual(c.password, 'p@ss:w/rd#1', q);
      assert.strictEqual(c.database, 'rune_claw', q);
      assert.strictEqual(c.host, 'db.example.com', q);
      assert.strictEqual(c.port, 4000, q);
    } finally { pool.end().catch(() => {}); }
  }
});
