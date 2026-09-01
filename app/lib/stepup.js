/**
 * Per-action 2FA step-up for money-moving / risk-changing web actions.
 *
 * A stolen web session (the primary infostealer target — the JWT lives in
 * localStorage) must not be enough to move real money or unlock live trading.
 * When an account has 2FA enrolled, the sensitive action must carry a fresh
 * TOTP code — the same verifier the login and fixed-term-staking flows use.
 *
 * This is a PURE check: the caller passes the `totp_enabled` / `totp_secret`
 * it already read from the user row (no extra DB round-trip), so it is trivial
 * to unit-test. Fail-CLOSED: an enrolled account with a missing or wrong code
 * is blocked; a non-enrolled account passes (nothing to check).
 */

const totp = require('./totp');
const lockout = require('./second_factor_lockout');

/**
 * @param {*} enrolled  users.totp_enabled (truthy = 2FA on)
 * @param {string} secret  users.totp_secret
 * @param {string} code  the client-supplied `totp_code`
 * @param {string} detail  human message for the 401 body
 * @param {string} [accountKey]  account to count failures against — pass
 *   `lockout.uidKey(user_id)` or the normalised email. WITHOUT IT NOTHING
 *   BOUNDS A GRINDER: the routes' own 6-20/min limits let a stolen session
 *   work the six-digit space for days, and a stolen session is the exact
 *   threat the docstring above says this exists to stop. Every call site in
 *   app/ passes it, pinned by app/test/second_factor_lockout.test.js.
 * @returns {null | {status:number, body:object}} null = allowed; object = send it
 */
function stepUpBlock(enrolled, secret, code, detail, accountKey) {
  if (!enrolled) return null;               // 2FA not enrolled — nothing to gate
  if (accountKey && !lockout.checkAccountLockout(accountKey)) {
    return {
      status: 429,
      body: {
        error: 'too_many_attempts',
        detail: 'Too many incorrect codes. Try again in a few minutes.',
      },
    };
  }
  const c = String(code || '').trim();
  if (!c) {
    // A MISSING code is the discovery handshake, not a guess. The browser
    // sends the action first, gets this 401, and only then prompts. Counting
    // it would lock people out through ordinary use — and the login path,
    // whose comment says "failed/missing codes count", does not count missing
    // ones either. Only a WRONG code is an attempt.
    return {
      status: 401,
      body: {
        error: 'two_factor_required',
        detail: detail || 'Enter your 6-digit authenticator code to continue.',
      },
    };
  }
  if (!totp.verifyTotp(secret, c)) {
    lockout.recordAccountFailure(accountKey);
    return {
      status: 401,
      body: {
        error: 'two_factor_required',
        detail: detail || 'Enter your 6-digit authenticator code to continue.',
      },
    };
  }
  lockout.clearAccountFailures(accountKey);
  return null;                               // valid fresh code — allowed
}

module.exports = { stepUpBlock };
