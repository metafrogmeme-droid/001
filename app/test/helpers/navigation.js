'use strict';
/**
 * Where a navigation page (lib/navigate.js) sends the browser.
 *
 * The OAuth hops answer a 200 page that navigates, not a 3xx, because the
 * live host follows redirects itself. This reads the page the way a browser
 * does, and asserts the page is that shape and nothing else: a 200, no-store,
 * no script, a meta refresh and a link that agree. Returns the target as the
 * browser would read it, entities decoded.
 */
const assert = require('node:assert');

function decode(s) {
  return s.replace(/&(amp|lt|gt|quot|#39);/g, (m, e) => ({
    amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'",
  })[e]);
}

async function navigationTarget(res) {
  assert.strictEqual(res.status, 200, `a navigation page answers 200, got ${res.status}`);
  assert.strictEqual(res.headers.get('location'), null, 'a navigation page carries no Location');
  assert.match(res.headers.get('cache-control') || '', /no-store/);
  assert.strictEqual(res.headers.get('referrer-policy'), 'no-referrer');
  const html = await res.text();
  assert.doesNotMatch(html, /<script\b/i, 'a navigation page runs no script');
  const refresh = html.match(/<meta http-equiv="refresh" content="0; url=([^"]*)">/);
  const link = html.match(/<a href="([^"]*)">Continue<\/a>/);
  assert.ok(refresh, `no meta refresh in ${html.slice(0, 200)}`);
  assert.ok(link, 'no fallback link');
  assert.strictEqual(refresh[1], link[1], 'the refresh and the link disagree');
  return decode(refresh[1]);
}

module.exports = { navigationTarget };
