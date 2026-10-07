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

  /** An integer count, or null. `true` and `1.5` and `''` are not counts. */
  function count(v) {
    return (typeof v === 'number' && Number.isInteger(v)) ? v : null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
    }[c]));
  }

  /**
   * Walk-forward folds. Absent, or a block that is not two integer counts,
   * is unmeasured — not zero folds and not zero profitable.
   * A measured 0 profitable is a real count.
   */
  function foldReading(folds) {
    const empty = {
      text: 'unmeasured', measured: false,
      requested: null, run: null, profitable: null,
    };
    if (folds == null || typeof folds !== 'object') return empty;
    const run = count(folds.run);
    const profitable = count(folds.profitable);
    if (run === null || profitable === null) return empty;
    return {
      text: `${profitable} of ${run} profitable`,
      measured: true,
      requested: count(folds.requested),
      run: run,
      profitable: profitable,
    };
  }

  function foldHtml(folds) {
    const read = foldReading(folds);
    const state = read.measured ? 'measured' : 'unmeasured';
    return '<span data-folds="' + state + '">Folds ' + esc(read.text) + '</span>';
  }

  function discoveryHtml(mark) {
    if (mark !== 'discovery') return '';
    return '<span class="chip" data-mark="discovery">discovery data</span>';
  }

  function tradesText(scorecard) {
    const m = scorecard && scorecard.metrics;
    const n = count(m && m.total_trades);
    return n === null ? DASH : String(n);
  }

  /**
   * `true` is an offer, `false` is withheld, anything else is absent.
   * Absent is not a grant and it is not "profit factor below 1".
   */
  function followOffer(agent) {
    if (!agent || typeof agent !== 'object') return 'absent';
    if (agent.copy_follow === true) return 'offered';
    if (agent.copy_follow === false) return 'withheld';
    return 'absent';
  }

  function withheldText(reason) {
    if (reason === 'below_one') {
      return 'Not offered for follow. Profit factor is below 1, and no eligibility record says this preset survives.';
    }
    if (reason === 'no_verdict') {
      return 'Not offered for follow. No measured verdict is on this preset.';
    }
    if (reason === 'eligibility_unreadable') {
      return 'Not offered for follow. The eligibility record could not be read.';
    }
    if (reason === 'eligibility_refused') {
      return 'Not offered for follow. The eligibility record filed for this preset was refused: '
        + 'it names another preset, or carries a field this build does not read.';
    }
    return 'Not offered for follow.';
  }

  /** The picks panel's line for a followed agent that is now withheld. */
  function withheldPicksText(reason) {
    return withheldText(reason) + ' Its picks are no longer shown or pushed. Unfollow to remove it.';
  }

  function followButtonHtml(agent, loggedIn, following) {
    const offer = followOffer(agent);
    if (offer === 'withheld') {
      const reason = agent && agent.copy_follow_reason;
      const chip = '<span class="chip" data-follow="withheld">' + esc(withheldText(reason)) + '</span>';
      // Still followed from before the withholding: say so, and offer only
      // the unfollow (a follow would be refused). The chip alone hid that
      // the user was subscribed.
      if (loggedIn && following) {
        const wid = agent && agent.id != null ? String(agent.id) : '';
        return chip + ' <button class="btn btn--sm btn--ghost" data-agentunfollow="' + esc(wid)
          + '" data-withheld-following="' + esc(wid) + '" type="button">✓ Following · Unfollow</button>';
      }
      return chip;
    }
    if (offer !== 'offered' || !loggedIn) return '';
    const id = agent && agent.id != null ? String(agent.id) : '';
    const on = !!following;
    return '<button class="btn btn--sm' + (on ? ' btn--ghost' : '')
      + '" data-agentfollow="' + esc(id) + '" type="button">'
      + (on ? '✓ Following' : '+ Follow') + '</button>';
  }

  /**
   * The Lab request that re-runs this card's backtest, or null without one.
   *
   * The card's gate block is the generator's (`scorecard_gates`), so its
   * names are read here as the generator writes them. This lived in
   * dashboard.js and read `ma_target_weight`, `ma_max_gross_leverage` and
   * `ma_utilization`, names no card carries, and no exit multiple at all:
   * ETH MA trend reproduced with no sizing (a book that opens nothing) and
   * Safe Scalper without the ATR stop and target it was measured with. An
   * absent gate is left out of the request, never sent as a zero. That
   * includes the confidence threshold: the Lab reads an absent one as the
   * runner's own default, 0.0, which is what the generator ran.
   */
  function labBody(name, sc) {
    if (!sc || typeof sc !== 'object' || !sc.dataset || !sc.gates || typeof sc.gates !== 'object') return null;
    var g = sc.gates;
    var pick = function (a, b) { return g[a] != null ? g[a] : g[b]; };
    return {
      _agent: name,
      body: {
        dataset: sc.dataset,
        symbols: sc.symbols || [],
        last_bars: sc.bars || 1500,
        confidence_threshold: g.confidence_threshold,
        volume_spike_min: g.volume_spike_min,
        regime_filter: g.regime_filter || '',
        rsi_max: g.rsi_max,
        rsi_min: g.rsi_min,
        direction: g.direction || '',
        ma_fast: g.ma_fast,
        ma_slow: g.ma_slow,
        ma_timeframe: g.ma_timeframe || '',
        ma_symbols: g.ma_symbols || '',
        ma_target_weight: pick('target_weight', 'ma_target_weight'),
        ma_max_gross_leverage: pick('max_gross_leverage', 'ma_max_gross_leverage'),
        ma_utilization: pick('utilization', 'ma_utilization'),
        leverage: g.leverage,
        signal_confidence: g.signal_confidence,
        sl_atr_mult: g.sl_atr_mult,
        tp_atr_mult: g.tp_atr_mult,
      },
    };
  }

  function followLinkHtml(agent) {
    const offer = followOffer(agent);
    if (offer === 'offered') {
      return '<a class="btn btn--sm" data-follow="offered" href="/dashboard#agents">Follow in the app</a>';
    }
    if (offer === 'withheld') {
      const reason = agent && agent.copy_follow_reason;
      return '<span class="chip" data-follow="withheld">' + esc(withheldText(reason)) + '</span>';
    }
    return '';
  }

  return {
    finite: finite,
    readings: readings,
    count: count,
    foldReading: foldReading,
    foldHtml: foldHtml,
    discoveryHtml: discoveryHtml,
    tradesText: tradesText,
    followOffer: followOffer,
    withheldText: withheldText,
    withheldPicksText: withheldPicksText,
    followButtonHtml: followButtonHtml,
    followLinkHtml: followLinkHtml,
    labBody: labBody,
  };
}));
