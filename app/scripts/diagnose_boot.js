#!/usr/bin/env node
'use strict';

/**
 * Boot-hang diagnostic — run this INSTEAD of requiring the app, and the hang
 * names itself.
 *
 *     node scripts/diagnose_boot.js ../server.js
 *     node scripts/diagnose_boot.js ./app-bundle/server
 *
 * Why this exists: when a container starts, binds a port, and then never
 * finishes loading the app, "it hangs" is not a diagnosis — every module is a
 * suspect. This wraps Module._load so every require announces itself BEFORE it
 * runs and again when it returns. The last "→" without a matching "✓" is the
 * module that never came back. Same principle as the engine's tick-phase caps:
 * bound the thing, then let the failure say its own name.
 *
 * It distinguishes the three ways a boot can fail, which look identical from
 * outside:
 *   1. a SYNCHRONOUS block — require() never returns. CommonJS require is
 *      synchronous, so only a blocking call can do this (sync child process,
 *      blocking read, entropy starvation, a spin loop). The last "→" names it.
 *   2. a THROW — require() explodes. Prints the error and the module, rather
 *      than dying silently inside a setImmediate where nobody sees it.
 *   3. an ASYNC stall — require() returns fine but the app never becomes
 *      reachable. The heartbeat keeps ticking and the summary prints; the
 *      problem is then AFTER module load, not in it.
 *
 * Everything is written with fs.writeSync to fd 2, not console.log, because a
 * buffered stream can lose exactly the line you need when the process is
 * killed mid-hang.
 */

const fs = require('fs');
const path = require('path');
const Module = require('module');

const SLOW_MS = Number(process.env.DIAG_SLOW_MS || 250);
const target = process.argv[2] || '../server.js';

function say(s) {
  try { fs.writeSync(2, s + '\n'); } catch (_) { /* never let logging throw */ }
}

const t0 = Date.now();
const ms = () => String(Date.now() - t0).padStart(6, ' ');

// ── the require tracer ────────────────────────────────────────────────────
const stack = [];
let depth = 0;
const slow = [];

const origLoad = Module._load;
Module._load = function tracedLoad(request, parent, isMain) {
  // node: builtins are never the problem and would drown the signal
  const builtin = request.startsWith('node:') || Module.builtinModules.includes(request);
  if (builtin) return origLoad.apply(this, arguments);

  const started = Date.now();
  const pad = '  '.repeat(depth);
  const frame = { request, started };
  stack.push(frame);
  depth += 1;
  say(`${ms()}ms ${pad}→ ${request}`);

  let result;
  try {
    result = origLoad.apply(this, arguments);
  } catch (err) {
    depth -= 1;
    stack.pop();
    say(`${ms()}ms ${pad}✗ ${request}  THREW: ${err && err.message}`);
    throw err;
  }
  depth -= 1;
  stack.pop();
  const took = Date.now() - started;
  if (took >= SLOW_MS) {
    slow.push({ request, took });
    say(`${ms()}ms ${pad}✓ ${request}  (${took}ms — SLOW)`);
  }
  return result;
};

// ── heartbeat: proves whether the event loop is alive during the hang ─────
// If the beats STOP, something is blocking the loop synchronously.
// If the beats CONTINUE while nothing else happens, the block is async.
const beat = setInterval(() => {
  const cur = stack.length ? stack[stack.length - 1] : null;
  const waiting = cur ? `inside require('${cur.request}') for ${Date.now() - cur.started}ms` : 'idle';
  say(`${ms()}ms ♥ event loop alive — ${waiting}`);
}, 5000);
beat.unref();

// ── make silent deaths loud ───────────────────────────────────────────────
process.on('uncaughtException', (err) => {
  say(`${ms()}ms ‼ uncaughtException: ${err && (err.stack || err.message)}`);
  process.exit(1);
});
process.on('unhandledRejection', (err) => {
  say(`${ms()}ms ‼ unhandledRejection: ${err && (err.stack || err.message)}`);
});
process.on('exit', (code) => {
  if (stack.length) {
    const cur = stack[stack.length - 1];
    say(`${ms()}ms ⛔ EXITED (code ${code}) while still inside require('${cur.request}')`);
    say('    ↑ THIS is the module that never returned.');
  }
});

// ── preflight: synchronous filesystem probes ──────────────────────────────
// server.js's FIRST executable statement is restoreFromVault(), which does
// fs.readFileSync on candidate data/ directories. A missing file throws
// instantly and is caught; a STALLED MOUNT does not throw — it blocks in
// uninterruptible I/O forever, and a try/catch cannot rescue a hang. Each
// probe below is run in a short-lived child process so that if the mount is
// wedged, the child hangs and THIS process still reports it.
function probeFsSync() {
  const { spawnSync } = require('child_process');
  const cands = [
    process.env.RUNECLAW_STATE_DIR,
    path.join(__dirname, '..', '..', 'data'),
    path.join(process.cwd(), 'data'),
    path.join(process.cwd(), '..', 'data'),
  ].filter(Boolean);

  say(`${ms()}ms ── preflight: probing ${cands.length} data-dir candidate(s), 5s each`);
  for (const dir of cands) {
    const code = `const fs=require('fs');try{fs.readdirSync(${JSON.stringify(dir)})}catch(e){};`
      + `try{fs.readFileSync(${JSON.stringify(path.join(dir, '.vault_master_key'))},'utf8')}catch(e){};`
      + `process.exit(0)`;
    const started = Date.now();
    const r = spawnSync(process.execPath, ['-e', code], { timeout: 5000, encoding: 'utf8' });
    const took = Date.now() - started;
    if (r.error && r.error.code === 'ETIMEDOUT') {
      say(`${ms()}ms   ⛔ ${dir} — BLOCKED (>5s, killed). A stalled mount here hangs boot.`);
    } else if (r.signal) {
      say(`${ms()}ms   ⛔ ${dir} — killed by ${r.signal} after ${took}ms`);
    } else {
      say(`${ms()}ms   ok ${dir} (${took}ms)`);
    }
  }

  // The env that decides whether server.js takes its blocking-random branch.
  const have = (k) => (process.env[k] ? 'set' : 'MISSING');
  say(`${ms()}ms ── env: JWT_SECRET=${have('JWT_SECRET')} `
    + `BOT_SYNC_SECRET=${have('BOT_SYNC_SECRET')} `
    + `DATABASE_URL=${have('DATABASE_URL')} NODE_ENV=${process.env.NODE_ENV || 'unset'}`);
}

// ── load it ───────────────────────────────────────────────────────────────
const resolved = path.isAbsolute(target) ? target : path.resolve(process.cwd(), target);
say(`${ms()}ms ── loading ${resolved}`);
say(`${ms()}ms ── node ${process.version} on ${process.platform}; slow threshold ${SLOW_MS}ms`);

if (process.env.DIAG_SKIP_FS !== '1') probeFsSync();

require(resolved);

say(`${ms()}ms ── module load COMPLETED without blocking.`);
if (slow.length) {
  say('── slowest requires:');
  for (const s of slow.sort((a, b) => b.took - a.took).slice(0, 10)) {
    say(`     ${String(s.took).padStart(6, ' ')}ms  ${s.request}`);
  }
} else {
  say('── no require exceeded the slow threshold.');
}
say('── if the app is still unreachable from here, the problem is AFTER module');
say('   load (a listener that never bound, or a wrapper that never mounted it),');
say('   NOT inside the require chain.');
