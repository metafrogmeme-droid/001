'use strict';
/**
 * Bluesky joins the receipt-first share row on /call and /trader.
 * Pins:
 * - both pages ship the button AND its href builder (a button without a
 *   builder is a dead link);
 * - the builder rides in the same after-i18n script as the other networks,
 *   composing the SAME translated no-numbers share text — Bluesky's intent
 *   takes a single text param, so the receipt URL rides inside it;
 * - brand.bs is a brand name: identical in every one of the 14 languages
 *   (the same rule brand.tg/x/wc follow, which keeps the static-English
 *   sweep honest).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');

test('both receipt pages ship the button and its builder together', () => {
  for (const page of ['call.html', 'trader.html']) {
    const html = read(page);
    assert.match(html, /id="shBs"[^>]*data-i18n="brand\.bs"/, `${page} has the button`);
    assert.match(html, /getElementById\('shBs'\)\.href = 'https:\/\/bsky\.app\/intent\/compose\?text=' \+ t \+ '%0A' \+ u;/,
      `${page} builds the intent from the same translated text + receipt URL`);
    // same script block as the other networks — after i18n by construction
    const builder = html.indexOf("getElementById('shBs')");
    const i18nTag = html.indexOf('/js/i18n-core.js?v=');
    assert.ok(i18nTag > 0 && builder > i18nTag, `${page}: builder runs after i18n loads`);
  }
});

test('brand.bs is identical in all 14 languages', () => {
  const i18n = read('js', 'i18n.js');
  const line = i18n.split('\n').find((l) => l.includes("'brand.bs':"));
  assert.ok(line, 'brand.bs exists');
  const values = [...line.matchAll(/\b\w{2}: '([^']+)'/g)].map((m) => m[1]);
  assert.strictEqual(values.length, 14, 'all 14 locales present');
  assert.ok(values.every((v) => v === 'Bluesky'), 'a brand name never translates');
});
