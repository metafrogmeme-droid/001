/*
 * The signed-in arena: one session, shared by the two hosts that run it.
 *
 * The Farcaster Mini App (miniapp-arena.js) and the Pi Browser app
 * (pi-arena.js) differ only in how a person signs in. Everything after that,
 * the account, the season, opening and closing positions, is this one file,
 * so the two cannot drift into two answers to the same question.
 *
 * THE SESSION IS A CLOSURE VARIABLE AND NOTHING ELSE. Not localStorage, not a
 * cookie, not a global. Two reasons, and the second is the one that matters:
 *
 *   - `embed_frame_policy` forbids browser storage on a framable page, and
 *     both pages that load this are framable by design.
 *   - A token that persists is a token that exists in a frame the viewer did
 *     not open. Held here, every fresh load, including one inside an
 *     attacker's iframe, starts signed out, and the only way back in is the
 *     host's sign-in, checked by our server (lib/siwf.js for Farcaster,
 *     lib/pi_auth.js for Pi). See routes/miniapp.js for the whole argument.
 *
 * The consequence is deliberate: closing the app signs you out. That is the
 * correct trade for a page anyone may frame, and it costs one tap to undo.
 *
 * A host passes:
 *   root            the element to render into
 *   host            'Farcaster' or 'Pi', for the sentences that name it
 *   waiting         [text, detail] shown while the host is asked
 *   signIn(api)     a Promise of { status, reason?, token?, me? }; status
 *                   'signed-in' carries the token. Anything else is a
 *                   sentence from RCMiniView.signInProblemHtml.
 *   onFirstRender   optional, called once the first screen is up
 */
(function () {
  'use strict';

  function start(opts) {
    var root = opts.root;
    // Deliberately not `|| {}` — if a module failed to load, calling through it
    // throws into the same handler as a network fault and the page says so,
    // rather than rendering a board assembled from nothing.
    var V = window.RCArenaView;
    var M = window.RCMiniView;
    var host = opts.host;

    /** The bearer token. In memory, for the life of this page. */
    var token = null;
    var me = null;          // { handle, ... } as the host's sign-in returned it
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
    function api(path, o) {
      o = o || {};
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

    // ── sign in ───────────────────────────────────────────────────────────

    var signInNote = '';

    function signIn() {
      if (busy) return Promise.resolve();
      busy = true;
      render();
      return Promise.resolve()
        .then(function () { return opts.signIn(api); })
        .then(function (outcome) {
          busy = false;
          outcome = outcome || { status: 'unavailable' };
          if (outcome.status === 'signed-in' && outcome.token) {
            token = outcome.token;
            me = outcome.me || {};
            signInNote = '';
            return refresh();
          }
          signInNote = M.signInProblemHtml(outcome.status, outcome.reason, host);
          return render();
        })
        .catch(function () {
          busy = false;
          signInNote = M.signInProblemHtml('unavailable', null, host);
          render();
        });
    }

    // ── the signed-in view ────────────────────────────────────────────────

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
          signInNote = M.signInProblemHtml('unavailable', null, host);
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
        root.innerHTML = state('load', opts.waiting[0], opts.waiting[1]);
        return;
      }

      if (!token) {
        root.innerHTML = V.seasonHtml(season)
          + signInNote
          + M.signedOutHtml(season, host)
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

    // ── actions ───────────────────────────────────────────────────────────

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

    // The season is public, so the signed-out screen can name it before anyone
    // signs in. Its failure is not fatal here: the sign-in button is the point
    // of this screen and must appear whether or not the season could be read.
    api('/api/arena/season')
      .then(function (r) {
        if (r.ok && r.body && 'season' in r.body) season = r.body.season;
      })
      .catch(function () { /* the gate renders without a season name */ })
      .then(function () {
        render();
        if (opts.onFirstRender) opts.onFirstRender();
      });
  }

  window.RCArenaSession = { start: start };
}());
