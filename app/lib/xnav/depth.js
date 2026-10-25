'use strict';
/**
 * Exit cost of one position against a resting book.
 *
 * The filled part uses the SaR walk's cost step (app/lib/sar/metrics.js
 * walkSide): fill * gap / WAD, gap measured from the mid. A book that
 * cannot fill the size is not zero slippage and not an unreadable S(Q).
 * The remainder is priced at the last visible level and capped at that
 * remainder's notional. A crossed book, where the visible walk would
 * credit the trader, is unreadable.
 */

const sar = require('../sar/metrics');
const { loadPlaceholders } = require('./config');

const WAD = sar.WAD;
const ORDER = ['full', '4', '3', '2'];

function blank(reason) {
  return {
    readable: false,
    reason,
    costWad: null,
    haircutBps: null,
    unfilledWad: null,
    filledWad: null,
    notionalWad: null,
    fullyFilled: null,
    midWad: null,
  };
}

function exitSideOf(side) {
  if (side === 'long' || side === 'L' || side === 'bid') return 'bid';
  if (side === 'short' || side === 'S' || side === 'ask') return 'ask';
  return null;
}

function exitCost(book, qtyRaw, side, midOverride) {
  const bids = sar.parseSide(book && book.bids, 'bid');
  const asks = sar.parseSide(book && book.asks, 'ask');
  if (bids.reason || asks.reason) return blank(bids.reason || asks.reason);
  const exitSide = exitSideOf(side);
  if (!exitSide) return blank('side_unreadable');
  const q = typeof qtyRaw === 'bigint' ? qtyRaw : sar.parseDec(String(qtyRaw));
  if (q == null || q <= 0n) return blank('q_unreadable');
  const mid = typeof midOverride === 'bigint'
    ? midOverride
    : (bids.levels[0].px + asks.levels[0].px) / 2n;
  if (mid <= 0n) return blank('book_unreadable');
  const levels = exitSide === 'bid' ? bids.levels : asks.levels;
  let remaining = q;
  let cost = 0n;
  let last = null;
  for (const lvl of levels) {
    if (remaining === 0n) break;
    const fill = lvl.sz < remaining ? lvl.sz : remaining;
    const gap = exitSide === 'bid' ? (mid - lvl.px) : (lvl.px - mid);
    cost += fill * gap / WAD;
    remaining -= fill;
    last = lvl.px;
  }
  if (cost < 0n) return blank('crossed_book');
  const notional = q * mid / WAD;
  if (notional <= 0n) return blank('q_unreadable');
  let penalty = 0n;
  if (remaining > 0n) {
    if (last == null) return blank('book_unreadable');
    const absGap = mid > last ? mid - last : last - mid;
    const raw = remaining * absGap / WAD;
    const unfilledNotional = remaining * mid / WAD;
    penalty = raw < unfilledNotional ? raw : unfilledNotional;
  }
  let total = cost + penalty;
  if (total > notional) total = notional;
  return {
    readable: true,
    reason: remaining > 0n ? 'unfilled_remainder' : null,
    costWad: total,
    haircutBps: total * 10000n / notional,
    unfilledWad: remaining,
    filledWad: q - remaining,
    notionalWad: notional,
    fullyFilled: remaining === 0n,
    midWad: mid,
  };
}

function _shift(px, shiftBps, adverseUp) {
  const shift = BigInt(shiftBps);
  if (adverseUp) return px * (10000n + shift) / 10000n;
  const moved = px * (10000n - shift) / 10000n;
  return moved > 0n ? moved : 0n;
}

function stressBook(book, side, table) {
  const ph = table || loadPlaceholders();
  const exitSide = exitSideOf(side);
  if (!exitSide) return null;
  const num = BigInt(ph.stress_depth_num);
  const den = BigInt(ph.stress_depth_den);
  if (den <= 0n || num < 0n) return null;
  const shift = ph.stress_shift_bps;
  function mapSide(levels, adverse) {
    if (!Array.isArray(levels)) return null;
    const out = [];
    for (const lvl of levels) {
      const px = sar.parseDec(lvl && lvl.px);
      const sz = sar.parseDec(lvl && lvl.sz);
      if (px == null || sz == null || sz <= 0n) return null;
      const sized = sz * num / den;
      if (sized <= 0n) continue;
      const moved = adverse ? _shift(px, shift, exitSide === 'ask') : px;
      if (moved <= 0n) return null;
      const orders = lvl.n == null ? 1 : lvl.n;
      out.push({ px: sar.wadToDecimal(moved), sz: sar.wadToDecimal(sized), n: orders });
    }
    return out;
  }
  const bids = mapSide(book && book.bids, exitSide === 'bid');
  const asks = mapSide(book && book.asks, exitSide === 'ask');
  if (!bids || !asks) return null;
  return { bids, asks };
}

function stressCost(book, qty, side, table) {
  const base = exitCost(book, qty, side);
  if (!base.readable) return blank(base.reason);
  const stressed = stressBook(book, side, table);
  if (!stressed) return blank('stress_unreadable');
  const next = exitCost(stressed, qty, side, base.midWad);
  if (!next.readable) return next;
  return next;
}

function choosePrecision(readings) {
  if (!Array.isArray(readings)) return blank('precision_missing');
  const by = new Map();
  for (const row of readings) {
    if (row && row.precision != null) by.set(String(row.precision), row);
  }
  for (const name of ORDER) {
    if (!by.has(name)) return { ...blank('precision_missing'), chosen: null };
  }
  for (const name of ORDER) {
    const row = by.get(name);
    if (row.readable === true && row.fullyFilled === true) {
      return { ...row, chosen: name, reason: null, readable: true };
    }
  }
  let best = null;
  for (const name of ORDER) {
    const row = by.get(name);
    if (row.readable !== true || typeof row.filledWad !== 'bigint') continue;
    if (!best || row.filledWad > best.filledWad) best = { ...row, chosen: name };
  }
  if (!best) return { ...blank('book_unreadable'), chosen: null };
  return { ...best, readable: true, reason: 'unfilled_remainder' };
}

function medianBps(values) {
  if (!Array.isArray(values) || values.length === 0) {
    return { readable: false, reason: 'no_precisions', bps: null };
  }
  for (const value of values) {
    if (typeof value !== 'bigint') return { readable: false, reason: 'precision_unreadable', bps: null };
  }
  const sorted = values.slice().sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return { readable: true, reason: null, bps: sorted[Math.floor(sorted.length / 2)] };
}

function twapBps(values, window) {
  if (!Number.isInteger(window) || window < 1) {
    return { readable: false, reason: 'window_short', bps: null };
  }
  if (!Array.isArray(values) || values.length !== window) {
    return { readable: false, reason: 'window_short', bps: null };
  }
  let sum = 0n;
  for (const value of values) {
    if (typeof value !== 'bigint') return { readable: false, reason: 'snapshot_unreadable', bps: null };
    sum += value;
  }
  return { readable: true, reason: null, bps: sum / BigInt(window) };
}

function deltaCap(previous, next, cap) {
  if (typeof next !== 'bigint' || typeof cap !== 'bigint' || cap < 0n) {
    return { readable: false, reason: 'unreadable', bps: null, capped: null };
  }
  if (previous == null) return { readable: true, reason: null, bps: next, capped: false };
  if (typeof previous !== 'bigint') {
    return { readable: false, reason: 'previous_unreadable', bps: null, capped: null };
  }
  const diff = next > previous ? next - previous : previous - next;
  if (diff <= cap) return { readable: true, reason: null, bps: next, capped: false };
  const bps = next > previous ? previous + cap : (previous > cap ? previous - cap : 0n);
  return { readable: true, reason: 'delta_capped', bps, capped: true };
}

function measurePosition(qty, side, books, table) {
  const readings = [];
  const src = books && typeof books === 'object' ? books : {};
  for (const precision of ORDER) {
    if (!Object.prototype.hasOwnProperty.call(src, precision) || src[precision] == null) {
      readings.push({ precision, ...blank('precision_missing') });
      continue;
    }
    readings.push({ precision, ...exitCost(src[precision], qty, side) });
  }
  const chosen = choosePrecision(readings);
  let medianUnreadable = false;
  const bps = [];
  for (const row of readings) {
    if (row.readable !== true || typeof row.haircutBps !== 'bigint') medianUnreadable = true;
    else bps.push(row.haircutBps);
  }
  const median = medianUnreadable
    ? { readable: false, reason: 'precision_unreadable', bps: null }
    : medianBps(bps);
  const book = chosen.chosen ? src[chosen.chosen] : null;
  const stress = book ? stressCost(book, qty, side, table) : blank(chosen.reason || 'book_unreadable');
  return { chosen, median, stress, readings };
}

function publishSeries(values, previous, cap, window) {
  const twap = twapBps(values, window);
  if (twap.readable !== true) return { readable: false, reason: twap.reason, bps: null, capped: null };
  const capped = deltaCap(previous, twap.bps, cap);
  return capped;
}

module.exports = {
  WAD, ORDER, blank, exitSideOf, exitCost, stressBook, stressCost,
  choosePrecision, medianBps, twapBps, deltaCap, measurePosition, publishSeries,
};
