/**
 * The engine telemetry card's four cells, as a PURE function.
 *
 * Production, 2026-07-28. The card read
 *
 *     ENGINE EQUITY $378.88   NET PNL +0.00   WIN RATE 0.0%   OPEN 0
 *
 * while the operator's Telegram /portfolio for the same account reported a
 * realized P&L of -$10.74 over 95 trades at 57%. The payload was
 * `{"equity":378.88,"net_pnl":0,"win_rate":0,"total_trades":0}` — so the two
 * middle figures were not measurements of zero, there was nothing to measure,
 * and they were printed in the same typeface as the equity beside them, which
 * IS real.
 *
 * That was fixed inline with a `_haveTrades(cb)` guard, and the fix was pinned
 * by a test that matched the guard's exact spelling in dashboard.js. Which
 * held the LETTER: `_haveTrades` asks "is there a record", and the question a
 * figure needs answered is "was THIS figure measured". Those came apart the
 * moment the bot learned to send `win_rate: null` for a book of twelve closes
 * none of which carry a recorded P&L — twelve trades passes _haveTrades, and
 * `fmt(null, 1) + '%'` renders `--%`.
 *
 * So the decision moved here, where a test can plant a state and read the
 * card, the way engine-status-model.js already does for the chip. Two rules,
 * and the second is the one that gets dropped:
 *
 *   * a figure that was not measured renders "—" and carries no colour;
 *   * a figure that WAS measured renders, including when it measured zero.
 *     A hidden 0.00 is the same defect facing the other way, and break-even
 *     is a real outcome of a real trade.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EngineCardModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DASH = '—';

  /** Finite number or null. Mirrors app.js's `num`, deliberately. */
  function num(v) {
    if (v === null || v === undefined) return null;
    const f = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(f) ? f : null;
  }

  function money(v) {
    return '$' + v.toLocaleString('en-US',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function signed(v) {
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  }

  /**
   * @param {object|null} cb  the scan payload's `circuit_breaker` block
   * @returns {{equity:{text:string,cls:string},
   *            netPnl:{text:string,cls:string},
   *            winRate:{text:string,cls:string},
   *            open:{text:string,cls:string},
   *            note:string}}
   */
  function engineCardCells(cb) {
    const c = cb || {};
    const equity = num(c.equity);
    const trades = num(c.total_trades);
    const net = num(c.net_pnl);
    const rate = num(c.win_rate);

    // A record has to exist AND the figure has to be readable. Either one
    // missing means the cell has nothing to say, and both were separately
    // observed in production.
    const haveTrades = trades !== null && trades > 0;
    const showNet = haveTrades && net !== null;
    const showRate = haveTrades && rate !== null;

    // Colour is a claim: green says "in profit" as loudly as the digits do,
    // so an unmeasured net gets no accent at all rather than the neutral-
    // looking green that `>= 0` would hand it.
    const netCls = showNet ? (net >= 0 ? 'pos' : 'neg') : '';

    // Why the middle cells are blank, when the reason is knowable. Absent
    // when there is nothing to explain — a caveat on every healthy card is
    // how a real one gets skipped.
    //
    // A PARTIAL window needs it as much as an empty one: "60% of 20" and
    // "60% of the 16 we could price" are different readings, and only this
    // line tells them apart. Writing it as an else-branch of "did a figure go
    // missing" is what left the partial case silent — the figures were both
    // present, so the caveat never fired.
    let note = '';
    const unpriced = num(c.unpriced_trades);
    if (c.record_unreadable) {
      note = 'The closed-trade record could not be read — these are not zeros.';
    } else if (unpriced !== null && unpriced > 0 && haveTrades) {
      note = `${unpriced} of ${trades} closes carry no recorded P&L.`;
    } else if (haveTrades && (!showNet || !showRate)) {
      note = 'No closed trade in this window carries a recorded P&L.';
    } else if (trades !== null && trades === 0) {
      note = 'No closed trades yet.';
    }

    return {
      // Equity is a live balance, not a statistic over trades — it is real
      // with an empty book and must never be gated on the trade count.
      equity: { text: equity === null ? DASH : money(equity), cls: '' },
      netPnl: { text: showNet ? signed(net) : DASH, cls: netCls },
      winRate: { text: showRate ? rate.toFixed(1) + '%' : DASH, cls: '' },
      open: { text: num(c.open_count) === null ? DASH : String(num(c.open_count)), cls: '' },
      note,
    };
  }

  return { engineCardCells, DASH };
}));
