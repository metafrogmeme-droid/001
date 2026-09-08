'use strict';
/**
 * How many enrolled 2FA seeds are actually encrypted at rest — counted, not
 * inferred from the presence of a key.
 *
 * `config_audit` warns when WEB_CREDS_KEY is unset: "new 2FA secrets are
 * stored unencrypted". True, precise, and it names the trap it walks into.
 * The operator sets the key, the warning stops, `secretsAreSealed()` starts
 * answering true, and `/2fa/setup` starts telling every new enrolment its seed
 * is encrypted — while every seed enrolled BEFORE that deploy is still sitting
 * in the clear in `users.totp_secret`, because setting a key seals nothing that
 * was already written.
 *
 * So the deploy that fixes the problem is also the deploy that removes the only
 * signal the problem exists. The remaining plaintext rows have no surface at
 * all: `/2fa/enable` re-seals on the enable transition and refuses to run for
 * an account that is already enabled, so short of disabling and re-enrolling,
 * an enrolled user's legacy seed stays plaintext for the life of the account.
 *
 * This counts them. `scripts/reseal_totp_secrets.js` fixes them.
 *
 * FOUR OUTCOMES, because three of them are indistinguishable from a distance:
 *
 *   sealed      an envelope this deployment can open — the good case
 *   plaintext   a legacy base32 seed; a leak of `users` hands it over
 *   unreadable  an envelope that will NOT open under the current key. Every
 *               account here fails its second factor on the next login, and
 *               from the shape alone it looks exactly like the good case
 *   absent      no secret stored; this account never enrolled. NOT a defect,
 *               and counting it as plaintext would invent seeds
 *
 * And a fifth thing that is not an outcome: the query failing. An unreadable
 * count renders as `could_not_check`, never as zero — a boot audit that reports
 * "no plaintext seeds" because it could not reach the database is the exact
 * failure this repo's guard tests exist to prevent.
 */

const totp = require('./totp');

/**
 * Classify one stored value. Reads the row, never the deployment config.
 *
 * `secretIsSealed` answers the SHAPE and `openSecret` answers whether anyone
 * can read it, and the pair is what separates a healthy sealed row from one
 * sealed to a key that has since been rotated away. Asking only the shape
 * reports the second as fixed.
 */
function classify(stored) {
  const shape = totp.secretIsSealed(stored);
  if (shape === null) return 'absent';
  if (!shape) return 'plaintext';
  return totp.openSecret(stored) ? 'sealed' : 'unreadable';
}

/** Tally `classify` over stored values. Always returns all four keys. */
function tally(values) {
  const out = { sealed: 0, plaintext: 0, unreadable: 0, absent: 0 };
  for (const v of Array.isArray(values) ? values : []) out[classify(v)] += 1;
  return out;
}

/**
 * What to do with one row, and the value to write. Pure apart from the key.
 *
 * The seam the backfill script would otherwise be. A migration on the column
 * that holds a permanent second factor is the last place to put untested
 * decision-making inside a `for` loop in a script CI never even compiles.
 *
 * ROUND-TRIP VERIFIED BEFORE IT IS OFFERED. `sealSecret` passes plaintext
 * through when there is no key, so a run with WEB_CREDS_KEY unset would
 * "seal" every row to itself and report a clean sweep having changed nothing.
 * Re-opening what was just sealed catches that, and catches a key that can
 * encrypt but not decrypt — which would otherwise lock out every account the
 * run touched, one row at a time, with the plaintext already overwritten.
 *
 * ONE REFUSAL, NOT TWO. The first draft had a separate `if (!plain) return
 * unreadable` above this, and a mutation replacing it with "seal it anyway"
 * SURVIVED the suite — `classify` had already ruled that case out for every
 * value the SELECT can return, so the guard was present and unreachable, which
 * is indistinguishable from one that does not work. Folded in here it is still
 * a refusal and no longer a claim nothing can exercise.
 *
 * The last limb is the exception and is stated rather than pretended: a key
 * that encrypts but will not decrypt cannot be constructed from outside, since
 * both halves read the same env var in the same call. It stays because a
 * round-trip check before overwriting a permanent second factor is the right
 * code whether or not a test can force it to fire.
 */
function resealRow(stored) {
  const state = classify(stored);
  if (state !== 'plaintext') return { action: state === 'sealed' ? 'skip' : state, next: null };
  const plain = totp.openSecret(stored);
  const next = plain ? totp.sealSecret(plain) : null;
  if (next === plain || totp.openSecret(next) !== plain) {
    return { action: 'unsealable', next: null };
  }
  return { action: 'seal', next };
}

/**
 * Rows carrying a secret at all; `absent` is decided in SQL, not by regex.
 *
 * ALIASED TO `stored`, and the name is the point rather than a shortening:
 * everything downstream handles an OPAQUE stored value whose readability is the
 * question, not a secret it may assume it holds. `totp_secret_at_rest.test.js`
 * enforces that every reader of the column hands it to something that opens it,
 * and reading `r.stored` keeps this module honest about which of those it is.
 */
const ENROLLED_SQL = "SELECT id, totp_secret AS stored FROM users "
  + "WHERE totp_secret IS NOT NULL AND totp_secret <> ''";

/**
 * Boot probe: count the seeds and say something true about them.
 *
 * Best-effort and non-blocking by contract — it runs after migration on a path
 * that also starts the watchers, and a diagnostic that can break boot is worse
 * than no diagnostic (same rule as `lib/boot_log.js`).
 *
 * @returns {Promise<Array<{level:'warn', key:string, msg:string}>>} findings,
 *   also logged. Empty means counted and clean — NOT "did not look".
 */
async function auditSealedSeeds(opts = {}) {
  const pool = opts.pool || require('../db').pool;
  const log = opts.log || console;
  const findings = [];
  const warn = (key, msg) => findings.push({ level: 'warn', key, msg });

  // `null` until a query answers, and the two blocks below read that rather
  // than a zero — the whole point of this module is that a count nobody took
  // is not a count of zero, and it would be a poor showing to encode that
  // distinction as an absent number.
  let counts = null;
  try {
    const [rows] = await pool.execute(ENROLLED_SQL);
    counts = tally((rows || []).map((r) => r.stored));
  } catch (err) {
    // A CODE, never the driver's message: it can carry a connection string.
    const code = (err && typeof err.code === 'string' && err.code) || 'no-code';
    warn('TOTP_AT_REST', `could not be checked (${code}) — the number of 2FA seeds `
      + 'stored in the clear is unknown, not zero.');
  }

  if (counts && counts.plaintext > 0) {
    const sealable = totp.secretsAreSealed();
    warn('TOTP_AT_REST', `${counts.plaintext} enrolled 2FA seed(s) are stored in the clear. `
      + (sealable
        ? 'WEB_CREDS_KEY seals new rows only — these predate it and no route will ever '
          + 'reseal them. Run `node scripts/reseal_totp_secrets.js --apply` from app/.'
        : 'WEB_CREDS_KEY is not usable, so nothing can seal them; set it, then run '
          + '`node scripts/reseal_totp_secrets.js --apply` from app/.'));
  }
  if (counts && counts.unreadable > 0) {
    warn('TOTP_AT_REST', `${counts.unreadable} sealed 2FA seed(s) will not open under the `
      + 'current WEB_CREDS_KEY — those accounts cannot pass a second-factor check. A '
      + 'rotated or truncated key looks identical to a healthy one from the row alone.');
  }

  for (const f of findings) log.warn(`WARNING [config] ${f.key}: ${f.msg}`);
  return findings;
}

module.exports = { classify, tally, resealRow, auditSealedSeeds, ENROLLED_SQL };
