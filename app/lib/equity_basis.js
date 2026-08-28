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

/**
 * The starting-equity basis, and an honest account of what it rests on.
 *
 * Three routes derived this inline and identically:
 *
 *     const net = rows.reduce((a, r) => a + (parseFloat(r.pnl) || 0), 0);
 *     const startEquity = Math.max(parseFloat(snap[0].equity) - net, 1);
 *
 * The basis was computed over ALL rows while `computeReputation` and
 * `computePerformance` both filter to rows with a readable pnl. So the
 * denominator and the numerator disagreed about which trades exist: the
 * account's real equity already includes whatever an unpriced close did, but
 * `net` counted it as zero, leaving `startEquity` wrong by exactly that
 * amount and every percentage derived from it quietly biased.
 *
 * That part is NOT fixable — the unpriced pnl is genuinely unknown, and no
 * arithmetic recovers it. It is declarable, which is the difference between a
 * number a reader can weigh and one they cannot. `sync.js` already carries
 * `scored_trades`/`unpriced_trades` for the same reason; this reports the
 * same pair plus what it means for the basis.
 *
 * The `10000` fallback is declared too. A book with no equity snapshot has no
 * measured basis at all, and a default presented as one is the same defect a
 * layer up — every percentage computed against it is a ratio to a number
 * nobody observed.
 */
function deriveStartEquity(rows, snapshotEquity) {
  let net = 0, scored = 0, unpriced = 0;
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    if (p === null) { unpriced += 1; continue; }
    scored += 1;
    net += p;
  }
  const snap = num(snapshotEquity);
  const measured = snap !== null;
  return {
    start_equity: measured ? Math.max(snap - net, 1) : 10000,
    // Whether the basis came from a real snapshot or the neutral default.
    basis_source: measured ? 'equity_snapshot' : 'default',
    scored_trades: scored,
    unpriced_trades: unpriced,
    // An estimate whenever something it needed could not be read: an unpriced
    // close whose effect on equity is unknown, or no snapshot at all.
    basis_is_estimate: !measured || unpriced > 0,
  };
}

module.exports = { maxDrawdownPct, segmentByCapitalEvents, segmentedMaxDrawdownPct, deriveStartEquity };
