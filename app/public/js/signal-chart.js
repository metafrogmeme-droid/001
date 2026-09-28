/*
 * RCSignalChart — the price chart behind a signal, with its own levels on it.
 *
 *   buildSignalChart(candles, geo, opts) -> { ok: true, svg, ... }
 *                                        |  { ok: false, reason }
 *
 * A signal row reads `entry 63,200 · stop 61,900 / target 66,000` and asks the
 * reader to imagine where price actually sits between those. This draws it:
 * recent candles, the three levels, and the live mark, so "about to stop out"
 * and "nearly at target" are visible rather than arithmetic.
 *
 * WHY THIS IS A PURE FUNCTION AND NOT AN innerHTML CALL SITE
 *
 * The chart it replaces was built inline in the loader and could only be
 * exercised by loading the dashboard. Its failure path was one line:
 *
 *     .catch(() => { el.style.display = 'none'; });
 *
 * An unreadable chart DISAPPEARED. A reader cannot tell that from a chart that
 * was never offered, and neither can a test. Every outcome here is a value:
 * `{ok:false, reason}` with a fixed vocabulary, so the caller must paint
 * something and a test can plant each failure and read what a user would see.
 *
 * THE RULE THIS FILE IS MOSTLY ABOUT
 *
 * "Unreadable is never zero, and absent is never a measurement." A chart is a
 * claim about price, and the ways to make a false one are all cheap:
 *
 *   - a flat line from one repeated candle reads as a calm market
 *   - a zero-span axis (every OHLC identical) divides by zero, and the usual
 *     `|| 1` fallback silently renders a market that never moved
 *   - COLOUR is a claim: a green fill between entry and mark says "in profit"
 *     as loudly as a number would, so an unknown direction must be muted
 *   - a level drawn at 0 because `entry_price` was absent puts the entry line
 *     at the bottom of the chart and rescales everything around it
 *
 * Each of those is a test in `app/test/signal_chart_honesty.test.js`.
 *
 * Exposed as window.RCSignalChart in the browser and module.exports in node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCSignalChart = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Why a chart could not be drawn. A fixed vocabulary: the caller renders a
   *  message per reason, so a new failure mode cannot arrive as a blank box. */
  var REASONS = {
    NO_CANDLES: 'no_candles',       // the fetch returned nothing at all
    TOO_FEW: 'too_few',             // fewer bars than a shape can be read from
    UNREADABLE: 'unreadable',       // rows present, none parse as numbers
    FLAT: 'flat',                   // every price identical — no axis to draw
  };

  /** Minimum bars before a line is a shape rather than a suggestion. */
  var MIN_BARS = 3;
  var MAX_BARS = 60;

  var W = 260, H = 72, PAD = 4;

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    // `is None`, not falsiness: a price of exactly 0 is not a real price for
    // any traded asset, but it IS a real number, and the distinction belongs to
    // the caller. Infinity and NaN are neither.
    return (typeof n === 'number' && isFinite(n)) ? n : null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /**
   * Parse Bitget-style `[t, o, h, l, c, ...]` rows into candles.
   *
   * A row that does not parse is DROPPED, and the count of dropped rows is
   * reported — it is not silently ignored. A feed that half-parses is a
   * different thing from a feed that works, and a chart drawn from the half
   * that happened to be numeric is a partial total presented as a whole one.
   */
  function parseCandles(rows, maxBars) {
    var out = [], dropped = 0;
    if (!rows || typeof rows.length !== 'number') return { candles: [], dropped: 0 };
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r || typeof r.length !== 'number' || r.length < 5) { dropped++; continue; }
      var t = num(r[0]), o = num(r[1]), h = num(r[2]), l = num(r[3]), c = num(r[4]);
      if (t === null || o === null || h === null || l === null || c === null) { dropped++; continue; }
      if (h < l) { dropped++; continue; }   // a bar whose high is below its low is not a bar
      out.push({ t: t, o: o, h: h, l: l, c: c });
    }
    out.sort(function (a, b) { return a.t - b.t; });
    // A row's mini chart shows the last MAX_BARS; a full-page chart (the live
    // chart a Telegram signal links to) asks for more.
    var cap = maxBars > 0 ? maxBars : MAX_BARS;
    return { candles: out.slice(-cap), dropped: dropped };
  }

  /**
   * Where the mark sits between stop and target, 0..1, or null.
   *
   * `null` whenever any leg is missing or the geometry is degenerate — never a
   * default of 0, which would render as "sitting exactly on the stop", the most
   * alarming reading available, for a signal whose stop we simply do not know.
   */
  function progress(geo, mark) {
    var e = num(geo && geo.entry), sl = num(geo && geo.stop), tp = num(geo && geo.target);
    var m = num(mark);
    if (e === null || sl === null || tp === null || m === null) return null;
    if (sl === tp) return null;
    var p = (m - sl) / (tp - sl);
    return Math.max(0, Math.min(1, p));
  }

  /**
   * Build the chart.
   *
   * @param rows  raw candle rows, newest-last or unsorted
   * @param geo   { entry, stop, target, direction } — any may be absent
   * @param opts  { label?, ariaLabel? }
   */
  function buildSignalChart(rows, geo, opts) {
    opts = opts || {};
    var R = readSignal(rows, geo);
    if (!R.ok) return { ok: false, reason: R.reason, dropped: R.dropped };
    var cs = R.candles, levels = R.levels, lo = R.lo, hi = R.hi, span = hi - lo, i;

    var innerW = W - 2 * PAD, innerH = H - 2 * PAD;
    var step = innerW / cs.length;
    var x = function (idx) { return PAD + idx * step; };
    var y = function (v) { return PAD + (hi - v) / span * innerH; };
    var f = function (n) { return Math.round(n * 10) / 10; };
    var bw = Math.max(1.2, step - 1.2);

    var parts = [];

    // Levels first, so candles draw over them.
    for (i = 0; i < levels.length; i++) {
      var L = levels[i];
      parts.push('<line class="sc-lvl sc-lvl--' + L.cls + '" x1="' + PAD + '" x2="' + (W - PAD)
        + '" y1="' + f(y(L.v)) + '" y2="' + f(y(L.v)) + '"/>');
    }

    for (i = 0; i < cs.length; i++) {
      var c = cs[i], bx = x(i), mid = f(bx + bw / 2);
      var up = c.c >= c.o;
      var cls = up ? 'sc-up' : 'sc-down';
      var top = y(Math.max(c.o, c.c));
      var hgt = Math.max(1, Math.abs(y(c.o) - y(c.c)));
      parts.push('<line class="' + cls + '" x1="' + mid + '" x2="' + mid
        + '" y1="' + f(y(c.h)) + '" y2="' + f(y(c.l)) + '" stroke-width="1"/>');
      parts.push('<rect class="' + cls + '" x="' + f(bx) + '" y="' + f(top)
        + '" width="' + f(bw) + '" height="' + f(hgt) + '"/>');
    }

    var mark = R.mark;
    parts.push('<line class="sc-mark" x1="' + PAD + '" x2="' + (W - PAD)
      + '" y1="' + f(y(mark)) + '" y2="' + f(y(mark)) + '"/>');

    var aria = opts.ariaLabel || buildAriaLabel(opts.label, cs, R.geo, mark, R.progress);

    var svg = '<svg class="sc" viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H
      + '" preserveAspectRatio="none" role="img" aria-label="' + esc(aria)
      + '" style="display:block">' + parts.join('') + '</svg>';

    return {
      ok: true,
      svg: svg,
      bars: cs.length,
      dropped: R.dropped,
      mark: mark,
      progress: R.progress,
      levels: levels.length,
    };
  }

  /**
   * The reading both renderers draw: the candles, the levels that exist, and
   * every reason there is no chart. One function, so the SVG and the
   * TradingView chart cannot disagree about whether a signal HAS a chart --
   * the flat-market trap, a level at 0, and a half-parsed feed are decided
   * here and nowhere else.
   */
  function readSignal(rows, geo, maxBars) {
    geo = geo || {};
    var parsed = parseCandles(rows, maxBars);
    var cs = parsed.candles;

    if (!rows || !rows.length) return { ok: false, reason: REASONS.NO_CANDLES, dropped: 0 };
    if (!cs.length) return { ok: false, reason: REASONS.UNREADABLE, dropped: parsed.dropped };
    if (cs.length < MIN_BARS) return { ok: false, reason: REASONS.TOO_FEW, dropped: parsed.dropped };

    var lo = Infinity, hi = -Infinity, i;
    for (i = 0; i < cs.length; i++) {
      if (cs[i].l < lo) lo = cs[i].l;
      if (cs[i].h > hi) hi = cs[i].h;
    }

    // Levels join the axis so a stop below every candle is still visible rather
    // than clipped off the bottom — but ONLY the ones that exist. A missing
    // `entry` used to arrive as 0 and drag the whole axis to zero, flattening
    // every candle into a line at the top of the chart.
    var levels = [];
    var e = num(geo.entry), sl = num(geo.stop), tp = num(geo.target);
    if (e !== null) levels.push({ v: e, cls: 'entry', label: 'entry' });
    if (sl !== null) levels.push({ v: sl, cls: 'stop', label: 'stop' });
    if (tp !== null) levels.push({ v: tp, cls: 'target', label: 'target' });
    for (i = 0; i < levels.length; i++) {
      if (levels[i].v < lo) lo = levels[i].v;
      if (levels[i].v > hi) hi = levels[i].v;
    }

    // A zero span is the flat-market trap. `(hi - lo) || 1` is the shape this
    // repo bans: it invents a one-unit axis and paints a market that never
    // moved as an ordinary chart. Say so instead.
    if (!(hi - lo > 0)) return { ok: false, reason: REASONS.FLAT, dropped: parsed.dropped };

    var mark = cs[cs.length - 1].c;
    var g = { entry: e, stop: sl, target: tp };
    return {
      ok: true, candles: cs, dropped: parsed.dropped, levels: levels,
      lo: lo, hi: hi, mark: mark, geo: g, progress: progress(g, mark),
    };
  }

  /** The TradingView renderer (tv-chart.js), in the browser or under node. */
  function tvModule() {
    if (typeof self !== 'undefined' && self.RCTVChart) return self.RCTVChart;
    if (typeof module === 'object' && module.exports && typeof require === 'function') {
      try { return require('./tv-chart.js'); } catch (e) { return null; }
    }
    return null;
  }

  /** The colour each level means, fixed. Colour is a claim, so the entry
   *  stays neutral: an entry line says nothing about winning. */
  var LEVEL_COLOR = { entry: '#8f99ab', stop: '#e5484d', target: '#2fbf71' };

  /**
   * The same reading as a TradingView spec (RCTVChart.mount draws it).
   *
   * Returns `{ok: false, reason}` for exactly the inputs buildSignalChart
   * refuses, so the placeholder a reader sees does not depend on which
   * renderer loaded.
   */
  function tvSpec(rows, geo, opts) {
    opts = opts || {};
    var R = readSignal(rows, geo, opts.maxBars);
    if (!R.ok) return { ok: false, reason: R.reason, dropped: R.dropped };
    // The renderer's own bar reading (seconds, ascending, one bar per
    // timestamp). This function carried a second copy of it, and a mutation
    // that let a repeated timestamp through the renderer's copy changed no
    // verdict, because every signal chart went through this one instead.
    var bars = tvModule().toBars(R.candles);
    var lines = R.levels.map(function (L) {
      return { price: L.v, color: LEVEL_COLOR[L.cls], title: L.label, style: L.cls === 'entry' ? 'solid' : 'dashed' };
    });
    return {
      ok: true,
      bars: bars,
      lines: lines,
      // Every level the signal states must be on the axis: a stop below
      // every candle is still the stop.
      autoscale: R.levels.map(function (L) { return L.v; }),
      aria: opts.ariaLabel || buildAriaLabel(opts.label, R.candles, R.geo, R.mark, R.progress),
      mark: R.mark,
      progress: R.progress,
      dropped: R.dropped,
    };
  }

  /**
   * The screen-reader sentence, which is the whole chart for some readers.
   *
   * It says what is KNOWN and omits what is not — never "0% to target" for a
   * signal with no target, which is the same false measurement one modality
   * over.
   */
  function buildAriaLabel(label, cs, geo, mark, prog) {
    var bits = [];
    bits.push((label ? label + ': ' : '') + cs.length + ' recent bars');
    if (mark !== null && mark !== undefined) bits.push('last ' + mark);
    if (geo.entry !== null) bits.push('entry ' + geo.entry);
    if (geo.stop !== null) bits.push('stop ' + geo.stop);
    if (geo.target !== null) bits.push('target ' + geo.target);
    if (prog !== null) bits.push(Math.round(prog * 100) + '% of the way from stop to target');
    else if (geo.stop === null || geo.target === null) bits.push('progress unknown — the levels are not all published');
    return bits.join(', ');
  }

  /**
   * What a reader is told when there is no chart.
   *
   * Every reason maps to a sentence. The caller renders this in place of the
   * chart rather than hiding the slot, because a missing chart and an absent
   * one look identical once the element is gone.
   */
  var MESSAGES = {};
  MESSAGES[REASONS.NO_CANDLES] = 'No price history returned for this symbol.';
  MESSAGES[REASONS.TOO_FEW] = 'Too few bars to draw yet.';
  MESSAGES[REASONS.UNREADABLE] = 'Price history could not be read.';
  MESSAGES[REASONS.FLAT] = 'Every bar reports the same price — nothing to plot.';

  function messageFor(reason) {
    return MESSAGES[reason] || 'Chart unavailable.';
  }

  /** The placeholder markup for a failed chart. Never an empty string, and
   *  never `display:none` — the slot stays, and it says which failure it was. */
  function placeholderHtml(reason) {
    return '<div class="sc-none" role="img" aria-label="' + esc(messageFor(reason))
      + '" data-sc-reason="' + esc(reason) + '">' + esc(messageFor(reason)) + '</div>';
  }

  /**
   * Draw a signal's chart into its slot: TradingView when the library loaded,
   * the SVG when it did not, the placeholder when there is no chart to draw.
   * Every surface that shows a signal's chart asks this, so no surface keeps
   * a private choice of renderer -- the dashboard row and the embed widget
   * used to each build the SVG themselves.
   */
  function render(el, rows, geo, opts) {
    opts = opts || {};
    if (!el) return { ok: false, reason: 'no_host' };
    var TV = typeof self !== 'undefined' ? self.RCTVChart : null;
    if (TV) TV.release(el.firstElementChild || el);
    if (TV && self.LightweightCharts) {
      var spec = tvSpec(rows, geo, opts);
      if (!spec.ok) {
        el.innerHTML = placeholderHtml(spec.reason);
        return spec;
      }
      el.innerHTML = '<div class="sc-tv"></div>';
      var h = TV.mount(el.firstElementChild, spec, { compact: true, timeAxis: opts.timeAxis,
        attributionLogo: opts.attributionLogo });
      if (h.ok) return { ok: true, tv: true, bars: spec.bars.length, progress: spec.progress };
    }
    var r = buildSignalChart(rows, geo, opts);
    el.innerHTML = r.ok ? r.svg : placeholderHtml(r.reason);
    return r;
  }

  return {
    buildSignalChart: buildSignalChart,
    tvSpec: tvSpec,
    render: render,
    readSignal: readSignal,
    LEVEL_COLOR: LEVEL_COLOR,
    parseCandles: parseCandles,
    progress: progress,
    buildAriaLabel: buildAriaLabel,
    messageFor: messageFor,
    placeholderHtml: placeholderHtml,
    REASONS: REASONS,
    MIN_BARS: MIN_BARS,
    MAX_BARS: MAX_BARS,
  };
}));
