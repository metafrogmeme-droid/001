'use strict';
/**
 * The RWA sector headline covers what REPORTED a reading, and it is the same
 * aggregate the per-category rows already used.
 *
 * `buildRadar` computed each figure twice: cured inside the category loop,
 * with three paragraphs of comment about why, and left as the uncured
 * original in the sector rollup forty lines below. `null * volume` is 0 in
 * JS, so an unreadable row added nothing to the numerator while its volume
 * stayed in the denominator — every such row DILUTED the headline toward
 * zero, always flatteringly. Driven on the same three tokens with one
 * unreadable change the two answered -5.94% and -7.04%, and ONE CARD PRINTS
 * BOTH, three lines apart.
 *
 * The existing `rwa.test.js` passed throughout, for a reason worth naming:
 * its fixture gives every token a readable change, and a fixture where every
 * row is readable cannot tell a filtered aggregate from an unfiltered one.
 * Every table here has at least one unreadable row.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const rwa = require('../lib/rwa');
const { codeOnly } = require('./helpers/code_only');

const P = rwa.RWA_UNIVERSE[0].bases;                 // platforms: ONDO POLYX OM RSR ...
const tick = (price, change, volume) => ({ price, change, volume });

/** A ticker map from [base, price, change, volume] rows. */
function mk(rows) {
  const m = {};
  for (const [b, p, c, v] of rows) m[`${b}USDT`] = tick(p, c, v);
  return m;
}

// ── the aggregate ───────────────────────────────────────────────────────────

test('the sector change is the per-category change when one category is listed', () => {
  // The defect: -5.94 (unfiltered, diluted) vs -7.04 (filtered). Same rows.
  const r = rwa.buildRadar(mk([
    [P[0], 0.92, -8.0, 40e6],
    [P[2], 1.85, -5.5, 25e6],
    [P[3], 0.011, null, 12e6],       // change unreadable, volume readable
  ]));
  const cat = r.categories.find((c) => c.listed);
  assert.equal(r.sector.change_24h_pct, cat.change_24h_pct);
  // and it is the honest weighted mean over the two that reported one
  const expect = ((-8.0 * 40e6) + (-5.5 * 25e6)) / 65e6;
  assert.equal(r.sector.change_24h_pct, Math.round(expect * 100) / 100);
  assert.equal(r.sector.change_scored, 2);
  assert.equal(r.sector.listed, 3);
});

test('not one readable change is null, never a measured flat sector', () => {
  const r = rwa.buildRadar(mk([
    [P[0], 0.92, null, 40e6],
    [P[2], 1.85, null, 25e6],
  ]));
  assert.equal(r.sector.change_24h_pct, null);
  assert.equal(r.sector.change_scored, 0);
  for (const c of r.categories.filter((x) => x.listed)) assert.equal(c.change_24h_pct, null);
});

test('an unreadable row is never the top gainer, in a market where all fell', () => {
  const r = rwa.buildRadar(mk([
    [P[0], 0.92, -8.0, 40e6],
    [P[2], 1.85, -5.5, 25e6],
    [P[3], 0.011, null, 12e6],
  ]));
  // `null` coerces to 0 in a raw subtraction, so the unreadable row used to
  // sort above every real loser and be published as the sector's best.
  assert.equal(r.sector.top_gainer.base, P[2]);
  assert.notEqual(r.sector.top_gainer.base, P[3]);
  assert.notEqual(r.sector.top_gainer.change_24h_pct, null);
});

test('an unreadable row is never the laggard, in a market where all rose', () => {
  const r = rwa.buildRadar(mk([
    [P[0], 0.92, 6.0, 40e6],
    [P[2], 1.85, 9.5, 25e6],
    [P[3], 0.011, null, 12e6],
  ]));
  assert.equal(r.sector.top_loser.base, P[0]);
  assert.notEqual(r.sector.top_loser.base, P[3]);
  assert.notEqual(r.sector.top_loser.change_24h_pct, null);
});

test('with no readable change at all there is no named extreme', () => {
  const r = rwa.buildRadar(mk([[P[0], 0.92, null, 40e6], [P[2], 1.85, null, 25e6]]));
  assert.equal(r.sector.top_gainer, null);
  assert.equal(r.sector.top_loser, null);
});

test('a volume total covers what reported one, and is null when none did', () => {
  const partial = rwa.buildRadar(mk([[P[0], 0.92, 6.0, 40e6], [P[2], 1.85, 9.5, null]]));
  assert.equal(partial.sector.volume_24h_usd, 40e6);
  assert.equal(partial.sector.volume_scored, 1);
  const none = rwa.buildRadar(mk([[P[0], 0.92, 6.0, null], [P[2], 1.85, 9.5, null]]));
  assert.equal(none.sector.volume_24h_usd, null, '$0 is a measurement');
  assert.equal(none.sector.volume_scored, 0);
});

test("BTC's unread 24h change is null, not a flat BTC", () => {
  // `round2(null)` is `Math.round(null * 100) / 100` — 0. The `vs_btc_pct`
  // guard one line above was already correct, so the two disagreed in place.
  const r = rwa.buildRadar(mk([[P[0], 0.92, 6.0, 40e6], ['BTC', 63000, null, 9e8]]));
  assert.equal(r.btc_change_24h_pct, null);
  assert.equal(r.sector.vs_btc_pct, null, 'no comparison against a BTC nobody read');
  const read = rwa.buildRadar(mk([[P[0], 0.92, 6.0, 40e6], ['BTC', 63000, 2.0, 9e8]]));
  assert.equal(read.btc_change_24h_pct, 2);
  assert.equal(read.sector.vs_btc_pct, 4);
});

// ── ONE aggregate, not two ──────────────────────────────────────────────────

test('there is ONE aggregate, defined once and called by both levels', () => {
  // This is a SCAN, and the reason is worth stating: `buildRadar` calls
  // `weightedChange` as a module-local reference, so patching the EXPORT
  // changes nothing the function does. A first draft of this test did patch
  // the export and passed — trivially, because both levels went on calling
  // the real one. A kill for a reason unrelated to the rule is how a guard
  // reports coverage it does not have, so the claim is the structural one
  // it can actually check: one definition, and no aggregate arithmetic of
  // its own inside the rollup.
  const src = codeOnly(require('node:fs').readFileSync(require.resolve('../lib/rwa'), 'utf8'));
  for (const fn of ['weightedChange', 'sumVolume', 'rankByChange']) {
    const defs = src.match(new RegExp(`function ${fn}\\s*\\(`, 'g')) || [];
    assert.equal(defs.length, 1, `${fn} must be defined exactly once, found ${defs.length}`);
  }
  // The rollup is everything from `const totalVol` to the closing return.
  const from = src.indexOf('const totalVol');
  const to = src.indexOf('let fetchTickers');
  assert.ok(from > 0 && to > from, 'rollup anchors moved');
  const rollup = src.slice(from, to);
  assert.ok(!/\.reduce\(/.test(rollup),
    'the sector rollup must not sum anything itself — that is how it came to disagree');
  assert.ok(!/change_24h_pct\s*\*/.test(rollup), 'no weighting of its own either');
  assert.ok(!/b\.change_24h_pct\s*-\s*a\.change_24h_pct/.test(rollup),
    'no raw-subtraction sort: `null` coerces to 0 and names an unreadable row');
  for (const call of ['sumVolume(all)', 'weightedChange(all)', 'rankByChange(all)']) {
    assert.ok(rollup.includes(call), `the rollup must call ${call}`);
  }
});

test('the aggregate seam itself reports its sample', () => {
  const t = [{ change_24h_pct: 2, volume_24h_usd: 100 },
             { change_24h_pct: null, volume_24h_usd: 100 },
             { change_24h_pct: 4, volume_24h_usd: null }];
  const w = rwa.weightedChange(t);
  assert.equal(w.scored, 2);
  assert.equal(w.total, 3);
  // only the row with BOTH readable can be volume-weighted; with one such row
  // the weighted mean is that row's own change.
  assert.equal(w.pct, 2);
  const v = rwa.sumVolume(t);
  assert.deepEqual([v.usd, v.scored, v.total], [200, 2, 3]);
  assert.deepEqual(rwa.rankByChange(t).map((x) => x.change_24h_pct), [4, 2]);
  assert.deepEqual(rwa.weightedChange([]), { pct: null, scored: 0, total: 0 });
  assert.deepEqual(rwa.sumVolume([]), { usd: null, scored: 0, total: 0 });
});

// ── a failed read is not a delisting ────────────────────────────────────────

test('markets_read separates a read that carried nothing from a real delisting', () => {
  const nothing = rwa.buildRadar({});
  assert.equal(nothing.markets_read, 0);
  assert.equal(nothing.sector.listed, 0);
  const others = rwa.buildRadar(mk([['DOGE', 0.1, 1.0, 1e6], ['SHIB', 0.00001, 2.0, 2e6]]));
  assert.equal(others.markets_read, 2);
  assert.equal(others.sector.listed, 0, 'none of ours listed, but the venue answered');
  assert.equal(nothing.universe, others.universe);
  assert.ok(others.universe >= 20);
});

test('a null ticker map is zero markets read, not a crash', () => {
  for (const bad of [null, undefined]) {
    const r = rwa.buildRadar(bad);
    assert.equal(r.markets_read, 0);
    assert.equal(r.sector.listed, 0);
  }
});

// ── the ONE card ────────────────────────────────────────────────────────────

async function card(map) {
  rwa.setTickerFetcher(async () => map);
  try { return (await rwa.rwaChatCard()).reply_html; } finally { rwa.setTickerFetcher(null); }
}

test('the card never prints +null%, a manufactured 0% or a $0 volume', async () => {
  const down = await card(mk([
    [P[0], 0.92, -8.0, 40e6], [P[2], 1.85, -5.5, 25e6], [P[3], 0.011, null, 12e6],
  ]));
  assert.ok(!/null/.test(down), down);
  assert.match(down, /Sector: <b>-7\.04%<\/b>/);
  assert.match(down, /2 of 3 reported one/);
  assert.match(down, /RSR —/, 'the table still lists the row it could not read');

  const flat = await card(mk([[P[0], 0.92, null, 40e6], [P[2], 1.85, null, 25e6]]));
  assert.match(flat, /Sector: <b>—<\/b>/);
  assert.ok(!/\+0%/.test(flat) && !/null/.test(flat), flat);
  // no sample caveat beside an em dash: a hedge about a figure that is absent
  assert.ok(!/reported one/.test(flat), flat);

  const noVol = await card(mk([[P[0], 0.92, 6.0, null], [P[2], 1.85, 9.5, null]]));
  assert.match(noVol, /— volume/);
  assert.ok(!/\$0 volume/.test(noVol), noVol);
});

test('the card says a read carried nothing, and never that the sector is unlisted', async () => {
  const failed = await card({});
  assert.match(failed, /no markets at all/);
  assert.match(failed, /not a report that the sector is unlisted/);
  assert.ok(!/None of the/.test(failed), failed);

  const unlisted = await card(mk([['DOGE', 0.1, 1.0, 1e6]]));
  assert.match(unlisted, /None of the \d+ tracked tokens/);
  assert.match(unlisted, /among the 1 markets read/);
});

test('the intercept is a regex test in front of the one renderer', async () => {
  rwa.setTickerFetcher(async () => mk([[P[0], 0.92, 6.0, 40e6]]));
  try {
    assert.equal(await rwa.maybeHandleRwaChat(1, 'what is the weather'), null);
    const hit = await rwa.maybeHandleRwaChat(1, 'rwa radar');
    const direct = await rwa.rwaChatCard();
    assert.deepEqual(hit, direct, 'the intercept must add nothing of its own');
    assert.ok(rwa.CHAT_RE.test('tokenized treasuries'));
  } finally { rwa.setTickerFetcher(null); }
});

test('a throwing ticker read is a refresh sentence, not a card', async () => {
  rwa.setTickerFetcher(async () => { throw new Error('tickers HTTP 503'); });
  try {
    const r = await rwa.maybeHandleRwaChat(1, 'rwa radar');
    assert.match(r.reply_html, /refreshing/);
    assert.ok(!/503/.test(r.reply_html), 'no driver text on a public surface');
  } finally { rwa.setTickerFetcher(null); }
});

// ── the 3D star map ─────────────────────────────────────────────────────────

test('the sector star map omits a row whose change it could not read', () => {
  // A SCAN, and the reason is the one `_tf_label`'s guard gives: this block
  // lives inside an 8,700-line file's async IIFE and mounts a WebGL canvas,
  // so driving it would mean standing up the renderer to read one boolean.
  // What it asserts is not a spelling but the shape: THREE of the four
  // channels this plot uses encode the 24h change — colour (`up`), height
  // (`elev`) and brightness (`intensity`) — and `Number(null) || 0` made an
  // unreadable row read as calm, flat and GREEN, under a caption whose own
  // words are "green = up".
  const src = codeOnly(require('node:fs')
    .readFileSync(require.resolve('../public/js/dashboard.js'), 'utf8'));
  const from = src.indexOf('RC3DRadar.mount');
  // FORWARD from `from`: `radar3dLegend` also names the container thousands
  // of lines earlier, so a bare indexOf put the end BEFORE the start and the
  // slice was empty — "a boundary that is whatever happens to be next".
  const to = src.indexOf('radar3dLegend', from);
  assert.ok(from > 0 && to > from, 'star-map anchors moved');
  const block = src.slice(from, to);
  assert.ok(!/Number\(t\.change_24h_pct\)\s*\|\|\s*0/.test(block),
    'an unreadable change must not be coerced to 0 and then plotted as up');
  assert.match(block, /change_24h_pct == null \? null :/);
  assert.match(block, /if \(chg == null \|\| !isFinite\(chg\)\) \{ unread \+= 1; return; \}/);
  // and the caption states the omission rather than passing a partial set
  // off as the universe — but only when something really was dropped.
  const caption = src.slice(to, src.indexOf('Visualization only', to) + 40);
  assert.match(caption, /unread \?/);
  assert.match(caption, /no readable 24h change and are not plotted/);
});
