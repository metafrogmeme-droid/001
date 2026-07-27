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
  const post = `${base}/api/frame/call/${encodeURIComponent(key)}/verify`;
  const tags = [
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    `<meta name="fc:frame:post_url" content="${escAttr(post)}">`,
    '<meta name="fc:frame:button:1" content="Verify in your browser">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    '<meta name="fc:frame:button:2" content="Re-verify now">',
    '<meta name="fc:frame:button:2:action" content="post">',
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
  const post = `${base}/api/frame/trader/${encodeURIComponent(handle)}/refresh`;
  const tags = [
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    `<meta name="fc:frame:post_url" content="${escAttr(post)}">`,
    '<meta name="fc:frame:button:1" content="View the record">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    '<meta name="fc:frame:button:2" content="Refresh the record">',
    '<meta name="fc:frame:button:2:action" content="post">',
    `<meta property="og:image" content="${escAttr(img)}">`,
    `<meta property="og:url" content="${escAttr(page)}">`,
  ].join('\n');
  return html.replace('</head>', tags + '\n</head>');
}

/**
 * The frame document a POST interaction answers with — a minimal page whose
 * only job is carrying fresh meta back to the Farcaster client. `bust` is a
 * SERVER-generated cache-buster (never echoed client input); the image route
 * ignores unknown queries except the recheck marker it understands.
 */
function callVerifyFrame(key, origin, bust) {
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base || !KEY_RE.test(String(key || ''))) return null;
  const img = `${base}/api/frame/call/${encodeURIComponent(key)}/image?rechecked=${encodeURIComponent(bust)}`;
  const page = `${base}/call/${encodeURIComponent(key)}`;
  const post = `${base}/api/frame/call/${encodeURIComponent(key)}/verify`;
  return ['<!DOCTYPE html><html><head>',
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    `<meta name="fc:frame:post_url" content="${escAttr(post)}">`,
    '<meta name="fc:frame:button:1" content="Verify in your browser">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    '<meta name="fc:frame:button:2" content="Re-verify now">',
    '<meta name="fc:frame:button:2:action" content="post">',
    `</head><body>Recomputed just now. Open ${escAttr(page)} to re-derive the hash yourself.</body></html>`,
  ].join('\n');
}

function traderRefreshFrame(handle, origin, bust) {
  const { HANDLE_RE } = require('./arena_trader');
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base || !HANDLE_RE.test(String(handle || ''))) return null;
  const img = `${base}/api/frame/trader/${encodeURIComponent(handle)}/image?t=${encodeURIComponent(bust)}`;
  const page = `${base}/trader/${encodeURIComponent(handle)}`;
  const post = `${base}/api/frame/trader/${encodeURIComponent(handle)}/refresh`;
  return ['<!DOCTYPE html><html><head>',
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    `<meta name="fc:frame:post_url" content="${escAttr(post)}">`,
    '<meta name="fc:frame:button:1" content="View the record">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    '<meta name="fc:frame:button:2" content="Refresh the record">',
    '<meta name="fc:frame:button:2:action" content="post">',
    `</head><body>Refreshed. Open ${escAttr(page)} for the full record.</body></html>`,
  ].join('\n');
}

/** The arena leaderboard as a frame — no path param, just an origin. */
function boardTags(base, bust) {
  const img = `${base}/api/frame/leaderboard/image${bust ? `?t=${encodeURIComponent(bust)}` : ''}`;
  const page = `${base}/arena`;
  const post = `${base}/api/frame/leaderboard/refresh`;
  return [
    '<meta name="fc:frame" content="vNext">',
    `<meta name="fc:frame:image" content="${escAttr(img)}">`,
    `<meta name="fc:frame:post_url" content="${escAttr(post)}">`,
    '<meta name="fc:frame:button:1" content="Enter the Arena">',
    '<meta name="fc:frame:button:1:action" content="link">',
    `<meta name="fc:frame:button:1:target" content="${escAttr(page)}">`,
    '<meta name="fc:frame:button:2" content="Refresh the board">',
    '<meta name="fc:frame:button:2:action" content="post">',
    `<meta property="og:image" content="${escAttr(img)}">`,
  ];
}

function injectBoardMeta(html, origin) {
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base) return html;
  return html.replace('</head>', boardTags(base).join('\n') + '\n</head>');
}

function boardRefreshFrame(origin, bust) {
  const base = String(origin || '').trim().replace(/\/+$/, '');
  if (!base) return null;
  return ['<!DOCTYPE html><html><head>']
    .concat(boardTags(base, bust))
    .concat([`</head><body>Board refreshed. Open ${escAttr(base)}/arena for the live floor.</body></html>`])
    .join('\n');
}

module.exports = { injectCallMeta, injectTraderMeta, callVerifyFrame, traderRefreshFrame,
  injectBoardMeta, boardRefreshFrame, KEY_RE };
