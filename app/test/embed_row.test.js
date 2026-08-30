'use strict';
/**
 * The signal card's new fields, driven at the values that have historically lied.
 *
 * Age and R:R are both additions, and both are shapes CLAUDE.md's table names.
 * The table is not decoration here — an age is the field it calls out by name
 * ("an age of 0.0 rendering as '0m' (just opened) for a position of unknown
 * age"), and a ratio is the `parseFloat(x) || 0` row. So these are written the
 * way that table says to write them: plant the unreadable value, assert what
 * the card SAYS.
 *
 * The card is a pure function precisely so these cases are reachable. Inline in
 * the loader, "what does a signal with a broken timestamp look like" could only
 * be answered by waiting for one to appear in production.
 */

const test = require('node:test');
const assert = require('node:assert');
const R = require('../public/js/embed-row');

const T0 = Date.UTC(2026, 7, 24, 12, 0, 0);   // fixed "now" for every age case

// ── age: the failure the honesty table names outright ─────────────────────

test('an unreadable timestamp has no age, not a fresh one', () => {
  // Each of these rendered "0m" under a `Date.now() - (parse||0)` shape, which
  // says JUST OPENED — the most flattering possible lie on a board whose whole
  // claim is freshness.
  for (const bad of [null, undefined, '', 'not-a-date', {}, [], NaN, 'yesterday']) {
    assert.equal(R.ageLabel(bad, T0), null,
      `ageLabel(${JSON.stringify(bad)}) invented an age`);
  }
});

test('a genuinely fresh signal still says now — a real zero is not an absent one', () => {
  // The corollary that keeps the guard honest. If "unreadable" and "brand new"
  // both rendered as nothing, the fix would have deleted a true statement to
  // avoid a false one.
  assert.equal(R.ageLabel(new Date(T0 - 5000).toISOString(), T0), 'now');
  assert.equal(R.ageLabel(new Date(T0 - 59000).toISOString(), T0), 'now');
});

test('ages read in the units a trader thinks in', () => {
  const at = (ms) => R.ageLabel(new Date(T0 - ms).toISOString(), T0);
  assert.equal(at(12 * 60000), '12m');
  assert.equal(at(90 * 60000), '1h');
  assert.equal(at(30 * 3600000), '30h');
  assert.equal(at(3 * 86400000), '3d');
});

test('a timestamp from the future is not an age, but small clock skew is tolerated', () => {
  // Boxes disagree by seconds routinely; treating that as unreadable would
  // blank the age on perfectly good rows. A signal "created" an hour from now
  // is a different thing and we do not know how old it is.
  assert.equal(R.ageLabel(new Date(T0 + 10000).toISOString(), T0), 'now');
  assert.equal(R.ageLabel(new Date(T0 + 3600000).toISOString(), T0), null);
});

// ── R:R: the `|| 0` row of the table ──────────────────────────────────────

test('an unreadable R:R is absent, never 0', () => {
  // `0R` on a trading card says this trade risks everything for nothing — a
  // strong claim to manufacture out of a missing column.
  for (const bad of [null, undefined, '', 'abc', NaN, Infinity, {}]) {
    assert.equal(R.rrLabel(bad), null, `rrLabel(${JSON.stringify(bad)}) invented a ratio`);
  }
});

test('a zero or negative R:R is dropped rather than printed', () => {
  // Both are values the column can really hold, and neither describes a
  // reward-to-risk anybody can act on.
  assert.equal(R.rrLabel(0), null);
  assert.equal(R.rrLabel('0.0000'), null);
  assert.equal(R.rrLabel(-2), null);
});

test('a real R:R renders at one decimal', () => {
  assert.equal(R.rrLabel('4.1700'), '4.2R');
  assert.equal(R.rrLabel(1), '1R');
  assert.equal(R.rrLabel(2.25), '2.3R');
});

// ── symbol: one market, printed as one thing ──────────────────────────────

test('venue notation is split off the base symbol', () => {
  assert.deepEqual(R.splitSymbol('NOKSTOCK/USDT:USDT'),
    { base: 'NOKSTOCK', quote: 'USDT', perp: true });
  assert.deepEqual(R.splitSymbol('ICP/USDT'),
    { base: 'ICP', quote: 'USDT', perp: false });
});

test('an odd symbol keeps its own text rather than rendering empty', () => {
  // Dropping it would remove the one field saying WHICH MARKET the trade is in.
  // A card that cannot parse its symbol should show the odd thing it was given.
  assert.equal(R.splitSymbol('WEIRD').base, 'WEIRD');
  assert.equal(R.splitSymbol('').base, '');
  assert.equal(R.splitSymbol(null).base, '');
});

// ── the whole card, as the reader sees it ─────────────────────────────────

const GOOD = {
  symbol: 'NOKSTOCK/USDT:USDT', direction: 'SHORT', confidence: '0.6600',
  entry_price: '10.21715200', stop_loss: '10.27891800', take_profit: '9.95941200',
  rr: '4.1700', regime: 'TREND_DOWN', created_at: new Date(T0 - 12 * 60000).toISOString(),
};

test('a complete signal shows what it measured', () => {
  const html = R.rowHtml(GOOD, { nowMs: T0 });
  for (const must of ['SHORT', 'NOKSTOCK', '66%', '4.2R', '12m', 'trend down', '10.2172']) {
    assert.ok(html.includes(must), `card is missing ${must}`);
  }
  assert.ok(!html.includes('NOKSTOCK/USDT:USDT'), 'raw venue notation leaked into the card');
});

test('a signal with nothing but prices still renders, minus what it cannot say', () => {
  // The composite-view strategy: one dead field must not blank a card whose
  // prices are perfectly good.
  const html = R.rowHtml({
    symbol: 'ICP/USDT', direction: 'LONG', confidence: '0.6000',
    entry_price: '2.3680', stop_loss: '2.3439', take_profit: '2.4155',
    rr: null, regime: null, created_at: 'garbage',
  }, { nowMs: T0 });

  assert.ok(html.includes('ICP'), 'the card lost its symbol');
  assert.ok(html.includes('2.368'), 'the card lost its entry');
  assert.ok(!html.includes('0R'), 'an absent R:R rendered as zero');
  assert.ok(!/>\s*0m\s*</.test(html), 'an unreadable age rendered as just-opened');
});

test('an entirely empty signal renders em dashes, not zeros', () => {
  const html = R.rowHtml({}, { nowMs: T0 });
  assert.ok(html.includes('—'), 'absent prices did not render as em dashes');
  assert.ok(!/>\s*0(\.0+)?\s*</.test(html), 'a manufactured zero reached the card');
});

test('the chart is asked about the contract symbol and labelled with the readable one', () => {
  // NEARLY REGRESSED WHILE WRITING THIS FILE. The first draft of the extracted
  // card re-derived one symbol locally and dropped `data-sc-label` — which is
  // the defect #172 had just fixed: the fetch asked Bitget about a market that
  // does not exist, so every chart drew "no candles", a claim about the market
  // manufactured from a bad request. Two forms, two attributes, pinned.
  const html = R.rowHtml(GOOD, { nowMs: T0 });
  assert.match(html, /data-sc-sym="NOKSTOCKUSDT"/,
    'the chart fetch is not using the contract form Bitget answers to');
  assert.match(html, /data-sc-label="NOKSTOCK"/,
    'the chart lost its human-readable label');
});

test('a contract-form symbol still renders a heading', () => {
  // `BTCUSDT` has no slash. Deriving the base by splitting on one yields the
  // whole string; stripping the quote leg yields `BTC`. An empty heading would
  // be a card that cannot say which market it is about.
  const html = R.rowHtml({ symbol: 'BTCUSDT', direction: 'LONG' }, { nowMs: T0 });
  assert.match(html, /class="e-sym">BTC</);
});

// ── the cast text, which leaves our page and becomes someone's post ───────

test('the share text carries what was measured', () => {
  const t = R.shareText(GOOD, { suffix: 'https://x.test/embed/signals' });
  assert.match(t, /SHORT NOKSTOCK/);
  assert.match(t, /66% confidence/);
  assert.match(t, /4\.2R target/);
  assert.match(t, /https:\/\/x\.test\/embed\/signals/);
});

test('an absent confidence is left out of the cast, not published as 0%', () => {
  // This is the honesty rule at its most expensive: a cast is published under
  // the reader's own name, on their timeline, permanently. "LONG BTC · 0%
  // confidence" assembled from a missing column is a claim they did not make
  // and cannot retract from everyone who saw it.
  const t = R.shareText({ symbol: 'BTC/USDT', direction: 'LONG', confidence: null });
  assert.ok(!t.includes('0%'), 'an absent confidence was published as zero');
  assert.ok(!t.includes('0R'), 'an absent R:R was published as zero');
  assert.match(t, /LONG BTC/, 'the parts that WERE readable went missing too');
});

test('a signal with no direction and no symbol produces no cast at all', () => {
  // Nothing identifiable survived, so there is no signal to share. Returning a
  // string of leftover punctuation would open a composer full of nonsense over
  // somebody's timeline.
  assert.equal(R.shareText({}), null);
  assert.equal(R.shareText({ confidence: '0.9' }), null);
  assert.equal(R.shareText(null), null);
});

test('no dollar amount can reach a cast', () => {
  // §4, on the surface where it matters most: this text becomes a public post.
  const t = R.shareText(Object.assign({}, GOOD, { pnl: 4210.55, net_pnl: 900 }),
    { suffix: 'https://x.test' });
  assert.ok(!t.includes('4210'), 'a P&L amount reached a public cast');
  assert.ok(!t.includes('$'), 'a dollar sign reached a public cast');
});

test('the share button is absent by default and present only when asked for', () => {
  assert.ok(!R.rowHtml(GOOD, { nowMs: T0 }).includes('<button'));
  assert.ok(R.rowHtml(GOOD, { nowMs: T0, canShare: true }).includes('e-share'));
});

test('an unshareable signal renders its card but no share button', () => {
  // The card still has prices worth reading; only the cast is impossible.
  const html = R.rowHtml({ entry_price: '1.5' }, { nowMs: T0, canShare: true });
  assert.ok(html.includes('1.5'), 'the card lost its price');
  assert.ok(!html.includes('<button'), 'a share button was drawn for a signal with no cast text');
});

test('the cast text cannot break out of the data attribute', () => {
  // It is interpolated into `data-share-text="..."` and read back with
  // getAttribute, so a quote in a symbol would end the attribute early.
  const html = R.rowHtml({ symbol: '"><img src=x>/USDT', direction: 'LONG', confidence: '0.5' },
    { nowMs: T0, canShare: true });
  assert.ok(!html.includes('"><img'), 'the share attribute was broken out of');
});

test('only direction carries colour', () => {
  // Direction is a fact the signal asserts about itself. Whether the trade is
  // WINNING is not something this board knows, and a green card would say so.
  const html = R.rowHtml(GOOD, { nowMs: T0 });
  assert.ok(/class="e-dir e-short"/.test(html));
  assert.ok(!/e-lv[^>]*e-(up|down)/.test(html), 'a price level was given a win/lose colour');
});

test('symbol and regime cannot inject markup', () => {
  // The card is built by string concatenation and rendered into a page inside
  // someone else's document.
  //
  // THE FIRST VERSION OF THIS TEST PASSED FOR THE WRONG REASON, and a mutation
  // is what said so: deleting `esc()` from the symbol changed nothing, because
  // `displaySym` strips every non-alphanumeric before the card ever sees it —
  // `<img src=x onerror=alert(1)>` arrives as `IMGSRCXONERRORALERT1`. The test
  // was reading that upstream sanitiser and crediting the escaping.
  //
  // The one path that reaches `esc()` with raw text is the fallback for a
  // symbol displaySym cannot render at all (no alphanumerics anywhere), where
  // the card falls back to the original so it can still say what odd thing it
  // was handed. `<>/USDT` is that case, so it is in here — without it, the
  // escaping on this field is untested.
  const sanitisedUpstream = R.rowHtml({
    symbol: '<img src=x onerror=alert(1)>/USDT', direction: '<b>LONG</b>',
    regime: '"><script>alert(1)</script>',
  }, { nowMs: T0 });
  assert.ok(!sanitisedUpstream.includes('<img'), 'symbol injected a tag');
  assert.ok(!sanitisedUpstream.includes('<script'), 'regime injected a tag');
  assert.ok(!sanitisedUpstream.includes('<b>LONG'), 'direction injected a tag');

  const viaFallback = R.rowHtml({ symbol: '<>/USDT', direction: 'LONG' }, { nowMs: T0 });
  assert.ok(!/class="e-sym"><>/.test(viaFallback),
    'the unparseable-symbol fallback emitted raw markup — esc() is not doing its job');
  assert.ok(viaFallback.includes('&lt;&gt;'), 'the fallback symbol was not escaped');
});

test('no dollar amount can reach the card', () => {
  // §4: public surfaces carry percent, ratio and count. Prices are public
  // market fact and stay; an amount must not appear even if a future field
  // carries one.
  const html = R.rowHtml(Object.assign({}, GOOD, { pnl: 1234.56, net_pnl: 99 }), { nowMs: T0 });
  assert.ok(!html.includes('1234'), 'a P&L amount reached a public card');
  assert.ok(!html.includes('$'), 'a dollar sign reached a public card');
});
