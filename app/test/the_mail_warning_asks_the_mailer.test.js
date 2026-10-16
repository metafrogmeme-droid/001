/**
 * The "email is NOT sent" warning asks the mailer, not a copy of it.
 *
 * 2026-10-16, reported from the Admin page: "SMTP_HOST/MAIL_FROM unset —
 * verification and password-reset emails are NOT sent". The boot config
 * audit (lib/config_audit.js) decided that with its own check, MAIL_FROM
 * alone, while the mailer (lib/mailer.js) sends with MAIL_FROM or SMTP_FROM.
 * Two readings of one fact: a site sending with SMTP_FROM was told its mail
 * was not sent. The audit now asks the mailer, and the warning names what is
 * actually missing.
 *
 * Run: npm test  (node --test test/)
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { auditConfig } = require('../lib/config_audit');
const mailer = require('../lib/mailer');

const silentLog = { warn() {}, error() {} };
const smtpFinding = (env) => auditConfig({ env, log: silentLog, onFatal: () => {} })
  .find((x) => x.key === 'SMTP');

test('a site sending with SMTP_FROM is not told its mail is NOT sent', () => {
  const env = { SMTP_HOST: 'smtp.gmail.com', SMTP_FROM: 'RUNECLAW <bot@example.com>' };
  assert.equal(mailer.isConfigured(env), true, 'the mailer would not send with SMTP_FROM');
  assert.equal(smtpFinding(env), undefined, 'the audit warns over a sender the mailer accepts');
});

test('the warning names what is missing, and only that', () => {
  const noFrom = smtpFinding({ SMTP_HOST: 'smtp.gmail.com' });
  assert.ok(noFrom, 'no warning with no sender at all');
  assert.match(noFrom.msg, /^MAIL_FROM \(or SMTP_FROM\) unset/);
  assert.doesNotMatch(noFrom.msg, /SMTP_HOST/, 'it blames a host that is set');

  const noHost = smtpFinding({ MAIL_FROM: 'bot@example.com' });
  assert.match(noHost.msg, /^SMTP_HOST unset/);

  const neither = smtpFinding({});
  assert.match(neither.msg, /^SMTP_HOST and MAIL_FROM \(or SMTP_FROM\) unset/);
});

test('the audit and the mailer agree on every shape of the settings', () => {
  const envs = [
    {},
    { SMTP_HOST: 'smtp.x.io' },
    { MAIL_FROM: 'a@x.io' },
    { SMTP_HOST: 'smtp.x.io', MAIL_FROM: 'a@x.io' },
    { SMTP_HOST: 'smtp.x.io', SMTP_FROM: 'a@x.io' },
    { SMTP_HOST: '   ', MAIL_FROM: 'a@x.io' },          // a blank host is no host
    { SMTP_HOST: 'smtp.x.io', MAIL_FROM: '  ' },        // nor a blank sender
    { SMTP_HOST: 'smtp.x.io', MAIL_FROM: ' ', SMTP_FROM: 'a@x.io' },
  ];
  for (const env of envs) {
    assert.equal(smtpFinding(env) === undefined, mailer.isConfigured(env),
      `audit and mailer disagree for ${JSON.stringify(env)}`);
  }
  // Both arms are present in the table, so agreement is not two constants.
  assert.ok(envs.some((e) => mailer.isConfigured(e)) && envs.some((e) => !mailer.isConfigured(e)));
  // Agreement cannot see a shared mistake. The audit trimmed and the mailer
  // did not, so a blank host made the mailer dial "   "; the one reading now
  // trims, and that is asserted on its own.
  assert.equal(mailer.isConfigured({ SMTP_HOST: '   ', MAIL_FROM: 'a@x.io' }), false);
  assert.equal(mailer.isConfigured({ SMTP_HOST: 'smtp.x.io', MAIL_FROM: '  ' }), false);
  // A blank MAIL_FROM does not hide a set SMTP_FROM.
  assert.equal(mailer.isConfigured({ SMTP_HOST: 'smtp.x.io', MAIL_FROM: ' ', SMTP_FROM: 'a@x.io' }), true);
});
