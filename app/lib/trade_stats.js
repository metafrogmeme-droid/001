'use strict';
/**
 * Profit factor and Sharpe, computed once so two surfaces cannot disagree.
 *
 * THE DEFECT THIS REPLACES
 *
 * `routes/trades.js` (the dashboard) and `routes/track.js` (the public track
 * page) each computed profit factor, and gave different answers for the same
 * account:
 *
 *     trades.js:  grossLosses > 0 ? grossWins / grossLosses
 *                                 : grossWins > 0 ? 999 : 0
 *     track.js:   grossLoss  > 0 ? round2(grossWin / grossLoss) : null
 *
 * An account with wins and no losses reads **999** on the dashboard and **—**
 * on the public page. Nothing in the data is 999; it is a sentinel that
 * renders as a measurement. `track.js` had it right — omit, never invent.
 *
 * Worse at the start: `grossWins > 0 ? 999 : 0` means an account with NO
 * trades reports a profit factor of **0**, the worst reading there is, when
 * the truth is "nothing has closed yet". That is the same shape as the 0%
 * win rate `bot/utils/win_rate.py` exists to eliminate.
 *
 * THE SECOND ONE WAS QUIETER
 *
 *     let sharpe = 0;
 *     const returns = allPnl.map(r => parseFloat(r.pnl) / parseFloat(r.size_usd));
 *     ...
 *     if (std > 0) sharpe = (mean / std) * Math.sqrt(252);
 *
 * One row with `size_usd` of 0 or null poisons the whole series: `x / 0` is
 * Infinity, `parseFloat(null)` is NaN, either one makes `mean` and then `std`
 * NaN, `NaN > 0` is false, and `sharpe` is left at its initial **0**.
 *
 * The account then displays a Sharpe of 0.00 — a specific, terrible-sounding
 * measurement — when the truth is that one row could not be priced. No error,
 * no warning, no null. And `size_usd` being zero or absent is not
 * hypothetical: `bot/utils/portfolio_return.py` had to guard the same case.
 *
 *     A ROW THAT CANNOT BE PRICED LEAVES THE SERIES. IT DOES NOT ZERO IT.
 *
 * Both functions return **null** when there is nothing honest to report. Null
 * renders as "—"; 0 and 999 are claims.
 */

/** A finite float, or null — never NaN, never Infinity. */
function num(v) {
  if (v === null || v === undefined) return null;
  const f = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(f) ? f : null;
}

/**
 * Gross wins over gross losses.
 *
 * null when there are no losses — the ratio is undefined, not enormous, and
 * not zero. A caller wanting to say "no losing trades yet" should say that
 * rather than print a number standing in for it.
 */
function profitFactor(rows) {
  let win = 0, loss = 0, priced = 0;
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    if (p === null) continue;
    priced += 1;
    if (p > 0) win += p;
    else if (p < 0) loss += -p;
  }
  if (!priced || loss <= 0) return null;
  return win / loss;
}

/**
 * Annualised Sharpe over per-trade returns (pnl / margin).
 *
 * Rows without a readable P&L or a positive size leave the series entirely —
 * the same rule win_rate.py applies to closes it cannot score. Keeping them
 * in would either poison the arithmetic (NaN) or drag the mean toward zero,
 * and both state a measurement nobody made.
 *
 * null when fewer than two rows survive, or when every return is identical
 * (zero variance has no defined Sharpe).
 */
function sharpe(rows, { periodsPerYear = 252 } = {}) {
  const returns = [];
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    const s = num(r && r.size_usd);
    if (p === null || s === null || s <= 0) continue;
    returns.push(p / s);
  }
  if (returns.length < 2) return null;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance =
    returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length - 1);
  const std = Math.sqrt(variance);
  // A near-zero std caused by floating-point residuals on identical returns
  // produces a Sharpe in the trillions. Guard with a relative epsilon: if
  // the coefficient of variation is negligible the series is effectively
  // constant and the ratio is undefined.
  const scale = Math.abs(mean) || 1;
  if (!(std / scale > 1e-10)) return null;
  return (mean / std) * Math.sqrt(periodsPerYear);
}

/** How much of the set each figure could actually be computed over. */
function coverage(rows) {
  let priced = 0, sizeable = 0;
  const all = (rows || []).length;
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    const s = num(r && r.size_usd);
    if (p !== null) priced += 1;
    if (p !== null && s !== null && s > 0) sizeable += 1;
  }
  return { total: all, priced, sizeable, unpriced: all - priced };
}

module.exports = { profitFactor, sharpe, coverage };
