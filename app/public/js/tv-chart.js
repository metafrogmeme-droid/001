/*
 * RCTVChart — every candle chart on the site is a TradingView chart.
 *
 *   mount(host, spec, opts)  -> { ok: true, chart, series, remove() }
 *                            |  { ok: false, reason }
 *
 * The Markets view drew with TradingView Lightweight Charts and every chart
 * behind a SIGNAL drew its own SVG: the row chart, the symbol modal, the
 * pattern-read mini, and the three in the Arena. Two looks for one market, and
 * the one a reader reaches from a signal was the hand-drawn one.
 *
 * WHAT THIS FILE DOES NOT DECIDE
 *
 * A spec is built by the model that owns the reading (RCSignalChart.tvSpec,
 * RCChartRead.tvSpec, miniSpec below). Those decide which levels exist, which
 * of them sit near the price action, and every reason a chart cannot be drawn
 * -- no candles, too few, unreadable, flat. This file draws a spec and nothing
 * else, so the honesty rules stay in pure functions the node suite can run.
 *
 * THE STYLE IS ONE STYLE
 *
 * `chartOptions` and `CANDLE` are the Markets chart's own option set, moved
 * here so the Markets chart reads them too: a second copy of a style is a
 * second answer about what the site's charts look like.
 *
 * WHAT A SMALL CHART MUST NOT DO
 *
 * A chart in a list or on a tappable card does not scroll or zoom. Lightweight
 * Charts cancels a touch drag it handles, so a row of charts that handle
 * scrolling is a page a phone cannot scroll past.
 *
 * A chart whose host has left the page is removed (`sweep`), because the
 * signals list re-renders on a 30s cycle and a chart that is never removed
 * keeps its canvas and its resize observer for the life of the tab.
 *
 * Exposed as window.RCTVChart in the browser and module.exports in node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCTVChart = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** The Markets chart's candle colours, one copy. */
  var CANDLE = {
    upColor: '#2fbf71', downColor: '#e5484d',
    wickUpColor: '#2fbf71', wickDownColor: '#e5484d',
    borderVisible: false,
  };

  /** Lightweight Charts line styles, spelled so a spec does not need the lib. */
  var STYLE = { solid: 0, dotted: 1, dashed: 2, large: 3, sparse: 4 };

  var REASONS = { NO_LIB: 'no_lib', NO_HOST: 'no_host', NO_BARS: 'no_bars' };

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    return (typeof n === 'number' && isFinite(n)) ? n : null;
  }

  /**
   * Decimal places for a price axis, by magnitude.
   *
   * The library's default is 2, which prints a sub-cent asset's whole axis as
   * `0.00` -- a chart whose every label is the same number. The places follow
   * the price: 2 above 1000, 3 for a two-digit price, and one more for each
   * factor of ten below that, capped at 10.
   */
  function precisionFor(price) {
    var p = num(price);
    if (p === null || !(p > 0)) return 2;
    var d = Math.ceil(-Math.log10(p)) + 4;
    return Math.max(2, Math.min(10, d));
  }

  /**
   * Parsed candles ({t ms, o, h, l, c}) to library bars ({time s, open, ...}).
   *
   * The library requires strictly ascending times, and a venue can echo the
   * live candle twice at the edge; the later of two equal times is dropped.
   */
  function toBars(candles) {
    var out = [];
    var sorted = (candles || []).slice().sort(function (a, b) { return a.t - b.t; });
    for (var i = 0; i < sorted.length; i++) {
      var c = sorted[i];
      var time = Math.floor(Number(c.t) / 1000);
      if (!isFinite(time)) continue;
      if (out.length && time <= out[out.length - 1].time) continue;
      out.push({ time: time, open: c.o, high: c.h, low: c.l, close: c.c });
    }
    return out;
  }

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }

  /**
   * The chart options: the Markets chart's, with `compact` for a chart that
   * sits in a list or on a card and must not take the page's gestures.
   */
  function chartOptions(lib, o) {
    o = o || {};
    var compact = !!o.compact;
    var opts = {
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: cssVar('--text-3', '#8f99ab'),
        fontFamily: cssVar('--font-data', 'monospace'),
        fontSize: compact ? 10 : 12,
      },
      grid: {
        vertLines: { color: 'rgba(49,57,80,.35)' },
        horzLines: { color: 'rgba(49,57,80,.35)' },
      },
      rightPriceScale: { borderColor: 'rgba(49,57,80,.6)' },
      timeScale: {
        borderColor: 'rgba(49,57,80,.6)', timeVisible: true, secondsVisible: false,
        visible: o.timeAxis !== false,
      },
      crosshair: { mode: lib && lib.CrosshairMode ? lib.CrosshairMode.Normal : 0 },
      autoSize: true,
    };
    // The library draws its attribution logo with an injected <style>, which
    // a page whose CSP has no 'unsafe-inline' (the embed board) refuses -- the
    // logo then lands in the chart's flow and shifts the price axis off its
    // lines. Such a page turns the logo off and carries the attribution as a
    // link instead, which is the library's documented alternative.
    if (o.attributionLogo === false) opts.layout.attributionLogo = false;
    if (compact) {
      opts.handleScroll = false;
      opts.handleScale = false;
      opts.crosshair = { mode: lib && lib.CrosshairMode ? lib.CrosshairMode.Hidden : 2 };
      opts.rightPriceScale.scaleMargins = { top: 0.12, bottom: 0.12 };
    } else if (o.pageScroll) {
      // A chart inside a scrolling modal: horizontal drag and pinch still
      // work, a vertical drag and the mouse wheel go to the page.
      opts.handleScroll = { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false };
      opts.handleScale = { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true };
    }
    return opts;
  }

  /**
   * Horizontal bands (fair-value gaps, the VWAP band, a swing range) drawn
   * under the candles. Lightweight Charts 4 has no rectangle, so this is the
   * library's own series-primitive interface.
   */
  function bandPrimitive(bands) {
    var series = null;
    var view = {
      zOrder: function () { return 'bottom'; },
      renderer: function () {
        return {
          draw: function (target) {
            if (!series) return;
            target.useBitmapCoordinateSpace(function (scope) {
              var ctx = scope.context, r = scope.verticalPixelRatio, w = scope.bitmapSize.width;
              for (var i = 0; i < bands.length; i++) {
                var b = bands[i];
                var y1 = series.priceToCoordinate(b.top), y2 = series.priceToCoordinate(b.bottom);
                if (y1 === null || y2 === null) continue;
                ctx.fillStyle = b.color;
                ctx.fillRect(0, Math.round(Math.min(y1, y2) * r), w, Math.max(1, Math.round(Math.abs(y2 - y1) * r)));
              }
            });
          },
        };
      },
    };
    return {
      attached: function (p) { series = p.series; },
      detached: function () { series = null; },
      updateAllViews: function () {},
      paneViews: function () { return [view]; },
    };
  }

  // Every chart this file mounted, so one whose host left the page can be
  // removed rather than kept alive by its own resize observer.
  var LIVE = [];

  function sweep() {
    for (var i = LIVE.length - 1; i >= 0; i--) {
      var h = LIVE[i];
      if (!h.host || !h.host.isConnected) {
        try { h.chart.remove(); } catch (e) { /* already gone */ }
        LIVE.splice(i, 1);
      }
    }
    return LIVE.length;
  }

  /** Remove the chart mounted on `host`, if any. A redraw into the same host
   *  (a timeframe switch in the symbol modal) must not leave the previous
   *  chart alive behind the new one: its host is still on the page, so
   *  `sweep` alone would never collect it. */
  function release(host) {
    for (var i = LIVE.length - 1; i >= 0; i--) {
      if (LIVE[i].host === host) {
        try { LIVE[i].chart.remove(); } catch (e) { /* already gone */ }
        LIVE.splice(i, 1);
      }
    }
  }

  function lineStyle(v) {
    if (typeof v === 'number') return v;
    return Object.prototype.hasOwnProperty.call(STYLE, v) ? STYLE[v] : STYLE.solid;
  }

  /**
   * Draw a spec into `host`.
   *
   * spec: {
   *   bars: [{time, open, high, low, close}],
   *   precision?: n,
   *   lines?:    [{price, color, title?, style?, width?, axisLabel?}],
   *   overlays?: [{points: [{time, value}], color, style?, width?}],
   *   bands?:    [{top, bottom, color}],
   *   markers?:  [{time, position, color, shape, text}],
   *   autoscale?: [price, ...],   levels the price axis must include
   *   legend?: string, aria?: string,
   * }
   * opts: { compact?, timeAxis?, pageScroll?, lib? }
   */
  function mount(host, spec, opts) {
    opts = opts || {};
    var lib = opts.lib || (typeof window !== 'undefined' ? window.LightweightCharts : null);
    if (!lib || typeof lib.createChart !== 'function') return { ok: false, reason: REASONS.NO_LIB };
    if (!host) return { ok: false, reason: REASONS.NO_HOST };
    if (!spec || !spec.bars || !spec.bars.length) return { ok: false, reason: REASONS.NO_BARS };
    sweep();
    release(host);

    host.innerHTML = '';
    host.classList.add('tvc');
    if (spec.aria) { host.setAttribute('role', 'img'); host.setAttribute('aria-label', spec.aria); }
    var pane = document.createElement('div');
    pane.className = 'tvc-pane';
    host.appendChild(pane);
    if (spec.legend) {
      var lg = document.createElement('div');
      lg.className = 'tvc-legend';
      lg.textContent = spec.legend;
      host.appendChild(lg);
    }

    var chart = lib.createChart(pane, chartOptions(lib, opts));
    var precision = spec.precision || precisionFor(spec.bars[spec.bars.length - 1].close);
    var format = { type: 'price', precision: precision, minMove: Math.pow(10, -precision) };
    var extra = (spec.autoscale || []).map(num).filter(function (p) { return p !== null; });
    var series = chart.addCandlestickSeries(Object.assign({}, CANDLE, {
      priceFormat: format,
      autoscaleInfoProvider: function (original) {
        var r = original();
        if (!r || !r.priceRange || !extra.length) return r;
        var lo = r.priceRange.minValue, hi = r.priceRange.maxValue;
        for (var i = 0; i < extra.length; i++) { if (extra[i] < lo) lo = extra[i]; if (extra[i] > hi) hi = extra[i]; }
        return { priceRange: { minValue: lo, maxValue: hi }, margins: r.margins };
      },
    }));
    series.setData(spec.bars);

    (spec.overlays || []).forEach(function (ov) {
      if (!ov.points || !ov.points.length) return;
      chart.addLineSeries({
        color: ov.color, lineWidth: ov.width || 1, lineStyle: lineStyle(ov.style),
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        priceFormat: format,
        // An overlay never widens the axis: a far-away level must not squash
        // the candles into a line, which is the rule every level here keeps.
        autoscaleInfoProvider: function () { return null; },
      }).setData(ov.points);
    });

    (spec.lines || []).forEach(function (L) {
      var p = num(L.price);
      if (p === null) return;
      series.createPriceLine({
        price: p, color: L.color, lineWidth: L.width || 1, lineStyle: lineStyle(L.style),
        axisLabelVisible: L.axisLabel !== false, title: L.title || '',
      });
    });

    if (spec.bands && spec.bands.length && typeof series.attachPrimitive === 'function') {
      series.attachPrimitive(bandPrimitive(spec.bands));
    }
    if (spec.markers && spec.markers.length && typeof series.setMarkers === 'function') {
      series.setMarkers(spec.markers.slice().sort(function (a, b) { return a.time - b.time; }));
    }

    chart.timeScale().fitContent();
    var handle = {
      ok: true, chart: chart, series: series, host: host,
      remove: function () {
        try { chart.remove(); } catch (e) { /* already gone */ }
        var i = LIVE.indexOf(handle);
        if (i >= 0) LIVE.splice(i, 1);
      },
    };
    LIVE.push(handle);
    return handle;
  }

  /**
   * The pattern-read mini: recent bars with the window's swing high and low
   * (the reference levels chart patterns key off) and nothing else. The SVG it
   * replaces tinted the last-close line by the dominant pattern's bias; that
   * is a detector's opinion, and the card already prints it beside the chart
   * in words, so the chart spends no colour on it.
   */
  function miniSpec(candles) {
    var cs = (candles || []).filter(function (c) {
      return isFinite(c.o) && isFinite(c.h) && isFinite(c.l) && isFinite(c.c);
    }).sort(function (a, b) { return a.t - b.t; }).slice(-44);
    var bars = toBars(cs);
    if (bars.length < 3) return { ok: false, bars: bars.length };
    var hi = -Infinity, lo = Infinity;
    for (var i = 0; i < bars.length; i++) {
      if (bars[i].high > hi) hi = bars[i].high;
      if (bars[i].low < lo) lo = bars[i].low;
    }
    if (!(hi > lo)) return { ok: false, bars: bars.length };
    return {
      ok: true,
      bars: bars,
      lines: [
        { price: hi, color: 'rgba(229,72,77,.55)', style: 'dashed', axisLabel: false },
        { price: lo, color: 'rgba(47,191,113,.55)', style: 'dashed', axisLabel: false },
      ],
    };
  }

  return {
    mount: mount, sweep: sweep, release: release, chartOptions: chartOptions, precisionFor: precisionFor,
    toBars: toBars, miniSpec: miniSpec, bandPrimitive: bandPrimitive,
    CANDLE: CANDLE, STYLE: STYLE, REASONS: REASONS,
    _live: function () { return LIVE.length; },
  };
}));
