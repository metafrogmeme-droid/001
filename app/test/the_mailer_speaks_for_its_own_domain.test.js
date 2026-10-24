'use strict';
/**
 * The mailer introduces itself by the sender's own domain, carries a
 * Reply-To when one is configured, and the boot check asks each link builder
 * for the origin it actually reads.
 *
 *  - EHLO. The mailer said `EHLO runeclaw.local` to every relay. `.local` is a
 *    name that cannot exist, and a relay may refuse a client that introduces
 *    itself with one. The sender's domain is the one name this deployment is
 *    entitled to use.
 *  - Reply-To. A deployment that sends from an address nobody reads can now
 *    name one that somebody does, so "reply to this email" in a notice
 *    reaches a person.
 *  - The boot check. Email links are built from lib/public_origin
 *    (PUBLIC_ORIGIN first); the OAuth redirect URI from APP_BASE_URL alone,
 *    because that URL is the one registered with each provider. The check
 *    asked APP_BASE_URL for both, so a deployment whose email links were
 *    right (PUBLIC_ORIGIN set) was refused at boot once SMTP was on.
 *
 * The wire is driven against a fake SMTP server on a real socket, through
 * the real `sendMail`.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');

const mailer = require('../lib/mailer');
const { auditConfig } = require('../lib/config_audit');

// A minimal SMTP server: no STARTTLS, no AUTH; records every line it is sent.
function fakeSmtp() {
  const lines = [];
  const server = net.createServer((sock) => {
    let inData = false;
    let buf = '';
    sock.write('220 fake ESMTP\r\n');
    sock.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      let i;
      while ((i = buf.indexOf('\r\n')) !== -1) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 2);
        lines.push(line);
        if (inData) {
          if (line === '.') { inData = false; sock.write('250 queued\r\n'); }
          continue;
        }
        const verb = line.split(' ')[0].toUpperCase();
        if (verb === 'EHLO') sock.write('250-fake\r\n250 OK\r\n');
        else if (verb === 'DATA') { inData = true; sock.write('354 go\r\n'); }
        else if (verb === 'QUIT') { sock.write('221 bye\r\n'); sock.end(); }
        else sock.write('250 OK\r\n');
      }
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, lines })));
}

async function sendThrough(env) {
  const { server, lines } = await fakeSmtp();
  const saved = {};
  const set = { SMTP_HOST: '127.0.0.1', SMTP_PORT: String(server.address().port),
    SMTP_SECURE: 'false', SMTP_USER: '', SMTP_PASS: '', MAIL_REPLY_TO: '', ...env };
  for (const [k, v] of Object.entries(set)) { saved[k] = process.env[k]; process.env[k] = v; }
  try {
    const r = await mailer.sendMail({ to: 'member@example.com', subject: 'Hi', text: 'body' });
    assert.deepEqual(r, { skipped: false });
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
    server.close();
  }
  return lines;
}

test('the mailer says EHLO with the sender\'s domain, not a .local name', async () => {
  const lines = await sendThrough({ MAIL_FROM: 'RUNECLAW <info@humanoid-traders.com>' });
  assert.equal(lines[0], 'EHLO humanoid-traders.com', lines.join(' | '));
  // A sender that names no usable domain keeps the old fixed name rather
  // than putting something malformed on the wire.
  assert.equal(mailer._ehloName('RUNECLAW <nobody>'), 'runeclaw.local');
  assert.equal(mailer._ehloName('a@bad domain.com'), 'runeclaw.local');
});

test('a blank MAIL_FROM does not hide SMTP_FROM, and no sender uses the noreply default', async () => {
  const viaSmtpFrom = await sendThrough({
    MAIL_FROM: ' ',
    SMTP_FROM: 'Desk <desk@humanoid-traders.com>',
  });
  assert.ok(viaSmtpFrom.some((l) => l === 'From: Desk <desk@humanoid-traders.com>'),
    viaSmtpFrom.join('\n'));
  const viaDefault = await sendThrough({ MAIL_FROM: '', SMTP_FROM: '' });
  assert.ok(viaDefault.some((l) => l === 'From: Humanoid Traders <noreply@humanoid-traders.com>'),
    viaDefault.join('\n'));
  assert.equal(viaDefault[0], 'EHLO humanoid-traders.com');
});

test('Reply-To is sent only when configured', async () => {
  const withIt = await sendThrough({ MAIL_FROM: 'RUNECLAW <no-reply@humanoid-traders.com>',
    MAIL_REPLY_TO: 'info@humanoid-traders.com' });
  assert.ok(withIt.includes('Reply-To: info@humanoid-traders.com'), withIt.join(' | '));
  const without = await sendThrough({ MAIL_FROM: 'RUNECLAW <info@humanoid-traders.com>' });
  assert.ok(!without.some((l) => /^Reply-To:/i.test(l)), without.join(' | '));
  // A value with a line break would be a second header, so it is dropped.
  const msg = mailer.buildMessage({ from: 'a@x.io', to: 'b@x.io', subject: 's', text: 't',
    replyTo: 'x@x.io\r\nBcc: everyone@x.io' });
  assert.doesNotMatch(msg, /Bcc:/);
  assert.doesNotMatch(msg, /Reply-To:/);
});

const silentLog = { warn() {}, error() {} };
const audit = (env) => auditConfig({ env, log: silentLog, onFatal: () => {} });
const fatalKeys = (f) => f.filter((x) => x.level === 'fatal').map((x) => x.key);
const MAIL = { SMTP_HOST: 'smtp.gmail.com', MAIL_FROM: 'RUNECLAW <info@humanoid-traders.com>' };

test('email with PUBLIC_ORIGIN boots; email with no origin at all does not', () => {
  assert.ok(!fatalKeys(audit({ ...MAIL, PUBLIC_ORIGIN: 'https://runeclaw.app' })).includes('APP_BASE_URL'),
    'refused a deployment whose email links are correct');
  assert.ok(fatalKeys(audit({ ...MAIL })).includes('APP_BASE_URL'));
  // The mailer and the check read the same answer.
  const prev = process.env.PUBLIC_ORIGIN;
  process.env.PUBLIC_ORIGIN = 'https://runeclaw.app';
  try { assert.equal(mailer.baseUrl(), 'https://runeclaw.app'); } finally {
    if (prev === undefined) delete process.env.PUBLIC_ORIGIN; else process.env.PUBLIC_ORIGIN = prev;
  }
});

test('OAuth still needs APP_BASE_URL itself: PUBLIC_ORIGIN does not build its redirect', () => {
  const OAUTH = { DISCORD_CLIENT_ID: 'd', DISCORD_CLIENT_SECRET: 's' };
  assert.ok(fatalKeys(audit({ ...OAUTH, PUBLIC_ORIGIN: 'https://runeclaw.app' })).includes('APP_BASE_URL'));
  assert.ok(!fatalKeys(audit({ ...OAUTH, APP_BASE_URL: 'https://runeclaw.app' })).includes('APP_BASE_URL'));
});
