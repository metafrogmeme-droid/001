'use strict';
/**
 * MH3 — public /status trust surface. Contract: every component state is
 * computed from real timestamps; degraded/no-data states are reported
 * honestly (never rounded up); the payload carries no secrets and no
 * dollar figures.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const status = require('../lib/status');

const NOW = 1_800_000_000_000;

function probes(overrides = {}) {
  return {
    getScan: async () => ({ received_at: new Date(NOW - 5 * 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(NOW - 30 * 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'reachable' }),
    pingBridge: async () => ({ state: 'reachable' }),
    latestLetter: async () => ({ week_key: '2026-W29',
      generated_at: new Date(NOW - 2 * 86_400_000).toISOString() }),
    dbMode: () => 'memory',
    uptimeS: () => 3700,
    ...overrides,
  };
}

test('all-healthy: overall ok, ages computed from real timestamps', async () => {
  status.setProbes(probes());
  const s = await status.buildStatus(NOW);
  assert.equal(s.status, 'ok');
  assert.equal(s.components.engine_scan.state, 'fresh');
  assert.equal(s.components.engine_scan.age_minutes, 5);
  assert.equal(s.components.intelligence_reports.age_minutes, 30);
  assert.equal(s.components.bot_gateway.state, 'reachable');
  assert.equal(s.components.weekly_letter.latest_week, '2026-W29');
});

test('stale scan + dead gateway: degraded, never rounded up to healthy', async () => {
  status.setProbes(probes({
    getScan: async () => ({ received_at: new Date(NOW - 90 * 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'unreachable' }),
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.status, 'degraded');
  assert.equal(s.components.engine_scan.state, 'stale');
  assert.equal(s.components.engine_scan.age_minutes, 90);
});

test('missing data and throwing probes read no_data — not ok, not a crash', async () => {
  status.setProbes(probes({
    getScan: async () => null,
    getReports: async () => { throw new Error('db down'); },
    latestLetter: async () => { throw new Error('db down'); },
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.engine_scan.state, 'no_data');
  assert.equal(s.components.intelligence_reports.state, 'no_data');
  assert.equal(s.components.weekly_letter.state, 'no_data');
  assert.equal(s.status, 'degraded');
});

test('not_configured gateway is honest but not alarming', async () => {
  status.setProbes(probes({ pingGateway: async () => ({ state: 'not_configured' }) }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.bot_gateway.state, 'not_configured');
  assert.equal(s.status, 'ok');
});

test('payload carries no secrets and no dollar figures', async () => {
  status.setProbes(probes());
  const raw = JSON.stringify(await status.buildStatus(NOW));
  for (const needle of ['$', 'usd', 'secret', 'token', 'password', 'key']) {
    assert.ok(!raw.toLowerCase().includes(needle), `payload must not contain "${needle}"`);
  }
});

/**
 * THE OUTAGE THIS COMPONENT EXISTS FOR.
 *
 * `bot/main.py` serves the gateway on :8080. `api_bridge.py` is a SEPARATE
 * uvicorn process on :8000, and three dashboard panels read it. On 2026-08-25
 * the bridge was not running for hours while this endpoint reported the system
 * healthy — because `bot_gateway: reachable` was true and it was the only link
 * probed. Two panels answered 502 and nothing said so; an operator found it by
 * clicking.
 *
 * A status page that probes one of two links reads as coverage while providing
 * none.
 */
test('a dead API bridge is reported, not hidden behind a healthy gateway', async () => {
  status.setProbes(probes({
    pingGateway: async () => ({ state: 'reachable' }),
    pingBridge: async () => ({ state: 'unreachable' }),
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.api_bridge.state, 'unreachable');
  assert.notEqual(s.status, 'ok',
    'the gateway being up reported the whole system healthy while two panels 502');
});

test('an unset BOT_API_URL is not_configured, not unreachable', async () => {
  // Different faults, different fixes. "Unreachable" sends an operator hunting
  // a dead process; "not_configured" tells them nobody has said where it is —
  // which is the actual state on a deployment that never set the variable.
  status.setProbes(probes({ pingBridge: async () => ({ state: 'not_configured' }) }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.api_bridge.state, 'not_configured');
  assert.equal(s.status, 'ok',
    'an unconfigured optional link should not read as an outage');
});

test('both links down is worse than one', async () => {
  status.setProbes(probes({
    pingGateway: async () => ({ state: 'unreachable' }),
    pingBridge: async () => ({ state: 'unreachable' }),
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.status, 'degraded');
});

test('a probe that throws is unreachable, never silently healthy', async () => {
  // The probe layer must not be able to turn its own failure into a pass.
  status.setProbes(probes({ pingBridge: async () => { throw new Error('boom'); } }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.api_bridge.state, 'unreachable');
});

test('the bridge component names what it serves', () => {
  // The note is the operator's only clue about which panels just broke.
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'lib', 'status.js'), 'utf8');
  assert.match(src, /insight, patterns and lab/);
});

// ── the REAL probe, not a fixture ─────────────────────────────────────────
//
// Every test above injects probe doubles, which is right for exercising the
// verdict logic and useless for vouching for the probe itself. A mutation
// proved it: deleting the not_configured guard from the live `pingBridge`
// passed the entire suite, because nothing ever called it. These do.

test('the real pingBridge reports not_configured when BOT_API_URL is unset', async () => {
  const saved = process.env.BOT_API_URL;
  delete process.env.BOT_API_URL;
  try {
    const r = await status.defaultProbes().pingBridge();
    assert.equal(r.state, 'not_configured',
      'an unset URL reads as a dead process, sending the operator to hunt one '
      + 'that was never addressed');
  } finally {
    if (saved !== undefined) process.env.BOT_API_URL = saved;
  }
});

test('the real pingBridge reports unreachable when nothing answers', async () => {
  const saved = process.env.BOT_API_URL;
  // Port 1 on loopback: reliably refused, never a real service.
  process.env.BOT_API_URL = 'http://127.0.0.1:1';
  try {
    const r = await status.defaultProbes().pingBridge();
    assert.equal(r.state, 'unreachable');
  } finally {
    if (saved === undefined) delete process.env.BOT_API_URL;
    else process.env.BOT_API_URL = saved;
  }
});

test('the real pingBridge reports reachable when a server answers', async () => {
  // Any 2xx-4xx means a server is there, which is the question being asked —
  // not whether that server is happy.
  const http = require('node:http');
  const srv = http.createServer((req, res) => { res.writeHead(200); res.end('ok'); });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const saved = process.env.BOT_API_URL;
  process.env.BOT_API_URL = `http://127.0.0.1:${srv.address().port}`;
  try {
    const r = await status.defaultProbes().pingBridge();
    assert.equal(r.state, 'reachable');
  } finally {
    if (saved === undefined) delete process.env.BOT_API_URL;
    else process.env.BOT_API_URL = saved;
    srv.close();
  }
});

test('a bridge that answers 5xx is "error", not "reachable"', async () => {
  // A process that accepts connections and returns 502 is UP and BROKEN, and
  // those are different from each other and from down. Collapsing it into
  // "reachable" is the status page reporting a link healthy while every panel
  // behind it fails — which is the whole defect this component was added for,
  // one layer in.
  const http = require('node:http');
  const srv = http.createServer((req, res) => { res.writeHead(502); res.end('bad gateway'); });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const saved = process.env.BOT_API_URL;
  process.env.BOT_API_URL = `http://127.0.0.1:${srv.address().port}`;
  try {
    const r = await status.defaultProbes().pingBridge();
    assert.equal(r.state, 'error',
      'a 502 from the bridge is being reported as a healthy link');
  } finally {
    if (saved === undefined) delete process.env.BOT_API_URL;
    else process.env.BOT_API_URL = saved;
    srv.close();
  }
});

test('a 4xx still counts as reachable — the process is answering', async () => {
  // /health needs no secret, but a 401/404 would still prove a server is there.
  // The question this probe asks is "is the process up", not "is it happy".
  const http = require('node:http');
  const srv = http.createServer((req, res) => { res.writeHead(404); res.end('nope'); });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const saved = process.env.BOT_API_URL;
  process.env.BOT_API_URL = `http://127.0.0.1:${srv.address().port}`;
  try {
    assert.equal((await status.defaultProbes().pingBridge()).state, 'reachable');
  } finally {
    if (saved === undefined) delete process.env.BOT_API_URL;
    else process.env.BOT_API_URL = saved;
    srv.close();
  }
});

test('a hung bridge does not hold the status page open', async () => {
  // The page that exists to report trouble must not become another thing that
  // is down. A dependency that accepts the connection and never answers is the
  // case a connect-timeout alone does not cover.
  const http = require('node:http');
  const srv = http.createServer(() => { /* accept, never respond */ });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const saved = process.env.BOT_API_URL;
  process.env.BOT_API_URL = `http://127.0.0.1:${srv.address().port}`;
  const t0 = Date.now();
  try {
    const r = await status.defaultProbes().pingBridge();
    assert.equal(r.state, 'unreachable');
    assert.ok(Date.now() - t0 < 8000,
      `the probe took ${Date.now() - t0}ms — a hung bridge is stalling the page`);
  } finally {
    if (saved === undefined) delete process.env.BOT_API_URL;
    else process.env.BOT_API_URL = saved;
    srv.close();
  }
});

test('HTTP surface serves the page and the API', async () => {
  // The route calls buildStatus() with the real clock — feed it live-relative
  // timestamps (the pinned-NOW probes would read as future/no_data here).
  status.setProbes(probes({
    getScan: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    latestLetter: async () => ({ week_key: '2026-W29',
      generated_at: new Date(Date.now() - 86_400_000).toISOString() }),
  }));
  const app = express();
  app.use('/api/public/status', require('../routes/public_status'));
  const server = await new Promise((res) => {
    const s = app.listen(0, '127.0.0.1', () => res(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const body = await new Promise((resolve, reject) => {
    http.get(`${base}/api/public/status`, (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => resolve(JSON.parse(d)));
    }).on('error', reject);
  });
  server.close();
  assert.equal(body.status, 'ok');

  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'status.html'), 'utf8');
  assert.match(html, /never hand-set/, 'the honesty promise is on the page');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/status"/, 'footer link exists');
  const server_js = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_js, /app\.get\('\/status'/, 'page route mounted');
});
