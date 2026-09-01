/**
 * One per-account failure counter for EVERY second-factor check.
 *
 * WHY THIS IS A MODULE AND NOT THREE LINES IN auth.js. The counter already
 * existed there (RC-AUD-026) and the login path used it, under a comment
 * saying failed codes "count toward the same lockout counters as bad
 * passwords, so codes can't be brute-forced past the rate limits."
 *
 * That was true of login and false of every other place a code is checked.
 * `POST /2fa/disable` verified a TOTP code and a backup code with NO limiter,
 * no lockout and no counter of any kind — so a stolen session, the exact
 * thing 2FA exists to survive, could grind the six-digit space at whatever
 * rate the network allowed and switch the second factor off. `stepUpBlock`,
 * which gates staking, web trades, control changes and account deletion, is
 * bounded only by its routes' 6-20/min rate limits: slow, but nothing stops a
 * patient grinder, and its own docstring names the threat it was not stopping
 * ("a stolen web session ... must not be enough to move real money").
 *
 * The counter could not simply be imported: app/auth.js requires
 * app/lib/stepup.js, so stepup.js importing auth.js would close a cycle. The
 * state lives here instead and both sides share this module's single Map —
 * `require` caches, so a failure recorded at the step-up counts against the
 * same account at login, which is the point. Guard at the boundary and new
 * callers inherit it, rather than five call sites each remembering to count.
 *
 * KEYED BY ACCOUNT, NOT IP, deliberately — the per-IP limiter in auth.js does
 * not stop distributed or rotating-IP grinding at one account. Callers pass a
 * stable account key (normalised email at login, `uid:<id>` where only the id
 * is in hand); the two spaces cannot collide because one always has an `@`.
 *
 * SINGLE PROCESS, like lib/rate_limit.js. In a multi-replica deployment this
 * bounds an attacker to N failures per replica, not N overall. That is a real
 * limit and it is stated rather than implied; a shared store is the fix if
 * this is ever fronted by more than one process.
 */

const WINDOW_MS = 15 * 60 * 1000;      // matches auth.js RATE_LIMIT_WINDOW
const MAX_FAILURES = 8;                // matches auth.js ACCOUNT_RATE_LIMIT_MAX
// After this the entry is NOT forgiven: the count survives, so
// checkAccountLockout keeps refusing for the remainder of WINDOW_MS. The
// effective bound is MAX_FAILURES per WINDOW_MS, not per LOCKOUT_MS — the
// stricter of the two readings, and the one auth.js has always had. Named
// "lockout" because that is what the login code called it; pinned by a test
// so nobody "fixes" it into handing out a fresh budget every 5 minutes.
const LOCKOUT_MS = 5 * 60 * 1000;      // matches auth.js LOCKOUT_DURATION

const attempts = new Map();            // key -> { count, firstAttempt, lockedUntil }

function _prune() {
  const now = Date.now();
  for (const [k, e] of attempts) {
    if (now - e.firstAttempt > WINDOW_MS && (!e.lockedUntil || now > e.lockedUntil)) {
      attempts.delete(k);
    }
  }
  if (attempts.size > 10000) {
    const sorted = [...attempts.entries()].sort((a, b) => a[1].firstAttempt - b[1].firstAttempt);
    for (let i = 0; i < sorted.length - 5000; i++) attempts.delete(sorted[i][0]);
  }
}
const _timer = setInterval(_prune, 60000);
if (_timer.unref) _timer.unref();      // never hold the event loop open

/** A stable key for an account when only its numeric id is in hand. */
function uidKey(userId) {
  return `uid:${userId}`;
}

/** True when this account may attempt another second-factor check. */
function checkAccountLockout(key) {
  if (!key) return true;               // nothing to key on — do not lock the world
  const now = Date.now();
  const e = attempts.get(key);
  if (!e) return true;
  if (e.lockedUntil && now < e.lockedUntil) return false;
  if (now - e.firstAttempt > WINDOW_MS) { attempts.delete(key); return true; }
  return e.count < MAX_FAILURES;
}

/** Count one failed second-factor check against this account. */
function recordAccountFailure(key) {
  if (!key) return;
  const now = Date.now();
  const e = attempts.get(key) || { count: 0, firstAttempt: now };
  e.count++;
  if (e.count >= MAX_FAILURES) e.lockedUntil = now + LOCKOUT_MS;
  attempts.set(key, e);
}

/** A success clears the account's failures. */
function clearAccountFailures(key) {
  if (key) attempts.delete(key);
}

/** Test seam: forget everything. Never called by production code. */
function _reset() {
  attempts.clear();
}

/**
 * Test seam: plant an entry directly.
 *
 * The `count < MAX_FAILURES` return below is unreachable-as-false while
 * `lockedUntil` is in the future, so no sequence of real calls can exercise
 * it — and a mutation replacing it with `return true` survived the whole
 * suite. It IS reachable once a lockout has expired inside the 15-minute
 * window, and it is what keeps the account shut for the rest of that window
 * rather than handing back a fresh budget every 5 minutes. Planting the state
 * is the only way to assert that, so the seam exists rather than the
 * behaviour going untested.
 */
function _seed(key, entry) {
  attempts.set(key, entry);
}

module.exports = {
  checkAccountLockout, recordAccountFailure, clearAccountFailures,
  uidKey, _reset, _seed,
  WINDOW_MS, MAX_FAILURES, LOCKOUT_MS,
};
