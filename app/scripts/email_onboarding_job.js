#!/usr/bin/env node
'use strict';
/**
 * Send the one-time first-steps email (lib/onboarding_mail.js) to the
 * members who signed up about three days ago and have not placed an Arena
 * paper trade.
 *
 *   EMAIL_ONBOARDING_JOB=1 node app/scripts/email_onboarding_job.js
 *   EMAIL_ONBOARDING_JOB=1 node app/scripts/email_onboarding_job.js --dry-run
 *
 * OFF BY DEFAULT. Unless EMAIL_ONBOARDING_JOB is 1 or true, the run reads
 * nothing, sends nothing and exits 0. This repository schedules nothing: no
 * timer in the web process, no crontab. An operator who has approved the
 * copy turns it on by setting the flag and running this once a day (more
 * often is safe).
 *
 * THE WINDOW. Only accounts created at least 3 and less than 4 days before
 * the run, not mailed this before and not opted out. Turning the job on
 * therefore reaches that one day's sign-ups and nobody older; it cannot mail
 * the back-catalogue. A day with no run skips that day's sign-ups. At most
 * 500 members per run; a second run the same day continues where it stopped.
 *
 * Run it where the website's settings are: it reads the same .env and the
 * same secrets vault server.js does (DATABASE_URL, SMTP_*, PUBLIC_ORIGIN or
 * APP_BASE_URL, BOT_SYNC_SECRET for the unsubscribe link), before loading
 * anything that reads them.
 *
 * --dry-run counts who would be mailed and claims and sends nothing.
 *
 * Prints counts, never an address. Exit codes, as scripts/verify_deploy.sh:
 *   0  done (or switched off)
 *   1  ran, but some members could not be read, or a send failed and the
 *      run stopped there; the rest stay unclaimed for the next run
 *   2  could not run: no MySQL database, no relay or From, no https public
 *      origin, no key for the unsubscribe link, or the candidates query
 *      failed. Nothing was claimed or sent.
 */

/**
 * @param {string[]} argv
 * @param {{db?: object, env?: object, now?: Date, out?: function, runOnce?: function}} [deps]
 *   Injected so the exit codes are driven by tests rather than grepped.
 */
async function main(argv = process.argv.slice(2), deps = {}) {
  const env = deps.env || process.env;
  const out = deps.out || ((line) => console.log(line));
  const onboarding = require('../lib/onboarding_mail');
  if (!onboarding.flagOn(env)) {
    out('email onboarding: EMAIL_ONBOARDING_JOB is off; nothing read, nothing sent.');
    return 0;
  }
  const db = deps.db || require('../db');
  if (db.backend() !== 'mysql') {
    out('email onboarding: could not run: no MySQL database (DATABASE_URL), so there is no member to read.');
    return 2;
  }
  const dryRun = argv.includes('--dry-run');
  const r = await (deps.runOnce || onboarding.runOnce)({
    pool: db.pool, now: deps.now || new Date(), env, dryRun,
  });
  if (!r.ran) {
    out(`email onboarding: could not run (${r.reason}): ${(r.problems || []).join(', ') || 'switched off'}`);
    return r.reason === 'flag_off' ? 0 : 2;
  }
  out(`email onboarding${dryRun ? ' (dry run)' : ''}: window ${new Date(r.window.lo * 1000).toISOString()}`
    + ` to ${new Date(r.window.hi * 1000).toISOString()}; ${JSON.stringify(r.counts)}`
    + (r.stopped ? `; stopped: ${r.stopped}` : ''));
  // A member who opted out between the query and the send is not trouble.
  const trouble = Object.keys(r.counts.not_sent).filter((reason) => reason !== 'opted_out');
  return r.counts.arena_unreadable > 0 || r.stopped || trouble.length ? 1 : 0;
}

if (require.main === module) {
  // The same start-up order as server.js: the .env first, then the secrets
  // vault for anything still missing, and only then the modules that read
  // the environment when they load (db.js, the mailer).
  try { require('../lib/env_file').loadEnvFile(); } catch (e) { /* absent is ordinary */ }
  try { require('../lib/secrets_vault').restoreFromVault(); } catch (e) { /* fail-open, as at boot */ }
  main().then((code) => process.exit(code)).catch((err) => {
    console.error(`email onboarding: could not run: ${err && err.name}`);
    process.exit(2);
  });
}

module.exports = { main };
