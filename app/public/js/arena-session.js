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
 *   extras          optional, a host's own blocks on the signed-in screen
 *                   (the Pi app's signals and tips), each { place, html(),
 *                   mounted(root), click(target, ctx) -> true when handled,
 *                   signedIn(ctx), reload() -> Promise, for the Refresh
 *                   button }; every member is optional. `place` is
 *                   'main' (after the positions) or 'end' (after the open
 *                   form). ctx is { api, render, refresh, toast }: the same
 *                   authenticated api() and nothing else, so a block can never
 *                   hold the token itself. mounted() runs after every render,
 *                   because a render replaces the screen and with it anything
 *                   a block drew into it (a chart).
 *
 *                   A block may also name a `tab` ('duel', 'signals', …) with
 *                   a `label`, an `icon` and an optional `badge()` -> number.
 *                   With two or more tabs on the screen (the account and
 *                   positions are always the first, 'arena'), the screen is
 *                   one tab at a time behind a bottom bar instead of one long
 *                   scroll; a block without a tab sits on the arena tab at
 *                   its `place`. The active tab is a closure variable, like
 *                   the session: no browser storage on a framable page.
 *
 *                   ctx.toast(kind, text) shows a short notice above the bar
 *                   for a few seconds (a recorded call); a failure belongs in
 *                   the block itself, where it stays until it is read.
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
    var extras = opts.extras || [];
    var tab = 'arena';      // the active tab, for the life of this page
    var toast = null;       // { kind, text, until }
    var toastTimer = null;

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

    // Long enough to read a sentence, short enough not to sit on the next
    // card; a tap dismisses it sooner.
    var TOAST_MS = 3500;
    function showToast(kind, text) {
      if (!text) return;
      toast = { kind: kind || 'ok', text: text, until: Date.now() + TOAST_MS };
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { toast = null; toastTimer = null; render(); }, TOAST_MS);
      render();
    }
    function toastHtml() {
      if (!toast || toast.until <= Date.now()) return '';
      return '<button type="button" class="p-toast p-toast--' + esc(toast.kind) + '" role="status" aria-label="Dismiss">' + esc(toast.text) + '</button>';
    }

    var extraCtx = {
      api: api,
      render: function () { render(); },
      refresh: function () { return refresh(); },
      toast: showToast,
    };

    function tabOf(x) { return x.tab || 'arena'; }
    function extrasHtml(place, tabName) {
      return extras.filter(function (x) {
        return (x.place || 'end') === place && tabOf(x) === (tabName || 'arena') && x.html;
      }).map(function (x) { return x.html(); }).join('');
    }
    /** The tabs on screen, the arena first, then each block's in the order given. */
    function tabList() {
      var names = ['arena'];
      extras.forEach(function (x) { if (x.tab && names.indexOf(x.tab) < 0) names.push(x.tab); });
      return names;
    }
    function tabMeta(name) {
      if (name === 'arena') return { label: 'Arena', icon: '\u25CE' };
      var x = extras.filter(function (e) { return e.tab === name; })[0] || {};
      return { label: x.label || name, icon: x.icon || '', badge: x.badge };
    }
    function tabsHtml(names) {
      return '<nav class="p-tabs" aria-label="Sections">' + names.map(function (name) {
        var m = tabMeta(name);
        var n = typeof m.badge === 'function' ? Number(m.badge()) : 0;
        return '<button type="button" class="p-tab-b" data-tab="' + esc(name) + '"'
          + ' aria-pressed="' + (name === tab ? 'true' : 'false') + '">'
          + (m.icon ? '<span class="p-tab-i">' + m.icon + '</span>' : '')
          + '<span class="p-tab-l">' + esc(m.label) + '</span>'
          + (n > 0 ? '<span class="p-tab-n">' + esc(n) + '</span>' : '')
          + '</button>';
      }).join('') + '</nav>';
    }
    function panel(name, html, hasTabs) {
      return '<section class="p-panel" data-tab="' + esc(name) + '"' + (hasTabs && name !== tab ? ' hidden' : '') + '>'
        + html + '</section>';
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
            extras.forEach(function (x) { if (x.signedIn) x.signedIn(extraCtx); });
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
    var readAt = null;      // when the account on screen was read
    var refreshing = false; // the Refresh button's read, in flight

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
        readAt = new Date();
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

    function pad2(n) { return (n < 10 ? '0' : '') + n; }

    /**
     * The Refresh button, and how old the screen is. Nothing here re-reads on
     * a timer, so a position's mark and P&L were the ones read at sign-in or
     * after the last action, and the screen did not say how old they were.
     * The time is when the account on screen was read, in the reader's clock.
     */
    function refreshBarHtml() {
      return '<div class="m-refresh-row">'
        + '<span class="m-updated">' + (readAt ? 'Updated ' + pad2(readAt.getHours()) + ':'
          + pad2(readAt.getMinutes()) + ':' + pad2(readAt.getSeconds()) : '') + '</span>'
        + '<button type="button" class="m-refresh"' + (refreshing ? ' disabled' : '') + '>'
        + (refreshing ? 'Refreshing…' : '↻ Refresh') + '</button></div>';
    }

    /**
     * Read everything on the screen again: the account and season, and every
     * block that can read itself (the Pi app's signals and their charts).
     * Each read says its own failure, so one that fails does not stop the
     * others, and the button comes back either way.
     */
    function refreshAll() {
      if (refreshing || busy || !token) return Promise.resolve();
      refreshing = true;
      render();
      var reads = [refresh()].concat(extras.filter(function (x) { return x.reload; })
        .map(function (x) { return Promise.resolve().then(function () { return x.reload(); }); }));
      return Promise.all(reads.map(function (p) { return p.catch(function () { /* said by its own block */ }); }))
        .then(function () { refreshing = false; render(); });
    }

    /**
     * What the member has typed into the open form, kept across a render.
     * The view is replaced wholesale on every render, and renders come from
     * places the member did not touch: the Pi app's signals panel re-reads
     * its calls once a minute, and a refused open re-renders to show why.
     * Each wiped the ticket from under the member's thumbs and closed the
     * keyboard, so a slow typist never got an open through. Cleared only
     * when a ticket was filled (openPosition), which is when it was used.
     */
    var FORM_FIELDS = ['.m-sym', '.m-margin', '.m-lev'];
    function keepForm() {
      var kept = { values: {}, focus: null };
      FORM_FIELDS.forEach(function (sel) {
        var el = root.querySelector(sel);
        if (!el) return;
        kept.values[sel] = el.value;
        if (typeof document !== 'undefined' && document.activeElement === el) kept.focus = sel;
      });
      return kept;
    }
    function restoreForm(kept) {
      FORM_FIELDS.forEach(function (sel) {
        var el = root.querySelector(sel);
        if (el && kept.values[sel]) el.value = kept.values[sel];
      });
      var f = kept.focus && root.querySelector(kept.focus);
      if (f) f.focus();
    }
    function clearForm() {
      FORM_FIELDS.forEach(function (sel) {
        var el = root.querySelector(sel);
        if (el) el.value = '';
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
          + '<p class="e-src">Paper trading · virtual balance</p>';
        return;
      }

      if (loadError) {
        root.innerHTML = V.seasonHtml(season)
          + state('error', 'Your account could not be loaded.',
            'This is a fault on our side, not a statement that it is empty.')
          + refreshBarHtml()
          + '<p class="e-src">Paper trading · virtual balance</p>';
        return;
      }

      if (!account) {
        root.innerHTML = state('load', 'Loading your account…');
        return;
      }

      var kept = keepForm();
      var names = tabList();
      var hasTabs = names.length > 1;
      if (hasTabs && names.indexOf(tab) < 0) tab = 'arena';
      var footer = '<p class="e-src">'
        + (me && me.handle
          ? 'Trading as ' + esc(me.handle)
          // A null handle is a real answer: this account is invisible on the
          // board until it picks one, and saying so is more useful than a blank.
          : 'No leaderboard handle yet — set one on the web to appear in standings')
        + '</p>';
      root.innerHTML = '<div class="p-top">' + V.seasonHtml(season) + M.accountHtml(account) + refreshBarHtml() + '</div>'
        + panel('arena', lastNote
          + M.positionsHtml(account.positions)
          + extrasHtml('main', 'arena')
          + M.openFormHtml(account.limits)
          + extrasHtml('end', 'arena')
          + footer, hasTabs)
        + names.slice(1).map(function (name) {
          return panel(name, extrasHtml('main', name) + extrasHtml('end', name), hasTabs);
        }).join('')
        + toastHtml()
        + (hasTabs ? tabsHtml(names) : '');
      if (root.classList) root.classList.toggle('p-has-tabs', hasTabs);
      restoreForm(kept);
      extras.forEach(function (x) { if (x.mounted) x.mounted(root); });
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
          clearForm();
          return refresh();
        }
        // The server's message verbatim — it is written for a person and names
        // what to do differently. A generic "could not open" would throw that
        // away and leave the viewer guessing which field was wrong.
        lastNote = M.actionNoteHtml('warn',
          refusal('opened', r));
        render();
      }).catch(function () {
        busy = false;
        lastNote = M.actionNoteHtml('warn',
          'The position could not be opened — we could not reach the server.');
        render();
      });
    }

    /**
     * A refusal in words. The server's own sentence when it sent one; when the
     * answer carried none (a gateway page, an empty body), the status that
     * came back. "The position could not be closed" alone was all the Pi app
     * could say on 2026-10-17, when every close was refused for want of a
     * live price, and it gave nobody anything to go on.
     */
    function refusal(verb, r) {
      if (r.body && r.body.error) return r.body.error;
      return 'The position could not be ' + verb + ' (the server answered ' + r.status + ').';
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
            refusal('closed', r));
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
      if (t.closest('.m-refresh')) return void refreshAll();
      if (t.closest('.p-toast')) {
        toast = null;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = null;
        return void render();
      }
      if (!token) return;
      var tb = t.closest('.p-tab-b');
      if (tb) {
        tab = tb.getAttribute('data-tab') || 'arena';
        render();
        if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
        return;
      }
      for (var i = 0; i < extras.length; i++) {
        if (extras[i].click && extras[i].click(t, extraCtx)) return;
      }
    });

    // Back in front after being put away: read the account again, so the marks
    // and P&L on screen are not the ones from before.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && token && !busy && !refreshing) refresh();
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
