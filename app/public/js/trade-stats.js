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
 *
 * LOADABLE IN THE BROWSER TOO (UMD, same wrapper as engine-status-model.js).
 * The dashboard's "Today for you" card computed its own
 * `parseFloat(t.pnl) || 0` over the same nullable column, because it had no
 * way to reach this file — so the rule had four spellings and the fourth was
 * the wrong one. `require()` behaves exactly as before; the browser gets
 * `window.TradeStats`.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TradeStats = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

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

/**
 * Wins, the scorable count, and the rate over SCORED rows — the JS mirror of
 * `bot/utils/win_rate.py`, same three rules and deliberately the same words:
 *
 *   * a row with no readable P&L is scored NEITHER way and leaves both the
 *     numerator and the denominator;
 *   * a recorded 0.0 IS a measurement and stays scored, as a non-win;
 *   * how many were unscorable travels WITH the rate.
 *
 * `rate` is null when nothing could be scored. A 0% win rate claims everything
 * lost; "we could not price any of these" is a different sentence.
 */
function winStats(rows) {
  let wins = 0, losses = 0, breakeven = 0, scored = 0, unscored = 0;
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    if (p === null) { unscored += 1; continue; }
    scored += 1;
    // Three outcomes, counted. `losses = total - wins` folds break-evens and
    // unpriced rows into the loss column, which is how a flat book publishes
    // a losing record. wins + losses + breakeven + unscored === total, and
    // the tests assert that identity.
    if (p > 0) wins += 1;
    else if (p < 0) losses += 1;
    else breakeven += 1;
  }
  return { wins, losses, breakeven, scored, unscored, total: scored + unscored,
           rate: scored > 0 ? wins / scored : null };
}

/**
 * The realized total, or null when nothing could be priced.
 *
 * Null on the EMPTY set too, and that is the point: `[].reduce((a,b)=>a+b, 0)`
 * is 0, and a card printing it says the book is flat when the truth is that
 * there is no book.
 */
function realizedTotal(rows) {
  let total = 0, scored = 0;
  for (const r of rows || []) {
    const p = num(r && r.pnl);
    if (p === null) continue;
    scored += 1;
    total += p;
  }
  return scored > 0 ? total : null;
}

/**
 * The same three rules over a SQL aggregate rather than a row set.
 *
 * `COUNT(*)` counts closed trades; `pnl` is nullable, so the count of rows and
 * the count of PRICED rows are different numbers and only the second may be a
 * denominator. `SUM(pnl)` already skips NULLs in MySQL — the danger is the
 * `COALESCE(SUM(pnl), 0)` wrapped around it, which turns "nothing priceable"
 * into a measured break-even.
 *
 * Expects `{ total, scored, wins, net_pnl }` as returned by the query in
 * routes/sync.js. Kept here, beside the row-set versions, so a reader
 * comparing the two paths sees one rule rather than two spellings of it.
 */
function aggregateStats(row) {
  const total = num(row && row.total) ?? 0;
  const scored = num(row && row.scored) ?? 0;
  const wins = num(row && row.wins) ?? 0;
  const net = num(row && row.net_pnl);
  return {
    total,
    scored,
    unpriced: Math.max(0, total - scored),
    wins,
    net_pnl: scored > 0 ? net : null,
    win_rate: scored > 0 ? (wins / scored) * 100 : null,
  };
}

/**
 * The three things a compact summary card needs, decided here.
 *
 * The rendering is part of the rule, not a detail downstream of it: "the total
 * is null" and "the cell says —" are the same statement, and splitting them is
 * how a correct null ends up printed as `$0.00`. The Python twin does the same
 * — `win_rate.coverage_note` lives beside `win_stats` for exactly this reason.
 *
 * It also gives an inline card a SEAM. The dashboard's "Today for you" row is
 * built inside a six-thousand-line browser script, where the only thing a test
 * could reach was the spelling of the expression — and a mutation pass proved
 * that was not enough: rendering an unmeasured total as `$0.00`, and dropping
 * the coverage note entirely, both survived every test in the suite.
 */
function summaryCells(rows) {
  const ws = winStats(rows);
  const net = realizedTotal(rows);
  return {
    wins: ws.wins,
    losses: ws.losses,
    scored: ws.scored,
    unscored: ws.unscored,
    total: ws.total,
    net,
    // null renders as an absence. A measured 0 renders as `+$0.00`, because
    // break-even is a real outcome and hiding it is the same defect facing the
    // other way.
    netText: net === null
      ? '—' : `${net < 0 ? '-' : '+'}$${Math.abs(net).toFixed(2)}`,
    // Empty on a complete window — a caveat printed every time is how a real
    // one gets skipped.
    coverage: ws.unscored ? `${ws.scored} priced` : '',
  };
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

  return {
    profitFactor, sharpe, coverage, winStats, realizedTotal, aggregateStats,
    summaryCells,
  };
}));
