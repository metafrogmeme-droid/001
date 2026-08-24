/*
 * The embeddable arena board.
 *
 * Same contract as embed-signals.js and for the same reasons: read-only,
 * unauthenticated, `credentials: 'omit'`, no action but the share button. It
 * runs inside somebody else's page and every state names whether what is on
 * screen is a reading or the absence of one.
 *
 * ONE FETCH, NOT THREE. `/api/arena/season` returns the season AND its
 * standings in a single response, so the board's primary state costs one
 * request. The tape is a second, and it is treated as OPTIONAL — a dead tape
 * must not blank a live leaderboard, which is the composite-view strategy from
 * CLAUDE.md's table. The standings are the guard case: if they cannot be read,
 * there is no board and the page says so.
 */
(function () {
  'use strict';

  var root = document.getElementById('root');
  // Deliberately NOT `|| {}`. If the module failed to load, every call throws
  // into the same .catch as a network fault and the page paints an error —
  // which is right, because we cannot read the response and must not describe
  // it. See embed-read.js for the bug this shape is a correction for.
  var V = window.RCArenaView;
  var REFRESH_MS = 30000;
  var timer = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function state(kind, text, detail) {
    return '<div class="e-state e-state--' + kind + '" role="status">'
      + '<p>' + esc(text) + '</p>'
      + (detail ? '<p class="e-sub">' + esc(detail) + '</p>' : '')
      + '<p class="e-src">RUNECLAW · <a href="/" target="_blank" rel="noopener">open the dashboard</a></p>'
      + '</div>';
  }

  /** Is there a Mini App host to share to? Asked once — see embed-signals.js. */
  var CAN_SHARE = (function () {
    var FR = window.RCFarcasterReady;
    try { return !!(FR && FR.pickEndpoint(window, document)); } catch (e) { return false; }
  }());

  function shareHtml(season, rows, total) {
    if (!CAN_SHARE) return '';
    var text = V.shareText(season, rows, {
      rankedTotal: total,
      suffix: window.location.origin + '/embed/arena',
    });
    if (!text) return '';
    return '<div class="e-act">'
      + '<button type="button" class="e-share" data-share-text="' + esc(text) + '">'
      + 'Share</button></div>';
  }

  /**
   * The tape, fetched separately and allowed to fail quietly.
   *
   * OMIT, not guard. The standings are what this page is for; a dead tape
   * endpoint must not replace a perfectly good leaderboard with an error. It
   * returns '' on any fault and the section simply is not there — which is
   * honest because the section makes no claim by its absence, unlike a heading
   * with nothing under it (the `_status_lines` failure CLAUDE.md records).
   */
  function loadTape() {
    return fetch('/api/arena/tape', { credentials: 'omit' })
      .then(function (r) {
        if (!r.ok) throw new Error('tape ' + r.status);
        return r.json();
      })
      .then(function (j) { return V.tapeHtml(V.readTape(j)); })
      .catch(function () { return ''; });
  }

  function load() {
    // The season response carries the standings with it, so the page's primary
    // state is one request. `credentials: 'omit'` because inside a frame the
    // browser would otherwise attach the viewer's cookies for this origin.
    return fetch('/api/arena/season', { credentials: 'omit' })
      .then(function (r) {
        // A non-ok response is NOT an empty arena. "Nobody has joined" and "we
        // could not ask" are different sentences and the viewer gets the right
        // one.
        if (!r.ok) throw new Error('season ' + r.status);
        return r.json();
      })
      .then(function (j) {
        var season = V.readSeason(j);
        // An UPCOMING season has no rows key at all, and that is correct — it
        // has not been played. Absent rows there is not a failed read, so the
        // board renders as empty-by-measurement rather than throwing.
        var rows = Array.isArray(j.rows) ? j.rows : [];
        return loadTape().then(function (tape) {
          root.innerHTML = V.seasonHtml(season)
            + V.standingsHtml(rows)
            + tape
            + shareHtml(season, rows, j.ranked_total)
            + '<p class="e-src">Paper trading · virtual stakes · '
            + '<a href="/arena" target="_blank" rel="noopener">join the arena</a></p>';
        });
      })
      .catch(function () {
        root.innerHTML = state('error', 'The arena board could not be loaded.',
          'This is a fault on our side, not a statement that nobody is competing.');
      });
  }

  // Share, delegated once — the list is replaced on every refresh, so
  // per-button listeners would leak. Identical handler to the signals board;
  // its whole authority is one postMessage to the host.
  root.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest('.e-share');
    if (!btn) return;
    var text = btn.getAttribute('data-share-text');
    var FR = window.RCFarcasterReady;
    if (!text || !FR) return;
    btn.disabled = true;
    FR.composeCast({ text: text }).then(function (res) {
      btn.disabled = false;
      if (res && res.status === 'posted') {
        btn.textContent = 'Shared';
        if (window.setTimeout) window.setTimeout(function () { btn.textContent = 'Share'; }, 4000);
      }
    }, function () { btn.disabled = false; });
  });

  /** Lift the Mini App splash once, on every outcome — see embed-signals.js. */
  var announced = false;
  function announceReady() {
    if (announced) return;
    announced = true;
    var FR = window.RCFarcasterReady;
    if (!FR) return;
    FR.signalReady({});
  }

  load().then(announceReady, announceReady);
  // And regardless: fetch carries no timeout, so a hung connection would leave
  // load() pending forever and the splash up with it.
  setTimeout(announceReady, 3000);

  timer = setInterval(load, REFRESH_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (timer) { clearInterval(timer); timer = null; }
    } else if (!timer) {
      load();
      timer = setInterval(load, REFRESH_MS);
    }
  });
}());
