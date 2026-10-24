'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Slippage-at-Risk as published by Sepper, arXiv:2603.09164, section 2,
 * read 23 October 2026. Discrete walk of the book from best to worst:
 *
 *   cost += fill * (reference - price)     on the bid side
 *   cost += fill * (price - reference)     on the ask side
 *   S(Q) = cost / (Q * reference)          when the book can fill Q
 *
 * A book shorter than Q is not zero slippage. It is unreadable
 * (the paper returns infinity). Q of zero is unreadable, not zero.
 * A level that cannot be read blanks that side. It is not dropped.
 *
 * N_eff and CR1 are the paper's concentration pair, computed on orders
 * rather than named providers: the published book gives a size and an
 * order count, not a maker. Each order is given an equal share of its
 * level. That stand-in is named in the README.
 *
 * Integer arithmetic at 1e18. Division truncates toward zero.
 */

const WAD = 10n ** 18n;

const PRESENT = Object.freeze({
  slippage: 1,
  nEff: 2,
  cr1: 4,
  oi: 8,
});

function blank(reason) {
  return { value: null, wad: null, reason, unreadable_positions: null };
}

/** A decimal string to 1e18, or null when it is not a plain decimal. */
function parseDec(raw) {
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const t = String(raw).trim();
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  const neg = t.startsWith('-');
  const body = neg ? t.slice(1) : t;
  const parts = body.split('.');
  const whole = parts[0];
  const frac = parts[1] || '';
  if (frac.length > 18) return null;
  const padded = (frac + '000000000000000000').slice(0, 18);
  let v;
  try {
    v = BigInt(whole) * WAD + BigInt(padded);
  } catch {
    return null;
  }
  return neg ? -v : v;
}

/** 1e18 integer to a plain decimal string. No float step. */
function wadToDecimal(wad) {
  if (typeof wad !== 'bigint') return null;
  const neg = wad < 0n;
  const v = neg ? -wad : wad;
  const whole = v / WAD;
  let frac = (v % WAD).toString().padStart(18, '0').replace(/0+$/, '');
  const s = frac ? `${whole.toString()}.${frac}` : whole.toString();
  return neg ? `-${s}` : s;
}

function orderCount(n) {
  if (typeof n === 'number') {
    if (!Number.isInteger(n) || n < 1 || !Number.isSafeInteger(n)) return null;
    return n;
  }
  if (typeof n === 'string' && /^\d+$/.test(n)) {
    const v = Number(n);
    if (!Number.isSafeInteger(v) || v < 1) return null;
    return v;
  }
  return null;
}

/**
 * One side of a book. A bad level blanks the side.
 * Bids sort high to low. Asks sort low to high.
 */
function parseSide(levels, side) {
  if (!Array.isArray(levels) || levels.length === 0) {
    return { levels: null, reason: 'book_unreadable' };
  }
  const out = [];
  for (const lvl of levels) {
    const px = parseDec(lvl && lvl.px);
    const sz = parseDec(lvl && lvl.sz);
    const n = orderCount(lvl && lvl.n);
    if (px == null || sz == null || sz <= 0n || n == null) {
      return { levels: null, reason: 'bad_level' };
    }
    out.push({ px, sz, n });
  }
  out.sort((a, b) => {
    if (a.px === b.px) return 0;
    if (side === 'bid') return a.px > b.px ? -1 : 1;
    return a.px < b.px ? -1 : 1;
  });
  return { levels: out, reason: null };
}

function walkSide(levels, mid, q, side) {
  if (q == null) return blank('q_is_zero');
  if (mid == null || mid === 0n) return blank('book_unreadable');
  let remaining = q;
  let cost = 0n;
  for (const lvl of levels) {
    if (remaining === 0n) break;
    const fill = lvl.sz < remaining ? lvl.sz : remaining;
    const gap = side === 'bid' ? (mid - lvl.px) : (lvl.px - mid);
    cost += fill * gap / WAD;
    remaining -= fill;
  }
  if (remaining > 0n) return blank('book_shorter_than_q');
  const den = q * mid / WAD;
  if (den === 0n) return blank('q_is_zero');
  const wad = cost * WAD / den;
  return { value: wadToDecimal(wad), wad, reason: null, unreadable_positions: null };
}

/**
 * Both sides, or neither. A missing mid is not a one-sided slippage.
 * `q` is a wad quantity. `qReason` is set when Q itself could not be read.
 */
function slippage(book, q, qReason) {
  const bids = parseSide(book && book.bids, 'bid');
  const asks = parseSide(book && book.asks, 'ask');
  if (bids.reason || asks.reason) {
    const reason = bids.reason || asks.reason;
    return { bid: blank(reason), ask: blank(asks.reason || reason), signed: null };
  }
  if (qReason) {
    return { bid: blank(qReason), ask: blank(qReason), signed: null };
  }
  const mid = (bids.levels[0].px + asks.levels[0].px) / 2n;
  const bid = walkSide(bids.levels, mid, q, 'bid');
  const ask = walkSide(asks.levels, mid, q, 'ask');
  let signed = null;
  if (bid.wad != null && ask.wad != null) {
    signed = bid.wad >= ask.wad ? bid.wad : ask.wad;
  }
  return { bid, ask, signed };
}

/** N_eff = 1/HHI and CR1, over equal-split orders on both sides together. */
function concentration(book) {
  const bids = parseSide(book && book.bids, 'bid');
  const asks = parseSide(book && book.asks, 'ask');
  if (bids.reason || asks.reason) {
    const reason = bids.reason || asks.reason;
    return { nEff: blank(reason), cr1: blank(reason) };
  }
  const levels = bids.levels.concat(asks.levels);
  let total = 0n;
  let sumSq = 0n;
  let maxOrder = 0n;
  for (const lvl of levels) {
    total += lvl.sz;
    const n = BigInt(lvl.n);
    const order = lvl.sz / n;
    if (order > maxOrder) maxOrder = order;
    sumSq += (lvl.sz * lvl.sz) / n;
  }
  if (total === 0n || sumSq === 0n) {
    return { nEff: blank('book_unreadable'), cr1: blank('book_unreadable') };
  }
  const nEffWad = total * total * WAD / sumSq;
  const cr1Wad = maxOrder * WAD / total;
  return {
    nEff: { value: wadToDecimal(nEffWad), wad: nEffWad, reason: null, unreadable_positions: null },
    cr1: { value: wadToDecimal(cr1Wad), wad: cr1Wad, reason: null, unreadable_positions: null },
  };
}

function qFromOi(betaWad, oi) {
  if (betaWad == null) return { q: null, reason: 'beta_unreadable' };
  const o = parseDec(oi);
  if (o == null) return { q: null, reason: 'oi_unreadable' };
  const q = betaWad * o / WAD;
  if (q === 0n) return { q: null, reason: 'q_is_zero' };
  return { q, reason: null };
}

/**
 * Share of size whose liquidation sits within `bandBps` of its own
 * reference. A row that cannot be read is counted and left out of both
 * sums. The ratio is then the share among rows that could be read, and
 * the count says so. No readable size is not a zero share.
 */
function oiWithinBand(rows, bandBps) {
  if (!Array.isArray(rows)) return blank('source_unreadable');
  if (bandBps == null) return blank('band_unreadable');
  let num = 0n;
  let den = 0n;
  let unread = 0;
  let readable = 0;
  const band = BigInt(bandBps);
  for (const row of rows) {
    if (!row || row.unreadable) {
      unread += 1;
      continue;
    }
    const size = parseDec(row.size);
    const liq = parseDec(row.liq);
    const ref = parseDec(row.ref);
    if (size == null || liq == null || ref == null || ref === 0n) {
      unread += 1;
      continue;
    }
    readable += 1;
    const abs = size < 0n ? -size : size;
    den += abs;
    const diff = liq > ref ? liq - ref : ref - liq;
    if (diff * 10000n <= ref * band) num += abs;
  }
  if (readable === 0) {
    return {
      value: null,
      wad: null,
      reason: unread > 0 ? 'partial_book' : 'no_positions',
      unreadable_positions: unread,
    };
  }
  if (den === 0n) {
    return { value: null, wad: null, reason: 'no_positions', unreadable_positions: unread };
  }
  const wad = num * WAD / den;
  return {
    value: wadToDecimal(wad),
    wad,
    reason: null,
    unreadable_positions: unread,
  };
}

const LAMBDA = 5n * WAD / 10n;
const MU = 3n * WAD / 10n;
const N_TARGET = 15n * WAD;
const CR1_THRESH = 25n * WAD / 100n;

/**
 * h = 0.5 * max(0, 15/N_eff - 1) + 0.3 * max(0, CR1 - 0.25).
 * N_eff and CR1 must already be account weights. An order-count
 * stand-in is not an input: passing it would understate a spoofed wall.
 */
function haircut(nEffWad, cr1Wad) {
  if (typeof nEffWad !== 'bigint' || typeof cr1Wad !== 'bigint') {
    return { h: null, reason: 'no_account_book' };
  }
  if (nEffWad <= 0n) return { h: null, reason: 'no_account_book' };
  const ratio = N_TARGET * WAD / nEffWad;
  const term1 = ratio > WAD ? ratio - WAD : 0n;
  const term2 = cr1Wad > CR1_THRESH ? cr1Wad - CR1_THRESH : 0n;
  const h = (LAMBDA * term1 + MU * term2) / WAD;
  return { h, reason: null };
}

/** S_adj = S * (1 + h). Either input missing leaves the result missing. */
function adjustSlippage(sWad, hWad) {
  if (typeof sWad !== 'bigint' || typeof hWad !== 'bigint') return null;
  return sWad * (WAD + hWad) / WAD;
}

/**
 * Keep size that has rested at least `tauMs`. A level with no age blanks
 * the book: unknown is not "young", and dropping it as zero would make
 * a spoofed book look thin in the direction that inflates slippage.
 */
function applyPersistence(levels, nowMs, tauMs) {
  if (!Array.isArray(levels)) return { levels: null, reason: 'persistence_unavailable' };
  if (typeof nowMs !== 'number' || typeof tauMs !== 'number') {
    return { levels: null, reason: 'persistence_unavailable' };
  }
  const kept = [];
  for (const lvl of levels) {
    let age = null;
    if (lvl && typeof lvl.restedMs === 'number' && Number.isFinite(lvl.restedMs)) age = lvl.restedMs;
    else if (lvl && typeof lvl.sinceMs === 'number' && Number.isFinite(lvl.sinceMs)) age = nowMs - lvl.sinceMs;
    if (age == null) return { levels: null, reason: 'persistence_unavailable' };
    if (age >= tauMs) kept.push(lvl);
  }
  return { levels: kept, reason: null };
}

/** Weights are account sizes already inside the band. */
function fromAccountSizes(sizes) {
  if (!Array.isArray(sizes) || sizes.length === 0) {
    return { nEff: blank('no_account_book'), cr1: blank('no_account_book'), dominant: null };
  }
  let total = 0n;
  let sumSq = 0n;
  let max = 0n;
  for (const sz of sizes) {
    if (typeof sz !== 'bigint' || sz <= 0n) continue;
    total += sz;
    if (sz > max) max = sz;
    sumSq += sz * sz;
  }
  if (total === 0n || sumSq === 0n) {
    return { nEff: blank('no_account_book'), cr1: blank('no_account_book'), dominant: null };
  }
  const nEffWad = total * total * WAD / sumSq;
  const cr1Wad = max * WAD / total;
  const dominant = cr1Wad > WAD / 2n;
  return {
    nEff: { value: wadToDecimal(nEffWad), wad: nEffWad, reason: null, unreadable_positions: null },
    cr1: { value: wadToDecimal(cr1Wad), wad: cr1Wad, reason: null, unreadable_positions: null },
    dominant,
  };
}

/**
 * Account concentration of persistent resting size within `bandBps` of mid.
 * `levels` are {px, sz, user} on one side or both. No user blanks the result.
 */
function accountConcentration(levels, mid, bandBps) {
  if (typeof mid !== 'bigint' || mid === 0n || bandBps == null) {
    return { nEff: blank('book_unreadable'), cr1: blank('book_unreadable'), dominant: null };
  }
  if (!Array.isArray(levels)) {
    return { nEff: blank('no_account_book'), cr1: blank('no_account_book'), dominant: null };
  }
  const band = BigInt(bandBps);
  const byUser = new Map();
  for (const lvl of levels) {
    const px = typeof lvl.px === 'bigint' ? lvl.px : parseDec(lvl && lvl.px);
    const sz = typeof lvl.sz === 'bigint' ? lvl.sz : parseDec(lvl && lvl.sz);
    const user = lvl && typeof lvl.user === 'string' ? lvl.user : null;
    if (px == null || sz == null || sz <= 0n || !user) {
      return { nEff: blank('no_account_book'), cr1: blank('no_account_book'), dominant: null };
    }
    const diff = px > mid ? px - mid : mid - px;
    if (diff * 10000n > mid * band) continue;
    byUser.set(user, (byUser.get(user) || 0n) + sz);
  }
  return fromAccountSizes([...byUser.values()]);
}

/** Rows with px and sz become a walkable side. Age and user are not required here. */
function asWalkLevels(rows) {
  if (!Array.isArray(rows)) return { levels: null, reason: 'book_unreadable' };
  const levels = [];
  for (const row of rows) {
    const px = typeof row.px === 'bigint' ? row.px : parseDec(row && row.px);
    const sz = typeof row.sz === 'bigint' ? row.sz : parseDec(row && row.sz);
    if (px == null || sz == null || sz <= 0n) return { levels: null, reason: 'bad_level' };
    levels.push({ px: wadToDecimal(px), sz: wadToDecimal(sz), n: 1, pxWad: px, szWad: sz });
  }
  return { levels, reason: null };
}

/** Dimensionless wad ratio to integer basis points. Truncates. Null stays null. */
function bpsFromWad(wad) {
  if (typeof wad !== 'bigint') return null;
  return wad * 10000n / WAD;
}

/** N_eff wad to integer hundredths. */
function hundredths(wad) {
  if (typeof wad !== 'bigint' || wad < 0n) return null;
  return wad * 100n / WAD;
}

/**
 * Sum of |positionValue| whose liquidation sits within `xBps` of ref.
 * Value is a decimal string of a market amount. Missing value or ref
 * blanks the sum. It is not zero.
 */
function liquidationDensity(rows, xBps) {
  if (!Array.isArray(rows)) return { wad: null, reason: 'source_unreadable' };
  if (xBps == null) return { wad: null, reason: 'band_unreadable' };
  const band = BigInt(xBps);
  let sum = 0n;
  let readable = 0;
  for (const row of rows) {
    if (!row || row.unreadable) return { wad: null, reason: 'partial_book' };
    const value = parseDec(row.value);
    const liq = parseDec(row.liq);
    const ref = parseDec(row.ref);
    if (value == null || liq == null || ref == null || ref === 0n) {
      return { wad: null, reason: 'partial_book' };
    }
    readable += 1;
    const abs = value < 0n ? -value : value;
    const diff = liq > ref ? liq - ref : ref - liq;
    if (diff * 10000n <= ref * band) sum += abs;
  }
  if (readable === 0) return { wad: null, reason: 'no_positions' };
  return { wad: sum, reason: null };
}

/** 1e18 wad to a 1e6 integer. Null stays null. Does not clamp. */
function e6FromWad(wad) {
  if (typeof wad !== 'bigint' || wad < 0n) return null;
  return wad * 1000000n / WAD;
}

/** 1e18 price wad to a 1e8 integer. */
function e8FromWad(wad) {
  if (typeof wad !== 'bigint' || wad < 0n) return null;
  return wad * 100000000n / WAD;
}

/**
 * SaR95 is the smallest sample whose rank clears 95 percent.
 * For three markets that is the largest, not the middle.
 * ESaR is the mean of that tail. A missing sample is not a quantile
 * of the markets that happened to be readable.
 */
function systemQuantile(wads) {
  if (!Array.isArray(wads) || wads.length === 0) {
    return { sar: null, esar: null, reason: 'partial_set' };
  }
  for (const w of wads) {
    if (typeof w !== 'bigint') return { sar: null, esar: null, reason: 'partial_set' };
  }
  const sorted = wads.slice().sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const n = sorted.length;
  let index = n - 1;
  for (let k = 0; k < n; k += 1) {
    if ((k + 1) * 100 >= n * 95) {
      index = k;
      break;
    }
  }
  let sum = 0n;
  const tail = n - index;
  for (let k = index; k < n; k += 1) sum += sorted[k];
  return { sar: sorted[index], esar: sum / BigInt(tail), reason: null };
}

module.exports = {
  WAD, PRESENT, LAMBDA, MU, N_TARGET, CR1_THRESH,
  blank, parseDec, wadToDecimal, orderCount,
  parseSide, walkSide, slippage, concentration, qFromOi, oiWithinBand,
  haircut, adjustSlippage, applyPersistence, fromAccountSizes, accountConcentration,
  asWalkLevels, bpsFromWad, hundredths, liquidationDensity, e6FromWad, e8FromWad,
  systemQuantile,
};
