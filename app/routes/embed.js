'use strict';
/**
 * Embeddable surfaces — the ONLY pages RUNECLAW allows inside someone else's
 * frame, and the reason the rest are not.
 *
 * `server.js` sets `frame-ancestors 'none'` and `X-Frame-Options: DENY` on
 * every response. That is correct for the dashboard: it is authenticated, it
 * carries buttons that move money, and a page that can be framed can be
 * clickjacked — an attacker overlays their own UI on an invisible iframe of
 * yours and your click lands on a control you never saw.
 *
 * A widget and a Farcaster Mini App both need the opposite, so this router is
 * the carve-out. It is scoped by construction rather than by discipline:
 *
 *   1. NOTHING here reads a cookie, a session, or an Authorization header.
 *      There is no authenticated content to clickjack because there is no
 *      authenticated content.
 *   2. NOTHING here performs an action. Every route is a GET that renders
 *      public data — the same data `/api/signals` already serves to anyone.
 *   3. The frame permission is set HERE, per response, and never widened in
 *      `server.js`. A carve-out applied globally would make the authenticated
 *      dashboard framable, which is the accident this file exists to avoid
 *      and which `embed_frame_policy.test.js` fails on.
 *
 * The embed CSP is TIGHTER than the app's everywhere except frame-ancestors:
 * `default-src 'none'` with no inline script and no inline style, because a
 * page that runs in a stranger's document should carry the least authority it
 * can, not the most convenient.
 *
 * WHO MAY FRAME IT. `EMBED_FRAME_ANCESTORS` is a space-separated allowlist;
 * unset means `*`, which is the honest default for a page that is entirely
 * public and actionless — restricting it would be security theatre while the
 * same bytes are readable by curl. Set it when a specific partner integration
 * wants the guarantee.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();

// Framed pages are fetched once per viewer load, and an embed on a busy site
// is legitimately high-volume — but it is still an unauthenticated public
// endpoint, so it is capped like the other ones.
router.use(rateLimit({ windowMs: 60000, max: 120, key: ipKey }));

/** Who is allowed to frame these pages. */
function frameAncestors() {
  const raw = String(process.env.EMBED_FRAME_ANCESTORS || '').trim();
  return raw || '*';
}

/**
 * The embed CSP. Deliberately stricter than the app's on every other axis.
 *
 * `default-src 'none'` means anything not named below is refused, so a future
 * edit that adds a fetch to a third party fails visibly in the console rather
 * than silently sending a viewer's IP somewhere new from inside a page hosted
 * on someone else's domain.
 */
function embedCsp() {
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    'frame-ancestors ' + frameAncestors(),
  ].join('; ');
}

/**
 * Replace the app-wide deny with the embed policy.
 *
 * `removeHeader` matters as much as `setHeader`: `X-Frame-Options: DENY` is
 * the legacy header, browsers that honour it ignore `frame-ancestors`
 * entirely, and leaving it set would produce a page that is framable
 * according to its CSP and blank in the browsers that read the old header —
 * a bug that only appears for some viewers.
 */
function framable(req, res, next) {
  res.removeHeader('X-Frame-Options');
  res.setHeader('Content-Security-Policy', embedCsp());
  // Never negotiate credentials from inside a frame.
  res.removeHeader('Set-Cookie');
  res.setHeader('Cache-Control', 'public, max-age=60');
  next();
}

router.use(framable);

/**
 * Every page here loads this: it is what lifts the Mini App splash and what
 * the share button talks to. A page without it hangs behind a splash screen
 * forever — see farcaster-ready.js.
 */
const COMMON_JS = ['/js/farcaster-ready.js?v=3'];

/**
 * `deps` is per-page ON PURPOSE. It used to be one hardcoded list, which was
 * right while there was one page — and the moment a second arrived it would
 * have shipped the signal-card renderer and the candlestick charter to a
 * leaderboard that draws neither. This board's whole argument for a 1KB
 * handshake shim over the 640KB official SDK was that bytes matter on a phone
 * inside a webview; sending a page code it cannot use would give that back.
 */
function page(title, bodyClass, script, meta, deps) {
  const scripts = COMMON_JS.concat(deps || [], [script])
    .map((s) => `<script src="${s}" defer></script>`).join('\n');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title>
${meta || ''}
<link rel="stylesheet" href="/embed.css?v=5">
</head><body class="${bodyClass}">
<div id="root" aria-live="polite"><div class="e-load">Loading…</div></div>
${scripts}
</body></html>`;
}

/**
 * GET /embed/signals — the live signal board, framable.
 *
 * Public data only: `/api/signals` is already served without auth, and the
 * chart is drawn client-side from `/api/market/candles`, which is likewise
 * public market fact. No dollar amounts appear on it — the public-surface rule
 * in CLAUDE.md — because signals carry percent, ratio and price levels, never
 * an account's money.
 */
router.get('/signals', (req, res) => {
  // The fc:miniapp tags make a link to this page render as a launchable card in
  // a cast. They are emitted only when a public origin resolves: absolute URLs
  // are mandatory here, and the wallet-QR incident is the precedent — a card
  // pointing at an internal hostname renders perfectly and is unreachable by
  // everyone who taps it.
  let meta = '';
  try {
    // `.origin`, not the object: resolve() answers {origin} or {error}, and
    // stringifying it yields "[object Object]" in every URL of a card that
    // still renders.
    const r = require('../lib/public_origin').resolve(req, process.env) || {};
    meta = r.origin ? require('../lib/farcaster_manifest').embedTags(r.origin) : '';
  } catch (_) { meta = ''; }
  res.type('html').send(
    page('RUNECLAW — live signals', 'e-signals', '/js/embed-signals.js?v=7', meta,
      ['/js/embed-read.js?v=2', '/js/embed-row.js?v=2', '/js/signal-chart.js?v=1']));
});

/**
 * GET /embed/arena — the paper-trading competition board, framable.
 *
 * Public data only, on the same terms as /signals: `/api/arena/season`,
 * `/leaderboard` and `/tape` are already served without auth, and §4 is
 * satisfied at the source — the arena publishes opt-in handles and percent
 * return against a uniform virtual stake, never a balance. Nothing here can
 * open a trade; joining happens on /arena behind a login.
 *
 * ITS OWN CARD, THE SAME APP. The manifest's `homeUrl` stays /embed/signals —
 * that is the app's identity in the directory and there is one of it. But
 * `embedTags` takes a url and a button title, so a link to THIS page renders
 * as its own launchable card. Two entry points, one Mini App, no second
 * domain — which is what the alternative would have cost.
 */
router.get('/arena', (req, res) => {
  let meta = '';
  try {
    const r = require('../lib/public_origin').resolve(req, process.env) || {};
    meta = r.origin
      ? require('../lib/farcaster_manifest').embedTags(r.origin, {
        url: `${r.origin}/embed/arena`,
        buttonTitle: 'Open the arena',
      })
      : '';
  } catch (_) { meta = ''; }
  res.type('html').send(
    page('RUNECLAW — the arena', 'e-arena', '/js/embed-arena.js?v=1', meta,
      ['/js/embed-arena-view.js?v=1']));
});

module.exports = router;
module.exports.embedCsp = embedCsp;
module.exports.frameAncestors = frameAncestors;
