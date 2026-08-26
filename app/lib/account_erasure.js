'use strict';
/**
 * Everything the web database holds about one person, in one list.
 *
 * There was no account-deletion path anywhere in the product. Not a broken
 * one — none: no route, no SQL, no `is_active = 0`. The privacy page had to
 * say so in as many words, because writing "you may request erasure" over a
 * system that cannot perform it is the same defect as any other confident
 * claim about a state that does not exist.
 *
 * WHY A LIST AND NOT A CASCADE. MySQL `ON DELETE CASCADE` would be shorter and
 * would hide the one thing worth reading: which tables hold a person, and
 * therefore what "delete my account" actually promises. A list is greppable, is
 * checked against the live schema by `account_erasure.test.js`, and fails when
 * somebody adds a 25th user-scoped table without thinking about erasure —
 * which is precisely the moment this needs to be thought about.
 *
 * WHY THE USER ROW IS TOMBSTONED RATHER THAN DELETED. `users.referred_by`
 * points at other users. Deleting the row would dangle every referral edge
 * that names this person, and `duel_squads.js` builds public squads out of
 * exactly those edges — a deleted captain would take their recruits' standing
 * with them. So the row survives with every identifying column cleared, which
 * leaves the graph intact and leaves nothing in it that identifies anybody.
 *
 * What survives, deliberately, is the fact that an id once existed. That is
 * not nothing, and it is stated on the privacy page rather than glossed.
 *
 * THE LIST WAS WRITTEN FROM MEMORY AND THE TEST FOUND TWO IT HAD MISSED.
 * `account_erasure.test.js` parses the DDL rather than trusting this file, and
 * on its first run it named `wallet_link_nonces` (keyed by the person's WALLET
 * ADDRESS, not by `user_id`) and `pending_stance` (keyed by `requested_by`,
 * carrying a `telegram_id` alongside it). Neither has a `user_id` column, which
 * is exactly why a list assembled by grepping for `user_id` could not see them
 * — and why the test derives the question from the schema instead of from the
 * answer. Both are handled below, under their own keys.
 */

/**
 * Child tables keyed by `user_id`, deleted outright.
 *
 * Ordered with the money- and credential-adjacent ones first so that a failure
 * partway through has removed the most sensitive rows rather than the least.
 */
const USER_SCOPED_TABLES = [
  // credentials and anything that could move value
  'pending_credentials',
  'exchange_status',
  'pending_controls',
  'user_controls',
  'pending_flatten',
  'arena_api_keys',
  'arena_envelopes',
  // trading history and positions
  'trades',
  'equity_snapshots',
  'arena_accounts',
  'arena_positions',
  'arena_trades',
  // identity, preferences and social graph
  'wallet_link_codes',
  'push_subscriptions',
  'copy_subscriptions',
  'user_profiles',
  'user_alerts',
  'user_strategies',
  'user_watchlist',
  'arena_follows',
  // Agent claims. The slug is released and the row goes; the SEAL already
  // minted into a published daily root is a hash and cannot be unmade — but
  // nothing is lost by that, because seal_roots stores each day's leaf set
  // alongside the root, so verification never reads this table.
  'agents',
  // Pre-signature scan receipts. The rows go; the seals already folded into a
  // published root are hashes of a hash of an input we never held, so there is
  // nothing left behind that describes the person or what they scanned.
  'scan_seals',
  'duel_picks',
  'learn_diary',
  'learn_progress',
];

/**
 * Tables keyed by a WALLET ADDRESS rather than by `user_id`.
 *
 * `wallet_link_nonces` stores the challenge a wallet signs to prove ownership,
 * with the address itself as the primary key. It is short-lived and pruned, but
 * "will expire eventually" is not erasure, and the row is the person's public
 * chain address sitting in a table an account deletion never touched.
 */
const ADDRESS_SCOPED_TABLES = ['wallet_link_nonces'];

/**
 * Tables keyed by the id of whoever REQUESTED something.
 *
 * `pending_stance` is a singleton approval queue: one pending stance change,
 * carrying the requester's user id and their telegram id. If an account is
 * erased while its request is still queued, the row keeps naming them — and,
 * worse, remains approvable in the name of an account that no longer exists.
 */
const REQUESTER_SCOPED_TABLES = ['pending_stance'];

/**
 * Columns on `users` that identify a person, blanked in place.
 *
 * `email` cannot simply be NULLed — it is UNIQUE and NOT NULL on most
 * deployments, and two erased accounts would collide. It gets a per-id
 * tombstone that is unique, obviously synthetic, and carries no information
 * about who it replaced.
 */
const IDENTIFYING_COLUMNS = [
  'password_hash', 'telegram_id', 'link_token', 'link_token_expires',
  'totp_secret', 'totp_backup_codes', 'google_id', 'avatar_url', 'discord_id',
  'x_id', 'wallet_address', 'sol_address', 'verify_token',
  'verify_token_expires', 'reset_token', 'reset_token_expires',
  'referral_code', 'leaderboard_handle',
];

/** The address an erased row carries. Unique per id, and unmistakably a tombstone. */
function tombstoneEmail(userId) {
  return `deleted-${userId}@account.invalid`;
}

/**
 * The statements that erase one user, in order.
 *
 * Returned rather than executed so a test can read the plan without a
 * database, and so the caller owns the transaction. Every statement is
 * parameterised; the table names are from the frozen lists above and never
 * from input.
 *
 * `identity` IS REQUIRED, AND AN ABSENT ONE THROWS. Two tables are keyed by
 * something other than the user id — a wallet address — so the plan cannot be
 * built from the id alone. Defaulting it to `{}` would have produced a plan
 * that runs cleanly, reports success, and silently leaves those rows behind:
 * absent is not "this account has no addresses". A caller that genuinely has
 * none passes `[]`, which is a statement rather than a silence.
 */
function erasurePlan(userId, identity) {
  if (!identity || typeof identity !== 'object' || Array.isArray(identity)) {
    throw new TypeError(
      'erasurePlan(userId, identity) needs the user row: rows keyed by wallet '
      + 'address cannot be found from the id, and an absent identity is not an '
      + 'empty one');
  }
  const addresses = [...new Set(
    (identity.addresses || [])
      .filter((a) => typeof a === 'string' && a.trim() !== '')
      .map((a) => a.trim()))];

  const steps = USER_SCOPED_TABLES.map((t) => ({
    table: t,
    sql: `DELETE FROM ${t} WHERE user_id = ?`,
    params: [userId],
  }));

  for (const t of REQUESTER_SCOPED_TABLES) {
    steps.push({ table: t, sql: `DELETE FROM ${t} WHERE requested_by = ?`, params: [userId] });
  }
  for (const t of ADDRESS_SCOPED_TABLES) {
    for (const a of addresses) {
      steps.push({ table: t, sql: `DELETE FROM ${t} WHERE address = ?`, params: [a] });
    }
  }

  const sets = IDENTIFYING_COLUMNS.map((c) => `${c} = NULL`).join(', ');
  steps.push({
    table: 'users',
    // `token_epoch` is bumped in the same statement that clears the identity:
    // every issued session for this account stops verifying at the moment the
    // row stops naming anybody, rather than one statement later.
    sql: `UPDATE users SET email = ?, ${sets}, telegram_linked = 0, `
       + 'email_verified = 0, token_epoch = COALESCE(token_epoch, 0) + 1, '
       + 'plan = \'deleted\' WHERE id = ?',
    params: [tombstoneEmail(userId), userId],
  });
  return steps;
}

module.exports = {
  USER_SCOPED_TABLES, ADDRESS_SCOPED_TABLES, REQUESTER_SCOPED_TABLES,
  IDENTIFYING_COLUMNS, tombstoneEmail, erasurePlan,
};
