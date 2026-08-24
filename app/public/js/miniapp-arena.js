/*
 * The arena Mini App: sign in with Farcaster, trade the season.
 *
 * THE SESSION IS A CLOSURE VARIABLE AND NOTHING ELSE. Not localStorage, not a
 * cookie, not a global. Two reasons, and the second is the one that matters:
 *
 *   - `embed_frame_policy` forbids browser storage on a framable page, and
 *     this page is framable by design.
 *   - A token that persists is a token that exists in a frame the viewer did
 *     not open. Held here, every fresh load of this page — including one
 *     inside an attacker's iframe — starts signed out, and the only way back
 *     in is a SIWF signature over a nonce our server issued seconds ago, in a
 *     message naming our domain. See routes/miniapp.js for the whole argument.
 *
 * The consequence is deliberate: closing the app signs you out. That is the
 * correct trade for a page anyone may frame, and it costs one tap to undo.
 */
(function () {
  'use strict';

  var root = document.getElementById('root');
  // Deliberately not `|| {}` — if a module failed to load, calling through it
  // throws into the same handler as a network fault and the page says so,
  // rather than rendering a board assembled from nothing.
  var V = window.RCArenaView;
  var M = window.RCMiniView;
  var FR = window.RCFarcasterReady;

  /** The bearer token. In memory, for the life of this page. */
  var token = null;
  var me = null;          // { fid, handle }
  var lastNote = '';      // the last action's message, cleared on the next one
  var busy = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function state(kind, text, detail) {
    return '<div class="e-state e-state--' + kind + '" role="status">'
      + '<p>' + esc(text) + '</p>'
      + (detail ? '<p class="e-sub">' + esc(detail) + '</p>' : '')
      + '</div>';
  }

  /**
   * Every call to our own API. `credentials: 'omit'` for the same reason the
   * public boards use it — inside a frame the browser would otherwise attach
   * whatever cookies the viewer holds for this origin. The bearer token is
   * sent explicitly, so authentication is something this page DOES rather than
   * something the browser does on its behalf.
   */
  function api(path, opts) {
    var o = opts || {};
    var headers = { accept: 'application/json' };
    if (o.body) headers['content-type'] = 'application/json';
    if (token) headers.authorization = 'Bearer ' + token;
    return fetch(path, {
      method: o.method || 'GET',
      credentials: 'omit',
      headers: headers,
      body: o.body ? JSON.stringify(o.body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (body) {
        // The status is kept alongside the body rather than thrown away: a 400
        // carrying "Unknown symbol — use a listed USDT-M pair" is the most
        // useful thing this app can show, and collapsing it into a generic
        // failure would discard the only part that says what to do next.
        return { ok: r.ok, status: r.status, body: body };
      });
    });
  }

  // ── sign in ─────────────────────────────────────────────────────────────

  /**
   * Nonce from our server, signature from the host, session back from us.
   *
   * The nonce is NOT generated here. One the page invents is one an attacker
   * can invent too, which binds the signature to nothing — see lib/siwf.js.
   */
  function signIn() {
    if (busy) return Promise.resolve();
    busy = true;
    render();

    return api('/api/farcaster/nonce', { method: 'POST' })
      .then(function (r) {
        if (!r.ok || !r.body || !r.body.nonce) {
          // We could not even start. Not the viewer's fault and not their
          // decision, so it is reported as ours.
          return { status: 'unavailable' };
        }
        return FR.signIn({ nonce: r.body.nonce });
      })
      .then(function (out) {
        if (!out || out.status !== 'signed') {
          // 'rejected' lands here too, and is NOT an error — the person was
          // asked and said no. M.signInProblemHtml renders the difference.
          return { status: (out && out.status) || 'unavailable' };
        }
        return api('/api/farcaster/signin', {
          method: 'POST',
          body: { message: out.message, signature: out.signature },
        }).then(function (r) {
          if (r.ok && r.body && r.body.token) {
            token = r.body.token;
            me = { fid: r.body.fid, handle: r.body.handle };
            return { status: 'signed-in' };
          }
          // 503 is ours (verifier down, or unconfigured); 401 is a refusal
          // with a reason code. They are different sentences to the viewer.
          if (r.status === 503) return { status: 'unavailable' };
          return { status: 'refused', reason: (r.body && r.body.reason) || null };
        });
      })
      .then(function (outcome) {
        busy = false;
        signInNote = (outcome.status === 'signed-in') ? ''
          : M.signInProblemHtml(outcome.status, outcome.reason);
        return outcome.status === 'signed-in' ? refresh() : render();
      })
      .catch(function () {
        busy = false;
        signInNote = M.signInProblemHtml('unavailable');
        render();
      });
  }

  var signInNote = '';

  // ── the signed-in view ──────────────────────────────────────────────────

  var account = null;     // { equity, balance, return_pct, limits, positions }
  var season = null;
  var loadError = false;

  function refresh() {
    return Promise.all([
      api('/api/arena/account'),
      api('/api/arena/season'),
    ]).then(function (res) {
      var acct = res[0];
      var seas = res[1];

      // A 401 means the session is gone — expired, or revoked. Signing the
      // page out is the honest response; leaving a stale account on screen
      // would show numbers nobody can act on.
      if (acct.status === 401) {
        token = null;
        me = null;
        signInNote = M.signInProblemHtml('unavailable');
        return render();
      }
      if (!acct.ok || !acct.body) throw new Error('account ' + acct.status);

      account = acct.body;
      // The season is CONTEXT, not the subject of this page. A failed read of
      // it must not blank an account that loaded perfectly well — omit, not
      // guard, exactly as the tape is treated on the public board.
      season = (seas.ok && seas.body && 'season' in seas.body) ? seas.body.season : null;
      loadError = false;
      render();
    }).catch(function () {
      loadError = true;
      render();
    });
  }

  function render() {
    if (busy && !token) {
      root.innerHTML = state('load', 'Waiting for Farcaster…',
        'Approve the sign-in in your Farcaster app.');
      return;
    }

    if (!token) {
      root.innerHTML = V.seasonHtml(season)
        + signInNote
        + M.signedOutHtml(season)
        + '<p class="e-src">Paper trading · virtual stakes</p>';
      return;
    }

    if (loadError) {
      root.innerHTML = V.seasonHtml(season)
        + state('error', 'Your account could not be loaded.',
          'This is a fault on our side, not a statement that it is empty.')
        + '<p class="e-src">Paper trading · virtual stakes</p>';
      return;
    }

    if (!account) {
      root.innerHTML = state('load', 'Loading your account…');
      return;
    }

    root.innerHTML = V.seasonHtml(season)
      + M.accountHtml(account)
      + lastNote
      + M.positionsHtml(account.positions)
      + M.openFormHtml(account.limits)
      + '<p class="e-src">'
      + (me && me.handle
        ? 'Trading as ' + esc(me.handle)
        // A null handle is a real answer: this account is invisible on the
        // board until it picks one, and saying so is more useful than a blank.
        : 'No leaderboard handle yet — set one on the web to appear in standings')
      + '</p>';
  }

  // ── actions ─────────────────────────────────────────────────────────────

  function openPosition(direction) {
    if (busy || !token) return;
    var symEl = root.querySelector('.m-sym');
    var marginEl = root.querySelector('.m-margin');
    var levEl = root.querySelector('.m-lev');
    var body = {
      symbol: (symEl && symEl.value || '').trim().toUpperCase(),
      direction: direction,
      margin: Number(marginEl && marginEl.value),
      leverage: Number(levEl && levEl.value),
    };
    busy = true;
    lastNote = '';
    api('/api/arena/open', { method: 'POST', body: body }).then(function (r) {
      busy = false;
      if (r.ok) {
        lastNote = M.actionNoteHtml('ok', 'Position opened.');
        return refresh();
      }
      // The server's message verbatim — it is written for a person and names
      // what to do differently. A generic "could not open" would throw that
      // away and leave the viewer guessing which field was wrong.
      lastNote = M.actionNoteHtml('warn',
        (r.body && r.body.error) || 'The position could not be opened.');
      render();
    }).catch(function () {
      busy = false;
      lastNote = M.actionNoteHtml('warn',
        'The position could not be opened — we could not reach the server.');
      render();
    });
  }

  function closePosition(id) {
    if (busy || !token) return;
    busy = true;
    lastNote = '';
    api('/api/arena/close', { method: 'POST', body: { position_id: Number(id) } })
      .then(function (r) {
        busy = false;
        if (r.ok) {
          lastNote = M.actionNoteHtml('ok', 'Position closed.');
          return refresh();
        }
        lastNote = M.actionNoteHtml('warn',
          (r.body && r.body.error) || 'The position could not be closed.');
        render();
      }).catch(function () {
        busy = false;
        lastNote = M.actionNoteHtml('warn',
          'The position could not be closed — we could not reach the server.');
        render();
      });
  }

  // One delegated listener: the view is replaced wholesale on every render, so
  // per-element handlers would be rebound constantly and leak the old ones.
  root.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    if (t.closest('.m-signin')) return void signIn();
    if (t.closest('.m-long')) return void openPosition('LONG');
    if (t.closest('.m-short')) return void openPosition('SHORT');
    var close = t.closest('.m-close');
    if (close) return void closePosition(close.getAttribute('data-close-id'));
  });

  /** Lift the splash once, on every outcome — see embed-signals.js. */
  var announced = false;
  function announceReady() {
    if (announced) return;
    announced = true;
    if (FR) FR.signalReady({});
  }

  // The season is public, so the signed-out screen can name it before anyone
  // signs in. Its failure is not fatal here: the sign-in button is the point
  // of this screen and must appear whether or not the season could be read.
  api('/api/arena/season')
    .then(function (r) {
      if (r.ok && r.body && 'season' in r.body) season = r.body.season;
    })
    .catch(function () { /* the gate renders without a season name */ })
    .then(function () { render(); announceReady(); });

  setTimeout(announceReady, 3000);
}());
