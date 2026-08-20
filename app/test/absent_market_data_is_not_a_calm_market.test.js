'use strict';
/**
 * Unreadable market data used to become a green LONG and a quiet market.
 *
 * `app/lib/strengthmap.js` turned any unreadable exchange field into 0:
 *
 *     function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
 *
 * `float(x or 0)`, applied to raw exchange JSON nobody controls. A ticker
 * missing fundingRate / holdingAmount / high24h / low24h scored, verifiably:
 *
 *     dir 0 · long_score 50.0 · funding 0 · oi_usd 0 · chg 0 · every factor 0
 *
 * and `0 >= 0` is true, so that coin rendered green, labelled "▲ Long", with a
 * `&dir=LONG` link that pre-fills a trade ticket. Five confident measurements,
 * none of them measured.
 *
 * THEN IT KEPT GOING. `sentinel.js` reads the same coins to publish a systemic
 * RISK score, and every dilution ran the same way — toward calm:
 *
 *   · a manufactured funding 0, OI-weighted, pulled the market-wide average
 *     toward neutral, so the crowded-book flag stopped firing
 *   · dir 0 read as an exact 50/50 lean, flattening the bias flag
 *   · an unreadable 24h change voted neither up nor down while still inflating
 *     the breadth denominator, diluting herding
 *   · with no ΔOI baseline — EVERY cold start — `surging` was empty and the
 *     leverage component, 22% of the score, was a measured zero
 *
 * and with four flags silent the last line published "No systemic crowding
 * stands out — positioning is broad and funding is near neutral."
 *
 * `sentinel_live.js` already refused to render an empty feed as a calm market,
 * and `sentinel_live_shared.test.js` pins that as "an error, never an empty
 * all-clear". The control was right and covered the case that does not happen.
 * Tickers arriving with unreadable FIELDS is the case that does.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { scoreTicker, buildStrengthMap, WEIGHTS } = require('../lib/strengthmap');
const { buildSentinel } = require('../lib/sentinel');

const AT = 1755000000000;

/** A complete Bitget ticker; override any field with undefined to remove it. */
function tk(o) {
  return Object.assign({
    symbol: 'AAAUSDT', lastPr: '100', change24h: '0.03', high24h: '110',
    low24h: '90', fundingRate: '0.0002', holdingAmount: '1000',
    usdtVolume: '1000000',
  }, o);
}

/** A 40-coin universe of already-scored coins, as the sentinel receives them. */
function universe(over) {
  const coins = [];
  for (let i = 0; i < 40; i++) {
    coins.push(Object.assign({
      base: 'C' + i, symbol: 'C' + i + 'USDT',
      funding: 0, oi_usd: 1e8 * (40 - i), doi_pct: 0, dir: 0,
      change_pct: (i % 2 ? 1 : -1),
    }, typeof over === 'function' ? over(i) : over));
  }
  return coins;
}

const kinds = (s) => s.flags.map((f) => f.kind);


// ── 1. the scorer: absence stays absent ─────────────────────────────────────

test('a ticker with unreadable fields scores NOTHING, not zero', () => {
  const s = scoreTicker({ symbol: 'NEWUSDT', lastPr: '1.5', usdtVolume: '1e6' });
  assert.equal(s.dir, null, 'an unscorable coin published a direction');
  assert.equal(s.long_score, null, '50.0 reads as a measured dead heat');
  assert.equal(s.short_score, null);
  assert.equal(s.funding, null);
  assert.equal(s.oi_usd, null);
  assert.equal(s.change_pct, null);
  for (const [k, v] of Object.entries(s.factors)) {
    assert.equal(v, null, `factor ${k} was invented`);
  }
});

test('the empty string is absence, not zero — Number("") is 0 AND finite', () => {
  const s = scoreTicker(tk({ fundingRate: '', change24h: '   ' }));
  assert.equal(s.funding, null);
  assert.equal(s.change_pct, null);
});

// Absence arrives in more shapes than `undefined`. An exchange under load
// answers with placeholders, and every one of these is finite-or-not in a
// different way — which is exactly how one of them slips past a guard written
// for the shape someone happened to think of.
for (const junk of ['abc', 'N/A', '—', 'null', 'NaN', Infinity, NaN, {}, []]) {
  test(`a ${JSON.stringify(junk)} funding rate is absence, not a rate`, () => {
    assert.equal(scoreTicker(tk({ fundingRate: junk })).funding, null);
  });
}

test('one dead field does not blank the coin — the readable rest survives', () => {
  const s = scoreTicker(tk({ fundingRate: undefined }));
  assert.equal(s.funding, null);
  assert.equal(s.change_pct, 3, 'a readable 24h move was discarded with it');
  assert.equal(s.oi_usd, 100000, 'a readable open interest was discarded with it');
  assert.ok(s.factors.momentum > 0, 'a computable factor was discarded with it');
});

test('a missing CONTRARIAN factor refuses a direction rather than renormalising', () => {
  // funding is the only contrarian input and the one most often absent (a new
  // listing has no funding window yet). Zeroing it biases long; renormalising
  // over the survivors — all trend-following — biases long HARDER.
  const s = scoreTicker(tk({ fundingRate: undefined }));
  assert.equal(s.dir, null, 'a direction was claimed without the only factor that could argue against it');
  assert.ok(s.dir_coverage > 0 && s.dir_coverage < 1, `coverage ${s.dir_coverage} should be partial`);
});

test('a cold start still scores — only ΔOI is missing, and that is 0.14 of the weight', () => {
  const s = scoreTicker(tk(), null);       // no previous poll
  assert.equal(s.doi_pct, null);
  assert.ok(s.dir !== null, 'the common case must keep working, or the guard gets removed');
  assert.equal(s.dir_coverage, 1 - WEIGHTS.doi);
});

test('at full coverage the renormalisation is a no-op, to the bit', () => {
  // `dir` is now `Σ w·f / Σ w` over the readable factors. That equals the old
  // plain `Σ w·f` ONLY because the weights sum to exactly 1 — which is a
  // separate test's invariant, and is now load-bearing here. If someone
  // retunes the blend to sum to 0.9, every fully-readable score silently
  // rescales by 1/0.9 and nothing else would notice.
  const s = scoreTicker(tk(), 4.9e7);         // a prior snapshot: all 5 factors read
  assert.equal(s.dir_coverage, 1, 'setup: this fixture must read every factor');
  const plain = Object.entries(WEIGHTS)
    .reduce((acc, [k, w]) => acc + w * s.factors[k], 0);
  // s.factors are rounded to 3dp, so compare at that resolution, not exactly.
  assert.ok(Math.abs(s.dir - plain) < 2e-3,
    `dir ${s.dir} drifted from the unnormalised blend ${plain}`);
  assert.ok(Math.abs(Object.values(WEIGHTS).reduce((a, b) => a + b, 0) - 1) < 1e-12,
    'the weights no longer sum to 1, so renormalisation now rescales every score');
});

test('a price or volume that cannot be read drops the row entirely', () => {
  assert.equal(scoreTicker(tk({ lastPr: undefined })), null);
  const { coins } = buildStrengthMap([tk({ symbol: 'XUSDT', usdtVolume: undefined })], null, 10);
  assert.deepEqual(coins, [], 'a coin with no readable volume was ranked by volume');
});

test('only a READ open interest becomes the next poll baseline', () => {
  const { oiSnapshot } = buildStrengthMap([tk({ symbol: 'NOOIUSDT', holdingAmount: undefined })], null, 10);
  assert.ok(!('NOOIUSDT' in oiSnapshot),
    'a null baseline would make the next poll compute a delta against nothing');
});


// ── 2. the sentinel: absence is not calm ────────────────────────────────────

test('THE ONE THAT MATTERS: an unreadable market is never an all-clear', () => {
  const s = buildSentinel(universe({ funding: null, doi_pct: null, dir: null, change_pct: null }), AT);
  assert.ok(!kinds(s).includes('calm'),
    'a systemic RISK surface published "no systemic crowding stands out" over data it could not read');
  assert.equal(s.gauge.score, null, 'a score was computed from no components');
  assert.equal(s.gauge.level, 'unknown');
  assert.ok(kinds(s).includes('coverage'), 'nothing told the reader what was missing');
});

test('a cold start cannot claim calm — there is no delta to have found nothing in', () => {
  const s = buildSentinel(universe({ doi_pct: null }), AT);
  assert.ok(!kinds(s).includes('calm'));
  assert.ok(s.gauge.partial, 'the gauge did not mark itself partial');
  assert.deepEqual(s.coverage.components_missing, ['leverage']);
  assert.match(s.flags.find((f) => f.kind === 'coverage').text, /previous poll/,
    'the reader is not told WHY open-interest change is missing');
});

test('the partial mark rides with the score, not in a footnote', () => {
  // "CALM" in 68px with the caveat in small text below is the instrument that
  // eventually gets read without its caveat.
  const s = buildSentinel(universe({ doi_pct: null }), AT);
  assert.equal(s.gauge.level, 'calm', 'three of four components genuinely read quiet');
  assert.equal(s.gauge.partial, true, 'and the chip must carry that itself');
});

test('a fully readable quiet market still reads calm — the guard must not cry wolf', () => {
  const s = buildSentinel(universe(), AT);
  assert.equal(s.gauge.level, 'calm');
  assert.equal(s.gauge.partial, false);
  assert.deepEqual(kinds(s), ['calm']);
});

test('a genuinely crowded market is unchanged', () => {
  const s = buildSentinel(universe((i) => ({
    funding: 0.0007, dir: 0.4, change_pct: -3, doi_pct: i < 12 ? 20 : 1,
  })), AT);
  assert.equal(s.gauge.level, 'high');
  assert.equal(s.gauge.partial, false);
  for (const k of ['funding', 'herding', 'leverage', 'bias']) {
    assert.ok(kinds(s).includes(k), `the ${k} flag stopped firing`);
  }
});

// A tenth of the universe unreadable: enough to have swung every aggregate
// when it was zeroed, and still comfortably above the coverage floor, so these
// assert the ARITHMETIC rather than the decline-to-answer path below.
const mostly = (readable, absent) => universe((i) => (i % 10 ? readable : absent));

test('a missing funding rate does not drag the market-wide average toward neutral', () => {
  // Crowded long at 9 bps. Averaging the unreadable coins in as zero pulled it
  // under the 5 bps threshold and the crowded-book flag went quiet.
  const s = buildSentinel(mostly({ funding: 0.0009, dir: 0.4 }, { funding: null, dir: 0.4 }), AT);
  assert.ok(s.funding.avg_bps >= 5,
    `avg ${s.funding.avg_bps} bps — unreadable coins were averaged in as zero`);
  assert.ok(kinds(s).includes('funding'));
});

test('breadth is a share of the coins that HAD a 24h move', () => {
  // Everything readable is moving up. That is 100% herding among what was
  // read — not diluted by coins that could not vote but still counted.
  const s = buildSentinel(mostly({ change_pct: 4 }, { change_pct: null }), AT);
  assert.equal(s.herding.same_dir_pct, 100);
  assert.equal(s.coverage.change, 0.9);
});

test('a coin with no direction is not admitted as an exact 50/50', () => {
  const s = buildSentinel(mostly({ dir: 0.9 }, { dir: null }), AT);
  assert.ok(s.bias.long_share_pct > 90,
    `${s.bias.long_share_pct}% — unscored coins voted "balanced"`);
});

test('below the coverage floor a component declines rather than answering', () => {
  // Half the market unreadable is not "the market-wide average is X". The
  // honest move is to stop, say so, and let the other components speak.
  const s = buildSentinel(universe((i) => (i % 2
    ? { funding: 0.0009 } : { funding: null })), AT);
  assert.equal(s.funding.avg_bps, null, 'half a market was described as the whole one');
  assert.equal(s.funding.lean, null, 'and a direction was attached to it');
  assert.ok(s.coverage.components_missing.includes('funding'));
  assert.ok(!kinds(s).includes('calm'), 'and it still must not read as an all-clear');
});

test('the floor is a coverage rule, not a count rule', () => {
  // OI-weighted: one enormous coin going dark takes the funding read with it,
  // even though 39 of 40 coins still have a rate. That is correct — it is the
  // one that dominates the average.
  const coins = universe({ funding: 0.0009 });
  coins[0].oi_usd = 1e12; coins[0].funding = null;
  const s = buildSentinel(coins, AT);
  assert.equal(s.funding.avg_bps, null, `39/40 coins read, but ${Math.round(100 * s.coverage.funding)}% of the OI did not`);
});

test('coins whose OI cannot be read leave the universe, and the count says so', () => {
  const coins = universe();
  coins[0].oi_usd = null; coins[1].oi_usd = null;
  const s = buildSentinel(coins, AT);
  assert.equal(s.universe, 38);
  assert.equal(s.coverage.omitted_no_oi, 2,
    'a sentinel describing part of the market must not call that "the market"');
});

test('a universe with no readable OI at all is unknown, not a zero-risk market', () => {
  const s = buildSentinel(universe({ oi_usd: null }), AT);
  assert.equal(s.gauge.score, null, 'score 0 / level calm was the lowest reading the gauge has');
  assert.equal(s.gauge.level, 'unknown');
  assert.ok(!kinds(s).includes('calm'));
});

test('a coin only appears in a crowded list it qualified for on a value it HAD', () => {
  const s = buildSentinel(universe({ funding: null, doi_pct: null }), AT);
  assert.deepEqual(s.funding.crowded_long, []);
  assert.deepEqual(s.funding.crowded_short, []);
  assert.deepEqual(s.leverage.surging, []);
  assert.equal(s.leverage.available, false, 'silence must be distinguishable from absence');
});

test('coverage is published, so an agent can tell "quiet" from "barely looked"', () => {
  const s = buildSentinel(universe({ funding: null }), AT);
  assert.equal(s.coverage.funding, 0);
  assert.equal(s.coverage.dir, 1);
  assert.ok(Array.isArray(s.coverage.components_missing));
});


// ── 4. what an adversarial review found that the mutation pass did not ──────

test('the leverage flag can actually fire at production universe size', () => {
  // `surging` is a top-8 DISPLAY list and its length was the component's
  // numerator AND the flag's gate. At UNIVERSE=400 that made the gate
  // `8 >= Math.max(3, 400*0.08)` — `8 >= 32` — so the "fresh leverage piling
  // in" flag could NEVER fire and the component saturated at 0.111, worth 2.4
  // of the 22 points it is weighted for. A whole market surging read as quiet.
  const coins = [];
  for (let i = 0; i < 400; i++) {
    coins.push({ base: 'C' + i, symbol: 'C' + i + 'USDT', funding: 0,
      oi_usd: 1e8, doi_pct: i < 40 ? 25 : 1, dir: 0, change_pct: (i % 2 ? 1 : -1) });
  }
  const s = buildSentinel(coins, AT);
  assert.ok(kinds(s).includes('leverage'),
    'a 10%-of-market OI surge did not raise the leverage flag');
  assert.equal(s.leverage.surging.length, 8, 'the display list is still capped');
  assert.equal(s.leverage.surging_count, 40,
    'a capped display list published without its total reads as the total');
  assert.match(s.flags.find((f) => f.kind === 'leverage').text, /^40 coins/);
});

test('a near-total OI blackout does not report full coverage of the remnant', () => {
  // Every coverage denominator is the SURVIVOR set — coins with unreadable OI
  // leave at `list`. So nine-in-ten going dark left a tenth with 100% funding
  // coverage and an unqualified all-clear about "the market". omitted_no_oi
  // was recorded and gated nothing.
  const coins = [];
  for (let i = 0; i < 40; i++) {
    coins.push({ base: 'C' + i, symbol: 'C' + i + 'USDT', funding: 0,
      oi_usd: i < 4 ? 1e8 : null, doi_pct: 0, dir: 0, change_pct: (i % 2 ? 1 : -1) });
  }
  const s = buildSentinel(coins, AT);
  assert.equal(s.gauge.score, null, 'a tenth of the market was scored as the market');
  assert.equal(s.gauge.partial, true);
  assert.ok(!kinds(s).includes('calm'));
  assert.equal(s.coverage.universe_share, 0.1);
  assert.match(s.flags.find((f) => f.kind === 'coverage').text, /10% of the coins/);
});

test('a component that merely scrapes past the floor still marks the read partial', () => {
  // The floor decides whether to SPEAK; this decides whether to HEDGE. They
  // were the same boolean, so a component renormalised from 61% of the market
  // published as a whole-market fact with no caveat on any surface.
  const coins = [];
  for (let i = 0; i < 100; i++) {
    coins.push({ base: 'C' + i, symbol: 'C' + i + 'USDT', funding: 0, oi_usd: 1e8,
      doi_pct: 0, dir: 0, change_pct: i < 70 ? (i % 2 ? 1 : -1) : null });
  }
  const s = buildSentinel(coins, AT);
  assert.equal(s.coverage.change, 0.7, 'setup: above the 0.6 floor, below complete');
  assert.ok(s.herding.same_dir_pct !== null, 'it should still score at 70%');
  assert.equal(s.gauge.partial, true, 'but the read must not present as complete');
  assert.ok(!kinds(s).includes('calm'));
});

test('the empty-universe payload has the same shape as every other read', () => {
  // It omitted `partial` and `components_missing`, so the read that covered
  // NOTHING was the one whose `!gauge.partial` said "complete".
  const s = buildSentinel([{ base: 'A', symbol: 'AUSDT', oi_usd: null }], AT);
  assert.equal(s.gauge.partial, true);
  assert.deepEqual(s.coverage.components_missing.sort(),
    ['bias', 'funding', 'herding', 'leverage']);
  assert.equal(s.leverage.available, false);
  assert.equal(s.leverage.surging_count, null);
  assert.equal(s.coverage.universe_share, 0);
});

test('the coverage note states what is true and does not name a cause', () => {
  // "the first read after a restart has none" asserted a CAUSE from a
  // condition that only establishes no coin carried a delta — a venue-wide OI
  // outage looks identical. A heuristic is never a verdict.
  const s = buildSentinel(universe({ doi_pct: null }), AT);
  const txt = s.flags.find((f) => f.kind === 'coverage').text;
  assert.ok(!/restart/.test(txt), `a cause was asserted: ${txt}`);
  assert.match(txt, /delta against a previous poll/);
  assert.ok(!/: \./.test(txt), `empty enumeration leaked into the note: ${txt}`);
});

test('every partial reason names itself rather than printing an empty list', () => {
  const reasons = [
    [universe({ doi_pct: null }), /Not measured this read: open-interest change/],
    [universe((i) => (i % 10 ? {} : { change_pct: null })), /Partial coverage: 24h moves/],
  ];
  for (const [coins, re] of reasons) {
    const txt = buildSentinel(coins, AT).flags.find((f) => f.kind === 'coverage').text;
    assert.match(txt, re);
    assert.ok(!/undefined|\[object/.test(txt), txt);
  }
});

// ── 3. the renderers: no null reaches a comparison ──────────────────────────

const SM = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'strengthmap.js'), 'utf8');
const SENTINEL_PAGE = fs.readFileSync(path.join(__dirname, '..', 'public', 'sentinel.html'), 'utf8');

test('the trade deeplink no longer picks a side it did not compute', () => {
  // The furthest downstream a manufactured zero reached: a pre-filled ticket.
  assert.ok(!/&dir=\$\{c\.dir >= 0/.test(SM),
    'the ticket link still derives a direction with `c.dir >= 0`, and null >= 0 is true');
  assert.match(SM, /Direction not scored/,
    'an unscored coin must still open the ticket, just without choosing a side');
});

test('the Long/Short label goes through a readability gate', () => {
  assert.ok(!/\$\{c\.dir >= 0 \? '▲ Long'/.test(SM), 'the label still branches straight off c.dir');
});

/**
 * `coinColor` decides the single loudest claim on this page — a star being
 * green — and it lives in a browser module with no export. A source scan
 * "proved" it fixed while a mutation that returned hue 135 from the unscored
 * branch sailed straight through, because the constant it grepped for was
 * still defined three lines up. So: slice the real functions out and RUN them.
 * (`app/test/engine_status_scenarios.test.js` established this pattern.)
 */
function loadCoinColor(bias) {
  const vm = require('node:vm');
  const cut = (name, kind) => {
    const i = SM.indexOf(`${kind} ${name}`);
    assert.ok(i >= 0, `${name} moved — this test slices it out by name`);
    // Walk to the end of the declaration: for `const` up to the closing `;`
    // at depth 0, for `function` up to its matching brace.
    let depth = 0, started = false;
    for (let j = i; j < SM.length; j++) {
      const ch = SM[j];
      if (ch === '{' || ch === '(') { depth++; started = true; }
      else if (ch === '}' || ch === ')') depth--;
      if (started && depth === 0 && (ch === '}' || ch === ';')) return SM.slice(i, j + 1);
    }
    throw new Error(`could not slice ${name}`);
  };
  const ctx = { state: { bias }, Math, isFinite, Number };
  vm.createContext(ctx);
  vm.runInContext(
    [cut('rd', 'const'), cut('UNSCORED_HUE', 'const'), cut('coinColor', 'function')].join('\n'),
    ctx);
  return (c) => vm.runInContext('coinColor', ctx)(c);
}

test('an unscored coin is NOT painted green — the loudest claim on the page', () => {
  const coinColor = loadCoinColor('long');
  const scored = coinColor({ dir: 0.4, long_score: 70, short_score: 30 });
  assert.equal(scored.hue, 135, 'a genuinely long-leaning coin must still read green');

  // `c.dir >= 0` with dir null is TRUE in JS, so this used to come out at
  // hue 135 — full green, "long-dominant" — from no data whatsoever.
  const unscored = coinColor({ dir: null, long_score: null, short_score: null });
  assert.notEqual(unscored.hue, 135, 'an unscored coin is being painted long-green');
  assert.equal(unscored.unscored, true);
  assert.ok(unscored.sat < 0.2, `sat ${unscored.sat} — grey is the only hue that claims nothing`);
});

test('a genuinely short-leaning coin still reads red', () => {
  const coinColor = loadCoinColor('short');
  assert.equal(coinColor({ dir: -0.4, long_score: 30, short_score: 70 }).hue, 0);
});

test('the "not plotted" disclosure cannot go stale', () => {
  // `funding` is one of the DEFAULT axes, so coins with no funding rate are
  // unplaced from the very first render and this line is load-bearing.
  //
  // The first version APPENDED to whatever was already on screen, guarded by
  // "have I appended before?". Two ways that goes false: switch axes and the
  // count changes but the guard blocks the update; switch to axes where
  // everything places and the branch is skipped entirely, leaving a stale
  // "5 not plotted" on screen while nothing is missing. A disclosure that goes
  // false is the defect this whole change exists to remove.
  const vm = require('node:vm');
  const i = SM.indexOf('function countLine');
  assert.ok(i >= 0, 'countLine moved — this test slices it out by name');
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(SM.slice(i, SM.indexOf('\n}', i) + 2), ctx);
  const countLine = vm.runInContext('countLine', ctx);

  const base = '240 coins · updated 12:00:00';
  assert.equal(countLine(base, 5), base + ' · 5 not plotted on these axes');
  assert.equal(countLine(base, 0), base, 'a stale count survived a clean relayout');
  // Idempotent across relayouts: the same base always yields the same line.
  assert.equal(countLine(countLine(base, 0), 0), base);
  assert.equal(countLine(base, 12), countLine(base, 12));
  assert.ok(!countLine(base, 12).includes('5 not plotted'));
});

test('the sentinel page reads the lean the server decided, not `>= 0` on a null', () => {
  assert.ok(!/d\.funding\.avg_bps >= 0/.test(SENTINEL_PAGE),
    'null >= 0 is TRUE, so this printed a GREEN "+null bps"');
  assert.ok(!/d\.bias\.long_share_pct >= 50/.test(SENTINEL_PAGE));
  assert.match(SENTINEL_PAGE, /d\.funding\.lean/);
  assert.match(SENTINEL_PAGE, /d\.bias\.lean/);
});

test('an unscorable gauge parks no needle at the calmest end of the track', () => {
  assert.ok(!/Math\.max\(0, Math\.min\(100, d\.gauge\.score\)\)\s*;/.test(SENTINEL_PAGE),
    'Math.min(100, null) is 0 — the most reassuring spot on the widget, chosen by absence');
  assert.match(SENTINEL_PAGE, /mk === null \? '' :/);
});

/**
 * THE THIRD SURFACE. /sentinel and the MCP tool were fixed first; this card
 * was missed, and turned up only by asking which OTHER surface makes the same
 * claim — the corollary that found five of the ten defects in the July sweep.
 *
 * Plant the state, assert what the card says.
 */
function guardianSentinelCard(payload) {
  const vm = require('node:vm');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'guardian-console.js'), 'utf8');
  const i = src.indexOf('function sentinelView');
  assert.ok(i >= 0, 'sentinelView moved — this test slices it out by name');
  const ctx = { esc: (s) => String(s == null ? '' : s), card: (t, b) => b };
  vm.createContext(ctx);
  vm.runInContext(src.slice(i, src.indexOf('\n  }', i) + 4), ctx);
  return vm.runInContext('sentinelView', ctx)(payload);
}

test('the guardian console card does not print "null% short" for an unread market', () => {
  const out = guardianSentinelCard({
    gauge: { score: null, level: 'unknown', partial: true },
    bias: { long_share_pct: null, label: 'unknown', lean: null },
    herding: { same_dir_pct: null, direction: null },
    funding: { avg_bps: null, lean: null },
    flags: [{ text: 'Not measured this read: funding rates.' }],
  });
  // `d.bias.long_share_pct >= 50` on a null is FALSE in JS, so an unread
  // market used to announce a direction — "OI bias null% short".
  assert.ok(!/null/.test(out), `a null reached the card text: ${out}`);
  assert.ok(!/\bshort\b|\blong\b/.test(out),
    `a side was named for a market with no readable lean: ${out}`);
  assert.ok(!/NaN|undefined/.test(out), out);
  assert.match(out, /partial read/, 'the card does not say the read was partial');
  assert.match(out, /Not measured this read/, 'the coverage note did not reach the card');
});

test('the same card is unchanged when the market IS readable', () => {
  const out = guardianSentinelCard({
    gauge: { score: 71, level: 'elevated', partial: false },
    bias: { long_share_pct: 64.2, label: 'crowded long', lean: 'long' },
    herding: { same_dir_pct: 78.5, direction: 'up' },
    funding: { avg_bps: 7.4, lean: 'positive' },
    flags: [{ text: 'Funding is positive market-wide.' }],
  });
  assert.match(out, /71\/100/);
  assert.match(out, /OI bias 64\.2% long/);
  assert.match(out, /herding 78\.5% up/);
  assert.match(out, /avg funding 7\.4 bps/);
  assert.ok(!/partial read/.test(out), 'a complete read was marked partial');
});

/**
 * Strip comments before scanning. Four false failures in this repo have come
 * from a comment that quotes the very string it forbids — and this file's
 * comments quote `|| 0` repeatedly, on purpose, to explain what went wrong.
 */
function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1')).join('\n');
}

test('nothing in the map defaults an unreadable value to zero', () => {
  // The first version of this test grepped for one exact expression. A
  // mutation that wrote the same defect as `(c[key] || 0)` — a different
  // string, the identical bug — went straight through it. The property is
  // "this file has no zero-defaults", not "this file lacks that character
  // sequence", so scan for the SHAPE and let the whole file be in scope.
  const code = codeOnly(SM);
  const hits = code.split('\n')
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => /\|\|\s*0\b/.test(l));
  assert.deepEqual(hits, [],
    'a `|| 0` fallback is back: an unreadable value would render as a measured zero');
});

test('the same rule holds in the scorer and the sentinel', () => {
  for (const f of ['lib/strengthmap.js', 'lib/sentinel.js']) {
    const code = codeOnly(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'));
    // `num(v, d = 0)` survives in sentinel.js for the CLOCK only — a timestamp
    // default is not a market measurement — so the ban is on the operator form.
    const hits = code.split('\n').filter((l) => /\|\|\s*0\b/.test(l));
    assert.deepEqual(hits, [], `${f} defaults an unreadable value to zero`);
  }
});
