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
 * on the site (routes/piapp.js); the game is played here in full, and so is
 * the proof: each receipt on a card is a button that checks the seal in the
 * person's own browser (pi-receipt.js), where the website links to /call.
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
    if (r.kind === 'sports') {
      // A match has no entry price to quote: the call is settled on the
      // final score, and the sentence says so instead of printing a dash.
      var m = r.match || {};
      var home = m.home_short || m.home || '—';
      var away = m.away_short || m.away || '—';
      return 'Call recorded: ' + word + ' on ' + home + ' vs ' + away + '. Settled on the final score.';
    }
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

  /**
   * opts.coach: true where the app has a coach tab to send "Explain this
   * round" to (the Pi app); the DogeOS app runs this block without one.
   */
  function create(opts) {
    var hasCoach = !!(opts && opts.coach);
    var card = null;      // null: not read yet
    var me = null;        // the record, or null when it could not be read
    var failed = false;   // the card read failed
    var lastRead = null;  // what the last read said, to skip a render that would change nothing
    var note = null;      // [kind, text]
    var busy = false;
    var timer = null;
    var ctx = null;
    var receipt = null;   // { key, label, res }: the receipt being checked, res null while it runs
    var scrollTo = false; // bring the receipt sheet into view after the next render

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
          ? '<p class="p-duel-norec">Your record could not be read just now. The card is current.</p>'
          : '';
      }
      var streak = me.streak && me.streak.current != null ? String(me.streak.current) : '—';
      var marks = D.marksLabel(me.marks);
      return '<div class="p-duel-rec">'
        + stat('Accuracy', D.accuracyLabel(me.accuracy))
        + stat('Marks', marks)
        + stat('Streak', { text: '🔥 ' + streak, tone: 'muted', sub: '' })
        + '</div>'
        // What a mark is, under the row: "Accuracy 60% of 5" beside "Marks 4"
        // cannot be reconciled without the rule.
        + (marks.how ? '<p class="p-duel-how">' + esc('Marks: ' + marks.how) + '</p>' : '');
    }

    /** How many of today's rounds are called, and how many are still open. */
    function counts() {
      var rounds = card && !failed ? card.rounds : [];
      var called = rounds.filter(function (r) { return r.my_call; }).length;
      var open = rounds.filter(function (r) { return r.callable && !r.my_call; }).length;
      return { total: rounds.length, called: called, open: open };
    }

    /** The day's progress: "3 of 6 called" over a bar. A card with no rounds shows none. */
    function progressHtml() {
      var c = counts();
      if (!c.total) return '';
      var pct = Math.round((c.called / c.total) * 100);
      return '<div class="p-duel-prog" role="progressbar" aria-valuemin="0" aria-valuemax="' + esc(c.total)
        + '" aria-valuenow="' + esc(c.called) + '">'
        + '<div class="p-duel-prog-t"><span>' + esc(c.called + ' of ' + c.total + ' called') + '</span>'
        + (c.open ? '<span class="p-duel-prog-o">' + esc(c.open + ' open') + '</span>' : '') + '</div>'
        + '<div class="p-duel-bar"><div class="p-duel-bar-f" style="width:' + pct + '%"></div></div>'
        + '</div>';
    }

    /** The number on the Duel tab: rounds still open to a call. */
    function badge() { return counts().open; }

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
        // Each round with the coach's door under it, where a coach is on the
        // screen (the Pi app); the DogeOS app has none and shows none.
        var canAsk = ctx && typeof ctx.ask === 'function' && hasCoach;
        body = '<div class="d-rounds">' + card.rounds.map(function (r) {
          return D.roundCard(Object.assign({ horizon_hours: card.horizon_hours }, r), null, undefined, { verify: !!window.RCPiReceipt })
            + (canAsk ? '<div class="p-why-row"><button type="button" class="p-why" data-ask="round" data-id="' + esc(r.id) + '">Explain this round</button></div>' : '');
        }).join('') + '</div>';
      }
      var day = card && !failed ? '<span class="p-duel-day">' + esc(card.day) + ' UTC</span>' : '';
      var sheet = receipt && window.RCPiReceipt ? window.RCPiReceipt.sheetHtml(receipt.label, receipt.res) : '';
      return '<section class="m-open p-duel"><div class="p-sig-head"><h2>Daily Duel</h2>' + day + '</div>'
        + recordHtml() + progressHtml() + noteHtml + sheet + body
        + '<p class="m-open-note">Call the market before the Claw does · free to play · counts and percent only</p>'
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
          if (typeof c.toast === 'function') { note = null; c.toast('ok', recordedText(r.body)); }
          else note = ['ok', recordedText(r.body)];
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

    /** Check one receipt in place; a second tap on another replaces it. */
    function checkReceipt(btn, c) {
      var key = btn.getAttribute('data-receipt');
      var label = btn.getAttribute('data-label') || 'Receipt';
      receipt = { key: key, label: label, res: null };
      scrollTo = true;
      c.render();
      window.RCPiReceipt.check(c.api, key).then(function (res) {
        // A newer tap replaced this one; its result is not this sheet's.
        if (!receipt || receipt.key !== key) return;
        receipt.res = res;
        c.render();
      });
    }

    function mounted(rootEl) {
      if (!scrollTo) return;
      scrollTo = false;
      var el = rootEl.querySelector('.p-rc-sheet');
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
    }

    function click(target, c) {
      var why = target.closest('.p-duel .p-why');
      if (why) {
        var id = Number(why.getAttribute('data-id'));
        var r = card && card.rounds ? card.rounds.filter(function (x) { return x.id === id; })[0] : null;
        var what = r ? (r.kind === 'mover' ? window.RCDuel.base(r.symbol) + ' vs ' + window.RCDuel.base(r.symbol_b)
          : r.kind === 'sports' && r.match ? (r.match.home_short || r.match.home) + ' vs ' + (r.match.away_short || r.match.away)
            : window.RCDuel.base(r.symbol)) : 'this';
        c.ask('Explain the ' + (r ? window.RCDuel.kindLabel(r.kind || 'direction') : '') + ' round on ' + what
          + ': what am I being asked, and how is it settled?', { kind: 'round', id: id });
        return true;
      }
      var rc = target.closest('.p-duel .d-rc-v');
      if (rc) { checkReceipt(rc, c); return true; }
      if (target.closest('.p-duel .p-rc-close')) { receipt = null; c.render(); return true; }
      var b = target.closest('.p-duel .d-btn');
      if (!b) return false;
      var wrap = b.closest('.d-actions');
      if (!wrap) return true;
      place(Number(wrap.getAttribute('data-round')), b.getAttribute('data-pick'), c);
      return true;
    }

    return { place: 'main', tab: 'duel', label: 'Duel', icon: '\u2694', badge: badge, html: html, click: click, signedIn: signedIn, reload: reload, mounted: mounted };
  }

  return { create: create, recordedText: recordedText, REFRESH_MS: REFRESH_MS };
}));
