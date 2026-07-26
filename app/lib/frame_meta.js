'use strict';
/**
 * Farcaster frame + OG meta injection for /call/:key — pure string work so
 * it is testable without a server.
 *
 * Honesty rules:
 * - Only well-formed keys get meta (charset-validated here AND resolved by
 *   the image route); a malformed key serves the untouched page.
 * - Absolute URLs require a configured public origin — without one the page
 *   is served untouched rather than shipping broken image links.
 */

const KEY_RE = /^[A-Za-z0-9:_.-]{4,128}$/;

function escAttr(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** Inject fc:frame + og:image tags for `key` into the call page HTML. */
function injectCallMeta(html, key, origin) {
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base || !KEY_RE.test(String(key || ''))) return html;
  const img = `${base}/api/frame/call/${encodeURIComponent(key)}/image`;
  const page = `${base}/call/${encodeURIComponent(key)}`;
  const tags = [
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    '<meta name="fc:frame:button:1" content="Verify in your browser">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    `<meta property="og:image" content="${escAttr(img)}">`,
    `<meta property="og:url" content="${escAttr(page)}">`,
  ].join('\n');
  return html.replace('</head>', tags + '\n</head>');
}

/** Same contract for /trader/:handle — the leaderboard record as a frame. */
function injectTraderMeta(html, handle, origin) {
  const { HANDLE_RE } = require('./arena_trader');
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base || !HANDLE_RE.test(String(handle || ''))) return html;
  const img = `${base}/api/frame/trader/${encodeURIComponent(handle)}/image`;
  const page = `${base}/trader/${encodeURIComponent(handle)}`;
  const tags = [
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    '<meta name="fc:frame:button:1" content="View the record">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    `<meta property="og:image" content="${escAttr(img)}">`,
    `<meta property="og:url" content="${escAttr(page)}">`,
  ].join('\n');
  return html.replace('</head>', tags + '\n</head>');
}

module.exports = { injectCallMeta, injectTraderMeta, KEY_RE };
