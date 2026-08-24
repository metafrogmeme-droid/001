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

  // `num`, `price` and `pct` moved to embed-row.js with the card that used
  // them. Left behind here they would be a second copy of the em-dash rule,
  // free to drift from the one the cards actually render through.

  function state(kind, text, detail) {
    return '<div class="e-state e-state--' + kind + '" role="status">'
      + '<p>' + esc(text) + '</p>'
      + (detail ? '<p class="e-sub">' + esc(detail) + '</p>' : '')
      + '<p class="e-src">RUNECLAW · <a href="/" target="_blank" rel="noopener">open the dashboard</a></p>'
      + '</div>';
  }

  // The card lives in embed-row.js as pure functions. It was inline here, and
  // that was fine while it only printed prices — but the fields added with the
  // redesign (age, R:R) are the exact shapes CLAUDE.md's table says lie by
  // default, and inline in a loader "what does a signal with an unreadable
  // timestamp look like" is answerable only by finding one in production.
  var ROW = window.RCEmbedRow;

  /**
   * Is there a Mini App host to share to?
   *
   * Asked ONCE at load, not per render: the answer cannot change without the
   * page being reloaded, and re-deciding it on every 30s refresh would let the
   * share buttons appear and disappear under the reader.
   *
   * This is the same endpoint test `signalReady` uses, so the button appears
   * exactly when there is something on the other end to receive it. On a plain
   * website embed there is no composer in existence and no button is drawn —
   * an affordance that silently does nothing is a claim of a capability that
   * is not there.
   */
  var CAN_SHARE = (function () {
    var FR = window.RCFarcasterReady;
    try { return !!(FR && FR.pickEndpoint(window, document)); } catch (e) { return false; }
  }());

  function rowHtml(s) {
    return ROW.rowHtml(s, {
      canShare: CAN_SHARE,
      // The link is what makes the cast useful to whoever reads it, and it
      // must be absolute: a relative path in a post on somebody else's
      // timeline resolves against THEIR host. Taken from the live location so
      // it cannot drift from wherever this page is actually served.
      shareSuffix: window.location.origin + '/embed/signals',
    });
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

  /**
   * Lift the Mini App splash once, whatever the board ended up showing.
   *
   * ON EVERY OUTCOME, including the error state, and that is deliberate. The
   * host holds a splash screen over us until it hears this; withholding it
   * while our fetch failed would leave a Farcaster user staring at a splash
   * with nothing behind it, which reads as "this app is broken" rather than
   * "this app could not reach its own API" — the same substitution of OUR
   * silence for THEIR failure that the board's error text exists to avoid.
   * The page has rendered its honest answer; the viewer is entitled to see it.
   *
   * Once, not per refresh: `load()` runs every 30s and re-announcing readiness
   * on a timer is noise the host never asked for.
   */
  var announced = false;
  function announceReady() {
    if (announced) return;
    announced = true;
    var FR = window.RCFarcasterReady;
    if (!FR) return;                 // module absent; nothing to announce with
    FR.signalReady({});              // never rejects — see farcaster-ready.js
  }

  /**
   * One delegated listener for every share button, now and after each refresh.
   *
   * Delegated deliberately: `load()` replaces the whole list every 30 seconds,
   * so per-button listeners would be re-bound on a timer and the ones attached
   * to replaced nodes would leak. This binds once to a node that never goes
   * away.
   *
   * The handler's entire authority is one postMessage to the host. It issues no
   * fetch, writes no storage, and cannot post a cast: Warpcast opens its own
   * composer and the person confirms there, in UI we neither see nor control.
   */
  function onShareClick(ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest('.e-share');
    if (!btn) return;
    var text = btn.getAttribute('data-share-text');
    if (!text) return;
    var FR = window.RCFarcasterReady;
    if (!FR) return;

    // Disabled while the composer is open, so a second tap cannot stack a
    // second composer on top of the first.
    btn.disabled = true;
    FR.composeCast({ text: text }).then(function (res) {
      btn.disabled = false;
      // 'cancelled' is a DECISION, not a failure, and gets no error styling.
      // 'unknown' means the composer is probably still open and we do not know
      // what happened — so the button says nothing rather than claiming either.
      if (res && res.status === 'posted') {
        btn.textContent = 'Shared';
        if (window.setTimeout) window.setTimeout(function () { btn.textContent = 'Share'; }, 4000);
      }
    }, function () { btn.disabled = false; });
  }
  root.addEventListener('click', onShareClick);

  load().then(announceReady, announceReady);
  // And unconditionally, shortly after. `load()` always settles today — its
  // .catch() sees to that — but `fetch` carries no timeout, so a connection
  // that opens and then hangs leaves the promise pending forever and the
  // splash up with it. That is this exact bug arriving through a different
  // door, so the announcement does not depend on the fetch finishing at all.
  // Idempotent via `announced`; whichever path gets there first wins.
  setTimeout(announceReady, 3000);
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
