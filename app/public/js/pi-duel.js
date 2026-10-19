/*
 * The Daily Duel in the Pi Browser app.
 *
 * A block for arena-session.js's signed-in screen (the `extras` list): the
 * day's card from /api/duel/today and the player's record from /api/duel/me,
 * through the session's own api(), so a signed-in Pi user plays the same
 * rounds a signed-in member plays on the website. Each round is drawn by the
 * website's own renderer (duel.js roundCard), so the two surfaces cannot
 * disagree about what a round asks or which words it takes: the buttons are
 * the round's own `options`, and the server names every refusal.
 *
 * NO LINK BACK TO THE SITE. The Pi app carries no presale and links nowhere
 * on the site (routes/piapp.js); the game is played here in full.
 *
 * HONESTY. A card that could not be read is said to be unreadable, never
 * drawn empty: "no rounds" is a claim about the game. A record that could not
 * be read is left out and said to be, never shown as a dash that could pass
 * for "no calls yet". The record's own dash (accuracy before anything has
 * settled) is duel.js's, and means what it says there.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiDuel = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** How often the card is re-read while the app is on screen: calls settle,
   *  and the day turns. */
  var REFRESH_MS = 120000;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** The sentence after a call is recorded: what was called, at what, and
   *  what the Claw had said where there was something to say. */
  function recordedText(res) {
    var D = window.RCDuel;
    var M = window.RCMiniView;
    var r = res || {};
    var word = D.pickLabel(r.pick, r);
    var at = M && typeof M.price === 'function' ? M.price(r.entry_price) : String(r.entry_price);
    var text;
    if (r.kind === 'mover') {
      var atB = M && typeof M.price === 'function' ? M.price(r.entry_price_b) : String(r.entry_price_b);
      text = 'Call recorded: ' + word + '. ' + D.base(r.symbol) + ' at ' + at + ', '
        + D.base(r.symbol_b) + ' at ' + atB + ', snapped for you just now.';
    } else {
      text = 'Call recorded: ' + word + ' on ' + D.base(r.symbol) + ' at ' + at + ', snapped for you just now.';
    }
    if ('agent_direction' in r && r.kind !== 'backfade') {
      text += r.agent_direction == null ? ' The Claw passed on this one.'
        : ' The Claw called ' + String(r.agent_direction).toUpperCase() + '.';
    }
    return text;
  }

  function stat(k, label) {
    return '<div class="p-duel-stat"><span class="k">' + esc(k) + '</span>'
      + '<span class="v tone-' + esc(label.tone) + '">' + esc(label.text) + '</span>'
      + (label.sub ? '<span class="d">' + esc(label.sub) + '</span>' : '') + '</div>';
  }

  function create() {
    var card = null;      // null: not read yet
    var me = null;        // the record, or null when it could not be read
    var failed = false;   // the card read failed
    var lastRead = null;  // what the last read said, to skip a render that would change nothing
    var note = null;      // [kind, text]
    var busy = false;
    var timer = null;
    var ctx = null;

    function load() {
      if (!ctx) return Promise.resolve();
      return Promise.all([
        ctx.api('/api/duel/today').then(function (r) {
          // A payload without rounds is unreadable, not empty.
          if (!r.ok || !r.body || !Array.isArray(r.body.rounds)) throw new Error('duel ' + r.status);
          return r.body;
        }),
        // The record is omitted, not faked, when it cannot be read.
        ctx.api('/api/duel/me').then(function (r) {
          return r.ok && r.body && r.body.accuracy ? r.body : null;
        }, function () { return null; }),
      ]).then(function (res) {
        card = res[0];
        me = res[1];
        failed = false;
      }, function () {
        failed = true;
      }).then(function () {
        var read = failed ? 'failed' : JSON.stringify([card, me]);
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
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          if (timer) { clearInterval(timer); timer = null; }
        } else if (ctx) {
          load();
          startTimer();
        }
      });
    }

    function recordHtml() {
      var D = window.RCDuel;
      if (!me) {
        return card && !failed
          ? '<p class="p-duel-norec">Your record could not be read just now. The card above is current.</p>'
          : '';
      }
      var streak = me.streak && me.streak.current != null ? String(me.streak.current) : '—';
      return '<div class="p-duel-rec">'
        + stat('Accuracy', D.accuracyLabel(me.accuracy))
        + stat('Marks', D.marksLabel(me.marks))
        + stat('Streak', { text: '🔥 ' + streak, tone: 'muted', sub: '' })
        + '</div>';
    }

    function html() {
      var M = window.RCMiniView;
      var D = window.RCDuel;
      var noteHtml = note ? M.actionNoteHtml(note[0], note[1]) : '';
      var body;
      if (failed) {
        body = M.actionNoteHtml('warn',
          'Today’s card could not be loaded. This is on our side; your record is unchanged.');
      } else if (card === null) {
        body = '<div class="e-load">Loading today’s card…</div>';
      } else if (!card.rounds.length) {
        body = M.actionNoteHtml('note', 'No rounds are on today’s card.');
      } else {
        body = '<div class="d-rounds">' + card.rounds.map(function (r) {
          return D.roundCard(Object.assign({ horizon_hours: card.horizon_hours }, r), null);
        }).join('') + '</div>';
      }
      var day = card && !failed ? '<span class="p-duel-day">' + esc(card.day) + ' UTC</span>' : '';
      return '<section class="m-open p-duel"><div class="p-sig-head"><h2>Daily Duel</h2>' + day + '</div>'
        + noteHtml + body + recordHtml()
        + '<p class="m-open-note">Call the market before the Claw does · no stake · counts and percent only</p>'
        + '</section>';
    }

    /** The session's Refresh button: the card and the record read again. */
    function reload() {
      if (!ctx) return Promise.resolve();
      lastRead = null;
      return load();
    }

    function place(roundId, pick, c) {
      if (busy) return;
      busy = true;
      note = ['note', 'Recording your call…'];
      c.render();
      c.api('/api/duel/pick', { method: 'POST', body: { round_id: roundId, pick: pick } }).then(function (r) {
        busy = false;
        if (r.ok && r.body && r.body.ok) {
          note = ['ok', recordedText(r.body)];
        } else {
          // The server names why: a closed round, a price it could not read,
          // a call already made, a word this round does not take.
          note = ['warn', (r.body && r.body.error) || 'Your call could not be recorded.'];
        }
        lastRead = null;
        return load();
      }, function () {
        busy = false;
        note = ['warn', 'Your call could not be recorded. We could not reach the server.'];
        c.render();
      });
    }

    function click(target, c) {
      var b = target.closest('.p-duel .d-btn');
      if (!b) return false;
      var wrap = b.closest('.d-actions');
      if (!wrap) return true;
      place(Number(wrap.getAttribute('data-round')), b.getAttribute('data-pick'), c);
      return true;
    }

    return { place: 'main', html: html, click: click, signedIn: signedIn, reload: reload };
  }

  return { create: create, recordedText: recordedText, REFRESH_MS: REFRESH_MS };
}));
