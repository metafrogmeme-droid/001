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
 * A setup cell is the exception to that colour. It reads "exploratory".
 * The colour and the bar are the claim that the rate is established, and a
 * clear interval with a q-value under 0.05 does not establish it. The
 * percentage, n, and the interval stay. A pattern column is not a setup
 * cell and keeps the sample-floor colour.
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

  /**
   * The display family rejects at this q. It is the complement of the 95%
   * interval (`z = 1.96`), not a second bar. Agreement with it used to be
   * the established colour. A setup cell stays muted either way.
   */
  var Q_DISPLAY = 0.05;

  /** A setup cell: the board marked it, or it carries the interval fields. */
  function exploratoryCell(g) {
    return !!(g && (g._setupCell || Object.prototype.hasOwnProperty.call(g, 'mean_r_lo')));
  }

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
    var setup = exploratoryCell(g);
    var out = {
      label: (label === null || label === undefined || label === '') ? '(none)' : String(label),
      rate: rate,
      n: n === null ? null : Math.max(0, Math.round(n)),
      rated: false,
      reason: null,
      detail: typeof g.detail === 'string' ? g.detail : '',
      // The plan's word, not a second status. A planted "survives" on the
      // payload is not copied: agreement does not promote the cell.
      reading: setup ? 'exploratory' : '',
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
    // Colour is the established verdict. A setup cell is exploratory, so
    // the verdict stays off even when the interval and q agree. A pattern
    // column has neither mark and keeps the sample-floor colour.
    if (!evidenceAgrees(g, rate >= 50)) {
      out.rated = false;
      out.reason = '';
    }
    return out;
  }

  /**
   * Does the published interval and q-value support the win-rate colour?
   *
   * A pattern column has no `mean_r_lo` and was not marked by the setup
   * board. The sample floor is its whole rule, and this returns true.
   *
   * A setup cell returns false on both arms. The interval and q are still
   * read: q below `Q_DISPLAY` and an interval strictly clear of zero
   * (`lo > 0` or `hi < 0`; touching zero is not clear) is the pair that
   * used to paint the established colour. That pair does not paint it.
   * A missing interval, a missing q, or a q that does not clear does not
   * paint it either.
   */
  function evidenceAgrees(g, positive) {
    if (!g || (!g._setupCell && !Object.prototype.hasOwnProperty.call(g, 'mean_r_lo'))) {
      return true;
    }
    var lo = num(g.mean_r_lo);
    var hi = num(g.mean_r_hi);
    var q = num(g.q_value);
    var established = lo !== null && hi !== null && q !== null && q < Q_DISPLAY
      && (positive ? lo > 0 : hi < 0);
    if (established) return false;
    return false;
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

    var note = (!c.rated && c.reason)
      ? '<span class="wr-why">' + esc(c.reason) + '</span>' : '';
    // The interval, the hit rate and the q-value. Muted on purpose: a green
    // mean would be a second verdict beside the win-rate bar.
    var detail = c.detail
      ? '<span class="wr-why">' + esc(c.detail) + '</span>' : '';
    // The plan's word, muted. Only that word: a different status is not shown.
    var reading = c.reading === 'exploratory'
      ? '<span class="wr-why">exploratory</span>' : '';

    return '<div class="wr-row' + (c.rated ? '' : ' wr-row--unrated') + '">'
      + '<div class="wr-head"><span class="wr-label">' + esc(c.label) + '</span>'
      + count
      + '<b class="wr-val ' + cls + '">' + value + '</b></div>'
      + reading + bar + note + detail
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

  /**
   * The label of one setup cell, or null when a dimension was not stored.
   *
   * Five words, in the scoreboard's order. A missing one is omitted — the
   * function returns null and the caller paints nothing — rather than
   * "(none)" or a 0R. `mean_r == null` is checked before `Number`, because
   * `Number(null)` is 0 and would print a measured flat book.
   */
  function setupCellLabel(g) {
    if (!g || typeof g !== 'object') return null;
    var dims = [g.setup, g.regime, g.timeframe, g.source, g.direction];
    for (var i = 0; i < dims.length; i++) {
      if (dims[i] == null || typeof dims[i] !== 'string' || dims[i].trim() === '') return null;
    }
    var label = dims.map(function (p) { return p.trim(); }).join(' · ');
    if (g.mean_r == null) return label;
    var mean = Number(g.mean_r);
    if (!Number.isFinite(mean)) return label;
    return label + ' · ' + mean + 'R';
  }

  /** A ratio for the row, or null when the field was not a measurement. */
  function ratioText(v) {
    var n = num(v);
    if (n === null) return null;
    if (n === 0) return '0';
    return String(n);
  }

  /**
   * The numbers the scoreboard shows under one cell.
   *
   * Read from the payload. This function does not recompute a hit rate, an
   * interval or a q-value — a second copy would be a second answer, and a
   * null rendered as 0.
   */
  function setupDetail(g) {
    var cell = g || {};
    var n = num(cell.n);
    var hit = ratioText(cell.hit_rate);
    var wlo = ratioText(cell.wilson_lo);
    var whi = ratioText(cell.wilson_hi);
    var mean = num(cell.mean_r);
    var lo = num(cell.mean_r_lo);
    var hi = num(cell.mean_r_hi);
    var q = ratioText(cell.q_value);
    return [
      'n ' + (n === null ? 'unavailable' : String(Math.round(n))),
      hit === null ? 'hit unavailable' : 'hit ' + hit,
      (wlo === null || whi === null) ? 'Wilson unavailable' : 'Wilson ' + wlo + ' to ' + whi,
      mean === null ? 'mean unavailable' : 'mean ' + mean + 'R',
      (lo === null || hi === null) ? 'interval unavailable' : 'interval ' + lo + ' to ' + hi,
      q === null ? 'q unavailable' : 'q ' + q,
    ].join(' · ');
  }

  /**
   * The setup scoreboard, or '' when there is no cell to show.
   *
   * A missing group, an empty list, and a cell that lacks a dimension all
   * omit. They do not become a 0% row and they are not labelled. Every
   * cell that is shown reads exploratory, and the established colour stays
   * off. The R in the label is a number, not a second colour claim.
   */
  function setupScoreboard(cells) {
    if (!Array.isArray(cells) || !cells.length) return '';
    var prepared = [];
    for (var i = 0; i < cells.length; i++) {
      var label = setupCellLabel(cells[i]);
      if (label == null) continue;
      var row = {};
      for (var k in cells[i]) {
        if (Object.prototype.hasOwnProperty.call(cells[i], k)) row[k] = cells[i][k];
      }
      row.label = label;
      row.detail = setupDetail(cells[i]);
      // Marks the row for classify. A cell with no interval fields is still
      // a setup cell, and a payload status is not consulted.
      row._setupCell = true;
      prepared.push(row);
    }
    if (!prepared.length) return '';
    return buildRows(prepared, 'label');
  }

  /** How many of these groups may honestly be ranked. For a caption. */
  function ratedCount(groups, key) {
    return (Array.isArray(groups) ? groups : [])
      .map(function (g) { return classify(g, key); })
      .filter(function (c) { return c.rated; }).length;
  }

  return { buildRows: buildRows, classify: classify, ratedCount: ratedCount,
           setupScoreboard: setupScoreboard, setupDetail: setupDetail,
           MIN_RATED: MIN_RATED, MAX_ROWS: MAX_ROWS, Q_DISPLAY: Q_DISPLAY };
}));
