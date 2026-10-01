/**
 * The six frozen-benchmark readings on a strategy-agent card.
 *
 * One reading of the scorecard metrics, painted by whatever page hosts the
 * card. Colour is NOT decided here. The caller passes `pnlClass` — the helper
 * in app.js — and this file only chooses WHICH number that helper sees:
 *
 *   return          signed percent, break-even 0
 *   profit factor   break-even 1, so the helper sees (pf - 1). A measured 0
 *                   (no gross profit) is a loss, not a missing ratio.
 *   sharpe          signed, break-even 0
 *   max drawdown    a magnitude, percent below peak. Not a signed return, so
 *                   it gets no class: a small drawdown is not "this lost".
 *   win rate        a rate. No verdict colour.
 *   trades          a count. No verdict colour.
 *
 * Unreadable (absent, non-number, non-finite) is an em dash and class ''.
 * A numeric string is not a measurement — the catalogue sends JSON numbers.
 * `PF_UNDEFINED` is not rewritten here: a sentinel that is not on these
 * committed cards stays a number the helper will call a gain, and hiding it
 * would be a second definition of the sentinel.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AgentScorecard = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DASH = '—';

  /** Finite number, or null. `Number('')` is 0, so a string never qualifies. */
  function finite(v) {
    return (typeof v === 'number' && Number.isFinite(v)) ? v : null;
  }

  function signedPct(n) {
    return `${n < 0 ? '' : '+'}${n.toFixed(2)}%`;
  }

  /**
   * @param {object} metrics scorecard.metrics
   * @param {function} pnlClass app.js pnlClass: '' unreadable, 'pos' at or
   *   above its zero, 'neg' below. Called only with a finite number.
   */
  function readings(metrics, pnlClass) {
    const m = metrics || {};
    const ret = finite(m.total_return_pct);
    const pf = finite(m.profit_factor);
    const wr = finite(m.win_rate);
    const dd = finite(m.max_drawdown_pct);
    const sh = finite(m.sharpe_ratio);
    const tr = finite(m.total_trades);
    const tone = (n) => pnlClass(n);
    return {
      // A count below 10 is a thin sample. null means the count was not read,
      // which is not "few trades".
      lowSample: (tr !== null && tr < 10) ? tr : null,
      cells: [
        { key: 'return', label: 'Return',
          text: ret === null ? DASH : signedPct(ret),
          cls: ret === null ? '' : tone(ret) },
        { key: 'profit-factor', label: 'Profit factor',
          text: pf === null ? DASH : pf.toFixed(2),
          cls: pf === null ? '' : tone(pf - 1) },
        { key: 'win-rate', label: 'Win rate',
          text: wr === null ? DASH : `${(wr * 100).toFixed(0)}%`,
          cls: '' },
        { key: 'max-dd', label: 'Max DD',
          text: dd === null ? DASH : `${dd.toFixed(2)}%`,
          cls: '' },
        { key: 'sharpe', label: 'Sharpe',
          text: sh === null ? DASH : sh.toFixed(2),
          cls: sh === null ? '' : tone(sh) },
        { key: 'trades', label: 'Trades',
          text: tr === null ? DASH : String(tr),
          cls: '' },
      ],
    };
  }

  return { finite: finite, readings: readings };
}));
