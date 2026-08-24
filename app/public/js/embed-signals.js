/*
 * The embeddable signal board.
 *
 * Runs inside somebody else's page, which changes what "handle the error" means.
 * On the dashboard an unreadable panel can say "retry" and the operator knows
 * where they are. Here the viewer may not know RUNECLAW exists — so every state
 * names the source and says plainly whether the thing on screen is a reading or
 * the absence of one.
 *
 * Strictly read-only and unauthenticated: no cookies, no Authorization header,
 * no actions. That is what makes the page safe to frame at all (see
 * routes/embed.js), so a future edit adding a button here is a security change,
 * not a feature — `embed_frame_policy.test.js` fails on it.
 */
(function () {
  'use strict';

  var root = document.getElementById('root');
  // The payload readers. If the module did not load, every read throws a
  // TypeError that lands in the same `.catch` as a network fault and paints the
  // error state — which is the right answer: we cannot read the response, so we
  // must not describe it. Deliberately NOT a `|| {}` fallback; that is the
  // shape of bug this whole file is a correction for.
  var RD = window.RCEmbedRead;
  var LIMIT = 8;
  var REFRESH_MS = 30000;
  var timer = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /** A price, or an em dash. Never 0 for absent — 0 is a price nothing trades at
   *  and printing it would put a real-looking number beside real ones. */
  function price(v) {
    var n = num(v);
    if (n === null) return '—';
    var abs = Math.abs(n);
    var dp = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
    return n.toFixed(dp).replace(/\.?0+$/, function (m) { return m.indexOf('.') === 0 ? '' : m; });
  }

  function pct(v) {
    var n = num(v);
    return n === null ? '—' : Math.round(n * 100) + '%';
  }

  function state(kind, text, detail) {
    return '<div class="e-state e-state--' + kind + '" role="status">'
      + '<p>' + esc(text) + '</p>'
      + (detail ? '<p class="e-sub">' + esc(detail) + '</p>' : '')
      + '<p class="e-src">RUNECLAW · <a href="/" target="_blank" rel="noopener">open the dashboard</a></p>'
      + '</div>';
  }

  function rowHtml(s) {
    var dir = String(s.direction || '').toUpperCase();
    // Direction is a fact the signal asserts about itself, so it is coloured.
    // Nothing else on the row is: whether the trade is WINNING is not something
    // this board knows, and a green row would say it does.
    var dirCls = dir === 'LONG' ? 'e-long' : dir === 'SHORT' ? 'e-short' : 'e-flat';
    var geo = JSON.stringify({
      entry: s.entry_price, stop: s.stop_loss, target: s.take_profit, direction: dir,
    });
    return '<article class="e-row">'
      + '<header class="e-head">'
      + '<span class="e-dir ' + dirCls + '">' + esc(dir || '—') + '</span>'
      + '<b class="e-sym">' + esc(s.symbol || '—') + '</b>'
      + '<span class="e-conf">' + pct(s.confidence) + ' conf</span>'
      + '</header>'
      // Two symbols, deliberately. `data-sc-sym` is what Bitget is ASKED about
      // and must be the contract form; `data-sc-label` is what the viewer
      // reads. They were one value, the display one, and so the fetch asked
      // about a market that does not exist — see RCEmbedRead.contractSym.
      + '<div class="e-chart" data-sc-sym="' + esc(RD.contractSym(s.symbol))
      + '" data-sc-label="' + esc(RD.displaySym(s.symbol)) + '" data-sc-geo=\''
      + esc(geo) + '\'><div class="e-load">…</div></div>'
      + '<dl class="e-lv">'
      + '<div><dt>entry</dt><dd>' + esc(price(s.entry_price)) + '</dd></div>'
      + '<div><dt>stop</dt><dd>' + esc(price(s.stop_loss)) + '</dd></div>'
      + '<div><dt>target</dt><dd>' + esc(price(s.take_profit)) + '</dd></div>'
      + '</dl>'
      + '</article>';
  }

  var candleCache = new Map();
  function fetchCandles(sym) {
    var hit = candleCache.get(sym);
    if (hit && Date.now() - hit.ts < 25000) return Promise.resolve(hit.rows);
    return fetch('/api/market/candles/' + encodeURIComponent(sym) + '?granularity=1h&limit=60',
      { credentials: 'omit' })
      .then(function (r) {
        if (!r.ok) throw new Error('candles ' + r.status);
        return r.json();
      })
      .then(function (j) {
        // Same defect as the signal reader: the route relays Bitget verbatim,
        // so the rows are at `j.data`, and `j.data.data` was undefined every
        // time — every chart would have drawn "no candles", a claim about the
        // market, from a payload we simply misread.
        var rows = RD.readCandles(j);
        candleCache.set(sym, { ts: Date.now(), rows: rows });
        return rows;
      });
  }

  function drawCharts() {
    var SC = window.RCSignalChart;
    if (!SC) return;                       // the renderer failed to load; leave the slot
    var slots = root.querySelectorAll('.e-chart[data-sc-sym]:not([data-done])');
    Array.prototype.forEach.call(slots, function (el) {
      el.setAttribute('data-done', '1');
      var sym = el.getAttribute('data-sc-sym');
      var label = el.getAttribute('data-sc-label') || sym;
      var geo = {};
      try { geo = JSON.parse(el.getAttribute('data-sc-geo') || '{}') || {}; } catch (e) { geo = {}; }
      // No contract symbol means there is no market to ask about. Say that,
      // rather than fetching `/candles/` and describing whatever answers.
      if (!sym) { el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE); return; }
      fetchCandles(sym).then(function (rows) {
        if (!el.isConnected) return;
        var out = SC.buildSignalChart(rows, geo, { label: label });
        el.innerHTML = out.ok ? out.svg : SC.placeholderHtml(out.reason);
      }).catch(function () {
        if (!el.isConnected) return;
        // The slot keeps its space and says what happened. It does not vanish,
        // and it does not draw a flat line at zero.
        el.innerHTML = SC.placeholderHtml(SC.REASONS.UNREADABLE);
      });
    });
  }

  function load() {
    // `credentials: 'omit'` is not incidental. Inside a frame the browser would
    // otherwise attach whatever cookies the viewer has for this origin, which
    // would make an authenticated request from a page a stranger controls.
    return fetch('/api/signals?limit=' + LIMIT, { credentials: 'omit' })
      .then(function (r) {
        // A non-ok response is NOT an empty board. "The engine has produced no
        // signals" and "we could not ask" are different sentences and the
        // viewer gets the right one.
        if (!r.ok) throw new Error('signals ' + r.status);
        return r.json();
      })
      .then(function (j) {
        // Reads `{signals:[...]}`, the shape the route actually sends, and
        // THROWS if the key is missing rather than falling back to []. The old
        // `(j && j.data && j.data.signals) || []` read a `fetchJSON` envelope
        // this page never uses, so it was empty on every load and the board
        // announced "No open signals right now." forever. See embed-read.js.
        var sigs = RD.readSignals(j);
        if (!sigs.length) {
          root.innerHTML = state('empty', 'No open signals right now.',
            'The engine publishes them as it scans; this board refreshes itself.');
          return;
        }
        root.innerHTML = '<div class="e-list">' + sigs.map(rowHtml).join('') + '</div>'
          + '<p class="e-src">Live from RUNECLAW · '
          + '<a href="/#signals" target="_blank" rel="noopener">every signal, taken or not</a></p>';
        drawCharts();
      })
      .catch(function () {
        root.innerHTML = state('error', 'Signals could not be loaded.',
          'This is a fault on our side, not a statement that there are none.');
      });
  }

  load();
  timer = setInterval(load, REFRESH_MS);
  // A framed page can be hidden for a long time; stop polling when it is.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (timer) { clearInterval(timer); timer = null; }
    } else if (!timer) {
      load();
      timer = setInterval(load, REFRESH_MS);
    }
  });
}());
