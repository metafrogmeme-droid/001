'use strict';

// Provider-agnostic transactional email — dependency-free.
//
// Works with ANY provider that speaks SMTP (Gmail, SendGrid, Postmark, Mailgun,
// Amazon SES, a self-hosted Postfix …). No npm dependency: it drives SMTP over
// Node's built-in net/tls. When SMTP is not configured it is a safe no-op that
// logs what it *would* have sent — so password-reset / email-verification flows
// degrade gracefully on a fresh deploy instead of crashing.
//
// Configure via env:
//   SMTP_HOST      smtp.gmail.com              (required to actually send)
//   SMTP_PORT      587 (default) or 465
//   SMTP_SECURE    "true" → implicit TLS (port 465). Otherwise STARTTLS on
//                  587, and the relay must offer it: the password is not sent
//                  on a clear connection.
//   SMTP_USER      the mailbox, an email address (AUTH LOGIN if set)
//   SMTP_PASS      app password or API key     (never logged)
//   MAIL_FROM      optional override. Blank falls through to SMTP_FROM, then
//                  to "Humanoid Traders <SMTP_USER>" when SMTP_USER is an
//                  address. Gmail rejects a From that is not that account.
//   SMTP_FROM      same job as MAIL_FROM, used when MAIL_FROM is blank.
//   MAIL_REPLY_TO  optional. With a Gmail mailbox, replies already land there.
//   PUBLIC_ORIGIN / APP_BASE_URL / WEBSITE_URL  (links; see lib/public_origin.js)
//
// Secrets are read from the environment only. An unconfigured send logs one
// line (recipient and subject) and never the body — a body preview is the
// verification token. A delivery error is scrubbed of SMTP_PASS before it
// is thrown. An own-domain noreply address is a documented alternative
// (docs/EMAIL_SETUP.md), set explicitly via MAIL_FROM; it is not the default.

const net = require('net');
const tls = require('tls');

const DISPLAY_NAME = 'Humanoid Traders';

// SMTP_USER is the From address only when it is a single mailbox. A login
// that is not an address (an API key, a blank) is not wrapped into one:
// Gmail would reject it, and a header must not carry a line break.
function defaultFrom(user) {
  const addr = String(user || '').trim();
  if (!addr || /[\r\n<>\s]/.test(addr)) return '';
  const at = addr.indexOf('@');
  if (at <= 0 || at !== addr.lastIndexOf('@') || !addr.slice(at + 1).includes('.')) return '';
  return `${DISPLAY_NAME} <${addr}>`;
}

function _cfg(env = process.env) {
  const host = (env.SMTP_HOST || '').trim();
  const user = String(env.SMTP_USER || '').trim();
  // Trimmed before the fallback, so a blank MAIL_FROM does not hide SMTP_FROM.
  // Neither set: the mailbox itself, which is the only From Gmail will send.
  const from = (env.MAIL_FROM || '').trim()
    || (env.SMTP_FROM || '').trim()
    || defaultFrom(user);
  const secure = String(env.SMTP_SECURE || '').toLowerCase() === 'true';
  const port = parseInt(env.SMTP_PORT || (secure ? '465' : '587'), 10);
  return {
    host,
    from,
    secure,
    // Port 587 is clear until STARTTLS. Require the upgrade before AUTH.
    requireTLS: !secure,
    port: Number.isFinite(port) ? port : 587,
    user,
    pass: String(env.SMTP_PASS || ''),
    replyTo: (env.MAIL_REPLY_TO || '').trim(),
    // Allow disabling cert verification only via an explicit opt-out (some
    // internal relays use self-signed certs). Defaults to verifying.
    rejectUnauthorized:
      String(env.SMTP_TLS_REJECT_UNAUTHORIZED || 'true').toLowerCase() !== 'false',
  };
}

// What the mailer still needs before it sends, by the names an operator sets.
// This is the one reading of "is mail configured": the boot config audit and
// the Admin page ask it too, so they cannot warn "unset" over a SMTP_FROM the
// mailer is sending with (lib/config_audit.js used to check MAIL_FROM alone).
function missingConfig(env = process.env) {
  const c = _cfg(env);
  // A missing host is the no-op. The sender resolves from MAIL_FROM,
  // SMTP_FROM, or SMTP_USER; it is not a second "is mail on" switch.
  return [!c.host && 'SMTP_HOST'].filter(Boolean);
}

function isConfigured(env = process.env) {
  return missingConfig(env).length === 0;
}

function baseUrl() {
  // A verification link is clicked from an INBOX — the one place a wrong
  // hostname is both invisible to us and unfixable by the user.
  return require('./public_origin').configured();
}

// --- RFC 5322 message assembly (pure; exported for tests) ---

function _foldFrom(from) {
  // Accept "Name <addr>" or a bare address.
  return from;
}

function _addrOnly(from) {
  const m = /<([^>]+)>/.exec(from);
  return (m ? m[1] : from).trim();
}

// Encode a header value that may contain non-ASCII using RFC 2047 (UTF-8, B).
function _encodeHeader(value) {
  // eslint-disable-next-line no-control-regex
  if (/^[\x00-\x7F]*$/.test(value)) return value;
  return `=?UTF-8?B?${Buffer.from(value, 'utf8').toString('base64')}?=`;
}

// The EHLO name. A relay may refuse a client that introduces itself with a
// name that cannot exist (`*.local`), and the sender's own domain is the one
// name this deployment is entitled to. Anything that is not a plain hostname
// falls back to the old fixed name rather than reaching the wire.
function _ehloName(from) {
  const domain = (_addrOnly(String(from || '')).split('@')[1] || '').trim();
  return /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/.test(domain) && domain.includes('.')
    ? domain : 'runeclaw.local';
}

function buildMessage({ from, to, subject, text, html, date, replyTo }) {
  const boundary = 'rc_' + Buffer.from(String(subject || '') + '|' + String(to || ''))
    .toString('hex')
    .slice(0, 24);
  const headers = [
    `From: ${_foldFrom(from)}`,
    `To: ${to}`,
    // Only an address: a header value with a line break in it is a second
    // header, and this one comes from the environment, not the user.
    ...(replyTo && !/[\r\n]/.test(replyTo) ? [`Reply-To: ${replyTo}`] : []),
    `Subject: ${_encodeHeader(subject || '')}`,
    `Date: ${(date || new Date()).toUTCString()}`,
    'MIME-Version: 1.0',
  ];
  let body;
  if (html) {
    headers.push(`Content-Type: multipart/alternative; boundary="${boundary}"`);
    const textPart = [
      `--${boundary}`,
      'Content-Type: text/plain; charset=UTF-8',
      'Content-Transfer-Encoding: base64',
      '',
      Buffer.from(text || '', 'utf8').toString('base64'),
    ].join('\r\n');
    const htmlPart = [
      `--${boundary}`,
      'Content-Type: text/html; charset=UTF-8',
      'Content-Transfer-Encoding: base64',
      '',
      Buffer.from(html, 'utf8').toString('base64'),
    ].join('\r\n');
    body = `${textPart}\r\n${htmlPart}\r\n--${boundary}--\r\n`;
  } else {
    headers.push('Content-Type: text/plain; charset=UTF-8');
    headers.push('Content-Transfer-Encoding: base64');
    body = Buffer.from(text || '', 'utf8').toString('base64') + '\r\n';
  }
  // Dot-stuffing: any line beginning with '.' must be doubled per RFC 5321.
  const message = `${headers.join('\r\n')}\r\n\r\n${body}`;
  return message.replace(/\r\n\./g, '\r\n..');
}

// --- SMTP conversation ---

function _talk(socket, cfg) {
  // A tiny promise-based SMTP client bound to an established socket.
  let buffer = '';
  let waiter = null;

  socket.setEncoding('utf8');
  socket.on('data', (chunk) => {
    buffer += chunk;
    // A full reply ends with a line "NNN <text>" (space after the code).
    const lines = buffer.split('\r\n').filter((l) => l.length > 0);
    const last = lines[lines.length - 1];
    if (last && /^\d{3} /.test(last) && waiter) {
      const code = parseInt(last.slice(0, 3), 10);
      const text = buffer;
      buffer = '';
      const w = waiter;
      waiter = null;
      w.resolve({ code, text });
    }
  });

  function read() {
    return new Promise((resolve, reject) => {
      waiter = { resolve, reject };
    });
  }

  function send(line) {
    socket.write(line + '\r\n');
    return read();
  }

  return { read, send };
}

function _redact(text, cfg) {
  let s = String(text == null ? '' : text);
  const pass = cfg && cfg.pass;
  if (!pass) return s;
  // AUTH LOGIN puts the password on the wire as base64. Scrub both shapes
  // before an SMTP reply can be logged by a caller.
  for (const secret of [pass, Buffer.from(pass, 'utf8').toString('base64')]) {
    if (secret) s = s.split(secret).join('[redacted]');
  }
  return s;
}

function _expect(reply, cfg, ...codes) {
  if (!codes.includes(reply.code)) {
    const err = new Error(`SMTP unexpected reply ${reply.code}: ${_redact(String(reply.text || '').trim(), cfg)}`);
    err.code = reply.code;
    throw err;
  }
  return reply;
}

async function _deliver(cfg, envelope, message) {
  const connectTimeoutMs = 15000;

  function connectPlain() {
    return new Promise((resolve, reject) => {
      const s = net.createConnection({ host: cfg.host, port: cfg.port });
      s.setTimeout(connectTimeoutMs);
      s.once('timeout', () => { s.destroy(new Error('SMTP connect timeout')); });
      s.once('error', reject);
      s.once('connect', () => { s.removeListener('error', reject); resolve(s); });
    });
  }

  function connectTLS(existing) {
    return new Promise((resolve, reject) => {
      const opts = {
        host: cfg.host,
        rejectUnauthorized: cfg.rejectUnauthorized,
      };
      // SNI is a hostname. Node otherwise copies `host`, and an address is not
      // a name it will send (a local relay in tests is one).
      opts.servername = net.isIP(cfg.host) ? 'localhost' : cfg.host;
      if (existing) opts.socket = existing;
      const s = tls.connect(existing ? opts : { port: cfg.port, ...opts });
      s.setTimeout(connectTimeoutMs);
      s.once('timeout', () => { s.destroy(new Error('SMTP TLS timeout')); });
      s.once('error', reject);
      s.once('secureConnect', () => { s.removeListener('error', reject); resolve(s); });
    });
  }

  let socket = cfg.secure ? await connectTLS() : await connectPlain();
  let smtp = _talk(socket, cfg);
  const expect = (reply, ...codes) => _expect(reply, cfg, ...codes);
  try {
    expect(await smtp.read(), 220);
    const ehloHost = _ehloName(cfg.from);
    let reply = expect(await smtp.send(`EHLO ${ehloHost}`), 250);

    if (cfg.requireTLS) {
      // STARTTLS before any credential. A relay that does not offer it is
      // refused: continuing would send SMTP_PASS in the clear.
      if (!/STARTTLS/i.test(reply.text)) {
        const err = new Error('SMTP server did not offer STARTTLS');
        err.code = 'ESTARTTLS';
        throw err;
      }
      expect(await smtp.send('STARTTLS'), 220);
      socket = await connectTLS(socket);
      smtp = _talk(socket, cfg);
      expect(await smtp.send(`EHLO ${ehloHost}`), 250);
    }

    if (cfg.user) {
      expect(await smtp.send('AUTH LOGIN'), 334);
      expect(await smtp.send(Buffer.from(cfg.user, 'utf8').toString('base64')), 334);
      expect(await smtp.send(Buffer.from(cfg.pass, 'utf8').toString('base64')), 235);
    }

    expect(await smtp.send(`MAIL FROM:<${envelope.from}>`), 250);
    expect(await smtp.send(`RCPT TO:<${envelope.to}>`), 250, 251);
    expect(await smtp.send('DATA'), 354);
    // Send the message body followed by the terminating "<CRLF>.<CRLF>".
    socket.write(message);
    if (!message.endsWith('\r\n')) socket.write('\r\n');
    expect(await smtp.send('.'), 250);
    try { await smtp.send('QUIT'); } catch { /* server may drop after QUIT */ }
  } finally {
    socket.end();
    socket.destroy();
  }
}

// Send an email. Resolves { skipped, messageId } — never rejects for a
// not-configured mailer (the caller treats email as best-effort). A real
// delivery failure DOES reject, so callers can log it.
function _oneLine(value) {
  return String(value || '').replace(/[\r\n]/g, ' ').slice(0, 200);
}

async function sendMail({ to, subject, text, html }) {
  const cfg = _cfg();
  if (!cfg.host) {
    // One line, no body. The body of a verification mail is the token.
    console.warn(`[mailer] not configured; email not sent to=${_oneLine(to)} subject="${_oneLine(subject)}"`);
    return { skipped: true, reason: 'not_configured' };
  }
  if (!cfg.from) {
    const err = new Error('MAIL_FROM is unset and SMTP_USER is not an email address');
    err.code = 'ENOMFROM';
    throw err;
  }
  const envelope = { from: _addrOnly(cfg.from), to: String(to).trim() };
  const message = buildMessage({ from: cfg.from, to, subject, text, html, replyTo: cfg.replyTo });
  await _deliver(cfg, envelope, message);
  console.log(`[mailer] sent to=${_oneLine(to)} subject="${_oneLine(subject)}"`);
  return { skipped: false };
}

module.exports = {
  isConfigured,
  missingConfig,
  baseUrl,
  sendMail,
  // exported for tests
  buildMessage,
  _addrOnly,
  _encodeHeader,
  _ehloName,
  _cfg,
  defaultFrom,
  DISPLAY_NAME,
};
