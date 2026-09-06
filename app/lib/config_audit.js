/**
 * Boot-time configuration audit — turn silent config degradation into a loud
 * signal.
 *
 * Many optional envs default to a no-op so the site still boots: unset SMTP
 * silently drops verification/reset emails, an unset WEB_GATEWAY_SECRET makes
 * web chat/trade return a mid-flow 503, an empty WEB_CREDS_KEY makes exchange-
 * key submission fail only when a user tries it, and a missing APP_BASE_URL
 * bakes malformed hosts into email links and OAuth redirect URIs. Each of those
 * failures is invisible at startup and only surfaces later as a broken user
 * flow.
 *
 * auditConfig() surfaces all of it ONCE at boot: a warning per degraded flow,
 * and — in production only — a hard finding for the cases that are security-
 * sensitive or that silently break a configured core flow. It is pure and takes
 * its env/logger by injection so it can be unit-tested without touching the real
 * process; server.js calls it with defaults and exits on a fatal in production.
 */

// A WEB_CREDS_KEY must decode (standard or url-safe base64) to exactly 32 bytes,
// matching lib/creds_crypto.loadKey — a set-but-malformed key is worse than an
// unset one because encryption throws at submit time with no boot signal.
function credsKeyState(raw) {
  if (!raw) return 'unset';
  const b64 = String(raw).replace(/-/g, '+').replace(/_/g, '/');
  let buf;
  try { buf = Buffer.from(b64, 'base64'); } catch { return 'invalid'; }
  return buf.length === 32 ? 'ok' : 'invalid';
}

function mailerConfigured(env) {
  return Boolean((env.SMTP_HOST || '').trim() && (env.MAIL_FROM || '').trim());
}

function oauthConfigured(env) {
  return Boolean(
    (env.GOOGLE_CLIENT_ID || '').trim()
    || ((env.DISCORD_CLIENT_ID || '').trim() && (env.DISCORD_CLIENT_SECRET || '').trim())
    || ((env.X_CLIENT_ID || '').trim() && (env.X_CLIENT_SECRET || '').trim()));
}

/**
 * @param {object} opts
 * @param {object} [opts.env=process.env]
 * @param {object} [opts.log=console]  needs .warn and .error
 * @param {function} [opts.onFatal]  called with the fatal findings in production
 *   (default: process.exit(1)); injected so tests don't kill the runner
 * @returns {Array<{level:'warn'|'fatal', key:string, msg:string}>}
 */
function auditConfig(opts = {}) {
  const env = opts.env || process.env;
  const log = opts.log || console;
  const prod = env.NODE_ENV === 'production';
  const findings = [];
  const warn = (key, msg) => findings.push({ level: 'warn', key, msg });
  const fatal = (key, msg) => findings.push({ level: 'fatal', key, msg });

  // WEB_CREDS_KEY. THE EFFECT OF LEAVING IT UNSET CHANGED, and the warning had
  // to change with it or it would be teaching the operator something false.
  //
  // It used to be the only thing that could protect an exchange-key
  // submission, so unset meant the connect form was off. Submissions are now
  // SEALED to the bot's own published key (lib/sealing_key.js) with nothing to
  // configure, so the form works without this. What still depends on it is
  // lib/totp.js: without a key, a newly enrolled 2FA secret is stored in the
  // clear, and a TOTP seed does not expire and cannot be rotated by the user.
  //
  // So the warning names THAT, and only that. A warning that describes a
  // surface which is actually working is how an operator learns to skip the
  // next one — which is the same misreading this key already caused once, on
  // the bot's boot line.
  const credsKey = credsKeyState(env.WEB_CREDS_KEY);
  if (credsKey === 'unset') {
    warn('WEB_CREDS_KEY', 'unset — new 2FA secrets are stored unencrypted (a TOTP seed is '
      + 'permanent). Website exchange-key connect is unaffected: submissions are sealed to '
      + "the bot's published key.");
  } else if (credsKey === 'invalid') {
    // Set-but-malformed is still fatal in production. It looks configured and
    // throws at use time, and the two things it is used for are a credential
    // submission and a second factor.
    fatal('WEB_CREDS_KEY', 'set but not a 32-byte base64 key — 2FA secrets cannot be sealed and '
      + 'legacy credential submissions fail at encrypt time.');
  }

  // Web gateway secret — the shared secret the chat/trade proxies present to the
  // bot. Unset/short → those routes 503. Degraded, not insecure → warn only.
  const gw = (env.WEB_GATEWAY_SECRET || '').trim();
  if (!gw) {
    warn('WEB_GATEWAY_SECRET', 'unset — web chat and web trade are disabled (routes return 503).');
  } else if (gw.length < 16) {
    warn('WEB_GATEWAY_SECRET', 'shorter than 16 chars — weak shared secret for the bot gateway.');
  }

  // Where that secret is SENT, and over what.
  //
  // Two traps, both hit in production on 2026-07-28 and both silent:
  //
  // 1. A near-miss env name. start.js set GATEWAY_URL; this app reads
  //    BOT_GATEWAY_URL. Nothing complained — the app simply kept its
  //    localhost default and every chat request went nowhere, which on a
  //    managed page host means the bot is not there at all. Setting a
  //    variable the app never reads should not look identical to setting
  //    nothing.
  //
  // 2. Plain HTTP to a remote host. WEB_GATEWAY_SECRET is presented in that
  //    request, so over http:// to anything but localhost it crosses the
  //    network in cleartext on every message. The shared secret grants chat
  //    and trade access to the bot, so this is a disclosure, not a degrade.
  //
  //    It is still a WARNING, not a fatal. Refusing to boot would take down a
  //    site that is otherwise serving perfectly, over a transport the operator
  //    may have chosen deliberately while standing up the gateway — and an
  //    outage is a certain harm today against a possible one. The remedy is
  //    named in the message and the finding reaches /diagz, so it is loud
  //    without being destructive.
  const gwUrlRaw = (env.BOT_GATEWAY_URL || '').trim();
  const GW_NEAR_MISSES = ['GATEWAY_URL', 'BOT_GATEWAY', 'BOT_URL', 'GATEWAY_BASE_URL'];
  if (!gwUrlRaw) {
    const named = GW_NEAR_MISSES.filter((k) => (env[k] || '').trim());
    if (named.length) {
      warn('BOT_GATEWAY_URL',
        `unset, but ${named.join(' / ')} is set — this app reads BOT_GATEWAY_URL only, `
        + 'so the gateway is falling back to http://localhost:8080 and web chat/trade '
        + 'will not reach the bot. Rename the variable.');
    }
  } else {
    let host = '';
    let proto = '';
    try {
      const u = new URL(gwUrlRaw);
      host = u.hostname;
      proto = u.protocol;
    } catch (e) {
      warn('BOT_GATEWAY_URL', 'set but not a parseable URL — expected e.g. https://host:port.');
    }
    const local = host === 'localhost' || host === '127.0.0.1' || host === '::1'
      || host.endsWith('.internal') || host.endsWith('.local');
    if (proto === 'http:' && host && !local) {
      const msg = `sends WEB_GATEWAY_SECRET over plain HTTP to ${host} — the shared `
        + 'secret crosses the network in cleartext on every chat and trade request. '
        + 'Use https, or terminate TLS in front of the bot gateway.';
      warn('BOT_GATEWAY_URL', msg);
    }
  }

  // Absolute base URL. Only matters once email or OAuth is configured — then a
  // missing host produces links like "/verify?token=…" and redirect URIs like
  // "/api/auth/oauth/discord/callback" with no origin, which silently 404 or
  // fail provider validation. Fatal in production when either is enabled.
  const base = (env.APP_BASE_URL || '').trim();
  if (!base) {
    if (mailerConfigured(env) || oauthConfigured(env)) {
      fatal('APP_BASE_URL', 'unset while email/OAuth is configured — verification links and OAuth redirect URIs are malformed.');
    } else {
      warn('APP_BASE_URL', 'unset — email links and OAuth redirects will be malformed once you enable those flows.');
    }
  }

  // Transactional email. Intentionally a no-op when unset, but the operator
  // should know verification + password-reset mail is silently not being sent.
  if (!mailerConfigured(env)) {
    warn('SMTP', 'SMTP_HOST/MAIL_FROM unset — verification and password-reset emails are NOT sent (flows no-op).');
  }

  // Bot analysis bridge — powers the market-insight panel. Silent localhost
  // default is fine in dev; worth a heads-up so a prod deploy is intentional.
  if (!(env.BOT_API_URL || '').trim()) {
    warn('BOT_API_URL', 'unset — the market-insight panel falls back to http://localhost:8000.');
  }

  for (const f of findings) {
    const line = `[config] ${f.key}: ${f.msg}`;
    if (f.level === 'fatal') log.error(`FATAL ${line}`);
    else log.warn(`WARNING ${line}`);
  }

  const fatals = findings.filter((f) => f.level === 'fatal');
  if (fatals.length && prod) {
    log.error(`[config] ${fatals.length} fatal configuration problem(s) in production — refusing to start.`);
    // The default onFatal exits, and auditConfig() runs at MODULE SCOPE in
    // server.js — so from outside the container this does not look like a
    // crash, it looks like the app never finished loading. Two other boot
    // fatals had exactly that disguise and cost hours each. A console write
    // to a container's stdout PIPE can be queued and lost when the process
    // leaves immediately, so restate the reasons through fs.writeSync, which
    // cannot be. Keys and levels only — a finding's message is authored here
    // and never contains a value, but nothing is interpolated from env.
    try {
      const lines = fatals.map((f) => `  - ${f.key}: ${f.msg}`).join('\n');
      require('fs').writeSync(2,
        `FATAL: ${fatals.length} configuration problem(s) in production — `
        + `refusing to start.\n${lines}\n`);
    } catch (_) { /* logging must never be the thing that breaks boot */ }
    (opts.onFatal || (() => process.exit(1)))(fatals);
  }

  return findings;
}

module.exports = { auditConfig, credsKeyState, mailerConfigured, oauthConfigured };
