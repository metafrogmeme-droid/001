'use strict';
/**
 * Strength Map — a factor scoring of the whole Bitget USDT-perp universe, from
 * PUBLIC market data only (price, 24h change, volume, funding, open interest).
 * Each coin gets per-factor scores and a composite long/short strength, which
 * the 3D star-map plots. This is data-viz, NOT investment advice, and it shows
 * only public exchange market data — never a user's account or P&L (§4).
 *
 * Pure functions so the route can cache their output and tests can pin the math.
 */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * A readable number, or null. NEVER 0.
 *
 * This was `function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }`
 * — `float(x or 0)` off CLAUDE.md's table, applied to raw exchange JSON we do
 * not control. A ticker missing fundingRate / holdingAmount / high24h / low24h
 * scored, verifiably:
 *
 *     dir 0 · long_score 50.0 · funding 0 · oi_usd 0 · chg 0 · every factor 0
 *
 * and downstream `0 >= 0` is TRUE, so that coin rendered green, labelled
 * "▲ Long", with a `&dir=LONG` link into a trade ticket. Five confident
 * measurements, none of them measured.
 *
 * `Number('')` is 0 AND finite, so the empty string has to be rejected before
 * the finite check or it arrives as a real zero — the same trap `pct()` in
 * public/js/strengthmap.js documents.
 */
function maybe(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  // Only a STRING is a candidate for parsing. `Number([])` is 0 and finite,
  // `Number([5])` is 5, `Number(true)` is 1 — a `Number(v)` first would coerce
  // all three into confident measurements. Found by enumerating the shapes
  // absence actually arrives in rather than the one that came to mind.
  if (typeof v !== 'string') return null;
  if (v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Round to `p` decimals, preserving absence. */
function round(v, p) { const m = Math.pow(10, p); return v === null ? null : Math.round(v * m) / m; }

// Smooth squash to (-1, 1); `k` is the input value that maps to ~0.76. Takes a
// readable number or null, and absence stays absence rather than becoming a
// measured zero — `tanh(0)` is 0, which is the exact centre of the scale and
// reads as "measured, and neutral".
function squash(x, k) { return x === null ? null : Math.tanh(x / (k || 1)); }

// Factor weights for the composite directional strength. Illustrative — this is
// a visualization, not a signal. Momentum/trend lead; funding is contrarian
// (crowded longs = a headwind), ΔOI confirms.
const WEIGHTS = { momentum: 0.34, trend: 0.24, doi: 0.14, range: 0.10, funding: 0.18 };

/**
 * What `dir` needs before it is willing to claim a direction.
 *
 * A composite over a partial set, printed as whole, is the defect this file was
 * fixed for — and the naive repairs are both wrong:
 *
 *   ZEROING a missing factor pulls the blend toward neutral, which is a
 *   measurement. RENORMALISING over the readable weight is WORSE here, because
 *   `funding` is the only CONTRARIAN input: drop it and the surviving factors
 *   are all trend-following, then get scaled UP. In the complete-data probe
 *   funding contributed -0.462, the largest single term. Absence would not
 *   merely blur the answer, it would bias it long — and the coins most likely
 *   to be missing a funding rate are new listings, which have no funding
 *   window yet.
 *
 * So: `funding` and `momentum` (the dominant directional input) must both be
 * readable, and three quarters of the total weight must be present. Then the
 * remaining ≤25% is redistributed across factors that are all pulling the same
 * conceptual direction, which renormalising CAN honestly do. Otherwise `dir` is
 * null and every surface downstream declines to colour, label or link it.
 *
 * A cold start still scores: with no ΔOI baseline only `doi` (0.14) is missing,
 * leaving 0.86 — so the common case keeps working and the biased case does not.
 */
const DIR_REQUIRED = ['funding', 'momentum'];
const DIR_MIN_WEIGHT = 0.75;

/**
 * scoreTicker(t, prevOiUsd) → one plotted coin, or null if unusable.
 *   t: a Bitget mix ticker { symbol, lastPr, change24h, high24h, low24h,
 *      fundingRate, holdingAmount, usdtVolume }
 *   prevOiUsd: this symbol's OI (usd) from the previous snapshot, for ΔOI.
 */
function scoreTicker(t, prevOiUsd) {
  if (!t || !t.symbol) return null;
  // GUARDED, deliberately: an unreadable or non-positive price drops the row
  // entirely, so no surface ever has to render a price it could not read.
  const price = maybe(t.lastPr);
  if (price === null || price <= 0) return null;

  const changePct = maybe(t.change24h);                   // Bitget change24h is a fraction
  const changePctOut = changePct === null ? null : changePct * 100;
  // GUARDED too: buildStrengthMap drops a coin with no readable volume.
  const volumeUsd = maybe(t.usdtVolume);
  const funding = maybe(t.fundingRate);                   // fraction, e.g. 0.0001 = 0.01%
  const holding = maybe(t.holdingAmount);                 // base units
  const oiUsd = holding === null ? null : holding * price;
  const hi = maybe(t.high24h), lo = maybe(t.low24h);
  // Where price sits in the 24h range: 0 (at low) … 1 (at high). Without both
  // bounds there is no range to sit in — this used to fall back to 0.5, the
  // exact midpoint, which is a measured "dead centre of its range".
  const rangePos = (hi !== null && lo !== null && hi > lo)
    ? clamp((price - lo) / (hi - lo), 0, 1) : null;
  // ΔOI vs the previous snapshot (percent), or null when there is nothing to
  // compare against. This is the one that fires on EVERY cold start: the
  // baseline begins empty, so before this it reported a measured 0% change in
  // open interest for the entire universe on the first poll after a restart.
  const doiPct = (prevOiUsd && prevOiUsd > 0 && oiUsd !== null)
    ? ((oiUsd - prevOiUsd) / prevOiUsd) * 100 : null;

  // Per-factor signed scores in (-1, 1) — the "factor breakdown" the panel shows.
  const fundingFactor = squash(funding, 0.0004);
  const factors = {
    momentum: squash(changePctOut, 8),        // 24h move
    trend: rangePos === null ? null : rangePos * 2 - 1,          // position in the 24h range
    doi: squash(doiPct, 15),                  // open-interest build/unwind
    range: rangePos === null ? null : squash((rangePos - 0.5) * 4, 1), // range extension
    funding: fundingFactor === null ? null : -fundingFactor,     // contrarian: crowded longs → headwind
  };

  // Composite directional strength in (-1, 1), over the factors actually read —
  // see DIR_REQUIRED / DIR_MIN_WEIGHT for why absence is not simply zeroed.
  let sum = 0, readWeight = 0;
  for (const [k, w] of Object.entries(WEIGHTS)) {
    if (factors[k] === null) continue;
    sum += w * factors[k];
    readWeight += w;
  }
  const dirOk = readWeight >= DIR_MIN_WEIGHT
    && DIR_REQUIRED.every((k) => factors[k] !== null);
  const dir = dirOk ? clamp(sum / readWeight, -1, 1) : null;
  // A score is a restatement of `dir`. If there is no direction to claim there
  // is no 50/50 either — 50.0 is what an unscorable coin used to publish, and
  // it reads as a measured dead heat.
  const long_score = dir === null ? null : Math.round((50 + dir * 50) * 10) / 10;
  const short_score = dir === null ? null : Math.round((50 - dir * 50) * 10) / 10;

  return {
    symbol: t.symbol,
    base: String(t.symbol).replace(/USDT$/, ''),
    price,
    change_pct: round(changePctOut, 2),
    volume_usd: volumeUsd,
    funding,
    oi_usd: oiUsd,
    doi_pct: round(doiPct, 2),
    range_pos: round(rangePos, 3),
    factors: Object.fromEntries(
      Object.entries(factors).map(([k, v]) => [k, round(v, 3)])),
    long_score,
    short_score,
    dir: round(dir, 3),
    //: Share of the composite's weight that was actually readable (0..1). A
    //: consumer that wants to aggregate `dir` across coins needs this to tell
    //: a fully-scored coin from a barely-scored one; without it, "dir is a
    //: number" is the only signal and every partial read looks complete.
    dir_coverage: round(readWeight, 3),
  };
}

/**
 * buildStrengthMap(tickers, prevOi, limit) → { coins, at, count }.
 *   tickers: Bitget mix tickers array.
 *   prevOi:  { [symbol]: oi_usd } from the previous poll (for ΔOI), optional.
 *   limit:   keep the top-N by USDT volume (the liquid, plottable universe).
 * Returns coins sorted by volume desc, plus a fresh { [symbol]: oi_usd } map the
 * caller stores as the next poll's `prevOi`.
 */
function buildStrengthMap(tickers, prevOi, limit) {
  const list = Array.isArray(tickers) ? tickers : [];
  const scored = [];
  const oiSnapshot = {};
  for (const t of list) {
    const s = scoreTicker(t, prevOi && prevOi[t && t.symbol]);
    if (!s) continue;
    // An unreadable volume is not a zero-volume coin, but both are dropped:
    // volume is the liquidity filter AND the sort key, so a coin whose volume
    // could not be read has no defensible place in a top-N by volume.
    if (s.volume_usd === null || s.volume_usd <= 0) continue;
    // Only a READ open interest becomes the next poll's baseline. Recording a
    // null would make the following poll compute a delta against nothing.
    if (s.oi_usd !== null) oiSnapshot[s.symbol] = s.oi_usd;
    scored.push(s);
  }
  scored.sort((a, b) => b.volume_usd - a.volume_usd);
  const coins = (limit && limit > 0) ? scored.slice(0, limit) : scored;
  return { coins, oiSnapshot, count: coins.length };
}

module.exports = { buildStrengthMap, scoreTicker, WEIGHTS };
