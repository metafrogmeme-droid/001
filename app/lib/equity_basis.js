/**
 * Capital-basis-aware equity series helpers (pure, shared).
 *
 * An equity-snapshot series can contain steps that trading cannot explain —
 * deposits, withdrawals, or a capital-base switch (paper $10,000 history
 * followed by a live account holding a few hundred dollars). Measuring
 * drawdown across such a step, or drawing the raw series, reports the
 * capital event as a trading loss ("98.7% drawdown", a cliff chart).
 *
 * Used by the public track record (routes/track.js) and the per-user
 * portfolio equity curve (routes/trades.js).
 */

function round2(v) { return Math.round(v * 100) / 100; }

/** A finite number, or null. Absent, NULL and non-numeric are all unknown. */
function num(v) {
  if (v === null || v === undefined) return null;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

// Largest peak-to-trough drop over an equity series, as a % of the peak.
function maxDrawdownPct(curve) {
  let peak = -Infinity, maxDd = 0;
  for (const p of curve) {
    peak = Math.max(peak, p.equity);
    if (peak > 0) maxDd = Math.max(maxDd, (peak - p.equity) / peak * 100);
  }
  return round2(maxDd);
}

/**
 * Split a chronological snapshot series ([{t, equity}]) wherever the equity
 * step is NOT explained by realised PnL closed between the two snapshots.
 * A step is a capital event when the unexplained portion exceeds both 30%
 * of the prior equity and $25 (the floor keeps tiny paper accounts from
 * splitting on noise; unrealised swings stay well under 30% at the
 * engine's position sizing).
 */
function segmentByCapitalEvents(curve, trades) {
  if (!curve.length) return [];
  // `trades.pnl` is DECIMAL(14,2) and NULLABLE — an unpriced close is a real,
  // expected state (routes/sync.js reports `unpriced_trades` for exactly it).
  // The null is KEPT here rather than folded to 0, because the difference
  // decides whether a loss is counted.
  const closes = (trades || [])
    .map(t => ({ t: new Date(t.closed_at).getTime(), pnl: num(t.pnl) }))
    .filter(c => isFinite(c.t));
  const segments = [[curve[0]]];
  for (let i = 1; i < curve.length; i++) {
    const prev = curve[i - 1], cur = curve[i];
    const window = closes.filter(c => c.t > prev.t && c.t <= cur.t);
    const pnlBetween = window
      .reduce((a, c) => a + (c.pnl === null ? 0 : c.pnl), 0);
    // `parseFloat(t.pnl) || 0` made an unreadable close contribute nothing, so
    // the equity it really moved showed up as UNEXPLAINED — and a step big
    // enough to cross the threshold was then classified a deposit or a
    // withdrawal and SPLIT OUT. `segmentedMaxDrawdownPct` measures only within
    // segments, so a genuine trading loss disappeared from the drawdown.
    //
    // On a risk metric the reassuring direction is the dangerous one. When a
    // close in the window could not be priced, this cannot tell a capital
    // event from a trade, so it does not claim one: the step stays in the
    // segment and the drawdown keeps it. Over-reporting risk is survivable;
    // hiding a real loss behind a fabricated deposit is not.
    const unreadableInWindow = window.some(c => c.pnl === null);
    const unexplained = Math.abs((cur.equity - prev.equity) - pnlBetween);
    const capitalEvent = !unreadableInWindow
      && prev.equity > 0
      && unexplained > Math.max(prev.equity * 0.30, 25);
    if (capitalEvent) segments.push([cur]);
    else segments[segments.length - 1].push(cur);
  }
  return segments;
}

// Max drawdown measured only within consistent-capital segments.
function segmentedMaxDrawdownPct(curve, trades) {
  const segments = segmentByCapitalEvents(curve, trades);
  let maxDd = 0;
  for (const seg of segments) {
    if (seg.length >= 2) maxDd = Math.max(maxDd, maxDrawdownPct(seg));
  }
  return round2(maxDd);
}

module.exports = { maxDrawdownPct, segmentByCapitalEvents, segmentedMaxDrawdownPct };
