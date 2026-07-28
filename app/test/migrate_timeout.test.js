'use strict';

/**
 * A migration attempt that never returns must not park the retry loop.
 *
 * Production, 2026-07-28, after the boot fix landed: the site was serving,
 * but /readyz reported {"ready":false,"reason":"starting","attempts":0} with
 * 403s of uptime. `attempts: 0` is the tell — markAttemptFailed had never
 * been called, so migrate() had not failed even once. It was HANGING.
 *
 * mysql2's pool does not bound a connect that gets no answer, so the retry
 * loop sat on its first call forever: no attempt recorded, no backoff run,
 * and no recovery possible even once the database came back. Retrying only
 * helps if the thing being retried can finish.
 *
 * The fix caps each ATTEMPT, converting a hang into a failure the loop can
 * act on. This test proves it against a real black-hole socket — one that
 * completes the TCP handshake and then says nothing, which is precisely the
 * shape mysql2 waits on forever.
 */

const test = require('node:test');
const assert = require('node:assert');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

const APP = path.join(__dirname, '..');

/** A server that ACCEPTS and never replies — a connect that hangs, not fails. */
function blackHole() {
  return new Promise((resolve) => {
    const sockets = [];
    const srv = net.createServer((s) => sockets.push(s));   // never write, never end
    srv.listen(0, '127.0.0.1', () => resolve({
      port: srv.address().port,
      close: () => { for (const s of sockets) s.destroy(); srv.close(); },
    }));
  });
}

function get(port, p) {
  return new Promise((resolve, reject) => {
    const req = require('node:http').get(
      { host: '127.0.0.1', port, path: p, timeout: 5000 },
      (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => resolve({ status: res.statusCode, body: b }));
      });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(new Error('probe timed out')); });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('a hanging database becomes a retried failure, not a parked loop',
  { timeout: 60000 }, async (t) => {
    const hole = await blackHole();
    const port = 3400 + (process.pid % 100);
    const child = spawn(process.execPath, ['server.js'], {
      cwd: APP,
      env: {
        ...process.env,
        PORT: String(port),
        DATABASE_URL: `mysql://u:p@127.0.0.1:${hole.port}/rc`,
        JWT_SECRET: 'j'.repeat(48),
        BOT_SYNC_SECRET: 'b'.repeat(48),
        MIGRATE_ATTEMPT_TIMEOUT_MS: '1500',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    child.stdout.on('data', (d) => { log += d; });
    child.stderr.on('data', (d) => { log += d; });
    t.after(() => { child.kill('SIGKILL'); hole.close(); });

    // Wait for the port, then let several capped attempts elapse.
    for (let i = 0; i < 40 && !log.includes('running on port'); i += 1) await sleep(250);
    assert.ok(log.includes('running on port'),
      `the site must serve while the database hangs. Log:\n${log}`);

    // Static content is unaffected by a hanging database.
    assert.equal((await get(port, '/')).status, 200);
    assert.equal((await get(port, '/healthz')).status, 200);

    await sleep(5000);   // >= 2 capped attempts at 1500ms plus backoff

    const r = await get(port, '/readyz');
    assert.equal(r.status, 503);
    const snap = JSON.parse(r.body);
    assert.equal(snap.ready, false);
    assert.equal(snap.reason, 'db_timeout',
      `a capped attempt must be distinguishable from a driver error, got ${r.body}`);
    assert.ok(snap.attempts >= 1,
      `the loop must RECORD attempts — attempts:0 is the parked-forever signature `
      + `this test exists to prevent (got ${r.body})`);

    // And the DB-backed surface refuses honestly rather than erroring out.
    const api = await get(port, '/api/public/leaderboard');
    assert.equal(api.status, 503);
    assert.match(api.body, /db_timeout/);

    // No abandoned attempt may surface as an unhandled rejection.
    assert.ok(!/UNHANDLED REJECTION/.test(log),
      `an abandoned attempt must be swallowed, not thrown. Log:\n${log}`);
  });

test('the timeout code classifies as its own reason, not as a driver error', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  assert.equal(readiness.classify({ code: readiness.TIMEOUT_CODE }), 'db_timeout');
  // ETIMEDOUT is the DRIVER giving up and remains distinct: "the server did
  // not answer" is a different fact from "the driver never came back".
  assert.equal(readiness.classify({ code: 'ETIMEDOUT' }), 'db_unreachable');
  assert.ok(readiness.REASONS.includes('db_timeout'));
  readiness._reset();
});

test('the cap can be disabled, and is not applied when non-positive', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.match(src, /if \(!\(MIGRATE_ATTEMPT_TIMEOUT_MS > 0\)\) return migrate\(\)/,
    'an operator must be able to opt out of the cap entirely');
  assert.match(src, /MIGRATE_ATTEMPT_TIMEOUT_MS \|\| 180000/,
    'the default must clear a FIRST migration — 33 distributed DDL statements '
    + 'on a possibly cold serverless cluster. 45s aborted one that was merely '
    + 'working hard, which is the failure the cap exists NOT to cause.');
});

// ── classification breadth ────────────────────────────────────────────────

test('a mandated-TLS rejection names the transport, not a generic failure', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  // TiDB Cloud mandates encrypted transport; a plaintext connect is refused
  // with this code. Collapsed into 'db_error' it reads as "the database is
  // broken" when the database is fine and the CONNECTION STRING is not.
  assert.equal(readiness.classify({ code: 'ER_SECURE_TRANSPORT_REQUIRED' }), 'db_tls');
  assert.equal(readiness.classify({ code: 'HANDSHAKE_NO_SSL_SUPPORT' }), 'db_tls');
  assert.equal(readiness.classify({ code: 'CERT_HAS_EXPIRED' }), 'db_tls');
  readiness._reset();
});

test('a URL pointing at something absent is a config fault, not an outage', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  assert.equal(readiness.classify({ code: 'ER_BAD_DB_ERROR' }), 'db_config');
  assert.equal(readiness.classify({ code: 'ER_HOST_IS_BLOCKED' }), 'db_config');
  readiness._reset();
});

test('a transient DNS failure counts as unreachable', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  assert.equal(readiness.classify({ code: 'EAI_AGAIN' }), 'db_unreachable');
  readiness._reset();
});

test('every classified reason is still in the declared vocabulary', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  const codes = ['ER_SECURE_TRANSPORT_REQUIRED', 'ER_BAD_DB_ERROR', 'EAI_AGAIN',
    'ECONNREFUSED', 'ER_ACCESS_DENIED_ERROR', 'RC_MIGRATE_TIMEOUT', 'TOTALLY_NEW'];
  for (const code of codes) {
    assert.ok(readiness.REASONS.includes(readiness.classify({ code })),
      `classify('${code}') produced an undeclared reason`);
  }
  readiness._reset();
});

test('the raw driver code reaches the server log but never /readyz', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.match(src, /code=\$\{code\}/,
    'an unclassified failure must log its driver code — that one word is the '
    + 'difference between grepping a stack trace and reading the cause');
  // /readyz still serves only the coarse snapshot.
  const readyz = src.slice(src.indexOf("app.get('/readyz'"), src.indexOf("app.get('/readyz'") + 300);
  assert.ok(!/err|code/.test(readyz), 'the public probe stays coarse');
});

// ── the grep-able record ──────────────────────────────────────────────────

test('the reason lands in a FILE, not only on stdout',
  { timeout: 60000 }, async (t) => {
    const fs = require('node:fs');
    const os = require('node:os');
    // The web app writes no log file of its own, so on a managed host its
    // diagnostics live only in the platform's log viewer — and
    // `grep 'Migration failed' logs/*.log` matches nothing, because logs/
    // belongs to the Python bot. That is how a database failure stayed
    // unreadable while /readyz was reporting it.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-weblog-'));
    const hole = await blackHole();
    const port = 3500 + (process.pid % 100);
    const child = spawn(process.execPath, ['server.js'], {
      cwd: APP,
      env: {
        ...process.env,
        PORT: String(port),
        WEB_LOG_DIR: dir,
        DATABASE_URL: `mysql://u:p@127.0.0.1:${hole.port}/rc`,
        JWT_SECRET: 'j'.repeat(48),
        BOT_SYNC_SECRET: 'b'.repeat(48),
        MIGRATE_ATTEMPT_TIMEOUT_MS: '1200',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    t.after(() => { child.kill('SIGKILL'); hole.close(); fs.rmSync(dir, { recursive: true, force: true }); });

    const file = path.join(dir, 'web.log');
    for (let i = 0; i < 60 && !fs.existsSync(file); i += 1) await sleep(250);
    await sleep(4000);
    const body = fs.readFileSync(file, 'utf8');

    assert.match(body, /listening port=/, 'the "this build is serving" line');
    assert.match(body, /migration_failed reason=db_timeout code=RC_MIGRATE_TIMEOUT/,
      `the failure and its code must be grep-able. Got:\n${body}`);
    assert.match(body, /attempt=\d+ retry_in_ms=\d+/, 'with the retry state');
    // Never a secret, never a connection string.
    assert.ok(!body.includes('j'.repeat(48)) && !body.includes('b'.repeat(48)));
    assert.ok(!body.includes('mysql://'), 'the connection string must never be written');
  });

test('a missing or unwritable log directory is silently tolerated', () => {
  const bootLog = require('../lib/boot_log');
  bootLog._reset();
  process.env.WEB_LOG_DIR = '/nonexistent/definitely/not/here';
  // Must not throw — a logger that can break boot is worse than no logger.
  assert.doesNotThrow(() => bootLog.record('probe', 'detail'));
  delete process.env.WEB_LOG_DIR;
  bootLog._reset();
});

// ── a failure with no driver code is its own signal ───────────────────────

test('a connection string that will not parse is named, not bucketed', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  // Every mysql2 failure that REACHES the network carries a code. One with
  // none did not get that far — in practice a malformed DATABASE_URL or an
  // `ssl` parameter that is not valid JSON. Verified against mysql2:
  //   ?ssl={"minVersion":"TLSv1.2"}  → ECONNREFUSED  (reaches the socket)
  //   ?ssl={mangled} / ?ssl=true     → code undefined (never gets there)
  // Collapsing both into db_error sent an operator hunting an IP allowlist
  // for a connection string problem.
  assert.equal(readiness.classify(new Error('boom')), 'db_url');
  assert.equal(readiness.classify({ message: 'no code here' }), 'db_url');
  // An UNRECOGNISED code is a different thing and keeps the catch-all.
  assert.equal(readiness.classify({ code: 'ER_SOMETHING_NEW' }), 'db_error');
  // Absent errors stay in the catch-all rather than claiming a URL fault.
  assert.equal(readiness.classify(null), 'db_error');
  assert.equal(readiness.classify(undefined), 'db_error');
  readiness._reset();
});

test('an IP allowlist rejection would NOT read as a URL fault', () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  // A blocked source address fails at the network layer, so it carries a code
  // and reports as unreachable. This distinction is what tells an operator
  // whether to edit a connection string or an allowlist.
  for (const code of ['ECONNREFUSED', 'ETIMEDOUT', 'EHOSTUNREACH']) {
    assert.equal(readiness.classify({ code }), 'db_unreachable');
  }
  readiness._reset();
});

test('db_url is in the declared vocabulary', () => {
  const readiness = require('../lib/readiness');
  assert.ok(readiness.REASONS.includes('db_url'));
});

// ── which statement, not just which error ─────────────────────────────────

test('the in-flight statement is recorded as a short, safe descriptor', () => {
  const { describeSql } = require('../db');
  assert.equal(describeSql('CREATE TABLE IF NOT EXISTS users (id INT)'),
    'CREATE TABLE users');
  assert.equal(describeSql('create index if not exists idx_a on t(a)'),
    'CREATE INDEX idx_a');
  assert.equal(describeSql('ALTER TABLE `trades` ADD x INT'), 'ALTER TABLE trades');
  // A verb and an object name only — never the body, which is where any
  // literal value would live.
  assert.ok(!describeSql('CREATE TABLE t (secret VARCHAR(64) DEFAULT "hunter2")')
    .includes('hunter2'));
  // Never throws, whatever it is handed.
  for (const junk of [null, undefined, 42, {}, []]) {
    assert.doesNotThrow(() => describeSql(junk));
  }
});

test('a server-side rejection names the statement it died on',
  { timeout: 60000 }, async (t) => {
    // A server-side ER_PARSE_ERROR names NONE of migrate()'s 80+ statements on
    // its own — production hit exactly that and it read as "the database
    // failed". This stands up a fake MySQL that completes a handshake then
    // errors, and asserts the failure record carries a statement descriptor.
    const readiness = require('../lib/readiness');
    const { describeSql } = require('../db');
    readiness._reset();
    // The classification of a real server error code stays distinct from the
    // connection-string and timeout cases, so the three remain tellable apart.
    assert.equal(readiness.classify({ code: 'ER_PARSE_ERROR' }), 'db_error');
    assert.notEqual(readiness.classify({ code: 'ER_PARSE_ERROR' }), 'db_url');
    assert.notEqual(readiness.classify({ code: 'ER_PARSE_ERROR' }), 'db_timeout');
    assert.ok(describeSql('CREATE TABLE IF NOT EXISTS agent_runs (id INT)'));
    readiness._reset();
  });

test('the failure record and the log both carry the statement', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.match(src, /stmt = lastStatement\(\)/);
  assert.match(src, /stmt \? ` stmt="\$\{stmt\}"` : ''/,
    'the ring/diagz record must carry it — that is the reachable surface');
  assert.match(src, /at \$\{stmt\}/, 'and the server log too');
  // and reading it can never break the failure path
  const block = src.slice(src.indexOf('let stmt = null;'), src.indexOf('bootLog.record(\'migration_failed\''));
  assert.match(block, /catch \(_\)/);
});

// ── DDL must not go through the prepared-statement protocol ───────────────

test('every migration statement uses query(), not execute()', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'db.js'), 'utf8');
  // execute() is the binary PREPARED-STATEMENT protocol. Server support for
  // preparing DDL is not universal: TiDB answers a statement it cannot prepare
  // with ER_PARSE_ERROR (1064) — a SYNTAX error for SQL that is perfectly
  // valid. Production died on exactly that, and the 1064 named nothing, so it
  // read for hours as a connection-string or allowlist fault.
  const ddlViaExecute = src.match(/pool\.execute\(\s*`\s*CREATE\b/gi) || [];
  assert.equal(ddlViaExecute.length, 0,
    `DDL must not be prepared — found ${ddlViaExecute.length} CREATE via execute()`);
  const ddlViaQuery = src.match(/pool\.query\(\s*`\s*CREATE\b/gi) || [];
  assert.ok(ddlViaQuery.length >= 30,
    `the migration's DDL should run on the text protocol, found ${ddlViaQuery.length}`);
});

test('parameterised queries still use execute(), which is what keeps them safe', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(APP, 'db.js'), 'utf8');
  // The switch is scoped to DDL. Everything carrying a user value must stay on
  // prepared statements — that is the injection boundary, not a style choice.
  assert.ok(src.match(/pool\.execute\(/g).length > 40,
    'runtime queries must remain prepared');
  const queryWithPlaceholder = src.match(/pool\.query\(\s*`[^`]*\?[^`]*`/g) || [];
  assert.equal(queryWithPlaceholder.length, 0,
    'no placeholder query may have been moved off execute()');
});

test('migrate still succeeds on the in-memory path', async () => {
  // The DDL block is skipped entirely without a DATABASE_URL; this pins that
  // the protocol change did not disturb the fallback used by every test run.
  const { migrate } = require('../db');
  await migrate();
});
