/*
 * RCWinRate — the "what works" rows, where a percentage is a claim about edge.
 *
 *   buildRows(groups, key, opts) -> html
 *
 * The panel this replaces already got the hard half right: a group with nothing
 * resolved shows a muted dash rather than a red 0%, and its own comment records
 * why. What it did not do was distinguish a rate from a SAMPLE.
 *
 *     const cls = wr === null ? 'muted' : (wr >= 50 ? 'pos' : 'neg');
 *
 * One resolved trade that won is `100%`, in green, ranked above a pattern with
 * 47 trades at 61%. That is the same defect one axis over: `null` was handled
 * and `n = 1` was not, because both the colour and — once bars exist — the bar
 * LENGTH assert a confidence the sample cannot carry.
 *
 * THE FLOOR IS NOT INVENTED HERE. `MIN_RATED = 10` is the threshold
 * `bot/learning/setup_expectancy.py` already uses to decide a setup has been
 * learned at all, ratified in this codebase before this file existed. Reusing
 * it keeps one number meaning one thing; picking a fresh one would mean the
 * dashboard and the learner disagreed about what counts as evidence.
 *
 * Below the floor: the percentage is still SHOWN — hiding a real measurement is
 * its own dishonesty — but it carries no colour, no bar, and says how many
 * trades it rests on. Above it: colour and a bar proportional to the rate.
 *
 * Exposed as window.RCWinRate; module.exports in node so it can be tested.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCWinRate = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Resolved outcomes below which a rate is reported but never ranked.
   *  Same value as SetupExpectancy(min_samples=10). */
  var MIN_RATED = 10;

  /** Rows rendered per column. */
  var MAX_ROWS = 6;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    return (typeof n === 'number' && isFinite(n)) ? n : null;
  }

  /**
   * Classify one group into what may honestly be said about it.
   *
   * Returns `{ rate, n, rated, reason }`:
   *   rate   the measured percentage, or null when nothing resolved
   *   rated  whether it may carry colour and a bar
   *   reason why not, when it may not
   *
   * `is None`, not falsiness, on both fields: a rate of exactly 0 is a real
   * measurement of a pattern that lost every time, and `n` of 0 is a real count
   * of nothing. Treating either as absent would erase the worst result on the
   * board or invent a sample.
   */
  function classify(group, key) {
    var g = group || {};
    var rate = num(g.win_rate);
    var n = num(g.n);
    var label = g[key];
    var out = {
      label: (label === null || label === undefined || label === '') ? '(none)' : String(label),
      rate: rate,
      n: n === null ? null : Math.max(0, Math.round(n)),
      rated: false,
      reason: null,
    };
    if (rate === null) {
      out.reason = 'nothing resolved yet';
      return out;
    }
    if (out.n === null) {
      // A rate with no sample count attached cannot be ranked: the number is
      // real but there is no way to know what it rests on.
      out.reason = 'sample size unknown';
      return out;
    }
    if (out.n < MIN_RATED) {
      out.reason = out.n + ' resolved, under ' + MIN_RATED;
      return out;
    }
    out.rated = true;
    return out;
  }

  /** One row. Colour and bar only when `rated`. */
  function rowHtml(c) {
    var pct = c.rate === null ? null : Math.round(c.rate);
    var cls = !c.rated ? 'wr-unrated' : (pct >= 50 ? 'wr-pos' : 'wr-neg');
    var count = c.n === null ? '' : '<span class="wr-n">×' + c.n + '</span>';
    var value = pct === null ? '—' : pct + '%';

    // The bar is drawn ONLY for a rated group. A 0-width bar for an unmeasured
    // one reads as 0%, and a full-width one for n=1 reads as certainty.
    var bar = c.rated
      ? '<div class="wr-track"><div class="wr-fill ' + cls + '" style="width:'
        + Math.max(0, Math.min(100, pct)) + '%"></div></div>'
      : '';

    var note = c.rated ? '' : '<span class="wr-why">' + esc(c.reason) + '</span>';

    return '<div class="wr-row' + (c.rated ? '' : ' wr-row--unrated') + '">'
      + '<div class="wr-head"><span class="wr-label">' + esc(c.label) + '</span>'
      + count
      + '<b class="wr-val ' + cls + '">' + value + '</b></div>'
      + bar + note
      + '</div>';
  }

  /**
   * Build the rows for one column.
   *
   * RATED GROUPS SORT FIRST, and this is a claim too: a list ordered by
   * percentage alone puts `100% ×1` at the top, which is exactly the ranking
   * the sample floor exists to refuse. Within each band the order is by rate.
   */
  function buildRows(groups, key, opts) {
    opts = opts || {};
    var list = Array.isArray(groups) ? groups : [];
    if (!list.length) return '';
    var rows = list.map(function (g) { return classify(g, key); });
    rows.sort(function (a, b) {
      if (a.rated !== b.rated) return a.rated ? -1 : 1;
      var ar = a.rate === null ? -1 : a.rate;
      var br = b.rate === null ? -1 : b.rate;
      return br - ar;
    });
    return rows.slice(0, opts.max || MAX_ROWS).map(rowHtml).join('');
  }

  /** How many of these groups may honestly be ranked. For a caption. */
  function ratedCount(groups, key) {
    return (Array.isArray(groups) ? groups : [])
      .map(function (g) { return classify(g, key); })
      .filter(function (c) { return c.rated; }).length;
  }

  return { buildRows: buildRows, classify: classify, ratedCount: ratedCount, MIN_RATED: MIN_RATED, MAX_ROWS: MAX_ROWS };
}));
