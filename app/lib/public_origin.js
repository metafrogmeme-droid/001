'use strict';
/**
 * The one place that answers "what is this deployment's public URL?".
 *
 * Four things need that answer and, until now, three different env vars supplied
 * it: PUBLIC_ORIGIN (phone-link QR), APP_BASE_URL (email links, OAuth redirect,
 * ERC-8257 manifest) and WEBSITE_URL (sitemap/robots). An operator who set one
 * fixed one surface and left the rest silently wrong — and "silently" is the
 * problem, because every one of these is consumed OUTSIDE the server:
 *
 *   - a QR opened on a phone
 *   - a verification link clicked from an inbox
 *   - an OAuth redirect the provider must match exactly
 *   - a metadataURI whose keccak256 is registered on-chain
 *   - sitemap URLs a crawler follows
 *
 * None of them can be validated from inside the request that produced them,
 * which is exactly how the phone-link QR shipped pointing at an internal VPC
 * hostname over plain http while every server-side test passed.
 *
 * Resolution order, first non-empty wins:
 *   PUBLIC_ORIGIN → APP_BASE_URL → WEBSITE_URL → the edge proxy's forwarded
 *   host → the request host.
 *
 * A host that cannot be reached from outside is REFUSED rather than returned.
 */

// Hosts that only resolve inside a datacentre. fcapp.run / *.vpc.* is the
// Function-Compute shape that actually shipped; the rest are the usual private
// ranges and mDNS/internal suffixes.
const PRIVATE_HOST = /(^|\.)(local|internal|localdomain)$|\.vpc\.|(^|\.)fcapp\.run$|^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\./i;

const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/i;

/** The configured origin, if any. No request needed — usable at boot. */
function configured(env) {
  const e = env || process.env;
  const v = String(e.PUBLIC_ORIGIN || e.APP_BASE_URL || e.WEBSITE_URL || '').trim();
  return v ? v.replace(/\/+$/, '') : '';
}

/** True when `host` is something a phone/crawler/inbox could actually reach. */
function isPubliclyReachable(host) {
  const h = String(host || '').trim();
  if (!h) return false;
  if (LOCAL_HOST.test(h)) return true;          // development
  return !PRIVATE_HOST.test(h.replace(/:\d+$/, ''));
}

/**
 * resolve(req, env) → { origin } | { error }
 *
 * `req` may be omitted for callers with no request in hand (boot-time config
 * checks, the ERC-8257 manifest), in which case only configuration counts.
 */
function resolve(req, env) {
  const cfg = configured(env);
  if (cfg) return { origin: cfg };
  if (!req) {
    return { error: 'This deployment does not know its public URL. Set PUBLIC_ORIGIN '
      + '(e.g. https://your-domain).' };
  }
  const get = (h) => String((req.get ? req.get(h) : (req.headers || {})[h]) || '').split(',')[0].trim();
  const host = get('x-forwarded-host') || get('host');
  if (!isPubliclyReachable(host)) {
    return { error: 'This deployment does not know its public URL, so the link it '
      + 'would produce points somewhere the outside world cannot reach. Set '
      + 'PUBLIC_ORIGIN (e.g. https://your-domain).' };
  }
  // Anything not local is served over TLS at the edge. A link handed to a phone
  // or an inbox must never carry http://, whatever the internal hop used.
  const local = LOCAL_HOST.test(host);
  const proto = local ? (get('x-forwarded-proto') || req.protocol || 'http') : 'https';
  return { origin: `${proto}://${host}` };
}

/** Convenience for callers that treat "unknown" as empty rather than an error. */
function originOr(req, fallback, env) {
  const r = resolve(req, env);
  return r.origin || fallback || '';
}

module.exports = { resolve, originOr, configured, isPubliclyReachable, PRIVATE_HOST, LOCAL_HOST };
