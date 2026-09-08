'use strict';
/**
 * Setting WEB_CREDS_KEY seals nothing that already exists.
 *
 * `totp_secret_at_rest.test.js` covers the encryption itself. This file covers
 * the half that was missing afterwards: WHICH ROWS ARE ACTUALLY SEALED, and
 * whether anything says so.
 *
 * The trap is that fixing it removes the evidence. `config_audit` warns while
 * the key is unset — "new 2FA secrets are stored unencrypted" — and the moment
 * an operator sets the key that warning stops, `secretsAreSealed()` starts
 * answering true, and `/2fa/setup` starts telling every enrolment its seed is
 * encrypted at rest. None of that touched a single existing row. `/2fa/enable`
 * migrates the row it enables and refuses to run for an account that is already
 * enabled, so an enrolled legacy seed has no path to encryption whatsoever.
 *
 * The deploy that fixes the problem is the deploy that hides what it did not
 * fix, and the surface left behind is a deployment-level heuristic answering a
 * row-level question. That is the shape CLAUDE.md spends most of its guard
 * tests on, on the column holding a permanent second factor.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const totp = require('../lib/totp');
const seeds = require('../lib/totp_seed_audit');

const KEY_A = Buffer.alloc(32, 7).toString('base64');
const KEY_B = Buffer.alloc(32, 9).toString('base64');

function withKey(key, fn) {
  const before = process.env.WEB_CREDS_KEY;
  if (key === null) delete process.env.WEB_CREDS_KEY;
  else process.env.WEB_CREDS_KEY = key;
  try { return fn(); } finally {
    if (before === undefined) delete process.env.WEB_CREDS_KEY;
    else process.env.WEB_CREDS_KEY = before;
  }
}

/**
 * The async sibling, and it is not a nicety.
 *
 * `withKey` restores the environment in a synchronous `finally`, so wrapping an
 * async call with it restores the key the moment the promise is RETURNED —
 * before the first `await` inside has resumed. Three tests here failed that way
 * on correct code: `auditSealedSeeds` read a key that was already gone and
 * classified every sealed row as unreadable. The assertion was wrong, not the
 * module, which is the check worth making before editing the thing under test.
 */
async function withKeyAsync(key, fn) {
  const before = process.env.WEB_CREDS_KEY;
  if (key === null) delete process.env.WEB_CREDS_KEY;
  else process.env.WEB_CREDS_KEY = key;
  try { return await fn(); } finally {
    if (before === undefined) delete process.env.WEB_CREDS_KEY;
    else process.env.WEB_CREDS_KEY = before;
  }
}

const PLAIN = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
const SEALED_A = withKey(KEY_A, () => totp.sealSecret(PLAIN));

/** A pool whose one query answers with these rows. */
function poolOf(rows) {
  return { execute: async () => [rows] };
}

/** A pool whose query fails the way a dead database does. */
function deadPool(code = 'ECONNREFUSED') {
  const err = new Error('connect ECONNREFUSED 10.0.0.5:3306 (user=runeclaw password=hunter2)');
  err.code = code;
  return { execute: async () => { throw err; } };
}

function capture() {
  const lines = [];
  return { lines, warn: (s) => lines.push(String(s)), error: (s) => lines.push(String(s)) };
}

// ── the row-level reading, which did not exist ───────────────────────────────

test('secretIsSealed answers about THE ROW, three-valued', () => {
  withKey(KEY_A, () => {
    assert.strictEqual(totp.secretIsSealed(SEALED_A), true);
    assert.strictEqual(totp.secretIsSealed(PLAIN), false);
    // NOT false. An account that never enrolled does not have an unencrypted
    // seed, and a bare boolean would invent one for every user on the site.
    for (const nothing of ['', null, undefined]) {
      assert.strictEqual(totp.secretIsSealed(nothing), null, String(nothing));
    }
  });
});

test('a sealed row is still sealed when the key is gone', () => {
  // SHAPE, NOT READABILITY. A row sealed to a key that has since been rotated
  // away is very much encrypted at rest — it is `openSecret` that reports
  // whether anyone can read it. Answering this one from `isConfigured()` would
  // tell a user their stored seed turned back into plaintext when an operator
  // changed an environment variable.
  withKey(null, () => {
    assert.strictEqual(totp.secretIsSealed(SEALED_A), true);
    assert.strictEqual(totp.secretsAreSealed(), false, 'the deployment cannot seal');
  });
});

// ── four outcomes, because three of them look alike from a distance ──────────

test('classify separates a healthy sealed row from one nobody can open', () => {
  withKey(KEY_A, () => {
    assert.strictEqual(seeds.classify(SEALED_A), 'sealed');
    assert.strictEqual(seeds.classify(PLAIN), 'plaintext');
    assert.strictEqual(seeds.classify(''), 'absent');
  });
  withKey(KEY_B, () => {
    // THE ROTATED KEY. Every account here fails its second factor on the next
    // login, and from the stored value alone it is indistinguishable from the
    // good case — which is exactly why the audit must open the row and not
    // just look at it.
    assert.strictEqual(seeds.classify(SEALED_A), 'unreadable');
  });
  withKey(null, () => {
    assert.strictEqual(seeds.classify(SEALED_A), 'unreadable',
      'with no key at all a sealed row is unreadable, not sealed-and-fine');
  });
});

test('tally always reports all four counts', () => {
  withKey(KEY_A, () => {
    const t = seeds.tally([SEALED_A, PLAIN, PLAIN, '', null]);
    assert.deepStrictEqual(t, { sealed: 1, plaintext: 2, unreadable: 0, absent: 2 });
  });
});

// ── the backfill's decision, which is where a mistake locks people out ───────

test('a plaintext row is sealed to something that opens back to it', () => {
  withKey(KEY_A, () => {
    const { action, next } = seeds.resealRow(PLAIN);
    assert.strictEqual(action, 'seal');
    assert.notStrictEqual(next, PLAIN);
    assert.ok(!next.includes(PLAIN), 'the seed survives verbatim inside the envelope');
    assert.strictEqual(totp.openSecret(next), PLAIN, 'the account can still log in');
  });
});

test('with no key, resealing REFUSES rather than writing the seed back', () => {
  // `sealSecret` passes plaintext through when unconfigured — deliberately, so
  // an unkeyed deployment still works. A backfill that trusted it would UPDATE
  // every row to the value it already held and print a clean sweep, which is
  // the one outcome worse than not running: an operator who believes the seeds
  // are now encrypted and has a successful run to point at.
  withKey(null, () => {
    assert.deepStrictEqual(seeds.resealRow(PLAIN), { action: 'unsealable', next: null });
  });
});

test('a row that cannot be read is never rewritten', () => {
  withKey(KEY_B, () => {
    // Sealed under A. Opening it yields null; sealing null would replace a row
    // somebody can still recover with key A by one nobody can recover at all.
    assert.deepStrictEqual(seeds.resealRow(SEALED_A), { action: 'unreadable', next: null });
  });
});

test('a stored value that is not a seed is refused, not sealed as one', () => {
  // The reachable half of the guard that used to sit above the round trip.
  // `openSecret` coerces with `stored || ''` and the shape test with an
  // explicit null check, so a driver handing back a non-string — the two
  // disagree on exactly those — is a value `classify` calls plaintext and
  // `openSecret` will not return. Sealing it writes a valid envelope holding
  // garbage over a row somebody is authenticating against.
  withKey(KEY_A, () => {
    assert.deepStrictEqual(seeds.resealRow(0), { action: 'unsealable', next: null });
  });
});

test('an already-sealed row and an empty one are both left alone', () => {
  withKey(KEY_A, () => {
    assert.deepStrictEqual(seeds.resealRow(SEALED_A), { action: 'skip', next: null });
    assert.deepStrictEqual(seeds.resealRow(''), { action: 'absent', next: null });
  });
});

test('the backfill is idempotent — a second pass has nothing to do', () => {
  withKey(KEY_A, () => {
    const first = seeds.resealRow(PLAIN);
    assert.strictEqual(seeds.resealRow(first.next).action, 'skip');
  });
});

// ── the boot probe: what an operator is actually told ────────────────────────

test('plaintext rows are named and counted after the key is set', async () => {
  const log = capture();
  const findings = await withKeyAsync(KEY_A, () => seeds.auditSealedSeeds({
    pool: poolOf([{ stored: PLAIN }, { stored: PLAIN }, { stored: SEALED_A }]),
    log,
  }));
  assert.strictEqual(findings.length, 1, JSON.stringify(findings));
  const msg = findings[0].msg;
  assert.match(msg, /^2 enrolled 2FA seed\(s\) are stored in the clear\./,
    'the count is the whole point — a warning without one reads as a possibility');
  assert.match(msg, /reseal_totp_secrets\.js --apply/, 'the remedy is named');
  assert.ok(log.lines.some((l) => l.includes('TOTP_AT_REST')), 'it reached the log');
});

test('with no key the warning says nothing can seal them', async () => {
  const findings = await withKeyAsync(null, () => seeds.auditSealedSeeds({
    pool: poolOf([{ stored: PLAIN }]), log: capture(),
  }));
  assert.strictEqual(findings.length, 1);
  assert.match(findings[0].msg, /WEB_CREDS_KEY is not usable/,
    'telling an operator to run the backfill when there is no key to seal with '
    + 'sends them to a script that refuses');
});

test('rows sealed to a key this deployment lost get their own finding', async () => {
  const findings = await withKeyAsync(KEY_B, () => seeds.auditSealedSeeds({
    pool: poolOf([{ stored: SEALED_A }, { stored: SEALED_A }]), log: capture(),
  }));
  assert.strictEqual(findings.length, 1, JSON.stringify(findings));
  assert.match(findings[0].msg, /2 sealed 2FA seed\(s\) will not open/);
  assert.match(findings[0].msg, /cannot pass a second-factor check/,
    'the consequence, not just the count — this is a live outage for those accounts');
});

test('an all-sealed tree says nothing at all', async () => {
  const log = capture();
  const findings = await withKeyAsync(KEY_A, () => seeds.auditSealedSeeds({
    pool: poolOf([{ stored: SEALED_A }]), log,
  }));
  assert.deepStrictEqual(findings, []);
  assert.deepStrictEqual(log.lines, [], 'a clean boot must stay quiet or the next one is skimmed');
});

test('an unreachable database is UNKNOWN, never zero plaintext seeds', async () => {
  // The rule this whole subsystem is about, turned on its own instrument. A
  // boot audit that reports "no plaintext seeds" because the query failed is a
  // confident all-clear assembled from no data — and it would be believed,
  // because it is the same output as the healthy case.
  const log = capture();
  const findings = await withKeyAsync(KEY_A, () => seeds.auditSealedSeeds({
    pool: deadPool(), log,
  }));
  assert.strictEqual(findings.length, 1, JSON.stringify(findings));
  assert.match(findings[0].msg, /could not be checked/);
  assert.match(findings[0].msg, /unknown, not zero/);
  assert.ok(log.lines.some((l) => l.includes('ECONNREFUSED')), 'the code is diagnosable');
});

test('the failure line carries the driver CODE and not its message', async () => {
  // mysql2 puts the connection string in the error text, and this is a boot log
  // an operator pastes into a ticket. Same discipline as lib/boot_log.js.
  const log = capture();
  await withKeyAsync(KEY_A, () => seeds.auditSealedSeeds({ pool: deadPool(), log }));
  const all = log.lines.join('\n');
  assert.ok(!all.includes('hunter2'), 'a credential reached the log');
  assert.ok(!all.includes('10.0.0.5'), 'a host reached the log');
});

test('a driver error with no code still says could-not-check', async () => {
  const pool = { execute: async () => { throw new Error('boom'); } };
  const findings = await withKeyAsync(KEY_A, () => seeds.auditSealedSeeds({ pool, log: capture() }));
  assert.strictEqual(findings.length, 1);
  assert.match(findings[0].msg, /could not be checked \(no-code\)/);
});

// ── the wiring, which is the half a green unit test cannot see ───────────────

test('the boot path actually calls the probe', () => {
  // A module nothing calls is indistinguishable from one that does not work,
  // and this one exists only to be run at boot.
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
  assert.match(src, /totp_seed_audit'\)\.auditSealedSeeds\(\)/,
    'server.js never runs the seed audit, so the count nobody takes stays untaken');
  assert.match(src, /auditSealedSeeds\(\)\s*\n?\s*\.catch\(/,
    'an unhandled rejection from a diagnostic must not be able to break boot');
});

test('/2fa/status reports THIS ROW, not the deployment key', () => {
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8'));
  const status = src.slice(src.indexOf("'/2fa/status'"), src.indexOf("'/2fa/setup'"));
  assert.ok(status.length > 100, 'the /2fa/status handler was not found');
  assert.match(status, /encrypted_at_rest: totp\.secretIsSealed\(rows\[0\]\.totp_secret\)/,
    'a user enrolled before WEB_CREDS_KEY existed is told their seed is encrypted '
    + 'by the very deploy that left it in the clear');
  assert.ok(!/secretsAreSealed/.test(status),
    'the deployment-level answer is a claim about the NEXT write, and using it here '
    + 'is how a row-level question got a heuristic for an answer in the first place');
});

// ── the backfill, driven: three exit codes and what each one wrote ───────────

const backfill = require('../scripts/reseal_totp_secrets');

/** A db whose SELECT answers with these rows and whose UPDATEs are recorded. */
function fakeDb(rows, { backend = 'mysql', affected = 1, selectThrows = null } = {}) {
  const writes = [];
  return {
    writes,
    backend: () => backend,
    pool: {
      execute: async (sql, params) => {
        if (/^SELECT/i.test(sql)) {
          if (selectThrows) throw selectThrows;
          return [rows];
        }
        writes.push({ sql, params });
        return [{ affectedRows: affected }];
      },
    },
  };
}

const quiet = () => {};

test('a dry run counts the plaintext rows, writes nothing, and exits 1', async () => {
  const db = fakeDb([{ id: 1, stored: PLAIN }, { id: 2, stored: SEALED_A }]);
  const said = [];
  const code = await withKeyAsync(KEY_A, () => backfill.main(
    { db, apply: false, out: (s) => said.push(s) }));
  assert.strictEqual(code, 1, 'work outstanding is not exit 0');
  assert.deepStrictEqual(db.writes, [], 'a dry run wrote to the users table');
  const out = said.join('\n');
  assert.match(out, /would seal\s+: 1/);
  assert.match(out, /already sealed : 1/);
  assert.match(out, /Re-run with --apply/);
});

test('--apply seals the plaintext row, matching the OLD value in the WHERE', async () => {
  const db = fakeDb([{ id: 7, stored: PLAIN }]);
  const code = await withKeyAsync(KEY_A, () => backfill.main(
    { db, apply: true, out: quiet }));
  assert.strictEqual(code, 0);
  assert.strictEqual(db.writes.length, 1);
  const [w] = db.writes;
  // COMPARE AND SWAP. Without the third parameter a user re-enrolling mid-run
  // has their fresh secret overwritten by the sealed form of the one it
  // replaced — an authenticator they set up a second ago, silently broken.
  assert.match(w.sql, /UPDATE users SET totp_secret = \? WHERE id = \? AND totp_secret = \?/);
  assert.deepStrictEqual(w.params.slice(1), [7, PLAIN]);
  assert.strictEqual(withKey(KEY_A, () => totp.openSecret(w.params[0])), PLAIN,
    'the value written back does not open to the seed the account is using');
  assert.ok(!w.params[0].includes(PLAIN), 'the seed is verbatim inside what was stored');
});

test('a row that changed under the run is counted, not overwritten twice', async () => {
  const db = fakeDb([{ id: 7, stored: PLAIN }], { affected: 0 });
  const said = [];
  const code = await withKeyAsync(KEY_A, () => backfill.main(
    { db, apply: true, out: (s) => said.push(s) }));
  assert.strictEqual(code, 1, 'a raced row is unfinished work, not a clean sweep');
  assert.match(said.join('\n'), /raced\s+: 1/);
});

test('an all-sealed table is exit 0 with nothing written', async () => {
  const db = fakeDb([{ id: 1, stored: SEALED_A }]);
  const code = await withKeyAsync(KEY_A, () => backfill.main({ db, apply: true, out: quiet }));
  assert.strictEqual(code, 0);
  assert.deepStrictEqual(db.writes, []);
});

test('it refuses a store that does not persist, and refuses without a key', async () => {
  // Exit 2 is COULD NOT RUN, and it is a third outcome for the same reason
  // verify_deploy.sh has one: an UPDATE against the in-memory store vanishes at
  // exit and would still print a clean sweep, which is a deploy reporting
  // success onto a stale tree with a different noun.
  const mem = fakeDb([{ id: 1, stored: PLAIN }], { backend: 'memory' });
  const said = [];
  assert.strictEqual(
    await withKeyAsync(KEY_A, () => backfill.main({ db: mem, apply: true, out: (s) => said.push(s) })),
    2);
  assert.deepStrictEqual(mem.writes, []);
  assert.match(said.join('\n'), /REFUSING/);

  // No key: sealSecret passes plaintext through, so a run would UPDATE every
  // row to the value it already held and report success having done nothing.
  const db = fakeDb([{ id: 1, stored: PLAIN }]);
  assert.strictEqual(
    await withKeyAsync(null, () => backfill.main({ db, apply: true, out: quiet })), 2);
  assert.deepStrictEqual(db.writes, []);
});

test('a failed SELECT is exit 2 — unknown, not "nothing to do"', async () => {
  const err = new Error('connect ECONNREFUSED 10.0.0.5:3306 (password=hunter2)');
  err.code = 'ECONNREFUSED';
  const db = fakeDb([], { selectThrows: err });
  const said = [];
  const code = await withKeyAsync(KEY_A, () => backfill.main(
    { db, apply: true, out: (s) => said.push(s) }));
  assert.strictEqual(code, 2, 'exiting 0 here tells a wrapper the seeds are sealed');
  const out = said.join('\n');
  assert.match(out, /unknown, not zero/);
  assert.ok(!out.includes('hunter2'), 'a credential reached the operator log');
});

test('the default is the run that changes nothing', async () => {
  // `--apply` is a sentence you have to mean. Driving `main` with no `apply`
  // key at all takes the same path the command line does.
  const db = fakeDb([{ id: 1, stored: PLAIN }]);
  await withKeyAsync(KEY_A, () => backfill.main({ db, out: quiet }));
  assert.deepStrictEqual(db.writes, [],
    'running the script with no arguments wrote to the users table');
});

test('the account page warns only when the seed is MEASURED unencrypted', () => {
  // Driven, not grepped. `tfaAtRestNote` is inline in index.html, and the
  // standing lesson from the engine-status chip is that a source window over
  // an inline renderer passes with the literal present and the logic inverted.
  const vm = require('node:vm');
  const { blockBetween } = require('./helpers/block');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  const fn = blockBetween(html, 'function tfaAtRestNote', 'async function tfaRefresh',
    { label: 'tfaAtRestNote' });
  const ctx = { out: null };
  vm.createContext(ctx);
  vm.runInContext(`${fn}\nout = tfaAtRestNote;`, ctx);

  const bare = ctx.out(false);
  assert.match(bare, /stored unencrypted/);
  assert.match(bare, /re-enrolling 2FA will store it encrypted/,
    'a warning a user cannot act on is one they learn to skip — the remedy is '
    + 'theirs here, because /2fa/enable reseals the row it enables');

  // The three readings that are NOT a measurement of "in the clear". `null` is
  // an account with no secret, `undefined` a server that did not say. Warning
  // on either invents the finding, which is the defect this whole file is
  // about, one level up.
  for (const quiet of [true, null, undefined]) {
    assert.strictEqual(ctx.out(quiet), '', `it spoke for ${String(quiet)}`);
  }
  // And no badge for the good case: "encrypted ✓" is a claim that earns
  // nothing and trains people past the line that matters.
  assert.strictEqual(ctx.out(true), '');
});

test('the CI parse gate compiles scripts/ too', () => {
  // reseal_totp_secrets.js is operator-run: nothing imports it, no test loads
  // it as a module by default, and the day it is needed is an incident. A
  // syntax error in it would have shipped.
  const ci = fs.readFileSync(
    path.join(__dirname, '..', '..', '.github', 'workflows', 'ci.yml'), 'utf8');
  // ANCHORED ON THE JOB, not the step name. `token/` has a step by the same
  // name and it comes first in the file, so the obvious `indexOf` read a
  // different job's glob and failed on a correct edit — the same misfire this
  // repo keeps recording, one directory over.
  const app = ci.slice(ci.indexOf('name: Web app (express)'));
  const step = app.slice(app.indexOf('Parse — every script must at least compile'));
  assert.match(step.slice(0, 400), /scripts\/\*\.js/,
    'app/scripts/ is not compiled by CI');
});
