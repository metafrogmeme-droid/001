'use strict';
/**
 * The borrow curve, in the same numbers the Python model pins.
 *
 * 0.05 + 4.75 * max(0, utilization - 0.8), capped at 1. Utilization is
 * borrowed / supplied on the raw 1e8 totals. The integer quote is a
 * cross-check under 2 bp. The funding baseline is 0.116, and the
 * crossover is that rate inverted through the curve. A stored
 * utilization would be a second answer.
 *
 * Fixed point, SCALE digits. The kink arithmetic on terminating decimals
 * is exact. A missing value stays null. Zero utilization is the base rate.
 */

const SCALE = 60;
const ONE = 10n ** BigInt(SCALE);
const ZERO_N = 0n;

function dec(n) {
  return { k: 'd', n: n };
}

function isDec(x) {
  return !!x && x.k === 'd' && typeof x.n === 'bigint';
}

function parseDec(raw) {
  if (typeof raw !== 'string') return null;
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return null;
  const neg = raw.startsWith('-');
  const body = neg ? raw.slice(1) : raw;
  const parts = body.split('.');
  const frac = parts[1] || '';
  if (frac.length > SCALE) return null;
  let n = BigInt(parts[0] + frac.padEnd(SCALE, '0'));
  if (neg) n = -n;
  return dec(n);
}

function fromInt(n) {
  if (typeof n !== 'bigint') return null;
  return dec(n * ONE);
}

function fmt(x) {
  if (!isDec(x)) return null;
  const neg = x.n < ZERO_N;
  const digits = (neg ? -x.n : x.n).toString().padStart(SCALE + 1, '0');
  const whole = digits.slice(0, digits.length - SCALE);
  const frac = digits.slice(digits.length - SCALE).replace(/0+$/, '');
  const text = frac ? whole + '.' + frac : whole;
  return neg ? '-' + text : text;
}

function add(a, b) { return dec(a.n + b.n); }
function sub(a, b) { return dec(a.n - b.n); }
function mul(a, b) { return dec(a.n * b.n / ONE); }
function div(a, b) {
  if (b.n === ZERO_N) return null;
  return dec(a.n * ONE / b.n);
}
function cmp(a, b) {
  if (a.n < b.n) return -1;
  if (a.n > b.n) return 1;
  return 0;
}
function abs(a) { return dec(a.n < ZERO_N ? -a.n : a.n); }

const BASE = parseDec('0.05');
const KINK = parseDec('0.8');
const SLOPE = parseDec('4.75');
const RATE_CAP = parseDec('1');
const TOLERANCE = parseDec('0.0002');
const FUNDING_BASELINE = parseDec('0.116');
const HOURS = parseDec('8760');
const CAP_HIT = parseDec('0.98');
const SPREAD_CEIL = parseDec('0.40');
const SHOCK_RATE = parseDec('0.525');
const DAY = parseDec('24');
const HUNDRED = parseDec('100');
const ZERO = parseDec('0');

function borrowApy(utilization) {
  if (!isDec(utilization)) return null;
  if (cmp(utilization, ZERO) < 0) return null;
  if (cmp(utilization, KINK) <= 0) return BASE;
  const rate = add(BASE, mul(SLOPE, sub(utilization, KINK)));
  if (cmp(rate, RATE_CAP) > 0) return RATE_CAP;
  return rate;
}

function utilizationOf(supplied, borrowed) {
  if (typeof supplied !== 'bigint' || typeof borrowed !== 'bigint') return null;
  if (supplied <= ZERO_N || borrowed < ZERO_N) return null;
  return dec(borrowed * ONE / supplied);
}

function expDec(x) {
  if (!isDec(x) || cmp(x, ZERO) < 0) return null;
  let sum = ONE;
  let term = ONE;
  for (let i = 1n; i <= 100n; i++) {
    term = term * x.n / ONE / i;
    if (term === ZERO_N) break;
    sum += term;
  }
  return dec(sum);
}

function borrowApyMatchingFunding(funding) {
  if (!isDec(funding) || cmp(funding, ZERO) < 0) return null;
  const e = expDec(funding);
  if (!e) return null;
  return sub(e, fromInt(1n));
}

function utilizationForBorrowApy(apy) {
  if (!isDec(apy)) return null;
  if (cmp(apy, BASE) < 0) return null;
  if (cmp(apy, BASE) === 0) return KINK;
  if (cmp(apy, RATE_CAP) > 0) return null;
  return add(KINK, div(sub(apy, BASE), SLOPE));
}

function baselineCrossover() {
  return utilizationForBorrowApy(FUNDING_BASELINE);
}

function crossoverUtilization(funding) {
  return utilizationForBorrowApy(borrowApyMatchingFunding(funding));
}

function quoteMatches(utilization, rateBps) {
  if (typeof rateBps !== 'bigint') return false;
  if (rateBps < ZERO_N || rateBps > 10000n) return false;
  const quoted = div(fromInt(rateBps), fromInt(10000n));
  const expected = borrowApy(utilization);
  if (!quoted || !expected) return false;
  const gap = abs(sub(expected, quoted));
  return cmp(gap, TOLERANCE) < 0;
}

function trailingMean(rates) {
  if (!Array.isArray(rates) || rates.length < 6) return null;
  const window = rates.slice(rates.length - 6);
  let sum = ZERO;
  for (const item of window) {
    if (!isDec(item)) return null;
    sum = add(sum, item);
  }
  return div(sum, fromInt(6n));
}

function chooseSettlement(precompileRate, feedRate) {
  const pre = isDec(precompileRate) ? precompileRate : null;
  const feed = isDec(feedRate) ? feedRate : null;
  if (!pre && !feed) return { rate: null, source: 'none', payoutZero: true };
  if (!pre) return { rate: feed, source: 'signed_feed', payoutZero: false };
  if (!feed) return { rate: pre, source: 'precompile', payoutZero: false };
  if (cmp(abs(sub(pre, feed)), TOLERANCE) < 0) {
    return { rate: pre, source: 'precompile', payoutZero: false };
  }
  const lower = cmp(pre, feed) <= 0 ? pre : feed;
  return { rate: lower, source: 'lower_of', payoutZero: false };
}

function percentTenth(utilization) {
  if (!isDec(utilization)) return null;
  const num = utilization.n * 1000n;
  let q = num / ONE;
  const rem = num % ONE;
  if (rem * 2n >= ONE) q += 1n;
  const whole = q / 10n;
  const frac = q % 10n;
  return whole.toString() + '.' + frac.toString();
}

module.exports = {
  BASE, KINK, SLOPE, RATE_CAP, TOLERANCE, FUNDING_BASELINE, HOURS, CAP_HIT, SPREAD_CEIL, SHOCK_RATE, DAY, HUNDRED, ZERO,
  isDec, parseDec, fromInt, fmt, add, sub, mul, div, cmp,
  borrowApy, utilizationOf, borrowApyMatchingFunding, utilizationForBorrowApy,
  baselineCrossover, crossoverUtilization, quoteMatches, trailingMean, chooseSettlement, percentTenth,
};
