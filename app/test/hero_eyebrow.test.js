'use strict';
/**
 * The first line a visitor reads named one exchange.
 *
 * `index.html` carries a whole section about multi-venue support, with a
 * comment that is careful to keep two claims apart — you can CONNECT any one of
 * eight, and the SCAN reads several at once. Then the hero strip did this:
 *
 *     eb.removeAttribute('data-i18n');   // honest mode beats the static copy
 *     eb.textContent = (d.mode === 'LIVE' ? 'Live' : 'Paper mode')
 *       + ' · Bitget USDT-M futures · every trade recorded';
 *
 * It STRIPPED the translated copy and overwrote it with a hardcoded single
 * exchange. So the page contradicted itself above the fold, and to anyone who
 * read no further the product was a Bitget bot.
 *
 * The payload already knew better: `/api/public/track-record` has returned
 * `venue` since it was written, and the page ignored it in favour of retyping
 * the same string. It now carries `venues_connectable` too, counted from
 * `app/lib/venues.js` — the list the credential route already validates
 * against — because a number written into prose is the part that rots first.
 *
 * WHAT MUST NOT HAPPEN HERE is an overclaim in the other direction. `venue` is
 * where THIS RECORD was traded and it really is Bitget; the strip decorates
 * those trades, so broadening it would misdescribe them. And connecting is one
 * venue per account — "8 venues connectable" is not "we route across eight".
 *
 * Tested by extraction rather than by reading the source, because a scan cannot
 * tell a string that is printed from one that is merely present — which is the
 * exact defect above.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const INDEX = path.join(__dirname, '..', 'public', 'index.html');
const { VENUES } = require('../lib/venues');

/** The `heroEyebrow` function as it exists in the shipped page. */
function shipped() {
  const html = fs.readFileSync(INDEX, 'utf8');
  const start = html.indexOf('function heroEyebrow(d) {');
  assert.ok(start > 0, 'heroEyebrow is gone from index.html');
  // Brace-matched to its own close, so no comment or heading bounds this.
  let depth = 0;
  let end = -1;
  for (let i = html.indexOf('{', start); i < html.length; i += 1) {
    if (html[i] === '{') depth += 1;
    else if (html[i] === '}') { depth -= 1; if (depth === 0) { end = i + 1; break; } }
  }
  assert.ok(end > start, 'could not find the end of heroEyebrow');
  const ctx = { isFinite };
  vm.createContext(ctx);
  vm.runInContext(`${html.slice(start, end)}; this.heroEyebrow = heroEyebrow;`, ctx);
  return ctx.heroEyebrow;
}

const FULL = {
  mode: 'LIVE',
  venue: 'Bitget USDT-M perpetuals',
  venues_connectable: 8,
};

test('the strip no longer describes the product as one exchange', () => {
  const out = shipped()(FULL);
  assert.match(out, /venues connectable/,
    `"${out}" still names an exchange and nothing else — the page contradicts `
    + 'its own multi-venue section above the fold');
});

test('it still says which venue the RECORD is from', () => {
  // The opposite failure. These are the operator's real trades on a real
  // exchange; a strip that dropped the venue to look broader would misdescribe
  // the numbers standing right beside it.
  assert.match(shipped()(FULL), /Bitget USDT-M perpetuals/);
});

test('the mode is still the first thing said', () => {
  assert.ok(shipped()(FULL).startsWith('Live on '));
  assert.ok(shipped()({ ...FULL, mode: 'PAPER' }).startsWith('Paper mode on '));
  // With no venue to attach to, the mode stands alone.
  assert.ok(shipped()({ mode: 'LIVE' }).startsWith('Live · '));
});

test('a missing count is omitted, not printed as zero', () => {
  // `absent is never a measurement`, on a marketing line: an older server, a
  // cached payload or a partial response must not make the page announce that
  // no venues can be connected.
  const out = shipped()({ mode: 'LIVE', venue: 'Bitget USDT-M perpetuals' });
  assert.doesNotMatch(out, /venues connectable/);
  assert.doesNotMatch(out, /\b0\b/);
  assert.equal(out, 'Live on Bitget USDT-M perpetuals · every trade recorded');
});

test('a nonsense count is refused rather than rendered', () => {
  for (const bad of [null, undefined, 0, 1, -3, NaN, '8', {}]) {
    const out = shipped()({ ...FULL, venues_connectable: bad });
    assert.doesNotMatch(out, /venues connectable/,
      `venues_connectable=${JSON.stringify(bad)} produced "${out}"`);
  }
});

test('a missing venue drops that segment and keeps the rest', () => {
  const out = shipped()({ mode: 'LIVE', venues_connectable: 8 });
  assert.equal(out, 'Live · 8 venues connectable · every trade recorded');
});

test('every trade recorded survives — it is the claim the page is built on', () => {
  for (const d of [FULL, { mode: 'PAPER' }, { mode: 'LIVE', venue: 'x' }]) {
    assert.match(shipped()(d), /every trade recorded$/);
  }
});

test('the count the server sends is the real venue list, not a typed number', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'track.js'), 'utf8');
  assert.match(src, /venues_connectable:\s*VENUES\.length/,
    'the payload types a venue count instead of counting the list — the number '
    + 'and the list will disagree the first time a venue is added');
  assert.ok(VENUES.length > 1,
    'app/lib/venues.js lists one venue or fewer, so the strip should not claim '
    + 'breadth at all');
});
