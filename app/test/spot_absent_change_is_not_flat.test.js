'use strict';
/**
 * The spot market center kept the shape tickers.js was cured of.
 *
 * `app/test/absent_24h_change_is_not_a_flat_market.test.js` fixed exactly this
 * expression in `lib/tickers.js`:
 *
 *     change: (parseFloat(t.change24h) || 0) * 100,
 *
 * `lib/spot.js` carried it THREE more times — once per venue — and was not
 * swept. CLAUDE.md's corollary, unheeded: ask which other surface makes the
 * same claim before calling the fix done.
 *
 * What it cost, and the renderers make it precise. `strengthmap.js` defines:
 *
 *     pct(v)        null / non-finite -> '—'
 *     moveClass(v)  null / non-finite -> ''   (no colour class)
 *
 * both already correct, both carrying comments explaining why — "Colour is a
 * claim, so an unreadable value gets no colour class at all". They never saw
 * a null. `NaN || 0` is 0, `pct(0)` is "+0.00%" and `moveClass(0)` is 'up'.
 * An unfetchable 24h move rendered as a GREEN +0.00%: the fix was made at the
 * display layer and defeated one layer upstream.
 *
 * Two more, both load-bearing:
 *
 *   THE SUM. `volume_usdt` is summed ACROSS venues and the payload advertises
 *   "ranked by 24h quote volume (real traded volume)". A venue that reported
 *   no volume contributed a 0 to that total, so the rank was computed from a
 *   partial sum printed as the whole.
 *
 *   THE PRIMARY VENUE. The highest-volume venue becomes the quoted price. A
 *   coerced 0 silently lost that contest; a raw null would be worse, because
 *   `null - 5` is NaN and a NaN comparator leaves the order undefined.
 *
 * THE RED HERRING, planted below: a pair that genuinely did not move. 0.00% is
 * a measurement, keeps its colour and keeps its place. A fix that muted every
 * zero would be the mirror defect.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
const test = require('node:test');
const assert = require('node:assert');
const spot = require('../lib/spot');

// The renderers, copied verbatim from app/public/js/strengthmap.js, so this
// asserts what the PAGE shows rather than what the payload holds.
const pct = (v) => {
  if (v == null || (typeof v === 'string' && v.trim() === '')) return '—';
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
};
const moveClass = (v) => {
  if (v == null || (typeof v === 'string' && v.trim() === '')) return '';
  const n = Number(v);
  if (!isFinite(n)) return '';
  return n >= 0 ? 'up' : 'down';
};

const RAW = { data: [
  // readable, up
  { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.012', usdtVolume: '2000000000', high24h: '101000', low24h: '98000' },
  // THE RED HERRING: genuinely flat. A real, measured zero.
  { symbol: 'XRPUSDT', lastPr: '2', change24h: '0', usdtVolume: '50000000', high24h: '2.1', low24h: '1.9' },
  // the venue said nothing about the move
  { symbol: 'ETHUSDT', lastPr: '4000', change24h: null, usdtVolume: '900000000' },
  // the venue said nothing about the volume
  { symbol: 'SOLUSDT', lastPr: '150', change24h: '-0.02', usdtVolume: null },
] };

function bySymbol(m) {
  return Object.fromEntries(m.pairs.map(p => [p.symbol, p]));
}

test('an unreadable 24h move is not a flat market', async () => {
  spot.setSpotFetcher(async () => RAW);
  const p = bySymbol(await spot.getSpotMarket());

  assert.strictEqual(p.ETHUSDT.change_pct, null,
    'an absent change24h became a number');
  assert.strictEqual(pct(p.ETHUSDT.change_pct), '—');
  assert.strictEqual(moveClass(p.ETHUSDT.change_pct), '',
    'an unreadable move was given a colour — 0 >= 0 is the green branch');
});

test('a genuinely flat market keeps its zero, its colour and its place', async () => {
  spot.setSpotFetcher(async () => RAW);
  const p = bySymbol(await spot.getSpotMarket());

  assert.strictEqual(p.XRPUSDT.change_pct, 0, 'a measured zero was muted');
  assert.strictEqual(pct(p.XRPUSDT.change_pct), '+0.00%');
  assert.strictEqual(moveClass(p.XRPUSDT.change_pct), 'up',
    'a real flat reading lost the colour it earned');
});

test('readable moves are untouched', async () => {
  spot.setSpotFetcher(async () => RAW);
  const p = bySymbol(await spot.getSpotMarket());
  assert.strictEqual(p.BTCUSDT.change_pct, 1.2);
  assert.strictEqual(moveClass(p.BTCUSDT.change_pct), 'up');
  assert.strictEqual(p.SOLUSDT.change_pct, -2);
  assert.strictEqual(moveClass(p.SOLUSDT.change_pct), 'down');
});

test('an unreadable volume is not zero volume', async () => {
  spot.setSpotFetcher(async () => RAW);
  const p = bySymbol(await spot.getSpotMarket());
  assert.strictEqual(p.SOLUSDT.volume_usdt, null,
    'a venue that reported no volume was recorded as having traded nothing');
});

test('a pair that cannot be ranked sorts last rather than anywhere', async () => {
  spot.setSpotFetcher(async () => RAW);
  const m = await spot.getSpotMarket();
  const order = m.pairs.map(x => x.symbol);
  assert.strictEqual(order[order.length - 1], 'SOLUSDT',
    `unrankable pair did not sort last: ${order.join(', ')}`);
  // and the readable ones are still in descending volume order
  const vols = m.pairs.map(x => x.volume_usdt).filter(v => v !== null);
  assert.deepStrictEqual(vols, [...vols].sort((a, b) => b - a),
    'readable volumes are no longer volume-ranked');
});

test('a cross-venue total built from some venues says so', async () => {
  // Bitget reports a volume for BTC; Bybit does not.
  spot.setSpotFetcher(async () => ({ data: [
    { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.01', usdtVolume: '1000', high24h: '1', low24h: '1' },
  ] }), 'bitget');
  spot.setSpotFetcher(async () => ({ result: { list: [
    { symbol: 'BTCUSDT', lastPrice: '100010', price24hPcnt: '0.01', turnover24h: null },
  ] } }), 'bybit');

  const p = bySymbol(await spot.getSpotMarket());
  assert.strictEqual(p.BTCUSDT.volume_usdt, 1000,
    'the unreadable venue contributed a 0 to the total');
  assert.strictEqual(p.BTCUSDT.volume_is_partial, true,
    'a partial total was presented as the whole');
  assert.strictEqual(p.BTCUSDT.volume_venues_unreadable, 1);
  assert.strictEqual(p.BTCUSDT.venue, 'bitget',
    'the venue with a readable volume must win the primary contest');
  spot.setSpotFetcher(null);
});

test('the payload does not advertise a ranking it cannot support', async () => {
  spot.setSpotFetcher(async () => RAW);
  const m = await spot.getSpotMarket();
  assert.match(m.ranked_by, /lower bound|could not be ranked/,
    'ranked_by still claims a clean volume ranking');
  spot.setSpotFetcher(null);
});

test('the chat card does not print a move it could not read', async () => {
  // `p.change_pct >= 0` is TRUE for null, so this line printed "+0%" while the
  // parsers invented the zero and would have printed "+null%" once they
  // stopped. The consumer needed the same guard as the producer.
  spot.setSpotFetcher(async () => ({ data: [
    { symbol: 'ETHUSDT', lastPr: '4000', change24h: null, usdtVolume: '900000000' },
    { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.012', usdtVolume: '2000000000' },
  ] }));
  const out = await spot.maybeHandleSpotChat('u', 'spot market');
  assert.match(out.reply_html, /<b>ETH<\/b> \$4,000 \(—\)/,
    `unreadable move rendered as a number: ${out.reply_html}`);
  assert.doesNotMatch(out.reply_html, /\+0%|null%/);
  // the readable one still reads normally
  assert.match(out.reply_html, /<b>BTC<\/b> \$100,000 \(\+1\.2%\)/);
  spot.setSpotFetcher(null);
});
