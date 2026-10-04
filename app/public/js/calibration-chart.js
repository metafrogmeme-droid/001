/*
 * The calibration chart — the curve already fitted, worded per C3.
 *
 * The numbers are the ones `ConfidenceCalibrator.to_dict` writes
 * (`x`, `y`, `n_samples`, `min_samples` in
 * `data/learning/confidence_calibration.json`). `summary()` prints a bin
 * as `0.65->0.18`. This file does not fit another curve and does not
 * turn the entry-path learner on. A curve below `min_samples` is
 * identity in the calibrator, and identity is not a measurement, so
 * nothing is drawn.
 *
 * C3's default (docs/adr/0006): stated confidence is a rung word or
 * "unmeasured", never a percentage read as a probability. The chart
 * says that, and it stays muted. Colour would be a verdict. A heuristic
 * is not one.
 *
 * An unreadable bin is omitted. A measured 0 stays 0.00, on the axis.
 * Exposed as window.RCCalibration; module.exports in node so it can be tested.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RCCalibration = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // The plot box. A measured 0 sits on the bottom edge (h - pad).
  var PLOT = { w: 240, h: 120, pad: 12 };

  // C3's own default, not a stronger claim. No percent sign.
  var CAPTION = 'Provisional. Stated confidence is a rung word or unmeasured, '
    + 'not a probability. Each point is the fitted curve\'s own bin: '
    + 'stated confidence, then the recorded rate.';

  function finite(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'boolean') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    if (typeof n !== 'number' || !isFinite(n)) return null;
    return n;
  }

  /**
   * Two decimals, the spelling `summary()` already uses (`0.65->0.18`).
   *
   * A measured 0 is `0.00`. A non-zero that those two decimals would
   * erase is kept, so a small rate is not painted as zero.
   */
  function formatRate(v) {
    if (v === 0) return '0.00';
    var text = v.toFixed(2);
    if (text === '0.00' || text === '-0.00') return v.toPrecision(2);
    return text;
  }

  function formatCount(n) {
    if (n === 0) return '0';
    if (Math.round(n) === n) return String(n);
    return String(n);
  }

  function binText(p) {
    return formatRate(p.x) + '->' + formatRate(p.y);
  }

  function blank(state) {
    return { state: state, n_samples: null, min_samples: null, bins: [] };
  }

  function unavailableReading() {
    return blank('unavailable');
  }

  function unmeasuredReading(n, need) {
    return {
      state: 'unmeasured',
      n_samples: n === undefined ? null : n,
      min_samples: need === undefined ? null : need,
      bins: [],
    };
  }

  /**
   * One bin the file actually stored, or null when either coordinate
   * could not be read. Null is not 0.
   */
  function pointOf(x, y) {
    var px = finite(x);
    var py = finite(y);
    if (px === null || py === null) return null;
    return { x: px, y: py };
  }

  /**
   * The curve document the calibrator saved, as a chart reading.
   *
   * Not ready (too few samples, or no points) is unmeasured: the
   * identity map is not drawn. A document that is not the curve's
   * shape is unavailable. Unreadable points are left out of `bins`;
   * a point whose rate is 0 stays.
   */
  function readingFromCurve(doc) {
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return unavailableReading();
    var xs = doc.x;
    var ys = doc.y;
    if (!Array.isArray(xs) || !Array.isArray(ys) || xs.length !== ys.length) {
      return unavailableReading();
    }
    var n = finite(doc.n_samples);
    var need = finite(doc.min_samples);
    if (n === null || need === null) return unavailableReading();
    // The calibrator's own gate: a curve with no points, or fewer
    // samples than it required, is not applied. Do not draw identity.
    if (!(n >= need) || xs.length === 0) return unmeasuredReading(n, need);
    var bins = [];
    for (var i = 0; i < xs.length; i++) {
      var p = pointOf(xs[i], ys[i]);
      if (p) bins.push(p);
    }
    if (!bins.length) return unavailableReading();
    return { state: 'fitted', n_samples: n, min_samples: need, bins: bins };
  }

  function clamp01(v) {
    if (v < 0) return 0;
    if (v > 1) return 1;
    return v;
  }

  function dotY(y) {
    var span = PLOT.h - 2 * PLOT.pad;
    return PLOT.h - PLOT.pad - clamp01(y) * span;
  }

  function dotX(x) {
    var span = PLOT.w - 2 * PLOT.pad;
    return PLOT.pad + clamp01(x) * span;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function stateHtml(state, body) {
    return '<div class="cal-chart" data-state="' + state + '">' + body + '</div>';
  }

  function unmeasuredHtml(reading) {
    var n = finite(reading && reading.n_samples);
    var need = finite(reading && reading.min_samples);
    var count = '';
    if (n !== null && need !== null) {
      count = '<p class="muted small">' + formatCount(n) + ' of '
        + formatCount(need) + ' samples</p>';
    }
    return stateHtml('unmeasured',
      '<p class="muted small">unmeasured</p>' + count);
  }

  function fittedHtml(reading, bins) {
    var n = finite(reading.n_samples);
    var samples = n === null ? ''
      : '<p class="muted small">' + formatCount(n) + ' samples</p>';
    var pts = bins.map(function (p) {
      return dotX(p.x).toFixed(1) + ',' + dotY(p.y).toFixed(1);
    }).join(' ');
    var circles = bins.map(function (p) {
      return '<circle data-y="' + formatRate(p.y) + '" cx="' + dotX(p.x).toFixed(1)
        + '" cy="' + dotY(p.y).toFixed(1)
        + '" r="2.5" fill="currentColor"></circle>';
    }).join('');
    // Digits, a dot, and the calibrator's own `->`. No markup, so the
    // bin is the same spelling summary() prints, not an escaped second one.
    var labels = bins.map(function (p) {
      var text = binText(p);
      return '<li data-bin="' + text + '">' + text + '</li>';
    }).join('');
    var aria = 'Calibration curve, provisional. ' + bins.map(binText).join(', ');
    var svg = '<svg viewBox="0 0 ' + PLOT.w + ' ' + PLOT.h
      + '" width="' + PLOT.w + '" role="img" aria-label="' + esc(aria) + '" '
      + 'style="display:block;max-width:360px;color:var(--text-3)">'
      + '<polyline points="' + pts + '" fill="none" stroke="currentColor" '
      + 'stroke-width="1.5"></polyline>'
      + circles + '</svg>';
    var list = '<ul class="muted small" style="list-style:none;padding:0;margin:8px 0 0">'
      + labels + '</ul>';
    return stateHtml('fitted',
      '<p class="muted small">' + esc(CAPTION) + '</p>' + samples + svg + list);
  }

  /**
   * The chart, or the C3 word when there is no curve to draw.
   *
   * `unavailable` is a curve that could not be read. `unmeasured` is a
   * curve the calibrator has not fitted. Neither is a line at 0.
   */
  function chart(reading) {
    if (!reading || typeof reading !== 'object') {
      return stateHtml('unavailable', '<p class="muted small">unavailable</p>');
    }
    if (reading.state === 'unmeasured') return unmeasuredHtml(reading);
    if (reading.state !== 'fitted') {
      return stateHtml('unavailable', '<p class="muted small">unavailable</p>');
    }
    var bins = [];
    var raw = Array.isArray(reading.bins) ? reading.bins : [];
    for (var i = 0; i < raw.length; i++) {
      var row = raw[i];
      if (!row || typeof row !== 'object') continue;
      var p = pointOf(row.x, row.y);
      if (p) bins.push(p);
    }
    if (!bins.length) {
      return stateHtml('unavailable', '<p class="muted small">unavailable</p>');
    }
    return fittedHtml(reading, bins);
  }

  return {
    CAPTION: CAPTION,
    PLOT: PLOT,
    chart: chart,
    readingFromCurve: readingFromCurve,
    unavailableReading: unavailableReading,
    unmeasuredReading: unmeasuredReading,
    formatRate: formatRate,
    dotY: dotY,
  };
}));
