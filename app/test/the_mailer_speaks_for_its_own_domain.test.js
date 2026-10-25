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
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const tls = require('node:tls');
const { execFileSync } = require('node:child_process');

const mailer = require('../lib/mailer');
const { auditConfig } = require('../lib/config_audit');

// One throwaway cert for the fake relay. The client is pointed at 127.0.0.1
// with verification off; production keeps the default, which checks the cert.
let CERT;
function testCert() {
  if (CERT) return CERT;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'smtp-cert-'));
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-keyout', 'key.pem', '-out', 'cert.pem',
    '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1',
  ], { cwd: dir, stdio: 'ignore' });
  CERT = {
    key: fs.readFileSync(path.join(dir, 'key.pem')),
    cert: fs.readFileSync(path.join(dir, 'cert.pem')),
  };
  return CERT;
}

/**
 * A minimal SMTP relay. `mode` is:
 *   starttls — clear greeting, then STARTTLS (the Gmail 587 path)
 *   implicit — TLS from the first byte (port 465)
 *   plain    — no TLS offer, so a requireTLS client must stop
 * Records every SMTP line, including those after the handshake.
 */
function fakeSmtp({ mode = 'starttls', echoAuth = false } = {}) {
  const lines = [];
  const pem = testCert();

  function speak(sock, { greet, offerStartTls }) {
    let inData = false;
    let authStep = 0;
    let buf = '';
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
        if (authStep === 1) {
          authStep = 2;
          sock.write('334 UGFzc3dvcmQ6\r\n');
          continue;
        }
        if (authStep === 2) {
          authStep = 0;
          if (!echoAuth) { sock.write('235 ok\r\n'); continue; }
          // A hostile relay that quotes the credential. The client must not
          // let that text out of sendMail still holding the password.
          let decoded = '';
          try { decoded = Buffer.from(line, 'base64').toString('utf8'); } catch { decoded = ''; }
          sock.write(`535 refused ${line} ${decoded}\r\n`);
          continue;
        }
        const verb = line.split(' ')[0].toUpperCase();
        if (verb === 'EHLO') {
          sock.write(offerStartTls
            ? '250-fake\r\n250-STARTTLS\r\n250 OK\r\n'
            : '250-fake\r\n250 OK\r\n');
        } else if (verb === 'STARTTLS' && offerStartTls) {
          sock.write('220 go\r\n');
          sock.removeAllListeners('data');
          const secure = new tls.TLSSocket(sock, { isServer: true, key: pem.key, cert: pem.cert });
          secure.on('error', () => {});
          speak(secure, { greet: false, offerStartTls: false });
        } else if (verb === 'DATA') { inData = true; sock.write('354 go\r\n'); }
        else if (verb === 'QUIT') { sock.write('221 bye\r\n'); sock.end(); }
        else if (verb === 'AUTH') { authStep = 1; sock.write('334 VXNlcm5hbWU6\r\n'); }
        else sock.write('250 OK\r\n');
      }
    });
    if (greet) sock.write('220 fake ESMTP\r\n');
  }

  const server = mode === 'implicit'
    ? tls.createServer({ key: pem.key, cert: pem.cert }, (sock) => { speak(sock, { greet: true, offerStartTls: false }); })
    : net.createServer((sock) => { speak(sock, { greet: true, offerStartTls: mode === 'starttls' }); });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, lines })));
}

async function withSmtp(env, fn, { mode = 'starttls', echoAuth = false } = {}) {
  const { server, lines } = await fakeSmtp({ mode, echoAuth });
  const saved = {};
  const set = {
    SMTP_HOST: '127.0.0.1', SMTP_PORT: String(server.address().port),
    SMTP_SECURE: mode === 'implicit' ? 'true' : 'false',
    SMTP_USER: '', SMTP_PASS: '', MAIL_FROM: '', SMTP_FROM: '', MAIL_REPLY_TO: '',
    SMTP_TLS_REJECT_UNAUTHORIZED: 'false',
    ...env,
  };
  for (const [k, v] of Object.entries(set)) { saved[k] = process.env[k]; process.env[k] = v; }
  try {
    return await fn(lines);
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
    server.close();
  }
}

async function sendThrough(env, opts) {
  return withSmtp(env, async (lines) => {
    const r = await mailer.sendMail({ to: 'member@example.com', subject: 'Hi', text: 'body' });
    assert.deepEqual(r, { skipped: false });
    return lines;
  }, opts);
}

test('the mailer says EHLO with the sender\'s domain, not a .local name', async () => {
  const lines = await sendThrough({ MAIL_FROM: 'RUNECLAW <info@humanoid-traders.com>' });
  assert.equal(lines[0], 'EHLO humanoid-traders.com', lines.join(' | '));
  // A sender that names no usable domain keeps the old fixed name rather
  // than putting something malformed on the wire.
  assert.equal(mailer._ehloName('RUNECLAW <nobody>'), 'runeclaw.local');
  assert.equal(mailer._ehloName('a@bad domain.com'), 'runeclaw.local');
});

test('a blank MAIL_FROM does not hide SMTP_FROM, and no sender uses the Gmail account', async () => {
  const viaSmtpFrom = await sendThrough({
    MAIL_FROM: ' ',
    SMTP_FROM: 'Desk <desk@humanoid-traders.com>',
    SMTP_USER: 'owner@gmail.com',
  });
  assert.ok(viaSmtpFrom.some((l) => l === 'From: Desk <desk@humanoid-traders.com>'),
    viaSmtpFrom.join('\n'));
  const viaDefault = await sendThrough({
    MAIL_FROM: '',
    SMTP_FROM: '',
    SMTP_USER: 'owner@gmail.com',
  });
  assert.ok(viaDefault.some((l) => l === 'From: Humanoid Traders <owner@gmail.com>'),
    viaDefault.join('\n'));
  assert.equal(viaDefault[0], 'EHLO gmail.com');
  assert.equal(mailer.defaultFrom('owner@gmail.com'), 'Humanoid Traders <owner@gmail.com>');
  assert.equal(mailer.defaultFrom('apikey'), '');
  assert.equal(mailer.defaultFrom('owner@gmail.com\r\nBcc: x@y.z'), '');
});

test('port 587 upgrades with STARTTLS before AUTH, and port 465 is TLS from the first byte', async () => {
  const pass = 'app-password-secret';
  const starttls = await sendThrough({
    SMTP_USER: 'owner@gmail.com',
    SMTP_PASS: pass,
    SMTP_SECURE: 'false',
  }, { mode: 'starttls' });
  const startIdx = starttls.indexOf('STARTTLS');
  const authIdx = starttls.findIndex((l) => l.startsWith('AUTH'));
  assert.ok(startIdx > 0 && authIdx > startIdx, starttls.join(' | '));
  assert.equal(starttls.filter((l) => l.startsWith('EHLO')).length, 2);

  const implicit = await sendThrough({
    SMTP_USER: 'owner@gmail.com',
    SMTP_PASS: pass,
    SMTP_SECURE: 'true',
  }, { mode: 'implicit' });
  assert.equal(implicit.some((l) => l === 'STARTTLS'), false, implicit.join(' | '));
  assert.ok(implicit.some((l) => l === 'From: Humanoid Traders <owner@gmail.com>'));
  assert.equal(implicit.filter((l) => l.startsWith('EHLO')).length, 1);
});

test('a relay with no STARTTLS is refused before the password is sent', async () => {
  const pass = 'app-password-secret';
  const logs = [];
  const orig = console.log;
  const origWarn = console.warn;
  console.log = (line) => logs.push(String(line));
  console.warn = (line) => logs.push(String(line));
  let seen = [];
  try {
    await assert.rejects(
      () => withSmtp({
        SMTP_USER: 'owner@gmail.com',
        SMTP_PASS: pass,
        MAIL_FROM: 'Humanoid Traders <owner@gmail.com>',
      }, async (lines) => {
        try {
          await mailer.sendMail({ to: 'member@example.com', subject: 'Hi', text: 'body' });
        } finally {
          seen = lines.slice();
        }
      }, { mode: 'plain' }),
      (err) => {
        assert.equal(err.code, 'ESTARTTLS');
        assert.equal(String(err.message).includes(pass), false);
        assert.equal(String(err.stack).includes(pass), false);
        return true;
      },
    );
  } finally {
    console.log = orig;
    console.warn = origWarn;
  }
  assert.equal(seen.some((l) => l.startsWith('AUTH') || l.startsWith('MAIL')), false, seen.join(' | '));
  assert.equal(logs.join('\n').includes(pass), false);
});

test('an SMTP reply that quotes the password does not leave sendMail carrying it', async () => {
  const pass = 'app-password-secret';
  const b64 = Buffer.from(pass).toString('base64');
  await assert.rejects(
    () => sendThrough({
      SMTP_USER: 'owner@gmail.com',
      SMTP_PASS: pass,
    }, { echoAuth: true }),
    (err) => {
      assert.match(err.message, /\[redacted\]/);
      assert.equal(err.message.includes(pass), false);
      assert.equal(err.message.includes(b64), false);
      assert.equal(String(err.stack).includes(pass), false);
      assert.equal(String(err.stack).includes(b64), false);
      return true;
    },
  );
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
