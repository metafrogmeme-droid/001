#!/usr/bin/env node
'use strict';

/**
 * Seal the 2FA seeds that were enrolled before WEB_CREDS_KEY existed.
 *
 *     node scripts/reseal_totp_secrets.js            # count, change nothing
 *     node scripts/reseal_totp_secrets.js --apply    # seal them
 *
 * WHY A BACKFILL AND NOT A ROUTE. Setting WEB_CREDS_KEY makes the NEXT write
 * encrypted and re-seals nothing. `/2fa/enable` migrates the row it enables,
 * but it refuses to run for an account that is already enabled — so an
 * enrolled user's legacy plaintext seed has no path to encryption at all short
 * of disabling 2FA and enrolling again. Meanwhile `secretsAreSealed()` starts
 * answering true and the boot warning that named the problem goes quiet, so
 * the deploy that fixes it is also the deploy that hides what it did not fix.
 *
 * DRY RUN IS THE DEFAULT. This writes to the column holding a permanent second
 * factor; a mistake here locks people out of their own accounts and no user can
 * rotate their way out of it. So the run that only counts is the one you get by
 * typing nothing, and `--apply` is a sentence you have to mean.
 *
 * THREE OUTCOMES, not two, matching `scripts/verify_deploy.sh`:
 *
 *   0  nothing left to seal
 *   1  rows still need attention (found in a dry run, or left behind by --apply)
 *   2  could not run at all — no usable key, a store that does not persist, or
 *      a query that failed. Reporting "0 plaintext seeds" because the database
 *      was unreachable is the failure this whole subsystem is about.
 *
 * It never prints a secret, sealed or plain. Ids and counts only — and ids only
 * for rows it could NOT fix, which are the ones an operator has to chase.
 */

const totp = require('../lib/totp');
const seeds = require('../lib/totp_seed_audit');

const APPLY_ARGV = process.argv.includes('--apply');

/**
 * @param {{db?: object, out?: function, apply?: boolean}} [deps]
 *   Injected so the three exit codes are DRIVEN rather than grepped. Every
 *   interesting branch here needs a database in a particular state — a store
 *   that does not persist, a query that throws, a row that changed under the
 *   run — and a source scan over this file cannot tell a reached guard from a
 *   present one. That distinction is the standing lesson from a card that was
 *   scanned, shipped, and rendered zero times.
 */
async function main(deps = {}) {
  const db = deps.db || require('../db');
  const say = deps.out || ((s) => process.stdout.write(`${s}\n`));
  const APPLY = deps.apply === undefined ? APPLY_ARGV : deps.apply;

  // A store that vanishes at exit would take every UPDATE with it and still
  // print a clean sweep. Same class as a deploy that reports success onto a
  // stale tree: every check passes, the only thing wrong is WHERE it ran.
  const where = db.backend();
  if (where !== 'mysql') {
    say(`REFUSING: the active store is '${where}', not mysql — an UPDATE here does `
      + 'not survive the process. Point DATABASE_URL at the real database.');
    return 2;
  }
  if (!totp.secretsAreSealed()) {
    say('REFUSING: WEB_CREDS_KEY is unset or not a 32-byte base64 key, so there is '
      + 'nothing to seal with. sealSecret() would pass every seed through unchanged '
      + 'and this run would report a clean sweep having done nothing.');
    return 2;
  }

  let rows = [];
  try {
    [rows = []] = await db.pool.execute(seeds.ENROLLED_SQL);
  } catch (err) {
    const code = (err && typeof err.code === 'string' && err.code) || 'no-code';
    say(`COULD NOT CHECK: the query failed (${code}). The number of plaintext seeds `
      + 'is unknown, not zero.');
    return 2;
  }

  const counts = { sealed: 0, seal: 0, unreadable: 0, unsealable: 0, raced: 0, failed: 0 };
  const stuck = [];

  for (const row of rows) {
    const { action, next } = seeds.resealRow(row.stored);
    if (action === 'skip') { counts.sealed += 1; continue; }
    if (action !== 'seal') {
      // 'unreadable' or 'unsealable'. Both are LEFT ALONE: one cannot be read
      // to reseal, and the other did not survive its own round trip. Writing
      // either would replace a row somebody might still recover with one
      // nobody can.
      counts[action] = (counts[action] || 0) + 1;
      stuck.push(`${row.id} (${action})`);
      continue;
    }
    if (!APPLY) { counts.seal += 1; continue; }
    try {
      // COMPARE AND SWAP. A user re-enrolling while this runs would have their
      // fresh secret overwritten by the sealed form of the one it replaced,
      // silently breaking an authenticator they had just set up. Matching the
      // old value in the WHERE clause makes that a no-op instead.
      const [res] = await db.pool.execute(
        'UPDATE users SET totp_secret = ? WHERE id = ? AND totp_secret = ?',
        [next, row.id, row.stored]);
      if (res && res.affectedRows === 1) counts.seal += 1;
      else { counts.raced += 1; stuck.push(`${row.id} (changed under the run)`); }
    } catch (err) {
      const code = (err && typeof err.code === 'string' && err.code) || 'no-code';
      counts.failed += 1;
      stuck.push(`${row.id} (write failed: ${code})`);
    }
  }

  const verb = APPLY ? 'sealed' : 'would seal';
  say(`${rows.length} account(s) carry a 2FA secret.`);
  say(`  already sealed : ${counts.sealed}`);
  say(`  ${verb.padEnd(14)} : ${counts.seal}`);
  if (counts.unreadable) {
    say(`  unreadable     : ${counts.unreadable}  (sealed to a key this deployment `
      + 'does not hold — these accounts cannot pass a second-factor check)');
  }
  if (counts.unsealable) {
    say(`  unsealable     : ${counts.unsealable}  (the seal did not round-trip; left `
      + 'in the clear rather than written unreadable)');
  }
  if (counts.raced) say(`  raced          : ${counts.raced}  (re-run to pick these up)`);
  if (counts.failed) say(`  write failed   : ${counts.failed}`);
  if (stuck.length) say(`  needs attention: ${stuck.join(', ')}`);

  const left = counts.unreadable + counts.unsealable + counts.raced + counts.failed
    + (APPLY ? 0 : counts.seal);
  if (!APPLY && counts.seal) {
    say('\nDry run — nothing was written. Re-run with --apply to seal them.');
  }
  return left ? 1 : 0;
}

if (require.main === module) {
  main()
    .then((code) => process.exit(code))
    .catch((err) => {
      // The message can carry a connection string; the stack is the operator's
      // to read on stderr, and the exit code is what a wrapper acts on.
      process.stderr.write(`reseal_totp_secrets failed: ${err && err.stack}\n`);
      process.exit(2);
    });
}

module.exports = { main };
