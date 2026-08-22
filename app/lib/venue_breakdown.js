'use strict';
/**
 * Closed trades, grouped by the venue they happened on.
 *
 * Phase 1 of multi-venue (`docs/MULTI_VENUE_RISK_SPLIT.md`). Phase 0 taught the
 * bot's records where a trade happened and the sync now carries it, so this is
 * the first place a person can SEE it.
 *
 * WHY A PURE FUNCTION AND NOT A `GROUP BY` IN THE ROUTE. The arithmetic here is
 * exactly the arithmetic this repository has got wrong most often, and a SQL
 * expression cannot be unit-tested against the rows that break it. `track.js`
 * carries a comment about three public surfaces answering "did this trade win?"
 * three different ways; this is the fourth surface and it is not going to be a
 * fifth answer.
 *
 * THE RULES, which are not negotiable per venue any more than they are overall:
 *
 *   - An UNPRICED close is not break-even and not a loss. `trades.pnl` is
 *     DECIMAL(14,2) with no NOT NULL, so a closed row can carry no recorded
 *     P&L. It is counted as `unscored` and kept out of every rate.
 *   - A win rate over zero scored trades is NOT 0%. It is `null`, because a
 *     rate over no measurements is not a rate — and 0% reads as "this venue
 *     loses everything", which is a claim nobody measured.
 *   - A venue with no trades is ABSENT from the result, not present with
 *     zeroes. A connected venue that has never traded has no record to show,
 *     and a row of zeroes beside a venue that really did trade invites reading
 *     one as the other.
 *
 * That last rule is the reason this exists as its own file: the natural
 * implementation — seed every connected venue at zero, then add — produces a
 * table where "never traded here" and "traded here and broke even" look
 * identical.
 */

/** The outcome of ONE close: 'win' | 'loss' | 'flat' | 'unknown'. */
function outcomeOf(raw) {
  const p = parseFloat(raw);
  if (!Number.isFinite(p)) return 'unknown';
  return p > 0 ? 'win' : p < 0 ? 'loss' : 'flat';
}

/**
 * @param {Array<{venue?: string, pnl?: *}>} rows closed trades
 * @returns {Array<object>} one entry per venue that actually traded, ordered by
 *   trade count descending then venue name, so the ordering is stable and does
 *   not depend on row order.
 */
function venueBreakdown(rows) {
  const by = new Map();
  for (const r of rows || []) {
    // An unlabelled row is not an unknown venue: every trade recorded before
    // venues existed is a Bitget trade, and the column is NOT NULL DEFAULT
    // 'bitget' for the same reason. Inventing an "unknown" bucket here would
    // manufacture a venue nobody traded on.
    const v = String((r && r.venue) || 'bitget').toLowerCase().trim() || 'bitget';
    if (!by.has(v)) {
      by.set(v, { venue: v, trades: 0, wins: 0, losses: 0, flat: 0, unscored: 0, pnl: 0 });
    }
    const e = by.get(v);
    e.trades += 1;
    const o = outcomeOf(r && r.pnl);
    if (o === 'unknown') { e.unscored += 1; continue; }
    e[o === 'win' ? 'wins' : o === 'loss' ? 'losses' : 'flat'] += 1;
    e.pnl += parseFloat(r.pnl);
  }

  return [...by.values()].map((e) => {
    const scored = e.wins + e.losses + e.flat;
    return {
      ...e,
      pnl: Math.round(e.pnl * 100) / 100,
      scored,
      // null, never 0 — see the header. A venue whose every close was unpriced
      // has no win rate, and saying 0% would be a measurement nobody took.
      win_rate_pct: scored > 0 ? Math.round((e.wins / scored) * 10000) / 100 : null,
    };
  }).sort((a, b) => b.trades - a.trades || a.venue.localeCompare(b.venue));
}

module.exports = { venueBreakdown, outcomeOf };
