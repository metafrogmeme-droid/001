'use strict';
/**
 * The landing page's venue list must be the venue list.
 *
 * Until 2026-08-17 the site said "Live · Bitget USDT-M futures" and nothing
 * else, while `app/lib/venues.js` had carried eight connectable venues and
 * `bot/core/cross_venue.py` had been reading funding from Bybit and Hyperliquid
 * alongside Bitget. The product had grown a whole dimension the front door
 * never mentioned — the opposite of the usual failure, and just as wrong: the
 * page was describing a smaller thing than the one that exists.
 *
 * TWO CLAIMS THAT MUST NOT MERGE. They are different in kind and only one of
 * them is "at once":
 *
 *   CONNECT   one venue per account. You link your own keys to one of the
 *             eight and your live account trades there. Not eight at once, and
 *             no routing between them — claiming either would be inventing a
 *             feature out of a list.
 *   READ      several venues simultaneously. cross_venue.py pulls the entire
 *             funding map from each of its venues per cache window and merges
 *             it with Bitget's, so positioning is compared rather than assumed.
 *
 * A number written into prose is the first thing to rot, so no number here is
 * typed twice: the count and every label are derived from app/lib/venues.js,
 * and the page is checked against them. Add a venue there and this fails until
 * the page and the fourteen translations catch up.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const { VENUES } = require('../lib/venues');
const index = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');

const LOCALES = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'de', 'nl', 'ja',
  'ko', 'ru', 'tr', 'ar'];

/** The venue chips the page actually ships, in document order. */
function chipsOnPage() {
  const strip = index.slice(index.indexOf('<ul class="venue-strip"'));
  const end = strip.indexOf('</ul>');
  assert.ok(end > 0, 'the venue strip has no closing </ul>');
  return [...strip.slice(0, end)
    .matchAll(/<li class="venue-chip" data-venue="([^"]+)">([^<]+)<\/li>/g)]
    .map((m) => ({ id: m[1], label: m[2] }));
}

test('every connectable venue appears on the landing page', () => {
  const chips = chipsOnPage();
  const onPage = chips.map((c) => c.id);
  const known = VENUES.map((v) => v.id);
  assert.deepStrictEqual(onPage, known,
    'the landing page and app/lib/venues.js disagree about which venues '
    + `exist.\n  page:      ${onPage.join(', ')}\n  venues.js: ${known.join(', ')}`);
});

test('each chip carries the venue\'s own label, not a paraphrase', () => {
  // "Hyperliquid (DEX)" and "KuCoin Futures" say something a trimmed name
  // does not — which product, and whether custody is involved.
  const chips = chipsOnPage();
  for (const v of VENUES) {
    const c = chips.find((x) => x.id === v.id);
    assert.ok(c, `${v.id} is missing from the page`);
    assert.strictEqual(c.label, v.label,
      `${v.id} is labelled "${c.label}" on the page and "${v.label}" in venues.js`);
  }
});

test('the venue count in the headline matches the real count', () => {
  // The English headline and the hero eyebrow both spell the number out. A
  // ninth venue must not leave "Eight venues" standing.
  const WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
    'eight', 'nine', 'ten', 'eleven', 'twelve'];
  const n = VENUES.length;
  const word = WORDS[n];
  assert.ok(word, `no word for ${n} venues — extend WORDS`);

  const h = i18n.match(/'lp\.venues_h':\s*\{\s*en:\s*"([^"]*)"/);
  assert.ok(h, 'lp.venues_h has no en string');
  assert.match(h[1].toLowerCase(), new RegExp(`\\b${word}\\b`),
    `the headline reads "${h[1]}" but there are ${n} venues`);

  const eb = i18n.match(/'hero\.eyebrow':\s*\{\s*en:\s*"([^"]*)"/);
  assert.ok(eb, 'hero.eyebrow has no en string');
  const digits = eb[1].match(/\b(\d+)\b/);
  assert.ok(digits, `the hero eyebrow "${eb[1]}" states no venue count`);
  assert.strictEqual(Number(digits[1]), n,
    `the hero eyebrow says ${digits[1]} venues; venues.js has ${n}`);
});

test('the hero no longer names a single exchange as the whole product', () => {
  // The literal defect: the first line of the page named one venue when eight
  // were connectable. Pinned by name so it cannot come back by a copy edit.
  const eb = i18n.match(/'hero\.eyebrow':\s*\{([^}]*)\}/)[1];
  assert.ok(!/Bitget USDT-M futures/.test(eb),
    'the hero eyebrow is back to naming Bitget alone');
});

test('the page separates connecting from reading', () => {
  // The honesty of this section is entirely in the split. One sentence saying
  // "connects to eight venues at once" would be a routing claim the product
  // does not support.
  const connect = i18n.match(/'lp\.venues_connect':\s*\{\s*en:\s*"([^"]*)"/);
  const read = i18n.match(/'lp\.venues_read':\s*\{\s*en:\s*"([^"]*)"/);
  assert.ok(connect && read, 'both venue notes must exist');
  assert.match(connect[1], /any one of them|the venue you connect/i,
    'the connect line must say ONE venue per account, not eight at once: '
    + `"${connect[1]}"`);
  assert.match(read[1], /at the same time|at once|across venues/i,
    `the read line must be the one claiming simultaneity: "${read[1]}"`);
  // And the strip must not promise order routing anywhere.
  const sec = index.slice(index.indexOf('id="venuesTease"'));
  const body = sec.slice(0, sec.indexOf('</section>'));
  assert.ok(!/best execution|smart order|route your order|routes orders/i.test(body),
    'the venue section implies order routing between venues, which the '
    + 'executor does not do');
});

test('every venue string is translated into all fourteen locales', () => {
  for (const key of ['lp.venues_eyebrow', 'lp.venues_h', 'lp.venues_sub',
    'lp.venues_connect', 'lp.venues_read', 'a11y.venues', 'hero.eyebrow']) {
    const m = i18n.match(new RegExp("'" + key.replace('.', '\\.') + "':\\s*\\{([^}]*)\\}"));
    assert.ok(m, 'missing i18n key ' + key);
    for (const loc of LOCALES) {
      assert.ok(new RegExp('\\b' + loc + ':').test(m[1]), `${key} missing locale ${loc}`);
    }
  }
});

test('the sweep is measuring something', () => {
  assert.ok(VENUES.length >= 2,
    `venues.js exports ${VENUES.length} venue(s) — with fewer than two the `
    + 'whole multi-venue claim is false and every test above is vacuous');
  assert.strictEqual(chipsOnPage().length, VENUES.length);
  // The chip parser is the load-bearing derivation; if the markup shape
  // changes it returns [] and three tests above pass against nothing.
  assert.ok(chipsOnPage().every((c) => c.id && c.label));
});
