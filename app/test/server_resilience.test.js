'use strict';
/**
 * Stability floor for the server process.
 *
 * Two failures motivated this file, both found from a single operator
 * screenshot of a 500 on /api/arena/account:
 *
 *   1. The log said "Arena unavailable" and nothing else. 158 call sites logged
 *      `err.message` with no stack, so a production incident could not name the
 *      handler, file or line that failed.
 *   2. Node 22 terminates the process on an unhandled promise rejection. This
 *      app runs 13 background timers; a rejection in any of them would kill the
 *      server, drop every in-flight request and open mind-stream, and get
 *      restarted by Docker with no record of the cause.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const APP = path.join(__dirname, '..');
const sources = [];
const walk = (dir) => {
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f);
    if (fs.statSync(p).isDirectory()) continue;
    if (f.endsWith('.js')) sources.push(p);
  }
};
walk(APP); walk(path.join(APP, 'routes')); walk(path.join(APP, 'lib'));

test('every server error log carries a stack, not just a message', () => {
  // err.message alone yields lines like "Cannot read properties of undefined"
  // with no location — the exact position the Arena 500 left us in.
  const offenders = [];
  for (const p of sources) {
    const src = fs.readFileSync(p, 'utf8');
    src.split('\n').forEach((line, i) => {
      if (/console\.error\([^)]*err\.message\)/.test(line) && !/err\.stack/.test(line)) {
        offenders.push(`${path.relative(APP, p)}:${i + 1}`);
      }
    });
  }
  assert.deepEqual(offenders, [],
    `these log err.message with no stack:\n  ${offenders.join('\n  ')}`);
});

test('a rejected background promise does not kill the process', () => {
  // Guard the assumption itself: if a future Node stops terminating on
  // unhandled rejections, this test says so rather than silently passing.
  const boom = 'setTimeout(()=>{Promise.reject(new Error("x"))},5);'
    + 'setTimeout(()=>{console.log("ALIVE")},120);';
  const bare = spawnSync(process.execPath, ['-e', boom], { encoding: 'utf8' });
  assert.notEqual(bare.status, 0, 'Node no longer dies on unhandled rejection — revisit this file');

  const guarded = spawnSync(process.execPath, ['-e',
    'process.on("unhandledRejection",()=>{});' + boom], { encoding: 'utf8' });
  assert.equal(guarded.status, 0);
  assert.match(guarded.stdout, /ALIVE/, 'the handler must let the process continue');
});

test('server.js installs both process-level safety nets', () => {
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  // A rejected background promise is logged and SWALLOWED — it must not be
  // fatal to a trading server.
  assert.match(src, /process\.on\('unhandledRejection'/);
  // Isolate the handler body: a windowed regex here spills into the
  // uncaughtException block below, which legitimately DOES exit.
  const start = src.indexOf("process.on('unhandledRejection'");
  const body = src.slice(start, src.indexOf("process.on('uncaughtException'"));
  assert.ok(start > 0 && body.length > 0, 'could not isolate the rejection handler');
  assert.ok(!/process\.exit/.test(body), 'an unhandled rejection must not exit the process');
  assert.match(body, /console\.error/, 'it must still be logged, not silently swallowed');
  // An uncaughtException may leave state inconsistent, so it exits — but it
  // must say why first, which is what was missing.
  assert.match(src, /process\.on\('uncaughtException'/);
  assert.match(src, /UNCAUGHT EXCEPTION[\s\S]{0,200}?err\.stack \|\| err\.message/);
  assert.match(src, /process\.exit\(1\)/);
});
