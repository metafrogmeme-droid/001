/**
 * What a reader is TOLD about a starting-equity basis, as a PURE function.
 *
 * `equity_basis.deriveStartEquity()` does the arithmetic and reports what it
 * could and could not see. This decides what that means on screen, and those
 * are two jobs — the same split as `venue-rows.js`, for the same reason.
 *
 * WHY THIS EXISTS AT ALL. Three routes derive a starting balance by
 * subtracting summed P&L from the latest equity snapshot, and every percentage
 * on the reputation card is a ratio to it. Two things can be wrong with that
 * number and neither was visible:
 *
 *   * A CLOSE WITH NO RECORDED P&L. The account's real equity already contains
 *     whatever that trade did; the subtraction counts it as zero. The basis is
 *     then wrong by exactly the amount nobody could read, and so is every
 *     percentage derived from it. Not fixable — the number is genuinely
 *     unknown — which is precisely why it has to be said.
 *   * NO EQUITY SNAPSHOT AT ALL. The basis falls back to a flat default.
 *     A default presented as a measurement is the defect this repo names most
 *     often, one layer up: the ratio is to a figure nobody observed.
 *
 * The first version of this work put `basis` on three JSON payloads and
 * rendered it nowhere, which is #999 exactly — present, correct, unreachable,
 * and indistinguishable from not having been built.
 *
 * EMPTY ON A CLEAN WINDOW. A caveat printed every time is how a real one gets
 * skipped. Same rule as `VenueRows.venueFootnote`.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BasisNote = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function int(v) {
    const n = Number(v);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  }

  /**
   * @param {object|null} coverage `deriveStartEquity()` output, or the
   *   dollar-free `coverage` subset the reputation route returns. Null or
   *   malformed input yields '' — an unreadable coverage report is not a
   *   report that the coverage was fine, but this line is a CAVEAT, and a
   *   caveat invented from nothing is its own kind of false claim. The
   *   panel's own numbers carry their own null handling.
   * @returns {string} plain text, or '' when there is nothing to say.
   */
  function coverageNote(coverage) {
    if (!coverage || typeof coverage !== 'object') return '';
    const unpriced = int(coverage.unpriced_trades);
    const scored = int(coverage.scored_trades);
    const defaulted = coverage.basis_source === 'default';

    const parts = [];
    if (defaulted) {
      parts.push('No equity snapshot has been recorded yet, so these '
        + 'percentages are ratios to a default starting balance, not to a '
        + 'measured one.');
    }
    if (unpriced) {
      const total = scored === null ? null : scored + unpriced;
      const of = total === null ? '' : ` of ${total}`;
      parts.push(`${unpriced}${of} closed trade${unpriced === 1 ? '' : 's'} `
        + `had no recorded P&L. The starting balance is estimated by that much, `
        + `and every percentage here inherits it.`);
    }
    return parts.join(' ');
  }

  /**
   * True when the panel should mark its percentages as estimates.
   *
   * Reads the flag the route computed rather than re-deriving it, so the API
   * and the screen cannot disagree about whether a number is measured — the
   * divergence this repo has already paid for once, in two copies of an auth
   * classifier. Falls back to the counts only when the flag is absent.
   */
  function isEstimate(coverage) {
    if (!coverage || typeof coverage !== 'object') return false;
    if (typeof coverage.basis_is_estimate === 'boolean') {
      return coverage.basis_is_estimate;
    }
    return coverage.basis_source === 'default' || !!int(coverage.unpriced_trades);
  }

  return { coverageNote, isEstimate };
}));
