/**
 * Cloudflare Worker for the Pi Testnet app.
 *
 * pi.humanoid-traders.com cannot be a second custom hostname at the host
 * (that quota is full, and a CNAME onto the host's Cloudflare account is
 * Error 1014). This Worker runs in the owner's own zone. It forwards the
 * Testnet app to https://www.humanoid-traders.com and proves the hop with
 * the secret PI_PROXY_SECRET, which the website reads as
 * PI_TESTNET_PROXY_SECRET.
 *
 * The secret is a Worker secret (`wrangler secret put PI_PROXY_SECRET`).
 * It is not in this file and not in wrangler.toml.
 *
 * Only the paths the /pi app fetches are proxied. Everything else is a
 * redirect to www for GET and HEAD, and a 404 otherwise, with no secret
 * attached and no request sent upstream.
 */

const UPSTREAM = 'https://www.humanoid-traders.com';
const PUBLIC_HOST = 'pi.humanoid-traders.com';
const PROXY_HEADER = 'x-runeclaw-pi-proxy';
const MIN_SECRET = 32;

const DROP_REQUEST_HEADERS = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length',
  'forwarded',
]);

/**
 * Paths the Testnet app actually requests: the page, Pi's validation file,
 * the Pi and arena APIs the page calls, and the static files it loads.
 * A prefix matches a path boundary, so /api/pi does not match /api/pioneer.
 */
const PROXY_EXACT = new Set([
  '/pi',
  '/validation-key.txt',
  '/api/pi',
  '/api/signals',
  '/api/roots',
  '/api/scam-or-safe',
  '/embed.css',
  '/pi_icon_200.png',
  '/pi_touch_180.png',
  '/pi_og_1200x630.jpg',
  '/api/leaderboard/opt-in',
  '/api/leaderboard/opt-out',
]);

const PROXY_PREFIXES = [
  '/pi/',
  '/api/pi/',
  '/api/arena/',
  '/api/signals/',
  '/api/market/candles/',
  '/api/duel/',
  '/api/roots/',
  '/api/call/',
  '/api/scam-or-safe/',
  '/js/',
  '/vendor/',
];

function shouldProxy(pathname) {
  if (PROXY_EXACT.has(pathname)) return true;
  return PROXY_PREFIXES.some((p) => pathname.startsWith(p));
}

function usableSecret(env) {
  const s = env && typeof env.PI_PROXY_SECRET === 'string' ? env.PI_PROXY_SECRET : '';
  return s.length >= MIN_SECRET ? s : '';
}

function plain(status, body) {
  return new Response(body, {
    status,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

function forwardHeaders(request, secret) {
  const headers = new Headers();
  for (const [key, value] of request.headers) {
    const name = key.toLowerCase();
    if (DROP_REQUEST_HEADERS.has(name)) continue;
    if (name === PROXY_HEADER) continue;
    // The upstream must not learn a client-supplied forwarded host. The
    // website treats the Testnet app from Host or from the proxy secret,
    // never from X-Forwarded-Host on its own.
    if (name === 'x-forwarded-host' || name.startsWith('x-forwarded-')) continue;
    if (name.startsWith('cf-')) continue;
    headers.append(key, value);
  }
  headers.set(PROXY_HEADER, secret);
  return headers;
}

function rewriteLocation(value) {
  const next = String(value || '');
  const pairs = [
    ['https://www.humanoid-traders.com', `https://${PUBLIC_HOST}`],
    ['http://www.humanoid-traders.com', `https://${PUBLIC_HOST}`],
    ['//www.humanoid-traders.com', `//${PUBLIC_HOST}`],
  ];
  for (const [from, to] of pairs) {
    if (next === from || next.startsWith(`${from}/`) || next.startsWith(`${from}?`)) {
      return to + next.slice(from.length);
    }
  }
  return next;
}

/** Drop Domain so the browser stores the cookie for the Testnet hostname. */
function hostOnlyCookie(cookie) {
  const parts = String(cookie).split(';');
  const kept = [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i].trim();
    if (!part) continue;
    if (i > 0 && /^domain\s*=/i.test(part)) continue;
    kept.push(part);
  }
  return kept.join('; ');
}

function rewriteResponse(upstream) {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    if (key.toLowerCase() === 'set-cookie') return;
    if (key.toLowerCase() === 'location') {
      headers.append(key, rewriteLocation(value));
      return;
    }
    headers.append(key, value);
  });
  const cookies = typeof upstream.headers.getSetCookie === 'function'
    ? upstream.headers.getSetCookie() : [];
  for (const cookie of cookies) headers.append('set-cookie', hostOnlyCookie(cookie));
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

async function handle(request, env, fetchImpl = globalThis.fetch) {
  let url;
  try { url = new URL(request.url); } catch (e) { return plain(400, 'Bad request'); }
  if (url.hostname.toLowerCase() !== PUBLIC_HOST) return plain(404, 'Not found');

  if (!shouldProxy(url.pathname)) {
    if (request.method === 'GET' || request.method === 'HEAD') {
      return new Response(null, {
        status: 302,
        headers: {
          location: `${UPSTREAM}${url.pathname}${url.search}`,
          'cache-control': 'no-store',
        },
      });
    }
    return plain(404, 'Not found');
  }

  const secret = usableSecret(env);
  // Fail closed. Proxying without the secret would make www answer the
  // Testnet hostname with the Mainnet validation key.
  if (!secret) return plain(503, 'Not available');

  const method = request.method;
  const hasBody = method !== 'GET' && method !== 'HEAD';
  const init = {
    method,
    headers: forwardHeaders(request, secret),
    redirect: 'manual',
  };
  if (hasBody) {
    init.body = request.body;
    init.duplex = 'half';
  }

  let upstream;
  try {
    upstream = await fetchImpl(`${UPSTREAM}${url.pathname}${url.search}`, init);
  } catch (e) {
    return plain(502, 'Bad gateway');
  }
  return rewriteResponse(upstream);
}

export { handle, shouldProxy, rewriteLocation, hostOnlyCookie, usableSecret, PUBLIC_HOST, UPSTREAM };

export default {
  fetch(request, env) {
    return handle(request, env, globalThis.fetch);
  },
};
