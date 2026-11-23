'use strict';
/**
 * The one-time first-steps email, about three days after sign-up, for a
 * member who has not placed a paper trade in the Arena yet.
 *
 * OFF UNLESS EMAIL_ONBOARDING_JOB IS 1 OR true. Unset, 0, false or anything
 * else is off, and an off run reads nothing and sends nothing. Nothing in
 * the web process calls this: scripts/email_onboarding_job.js does, when an
 * operator runs it.
 *
 * WHO. An account at least 3 and under 4 days old (created_at after
 * now − 4 days and no later than now − 3 days),
 * not mailed this before, not opted out (lib/email_prefs.js), at an address
 * that can receive mail (lib/account_mail.js deliverable), with no Arena
 * paper trade, open or closed. The window is one day wide on purpose: turning
 * the job on reaches the accounts made three days earlier and nobody older,
 * so it cannot mail the back-catalogue. A day on which the job does not run
 * at all skips that day's sign-ups; running it more often than daily is
 * safe, because each account is in the window for 24 hours and is mailed at
 * most once. The bounds are compared as epoch seconds (UNIX_TIMESTAMP), so
 * the database's time zone and the job host's cannot shift the window.
 *
 * AT MOST ONCE. Each member is claimed (onboarding_email_sent_at, set only
 * where it is still NULL) before the send, and only a claim that changed the
 * row goes on to send. A crash after a claim never sends twice; a retry of
 * the run is a no-op for everyone already claimed. The price is that a send
 * that fails after its claim is not retried, which is the right side to err
 * on for an email: it is an event, not state.
 *
 * NOTHING IS CLAIMED UNTIL THE RUN CAN SEND. Before the first claim it checks
 * that the mailer has a relay and a From, that the public origin is an
 * absolute https URL, and that the unsubscribe link can be signed, by
 * rendering one mail. And the first delivery failure stops the run, so the
 * members after it stay unclaimed for the next run.
 *
 * UNREADABLE IS NEVER "HAS NOT TRADED". An Arena read that fails skips the
 * member and is counted; it is not taken as permission to mail them.
 *
 * The send itself is accountMail.notify('onboarding', …), so the opt-out, the
 * address check and the unsubscribe link are decided in the one place every
 * non-security mail goes through.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const WINDOW_START_DAYS = 4;   // at least 3 and under 4 days old
const WINDOW_END_DAYS = 3;
const MAX_PER_RUN = 500;

const CANDIDATES_SQL = 'SELECT id, email, email_verified FROM users'
  + ' WHERE UNIX_TIMESTAMP(created_at) > ? AND UNIX_TIMESTAMP(created_at) <= ?'
  + ' AND onboarding_email_sent_at IS NULL AND email_opted_out_at IS NULL'
  + ` ORDER BY id LIMIT ${MAX_PER_RUN}`;
const CLAIM_SQL = 'UPDATE users SET onboarding_email_sent_at = ? WHERE id = ? AND onboarding_email_sent_at IS NULL';

/** Is the job switched on? Only 1 or true, in any case. */
function flagOn(env = process.env) {
  return /^(1|true)$/i.test(String(env.EMAIL_ONBOARDING_JOB || '').trim());
}

/**
 * The created_at window for a run at `now`, as epoch seconds: after `lo`
 * (exactly 4 days old is out) and no later than `hi` (exactly 3 days old is
 * in). Consecutive daily runs tile without a gap or an overlap.
 */
function windowFor(now) {
  const t = now.getTime();
  return {
    lo: Math.floor((t - WINDOW_START_DAYS * DAY_MS) / 1000),
    hi: Math.floor((t - WINDOW_END_DAYS * DAY_MS) / 1000),
  };
}

/** Has this member placed an Arena paper trade, open or closed? Throws when unreadable. */
async function hasTraded(pool, userId) {
  const [closed] = await pool.execute('SELECT id FROM arena_trades WHERE user_id = ? LIMIT 1', [userId]);
  if (!Array.isArray(closed)) throw new Error('arena_trades read returned no rows array');
  if (closed.length) return true;
  const [open] = await pool.execute('SELECT id FROM arena_positions WHERE user_id = ? LIMIT 1', [userId]);
  if (!Array.isArray(open)) throw new Error('arena_positions read returned no rows array');
  return open.length > 0;
}

/** Why this run cannot send, before anyone is claimed. Empty when it can. */
function preflight({ mailer, accountMail, now }) {
  const problems = [];
  if (!mailer.isConfigured()) problems.push('mailer_not_configured');
  else if (!mailer.sender()) problems.push('mailer_has_no_from');
  if (!/^https:\/\/[^/\s]+$/.test(mailer.baseUrl())) problems.push('public_origin_not_https');
  try {
    accountMail.compose('onboarding', now, { userId: 1, unverified: true });
  } catch (err) {
    problems.push(err && err.code === 'EMAIL_PREFS_NO_KEY' ? 'no_unsubscribe_key' : 'render_failed');
  }
  return problems;
}

/**
 * One run. Resolves to { ran: false, reason, problems? } when it did nothing,
 * or { ran: true, window, counts, stopped }. Never sends when the flag is off.
 */
async function runOnce({
  pool,
  now = new Date(),
  env = process.env,
  dryRun = false,
  mailer = require('./mailer'),
  accountMail = require('./account_mail'),
} = {}) {
  if (!flagOn(env)) return { ran: false, reason: 'flag_off' };
  const problems = preflight({ mailer, accountMail, now });
  if (problems.length) return { ran: false, reason: 'cannot_run', problems };
  const window = windowFor(now);
  let rows;
  try {
    [rows] = await pool.execute(CANDIDATES_SQL, [window.lo, window.hi]);
  } catch (err) {
    return { ran: false, reason: 'cannot_run', problems: ['candidates_unreadable'] };
  }
  const counts = {
    candidates: rows.length, sent: 0, would_send: 0, undeliverable: 0, traded: 0,
    arena_unreadable: 0, already_claimed: 0, not_sent: {},
  };
  let stopped = null;
  for (const row of rows) {
    const userId = Number(row.id);
    if (!accountMail.deliverable(row.email)) { counts.undeliverable += 1; continue; }
    let traded;
    try {
      traded = await hasTraded(pool, userId);
    } catch (err) {
      counts.arena_unreadable += 1;
      continue;
    }
    if (traded) { counts.traded += 1; continue; }
    if (dryRun) { counts.would_send += 1; continue; }
    let claim;
    try {
      [claim] = await pool.execute(CLAIM_SQL, [now, userId]);
    } catch (err) {
      stopped = 'claim_failed';
      break;
    }
    if (!claim || claim.affectedRows !== 1) { counts.already_claimed += 1; continue; }
    const verified = row.email_verified === true || row.email_verified === 1;
    const r = await accountMail.notify('onboarding', row.email, now, { userId, unverified: !verified });
    if (r.sent) { counts.sent += 1; continue; }
    if (!Object.prototype.hasOwnProperty.call(counts.not_sent, r.reason)) counts.not_sent[r.reason] = 0;
    counts.not_sent[r.reason] += 1;
    // A relay that failed or went away fails the next send too; the members
    // after this one stay unclaimed for the next run.
    if (r.reason === 'failed' || r.reason === 'not_configured') { stopped = `send_${r.reason}`; break; }
  }
  return { ran: true, window, counts, stopped };
}

module.exports = {
  flagOn, windowFor, hasTraded, preflight, runOnce,
  CANDIDATES_SQL, CLAIM_SQL, MAX_PER_RUN, WINDOW_START_DAYS, WINDOW_END_DAYS,
};
