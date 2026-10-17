/*
 * The arena in the Pi Browser: sign in with Pi, trade the season.
 *
 * The Pi half only: the SDK's sign-in. The session, the account and the
 * trading are arena-session.js, shared with the Farcaster Mini App, and its
 * header carries the argument for why the session lives in memory only.
 *
 * WHAT THE PAGE SENDS IS THE ACCESS TOKEN, NOT THE USER. `Pi.authenticate`
 * also hands back a uid and username, and a page can say anything it likes
 * about who that is, so both are ignored. routes/pi_auth.js exchanges the
 * token with Pi's App Studio, and the uid in that answer is the identity.
 *
 * Pi.init is awaited to completion before Pi.authenticate, and is not told
 * whether this is the sandbox: the SDK detects that itself.
 *
 * NO PAYMENTS. This app takes none. `Pi.authenticate` requires a callback for
 * a payment left incomplete, so it gets one that does nothing: there is no
 * payment of ours it could ever be handed.
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

  function onIncompletePaymentFound() { /* this app creates no payments */ }

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
      .then(function () { return Pi.authenticate(['username'], onIncompletePaymentFound); })
      .then(function (auth) {
        if (!auth || !auth.accessToken) return { status: 'unknown' };
        return api('/api/pi/signin', {
          method: 'POST',
          body: { accessToken: auth.accessToken },
        }).then(function (r) {
          if (r.ok && r.body && r.body.token) {
            return { status: 'signed-in', token: r.body.token, me: { handle: r.body.handle } };
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

  window.RCArenaSession.start({
    root: document.getElementById('root'),
    host: 'Pi',
    waiting: ['Waiting for Pi…', 'Approve the sign-in in the Pi Browser.'],
    signIn: signIn,
  });
}());
