/**
 * The "By venue" panel's rows, as a PURE function.
 *
 * Phase 1 of multi-venue (`docs/MULTI_VENUE_RISK_SPLIT.md`). `venueBreakdown()`
 * does the arithmetic; this decides what a person is TOLD about it, and those
 * are two different jobs that this repository has repeatedly written as one.
 *
 * WHY THIS IS NOT INLINE IN dashboard.js. Because the panel would then be
 * exactly the thing CLAUDE.md records under #999: built, source-scanned,
 * shipped, and rendering something nobody ever read back. The `pstats` panel
 * five hundred lines up is 40 lines of template literal with six live values in
 * it, and no test can plant a book and ask what it says. This one can.
 *
 * THE CLAIMS THIS PANEL MAKES, each of which is a lie if the data is thinner
 * than the rendering:
 *
 *   * A COLOUR. Green beside a venue says "you are up there" as loudly as the
 *     number does. A venue whose every close was unpriced gets a muted one —
 *     `venueBreakdown` hands back `pnl: 0` for it (nothing summed to nothing),
 *     and painting that green would be the `(x || 0) >= 0` row of CLAUDE.md's
 *     table wearing a different hat.
 *   * A WIN RATE. `win_rate_pct` is null over zero scored trades, and null
 *     renders as an em dash, never `0%`.
 *   * A COMPARISON. Two rows side by side invite reading one against the
 *     other, so a row built on fewer measurements than its trade count says so
 *     on its own line rather than in a footnote under the table.
 *
 * And one claim it makes by OMISSION, which is the reason the single-venue
 * case is spelled out rather than hidden: a panel headed "By venue" showing one
 * row is not obviously "everything happened here" — it reads just as easily as
 * "here is the first of several". It says which.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VenueRows = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DASH = '—';

  function money(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return DASH;
    return `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`;
  }

  /**
   * One venue's cells: what to print, and what colour it has earned.
   *
   * `cls` is '' — NOT 'pos' — whenever nothing was priced, because the zero in
   * `e.pnl` is then the absence of a sum, not a sum that came to zero.
   *
   * @param {object} e one entry from venueBreakdown()
   */
  function venueRow(e) {
    const scored = Number(e && e.scored) || 0;
    const trades = Number(e && e.trades) || 0;
    const unscored = Number(e && e.unscored) || 0;
    const measured = scored > 0;
    const pnl = Number(e && e.pnl);

    return {
      venue: String((e && e.venue) || '').toUpperCase(),
      trades,
      // A P&L over nothing priced is not a P&L. Both the text and the colour
      // have to agree about that or the muted number sits under a green stripe.
      pnl: measured && Number.isFinite(pnl) ? money(pnl) : DASH,
      cls: !measured || !Number.isFinite(pnl) ? '' : (pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : ''),
      winRate: e && e.win_rate_pct != null ? `${Number(e.win_rate_pct).toFixed(1)}%` : DASH,
      // Empty on a complete row. A caveat printed every time is how a real one
      // gets skipped — the same reason `summaryCells.coverage` is empty on a
      // fully-priced window.
      note: unscored > 0
        ? `${scored} of ${trades} priced` : '',
    };
  }

  /**
   * The line under the table. Returns '' when there is nothing worth saying.
   *
   * @param {Array<object>} rows venueBreakdown() output
   * @param {number|null} connected how many venues the user has CONNECTED, or
   *   null when that could not be read. Never inferred from the rows: a venue
   *   with no trades is absent from them by design, so counting rows would make
   *   "connected two, traded on one" indistinguishable from "connected one".
   */
  function venueFootnote(rows, connected) {
    const list = rows || [];
    if (!list.length) return '';
    const c = Number.isFinite(Number(connected)) && connected != null
      ? Number(connected) : null;
    if (list.length === 1) {
      const only = String(list[0].venue || '').toUpperCase();
      const n = c != null ? c - 1 : 0;
      const idle = n > 0
        ? ` ${n} other connected venue${n === 1 ? ' has' : 's have'} not traded, `
          + `so ${n === 1 ? 'it has' : 'they have'} no row.`
        : '';
      return `Every closed trade happened on ${only}.${idle}`;
    }
    return `Results are split by the venue each trade was placed on. `
      + `Totals elsewhere on this page cover all ${list.length}.`;
  }

  return { venueRow, venueFootnote, DASH };
}));
