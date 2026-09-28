/*
 * The live chart page a Telegram signal links to.
 *
 * A Telegram chart is a picture: a PNG rendered once, with nothing to scroll,
 * zoom or read past its last candle. This page is the same setup as the site's
 * TradingView chart, live, one tap from the picture: the market the signal
 * names, on the timeframe it was sent on, with the entry, stop and target the
 * bot published drawn on it.
 *
 * Everything it shows comes from the link and from `/api/market/candles`,
 * which is public market fact. It reads no cookie, sends none, and places
 * nothing: it is an /embed page, and `routes/embed.js` says at length why that
 * is the contract that makes it safe to open anywhere.
 *
 * `readParams` is pure and exported, because what the page does with a link
 * it cannot read is the part worth testing: a symbol it cannot place is a
 * link to no market, and a level that is not a positive number is not a level.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCEmbedChart = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** The timeframes the page draws, as the bot names them, to the candle
   *  route's granularity. Anything else is drawn on 1h and says so. */
  var TIMEFRAMES = { '15m': '15min', '1h': '1h', '4h': '4h', '1d': '1d' };
  var DEFAULT_TF = '1h';
  var REFRESH_MS = 30000;
  var BARS = 200;

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) && n > 0 ? n : null;
  }

  /**
   * What the link names. `{ok: false, reason}` for a link that names no market
   * the candle route could be asked about; otherwise the symbol, the timeframe
   * actually drawn, and each level that is a positive number (an absent or
   * unreadable one is left out, never drawn at zero).
   */
  function readParams(search) {
    var q;
    try { q = new URLSearchParams(search || ''); } catch (e) { return { ok: false, reason: 'unreadable' }; }
    var sym = String(q.get('s') || '').toUpperCase().split(':')[0].replace('/', '');
    if (!/^[A-Z0-9]{2,24}$/.test(sym)) return { ok: false, reason: 'symbol' };
    var asked = String(q.get('tf') || DEFAULT_TF);
    var tf = Object.prototype.hasOwnProperty.call(TIMEFRAMES, asked) ? asked : DEFAULT_TF;
    var d = String(q.get('d') || '').toUpperCase();
    return {
      ok: true,
      symbol: sym,
      tf: tf,
      tfAsked: asked,
      gran: TIMEFRAMES[tf],
      geo: {
        entry: num(q.get('e')),
        stop: num(q.get('sl')),
        target: num(q.get('tp')),
        direction: d === 'LONG' || d === 'SHORT' ? d : null,
      },
    };
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /**
   * A level as the signal published it. The chart's axis precision alone
   * rounded 15.0885 to 15.088 under a line saying these are the published
   * levels, so a level keeps every decimal the link carried, and never fewer
   * than the chart's own precision for its size.
   */
  function fmt(v, TV) {
    if (v === null) return '—';
    var p = TV && TV.precisionFor ? TV.precisionFor(v) : 4;
    var m = /\.(\d+)$/.exec(String(v));
    if (m) p = Math.max(p, Math.min(m[1].length, 10));
    return v.toFixed(p);
  }

  function state(text, detail) {
    return '<div class="e-state e-state--error" role="status"><p>' + esc(text) + '</p>'
      + (detail ? '<p class="e-sub">' + esc(detail) + '</p>' : '') + '</div>';
  }

  function shellHtml(P, TV) {
    var dirCls = P.geo.direction === 'LONG' ? 'e-long' : P.geo.direction === 'SHORT' ? 'e-short' : 'e-flat';
    var tfs = Object.keys(TIMEFRAMES).map(function (tf) {
      var q = new URLSearchParams(window.location.search);
      q.set('tf', tf);
      return tf === P.tf ? '<b>' + tf + '</b>'
        : '<a href="?' + esc(q.toString()) + '">' + tf + '</a>';
    }).join(' · ');
    var note = P.tfAsked !== P.tf
      ? '<p class="e-sub">This link asked for ' + esc(P.tfAsked) + ', which this page does not draw; this is ' + P.tf + '.</p>'
      : '';
    var lv = [['Entry', P.geo.entry], ['Stop', P.geo.stop], ['Target', P.geo.target]]
      .map(function (r) { return '<div><dt>' + r[0] + '</dt><dd>' + esc(fmt(r[1], TV)) + '</dd></div>'; }).join('');
    return '<header class="e-ch-head"><span class="e-sym">' + esc(P.symbol) + '</span>'
      + (P.geo.direction ? ' <span class="e-dir ' + dirCls + '">' + P.geo.direction + '</span>' : '')
      + ' <span class="e-ch-tf">' + tfs + '</span></header>' + note
      + '<div class="e-ch-chart" id="e-ch-chart"><div class="e-load">Loading candles…</div></div>'
      + '<dl class="e-lv">' + lv + '</dl>'
      + '<p class="e-src">Levels as the signal published them · this page places nothing · '
      + 'charts by <a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a></p>';
  }

  /** The chart spec for the link's rows: the full page's window, never the
   *  60-bar thumbnail a signal row draws. Pure, so the window is tested where
   *  it is decided rather than inside a browser callback. */
  function specFor(rows, P, SC) {
    return SC.tvSpec(rows, P.geo, { label: P.symbol + ' ' + P.tf, maxBars: BARS });
  }

  function fetchRows(P, RD) {
    return fetch('/api/market/candles/' + encodeURIComponent(P.symbol)
      + '?granularity=' + encodeURIComponent(P.gran) + '&limit=' + BARS, { credentials: 'omit' })
      .then(function (r) {
        if (!r.ok) throw new Error('candles ' + r.status);
        return r.json();
      })
      .then(function (j) { return RD.readCandles(j); });
  }

  function boot() {
    var root = document.getElementById('root');
    var P = readParams(window.location.search);
    if (!P.ok) {
      root.innerHTML = state('This link does not name a market.',
        'A chart link carries the symbol it is about; this one could not be read.');
      return;
    }
    var TV = window.RCTVChart, SC = window.RCSignalChart, RD = window.RCEmbedRead;
    document.title = P.symbol + ' · ' + P.tf + ' — RUNECLAW';
    root.innerHTML = shellHtml(P, TV);
    var host = document.getElementById('e-ch-chart');
    var handle = null;

    function draw(rows) {
      // No library: the SVG chart, as every other signal chart falls back.
      if (!TV || !window.LightweightCharts) {
        SC.render(host, rows, P.geo, { label: P.symbol + ' ' + P.tf });
        return;
      }
      var spec = specFor(rows, P, SC);
      if (!spec.ok) {
        if (handle && TV) TV.release(host);
        handle = null;
        host.innerHTML = SC.placeholderHtml(spec.reason);
        return;
      }
      // A refresh updates the series in place, so the reader's zoom and
      // scroll survive it; only the first draw mounts.
      if (handle && handle.ok && handle.series) { handle.series.setData(spec.bars); return; }
      handle = TV.mount(host, spec, { compact: false, pageScroll: true, attributionLogo: false });
      if (!handle.ok) host.innerHTML = SC.placeholderHtml(spec.reason || 'unreadable');
    }

    function load() {
      return fetchRows(P, RD).then(draw).catch(function () {
        // A failed read is not a flat market: the chart already on screen
        // stays, and a first read that failed says what happened.
        if (handle && handle.ok) return;
        host.innerHTML = state('Candles could not be loaded.',
          'This is a fault reaching the market data, not a statement about the market.');
      });
    }

    load().then(function () {
      var FR = window.RCFarcasterReady;
      if (FR) FR.signalReady({});
    });
    setInterval(load, REFRESH_MS);
  }

  if (typeof window !== 'undefined' && typeof document !== 'undefined'
      && document.getElementById && document.getElementById('root')) {
    boot();
  }

  return { readParams: readParams, fmt: fmt, specFor: specFor, BARS: BARS, TIMEFRAMES: TIMEFRAMES, DEFAULT_TF: DEFAULT_TF };
}));
