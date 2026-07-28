'use strict';

/**
 * "Did my deploy actually land?" — answerable without any git metadata.
 *
 * version.js has five ways to name the running commit, and on 2026-07-28 all
 * five missed at once: the deployed bundle carried no BUILD_SHA, no
 * build-info.json, no git binary and no .git directory, so /api/version
 * honestly reported {"sha":"unknown"}. Truthful, and useless — for an entire
 * day the only way to tell which build production was serving was to probe
 * its behaviour and infer, which is how #952 and #954 each spent time being
 * evaluated against a deploy that had not landed.
 *
 * The fingerprint does not pretend to be a commit id. It is a hash over the
 * source that actually shipped: it CHANGES when the code changes and stays
 * identical when it does not, which is precisely the question. And because it
 * is computable from a checkout, an expected value can be stated in advance:
 *
 *     node -e "console.log(require('./app/lib/version').fingerprint())"
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { fingerprint, buildInfo } = require('../lib/version');
const APP = path.join(__dirname, '..');

test('a fingerprint is produced, and names how many files it covers', () => {
  const fp = fingerprint();
  assert.ok(fp, 'the app source is present, so a fingerprint must be computable');
  assert.match(fp, /^[0-9a-f]{12}\+\d+$/,
    `expected "<12 hex>+<count>", got ${fp}`);
  const count = Number(fp.split('+')[1]);
  assert.ok(count > 50, `a real app covers many files, got ${count}`);
});

test('it is deterministic — an unchanged tree yields an unchanged value', () => {
  assert.equal(fingerprint(), fingerprint());
  assert.equal(fingerprint(), fingerprint(), 'and again, across repeated calls');
});

test('it changes when a covered source file changes, and returns when reverted', () => {
  const target = path.join(APP, 'lib', 'readiness.js');
  const original = fs.readFileSync(target);
  const before = fingerprint();
  try {
    fs.writeFileSync(target, Buffer.concat([original, Buffer.from('\n// x\n')]));
    assert.notEqual(fingerprint(), before,
      'a changed source file MUST move the fingerprint — otherwise it cannot '
      + 'answer "did my deploy land?"');
  } finally {
    fs.writeFileSync(target, original);
  }
  assert.equal(fingerprint(), before, 'and reverting restores it exactly');
});

test('it covers the boot path, not just the leaf libraries', () => {
  // server.js and auth.js are where two of today's outages lived; a
  // fingerprint that missed them would report "unchanged" across the fix.
  const target = path.join(APP, 'server.js');
  const original = fs.readFileSync(target);
  const before = fingerprint();
  try {
    fs.writeFileSync(target, Buffer.concat([original, Buffer.from('\n')]));
    assert.notEqual(fingerprint(), before, 'server.js must be covered');
  } finally {
    fs.writeFileSync(target, original);
  }
  assert.equal(fingerprint(), before);
});

test('the payload carries it, and omits rather than nulls when absent', () => {
  const info = buildInfo();
  assert.ok(info.build, 'this checkout can compute one, so it must be present');
  assert.equal(typeof info.build, 'string');
  // The contract when it cannot be computed is OMISSION: an absent field
  // reads as "not available here", where a null invites being read as a value.
  const src = fs.readFileSync(path.join(APP, 'lib', 'version.js'), 'utf8');
  assert.match(src, /if \(BUILD\.build\) out\.build = BUILD\.build;/);
});

test('it never reaches for secrets or state', () => {
  const src = fs.readFileSync(path.join(APP, 'lib', 'version.js'), 'utf8');
  const block = src.slice(src.indexOf('const FINGERPRINT_DIRS'),
    src.indexOf('function compute()'));
  for (const f of ['.env', 'data', 'secrets', 'creds', 'process.env']) {
    assert.ok(!block.includes(f),
      `the fingerprint must not touch ${f} — it hashes shipped .js source only`);
  }
  assert.ok(block.includes("endsWith('.js')"), 'and only .js files');
});

test('it survives an unreadable file rather than throwing', () => {
  // A bundle may omit a file the list expects; the digest must still resolve.
  // (readFileSync failures are skipped, and a zero-file result returns null.)
  const src = fs.readFileSync(path.join(APP, 'lib', 'version.js'), 'utf8');
  const block = src.slice(src.indexOf('function fingerprint()'),
    src.indexOf('function compute()'));
  assert.match(block, /catch \(e\) \{ continue; \}/,
    'a missing file is skipped, not fatal');
  assert.match(block, /if \(!counted\) return null;/,
    'and nothing readable yields null, never a hash of emptiness');
});
