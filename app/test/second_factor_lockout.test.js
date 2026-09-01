/**
 * Every second-factor check counts against ONE per-account budget.
 *
 * From the audit's confirmed-not-remediated tier:
 * "web-authz: /api/auth/2fa/disable has no throttle, lockout or attempt
 * counter (HIGH)".
 *
 * It was true, and narrower than the whole defect. Four places check a TOTP
 * code or a backup code:
 *
 *   login          — HAD a per-account lockout (RC-AUD-026), under a comment
 *                    saying codes "can't be brute-forced past the rate limits"
 *   /2fa/disable    — NOTHING. No limiter, no lockout, no counter. The route
 *                    that REMOVES the second factor accepted unlimited guesses
 *                    at a six-digit code and at eight static backup codes,
 *                    behind nothing but a session — and surviving a stolen
 *                    session is the entire reason 2FA exists.
 *   /2fa/enable     — nothing.
 *   stepUpBlock     — nothing of its own; bounded only by its routes' 6-20/min
 *                    rate limits, so a patient grinder had days. Its own
 *                    docstring names the threat: "a stolen web session ... must
 *                    not be enough to move real money".
 *
 * The counter could not simply be imported into stepup.js — auth.js requires
 * stepup.js, so that closes a cycle. It lives in lib/second_factor_lockout.js
 * now and both sides share the one Map, which is what makes the budget an
 * account budget rather than a per-route one.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const lockout = require('../lib/second_factor_lockout');
const { stepUpBlock } = require('../lib/stepup');
const totp = require('../lib/totp');

const APP = path.join(__dirname, '..');

function validCode(secret) {
  const step = Math.floor(Date.now() / 1000 / 30);
  return totp.hotp(secret, step);
}

test.beforeEach(() => lockout._reset());

// ── the counter itself ────────────────────────────────────────────────────

test('an account is open until it burns its budget', () => {
  const k = 'uid:1';
  assert.strictEqual(lockout.checkAccountLockout(k), true);
  for (let i = 0; i < lockout.MAX_FAILURES - 1; i++) lockout.recordAccountFailure(k);
  assert.strictEqual(lockout.checkAccountLockout(k), true, 'locked one guess early');
  lockout.recordAccountFailure(k);
  assert.strictEqual(lockout.checkAccountLockout(k), false, 'never locked');
});

test('a success clears the budget', () => {
  const k = 'uid:2';
  for (let i = 0; i < lockout.MAX_FAILURES; i++) lockout.recordAccountFailure(k);
  assert.strictEqual(lockout.checkAccountLockout(k), false);
  lockout.clearAccountFailures(k);
  assert.strictEqual(lockout.checkAccountLockout(k), true);
});

test('accounts do not share a budget', () => {
  // Through uidKey(), not literals: a mutation making uidKey return a
  // constant — every account in one bucket — survived the literal version.
  const a = lockout.uidKey(3);
  const b = lockout.uidKey(4);
  assert.notStrictEqual(a, b, 'uidKey maps two accounts to the same bucket');
  for (let i = 0; i < lockout.MAX_FAILURES; i++) lockout.recordAccountFailure(a);
  assert.strictEqual(lockout.checkAccountLockout(a), false);
  assert.strictEqual(lockout.checkAccountLockout(b), true,
    'one account locking out took another with it');
});

test('an expired lockout does not hand back a fresh budget', () => {
  // Reaches the `count < MAX_FAILURES` return, which no sequence of real
  // calls can reach as false — a mutation to `return true` survived the whole
  // suite until this existed. The bound is MAX_FAILURES per WINDOW_MS, and
  // "the lockout expired" must not mean "start over".
  const k = lockout.uidKey(5);
  lockout._seed(k, {
    count: lockout.MAX_FAILURES,
    firstAttempt: Date.now() - (lockout.LOCKOUT_MS + 1000),
    lockedUntil: Date.now() - 1000,          // expired, still inside the window
  });
  assert.strictEqual(lockout.checkAccountLockout(k), false,
    'the 5-minute lockout expired and the account got 8 more guesses, turning '
    + 'the bound into 8 per 5 minutes instead of 8 per 15');
});

test('a stale entry past the window is forgiven', () => {
  const k = lockout.uidKey(6);
  lockout._seed(k, {
    count: lockout.MAX_FAILURES,
    firstAttempt: Date.now() - (lockout.WINDOW_MS + 1000),
    lockedUntil: Date.now() - 1000,
  });
  assert.strictEqual(lockout.checkAccountLockout(k), true,
    'an account stayed locked forever — the window never forgives');
});

test('an absent key does not lock the world', () => {
  assert.strictEqual(lockout.checkAccountLockout(''), true);
  assert.strictEqual(lockout.checkAccountLockout(undefined), true);
  lockout.recordAccountFailure(undefined);   // must not throw
});

test('email and uid key spaces cannot collide', () => {
  assert.ok(lockout.uidKey(7).startsWith('uid:'));
  assert.ok(!lockout.uidKey(7).includes('@'));
});

// ── stepUpBlock now spends from that budget ───────────────────────────────

test('a wrong step-up code is counted', () => {
  const secret = totp.generateSecret();
  const k = 'uid:10';
  const blk = stepUpBlock(1, secret, '000000', 'need code', k);
  assert.strictEqual(blk.status, 401);
  lockout.recordAccountFailure(k);           // one more
  for (let i = 0; i < lockout.MAX_FAILURES - 2; i++) lockout.recordAccountFailure(k);
  assert.strictEqual(lockout.checkAccountLockout(k), false,
    'the wrong code did not count toward the budget');
});

test('a MISSING step-up code is not counted', () => {
  const secret = totp.generateSecret();
  const k = 'uid:11';
  for (let i = 0; i < lockout.MAX_FAILURES + 4; i++) {
    stepUpBlock(1, secret, '', 'need code', k);
  }
  assert.strictEqual(lockout.checkAccountLockout(k), true,
    'the discovery handshake — the browser asks, gets 401, then prompts — '
    + 'was counted as a guess, which locks people out through ordinary use');
});

test('a locked-out account is refused before the code is even checked', () => {
  const secret = totp.generateSecret();
  const k = 'uid:12';
  for (let i = 0; i < lockout.MAX_FAILURES; i++) lockout.recordAccountFailure(k);
  const blk = stepUpBlock(1, secret, validCode(secret), 'x', k);
  assert.ok(blk, 'a valid code got through a locked-out account');
  assert.strictEqual(blk.status, 429);
  assert.strictEqual(blk.body.error, 'too_many_attempts');
});

test('a correct step-up code clears the budget', () => {
  const secret = totp.generateSecret();
  const k = 'uid:13';
  for (let i = 0; i < lockout.MAX_FAILURES - 1; i++) lockout.recordAccountFailure(k);
  assert.strictEqual(stepUpBlock(1, secret, validCode(secret), 'x', k), null);
  assert.strictEqual(lockout.checkAccountLockout(k), true);
});

test('an unenrolled account is untouched by any of this', () => {
  const k = 'uid:14';
  for (let i = 0; i < lockout.MAX_FAILURES; i++) lockout.recordAccountFailure(k);
  assert.strictEqual(stepUpBlock(0, null, undefined, 'x', k), null,
    'a 2FA-less account was gated by a lockout it can never satisfy');
});

// ── wiring: every call site must actually pass a key ──────────────────────

test('every stepUpBlock call site passes an account key', () => {
  // The optional parameter is the whole risk: a call that omits it is a
  // perfectly working guard that counts nothing, which is the #999 shape —
  // code present, never reached in the way that matters.
  const files = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'test') continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.name.endsWith('.js')) files.push(full);
    }
  };
  walk(APP);

  const sites = [];
  for (const f of files) {
    const src = fs.readFileSync(f, 'utf8');
    // The call, plus up to three continuation lines — these calls wrap.
    const re = /stepUpBlock\(([\s\S]{0,320}?)\);/g;
    let m;
    while ((m = re.exec(src)) !== null) {
      if (/require\(/.test(m[0])) continue;          // the import line
      sites.push({ file: path.relative(APP, f), call: m[0] });
    }
  }

  assert.ok(sites.length >= 5,
    `only found ${sites.length} call sites — the scan has drifted`);
  const unkeyed = sites.filter(s => !/uidKey\(/.test(s.call));
  assert.deepStrictEqual(unkeyed.map(s => s.file), [],
    'stepUpBlock call site(s) with no account key — the guard runs and counts '
    + 'nothing:\n' + unkeyed.map(s => `${s.file}: ${s.call.slice(0, 120)}`).join('\n'));
});

test('the routes that check a code all consult the lockout', () => {
  const src = fs.readFileSync(path.join(APP, 'auth.js'), 'utf8');
  // Strip line comments so the prose explaining this fix cannot satisfy it.
  const code = src.split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n');
  for (const route of ['/2fa/disable', '/2fa/enable']) {
    const i = code.indexOf(`router.post('${route}'`);
    assert.ok(i !== -1, `${route} is gone`);
    const body = code.slice(i, i + 2600);
    assert.match(body, /checkAccountLockout\(/,
      `${route} verifies a code without consulting the lockout`);
    assert.match(body, /recordAccountFailure\(/,
      `${route} does not count a failed code`);
  }
});
