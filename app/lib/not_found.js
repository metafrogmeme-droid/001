'use strict';
/**
 * The terminal 404 — what a visitor or an API client gets for a path we do not
 * serve.
 *
 * THE FINDING. Express had no catch-all, so anything unmatched fell through to
 * the hosting layer and was answered by `pmvc58g2.mule.page` with a 12,628-byte
 * branded page carrying its own promo bar. Two separate problems:
 *
 *   - `GET /api/market/overview` -> `content-type: text/html`. A JSON client
 *     asked for JSON, got twelve kilobytes of somebody else's HTML, and had to
 *     guess. That is a content-type lie, and the parse failure it causes is
 *     less informative than the 404 it replaced.
 *   - A mistyped page URL showed a third-party host's branding on our domain.
 *
 * The route-level 404s were always fine and stay untouched — `/api/public/agent`
 * answers `{"error":"unknown_agent"}` for an address it does not know, which is
 * a real measurement of absence. This module only covers paths that reach no
 * handler at all.
 *
 * WHY A MODULE AND NOT FOUR LINES IN `server.js`. The handler must be the LAST
 * middleware; registered before the routes it would swallow the entire site,
 * and that failure is total, silent in review, and obvious only in production.
 * A pure renderer can be tested without booting the app, and the placement can
 * be asserted separately — see `app/test/not_found.test.js`.
 *
 * THE PATH IS NEVER ECHOED BACK. Reflecting it would put attacker-controlled
 * bytes into an HTML response for a URL anyone can craft and send to anyone
 * else. It buys nothing a server log does not already have, and CSP is a
 * backstop rather than a licence to skip escaping.
 */

/** True when the caller wants JSON: an API path, or an explicit Accept. */
function wantsJson(pathname, accept) {
  if (String(pathname || '').startsWith('/api/')) return true;
  const a = String(accept || '').toLowerCase();
  // `*/*` is what a browser sends after its html preference and what curl sends
  // by default — it is not a request FOR json, and treating it as one would
  // hand a bare `curl https://host/typo` a JSON body instead of the page.
  return a.includes('application/json') && !a.includes('text/html');
}

const JSON_BODY = { error: 'not_found' };

/**
 * The HTML body. Static: no interpolation, so there is nothing to escape and
 * no way for a crafted URL to reach the output. No inline <script> either —
 * `script-src` is hash-based with no 'unsafe-inline', so an inline block would
 * simply not execute, and a page whose script silently does not run is the
 * shape this repo spends its guard tests preventing.
 */
const HTML_BODY = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Not found — RUNECLAW</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/styles.css?v=38">
</head>
<body>
<main style="max-width:42rem;margin:18vh auto;padding:0 1.5rem;text-align:center">
  <p style="font-size:.8rem;letter-spacing:.18em;opacity:.55;margin:0 0 .75rem">RUNECLAW</p>
  <h1 style="font-family:var(--font-display);letter-spacing:.03em;margin:0 0 1rem">404</h1>
  <p style="opacity:.8;margin:0 0 2rem">
    There is no page at this address. It may have moved, or the link may be wrong.
  </p>
  <p><a href="/">Back to the home page</a> &nbsp;·&nbsp; <a href="/explore">Explore everything</a></p>
</main>
</body>
</html>
`;

/**
 * `{ status, type, body }` for an unmatched request. Pure — no req, no res, so
 * a test can drive every branch without a server.
 */
function notFound(pathname, accept) {
  return wantsJson(pathname, accept)
    ? { status: 404, type: 'json', body: JSON_BODY }
    : { status: 404, type: 'html', body: HTML_BODY };
}

/** The Express handler. Deliberately thin: all decisions live in `notFound`. */
function handler(req, res) {
  const out = notFound(req.path, req.get && req.get('accept'));
  res.status(out.status);
  if (out.type === 'json') return res.json(out.body);
  return res.type('html').send(out.body);
}

module.exports = { notFound, wantsJson, handler, JSON_BODY, HTML_BODY };
