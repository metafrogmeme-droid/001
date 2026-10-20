/*
 * The arena in the Pi Browser: sign in with Pi, trade the season, and tip.
 *
 * The Pi half only: the SDK's sign-in, the first screen, the About sheet and
 * the tip; the signals panel is pi-signals.js, the Duel pi-duel.js and the
 * standings pi-leaders.js. The session, the account and the trading are
 * arena-session.js, shared with the Farcaster Mini App,
 * and its header carries the argument for why the session lives in memory only.
 *
 * WHAT THE PAGE SENDS IS THE ACCESS TOKEN, NOT THE USER. `Pi.authenticate`
 * also hands back a uid and username, and a page can say anything it likes
 * about who that is, so both are ignored. routes/pi_auth.js exchanges the
 * token with Pi's App Studio, and the uid in that answer is the identity.
 *
 * Pi.init is awaited to completion before Pi.authenticate, and is not told
 * whether this is the sandbox: the SDK detects that itself.
 *
 * ONE PAYMENT: AN OPTIONAL TIP, AND IT UNLOCKS NOTHING. The amounts, the
 * memo and the order come from our server (routes/pi_payments.js), never
 * from this page, and the page hands Pi's payment id back for the server to
 * approve and complete against Pi with a key the page never sees. A payment
 * the SDK finds unfinished at sign-in is handed back the same way, by id
 * only: the server reads the rest from Pi.
 */
(function () {
  'use strict';

  var ready = null;   // the one Pi.init, as a promise

  /** The SDK, initialised once and fully, or null where it did not load. */
  function sdk() {
    var Pi = window.Pi;
    if (!Pi || typeof Pi.authenticate !== 'function' || typeof Pi.init !== 'function') {
      return Promise.resolve(null);
    }
    if (!ready) {
      ready = Promise.resolve(Pi.init({ version: '2.0' })).then(function () { return Pi; });
      // A failed init is not cached: the next tap asks again.
      ready.catch(function () { ready = null; });
    }
    return ready;
  }

  // ── tips ────────────────────────────────────────────────────────────────

  var unfinished = null;   // a payment id the SDK reported at sign-in
  var tips = null;         // null: not read yet; { enabled, amounts } once read
  var tipsFailed = false;  // the read failed: said so, not hidden
  var tipBusy = false;
  var tipNote = '';        // [kind, text]

  /**
   * The SDK calls this during sign-in for a payment that reached the
   * blockchain but was never completed. Only its id is kept: the server reads
   * the payment itself from Pi before doing anything with it.
   */
  function onIncompletePaymentFound(payment) {
    if (payment && typeof payment.identifier === 'string') unfinished = payment.identifier;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function note(kind, text) { tipNote = [kind, text]; }

  function tipHtml() {
    var M = window.RCMiniView;
    var noteHtml = tipNote ? M.actionNoteHtml(tipNote[0], tipNote[1]) : '';
    if (tipsFailed) {
      return '<section class="m-open">' + noteHtml
        + M.actionNoteHtml('note', 'Tips could not be loaded right now.') + '</section>';
    }
    // Not read yet, or not offered on this server: there is nothing to say.
    if (!tips || !tips.enabled || !tips.amounts || !tips.amounts.length) return noteHtml;
    return '<section class="m-open">'
      + '<h2>Support RUNECLAW</h2>'
      + noteHtml
      + '<div class="m-row m-row--go">'
      + tips.amounts.map(function (a) {
        return '<button type="button" class="p-tip" data-tip="' + esc(a) + '"'
          + (tipBusy ? ' disabled' : '') + '>' + esc(a) + ' π</button>';
      }).join('')
      + '</div>'
      + '<p class="m-open-note">An optional tip in Pi. It unlocks nothing and does not touch your arena account.</p>'
      + '</section>';
  }

  /** The server's word on a step, in the person's terms. */
  function problem(r) {
    var code = r && r.body && r.body.error;
    if (r && r.status === 503) return 'Tips are unavailable right now. This is on our side; nothing was taken.';
    if (code === 'pi_account_required') return 'Tips need a Pi sign-in.';
    if (code === 'amount_not_offered') return 'That amount is not offered. Pick one of the buttons.';
    return 'The tip could not be prepared. Nothing was taken.';
  }

  function finish(ctx, paymentId, txid) {
    return ctx.api('/api/pi/payments/complete', {
      method: 'POST', body: { paymentId: paymentId, txid: txid },
    }).then(function (r) {
      tipBusy = false;
      if (r.ok) note('ok', 'Thank you, your tip arrived.');
      else note('warn', 'Your tip was sent but not yet confirmed here. Reopen the app and it will be finished.');
      ctx.render();
    }, function () {
      tipBusy = false;
      note('warn', 'Your tip was sent but not yet confirmed here. Reopen the app and it will be finished.');
      ctx.render();
    });
  }

  function tip(amount, ctx) {
    if (tipBusy) return;
    var Pi = window.Pi;
    if (!Pi || typeof Pi.createPayment !== 'function') {
      note('note', 'Open this in the Pi Browser to tip.');
      ctx.render();
      return;
    }
    tipBusy = true;
    note('note', 'Preparing your tip…');
    ctx.render();
    ctx.api('/api/pi/payments/tips', { method: 'POST', body: { amount: amount } }).then(function (r) {
      if (!r.ok || !r.body || !r.body.orderId) {
        tipBusy = false;
        note('warn', problem(r));
        return ctx.render();
      }
      var order = r.body;
      note('note', 'Confirm the tip in the Pi Browser.');
      ctx.render();
      // Amount, memo and metadata exactly as the server issued them.
      Pi.createPayment({ amount: order.amount, memo: order.memo, metadata: order.metadata }, {
        onReadyForServerApproval: function (paymentId) {
          // Pi retries this until the server approves; the server is idempotent.
          ctx.api('/api/pi/payments/approve', { method: 'POST', body: { paymentId: paymentId } });
        },
        onReadyForServerCompletion: function (paymentId, txid) {
          finish(ctx, paymentId, txid);
        },
        onCancel: function () {
          tipBusy = false;
          note('note', 'Tip cancelled. Nothing was paid.');
          ctx.render();
        },
        onError: function () {
          tipBusy = false;
          // The SDK does not say whether the transaction was signed, so this
          // does not claim either way.
          note('warn', 'The tip did not go through. If Pi shows it as paid, reopen the app and it will be finished.');
          ctx.render();
        },
      });
      return undefined;
    }, function () {
      tipBusy = false;
      note('warn', 'The tip could not be prepared. Nothing was taken.');
      ctx.render();
    });
  }

  /** After sign-in: finish an interrupted tip, then read what is offered. */
  function signedIn(ctx) {
    var resumed = Promise.resolve();
    if (unfinished) {
      var id = unfinished;
      unfinished = null;
      resumed = ctx.api('/api/pi/payments/incomplete', { method: 'POST', body: { paymentId: id } })
        .then(function (r) {
          if (r.ok) note('ok', 'Your earlier tip was completed. Thank you.');
          else note('warn', 'An earlier tip could not be finished yet. It will be tried again next time.');
        }, function () {
          note('warn', 'An earlier tip could not be finished yet. It will be tried again next time.');
        });
    }
    resumed.then(function () {
      return ctx.api('/api/pi/payments/tips');
    }).then(function (r) {
      if (r.ok && r.body && typeof r.body.enabled === 'boolean') {
        tips = r.body;
        tipsFailed = false;
      } else {
        tipsFailed = true;
      }
      ctx.render();
    }, function () {
      tipsFailed = true;
      ctx.render();
    });
  }

  function click(target, ctx) {
    var b = target.closest('.p-tip');
    if (!b) return false;
    var amount = Number(b.getAttribute('data-tip'));
    if (amount > 0) tip(amount, ctx);
    return true;
  }

  // ── sign-in ─────────────────────────────────────────────────────────────

  function signIn(api) {
    var started;
    try { started = sdk(); } catch (e) { started = Promise.resolve(null); }
    return started.then(function (Pi) {
      // Outside the Pi Browser there is no SDK to ask. Saying where to open it
      // is the useful sentence; "sign-in failed" would describe nothing that
      // happened.
      if (!Pi) return { status: 'no-host' };
      return authenticate(api, Pi);
    }, function () { return { status: 'unknown' }; });
  }

  function authenticate(api, Pi) {
    return Promise.resolve()
      // `payments` because the tip uses Pi.createPayment; nothing else is asked.
      .then(function () { return Pi.authenticate(['username', 'payments'], onIncompletePaymentFound); })
      .then(function (auth) {
        if (!auth || !auth.accessToken) return { status: 'unknown' };
        return api('/api/pi/signin', {
          method: 'POST',
          body: { accessToken: auth.accessToken },
        }).then(function (r) {
          if (r.ok && r.body && r.body.token) {
            // The suggestion is our server's, from the username Pi verified;
            // the SDK's own copy of the user is never read (header above).
            return { status: 'signed-in', token: r.body.token,
              me: { handle: r.body.handle, suggested_handle: r.body.suggested_handle || null } };
          }
          // 503 is ours (Pi could not be asked); 401 is Pi refusing the token.
          if (r.status === 503) return { status: 'unavailable' };
          return { status: 'refused', reason: (r.body && r.body.reason) || null };
        });
      }, function () {
        // The SDK rejected: the person declined, or the host refused. The SDK
        // does not say which, so neither is claimed.
        return { status: 'unknown' };
      });
  }

  // ── about ───────────────────────────────────────────────────────────────

  /**
   * The About sheet, opened from the header: what this is, what it is not,
   * where the policies are, and the tip. The tip sat in the middle of the
   * trading screen, where an amount in π beside the trade buttons read as a
   * price of entry; it unlocks nothing, and it now lives where that is said.
   */
  function aboutHtml() {
    var M = window.RCMiniView;
    return '<section class="m-open p-about">'
      + '<h2>About RUNECLAW Arena</h2>'
      + '<p class="p-about-p">Call the market against an AI and practise trading at live prices. '
      + 'Everything here is practice: credits are not money, and they cannot be bought, sold or withdrawn.</p>'
      + '<ul class="p-about-list">'
      + '<li><b>Daily Duel.</b> A few rounds a day: call each one before the Claw reveals its own call. '
      + 'Every round is sealed when it is dealt; tap a receipt to check it in your browser.</li>'
      + '<li><b>Signals.</b> The calls the agent makes, with their charts. Follow one with practice credits.</li>'
      + '<li><b>Leaders.</b> The season ranked on percent return, under the name you choose.</li>'
      + '</ul>'
      + M.legalHtml()
      + '</section>'
      + tipHtml();
  }

  window.RCArenaSession.start({
    root: document.getElementById('root'),
    host: 'Pi',
    waiting: ['Waiting for Pi…', 'Approve the sign-in in the Pi Browser.'],
    signIn: signIn,
    // Credits, not vUSDT: see miniapp-view.js unitLabel.
    view: {
      unit: 'credits',
      intro: {
        image: '/mascot/runeclaw_owl_round.webp?v=1',
        title: 'Beat the Claw',
        lead: 'Call the market against an AI. Free to play, and every round is provable.',
        points: [
          { icon: '\u2694', text: 'Daily Duel: call today\u2019s rounds before the AI shows its own.' },
          { icon: '\u25CE', text: 'Practice trading at live prices, with credits, not money.' },
          { icon: '\u265B', text: 'A season leaderboard, ranked on percent return.' },
        ],
        note: 'Signing in with Pi creates a practice account. No exchange keys, no real funds.',
      },
    },
    noHandle: 'No leaderboard name yet. Choose one in',
    noHandleTab: 'leaders',
    legal: true,
    extras: [
      // The Daily Duel's card: quick to play, so the first tab after the arena.
      window.RCPiDuel.create(),
      // The engine's calls with their charts.
      window.RCPiSignals.create(),
      // The season's standings, and your name on them.
      window.RCPiLeaders.create(),
      // What this is, the policies and the tip, behind the header's About.
      { place: 'main', tab: 'about', bar: false, label: 'About', icon: '\u24D8',
        html: aboutHtml, click: click, signedIn: signedIn },
    ],
  });
}());
