'use strict';
//
// `app.listen` had NO 'error' handler, and a failed bind is the one failure
// that guarantees the site never comes up.
//
// Everything around that line is built to survive a fault: the listen happens
// BEFORE migrate so a database outage costs only the DB-backed panels, the
// migration retries forever rather than exiting, /readyz explains itself. None
// of it helps if the bind fails — an unhandled 'error' event terminates the
// process on a raw stack trace. On 2026-09-22 PORT defaulted to 8080 (the
// bot's gateway on the same host), the bind lost, and the site served 520
// while the reason went nowhere an operator was looking.
//
// DRIVEN: the real server.js is spawned against a port that is already taken.
// A source scan could see the handler and not whether anything reaches it —
// which is the only question here.

const test = require('node:test');
const assert = require('node:assert');
const net = require('net');
const path = require('path');
const { spawnSync } = require('child_process');

const SERVER = path.join(__dirname, '..', 'server.js');

function bootAgainstTakenPort(port, extraEnv) {
  return spawnSync(process.execPath, [SERVER], {
    env: {
      ...process.env,
      PORT: String(port),
      // Past the fail-closed boot checks so we reach the listen.
      BOT_SYNC_SECRET: 'x'.repeat(48),
      JWT_SECRET: 'y'.repeat(48),
      NODE_ENV: 'test',
      ...(extraEnv || {}),
    },
    encoding: 'utf8',
    timeout: 40_000,
    killSignal: 'SIGKILL',
  });
}

async function holdAPort(want) {
  const blocker = net.createServer();
  await new Promise((res, rej) => {
    blocker.once('error', rej);
    blocker.listen(want || 0, '0.0.0.0', res);
  });
  return blocker;
}

// THE FIRST DRAFT OF THIS TEST WAS WORTHLESS AND THE MUTATION PROVED IT.
//
// It asserted only that EADDRINUSE and the port appeared and that the exit was
// non-zero — every one of which is ALSO true of the raw crash. Deleting the
// handler entirely left it green: Node's unhandled 'error' event exits 1 and
// prints a stack trace that happens to contain both strings. A guard that
// cannot tell the cure from the disease is not a guard.
//
// So assert what the HANDLER contributes and the crash cannot: its own FATAL
// sentence, a named remedy, and the ABSENCE of a stack trace — the operator
// reading this has a broken site and needs an instruction, not a backtrace
// through node:net.

test('a taken port produces an explanation, not a stack trace', async () => {
  const blocker = await holdAPort();
  const port = blocker.address().port;
  try {
    const out = bootAgainstTakenPort(port);
    const said = `${out.stderr || ''}${out.stdout || ''}`;

    assert.ok(said.includes(`FATAL: could not listen on port ${port}`),
      `the handler did not run — this is the raw crash:\n${said.slice(0, 1200)}`);
    assert.ok(/EADDRINUSE/.test(said), 'the reason was not named');
    assert.ok(!/^\s+at .*\(node:/m.test(said),
      `a stack trace reached the operator instead of an instruction:\n${said.slice(0, 1200)}`);
    assert.strictEqual(out.status, 1,
      'a server that cannot bind must exit, not linger looking alive');
  } finally {
    await new Promise((res) => blocker.close(res));
  }
});

test('on :8080 it names the bot — the 2026-09-22 outage, verbatim', async () => {
  // The one collision that actually happened. 8080 is the bot's aiohttp
  // gateway, so "something else has it" is true and useless; the operator
  // needs to be told WHICH something and what to set.
  let blocker;
  try {
    blocker = await holdAPort(8080);
  } catch (err) {
    // Something on this box already holds 8080 — which is the very condition
    // under test, so the child will still fail to bind. Proceed without it.
    blocker = null;
  }
  try {
    const out = bootAgainstTakenPort(8080);
    const said = `${out.stderr || ''}${out.stdout || ''}`;
    const at = said.indexOf('FATAL: could not listen on port 8080');
    assert.ok(at !== -1, `the handler did not run:\n${said.slice(0, 1200)}`);

    // ANCHORED TO THE TEXT AFTER `FATAL:`, AND A MUTATION TAUGHT THAT.
    // The startup warning above also says "bot gateway" and "PORT ... 3000",
    // so asserting over the WHOLE output matched the warning and passed with
    // the handler's remedy deleted — the same false pass this file's first
    // draft had, in a second place.
    const fatal = said.slice(at);
    assert.ok(/likeliest holder/.test(fatal),
      `the FATAL names no likely holder — the operator is told what broke and `
      + `not what to do:\n${fatal.slice(0, 800)}`);
    assert.ok(/set PORT/i.test(fatal) && /3000/.test(fatal),
      `the FATAL gives no remedy:\n${fatal.slice(0, 800)}`);
  } finally {
    if (blocker) await new Promise((res) => blocker.close(res));
  }
});

test('the collision warning is NOT disarmed by a set BOT_GATEWAY_URL', async () => {
  // It used to be gated on `!process.env.BOT_GATEWAY_URL`, so the one
  // deployment wired up correctly enough to talk to the bot — which is what
  // sets that variable — was the one told nothing.
  const blocker = await holdAPort();
  const port = blocker.address().port;
  try {
    const out = bootAgainstTakenPort(port, {
      PORT: '8080',
      BOT_GATEWAY_URL: 'http://localhost:8080',
    });
    const said = `${out.stderr || ''}${out.stdout || ''}`;
    assert.ok(/will bind :8080/.test(said),
      `the warning was suppressed by BOT_GATEWAY_URL being set:\n${said.slice(0, 1200)}`);
  } finally {
    await new Promise((res) => blocker.close(res));
  }
});


// ── is the loader REACHED? ────────────────────────────────────────────────
//
// `env_file_is_read_and_never_overrules.test.js` drives loadEnvFile directly,
// which proves the function works and NOTHING about whether server.js calls
// it. A mutation replacing the require with `() => []` left that whole suite
// green — the reachability gap that is the entire subject here, since the
// outage was a correct file nobody opened.
//
// So: write a repo-root .env, spawn the real server with the key ABSENT from
// its environment, and read back what it bound.

test('a PORT in repo-root .env reaches the real server', async (t) => {
  const envPath = path.join(__dirname, '..', '..', '.env');
  if (require('fs').existsSync(envPath)) {
    // Never clobber an operator's real file to run a test.
    t.skip('a real .env exists here; not overwriting it');
    return;
  }
  const blocker = await holdAPort();
  const chosen = blocker.address().port;
  await new Promise((res) => blocker.close(res));   // free it; .env will claim it

  require('fs').writeFileSync(envPath, `PORT=${chosen}\n`, { mode: 0o600 });
  try {
    const out = spawnSync(process.execPath, ['-e', `
      process.env.PORT = undefined; delete process.env.PORT;
      const { loadEnvFile } = require(${JSON.stringify(path.join(__dirname, '..', 'lib', 'env_file'))});
      loadEnvFile();
      process.stdout.write('PORT=' + process.env.PORT);
    `], { encoding: 'utf8', timeout: 20_000, env: { ...process.env, PORT: undefined } });
    assert.ok(String(out.stdout).includes(`PORT=${chosen}`),
      `the repo-root .env did not reach process.env: ${out.stdout} ${out.stderr}`);

    // And the server itself must use it: spawn with PORT absent entirely.
    const childEnv = { ...process.env, BOT_SYNC_SECRET: 'x'.repeat(48),
      JWT_SECRET: 'y'.repeat(48), NODE_ENV: 'test' };
    delete childEnv.PORT;
    const boot = spawnSync(process.execPath, ['-e', `
      const cp = require('child_process');
      const p = cp.spawn(process.execPath, [${JSON.stringify(SERVER)}], { env: process.env });
      let seen = '';
      p.stdout.on('data', (d) => { seen += d; if (/running on port/.test(seen)) { p.kill('SIGKILL'); process.stdout.write(seen); process.exit(0); } });
      setTimeout(() => { p.kill('SIGKILL'); process.stdout.write(seen); process.exit(0); }, 15000);
    `], { encoding: 'utf8', timeout: 25_000, env: childEnv });
    assert.ok(String(boot.stdout).includes(`running on port ${chosen}`),
      `the server did not bind the .env port — the loader is not wired:\n${boot.stdout}\n${boot.stderr}`);
  } finally {
    try { require('fs').unlinkSync(envPath); } catch (e) { /* best effort */ }
  }
});
