'use strict';

/**
 * Boot configuration — a misconfigured secret must not masquerade as a hang.
 *
 * The outage this pins (production, 2026-07-28): JWT_SECRET was set to a
 * 29-character string. server.js only asked "is JWT_SECRET set?" before
 * deciding whether to derive one, so a truthy-but-short value skipped the
 * derivation entirely and fell through to auth.js's `length < 32` check —
 * which calls process.exit(1) at MODULE SCOPE, during require('./auth').
 *
 * From outside the container that does not look like a crash. It looks like
 * the app never finishes loading: the wrapper's require() never returns
 * because the process left mid-require. Every deploy crash-looped for hours
 * behind that disguise, while a perfectly good derivation source sat unused
 * in BOT_SYNC_SECRET, and the message ("must be set") read as "unset" when
 * the real problem was "set, three characters too short".
 *
 * The contract now:
 *   - too short takes the SAME path as absent, so the app derives and boots;
 *   - the override is announced, never silent — the operator set something
 *     and is told plainly it was ignored and what replaced it;
 *   - a genuine fatal says WHICH failure it is and HOW short, and is written
 *     with fs.writeSync so it survives the exit on the next line;
 *   - no message ever contains the secret's value, only its length.
 */

const test = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const APP = path.join(__dirname, '..');
const SHORT = 'a'.repeat(29);      // the production value's length
const LONG = 'b'.repeat(48);

function runNode(code, env) {
  return spawnSync(process.execPath, ['-e', code], {
    cwd: APP, encoding: 'utf8', timeout: 30000,
    env: { ...process.env, JWT_SECRET: '', BOT_SYNC_SECRET: '', ...env },
  });
}

/**
 * Boot the REAL server.js in a child and return everything it said.
 *
 * It must be server.js, not auth.js: the resolution under test lives in
 * server.js, and requiring auth.js directly skips it entirely — which is
 * exactly the ordering that made the original bug possible. A healthy server
 * never exits on its own, so it is killed by the timeout; stdout up to that
 * point is what we assert on.
 */
function bootServer(env) {
  const r = spawnSync(process.execPath, ['server.js'], {
    cwd: APP, encoding: 'utf8', timeout: 8000,
    env: { ...process.env, PORT: '0', JWT_SECRET: '', BOT_SYNC_SECRET: '', ...env },
  });
  return { said: `${r.stdout || ''}${r.stderr || ''}`, status: r.status };
}

// ── the fix: short is treated as absent ───────────────────────────────────

test('a too-short JWT_SECRET is replaced by the derived one instead of exiting', () => {
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.ok(/_jwtProvided\.length < SECRET_MIN_LEN/.test(src),
    'the resolution must branch on LENGTH, not merely on presence — asking only '
    + '"is it set?" is what let a 29-character value reach auth.js and exit');
  assert.ok(!/if \(!process\.env\.JWT_SECRET\) \{/.test(src),
    'the old presence-only test must be gone');
});

test('the override is announced, and never prints the value', () => {
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const block = src.slice(src.indexOf('const _jwtProvided'),
    src.indexOf('// If production without a derivable secret'));
  assert.ok(/IGNORED/.test(block), 'a silently overridden secret is a trap');
  assert.ok(/\$\{_jwtProvided\.length\}/.test(block), 'the LENGTH is reported');
  assert.ok(!/\$\{_jwtProvided\}/.test(block) && !/\$\{process\.env\.JWT_SECRET\}/.test(block),
    'the VALUE must never be interpolated into any message');
});

// ── the fatals: say which failure, and how short ──────────────────────────

test('auth.js distinguishes "not set" from "too short" and gives the length', () => {
  const short = runNode("require('./auth')", { JWT_SECRET: SHORT });
  assert.equal(short.status, 1);
  assert.match(short.stderr, /JWT_SECRET is only 29 characters/);
  assert.ok(!short.stderr.includes(SHORT), 'the value must not appear in the message');

  const unset = runNode("require('./auth')", {});
  assert.equal(unset.status, 1);
  assert.match(unset.stderr, /JWT_SECRET is not set/);
});

test('auth.js points at the derivation escape hatch, not just at generating one', () => {
  const r = runNode("require('./auth')", { JWT_SECRET: SHORT });
  assert.match(r.stderr, /BOT_SYNC_SECRET/,
    'the operator whose value is too short should be told unsetting it works');
});

test('the BOT_SYNC_SECRET fatal also names which failure and how short', () => {
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const block = src.slice(src.indexOf('FATAL: BOT_SYNC_SECRET') - 700,
    src.indexOf('FATAL: BOT_SYNC_SECRET') + 400);
  assert.ok(/is not set/.test(block) && /is only \$\{_n\} characters/.test(block));
  assert.ok(!/\$\{process\.env\.BOT_SYNC_SECRET\}/.test(block),
    'never interpolate the secret itself');
});

test('boot fatals use fs.writeSync so they survive the immediate exit', () => {
  // console.error to a container's stdout PIPE can be queued and lost when the
  // process leaves on the next line — which is how this failure spent hours
  // looking like a hang instead of a config error.
  for (const f of ['server.js', 'auth.js']) {
    const src = fs.readFileSync(path.join(APP, f), 'utf8');
    const idx = src.indexOf('FATAL:');
    assert.ok(idx > 0, `${f} should carry a boot fatal`);
    const around = src.slice(Math.max(0, idx - 400), idx + 200);
    assert.ok(/writeSync\(2,/.test(around),
      `${f}'s boot fatal must fs.writeSync — a lost reason reads as a hang`);
  }
});

// ── end to end ────────────────────────────────────────────────────────────

test('the exact production case boots instead of crash-looping', () => {
  const { said } = bootServer({ JWT_SECRET: SHORT, BOT_SYNC_SECRET: LONG });
  assert.ok(said.includes('running on port'),
    `a 29-char JWT_SECRET must no longer stop the app. Got:\n${said}`);
  assert.ok(said.includes('only 29 characters'), 'the operator is warned');
  assert.ok(said.includes('derived from BOT_SYNC_SECRET'), 'and told what replaced it');
  assert.ok(!said.includes('FATAL'));
  assert.ok(!said.includes(SHORT), 'the value never appears in any output');
});

test('an explicit, long JWT_SECRET is left exactly as provided', () => {
  const { said } = bootServer({ JWT_SECRET: LONG, BOT_SYNC_SECRET: LONG });
  assert.ok(said.includes('running on port'));
  assert.ok(!said.includes('IGNORED'), 'a valid explicit secret must not be overridden');
  assert.ok(!said.includes('derived from BOT_SYNC_SECRET'));
  assert.ok(!said.includes(LONG), 'the value never appears in any output');
});

test('a missing BOT_SYNC_SECRET still refuses to start, legibly', () => {
  const { said, status } = bootServer({ JWT_SECRET: LONG });
  assert.equal(status, 1);
  assert.match(said, /FATAL: BOT_SYNC_SECRET is not set/);
  assert.ok(!said.includes('running on port'));
});
