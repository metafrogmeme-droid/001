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

    function me() { return ctx && ctx.me ? (ctx.me() || {}) : {}; }

    function signedIn(c) { ctx = c; }

    function nameHtml() {
      var M = window.RCMiniView;
      var who = me();
      var noteHtml = note ? M.actionNoteHtml(note[0], note[1]) : '';
      if (who.handle && !editing) {
        return '<section class="m-open p-name">'
          + '<h2>Your name on the board</h2>' + noteHtml
          + '<p class="p-name-is">You appear as <b>' + esc(who.handle) + '</b>.</p>'
          + '<div class="m-row m-row--go">'
          + '<button type="button" class="p-name-edit">Change</button>'
          + '<button type="button" class="p-name-hide">Hide me</button>'
          + '</div></section>';
      }
      var value = typed != null ? typed : (who.handle || who.suggested_handle || '');
      return '<section class="m-open p-name">'
        + '<h2>' + (who.handle ? 'Change your name' : 'Choose a name for the board') + '</h2>'
        + noteHtml
        + '<label class="m-field"><span class="m-label">Leaderboard name</span>'
        + '<input class="p-name-in" type="text" maxlength="20" autocapitalize="off" autocomplete="off" '
        + 'spellcheck="false" placeholder="e.g. pioneer_42" value="' + esc(value) + '"></label>'
        + (!who.handle && who.suggested_handle && value === who.suggested_handle
          ? '<p class="m-open-note">This is your Pi username. It is shown only if you save it; any other name works too.</p>'
          : '')
        + '<div class="m-row m-row--go">'
        + '<button type="button" class="p-name-save"' + (busy ? ' disabled' : '') + '>Save</button>'
        + (who.handle ? '<button type="button" class="p-name-cancel">Cancel</button>' : '')
        + '</div>'
        + '<p class="m-open-note">Shown publicly beside your percent return. Your balance never is.</p>'
        + '</section>';
    }

    function boardHtml() {
      var V = window.RCArenaView;
      var M = window.RCMiniView;
      var body = ctx && ctx.season ? ctx.season() : null;
      var inner;
      if (!body) {
        inner = M.actionNoteHtml('warn', 'The standings could not be read just now. Refresh to try again.');
      } else if (!body.season) {
        inner = '<p class="a-empty">No season is running.</p>';
      } else if (String(body.season.status || '') === 'upcoming') {
        inner = '<p class="a-empty">The season has not started yet.</p>';
      } else {
        inner = V.standingsHtml(body.rows, body);
      }
      return '<section class="m-open p-board">'
        + '<h2>Season standings</h2>'
        + '<p class="m-open-note">Ranked on percent return from trades closed this season.</p>'
        + inner + '</section>';
    }

    function html() {
      return nameHtml() + boardHtml();
    }

    function save(c) {
      if (busy) return;
      var el = document.querySelector('.p-name-in');
      var handle = String(el && el.value || '').trim();
      typed = handle;
      // The rule is the server's (routes/leaderboard.js) and so is the
      // sentence that refuses a name; a copy of it here would drift.
      if (!handle) {
        note = ['warn', 'Type a name first.'];
        return c.render();
      }
      busy = true;
      note = ['note', 'Saving…'];
      c.render();
      c.api('/api/leaderboard/opt-in', { method: 'POST', body: { handle: handle } }).then(function (r) {
        busy = false;
        if (r.ok && r.body && r.body.ok) {
          note = null;
          typed = null;
          editing = false;
          c.setHandle(r.body.handle || handle);
          c.toast('ok', 'Saved. You appear as ' + (r.body.handle || handle) + '.');
          // The board is read again so the name shows on it.
          return c.refresh();
        }
        // The server names why (taken, or not a valid name).
        note = ['warn', (r.body && r.body.error) || 'The name could not be saved.'];
        c.render();
      }, function () {
        busy = false;
        note = ['warn', 'The name could not be saved. We could not reach the server.'];
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
          c.toast('ok', 'You are off the board. Your account and positions are unchanged.');
          return c.refresh();
        }
        note = ['warn', (r.body && r.body.error) || 'That could not be done just now.'];
        c.render();
      }, function () {
        busy = false;
        note = ['warn', 'That could not be done just now. We could not reach the server.'];
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
      click: click, signedIn: signedIn, mounted: mounted };
  }

  return { create: create };
}));
