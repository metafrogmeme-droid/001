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
 *
 *                   A tab with `bar: false` is not in the bottom bar: the
 *                   header's button with its label opens it (the Pi app's
 *                   About sheet). ctx also carries season() (the last
 *                   /api/arena/season answer, or null when it could not be
 *                   read), me() (who signed in), setHandle(h) (a leaderboard
 *                   name was saved), go(tab), and ask(text, about): hand a
 *                   question to the block that answers questions (the Pi
 *                   app's coach, an extra with an `ask` member) and open
 *                   its tab; false when no block answers questions.
 *   view            optional, the host's options for RCMiniView ({ unit }),
 *                   so the Pi app can say "credits" where Farcaster says vUSDT
 *   noHandle        optional, the footer's sentence for an account with no
 *                   leaderboard name, and `noHandleTab`, the tab that sets one
 *   legal           optional, true to show Privacy and Terms links under the
 *                   sign-in, our own pages, which carry no presale
 *
 * WHAT AN ACTION SAYS, AND FOR HOW LONG. A filled open and a done close are
 * toasts that name the position and go away; a refusal is a note on the arena
 * tab that stays until the next action or a change of tab. The note used to
 * hold the success too, and it outlived everything after it: a close, then a
 * trade opened from the Signals tab, and the arena tab still read "Position
 * closed." over the position just opened (the Pi submission recording,
 * 2026-10-20, at 0:45 and 1:02).
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
    var view = opts.view || {};

    /** The bearer token. In memory, for the life of this page. */
    var token = null;
    var me = null;          // { handle, ... } as the host's sign-in returned it
    var lastNote = '';      // the last refusal, cleared by the next action or a change of tab
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
      // The whole season answer (rows and their counts with it), or null when
      // it could not be read: a Leaders tab must not draw "nobody yet" over a
      // read that failed.
      season: function () { return seasonBody; },
      me: function () { return me; },
      setHandle: function (h) {
        if (me) me.handle = h || null;
        render();
      },
      go: function (name) { goTab(name); },
      ask: function (text, about) {
        var coach = extras.filter(function (x) { return typeof x.ask === 'function'; })[0];
        if (!coach) return false;
        if (coach.tab) goTab(coach.tab);
        coach.ask(text, about || null, extraCtx);
        return true;
      },
    };

    /** Open a tab. A refusal on the arena tab belongs to what was done there. */
    function goTab(name) {
      var next = name || 'arena';
      if (next !== tab) lastNote = '';
      tab = next;
      render();
      if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    }

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
      if (name === 'arena') return { label: 'Arena', icon: '\u25CE', bar: true };
      var x = extras.filter(function (e) { return e.tab === name; })[0] || {};
      return { label: x.label || name, icon: x.icon || '', badge: x.badge, bar: x.bar !== false };
    }
    /** The header's buttons for the tabs that are not in the bar (About). */
    function offBarHtml(names) {
      return names.filter(function (n) { return !tabMeta(n).bar; }).map(function (name) {
        var m = tabMeta(name);
        return '<button type="button" class="p-off-b" data-tab="' + esc(name) + '"'
          + ' aria-pressed="' + (name === tab ? 'true' : 'false') + '">'
          + (m.icon ? '<span aria-hidden="true">' + m.icon + '</span> ' : '') + esc(m.label) + '</button>';
      }).join('');
    }
    function tabsHtml(names) {
      return '<nav class="p-tabs" aria-label="Sections">' + names.filter(function (n) {
        return tabMeta(n).bar;
      }).map(function (name) {
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
          signInNote = M.signInProblemHtml(outcome.status, outcome.reason, host, view);
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
    var seasonBody = null;  // the whole answer (rows, counts), or null when unread
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
        var readable = !!(seas.ok && seas.body && 'season' in seas.body);
        season = readable ? seas.body.season : null;
        seasonBody = readable ? seas.body : null;
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
    function refreshBarHtml(extra) {
      return '<div class="m-refresh-row">'
        + '<span class="m-updated">' + (readAt ? 'Updated ' + pad2(readAt.getHours()) + ':'
          + pad2(readAt.getMinutes()) + ':' + pad2(readAt.getSeconds()) : '') + '</span>'
        + (extra || '')
        + '<button type="button" class="m-refresh"' + (refreshing ? ' disabled' : '') + '>'
        + (refreshing ? 'Refreshing…' : '↻ Refresh') + '</button></div>';
    }

    /**
     * Privacy and Terms, our own pages (built from site/, served without the
     * presale bar or any script: app/test/the_pi_app_is_the_arena_and_nothing_else.test.js).
     * A new tab, because this page's session is held in memory and following
     * a link in place would sign the person out.
     */
    function legalHtml() {
      return opts.legal ? M.legalHtml() : '';
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
        // A host with a first screen of its own (view.intro) puts it first,
        // and the season under it; the season heads the plain gate.
        root.innerHTML = (view.intro ? '' : V.seasonHtml(season))
          + signInNote
          + M.signedOutHtml(season, host, view)
          + (view.intro ? V.seasonHtml(season) : '')
          + '<p class="e-src">' + esc(M.sourceLine(view)) + '</p>'
          + legalHtml();
        return;
      }

      if (loadError) {
        root.innerHTML = V.seasonHtml(season)
          + state('error', 'Your account could not be loaded.',
            'This is a fault on our side, not a statement that it is empty.')
          + refreshBarHtml('')
          + '<p class="e-src">' + esc(M.sourceLine(view)) + '</p>';
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
          // A host that can set one in the app says where; "on the web" sent
          // a Pi user to a site the Pi app does not link to.
          : opts.noHandle
            ? esc(opts.noHandle) + (opts.noHandleTab && names.indexOf(opts.noHandleTab) >= 0
              ? ' <button type="button" class="p-goto" data-tab="' + esc(opts.noHandleTab) + '">'
                + esc(tabMeta(opts.noHandleTab).label) + '</button>' : '')
            : 'No leaderboard handle yet — set one on the web to appear in standings')
        + '</p>';
      // The season and the account head the arena tab only. On the Duel or
      // Signals tab they repeated a balance that tab is not about, and the
      // season's name over every tab read as that tab's title (the Pi review,
      // 2026-10-20: "FARCASTER · SEASON LIVE" over the Duel).
      var onArena = !hasTabs || tab === 'arena';
      var off = hasTabs ? offBarHtml(names) : '';
      root.innerHTML = (onArena
        ? '<div class="p-top">' + V.seasonHtml(season) + M.accountHtml(account, view) + refreshBarHtml(off) + '</div>'
        : '<div class="p-top p-top--slim">' + refreshBarHtml(off) + '</div>')
        + panel('arena', lastNote
          + M.positionsHtml(account.positions)
          + extrasHtml('main', 'arena')
          + M.openFormHtml(account.limits, view)
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
          showToast('ok', M.openedText(r.body && r.body.filled));
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
      var b = r.body || {};
      // The one refusal that names a unit: the server's sentence says vUSDT,
      // which a host that calls the balance credits does not print. Its code
      // and the limit it quotes are the server's; only the unit is the host's.
      var min = account && account.limits ? account.limits.min_margin : null;
      if (b.code === 'min_margin' && view.unit && min != null && isFinite(Number(min))) {
        return 'The amount must be at least ' + Number(min) + ' ' + M.unitLabel(view) + '.';
      }
      if (r.body && r.body.error) return r.body.error;
      return 'The position could not be ' + verb + ' (the server answered ' + r.status + ').';
    }

    function closePosition(id) {
      if (busy || !token) return;
      busy = true;
      lastNote = '';
      // Which position, read before the close: the answer names the symbol
      // and the result, not the side.
      var held = ((account && account.positions) || []).filter(function (p) {
        return String(p.id) === String(id);
      })[0] || null;
      api('/api/arena/close', { method: 'POST', body: { position_id: Number(id) } })
        .then(function (r) {
          busy = false;
          if (r.ok) {
            showToast('ok', M.closedText(r.body && r.body.closed, held, view));
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

    /**
     * Copy the page's link (the no-wallet note's box). The clipboard is
     * refused by many in-app browsers; then the box is selected, so a long
     * press copies it, and the button says to.
     */
    function copyLink(btn) {
      var box = root.querySelector('.m-copy-url');
      if (!box) return;
      var said = function (text) { btn.textContent = text; };
      var select = function () {
        try { box.focus(); box.select(); } catch (e) { /* nothing to select into */ }
        said('Selected, copy it');
      };
      var clip = typeof navigator !== 'undefined' && navigator.clipboard;
      if (!clip || typeof clip.writeText !== 'function') return void select();
      clip.writeText(box.value).then(function () { said('Copied'); }, select);
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
      if (t.closest('.m-copy-b')) return void copyLink(t.closest('.m-copy-b'));
      if (t.closest('.p-toast')) {
        toast = null;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = null;
        return void render();
      }
      if (!token) return;
      // The bar, the header's off-bar buttons (About) and a sentence's own
      // button ("Choose one in Leaders") all open a tab the same way.
      var tb = t.closest('.p-tab-b, .p-off-b, .p-goto');
      if (tb) return void goTab(tb.getAttribute('data-tab'));
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
        if (r.ok && r.body && 'season' in r.body) {
          season = r.body.season;
          seasonBody = r.body;
        }
      })
      .catch(function () { /* the gate renders without a season name */ })
      .then(function () {
        render();
        if (opts.onFirstRender) opts.onFirstRender();
      });
  }

  window.RCArenaSession = { start: start };
}());
