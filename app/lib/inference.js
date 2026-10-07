'use strict';
/**
 * The JS twin of the two interval functions a setup cell is allowed to use.
 *
 * C11's module (`bot/utils/inference.py`) is not on this tree. The plan names
 * this file as its twin for the scoreboard. Until that module exists, the
 * numbers come from the functions the rest of the tree already pins:
 *
 *   hit rate  — `bot.learning.readiness.wilson_lower_bound`, both ends the
 *               way `bot.backtest.parity._wilson` builds them (the upper end
 *               is the lower end of the complement)
 *   mean R    — `bot.core.shadow_book.mean_r_interval`, the normal interval
 *               on a signed R, which is the instrument `poc_retest_record`
 *               already asks for a setup
 *
 * A second formula is not written here. The cross-runtime test feeds both
 * sides the same inputs and requires the same outputs.
 *
 * An unreadable input is null. It is not a zero-width interval at 0, and it
 * is not the 0.0 `wilson_lower_bound` returns for an empty sample so that a
 * gate comparison fails closed. That 0.0 is a refusal to clear a bar. On a
 * scoreboard it would read as a measurement.
 */

const Z = 1.96;

/**
 * Lower end of the Wilson score interval. The arithmetic of
 * `wilson_lower_bound`, including the exact-zero exit at p == 0.
 *
 * `n <= 0` returns 0, matching the Python gate. Callers that publish a
 * reading use `wilsonInterval`, which returns null instead.
 */
function wilsonLowerBound(successes, n, z) {
  const zz = z == null ? Z : z;
  if (!(n > 0) || !Number.isFinite(n) || !Number.isFinite(successes) || !Number.isFinite(zz)) {
    return 0;
  }
  const p = Math.max(0, Math.min(1, successes / n));
  if (p <= 0) return 0;
  const z2 = zz * zz;
  const centre = p + z2 / (2 * n);
  const margin = zz * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n));
  return (centre - margin) / (1 + z2 / n);
}

function readableCount(successes, n) {
  return Number.isFinite(successes) && Number.isFinite(n)
    && n >= 1 && successes >= 0 && successes <= n;
}

/**
 * Both ends of the Wilson interval, or null when the rate cannot be read.
 *
 * Null covers an empty sample, a non-numeric count, and a success count
 * outside 0..n. Those are not a bound of 0.
 */
function wilsonInterval(successes, n, z) {
  if (!readableCount(successes, n)) return null;
  const lo = wilsonLowerBound(successes, n, z);
  const hi = 1 - wilsonLowerBound(n - successes, n, z);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  return [lo, hi];
}

/**
 * Sample moments `mean_r_interval` uses, or null when they will not resolve.
 */
function meanMoments(n, sumR, sumR2) {
  if (!(n >= 2) || !Number.isFinite(n) || !Number.isFinite(sumR) || !Number.isFinite(sumR2)) {
    return null;
  }
  const mean = sumR / n;
  if (!Number.isFinite(mean)) return null;
  const raw = (sumR2 - n * mean * mean) / (n - 1);
  if (!Number.isFinite(raw)) return null;
  // A tiny negative is catastrophic cancellation, the same clamp as
  // `mean_r_interval`. It is not a measured variance of zero from no data.
  const variance = Math.max(0, raw);
  const se = Math.sqrt(variance / n);
  if (!Number.isFinite(se)) return null;
  return { mean, se };
}

/**
 * Whether `a` (finite, > 0) lies EXACTLY halfway between two multiples of
 * 10^-digits, in its binary value. `a` is M * 2^E exactly; a * 2 * 10^d is
 * an odd integer exactly when E is negative and the power of two left over
 * after M's own factors of two and the d + 1 of 2 * 10^d is none.
 */
function exactTie(a, digits) {
  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, a);
  const hi = view.getUint32(0);
  const lo = view.getUint32(4);
  const field = (hi >>> 20) & 0x7ff;
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let exp;
  if (field === 0) exp = -1074;
  else { mant |= 1n << 52n; exp = field - 1075; }
  if (mant === 0n || exp >= 0) return false;
  let twos = 0;
  while ((mant & 1n) === 0n) { mant >>= 1n; twos += 1; }
  return twos + digits + 1 === -exp;
}

/**
 * Python's `round(x, digits)` for a float. It rounds the EXACT binary value,
 * and an exact tie to even. This scaled by 10^digits in floating point
 * first, which is not the value Python rounds: 0.00375 is 0.0037499999…
 * exactly and Python answers 0.0037, while 0.00375 * 10000 is 37.5 and this
 * answered 0.0038, so an interval end printed one way here and the other
 * way by the bot. `toFixed` is specified on the exact value too (and picks
 * the larger of a tie), so it is the reading, and only an exact binary tie
 * is moved to the even neighbour.
 */
function pyRound(x, digits) {
  if (!Number.isFinite(x)) return null;
  const a = Math.abs(x);
  if (a >= 1e21) return x;
  let text = a.toFixed(digits);
  if (a > 0 && exactTie(a, digits)) {
    const units = BigInt(text.replace('.', ''));
    if (units % 2n === 1n) {
      const even = (units - 1n).toString().padStart(digits + 1, '0');
      text = digits > 0 ? `${even.slice(0, -digits)}.${even.slice(-digits)}` : even;
    }
  }
  const out = Number(text);
  return x < 0 ? -out : out;
}

/**
 * The 95% interval on the mean, or null below two samples or an unreadable
 * accumulator. Twin of `shadow_book.mean_r_interval`, including the 4-decimal
 * rounding of each end.
 */
function meanRInterval(n, sumR, sumR2, z) {
  const m = meanMoments(n, sumR, sumR2);
  if (!m) return null;
  const zz = z == null ? Z : z;
  if (!Number.isFinite(zz)) return null;
  const margin = zz * m.se;
  const lo = pyRound(m.mean - margin, 4);
  const hi = pyRound(m.mean + margin, 4);
  if (lo == null || hi == null) return null;
  return [lo, hi];
}

/**
 * Abramowitz and Stegun 7.1.26. The p-value is the two-sided normal test
 * on the same mean and standard error as `meanRInterval` — the dual of that
 * interval, not a second one. Node on this tree has no `Math.erf`.
 */
function erf(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return sign * y;
}

function normalCdf(z) {
  if (!Number.isFinite(z)) return null;
  return 0.5 * (1 + erf(z / Math.sqrt(2)));
}

/**
 * Two-sided p-value for mean R = 0, or null when the interval cannot be
 * computed. A measured mean of 0 with no spread is p = 1. A non-zero mean
 * with no spread is p = 0, the same false certainty `mean_r_interval` reports
 * as a zero-width interval at that mean — not at 0.
 */
function meanRPValue(n, sumR, sumR2) {
  const m = meanMoments(n, sumR, sumR2);
  if (!m) return null;
  if (m.se === 0) return m.mean === 0 ? 1 : 0;
  const z = m.mean / m.se;
  const cdf = normalCdf(Math.abs(z));
  if (cdf == null) return null;
  const p = 2 * (1 - cdf);
  if (!Number.isFinite(p)) return null;
  if (p < 0) return 0;
  if (p > 1) return 1;
  return p;
}

/**
 * Benjamini-Hochberg q-values, aligned with `pValues`.
 *
 * A null or non-finite entry is not a test: it stays null and it is not in
 * the family size. The caller passes the displayed cells only. A cell left
 * out of that list — a missing dimension, a row past the cap — is not adjusted
 * for and does not move the others.
 */
function bhQValues(pValues) {
  const ps = Array.isArray(pValues) ? pValues : [];
  const idx = [];
  for (let i = 0; i < ps.length; i++) {
    if (typeof ps[i] === 'number' && Number.isFinite(ps[i])) idx.push(i);
  }
  const out = ps.map(() => null);
  const m = idx.length;
  if (m === 0) return out;
  idx.sort((a, b) => (ps[a] - ps[b]) || (a - b));
  let running = 1;
  const qs = new Array(m);
  for (let k = m - 1; k >= 0; k--) {
    const rank = k + 1;
    const raw = (ps[idx[k]] * m) / rank;
    if (raw < running) running = raw;
    qs[k] = running;
  }
  for (let k = 0; k < m; k++) {
    let q = qs[k];
    if (q < 0) q = 0;
    else if (q > 1) q = 1;
    out[idx[k]] = q;
  }
  return out;
}

module.exports = {
  wilsonInterval,
  meanRInterval,
  meanRPValue,
  bhQValues,
  pyRound,
};
