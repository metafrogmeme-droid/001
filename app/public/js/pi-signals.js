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
 * ONE DOOR PER CALL, never two (app/lib/arena_signal_trade.js, rules 3-5):
 *   live                   "Paper 200 · 2×" follows the call, at the live
 *                          mark, with the same 200 vUSDT at 2x the website's
 *                          signal stream uses. A countdown says how long.
 *   window closed, not     "Trade it yourself": the call's side as the
 *   yet ended              member's OWN trade at the live price, not
 *                          credited to the call (the server records it as a
 *                          manual position with no signal key).
 *   ended                  no button; what became of it, in the dashboard's
 *                          words (signal-status-model.js).
 * The server fills, applies the season's rules, and names any refusal; the
 * page shows that sentence as sent.
 *
 * THE CHARTS can be pinched to zoom and dragged back through time while a
 * vertical drag still scrolls the app, on the website Arena's four
 * timeframes. A zoom survives the panel's own redraws, and a timeframe switch
 * or a closing window redraws only what changed: the screen is rebuilt
 * wholesale on a render, which would reset every chart under the reader's
 * fingers.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiSignals = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var LIMIT = 6;
  var REFRESH_MS = 60000;
  var PAPER = { margin: 200, leverage: 2 };
  /** The website Arena's timeframes (arena.html TFS), in the candle relay's spelling. */
  var TFS = [['15min', '15m'], ['1h', '1H'], ['4h', '4H'], ['1d', '1D']];
  var DEFAULT_TF = '1h';
  /** Bars asked of the relay (its ceiling), i.e. how far back a drag can go. */
  var BARS = 200;
  /** Bars the first view of a chart shows. */
  var VISIBLE_BARS = 60;
  var TF_KEY = 'rc.pi.signals.tf';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /** A window's end in the reader's own clock, HH:MM. */
  function clockText(ms) {
    var d = new Date(ms);
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }

  function isTf(v) {
    return TFS.some(function (t) { return t[0] === v; });
  }

  /** The reader's last timeframe: a convenience, so storage that fails is the default. */
  function readTf() {
    try {
      var v = window.localStorage.getItem(TF_KEY);
      return isTf(v) ? v : DEFAULT_TF;
    } catch (e) {
      return DEFAULT_TF;
    }
  }

  function saveTf(v) {
    try { window.localStorage.setItem(TF_KEY, v); } catch (e) { /* the choice lasts this visit */ }
  }

  /**
   * What a card offers, from the one reading of a call's state
   * (signal-status-model.js). `now` is injected so a test can stand on
   * either side of a window.
   */
  function actionInner(sig, now, busy) {
    var SS = window.SignalStatusModel;
    if (!SS) return '';
    var key = esc(sig.signal_key);
    var dis = busy ? ' disabled' : '';
    if (SS.actionable(sig, now)) {
      var until = SS.liveUntil(sig);
      var word = 'Live call · follow it within {t}';
      return '<p class="p-sig-when" data-live-until="' + esc(until) + '" data-live-word="' + esc(word)
        + '" data-closed-word="Window closed">' + esc(word.replace('{t}', SS.leftText(sig, now))) + '</p>'
        + '<div class="m-row m-row--go"><button type="button" class="p-sig-open" data-key="' + key
        + '" data-live-until="' + esc(until) + '"' + dis + '>Paper '
        + PAPER.margin + ' · ' + PAPER.leverage + '×</button></div>';
    }
    if (SS.ownable(sig, now)) {
      var closedAt = SS.liveUntil(sig);
      var when = closedAt === null ? 'This call states no window.'
        : 'Its window closed at ' + clockText(closedAt) + '.';
      return '<p class="p-sig-when">' + esc(when)
        + ' Taking it now is your own trade at the live price, not credited to the call.</p>'
        + '<div class="m-row m-row--go"><button type="button" class="p-sig-own" data-key="' + key + '"'
        + dis + '>Trade it yourself · ' + PAPER.margin + ' · ' + PAPER.leverage + '×</button></div>';
    }
    var st = SS.status(sig);
    if (st.final) {
      // Colour is a claim: only the two words that carry an R get one.
      var tone = st.cls === 'chip--up' ? ' p-sig-out--up' : st.cls === 'chip--down' ? ' p-sig-out--down' : '';
      return '<p class="p-sig-when"><span class="p-sig-out' + tone + '">' + esc(st.label)
        + '</span> This call has ended.</p>';
    }
    return '';
  }

  /** What an open got, and what of the call it did not carry. */
  function filledText(f) {
    var text = 'Opened ' + f.symbol + ' ' + f.direction + ' at ' + f.entry + ', the live mark'
      + (f.own ? ', as your own trade. It is not credited to the call.' : '.');
    var dropped = Array.isArray(f.dropped) ? f.dropped : [];
    if (dropped.length === 2) text += ' Both its exits were already behind the market, so neither was carried over.';
    else if (dropped[0] === 'tp') text += ' Its take-profit was already behind the market and was not carried over.';
    else if (dropped[0] === 'sl') text += ' Its stop-loss was already behind the market and was not carried over.';
    if (f.drift_warn === true && typeof f.drift_pct === 'number' && f.signal_entry != null) {
      text += ' That is ' + (f.drift_pct > 0 ? '+' : '') + f.drift_pct + '% from the call\'s entry of '
        + f.signal_entry + '.';
    }
    return text;
  }

  function create() {
    var sigs = null;        // null: not read yet; [] read and empty
    var failed = false;
    var lastRead = null;    // what the last read said, to skip a render that would change nothing
    var note = null;        // [kind, text]
    var busy = false;
    var timer = null;
    var ctx = null;
    var candles = {};       // symbol|granularity -> { ts, read }: a read in flight is shared
    var views = {};         // symbol|granularity -> { fromEnd, toEnd }: a reader's zoom and pan
    var tf = readTf();
    var countdown = null;   // the one-second countdown while a live call is on screen
    var flip = null;        // the redraw of the actions when the soonest live window closes
    var panel = null;       // the root the last render mounted

    function load() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/signals?limit=' + LIMIT).then(function (r) {
        if (!r.ok) throw new Error('signals ' + r.status);
        // Throws on a payload without `signals`: unreadable is not empty.
        sigs = window.RCEmbedRead.readSignals(r.body);
        failed = false;
      }).catch(function () {
        failed = true;
      }).then(function () {
        // A minute's re-read that found the same calls renders nothing: a
        // render rebuilds the whole screen, the reader's charts included.
        var read = failed ? 'failed' : JSON.stringify(sigs);
        if (read === lastRead) return;
        lastRead = read;
        ctx.render();
      });
    }

    function startTimer() {
      if (timer || typeof document === 'undefined' || document.hidden) return;
      timer = setInterval(load, REFRESH_MS);
    }

    function signedIn(c) {
      ctx = c;
      load();
      startTimer();
      // A hidden app is not polled, and its countdowns do not tick.
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          if (timer) { clearInterval(timer); timer = null; }
          stopClocks();
        } else if (ctx) {
          load();
          startTimer();
          if (panel) { redrawActions(); startClocks(); }
        }
      });
    }

    function cardHtml(sig) {
      if (sig.locked === true) {
        var sym = window.RCEmbedRow.splitSymbol(sig.symbol);
        return '<article class="e-row e-row--locked"><header class="e-head">'
          + '<span class="e-dir e-flat">LIVE</span><b class="e-sym">' + esc(sym.base || '—') + '</b>'
          + '</header><p class="e-meta">A live call. Sign in again to see its side and levels.</p></article>';
      }
      // The action goes inside the card, so it reads as this call's.
      var row = window.RCEmbedRow.rowHtml(sig, {});
      var act = '<div class="p-sig-act" data-key="' + esc(sig.signal_key) + '">' + actionInner(sig, new Date(), busy) + '</div>';
      var end = row.lastIndexOf('</article>');
      return end < 0 ? row + act : row.slice(0, end) + act + row.slice(end);
    }

    function tfHtml() {
      return '<div class="p-tf" role="group" aria-label="Chart timeframe">'
        + TFS.map(function (t) {
          return '<button type="button" class="p-tf-b" data-tf="' + t[0] + '" aria-pressed="'
            + (t[0] === tf ? 'true' : 'false') + '">' + t[1] + '</button>';
        }).join('') + '</div>';
    }

    function html() {
      var M = window.RCMiniView;
      var noteHtml = note ? M.actionNoteHtml(note[0], note[1]) : '';
      var body;
      var charts = false;
      if (failed) {
        body = M.actionNoteHtml('warn',
          'Signals could not be loaded. This is on our side, not a statement that there are none.');
      } else if (sigs === null) {
        body = '<div class="e-load">Loading signals…</div>';
      } else if (!sigs.length) {
        body = M.actionNoteHtml('note', 'No signals right now. The engine publishes them as it scans.');
      } else {
        charts = true;
        body = '<div class="e-list">' + sigs.map(cardHtml).join('') + '</div>';
      }
      return '<section class="m-open p-signals"><div class="p-sig-head"><h2>Signals</h2>'
        + (charts ? tfHtml() : '') + '</div>' + noteHtml + body
        + '<p class="m-open-note">Paper opens at the live mark · virtual funds only'
        + (charts ? ' · pinch a chart to zoom, drag it to look back' : '') + '</p></section>';
    }

    /**
     * One read per chart per 25 seconds. A read still in flight is joined,
     * not repeated: the screen renders again while the first chart is still
     * loading (the account and the tips arrive after the calls), and each
     * render used to ask the relay for the same bars again. A failed read is
     * forgotten, so the next draw asks afresh.
     */
    function candlesFor(sym, gran) {
      var k = sym + '|' + gran;
      var hit = candles[k];
      if (hit && Date.now() - hit.ts < 25000) return hit.read;
      var read = ctx.api('/api/market/candles/' + encodeURIComponent(sym) + '?granularity=' + gran + '&limit=' + BARS)
        .then(function (r) {
          if (!r.ok) throw new Error('candles ' + r.status);
          return window.RCEmbedRead.readCandles(r.body);
        });
      candles[k] = { ts: Date.now(), read: read };
      read.catch(function () { if (candles[k] && candles[k].read === read) delete candles[k]; });
      return read;
    }

    /** Draw one chart slot at the current timeframe, keeping the reader's zoom on it. */
    function drawSlot(el) {
      var SC = window.RCSignalChart;
      el.setAttribute('data-done', '1');
      var sym = el.getAttribute('data-sc-sym');
      var label = el.getAttribute('data-sc-label') || sym;
      var geo = {};
      try { geo = JSON.parse(el.getAttribute('data-sc-geo') || '{}') || {}; } catch (e) { geo = {}; }
      if (!sym) { el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE); return; }
      var gran = tf;
      var k = sym + '|' + gran;
      candlesFor(sym, gran).then(function (rows) {
        if (!el.isConnected || gran !== tf) return;
        SC.render(el, rows, geo, {
          label: label, timeAxis: true, interactive: true,
          maxBars: BARS, visibleBars: VISIBLE_BARS, view: views[k],
          onView: function (v) { views[k] = v; },
        });
      }).catch(function () {
        if (!el.isConnected || gran !== tf) return;
        el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE);
      });
    }

    function drawCharts(rootEl, all) {
      if (!window.RCSignalChart || !ctx) return;
      var sel = '.p-signals .e-chart[data-sc-sym]' + (all ? '' : ':not([data-done])');
      Array.prototype.forEach.call(rootEl.querySelectorAll(sel), drawSlot);
    }

    /** Each card's actions brought up to now, without touching its chart. */
    function redrawActions() {
      if (!panel || !sigs) return;
      var now = new Date();
      Array.prototype.forEach.call(panel.querySelectorAll('.p-signals .p-sig-act'), function (el) {
        var key = el.getAttribute('data-key');
        var sig = sigs.filter(function (s) { return String(s.signal_key) === key; })[0];
        if (sig) el.innerHTML = actionInner(sig, now, busy);
      });
    }

    function stopClocks() {
      if (countdown) clearInterval(countdown);
      if (flip) clearTimeout(flip);
      countdown = null;
      flip = null;
    }

    /**
     * While a live call is on screen: its countdown, every second, and a
     * redraw of the actions just after the soonest window closes, so its
     * card turns from "Paper" to "Trade it yourself" in front of the reader
     * rather than at the next minute's read.
     */
    function startClocks() {
      stopClocks();
      var SS = window.SignalStatusModel;
      if (!SS || !panel || !sigs || (typeof document !== 'undefined' && document.hidden)) return;
      var now = new Date();
      var soonest = null;
      sigs.forEach(function (s) {
        if (!SS.actionable(s, now)) return;
        var u = SS.liveUntil(s);
        if (soonest === null || u < soonest) soonest = u;
      });
      if (soonest === null) return;
      var section = panel.querySelector('.p-signals');
      countdown = setInterval(function () { SS.tick(section, new Date()); }, 1000);
      // The window's last instant is still inside it (SS.live), so a second after.
      flip = setTimeout(function () {
        flip = null;
        redrawActions();
        startClocks();
      }, Math.max(0, soonest - now.getTime()) + 1000);
    }

    /** After every render: draw each chart slot the render just made, and start the clocks. */
    function mounted(rootEl) {
      if (!ctx) return;
      panel = rootEl;
      if (window.RCTVChart) window.RCTVChart.sweep();   // charts the last render replaced
      drawCharts(rootEl, false);
      startClocks();
    }

    /** The session's Refresh button: the calls read again, and their charts' bars. */
    function reload() {
      if (!ctx) return Promise.resolve();
      lastRead = null;
      candles = {};
      return load();
    }

    function setTf(v) {
      if (!isTf(v) || v === tf) return;
      tf = v;
      saveTf(v);
      if (!panel) return;
      Array.prototype.forEach.call(panel.querySelectorAll('.p-signals .p-tf-b'), function (b) {
        b.setAttribute('aria-pressed', b.getAttribute('data-tf') === tf ? 'true' : 'false');
      });
      drawCharts(panel, true);
    }

    function openOnPaper(key, c, own) {
      if (busy || !key) return;
      busy = true;
      note = ['note', own ? 'Opening your own trade on paper…' : 'Opening on paper…'];
      c.render();
      var body = { signal_key: key, margin: PAPER.margin, leverage: PAPER.leverage };
      if (own) body.own = true;
      c.api('/api/arena/open-signal', { method: 'POST', body: body }).then(function (r) {
        busy = false;
        var f = r.ok && r.body && r.body.filled;
        if (f) {
          // A toast, like the arena's own open: it replaces whatever the last
          // action said. As a note in this panel it left the arena's toast
          // ("Position closed: …") on screen over the trade just opened.
          if (typeof c.toast === 'function') { note = null; c.toast('ok', filledText(f)); }
          else note = ['ok', filledText(f)];
          return c.refresh();
        }
        // The server names why (window closed, still live, ended, already
        // open, season rules).
        note = ['warn', (r.body && r.body.error) || 'That call could not be opened.'];
        c.render();
        // The call changed under the page: read the calls again, so the card
        // offers what is true now.
        var code = r.body && r.body.code;
        if (code === 'expired' || code === 'live' || code === 'ended') {
          lastRead = null;
          return load();
        }
      }, function () {
        busy = false;
        note = ['warn', 'That call could not be opened. We could not reach the server.'];
        c.render();
      });
    }

    function click(target, c) {
      var t = target.closest('.p-tf-b');
      if (t) { setTf(t.getAttribute('data-tf')); return true; }
      var b = target.closest('.p-sig-open, .p-sig-own');
      if (!b) return false;
      var own = b.classList.contains('p-sig-own');
      // A follow button can outlive its window by up to a second: ask again.
      var SS = window.SignalStatusModel;
      if (!own && (!SS || !SS.elementLive(b, new Date()))) { redrawActions(); return true; }
      openOnPaper(b.getAttribute('data-key'), c, own);
      return true;
    }

    return { place: 'main', tab: 'signals', label: 'Signals', icon: '\u224B', html: html, mounted: mounted, click: click, signedIn: signedIn, reload: reload };
  }

  return {
    create: create, actionInner: actionInner, filledText: filledText,
    LIMIT: LIMIT, PAPER: PAPER, TFS: TFS, BARS: BARS, VISIBLE_BARS: VISIBLE_BARS,
  };
}));
