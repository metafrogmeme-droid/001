'use strict';

/**
 * Diagnosis must be reachable from wherever the app actually runs.
 *
 * The web app runs in a managed page container — no shell, no reachable
 * disk. So logs/web.log, added precisely because stdout was unreachable,
 * landed somewhere nobody could grep either: the operator could watch
 * /readyz report "db_error" for hours and never reach the driver code behind
 * it. HTTP is the only channel into that container.
 *
 * The guard is a shared secret from the ENVIRONMENT, deliberately not the
 * normal auth stack: logging in needs the database, and the database being
 * unreachable is the exact circumstance this endpoint exists for. An
 * authenticated diagnostic that cannot authenticate during an outage is
 * furniture.
 *
 * Fail-closed: unset token → 404, as though the route were never mounted.
 */

const test = require('node:test');
const assert = require('node:assert');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

const APP = path.join(__dirname, '..');
const TOKEN = 'd'.repeat(40);

function blackHole() {
  return new Promise((resolve) => {
    const sockets = [];
    const srv = net.createServer((s) => sockets.push(s));
    srv.listen(0, '127.0.0.1', () => resolve({
      port: srv.address().port,
      close: () => { for (const s of sockets) s.destroy(); srv.close(); },
    }));
  });
}

function get(port, p, headers) {
  return new Promise((resolve, reject) => {
    const req = require('node:http').get(
      { host: '127.0.0.1', port, path: p, headers: headers || {}, timeout: 5000 },
      (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => resolve({ status: res.statusCode, body: b }));
      });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('probe timed out')));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function boot(t, env) {
  const hole = await blackHole();
  const port = 3600 + (process.pid % 100);
  const child = spawn(process.execPath, ['server.js'], {
    cwd: APP,
    env: {
      ...process.env,
      PORT: String(port),
      WEB_LOG_DIR: '',
      DATABASE_URL: `mysql://u:p@127.0.0.1:${hole.port}/rc`,
      JWT_SECRET: 'j'.repeat(48),
      BOT_SYNC_SECRET: 'b'.repeat(48),
      MIGRATE_ATTEMPT_TIMEOUT_MS: '1200',
      ...env,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', (d) => { log += d; });
  child.stderr.on('data', (d) => { log += d; });
  t.after(() => { child.kill('SIGKILL'); hole.close(); });
  for (let i = 0; i < 60 && !log.includes('running on port'); i += 1) await sleep(250);
  return { port, log: () => log };
}

test('with a token, the driver codes are reachable over HTTP',
  { timeout: 60000 }, async (t) => {
    const { port } = await boot(t, { DIAG_TOKEN: TOKEN });
    await sleep(4000);   // let capped attempts accumulate

    const r = await get(port, '/diagz', { 'X-Diag-Token': TOKEN });
    assert.equal(r.status, 200);
    const body = JSON.parse(r.body);

    assert.equal(body.reason, 'db_timeout');
    assert.ok(body.attempts >= 1);
    assert.ok(Array.isArray(body.events) && body.events.length >= 2,
      `the events must be served, got ${r.body}`);

    const flat = body.events.map((e) => `${e.event} ${e.detail}`).join('\n');
    assert.match(flat, /listening port=/);
    assert.match(flat, /migration_failed reason=db_timeout code=RC_MIGRATE_TIMEOUT/,
      'the driver code is the whole point of reaching this at all');

    // Nothing here may carry a secret or a connection string.
    assert.ok(!r.body.includes('j'.repeat(48)) && !r.body.includes('b'.repeat(48)));
    assert.ok(!r.body.includes('mysql://') && !r.body.includes(TOKEN));
  });

test('a wrong or missing token is indistinguishable from no route',
  { timeout: 60000 }, async (t) => {
    const { port } = await boot(t, { DIAG_TOKEN: TOKEN });

    for (const headers of [{}, { 'X-Diag-Token': 'wrong' },
      { 'X-Diag-Token': `${TOKEN}x` }, { 'X-Diag-Token': TOKEN.slice(0, -1) }]) {
      const r = await get(port, '/diagz', headers);
      assert.equal(r.status, 404,
        `a bad token must 404, never 401/403 — a distinct code confirms the `
        + `route exists and invites guessing (headers: ${JSON.stringify(headers)})`);
      assert.ok(!r.body.includes('events'), 'and leaks nothing in the body');
    }
  });

test('with DIAG_TOKEN unset the route does not exist at all',
  { timeout: 60000 }, async (t) => {
    const { port } = await boot(t, {});   // no DIAG_TOKEN
    const r = await get(port, '/diagz', { 'X-Diag-Token': TOKEN });
    assert.equal(r.status, 404, 'fail closed — an unconfigured deploy exposes nothing');
    // and the unguarded probes still work
    assert.equal((await get(port, '/healthz')).status, 200);
    assert.equal((await get(port, '/readyz')).status, 503);
  });

test('the public probe stays coarse — codes live behind the token only', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  // The HANDLER BODY only — the prose between the two routes legitimately
  // discusses codes and events.
  const from = src.indexOf("app.get('/readyz'");
  const readyz = src.slice(from, src.indexOf('});', from) + 3);
  assert.ok(!/bootLog|events|code/.test(readyz),
    `/readyz must not gain the detail — it is unauthenticated. Body:\n${readyz}`);
  assert.match(readyz, /readiness\.snapshot\(\)/, 'it serves the coarse snapshot');
});

test('the ring is bounded and hands out copies', () => {
  const bootLog = require('../lib/boot_log');
  bootLog._reset();
  for (let i = 0; i < bootLog.RING_MAX + 25; i += 1) bootLog.record('e', `i=${i}`);
  const got = bootLog.recent();
  assert.equal(got.length, bootLog.RING_MAX, 'oldest drops first, bounded');
  assert.match(got[got.length - 1].detail, /i=74$/, 'the newest is kept');

  got[0].event = 'mutated';
  assert.notEqual(bootLog.recent()[0].event, 'mutated', 'callers get a copy');
  bootLog._reset();
  assert.equal(bootLog.recent().length, 0);
});
