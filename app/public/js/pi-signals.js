/*
 * The engine's signals in the Pi Browser app, with the site's charts.
 *
 * A block for arena-session.js's signed-in screen (the `extras` list): the
 * latest calls from /api/signals through the session's own api(), so a
 * signed-in Pi user sees live calls in full, exactly as a signed-in member
 * does on the website. Each card is embed-row.js's, and each chart is the
 * site's TradingView chart (signal-chart.js, the SVG when the library did not
 * load), drawn from our own candle relay. Nothing here talks to anyone but us.
 *
 * NO LINK BACK TO THE SITE. The Pi app carries no presale and links nowhere
 * on the site (routes/piapp.js), and embed-row.js's locked card links to "/".
 * A signed-in viewer is never sent a locked call, so this block renders its
 * own sentence for one instead of that card.
 *
 * ONE ACTION: open a live call on paper, at the live mark, with the same
 * 200 vUSDT at 2x the website's signal stream uses. The server fills it,
 * applies the season's rules, and names any refusal; the page shows that
 * sentence as sent.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiSignals = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var LIMIT = 6;
  var REFRESH_MS = 60000;
  var PAPER = { margin: 200, leverage: 2 };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function create() {
    var sigs = null;        // null: not read yet; [] read and empty
    var failed = false;
    var note = null;        // [kind, text]
    var busy = false;
    var timer = null;
    var ctx = null;
    var candles = {};       // symbol -> { ts, rows }

    function load() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/signals?limit=' + LIMIT).then(function (r) {
        if (!r.ok) throw new Error('signals ' + r.status);
        // Throws on a payload without `signals`: unreadable is not empty.
        sigs = window.RCEmbedRead.readSignals(r.body);
        failed = false;
      }).catch(function () {
        failed = true;
      }).then(function () { ctx.render(); });
    }

    function startTimer() {
      if (timer || typeof document === 'undefined' || document.hidden) return;
      timer = setInterval(load, REFRESH_MS);
    }

    function signedIn(c) {
      ctx = c;
      load();
      startTimer();
      // A hidden app is not polled.
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          if (timer) { clearInterval(timer); timer = null; }
        } else if (ctx) {
          load();
          startTimer();
        }
      });
    }

    function cardHtml(sig) {
      var SS = window.SignalStatusModel;
      if (sig.locked === true) {
        var sym = window.RCEmbedRow.splitSymbol(sig.symbol);
        return '<article class="e-row e-row--locked"><header class="e-head">'
          + '<span class="e-dir e-flat">LIVE</span><b class="e-sym">' + esc(sym.base || '—') + '</b>'
          + '</header><p class="e-meta">A live call. Sign in again to see its side and levels.</p></article>';
      }
      var open = SS && SS.actionable(sig)
        ? '<div class="m-row m-row--go"><button type="button" class="p-sig-open" data-key="'
          + esc(sig.signal_key) + '"' + (busy ? ' disabled' : '') + '>Paper '
          + PAPER.margin + ' · ' + PAPER.leverage + '×</button></div>'
        : '';
      return window.RCEmbedRow.rowHtml(sig, {}) + open;
    }

    function html() {
      var M = window.RCMiniView;
      var noteHtml = note ? M.actionNoteHtml(note[0], note[1]) : '';
      var body;
      if (failed) {
        body = M.actionNoteHtml('warn',
          'Signals could not be loaded. This is on our side, not a statement that there are none.');
      } else if (sigs === null) {
        body = '<div class="e-load">Loading signals…</div>';
      } else if (!sigs.length) {
        body = M.actionNoteHtml('note', 'No signals right now. The engine publishes them as it scans.');
      } else {
        body = '<div class="e-list">' + sigs.map(cardHtml).join('') + '</div>';
      }
      return '<section class="m-open p-signals"><h2>Signals</h2>' + noteHtml + body
        + '<p class="m-open-note">Paper opens at the live mark · virtual funds only</p></section>';
    }

    function candlesFor(sym) {
      var hit = candles[sym];
      if (hit && Date.now() - hit.ts < 25000) return Promise.resolve(hit.rows);
      return ctx.api('/api/market/candles/' + encodeURIComponent(sym) + '?granularity=1h&limit=60')
        .then(function (r) {
          if (!r.ok) throw new Error('candles ' + r.status);
          var rows = window.RCEmbedRead.readCandles(r.body);
          candles[sym] = { ts: Date.now(), rows: rows };
          return rows;
        });
    }

    /** After every render: draw each chart slot the render just made. */
    function mounted(rootEl) {
      var SC = window.RCSignalChart;
      if (!SC || !ctx) return;
      if (window.RCTVChart) window.RCTVChart.sweep();   // charts the last render replaced
      var slots = rootEl.querySelectorAll('.p-signals .e-chart[data-sc-sym]:not([data-done])');
      Array.prototype.forEach.call(slots, function (el) {
        el.setAttribute('data-done', '1');
        var sym = el.getAttribute('data-sc-sym');
        var label = el.getAttribute('data-sc-label') || sym;
        var geo = {};
        try { geo = JSON.parse(el.getAttribute('data-sc-geo') || '{}') || {}; } catch (e) { geo = {}; }
        if (!sym) { el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE); return; }
        candlesFor(sym).then(function (rows) {
          if (!el.isConnected) return;
          SC.render(el, rows, geo, { label: label, timeAxis: false });
        }).catch(function () {
          if (!el.isConnected) return;
          el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE);
        });
      });
    }

    function openOnPaper(key, c) {
      if (busy || !key) return;
      busy = true;
      note = ['note', 'Opening on paper…'];
      c.render();
      c.api('/api/arena/open-signal', {
        method: 'POST',
        body: { signal_key: key, margin: PAPER.margin, leverage: PAPER.leverage },
      }).then(function (r) {
        busy = false;
        var f = r.ok && r.body && r.body.filled;
        if (f) {
          // What you got, and which of the call's exits the market had passed.
          var text = 'Opened ' + f.symbol + ' ' + f.direction + ' at ' + f.entry + ', the live mark.';
          var dropped = Array.isArray(f.dropped) ? f.dropped : [];
          if (dropped.length === 2) text += ' Both its exits were already behind the market, so neither was carried over.';
          else if (dropped[0] === 'tp') text += ' Its take-profit was already behind the market and was not carried over.';
          else if (dropped[0] === 'sl') text += ' Its stop-loss was already behind the market and was not carried over.';
          note = ['ok', text];
          return c.refresh();
        }
        // The server names why (window closed, already open, season rules).
        note = ['warn', (r.body && r.body.error) || 'That call could not be opened.'];
        return c.render();
      }, function () {
        busy = false;
        note = ['warn', 'That call could not be opened. We could not reach the server.'];
        c.render();
      });
    }

    function click(target, c) {
      var b = target.closest('.p-sig-open');
      if (!b) return false;
      openOnPaper(b.getAttribute('data-key'), c);
      return true;
    }

    return { place: 'main', html: html, mounted: mounted, click: click, signedIn: signedIn };
  }

  return { create: create, LIMIT: LIMIT, PAPER: PAPER };
}));
