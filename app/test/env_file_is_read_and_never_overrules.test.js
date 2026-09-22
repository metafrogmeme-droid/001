'use strict';
//
// `.env` said PORT=3000, was correct, and was never read.
//
// The Python half calls load_dotenv() at import; the Express half required no
// such thing. So PORT was undefined, server.js fell back to 8080 — the BOT's
// gateway port on the same host — the listen lost the race, EADDRINUSE went
// unhandled, and the site served 520 for hours on 2026-09-22 with the correct
// value sitting in a file nobody opened.
//
// Driven, not scanned: the question is whether the value ARRIVES, and a scan
// for `require('./lib/env_file')` cannot tell a wired loader from a dead one.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { loadEnvFile, parseEnv } = require('../lib/env_file');

function withFile(body, contents) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'envfile-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, contents);
  try { return body(file); } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

function withCleanEnv(keys, body) {
  const saved = {};
  for (const k of keys) { saved[k] = process.env[k]; delete process.env[k]; }
  try { return body(); } finally {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k];
    }
  }
}

test('a value in .env reaches process.env — the outage in one line', () => {
  withCleanEnv(['PORT'], () => {
    withFile((f) => {
      const filled = loadEnvFile(f);
      assert.strictEqual(process.env.PORT, '3000');
      assert.ok(filled.includes('PORT'));
    }, 'PORT=3000\n');
  });
});

test('a live environment value ALWAYS wins over the file', () => {
  // The rule secrets_vault states one layer down. An operator who exported
  // something at the launch site meant it, and a file must never overrule the
  // command they just ran.
  withCleanEnv(['PORT'], () => {
    process.env.PORT = '4444';
    withFile((f) => {
      const filled = loadEnvFile(f);
      assert.strictEqual(process.env.PORT, '4444', 'the file overruled the environment');
      assert.ok(!filled.includes('PORT'));
    }, 'PORT=3000\n');
  });
});

test('an exported EMPTY string is a decision and is not overwritten', () => {
  withCleanEnv(['SOME_OPTIONAL'], () => {
    process.env.SOME_OPTIONAL = '';
    withFile((f) => {
      loadEnvFile(f);
      assert.strictEqual(process.env.SOME_OPTIONAL, '',
        'truthiness was used where `in` was needed');
    }, 'SOME_OPTIONAL=surprise\n');
  });
});

test('an absent file is the ordinary case, never an error', () => {
  assert.deepStrictEqual(loadEnvFile(path.join(os.tmpdir(), 'definitely-not-here-.env')), []);
});

test('an unreadable file cannot take the site down', () => {
  // A loader installed to END a boot failure must not become one.
  assert.doesNotThrow(() => loadEnvFile(os.tmpdir()));   // a directory, not a file
});

test('it returns key NAMES and never values', () => {
  withCleanEnv(['SECRET_ISH'], () => {
    withFile((f) => {
      const filled = loadEnvFile(f);
      assert.ok(filled.includes('SECRET_ISH'));
      assert.ok(!filled.join(' ').includes('hunter2'), 'a value leaked into the return');
    }, 'SECRET_ISH=hunter2\n');
  });
});

test('a # inside an unquoted value is kept — half a key is worse than a stray comment', () => {
  const got = Object.fromEntries(parseEnv('K=abc#def\n'));
  assert.strictEqual(got.K, 'abc#def');
});

test('comments, blanks, export and quotes', () => {
  const got = Object.fromEntries(parseEnv([
    '# a comment',
    '',
    'export A=1',
    'B="two"',
    "C='three'",
    'D=',
    'no_equals_here',
    '=novalue',
  ].join('\n')));
  assert.strictEqual(got.A, '1');
  assert.strictEqual(got.B, 'two');
  assert.strictEqual(got.C, 'three');
  assert.strictEqual(got.D, '');
  assert.ok(!('no_equals_here' in got));
  assert.ok(!('' in got));
});
