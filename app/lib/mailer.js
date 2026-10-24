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
//   SMTP_HOST      smtp.example.com            (required to actually send)
//   SMTP_PORT      587 (default) or 465
//   SMTP_SECURE    "true" → implicit TLS (port 465). Otherwise STARTTLS on 587.
//   SMTP_USER      login username              (optional; AUTH LOGIN if set)
//   SMTP_PASS      login password / API key    (optional)
//   MAIL_FROM      optional override. Blank falls through to SMTP_FROM,
//                  then to the built-in noreply sender below.
//   SMTP_FROM      same job as MAIL_FROM, used when MAIL_FROM is blank.
//   MAIL_REPLY_TO  support@…                   (optional; where a reply goes
//                  when the sender is an address nobody reads)
//   PUBLIC_ORIGIN / APP_BASE_URL / WEBSITE_URL  (links; see lib/public_origin.js)
//
// Secrets are read from the environment only. An unconfigured send logs one
// line (recipient and subject) and never the body — a body preview is the
// verification token.

const net = require('net');
const tls = require('tls');

// Used when neither MAIL_FROM nor SMTP_FROM is set. An explicit sender still
// wins, and a blank MAIL_FROM does not hide a set SMTP_FROM.
const DEFAULT_FROM = 'Humanoid Traders <noreply@humanoid-traders.com>';

function _cfg(env = process.env) {
  const host = (env.SMTP_HOST || '').trim();
  // Trimmed before the fallback, so a blank MAIL_FROM does not hide SMTP_FROM.
  const from = (env.MAIL_FROM || '').trim()
    || (env.SMTP_FROM || '').trim()
    || DEFAULT_FROM;
  const secure = String(env.SMTP_SECURE || '').toLowerCase() === 'true';
  const port = parseInt(env.SMTP_PORT || (secure ? '465' : '587'), 10);
  return {
    host,
    from,
    secure,
    port: Number.isFinite(port) ? port : 587,
    user: env.SMTP_USER || '',
    pass: env.SMTP_PASS || '',
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
  // The sender always resolves (explicit, then the built-in noreply address),
  // so the only thing that leaves mail as a no-op is a missing host.
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

function _expect(reply, ...codes) {
  if (!codes.includes(reply.code)) {
    const err = new Error(`SMTP unexpected reply ${reply.code}: ${reply.text.trim()}`);
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
        servername: cfg.host,
        rejectUnauthorized: cfg.rejectUnauthorized,
      };
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
  try {
    _expect(await smtp.read(), 220);
    const ehloHost = _ehloName(cfg.from);
    let reply = _expect(await smtp.send(`EHLO ${ehloHost}`), 250);

    if (!cfg.secure && /STARTTLS/i.test(reply.text)) {
      _expect(await smtp.send('STARTTLS'), 220);
      socket = await connectTLS(socket);
      smtp = _talk(socket, cfg);
      reply = _expect(await smtp.send(`EHLO ${ehloHost}`), 250);
    }

    if (cfg.user) {
      _expect(await smtp.send('AUTH LOGIN'), 334);
      _expect(await smtp.send(Buffer.from(cfg.user, 'utf8').toString('base64')), 334);
      _expect(await smtp.send(Buffer.from(cfg.pass, 'utf8').toString('base64')), 235);
    }

    _expect(await smtp.send(`MAIL FROM:<${envelope.from}>`), 250);
    _expect(await smtp.send(`RCPT TO:<${envelope.to}>`), 250, 251);
    _expect(await smtp.send('DATA'), 354);
    // Send the message body followed by the terminating "<CRLF>.<CRLF>".
    socket.write(message);
    if (!message.endsWith('\r\n')) socket.write('\r\n');
    _expect(await smtp.send('.'), 250);
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
  const envelope = { from: _addrOnly(cfg.from), to: String(to).trim() };
  const message = buildMessage({ from: cfg.from, to, subject, text, html, replyTo: cfg.replyTo });
  await _deliver(cfg, envelope, message);
  console.log(`[mailer] sent to=${to} subject="${subject}"`);
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
  DEFAULT_FROM,
};
