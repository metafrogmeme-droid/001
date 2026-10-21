'use strict';
/**
 * Memeclaw on its own domain, served by this same server (owner, 21 October
 * 2026: "We make seperate website for it").
 *
 * MEMECLAW_HOSTS is the comma list of host names that ARE Memeclaw (for
 * example `memeclaw.example,www.memeclaw.example`). A request on one of them
 * sees Memeclaw's site and nothing of RUNECLAW's: `/` is the launchpad, and
 * only Memeclaw's own paths (its API, its scripts, its stylesheet, a launch
 * receipt, the two policies) are answered. Everything else is a plain 404,
 * so the presale bar, the Arena, the dashboard and the rest of the website
 * are not one typed path away on the launchpad's domain.
 *
 * Unset, nothing changes: /memeclaw keeps serving on the main site, where
 * the operator's tools work with the site's own session.
 */

/** The page and the paths a Memeclaw host answers. One list, read by the gate and its test. */
const ALLOWED = [
  /^\/memeclaw\/?$/,
  /^\/api\/memeclaw(\/|$)/,
  /^\/api\/call\/launch:[0-9a-f]{64}$/,
  /^\/js\/(api-retry|evm-wallet|pi-receipt|memeclaw)\.js$/,
  /^\/memeclaw\.css$/,
  /^\/favicon\.svg$/,
  /^\/og_image_1200x630\.jpg$/,
  /^\/(privacy|terms)\/?$/,
  /^\/assets\/index\.[A-Za-z0-9_-]+\.css$/,
  /^\/api\/version$/,
  /^\/(healthz|readyz)$/,
];

function hosts(env = process.env) {
  return new Set(String(env.MEMECLAW_HOSTS || '').split(',')
    .map((h) => h.trim().toLowerCase()).filter((h) => /^[a-z0-9.-]+$/.test(h)));
}

function isMemeclawHost(req, env) {
  const set = hosts(env);
  if (!set.size) return false;
  return set.has(String(req.hostname || '').toLowerCase());
}

/** The address a link preview names: the domain when one is set, else the main site's page. */
function publicUrl(env = process.env) {
  const raw = String(env.MEMECLAW_PUBLIC_URL || '').trim().replace(/\/+$/, '');
  return /^https:\/\/[a-z0-9.-]+$/i.test(raw) ? raw : 'https://www.humanoid-traders.com/memeclaw';
}

// The launchpad's stylesheet at the launchpad's version (memeclaw_page.js
// STYLES; a test holds the two equal, since that module requires this one).
const NOT_HERE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Not on Memeclaw</title>
<link rel="stylesheet" href="/memeclaw.css?v=1"></head><body class="mc">
<main id="mc-root"><div class="mc-card"><p>There is no such page on Memeclaw.</p><p><a href="/">Memeclaw</a></p></div></main>
</body></html>`;

/** Express middleware. `deps.env` for tests. */
function gate(deps = {}) {
  return function memeclawHostGate(req, res, next) {
    const env = deps.env || process.env;
    if (!isMemeclawHost(req, env)) return next();
    if (req.method !== 'GET' && req.method !== 'HEAD' && !/^\/api\/memeclaw(\/|$)/.test(req.path)) {
      return res.status(404).json({ error: 'Not found' });
    }
    if (req.path === '/') {
      // The launchpad is the domain's home: the same page /memeclaw serves.
      req.url = '/memeclaw' + req.url.slice(1);
      return next();
    }
    if (ALLOWED.some((re) => re.test(req.path))) return next();
    res.status(404).type('html').set('Cache-Control', 'no-cache').send(NOT_HERE);
    return undefined;
  };
}

module.exports = { gate, hosts, isMemeclawHost, publicUrl, ALLOWED, _notHere: NOT_HERE };
