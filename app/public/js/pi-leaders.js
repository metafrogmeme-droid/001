/*
 * The Leaders tab of the Pi Browser app: the season's standings, and your
 * name on them, chosen here.
 *
 * WHY IT IS HERE. The arena's footer told a Pi user with no leaderboard name
 * to "set one on the web". The Pi app links nowhere on the site, so that was
 * a door with nothing behind it, and the listing review reads a request to
 * leave the app as a redirect. The name is set in the app, through the same
 * route the website uses (POST /api/leaderboard/opt-in), with the session's
 * own bearer token.
 *
 * THE PI USERNAME IS A SUGGESTION, NEVER A DEFAULT. Sign-in hands back the
 * username Pi verified for this person (`suggested_handle`, from our server,
 * never from the page's own copy of the SDK's answer). It fills the box; it is
 * saved only when the person taps Save. A username identifies someone, and
 * putting it on a public board is their decision.
 *
 * THE STANDINGS ARE THE SESSION'S SEASON READ (ctx.season()), not a second
 * read of their own, so the header and this tab cannot disagree. Three
 * states: read (rows, or one of the board's own reasons it is empty), no
 * season running, and could not be read, each its own sentence. Percent and
 * counts only: the board is public and its rows carry no amount (§4).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiLeaders = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** The Pi app's translation (pi-i18n.js), the English where it is not loaded. */
  function T(en, vars) {
    var I = typeof window !== 'undefined' && window.RCPiI18n;
    if (I) return I.t(en, vars);
    return !vars ? en : en.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function create() {
    var ctx = null;
    var editing = false;   // the name form is open for a person who has a name
    var busy = false;
    var note = null;       // [kind, text]
    var typed = null;      // what is in the box, kept across a render
    // Season prizes (routes/pi_prizes.js): null not read, false off or not
    // there, 'unreadable', or the table. Off draws nothing at all.
    var prizes = null;

    function me() { return ctx && ctx.me ? (ctx.me() || {}) : {}; }

    function readPrizes() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/pi/prizes').then(function (r) {
        var b = r && r.body;
        if (r && r.ok && b && b.enabled === false) prizes = false;
        else if (r && r.ok && b && b.enabled === true && Array.isArray(b.places)) prizes = b;
        // A 404 is a server without the route: no prizes, not a fault.
        else if (r && r.status === 404) prizes = false;
        else prizes = 'unreadable';
      }, function () { prizes = 'unreadable'; }).then(function () { if (ctx) ctx.render(); });
    }

    function signedIn(c) { ctx = c; readPrizes(); }

    /** The prize table, when the operator has turned prizes on. */
    function prizesHtml() {
      if (prizes === null || prizes === false) return '';
      if (prizes === 'unreadable') {
        return '<p class="m-open-note p-prize-note">' + esc(T('The season prizes could not be read just now.')) + '</p>';
      }
      return '<section class="m-open p-prizes"><h2>' + esc(T('Season prizes')) + '</h2>'
        + '<ol class="p-prize-list">' + prizes.places.map(function (amount, i) {
          return '<li><span class="p-prize-place">#' + (i + 1) + '</span><b>' + esc(amount) + ' π</b></li>';
        }).join('') + '</ol>'
        + '<p class="m-open-note">' + esc(T('Free to enter. When the season ends, the top names on this board with at least {n} closed trades are paid in Pi. Names tied on return share their places’ prizes.', { n: prizes.min_trades })) + '</p>'
        + '</section>';
    }

    function nameHtml() {
      var M = window.RCMiniView;
      var who = me();
      var noteHtml = note ? M.actionNoteHtml(note[0], note[1]) : '';
      if (who.handle && !editing) {
        return '<section class="m-open p-name">'
          + '<h2>' + esc(T('Your name on the board')) + '</h2>' + noteHtml
          // The name is drawn bold inside the translated sentence: the
          // sentence is escaped whole, then its {name} is the escaped name in <b>.
          + '<p class="p-name-is">' + esc(T('You appear as {name}.')).replace('{name}', '<b>' + esc(who.handle) + '</b>') + '</p>'
          + '<div class="m-row m-row--go">'
          + '<button type="button" class="p-name-edit">' + esc(T('Change')) + '</button>'
          + '<button type="button" class="p-name-hide">' + esc(T('Hide me')) + '</button>'
          + '</div></section>';
      }
      var value = typed != null ? typed : (who.handle || who.suggested_handle || '');
      return '<section class="m-open p-name">'
        + '<h2>' + esc(who.handle ? T('Change your name') : T('Choose a name for the board')) + '</h2>'
        + noteHtml
        + '<label class="m-field"><span class="m-label">' + esc(T('Leaderboard name')) + '</span>'
        + '<input class="p-name-in" type="text" maxlength="20" autocapitalize="off" autocomplete="off" '
        + 'spellcheck="false" placeholder="' + esc(T('e.g. pioneer_42')) + '" value="' + esc(value) + '"></label>'
        + (!who.handle && who.suggested_handle && value === who.suggested_handle
          ? '<p class="m-open-note">' + esc(T('This is your Pi username. It is shown only if you save it; any other name works too.')) + '</p>'
          : '')
        + '<div class="m-row m-row--go">'
        + '<button type="button" class="p-name-save"' + (busy ? ' disabled' : '') + '>' + esc(T('Save')) + '</button>'
        + (who.handle ? '<button type="button" class="p-name-cancel">' + esc(T('Cancel')) + '</button>' : '')
        + '</div>'
        + '<p class="m-open-note">' + esc(T('Shown publicly beside your percent return. Your balance never is.')) + '</p>'
        + '</section>';
    }

    function boardHtml() {
      var V = window.RCArenaView;
      var M = window.RCMiniView;
      var body = ctx && ctx.season ? ctx.season() : null;
      var inner;
      if (!body) {
        inner = M.actionNoteHtml('warn', T('The standings could not be read just now. Refresh to try again.'));
      } else if (!body.season) {
        inner = '<p class="a-empty">' + esc(T('No season is running.')) + '</p>';
      } else if (String(body.season.status || '') === 'upcoming') {
        inner = '<p class="a-empty">' + esc(T('The season has not started yet.')) + '</p>';
      } else {
        inner = V.standingsHtml(body.rows, body);
      }
      return '<section class="m-open p-board">'
        + '<h2>' + esc(T('Season standings')) + '</h2>'
        + '<p class="m-open-note">' + esc(T('Ranked on percent return from trades closed this season.')) + '</p>'
        + inner + '</section>';
    }

    function html() {
      return nameHtml() + prizesHtml() + boardHtml();
    }

    function save(c) {
      if (busy) return;
      var el = document.querySelector('.p-name-in');
      var handle = String(el && el.value || '').trim();
      typed = handle;
      // The rule is the server's (routes/leaderboard.js) and so is the
      // sentence that refuses a name; a copy of it here would drift.
      if (!handle) {
        note = ['warn', T('Type a name first.')];
        return c.render();
      }
      busy = true;
      note = ['note', T('Saving…')];
      c.render();
      c.api('/api/leaderboard/opt-in', { method: 'POST', body: { handle: handle } }).then(function (r) {
        busy = false;
        if (r.ok && r.body && r.body.ok) {
          note = null;
          typed = null;
          editing = false;
          c.setHandle(r.body.handle || handle);
          c.toast('ok', T('Saved. You appear as {name}.', { name: r.body.handle || handle }));
          // The board is read again so the name shows on it.
          return c.refresh();
        }
        // The server names why (taken, or not a valid name).
        note = ['warn', (r.body && r.body.error) || T('The name could not be saved.')];
        c.render();
      }, function () {
        busy = false;
        note = ['warn', T('The name could not be saved. We could not reach the server.')];
        c.render();
      });
    }

    function hide(c) {
      if (busy) return;
      busy = true;
      c.api('/api/leaderboard/opt-out', { method: 'POST' }).then(function (r) {
        busy = false;
        if (r.ok) {
          note = null;
          c.setHandle(null);
          c.toast('ok', T('You are off the board. Your account and positions are unchanged.'));
          return c.refresh();
        }
        note = ['warn', (r.body && r.body.error) || T('That could not be done just now.')];
        c.render();
      }, function () {
        busy = false;
        note = ['warn', T('That could not be done just now. We could not reach the server.')];
        c.render();
      });
    }

    function click(target, c) {
      if (target.closest('.p-name-save')) { save(c); return true; }
      if (target.closest('.p-name-edit')) { editing = true; note = null; typed = null; c.render(); return true; }
      if (target.closest('.p-name-cancel')) { editing = false; note = null; typed = null; c.render(); return true; }
      if (target.closest('.p-name-hide')) { hide(c); return true; }
      return false;
    }

    /** What is typed survives a render that came from somewhere else. */
    function mounted(rootEl) {
      var el = rootEl.querySelector('.p-name-in');
      if (!el) return;
      el.addEventListener('input', function () { typed = el.value; });
    }

    return { place: 'main', tab: 'leaders', label: 'Leaders', icon: '♛', html: html,
      click: click, signedIn: signedIn, mounted: mounted, reload: readPrizes };
  }

  return { create: create };
}));
