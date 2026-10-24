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
  // A host and no sender is configured: the mailer sends once SMTP_USER is
  // an address, and a host alone is still "mail is on". The warning used to
  // blame MAIL_FROM here, which would be a second reading of that fact.
  const hostOnly = smtpFinding({ SMTP_HOST: 'smtp.gmail.com' });
  assert.equal(hostOnly, undefined, 'a host with no explicit sender is still configured');

  const noHost = smtpFinding({ MAIL_FROM: 'bot@example.com' });
  assert.match(noHost.msg, /^SMTP_HOST unset/);
  assert.doesNotMatch(noHost.msg, /MAIL_FROM/, 'the sender is set; the host is what is missing');

  const neither = smtpFinding({});
  assert.match(neither.msg, /^SMTP_HOST unset/);
  assert.doesNotMatch(neither.msg, /MAIL_FROM/);
});

test('the audit and the mailer agree on every shape of the settings', () => {
  const envs = [
    {},
    { SMTP_HOST: 'smtp.x.io' },
    { MAIL_FROM: 'a@x.io' },
    { SMTP_HOST: 'smtp.x.io', MAIL_FROM: 'a@x.io' },
    { SMTP_HOST: 'smtp.x.io', SMTP_FROM: 'a@x.io' },
    { SMTP_HOST: '   ', MAIL_FROM: 'a@x.io' },          // a blank host is no host
    { SMTP_HOST: 'smtp.x.io', MAIL_FROM: '  ' },        // blank sender, no mailbox yet
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
  // A blank sender is not "unconfigured": the host is what turns mail on.
  // The From address, when neither override is set, is the SMTP mailbox.
  assert.equal(mailer.isConfigured({ SMTP_HOST: 'smtp.x.io', MAIL_FROM: '  ' }), true);
  assert.equal(mailer.isConfigured({ SMTP_HOST: 'smtp.x.io', MAIL_FROM: ' ', SMTP_FROM: 'a@x.io' }), true);
  assert.equal(
    mailer._cfg({ SMTP_HOST: 'smtp.gmail.com', SMTP_USER: 'owner@gmail.com', SMTP_PASS: 'secret' }).from,
    'Humanoid Traders <owner@gmail.com>',
  );
  assert.equal(mailer._cfg({ SMTP_HOST: 'smtp.gmail.com', SMTP_USER: 'owner@gmail.com' }).pass, '');
  const explicit = mailer._cfg({
    SMTP_HOST: 'smtp.gmail.com',
    SMTP_USER: 'owner@gmail.com',
    MAIL_FROM: 'Humanoid Traders <noreply@humanoid-traders.com>',
  });
  assert.equal(explicit.from, 'Humanoid Traders <noreply@humanoid-traders.com>');
});

test('an unconfigured send logs one line and not the message body', async () => {
  const lines = [];
  const orig = console.warn;
  console.warn = (line) => lines.push(String(line));
  const prev = process.env.SMTP_HOST;
  delete process.env.SMTP_HOST;
  try {
    const r = await mailer.sendMail({
      to: 'a@b.co',
      subject: 'Verify',
      text: 'secret-token-do-not-log abcdef',
    });
    assert.deepEqual(r, { skipped: true, reason: 'not_configured' });
  } finally {
    console.warn = orig;
    if (prev === undefined) delete process.env.SMTP_HOST;
    else process.env.SMTP_HOST = prev;
  }
  assert.equal(lines.length, 1);
  assert.match(lines[0], /not configured/);
  assert.equal(lines.join('\n').includes('secret-token-do-not-log'), false);
  assert.equal(lines.join('\n').includes('abcdef'), false);
});
