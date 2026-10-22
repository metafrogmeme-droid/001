'use strict';
/**
 * Post-quantum TLS, checked: does a host negotiate the hybrid key exchange
 * X25519MLKEM768 with a client that offers it?
 *
 * A TLS 1.3 handshake's key exchange is what a recorded session's secrecy
 * rests on. A classical one (X25519) can be recorded now and decrypted later
 * by a large enough quantum computer; the hybrid adds ML-KEM-768 (FIPS 203)
 * beside X25519, so the session stays secret unless both are broken. The
 * roadmap's mainnet gate asks that the site, the API and the webhooks
 * negotiate it.
 *
 * HOW IT IS ASKED. One handshake offering what a current browser offers, the
 * hybrid first and the classical groups after it, and the group the server
 * chose is read from the connection (getEphemeralKeyInfo). The server's
 * choice IS the answer: a server that supports the hybrid but prefers X25519
 * gives browsers X25519.
 *
 * THREE VERDICTS AND A NON-VERDICT, as scripts/verify_deploy.sh has them:
 *   pq            the server chose X25519MLKEM768
 *   classical     the handshake completed on a classical group
 *   unknown       no handshake: the host could not be reached, refused,
 *                 timed out, or failed TLS for another reason
 *   cannot_check  this machine's TLS library does not know the hybrid group,
 *                 so it could not have been offered
 * "Could not reach it" is never "not post-quantum", and a library that cannot
 * offer the group measures nothing about the host.
 *
 * Nothing here sends a request: the handshake is the whole check, and the
 * connection is closed as soon as it completes.
 */
const tls = require('tls');

const PQ_GROUP = 'X25519MLKEM768';
/** What is offered: the hybrid first, then what a browser falls back to. */
const OFFER = [PQ_GROUP, 'X25519', 'P-256', 'P-384'].join(':');
const TIMEOUT_MS = 10000;

/** Whether this process's TLS library can offer the hybrid group at all. */
function canOffer() {
  try {
    tls.createSecureContext({ ecdhCurve: PQ_GROUP, minVersion: 'TLSv1.3' });
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * One handshake to `host:port`. Resolves, never rejects:
 *   { verdict, host, port, group, protocol }   when the handshake completed
 *   { verdict: 'unknown', host, port, reason } when it did not
 *   { verdict: 'cannot_check', host, port, reason }
 * `opts.servername` (default host) is the name sent and verified;
 * `opts.connect` stands in for tls.connect in a test; `opts.ca` and
 * `opts.rejectUnauthorized` reach it unchanged.
 */
function checkHost(host, port = 443, opts = {}) {
  const base = { host: String(host), port: Number(port) };
  if (!(opts.canOffer || canOffer)()) {
    return Promise.resolve({ ...base, verdict: 'cannot_check',
      reason: `this TLS library cannot offer ${PQ_GROUP}; run the check with Node 24 or later` });
  }
  const connect = opts.connect || tls.connect;
  const timeoutMs = Number(opts.timeoutMs) || TIMEOUT_MS;
  return new Promise((resolve) => {
    let done = false;
    const finish = (out) => { if (!done) { done = true; resolve({ ...base, ...out }); } };
    let socket;
    try {
      socket = connect({
        host: base.host, port: base.port, servername: opts.servername || base.host,
        ecdhCurve: OFFER, minVersion: 'TLSv1.2', ALPNProtocols: ['http/1.1'],
        ...(opts.ca ? { ca: opts.ca } : {}),
        ...(opts.rejectUnauthorized === false ? { rejectUnauthorized: false } : {}),
      }, () => {
        const info = typeof socket.getEphemeralKeyInfo === 'function' ? socket.getEphemeralKeyInfo() : null;
        const protocol = typeof socket.getProtocol === 'function' ? socket.getProtocol() : null;
        socket.destroy();
        // A runtime that cannot say what was negotiated has not said.
        if (!info || typeof info !== 'object') return finish({ verdict: 'unknown', protocol, reason: 'the negotiated group could not be read' });
        // A named group is the answer. A TLS 1.2 handshake can complete with
        // no named group at all: finite-field DH reports only a type and a
        // size, and static RSA key transport reports nothing, because nothing
        // ephemeral was agreed. Both are classical, the second without even
        // forward secrecy; neither is "could not be read".
        const group = info.name ? String(info.name)
          : info.type ? `${info.type}${info.size ? '-' + info.size : ''}` : 'no ephemeral key exchange';
        finish({ verdict: group === PQ_GROUP ? 'pq' : 'classical', group, protocol });
      });
    } catch (e) {
      return finish({ verdict: 'unknown', reason: (e && e.code) || (e && e.name) || 'Error' });
    }
    socket.setTimeout(timeoutMs, () => { socket.destroy(); finish({ verdict: 'unknown', reason: `no handshake within ${timeoutMs} ms` }); });
    // The reason names the error's code, never its message: a message can
    // carry a resolver's or a proxy's internals.
    socket.on('error', (e) => finish({ verdict: 'unknown', reason: (e && e.code) || (e && e.name) || 'Error' }));
  });
}

/**
 * The exit status for a set of results, as verify_deploy.sh has it:
 *   0  every host is post-quantum            a verdict
 *   1  a host negotiated a classical group   a verdict
 *   3  something could not be checked        not a verdict
 * A classical host is a finding whatever else could not be read; an empty
 * set checked nothing and is 3.
 */
function exitCode(results) {
  const list = results || [];
  if (!list.length) return 3;
  if (list.some((r) => r.verdict === 'classical')) return 1;
  if (list.every((r) => r.verdict === 'pq')) return 0;
  return 3;
}

/**
 * The hosts the deploy serves, from the variables that already name them,
 * never a second list: WEB_URL (verify_deploy.sh's, default the site), its
 * www twin, MEMECLAW_HOSTS (app/lib/memeclaw_host.js), and the bot gateway's
 * public URL when it is https (BOT_GATEWAY_URL; the webhooks reach the bot
 * through it). Each as { host, port }, once.
 */
function deployHosts(env = process.env) {
  const out = [];
  const add = (raw) => {
    let u;
    try { u = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `https://${raw}`); } catch (e) { return; }
    if (u.protocol !== 'https:' || !u.hostname) return;
    const host = u.hostname.toLowerCase();
    const port = u.port ? Number(u.port) : 443;
    if (!out.some((h) => h.host === host && h.port === port)) out.push({ host, port });
  };
  const web = String(env.WEB_URL || 'https://humanoid-traders.com').trim();
  add(web);
  try {
    const h = new URL(web).hostname;
    add(h.startsWith('www.') ? h.slice(4) : `www.${h}`);
  } catch (e) { /* an unreadable WEB_URL adds nothing */ }
  String(env.MEMECLAW_HOSTS || '').split(',').map((s) => s.trim()).filter(Boolean).forEach(add);
  if (env.BOT_GATEWAY_URL) add(String(env.BOT_GATEWAY_URL).trim());
  return out;
}

module.exports = { PQ_GROUP, OFFER, TIMEOUT_MS, canOffer, checkHost, exitCode, deployHosts };
