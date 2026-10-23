/*
 * The Daily Duel as a Telegram Mini App, opened from the Duel bot.
 *
 * This file is the Telegram half only: Telegram's signed launch data in, a
 * session back from our server (routes/tg_auth.js checks the signature with
 * Telegram's public key, lib/tg_webapp.js). The session, the tabs and the
 * blocks are arena-session.js, shared with the Pi and Farcaster apps, and its
 * header carries the argument for why the session lives in memory only.
 *
 * WHAT IS INSIDE, AND WHAT IS NOT. The Duel (pi-duel.js) and the daily proof
 * (pi-proof.js), read-only: no paper account (`arena: false`), no wallet, no
 * transaction, no token, nothing bought. Telegram requires TON for anything
 * that touches a chain inside a Mini App, and this app touches none; a day's
 * anchoring is done on the website. Every sentence the person reads comes
 * from miniapp-view.js and the blocks, which the Pi app's translation build
 * reads, so this file draws nothing of its own.
 */
(function () {
  'use strict';

  var W = window.Telegram && window.Telegram.WebApp;
  var M = window.RCMiniView;

  /**
   * Telegram's launch data, sent as received: the page's own reading of who
   * this is (initDataUnsafe) counts for nothing, and is never sent.
   */
  function signIn(api) {
    var data = W && typeof W.initData === 'string' ? W.initData : '';
    if (!data) return Promise.resolve({ status: 'no-host' });
    return api('/api/tgapp/signin', { method: 'POST', body: { init_data: data } }).then(function (r) {
      if (r.ok && r.body && r.body.token) {
        return { status: 'signed-in', token: r.body.token, me: { handle: r.body.handle || null } };
      }
      // 503 is ours (no bot id configured, or the database); 401 is Telegram's
      // signature not holding, with a reason the view turns into a sentence.
      if (r.status === 503) return { status: 'unavailable' };
      return { status: 'refused', reason: (r.body && r.body.reason) || null };
    });
  }

  // The language the server chose for the page (routes/tgapp.js, from the
  // reader's Accept-Language); its words were loaded just after pi-i18n.js.
  if (window.RCPiI18n) window.RCPiI18n.use(document.documentElement.getAttribute('lang') || 'en');

  // Telegram keeps its loading screen up until the app says it is ready.
  if (W) {
    try { W.ready(); W.expand(); } catch (e) { /* an older client: the page still works */ }
  }

  /**
   * Share a page of this site through Telegram's own share sheet
   * (https://t.me/share/url, which Telegram opens in the app with the chat
   * picker). The link is this page's origin plus `path`: the site serving the
   * Mini App is the site the receipt lives on. Telegram draws the link's
   * preview from the page (routes/frame.js for a receipt). Nothing is posted
   * for the player: they pick the chat and send it themselves.
   */
  function share(path, text) {
    var link = 'https://t.me/share/url?url=' + encodeURIComponent(location.origin + path)
      + '&text=' + encodeURIComponent(text || '');
    try {
      if (W && typeof W.openTelegramLink === 'function') { W.openTelegramLink(link); return; }
    } catch (e) { /* an older client: the plain link below */ }
    window.open(link, '_blank', 'noopener');
  }

  window.RCArenaSession.start({
    root: document.getElementById('root'),
    host: 'Telegram',
    waiting: function () { return M.waitingFor('Telegram'); },
    signIn: signIn,
    arena: false,
    autoSignIn: true,
    legal: true,
    extras: [window.RCPiDuel.create({ share: share }), window.RCPiProof.create()],
  });
}());
