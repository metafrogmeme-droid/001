'use strict';
/**
 * Which hops are allowed to describe the caller — the Node half of
 * bot/utils/client_ip.py, reading the same TRUSTED_PROXY variable.
 *
 * WHAT WAS WRONG
 *
 * server.js said `app.set('trust proxy', 1)`. That is a hop COUNT: express
 * takes the entry one place from the right of X-Forwarded-For and calls it
 * req.ip, regardless of who actually connected. It never asks whether the peer
 * is a proxy at all.
 *
 * So reaching this server off-proxy — a misrouted port, a container published
 * by accident, anything already inside the network — makes req.ip whatever the
 * caller typed. And req.ip is the bucket key for:
 *
 *   auth.js:589   the failed-LOGIN rate limit
 *   auth.js:532   registration
 *   auth.js:995   wallet-link code issue
 *   auth.js:1394  wallet-link redeem
 *   lib/rate_limit.js:16  every rateLimit({ key: ipKey })
 *   routes/{insight,lab,macro,market,patterns}.js  per-IP quotas
 *
 * Rotating one header per request buys a fresh bucket for each of them. The
 * per-ACCOUNT lockout added in RC-AUD-026 (auth.js:598) still bounds an attack
 * on a single account, which is why this was not total — but a spray across
 * many accounts was bounded by nothing.
 *
 * THE RULE, STATED POSITIVELY
 *
 * The Python side already worked this out and wrote it down:
 *
 *     headers describing the caller are evidence only when the connection
 *     arrived from a hop we trust to have written them. Everything else is the
 *     peer address, which is the only thing TCP will vouch for.
 *
 * That fix was never ported here. This is the port, deliberately reading the
 * same TRUSTED_PROXY value so one setting configures both halves — and
 * docker-compose.yml:164 already pins the bridge subnet (172.28.0.0/16) so
 * "trust the nginx container" is expressible at all.
 *
 * DEFAULT
 *
 * No TRUSTED_PROXY set means trust nothing: req.ip is the peer. That is the
 * safe default rather than the convenient one — an unconfigured deployment
 * gets correct-but-coarse limiting, not forgeable limiting.
 */

const { BlockList, isIPv4, isIPv6 } = require('net');

function parse(raw) {
  const list = new BlockList();
  let count = 0;
  for (const entry of String(raw || '').split(',')) {
    const item = entry.trim();
    if (!item) continue;
    try {
      const slash = item.indexOf('/');
      if (slash === -1) {
        if (isIPv4(item)) list.addAddress(item, 'ipv4');
        else if (isIPv6(item)) list.addAddress(item, 'ipv6');
        else throw new Error('not an address');
        count++;
        continue;
      }
      const addr = item.slice(0, slash);
      const prefix = Number(item.slice(slash + 1));
      if (!Number.isInteger(prefix)) throw new Error('bad prefix');
      if (isIPv4(addr)) list.addSubnet(addr, prefix, 'ipv4');
      else if (isIPv6(addr)) list.addSubnet(addr, prefix, 'ipv6');
      else throw new Error('not an address');
      count++;
    } catch (_) {
      // Named once, at startup. A typo must not silently widen or narrow
      // trust: an entry that vanished quietly would leave an operator
      // believing a hop is trusted when it is not. Same reasoning, and the
      // same wording, as bot/utils/client_ip.py.
      console.warn(`TRUSTED_PROXY entry "${item}" is not an IP or CIDR — ignored`);
    }
  }
  return { list, count };
}

/** Normalise the IPv4-mapped IPv6 form Node hands back on dual-stack sockets. */
function normalize(addr) {
  const s = String(addr || '').trim();
  if (s.startsWith('::ffff:') && isIPv4(s.slice(7))) return s.slice(7);
  return s;
}

/**
 * Build the value for `app.set('trust proxy', …)`.
 *
 * Express accepts a predicate `(addr, hopIndex) => boolean`, which is the only
 * form that asks the question we care about: is the hop that connected one we
 * trust to have written X-Forwarded-For?
 */
function trustProxyFrom(raw) {
  const { list, count } = parse(raw);
  if (count === 0) return false;   // nothing declared → the peer is the evidence
  return (addr) => {
    const ip = normalize(addr);
    if (isIPv4(ip)) return list.check(ip, 'ipv4');
    if (isIPv6(ip)) return list.check(ip, 'ipv6');
    return false;
  };
}

/** True when `addr` is a configured hop. Exported for tests and for logging. */
function isTrustedProxy(addr, raw = process.env.TRUSTED_PROXY) {
  const t = trustProxyFrom(raw);
  return typeof t === 'function' ? t(addr) : false;
}

module.exports = { trustProxyFrom, isTrustedProxy, normalize };
