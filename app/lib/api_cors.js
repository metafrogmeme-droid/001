'use strict';
/**
 * Cross-origin reads of the Daily Duel's API, for a front end hosted
 * somewhere else: the DogeOS "Vibe Code on DOGE" entry is a page built and
 * hosted on Noah AI, calling this server for the day's rounds, a player's
 * calls, the roots and the receipts (docs/hackathon/VIBE_CODE_ON_DOGE.md).
 * Until 21 October 2026 the server sent no Access-Control-Allow-Origin
 * header at all, so a browser blocked every such call.
 *
 * WHO. Only the origins named in API_CORS_ORIGINS (a comma list), each
 * `https://host` exactly or `https://*.host` for its subdomains. Unset, no
 * cross-origin header is ever sent.
 *
 * WHAT. Only the routes a duel front end calls (PREFIXES, held to the /doge
 * app's own calls by app/test/a_hosted_front_end_can_call_the_duel_api.test.js),
 * and only GET and POST.
 *
 * NO COOKIES. Access-Control-Allow-Credentials is never sent, so a browser
 * never attaches the site's session cookie to a cross-origin call: a hosted
 * front end signs in through the wallet routes and sends the token they
 * answer as a Bearer header, as the /doge app does.
 */

/** The API a duel front end calls: the duel, the roots and anchors, receipts, wallet sign-in. */
const PREFIXES = Object.freeze([
  '/api/duel/',
  '/api/roots',
  '/api/call/',
  '/api/auth/wallet/nonce',
  '/api/auth/wallet/verify',
]);

const ORIGIN_RE = /^https:\/\/(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$/;

/** The allowed origins, as { exact: Set, suffixes: ['.host'] }. Anything malformed is dropped. */
function allowed(env = process.env) {
  const exact = new Set();
  const suffixes = [];
  for (const raw of String(env.API_CORS_ORIGINS || '').split(',')) {
    const o = raw.trim().toLowerCase().replace(/\/+$/, '');
    if (!ORIGIN_RE.test(o)) continue;
    if (o.startsWith('https://*.')) suffixes.push(o.slice('https://*'.length));
    else exact.add(o);
  }
  return { exact, suffixes };
}

function originAllowed(origin, env) {
  const o = String(origin || '').toLowerCase();
  if (!/^https:\/\/[a-z0-9.-]+$/.test(o)) return false;
  const { exact, suffixes } = allowed(env);
  if (exact.has(o)) return true;
  const host = o.slice('https://'.length);
  return suffixes.some((s) => host.endsWith(s) && host.length > s.length);
}

const covered = (p) => PREFIXES.some((x) => p === x || p.startsWith(x.endsWith('/') ? x : x + '/') || p.startsWith(x + '?'));

/** Express middleware. `deps.env` for tests. */
function cors(deps = {}) {
  return function apiCors(req, res, next) {
    const origin = req.get('origin');
    if (!origin || !covered(req.path)) return next();
    res.vary('Origin');
    if (!originAllowed(origin, deps.env || process.env)) return next();
    res.set('Access-Control-Allow-Origin', origin);
    if (req.method === 'OPTIONS') {
      res.set('Access-Control-Allow-Methods', 'GET, POST');
      res.set('Access-Control-Allow-Headers', 'authorization, content-type');
      res.set('Access-Control-Max-Age', '600');
      return res.status(204).end();
    }
    return next();
  };
}

module.exports = { cors, PREFIXES, allowed, originAllowed, covered };
