/*
 * RCFarcasterReady — tell the Mini App host the page is up.
 *
 * A Farcaster Mini App shows a splash screen over the app until the app calls
 * `sdk.actions.ready()`. Never call it and the splash stays forever: the card
 * renders, the button is tappable, and tapping it appears to do NOTHING. That
 * is what "not opening" looks like from the outside, and it is indistinguishable
 * from a dead server — which is the honesty problem this repo keeps meeting,
 * arriving from the other direction: our own silence rendering as their failure.
 *
 * WHY NOT THE SDK. The obvious fix is `@farcaster/miniapp-sdk`. It was measured
 * rather than assumed:
 *
 *     prebuilt dist/index.min.js          652 KB
 *     tree-shaken to just this one call   640 KB
 *
 * The `sdk` object eagerly builds the Ethereum and Solana providers, so `ox`
 * comes along whatever you import. 640 KB is roughly a hundred times this
 * page's entire JS, downloaded on a phone, inside a webview, to send ONE
 * message. The embed board exists to load fast in someone else's feed.
 *
 * WHY A SHIM IS NOT A GUESS. The SDK's own `endpoint.js` shows the transport is
 * comlink: `wrap(windowEndpoint(window.parent))`, so the host runs comlink's
 * `expose()`. Comlink's APPLY frame is a documented, readable shape, and the
 * proof is not that this file looks right — `farcaster_ready.test.js` drives it
 * against REAL comlink `expose()` and asserts the host's `ready` actually runs.
 * If the frame is wrong, comlink ignores it and that test fails.
 *
 * WHAT IS NOT PROVEN, AND SAYING SO. The handshake is verified against comlink.
 * It is NOT verified against a live Warpcast host, which cannot be reached from
 * CI. If Farcaster changes transport, this breaks and the SDK would not — that
 * is the trade this file accepts, and the reason it is one small readable
 * function rather than something clever.
 *
 * No CSP change: postMessage is not a fetch, so `default-src 'none'` and
 * `connect-src 'self'` are untouched. Outside a Mini App host this is inert.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCFarcasterReady = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /**
   * The transport, chosen exactly as the SDK's endpoint.js chooses it.
   *
   * Returns null when there is no host to talk to — a plain browser tab, where
   * `window.parent === window`. Posting to ourselves would be harmless and
   * meaningless, and returning a live-looking endpoint for it would make the
   * status below claim we reached a host we never had.
   */
  function pickEndpoint(win, doc) {
    if (!win) return null;

    // React Native webview host: a JSON string over the bridge, replies
    // dispatched as a DOM event rather than a message.
    if (win.ReactNativeWebView) {
      return {
        kind: 'reactnative',
        post: function (msg) { win.ReactNativeWebView.postMessage(JSON.stringify(msg)); },
        listen: function (fn) { if (doc) doc.addEventListener('FarcasterFrameCallback', fn); },
        unlisten: function (fn) { if (doc) doc.removeEventListener('FarcasterFrameCallback', fn); },
      };
    }

    // Framed web host.
    if (win.parent && win.parent !== win) {
      return {
        kind: 'iframe',
        post: function (msg) { win.parent.postMessage(msg, '*'); },
        listen: function (fn) { win.addEventListener('message', fn); },
        unlisten: function (fn) { win.removeEventListener('message', fn); },
      };
    }

    return null;
  }

  /** Comlink correlates request and reply by this id; it only has to be unique. */
  function frameId() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  /**
   * One comlink APPLY frame calling `path` on the exposed host object.
   *
   * `{type:'RAW', value}` is what comlink's `toWireValue` produces for a plain
   * object with nothing transferable in it — which is all these arguments ever
   * are.
   *
   * Generalised from a hardcoded `ready` when the share button arrived: the SDK
   * routes EVERY action through the same `wrap()` proxy, so `composeCast` is
   * this frame with a different path and nothing else. Writing a second
   * transport beside the first would have been two things to get wrong.
   */
  function actionFrame(id, path, options) {
    return {
      id: id,
      type: 'APPLY',
      path: [path],
      argumentList: [{ type: 'RAW', value: options || {} }],
    };
  }

  /** Kept for the ready-specific tests that pin the exact published frame. */
  function readyFrame(id, options) { return actionFrame(id, 'ready', options); }

  /**
   * Tell the host we are ready.
   *
   * Resolves to a STATUS rather than throwing, and the three values are three
   * different facts, never collapsed:
   *
   *   'no-host'      there is no Mini App host — a normal tab. Nothing to do.
   *   'acknowledged' the host replied. The splash is being dismissed.
   *   'sent'         posted, no reply within the window. The usual case for a
   *                  host that dismisses without answering; NOT a claim that it
   *                  arrived, which is why it is not called 'acknowledged'.
   *
   * Never rejects. A page that throws while announcing itself ready has taken
   * the board down to fix a splash screen.
   */
  function signalReady(options, deps) {
    var d = deps || {};
    var win = d.window || (typeof window !== 'undefined' ? window : null);
    var doc = d.document || (typeof document !== 'undefined' ? document : null);
    var waitMs = typeof d.waitMs === 'number' ? d.waitMs : 1000;

    var ep = pickEndpoint(win, doc);
    if (!ep) return Promise.resolve({ status: 'no-host', transport: null });

    return new Promise(function (resolve) {
      var id = frameId();
      var done = false;

      function finish(status) {
        if (done) return;
        done = true;
        try { ep.unlisten(onMessage); } catch (e) { /* listener already gone */ }
        resolve({ status: status, transport: ep.kind });
      }

      function onMessage(ev) {
        var data = ev && ev.data;
        if (!data || data.id !== id) return;   // not our reply
        finish('acknowledged');
      }

      try {
        ep.listen(onMessage);
        ep.post(readyFrame(id, options));
      } catch (e) {
        // A host that refuses the message is not a reason to break the page.
        finish('no-host');
        return;
      }

      if (win.setTimeout) win.setTimeout(function () { finish('sent'); }, waitMs);
      else finish('sent');
    });
  }

  /**
   * Open the host's cast composer, pre-filled.
   *
   * FOUR OUTCOMES, and the difference between the last three is the whole
   * point of not writing this as a boolean:
   *
   *   'no-host'   not inside a Mini App. There is no composer to open.
   *   'posted'    the host returned a cast. It exists; `hash` identifies it.
   *   'cancelled' the host returned `{cast: null}` — the composer opened and
   *               the person chose not to send. A DELIBERATE DECISION, and
   *               reporting it as failure would put an error on a screen where
   *               nothing went wrong.
   *   'unknown'   posted the request, no reply inside the window. The composer
   *               is very likely open and being typed into; we simply do not
   *               know the outcome and must not claim one.
   *
   * The window is long because a human is writing a cast, not because the
   * network is slow. `ready` waits a second; asking someone to finish typing in
   * a second and calling their silence a result would manufacture 'unknown' on
   * almost every real share.
   *
   * Never rejects, for the same reason signalReady does not.
   */
  function composeCast(options, deps) {
    var d = deps || {};
    var win = d.window || (typeof window !== 'undefined' ? window : null);
    var doc = d.document || (typeof document !== 'undefined' ? document : null);
    var waitMs = typeof d.waitMs === 'number' ? d.waitMs : 120000;

    var ep = pickEndpoint(win, doc);
    if (!ep) return Promise.resolve({ status: 'no-host', transport: null });

    return new Promise(function (resolve) {
      var id = frameId();
      var done = false;

      function finish(status, hash) {
        if (done) return;
        done = true;
        try { ep.unlisten(onMessage); } catch (e) { /* listener already gone */ }
        resolve({ status: status, transport: ep.kind, hash: hash || null });
      }

      function onMessage(ev) {
        var data = ev && ev.data;
        if (!data || data.id !== id) return;
        // comlink's reply carries the return value at `.value` for a RAW
        // result, and composeCast resolves to `{cast: <cast>|null}`.
        var v = data.value;
        var cast = v && typeof v === 'object' ? v.cast : null;
        // `cast == null` is the host telling us the person cancelled. It is
        // NOT a read failure and must not be reported as one.
        if (cast && cast.hash) finish('posted', cast.hash);
        else finish('cancelled');
      }

      try {
        ep.listen(onMessage);
        ep.post(actionFrame(id, 'composeCast', options));
      } catch (e) {
        finish('no-host');
        return;
      }

      if (win.setTimeout) win.setTimeout(function () { finish('unknown'); }, waitMs);
    });
  }

  return {
    signalReady: signalReady,
    composeCast: composeCast,
    // Exported for the tests that prove the frames are comlink's, not ours.
    readyFrame: readyFrame,
    actionFrame: actionFrame,
    pickEndpoint: pickEndpoint,
  };
}));
