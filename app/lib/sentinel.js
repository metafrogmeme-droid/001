'use strict';
/**
 * Systemic Risk Sentinel — a market-wide read of CROWDING and herding across the
 * USDT-perp universe, computed from PUBLIC market data (funding, open interest,
 * ΔOI, 24h direction). When positioning piles onto one side, funding runs hot,
 * leverage surges and moves correlate, a coordinated unwind / liquidation
 * cascade gets more likely — this surfaces that as heuristic FLAGS, never a
 * verdict or a signal to trade.
 *
 * §4: public market facts only (OI/funding/breadth). Market OI in dollars is a
 * public fact, not a user's P&L. Flags are heuristic reads, explicitly not
 * advice. Pure + deterministic so the route can cache and tests can assert it.
 */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const num = (v, d = 0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
/** A readable number or null — never a default. `num` is kept only for `now`. */
const read = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

// Thresholds (documented so the flags are legible, not magic).
const FUND_HOT_BPS = 5;     // |funding| ≥ 5 bps/interval → a crowded book
const DOI_SURGE_PCT = 12;   // ΔOI ≥ +12% since the last poll → leverage piling in
const HERD_FLOOR = 0.55;    // same-direction share where herding starts to count

/**
 * How much of the market a component must actually have read before it is
 * allowed to score, and how much of the composite must exist before a number
 * is published at all.
 *
 * WHY THIS FILE NEEDED IT. Every input here arrives from `strengthmap`, which
 * until now turned any unreadable exchange field into 0. Follow one through:
 * a coin whose funding rate was missing contributed `0 * its OI share` to the
 * market-wide average, so a large-OI coin with no funding pulled the whole
 * market toward neutral. `dir = 0` read as an exact 50/50 lean. An unreadable
 * 24h change voted neither up nor down while still inflating the breadth
 * denominator. A cold start had no ΔOI baseline at all, so `surging` was empty
 * and the leverage component — 22% of the score — was a measured zero on the
 * first read after EVERY restart.
 *
 * Every one of those dilutions runs the same way: toward CALM. And with the
 * four flags suppressed, the last line published
 *
 *     "No systemic crowding stands out — positioning is broad and funding is
 *      near neutral."
 *
 * a confident all-clear assembled from data nobody could read, on a systemic
 * RISK surface consumed by both the website and an agent tool.
 *
 * `sentinel_live.js` already refuses to render an empty feed as a calm market
 * — `if (!tickers.length) throw` — and `sentinel_live_shared.test.js` pins it
 * as "an error, never an empty all-clear". That control was correct and
 * applied to a set that did not include the case that actually happens:
 * tickers arrive, their FIELDS do not.
 *
 * So absence is omitted rather than zeroed (a composite view where one dead
 * source must not blank the rest), each component renormalises over the share
 * it genuinely covered, and a component that read too little does not score at
 * all. Fail-open per component is right for SCORING and wrong for DISPLAY, so
 * `calm` is gated separately below — the same seam `integrity_veto.is_reading`
 * draws in the Python half.
 */
const MIN_COVERAGE = 0.6;      // per component, share of the universe read
const MIN_COMPOSITE = 0.6;     // share of component weight present to publish a score
//: Above this a read is treated as complete. Between MIN_COVERAGE and this,
//: a component still scores but the read is marked partial — speaking and
//: hedging are different decisions.
const FULL_COVERAGE = 0.95;

const COMPONENT_WEIGHTS = { herding: 0.32, funding: 0.28, leverage: 0.22, bias: 0.18 };

function level(score) {
  if (score === null) return 'unknown';
  return score >= 75 ? 'high' : score >= 50 ? 'elevated' : score >= 25 ? 'building' : 'calm';
}

/**
 * @param coins strength-map coins: { base, symbol, funding(fraction), oi_usd,
 *              doi_pct, dir(-1..1), change_pct, long_score, short_score, ... }
 * @returns systemic read (see fields below)
 */
function buildSentinel(coins, now) {
  const all = (coins || []).filter(Boolean);
  // Open interest is the weighting basis for half of this read, so a coin
  // whose OI could not be read cannot be weighted and leaves the universe.
  // How MANY left is itself information: a sentinel describing a third of the
  // market must not call that "the market".
  const list = all.filter((c) => read(c.oi_usd) !== null && c.oi_usd > 0);
  const n = list.length;
  const omittedNoOi = all.length - n;
  const empty = {
    generated_at: new Date(num(now, Date.now())).toISOString(),
    universe: 0, total_oi_usd: 0,
    // NOT `score: 0, level: 'calm'`. That published the lowest risk reading the
    // gauge has for a market it had not read at all.
    // Same SHAPE as every other return path. This one omitted `partial` and
    // `components_missing`, so the read that covered NOTHING was the single
    // read whose `!gauge.partial` said "complete" — a consumer checking that
    // flag got the most confident answer from the least data.
    gauge: { score: null, level: 'unknown', partial: true },
    bias: { long_share_pct: null, label: 'unknown', lean: null },
    funding: { avg_bps: null, lean: null, crowded_long: [], crowded_short: [] },
    leverage: { surging: [], surging_count: null, available: false },
    herding: { same_dir_pct: null, direction: null },
    coverage: { universe: 0, universe_share: 0, omitted_no_oi: omittedNoOi,
      funding: 0, dir: 0, change: 0, doi: 0,
      components_missing: Object.keys(COMPONENT_WEIGHTS) },
    flags: [{ kind: 'coverage', severity: 'low',
      text: 'No open interest could be read for any coin, so there is nothing to weigh a systemic read against. This is missing data, not a calm market.' }],
    note: 'No market data right now.',
  };
  if (!n) return empty;

  const totalOi = list.reduce((a, c) => a + c.oi_usd, 0);

  // Each aggregate is taken over the coins that actually carried the field,
  // and normalised by the share it covered — NOT by the whole universe, which
  // is what silently dragged every one of these toward neutral.
  let fundWeighted = 0, fundOi = 0;
  let longOi = 0, dirOi = 0;
  let up = 0, down = 0, changeRead = 0, doiRead = 0;
  for (const c of list) {
    const funding = read(c.funding);
    if (funding !== null) { fundWeighted += funding * c.oi_usd; fundOi += c.oi_usd; }

    const dir = read(c.dir);
    if (dir !== null) {
      // Smooth long lean per coin: dir −1..1 → 0..1. A coin with no direction
      // is left out; it used to be admitted as an exact 50/50, which is a
      // measurement of balance rather than an absence of one.
      longOi += c.oi_usd * ((clamp(dir, -1, 1) + 1) / 2);
      dirOi += c.oi_usd;
    }

    const chg = read(c.change_pct);
    // A real 0.00% is a flat coin and counts in the denominator; an unreadable
    // one is not a flat coin and counts nowhere.
    if (chg !== null) { changeRead++; if (chg > 0) up++; else if (chg < 0) down++; }

    if (read(c.doi_pct) !== null) doiRead++;
  }

  const cover = {
    universe: n,
    omitted_no_oi: omittedNoOi,
    // EVERY OTHER DENOMINATOR BELOW IS THE SURVIVOR SET. Coins with no
    // readable open interest have already left at `list`, so `totalOi` and `n`
    // describe what remained — and a market where nine coins in ten went dark
    // would report 100% funding coverage over the tenth and publish a
    // confident, unqualified all-clear about "the market". `omitted_no_oi` was
    // recorded and gated nothing. This is the share that survived, and it
    // gates the read like any other coverage number.
    universe_share: all.length > 0 ? n / all.length : 0,
    funding: totalOi > 0 ? fundOi / totalOi : 0,      // OI-weighted, of survivors
    dir: totalOi > 0 ? dirOi / totalOi : 0,           // OI-weighted, of survivors
    change: changeRead / n,                            // per surviving coin
    doi: doiRead / n,                                  // per surviving coin
  };

  // Too little of the market left to describe the market at all.
  const universeUsable = cover.universe_share >= MIN_COVERAGE;

  const avgFundingBps = cover.funding >= MIN_COVERAGE && fundOi > 0
    ? (fundWeighted / fundOi) * 10000 : null;
  const longSharePct = cover.dir >= MIN_COVERAGE && dirOi > 0
    ? (longOi / dirOi) * 100 : null;

  // Crowded books by funding extreme (biggest OI first). A coin only appears in
  // a list it QUALIFIED for on a value that was read — the filters below all
  // test the field itself, so an absent one can never satisfy a threshold.
  const byOi = (a, b) => b.oi_usd - a.oi_usd;
  const r2 = (v) => (v === null ? null : Math.round(v * 100) / 100);
  const slim = (c) => ({
    base: c.base || String(c.symbol || '').replace(/USDT$/, ''),
    funding_bps: read(c.funding) === null ? null : Math.round(c.funding * 10000 * 100) / 100,
    doi_pct: r2(read(c.doi_pct)),
    oi_usd: Math.round(c.oi_usd),
    change_pct: r2(read(c.change_pct)),
  });
  const crowdedLong = list.filter((c) => read(c.funding) !== null && c.funding * 10000 >= FUND_HOT_BPS)
    .sort(byOi).slice(0, 8).map(slim);
  const crowdedShort = list.filter((c) => read(c.funding) !== null && c.funding * 10000 <= -FUND_HOT_BPS)
    .sort(byOi).slice(0, 8).map(slim);
  // COUNT FIRST, TRUNCATE SECOND. `surging` is a top-8 DISPLAY list, and its
  // length was being used as the leverage component's numerator and as the
  // flag's gate. At the production universe of 400 that made the gate
  // `8 >= Math.max(3, 400 * 0.08)` — i.e. `8 >= 32` — so the "fresh leverage
  // piling in" flag could NEVER fire, and the component saturated at 0.111,
  // contributing at most 2.4 of the 22 points it is weighted for. A whole
  // market surging read the same as a quiet one.
  //
  // Pre-existing rather than introduced here, but it is the same defect as the
  // rest of this file — a risk component structurally understated — and the
  // count is published so a bounded display list cannot read as the total.
  const surgingAll = list.filter((c) => read(c.doi_pct) !== null && c.doi_pct >= DOI_SURGE_PCT);
  const surgingCount = surgingAll.length;
  const surging = surgingAll.slice()
    .sort((a, b) => b.doi_pct - a.doi_pct).slice(0, 8).map(slim);

  // Breadth is a share of the coins that HAD a 24h change, not of the universe.
  const sameDirShare = cover.change >= MIN_COVERAGE && changeRead > 0
    ? Math.max(up, down) / changeRead : null;
  const herdDir = sameDirShare === null ? null : (up >= down ? 'up' : 'down');

  // Component scores 0..1, or null when the component did not read enough to
  // have an opinion. `leverage` is null on every cold start — with no previous
  // poll there is no delta to measure, and "no surge detected" is a different
  // statement from "nothing to detect it with".
  const fundingCrowd = avgFundingBps === null ? null
    : clamp(Math.abs(avgFundingBps) / FUND_HOT_BPS, 0, 1);
  const herding = sameDirShare === null ? null
    : clamp((sameDirShare - HERD_FLOOR) / (1 - HERD_FLOOR), 0, 1);
  const leverage = cover.doi >= MIN_COVERAGE && doiRead > 0
    ? clamp((surgingCount / doiRead) / 0.18, 0, 1) : null;
  const sideLean = longSharePct === null ? null
    : clamp(Math.abs(longSharePct - 50) / 30, 0, 1);   // >80/20 → max

  // The composite renormalises over the components that exist. Below
  // MIN_COMPOSITE there is no number to publish — a partial total printed as a
  // whole is the shape this whole change is about.
  const parts = { herding, funding: fundingCrowd, leverage, bias: sideLean };
  let weighted = 0, presentWeight = 0;
  for (const [k, wgt] of Object.entries(COMPONENT_WEIGHTS)) {
    if (parts[k] === null) continue;
    weighted += wgt * parts[k];
    presentWeight += wgt;
  }
  const score = universeUsable && presentWeight >= MIN_COMPOSITE
    ? Math.round(100 * (weighted / presentWeight)) : null;
  const missing = Object.keys(COMPONENT_WEIGHTS).filter((k) => parts[k] === null);

  // Heuristic flags — reads, not verdicts. Each one is gated on its own
  // component having read enough to speak; a flag that cannot fire because the
  // data was missing must not be indistinguishable from one that did not fire
  // because the market is quiet.
  const flags = [];
  if (avgFundingBps !== null && Math.abs(avgFundingBps) >= FUND_HOT_BPS) {
    const longCrowd = avgFundingBps > 0;
    flags.push({ kind: 'funding', severity: fundingCrowd >= 0.8 ? 'high' : 'medium',
      text: `Funding is ${longCrowd ? 'positive' : 'negative'} market-wide (${avgFundingBps.toFixed(1)} bps) — `
        + `${longCrowd ? 'longs are paying to hold, a crowded-long tell (squeeze-DOWN risk)' : 'shorts are paying, a crowded-short tell (squeeze-UP risk)'}.` });
  }
  if (herding !== null && herding >= 0.4) {
    flags.push({ kind: 'herding', severity: herding >= 0.75 ? 'high' : 'medium',
      text: `${Math.round(sameDirShare * 100)}% of the coins with a readable 24h move are moving ${herdDir} together — correlated, low-dispersion tape where a single shock hits everything at once.` });
  }
  if (leverage !== null && surgingCount >= Math.max(3, doiRead * 0.08)) {
    flags.push({ kind: 'leverage', severity: leverage >= 0.7 ? 'high' : 'medium',
      text: `${surgingCount} coins are seeing open-interest surge ≥${DOI_SURGE_PCT}% — fresh leverage piling in, which fuels a faster unwind if it turns.`
        + (surgingCount > surging.length ? ` (largest ${surging.length} listed)` : '') });
  }
  if (sideLean !== null && sideLean >= 0.5) {
    flags.push({ kind: 'bias', severity: sideLean >= 0.8 ? 'high' : 'medium',
      text: `${longSharePct.toFixed(0)}% of open interest leans ${longSharePct >= 50 ? 'long' : 'short'} — one-sided positioning concentrates liquidation levels.` });
  }

  // THE SEAM BETWEEN SCORING AND DISPLAY.
  //
  // Everything above is fail-open per component, which is right: one dead
  // field must not blank a market-wide read. This is the other half, and it
  // is the opposite rule. "No systemic crowding stands out" is a claim about
  // what was LOOKED AT, so it may only be made when every component actually
  // looked. Four silent flags because four components had no data produce the
  // identical output to four silent flags because the market is quiet — and
  // one of those is an all-clear manufactured from nothing.
  // Captured BEFORE any note is appended. Reading `flags.length` after pushing
  // a coverage note would make "the market is quiet" depend on whether a note
  // happened to be added — true today, and silently untrue the moment the note
  // becomes conditional. The two questions are separate, so they are separate
  // variables: `quiet` is about the market, `partial` is about the read.
  const quiet = flags.length === 0;
  // `missing.length > 0` alone only fired when a component was ENTIRELY
  // absent. A component renormalised from 61% of the market scored, published
  // as a market-wide fact, and carried no caveat anywhere — the floor decides
  // whether to speak, and this decides whether to hedge. They are different
  // questions and were the same boolean.
  const thin = Object.entries({ universe: cover.universe_share, funding: cover.funding,
    dir: cover.dir, change: cover.change, doi: cover.doi })
    .filter(([k, v]) => v < FULL_COVERAGE
      && !(k === 'doi' && missing.includes('leverage'))    // already named below
      && !(k === 'funding' && missing.includes('funding'))
      && !(k === 'dir' && missing.includes('bias'))
      && !(k === 'change' && missing.includes('herding')))
    .map(([k]) => k);
  const partial = missing.length > 0 || thin.length > 0 || !universeUsable;
  if (partial) {
    const NAMES = { herding: '24h breadth', funding: 'funding rates',
      leverage: 'open-interest change', bias: 'directional lean' };
    // `thin` is keyed by COVERAGE FIELD, not by component, so it needs its own
    // vocabulary — NAMES.change is undefined and would print the raw key.
    const FIELDS = { universe: 'the coin universe', funding: 'funding rates',
      dir: 'directional lean', change: '24h moves', doi: 'open-interest change' };
    // Three different reasons a read is partial, and the note must name the one
    // that applies. Joining an empty `missing` produced the literal
    // "Not measured this read: ." on the case where every component scored but
    // the UNIVERSE was too thin to represent the market.
    const say = [];
    if (missing.length) {
      say.push(`Not measured this read: ${missing.map((k) => NAMES[k]).join(', ')}.`);
      if (missing.includes('leverage') && cover.doi === 0) {
        // NOT "the first read after a restart has none". That names a CAUSE
        // from a condition which only establishes that no coin carried a
        // delta — a venue-wide open-interest outage looks identical. State
        // what is true and let the reader draw it: a heuristic is never a
        // verdict, and not overclaiming is this file's whole job.
        say.push('Open-interest change is a delta against a previous poll, and no coin had one.');
      }
    }
    if (!universeUsable) {
      say.push(`Only ${Math.round(cover.universe_share * 100)}% of the coins offered a readable `
        + 'open interest, which is too little to describe the market — no systemic score is '
        + 'published for this read.');
    } else if (thin.length) {
      say.push(`Partial coverage: ${thin.map((k) => FIELDS[k] || k).join(', ')}.`);
    }
    if (omittedNoOi > 0 && universeUsable) {
      say.push(`${omittedNoOi} coin(s) had no readable open interest and are outside this read.`);
    }
    say.push('Those conditions are unknown here, not absent — this read covers the rest.');
    flags.push({ kind: 'coverage', severity: 'low', text: say.join(' ') });
  } else if (quiet) {
    flags.push({ kind: 'calm', severity: 'low',
      text: 'No systemic crowding stands out — positioning is broad and funding is near neutral.' });
  }

  const r1 = (v) => (v === null ? null : Math.round(v * 10) / 10);
  return {
    generated_at: new Date(num(now, Date.now())).toISOString(),
    universe: n, total_oi_usd: Math.round(totalOi),
    // `partial` rides WITH the score, not in a footnote. A cold start scores a
    // genuine 3-of-4 quiet and the word for that is still "calm" — but "CALM"
    // set in 68px with the caveat in small text below is the instrument the
    // runbook warns about: the one somebody eventually reads without its
    // caveat. The chip carries its own qualifier so the two cannot separate.
    gauge: { score, level: level(score), partial },
    bias: {
      long_share_pct: r1(longSharePct),
      label: sideLean === null ? 'unknown'
        : sideLean >= 0.5 ? (longSharePct >= 50 ? 'crowded long' : 'crowded short') : 'balanced',
      // Decided HERE, once, so a page cannot re-derive it with `>= 50` and get
      // a confident answer out of a null.
      lean: longSharePct === null ? null : (longSharePct >= 50 ? 'long' : 'short'),
    },
    funding: {
      avg_bps: r2(avgFundingBps),
      lean: avgFundingBps === null ? null : (avgFundingBps >= 0 ? 'positive' : 'negative'),
      crowded_long: crowdedLong, crowded_short: crowdedShort,
    },
    //: `surging` is a top-8 display list; `surging_count` is how many there
    //: actually are. A bounded list published without its total reads as the
    //: total — the same silent-cap mistake the repo's own workflow guidance
    //: calls out.
    leverage: { surging, surging_count: leverage === null ? null : surgingCount,
      available: leverage !== null },
    herding: {
      same_dir_pct: sameDirShare === null ? null : Math.round(sameDirShare * 1000) / 10,
      direction: herdDir,
    },
    //: What this read actually covered. Published rather than kept internal:
    //: an agent consuming the gauge needs to know a 12 is "quiet market" and
    //: not "we could only see a third of it".
    coverage: {
      universe: n,
      universe_share: Math.round(cover.universe_share * 1000) / 1000,
      omitted_no_oi: omittedNoOi,
      funding: Math.round(cover.funding * 1000) / 1000,
      dir: Math.round(cover.dir * 1000) / 1000,
      change: Math.round(cover.change * 1000) / 1000,
      doi: Math.round(cover.doi * 1000) / 1000,
      components_missing: missing,
    },
    flags,
    disclaimer: 'Public market data · heuristic crowding read, not a verdict and not investment advice.',
  };
}

module.exports = { buildSentinel, FUND_HOT_BPS, DOI_SURGE_PCT };
