/**
 * WHAT BECAME OF A SIGNAL — the words the bot's outcome walk writes, and what
 * the signal panels may say about each.
 *
 * Both producers of the public stream pushed `status: NEW` and nothing ever
 * pushed a second row, so every signal read NEW for good and the stats panel
 * promised "outcomes appear once signals hit target or stop" over a path that
 * did not exist. The bot now walks hourly candles from each signal's
 * publication (bot/core/signal_outcomes.py) and re-sends the row with one of
 * eight words. Five of them are FINAL and only two carry an R, and a panel
 * that knew only WIN / LOSS would have read the other three as unresolved and
 * kept offering a Trade button on a call whose window had closed.
 *
 * A word this page does not know is shown as sent and is NOT actionable: a
 * newer bot's word is more likely to be a new way of ending than a new way of
 * staying open, and offering a trade on a call nobody can read is the loose
 * direction.
 *
 * It is a module rather than a closure in the panel for the reason
 * `dashboard_helpers_are_in_scope.test.js` exists: a renderer reachable only
 * through a DOM event is one no test can drive.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SignalStatusModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const PENDING = ['NEW', 'OPEN'];
  const TERMINAL = ['TARGET', 'STOP', 'AMBIGUOUS', 'EXPIRED', 'NO_EXIT', 'UNSCORED'];

  // [chip class, label]. Only the two words that carry an R get a colour:
  // colour is a claim, and a call that was never filled or ended on a bar the
  // candles cannot order has made none.
  const CHIP = {
    NEW: ['', 'NEW'],
    OPEN: ['', 'ENTRY HIT'],
    TARGET: ['chip--up', '✓ TARGET'],
    STOP: ['chip--down', '✗ STOP'],
    AMBIGUOUS: ['', '? AMBIGUOUS'],
    EXPIRED: ['', 'NOT FILLED'],
    NO_EXIT: ['', 'NO EXIT'],
    UNSCORED: ['', 'UNSCORED'],
  };

  /** The word a row carries, upper-cased; an absent one is NEW. */
  function word(s) {
    const w = s && s.status != null && String(s.status).trim() !== ''
      ? String(s.status).trim().toUpperCase() : 'NEW';
    return w;
  }

  /** `{word, cls, label, final, known}` for one signal row. */
  function status(s) {
    const w = word(s);
    const c = CHIP[w];
    if (!c) return { word: w, cls: '', label: w, final: true, known: false };
    return { word: w, cls: c[0], label: c[1], final: TERMINAL.indexOf(w) >= 0, known: true };
  }

  /**
   * May the panel offer to act on this signal? Only while the call is still
   * pending, and never once an outcome label has arrived (an older server
   * derives `outcome` from the R and sends no status beside it).
   */
  function actionable(s) {
    if (!s) return false;
    const st = status(s);
    return !st.final && (s.outcome === null || s.outcome === undefined);
  }

  /** A finite number, or null. */
  function num(v) {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    const n = Number(v);
    return isFinite(n) ? n : null;
  }

  const OTHER = [
    ['NEW', 'waiting for the entry'],
    ['OPEN', 'entry hit, no exit yet'],
    ['EXPIRED', 'not filled'],
    ['AMBIGUOUS', 'ambiguous bar'],
    ['NO_EXIT', 'no exit in a week'],
    ['UNSCORED', 'unscored'],
  ];

  /**
   * The words besides TARGET and STOP, counted, as one line; printed only
   * where it bites. `null` counts are a read that failed and say so, never a
   * row of zeros.
   */
  function otherLine(byStatus) {
    if (byStatus === null || typeof byStatus !== 'object') {
      return 'The other outcomes could not be counted.';
    }
    const parts = [];
    for (const [w, label] of OTHER) {
      const n = num(byStatus[w]);
      if (n && n > 0) parts.push(n + ' ' + label);
    }
    const other = num(byStatus.other);
    if (other && other > 0) parts.push(other + ' in a word this page does not know');
    return parts.length ? parts.join(' · ') : '';
  }

  const BASIS = 'Each signal is walked on hourly candles from when it was published: '
    + 'reaching the target scores its own reward÷risk in R, the stop scores −1R. '
    + 'Gross of fees — a signal has no size. The win rate is over those two outcomes only.';

  /** The mean R as text, or a dash. */
  function avgR(v) {
    const n = num(v);
    if (n === null) return '—';
    return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(2) + 'R';
  }

  /** Muted unless measured: a mean of 0 is flat, and flat has no colour. */
  function avgCls(v) {
    const n = num(v);
    if (n === null || n === 0) return '';
    return n > 0 ? 'pos' : 'neg';
  }

  /**
   * The stats panel, or null for the panel's empty state. `esc` is the
   * caller's escaper and is required: a renderer that silently stops escaping
   * publishes server text as markup.
   */
  function statsHtml(s, esc) {
    if (typeof esc !== 'function') throw new Error('statsHtml needs an escaper');
    if (!s || typeof s !== 'object') return null;
    const resolved = num(s.resolved) || 0;
    const other = otherLine(s.by_status === undefined ? {} : s.by_status);
    if (!resolved) {
      // Nothing reached its target or stop. Say what IS being tracked, when
      // anything is, rather than an empty state that reads as "no signals".
      if (!other) return null;
      return `<p class="muted small">None has reached its target or stop yet · ${esc(other)}</p>`
        + `<p class="muted small mt-1">${esc(BASIS)}</p>`;
    }
    const wins = num(s.wins) || 0;
    const losses = num(s.losses) || 0;
    const flat = num(s.flat) || 0;
    return `<div class="stat-row">
        <div class="stat"><div class="k">Resolved</div><div class="v">${resolved}</div></div>
        <div class="stat"><div class="k">Win rate</div><div class="v">${num(s.win_rate) !== null ? num(s.win_rate).toFixed(1) + '%' : '—'}</div></div>
        <div class="stat"><div class="k">Avg R (gross)</div><div class="v ${avgCls(s.avg_r)}">${esc(avgR(s.avg_r))}</div></div>
        <div class="stat"><div class="k">Target / Stop</div><div class="v">${wins} / ${losses}${flat ? ` <span class="muted small">/ ${flat} flat</span>` : ''}</div></div>
      </div>`
      + (other ? `<p class="muted small mt-1">${esc(other)}</p>` : '')
      + `<p class="muted small mt-1">${esc(BASIS)}</p>`;
  }

  return {
    PENDING: PENDING, TERMINAL: TERMINAL, CHIP: CHIP,
    word: word, status: status, actionable: actionable,
    otherLine: otherLine, avgR: avgR, avgCls: avgCls, statsHtml: statsHtml, BASIS: BASIS,
  };
}));
