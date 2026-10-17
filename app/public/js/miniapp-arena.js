/*
 * The arena Mini App: sign in with Farcaster, trade the season.
 *
 * This file is the Farcaster half only: the sign-in, and lifting the host's
 * splash. The session, the account and the trading are arena-session.js,
 * shared with the Pi Browser app, and its header carries the argument for why
 * the session lives in memory and nowhere else.
 */
(function () {
  'use strict';

  var FR = window.RCFarcasterReady;

  /**
   * Nonce from our server, signature from the host, session back from us.
   *
   * The nonce is NOT generated here. One the page invents is one an attacker
   * can invent too, which binds the signature to nothing — see lib/siwf.js.
   */
  function signIn(api) {
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
          // asked and said no. RCMiniView.signInProblemHtml renders the difference.
          return { status: (out && out.status) || 'unavailable' };
        }
        return api('/api/farcaster/signin', {
          method: 'POST',
          body: { message: out.message, signature: out.signature },
        }).then(function (r) {
          if (r.ok && r.body && r.body.token) {
            return { status: 'signed-in', token: r.body.token,
              me: { fid: r.body.fid, handle: r.body.handle } };
          }
          // 503 is ours (verifier down, or unconfigured); 401 is a refusal
          // with a reason code. They are different sentences to the viewer.
          if (r.status === 503) return { status: 'unavailable' };
          return { status: 'refused', reason: (r.body && r.body.reason) || null };
        });
      });
  }

  /** Lift the splash once, on every outcome — see embed-signals.js. */
  var announced = false;
  function announceReady() {
    if (announced) return;
    announced = true;
    if (FR) FR.signalReady({});
  }

  window.RCArenaSession.start({
    root: document.getElementById('root'),
    host: 'Farcaster',
    waiting: ['Waiting for Farcaster…', 'Approve the sign-in in your Farcaster app.'],
    signIn: signIn,
    onFirstRender: announceReady,
  });

  setTimeout(announceReady, 3000);
}());
