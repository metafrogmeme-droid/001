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
 * state lives here instead and both sides share this module's one counter —
 * `require` caches, so a failure recorded at the step-up counts against the
 * same account at login, which is the point. Guard at the boundary and new
 * callers inherit it, rather than five call sites each remembering to count.
 *
 * KEYED BY ACCOUNT, NOT IP, deliberately — the per-IP limiter in auth.js does
 * not stop distributed or rotating-IP grinding at one account. Callers pass a
 * stable account key (normalised email at login, `uid:<id>` where only the id
 * is in hand); the two spaces cannot collide because one always has an `@`.
 *
 * SHARED BY EVERY INSTANCE (2026-10-11). This was a Map, and its comment
 * said: "In a multi-replica deployment this bounds an attacker to N failures
 * per replica, not N overall ... a shared store is the fix." The web host
 * replaces its instance about every 70 seconds with overlap, which is that
 * deployment: the count now lives in the database through
 * lib/durable_attempts.js, and falls back to this process's memory only while
 * the database does not answer. Every function is async because of it.
 */

const { attemptCounter } = require('./durable_attempts');

const WINDOW_MS = 15 * 60 * 1000;      // matches auth.js RATE_LIMIT_WINDOW
const MAX_FAILURES = 8;                // matches auth.js ACCOUNT_RATE_LIMIT_MAX
// After this the entry is NOT forgiven: the count survives, so
// checkAccountLockout keeps refusing for the remainder of WINDOW_MS. The
// effective bound is MAX_FAILURES per WINDOW_MS, not per LOCKOUT_MS — the
// stricter of the two readings, and the one auth.js has always had. Named
// "lockout" because that is what the login code called it; pinned by a test
// so nobody "fixes" it into handing out a fresh budget every 5 minutes.
const LOCKOUT_MS = 5 * 60 * 1000;      // matches auth.js LOCKOUT_DURATION

const counter = attemptCounter({ name: 'acct', windowMs: WINDOW_MS, max: MAX_FAILURES, lockoutMs: LOCKOUT_MS });

/** A stable key for an account when only its numeric id is in hand. */
function uidKey(userId) {
  return `uid:${userId}`;
}

/** True when this account may attempt another second-factor check. */
async function checkAccountLockout(key) {
  return counter.check(key);            // a missing key never locks the world
}

/** Count one failed second-factor check against this account. */
async function recordAccountFailure(key) {
  await counter.record(key);
}

/** A success clears the account's failures. */
async function clearAccountFailures(key) {
  await counter.clear(key);
}

/** Test seam: forget everything. Never called by production code. */
async function _reset() {
  await counter._reset();
}

/**
 * Test seam: plant an entry directly.
 *
 * The `count < MAX_FAILURES` return is unreachable-as-false while
 * `lockedUntil` is in the future, so no sequence of real calls can exercise
 * it — and a mutation replacing it with `return true` survived the whole
 * suite. It IS reachable once a lockout has expired inside the 15-minute
 * window, and it is what keeps the account shut for the rest of that window
 * rather than handing back a fresh budget every 5 minutes. Planting the state
 * is the only way to assert that, so the seam exists rather than the
 * behaviour going untested. It plants the SHARED entry, as another instance
 * would have left it.
 */
async function _seed(key, entry) {
  await counter._seed(key, entry);
}

/** Test seam: drop this process's fallback counts, as a fresh instance has none. */
function _clearMemory() {
  counter._clearMemory();
}

module.exports = {
  checkAccountLockout, recordAccountFailure, clearAccountFailures,
  uidKey, _reset, _seed, _clearMemory,
  WINDOW_MS, MAX_FAILURES, LOCKOUT_MS,
};
