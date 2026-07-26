'use strict';
/**
 * Discipline read — what a trader's own recorded closes say about their exit
 * habits. The Arena spent today growing a full exit curriculum (stops, edits,
 * trailing, and a distinct reason on every close); this is the mirror: did
 * the plan close your trades, or did panic and liquidation?
 *
 * Honest by construction:
 *   - Derived ONLY from recorded closes — the same rows the history table
 *     shows. Nothing is asked, sampled or estimated.
 *   - PRIVATE surface only: this reads a person's habits back to them. It is
 *     never published, ranked or compared (§4 — and taste).
 *   - A READ, not a verdict. The levels are named for what the data shows
 *     (planned / mixed / improvised), never "good trader / bad trader" — the
 *     same heuristic-flags-not-verdicts doctrine the safety reads follow.
 *   - Below MIN_CLOSES the read declines to exist. Five closes is noise;
 *     grading noise would be invention.
 *
 * Pure: rows in, read out. Dual-consumed by the route and the tests.
 */

const MIN_CLOSES = 5;

// A close is PLANNED when a pre-set exit did the closing: target, stop, or
// trailing stop. Manual closes are improvised (sometimes rightly!), and a
// liquidation is the market closing what the trader would not.
const PLANNED = new Set(['tp', 'sl', 'trail']);

function computeDiscipline(trades) {
  const rows = (Array.isArray(trades) ? trades : [])
    .filter((t) => t && t.reason != null);
  const n = rows.length;
  if (n < MIN_CLOSES) {
    return { enough: false, closes: n, min_closes: MIN_CLOSES };
  }

  const by = { tp: 0, sl: 0, trail: 0, manual: 0, liquidated: 0, other: 0 };
  let planned = 0, plannedWins = 0, manualWins = 0, manualN = 0;
  for (const t of rows) {
    const r = String(t.reason);
    if (by[r] == null) by.other += 1; else by[r] += 1;
    const win = (parseFloat(t.pnl) || 0) > 0;
    if (PLANNED.has(r)) { planned += 1; if (win) plannedWins += 1; }
    if (r === 'manual') { manualN += 1; if (win) manualWins += 1; }
  }

  const pct = (a, b) => (b > 0 ? Math.round(a / b * 1000) / 10 : null);
  const plannedRate = pct(planned, n);
  const liqRate = pct(by.liquidated, n);

  // Level thresholds, stated so they can be argued with: half of closes
  // planned (and no liquidation habit) reads as planned; a quarter as mixed;
  // below that, improvised. A liquidation rate over 20% overrides to
  // improvised — the market keeps closing these trades, whatever else is true.
  let level;
  if (liqRate != null && liqRate >= 20) level = 'improvised';
  else if (plannedRate >= 50) level = 'planned';
  else if (plannedRate >= 25) level = 'mixed';
  else level = 'improvised';

  return {
    enough: true,
    closes: n,
    by,                                     // counts per close reason
    planned_rate_pct: plannedRate,          // % of closes a pre-set exit made
    liquidation_rate_pct: liqRate,
    // The comparison that teaches: do your planned exits win more often than
    // your improvised ones? null when either side has no sample — a
    // comparison with nothing on one side is not a comparison.
    win_rate_planned_pct: planned > 0 ? pct(plannedWins, planned) : null,
    win_rate_manual_pct: manualN > 0 ? pct(manualWins, manualN) : null,
    level,
  };
}

module.exports = { computeDiscipline, MIN_CLOSES, PLANNED };
