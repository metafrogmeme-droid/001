/*
 * The signed-in half of the arena Mini App: your account, your positions.
 *
 * §4 CUTS THROUGH THE MIDDLE OF THIS FILE, and the line is worth stating
 * because it is the opposite of the rule everywhere else in the embed pages.
 * Public surfaces carry percent, ratio and count only. This is a PRIVATE
 * per-user view — CLAUDE.md permits amounts here — and the amounts are virtual
 * anyway, vUSDT against a stake everybody starts equal on.
 *
 * So a balance renders here and must NOT render on anything shared. The share
 * text in embed-arena-view.js is the surface that crosses back over, and it
 * carries percent only; nothing in this file feeds it.
 *
 * Everything else is the honesty rule as usual, and the account payload
 * already hands us the hard cases correctly: `mark` and `pnl` arrive as null
 * when the price could not be read, rather than as zero. The job here is not
 * to undo that on the way to the screen.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCMiniView = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /**
   * A sentence in the reader's language where the Pi app's table is loaded
   * (pi-i18n.js); the English here everywhere else, placeholders filled.
   */
  function T(en, vars) {
    var I = typeof window !== 'undefined' && window.RCPiI18n;
    if (I) return I.t(en, vars);
    return !vars ? en : en.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /** A virtual amount. Private view only — see the §4 note above. */
  function vusdt(v) {
    var n = num(v);
    if (n === null) return '—';
    return n.toFixed(2);
  }

  /**
   * What the host calls the virtual balance. The Farcaster Mini App says
   * vUSDT, as the website does. The Pi app says credits: "USDT" beside a Pi
   * app reads as a second currency to a reviewer and means nothing to most
   * Pioneers, and nothing is transacted in either. One number, two names;
   * the amount is the same everywhere.
   */
  function unitLabel(opts) {
    return opts && opts.unit === 'credits' ? T('credits') : 'vUSDT';
  }

  /** The line under the screen that says what kind of money this is. */
  function sourceLine(opts) {
    return opts && opts.unit === 'credits'
      ? T('Practice trading · credits, not money')
      : T('Paper trading · virtual balance');
  }

  /**
   * Privacy and Terms: our own pages, built from site/ and served with no
   * presale bar and no script. A new tab, because a framed app's session is
   * held in memory and following a link in place would sign the person out.
   */
  function legalHtml() {
    return '<p class="p-legal">'
      + '<a href="/privacy" target="_blank" rel="noopener noreferrer">' + esc(T('Privacy')) + '</a>'
      + '<span aria-hidden="true"> · </span>'
      + '<a href="/terms" target="_blank" rel="noopener noreferrer">' + esc(T('Terms')) + '</a></p>';
  }

  /** "Position opened: BTCUSDT LONG at 65010, 2×." from the server's fill. */
  function openedText(filled) {
    var f = filled || {};
    if (!f.symbol) return T('Position opened.');
    var dir = String(f.direction || '').toUpperCase();
    var lev = num(f.leverage);
    var v = { what: f.symbol + (dir ? ' ' + dir : ''), price: price(f.entry), lev: lev };
    if (num(f.entry) === null) return lev === null ? T('Position opened: {what}.', v) : T('Position opened: {what}, {lev}×.', v);
    return lev === null ? T('Position opened: {what} at {price}.', v) : T('Position opened: {what} at {price}, {lev}×.', v);
  }

  /**
   * "Position closed: BTCUSDT LONG at 65500. Result +12.34 credits." from the
   * server's answer, with the side read from the position as it was held.
   * A result the server did not send is left out, never printed as zero.
   */
  function closedText(closed, held, opts) {
    var c = closed || {};
    var h = held || {};
    var sym = c.symbol || h.symbol;
    if (!sym) return T('Position closed.');
    var dir = String(h.direction || '').toUpperCase();
    var pnl = num(c.pnl);
    var v = { what: sym + (dir ? ' ' + dir : ''), price: price(c.exit_price) };
    var at = num(c.exit_price) !== null;
    var head = c.liquidated === true
      ? (at ? T('Position liquidated: {what} at {price}.', v) : T('Position liquidated: {what}.', v))
      : (at ? T('Position closed: {what} at {price}.', v) : T('Position closed: {what}.', v));
    return head + (pnl === null ? '' : ' ' + T('Result {pnl} {unit}.', { pnl: (pnl > 0 ? '+' : '') + vusdt(pnl), unit: unitLabel(opts) }));
  }

  /**
   * A market price, with the decimals its size needs. Entry and mark were
   * printed with vusdt(), an amount's two decimals, so a coin at 0.14945 read
   * "entry 0.15 · mark 0.15" while it moved, and a position looked flat that
   * was not. The rule is embed-row.js's price(), which prints the signal
   * cards' levels on the same screen; a test holds the two to one answer.
   */
  function price(v) {
    var n = num(v);
    if (n === null) return '—';
    var abs = Math.abs(n);
    // Below a cent, the chart's places (tv-chart.js precisionFor): one more
    // for each factor of ten, capped at 10, so 0.0000123 keeps its digits
    // instead of reading 0.000012.
    var dp = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 || abs === 0 ? 6
      : Math.min(10, Math.ceil(-Math.log10(abs)) + 4);
    return n.toFixed(dp).replace(/\.?0+$/, function (m) {
      return m.indexOf('.') === 0 ? '' : m;
    });
  }

  function pct(v, dp) {
    var n = num(v);
    if (n === null) return '—';
    var d = typeof dp === 'number' ? dp : 2;
    return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(d) + '%';
  }

  /** Colour is a claim; an unreadable number has not won. */
  function toneFor(v) {
    var n = num(v);
    if (n === null) return 'a-flat';
    if (n > 0) return 'a-up';
    if (n < 0) return 'a-down';
    return 'a-even';
  }

  /**
   * The signed-out state.
   *
   * Names what signing in DOES before asking for it. A bare "Sign in" button
   * in a trading app is a request to trust it with something unnamed.
   */
  function signedOutHtml(season, host, opts) {
    // The host is who signs the person in: Farcaster for the Mini App, Pi for
    // the Pi Browser app, a wallet for the DogeOS app, Telegram for the Duel's
    // Mini App. One sentence each, and nothing else differs.
    var who = host === 'Pi' ? 'Pi' : host === 'DogeOS' ? T('a DogeOS wallet') : host === 'Telegram' ? 'Telegram' : 'Farcaster';
    var intro = opts && opts.intro;
    if (intro) return introHtml(intro, who);
    // The Telegram Mini App is the Duel and its receipts, with no paper
    // account: its gate says what it is, and that nothing is bought or held.
    if (host === 'Telegram') {
      return '<section class="m-gate">'
        + '<p class="m-gate-lead">' + esc(T('Call the Daily Duel against the Claw, from Telegram.')) + '</p>'
        + '<p class="m-gate-sub">' + esc(T('Your Telegram account signs you in: no wallet, no real funds, nothing to buy.')) + '</p>'
        + '<button type="button" class="m-signin">' + esc(T('Sign in with {who}', { who: who })) + '</button>'
        + '</section>';
    }
    var name = season && season.name ? season.name : null;
    return '<section class="m-gate">'
      + '<p class="m-gate-lead">'
      + (name ? esc(T('Trade {name} with a virtual balance.', { name: name })) : esc(T('Trade the season with a virtual balance.')))
      + '</p>'
      + '<p class="m-gate-sub">' + esc(T('Signing in with {who} creates a paper account. No exchange keys, no real funds — everyone starts on the same balance.', { who: who })) + '</p>'
      + '<button type="button" class="m-signin">' + esc(T('Sign in with {who}', { who: who })) + '</button>'
      + '</section>';
  }

  /**
   * A host's own first screen: a picture, a promise, what is inside, then the
   * button, and what signing in does under it. The Pi app's gate was a
   * season name, a sentence and a button over two thirds of an empty screen.
   * The copy is the host's (`intro`); the shape and the button are shared.
   */
  function introHtml(intro0, who) {
    // A function, so a language chosen after the app started is followed.
    var intro = typeof intro0 === 'function' ? intro0() : intro0;
    var points = Array.isArray(intro.points) ? intro.points : [];
    return '<section class="m-gate m-gate--intro">'
      + (intro.image ? '<img class="m-gate-pic" src="' + esc(intro.image) + '" alt="" width="144" height="144" decoding="async">' : '')
      + (intro.title ? '<h1 class="m-gate-title">' + esc(intro.title) + '</h1>' : '')
      + (intro.lead ? '<p class="m-gate-lead">' + esc(intro.lead) + '</p>' : '')
      + (points.length ? '<ul class="m-gate-points">' + points.map(function (p) {
        return '<li><span class="m-gate-i" aria-hidden="true">' + esc(p.icon || '•') + '</span>'
          + '<span>' + esc(p.text) + '</span></li>';
      }).join('') + '</ul>' : '')
      + '<button type="button" class="m-signin">' + esc(T('Sign in with {who}', { who: who })) + '</button>'
      + '<p class="m-gate-sub">' + esc(intro.note || T('Signing in creates a practice account. No exchange keys, no real funds.')) + '</p>'
      + '</section>';
  }

  /**
   * Why a sign-in did not complete, in the viewer's terms.
   *
   * The four outcomes are four different sentences, and 'rejected' is NOT an
   * error — the person was asked and said no. Telling them something failed
   * would be describing their own decision back to them as a fault.
   */
  function signInProblemHtml(status, reason, host, opts) {
    var text;
    if (status === 'rejected') text = T('Sign-in cancelled.');
    else if (status === 'no-host') {
      text = host === 'Pi' ? T('Open this in the Pi Browser to sign in.')
        // The DogeOS app signs in with a browser wallet; none was found on the page.
        : host === 'DogeOS' ? T('No wallet found. Open this page in a browser with an EVM wallet (MetaMask, Rabby or the DogeOS wallet) to sign in.')
          // Opened in a browser, not from the bot: Telegram signed nothing.
          : host === 'Telegram' ? T('Open this from the RUNECLAW Duel bot in Telegram to sign in.')
            : T('Open this from a Farcaster app to sign in.');
    }
    // A Telegram launch is signed once, when the app opens; an old one is not a sign-in (lib/tg_webapp.js).
    else if (reason === 'expired') text = T('That launch is too old. Close the app and open it again from the bot.');
    else if (status === 'unknown') text = T('Sign-in did not come back. You can try again.');
    else if (reason === 'domain_mismatch') text = T('That signature was for a different app.');
    else if (reason === 'unknown_or_used_nonce') text = T('That sign-in expired. Try again.');
    else if (reason === 'token_rejected') text = T('Pi did not confirm that sign-in. Try again.');
    else if (status === 'unavailable') text = T('Sign-in is unavailable right now — this is on our side.');
    else text = T('Sign-in could not be completed.');
    // No accent colour on a cancellation: nothing went wrong.
    var kind = (status === 'rejected') ? 'note' : 'warn';
    return '<p class="m-problem m-problem--' + kind + '">' + esc(text) + '</p>'
      + (status === 'no-host' && host === 'DogeOS' && opts && opts.pageUrl ? copyLinkHtml(opts.pageUrl) : '');
  }

  /**
   * The page's own link, to carry into a wallet. A link to the DogeOS app
   * opened from X or Telegram lands in that app's own browser, which has no
   * wallet (the operator's phone, 2026-10-20), and "open this in a browser
   * with a wallet" left the person to find the address themselves. The box
   * is selectable when the clipboard is refused, as in-app browsers often do.
   */
  function copyLinkHtml(url) {
    return '<div class="m-copy">'
      + '<p class="m-copy-how">' + esc(T('Inside X or Telegram there is no wallet. Copy the link and open it in your wallet app’s browser.')) + '</p>'
      + '<div class="m-copy-row"><input class="m-copy-url" type="text" readonly value="' + esc(url) + '" aria-label="' + esc(T('This page’s link')) + '">'
      + '<button type="button" class="m-copy-b">' + esc(T('Copy link')) + '</button></div></div>';
  }

  /**
   * The account header: equity, return, and what is still spendable.
   *
   * `return_pct` is the number that ranks you, so it is the one that gets size
   * and colour. Balance is context.
   */
  function accountHtml(acct, opts) {
    var a = acct || {};
    return '<section class="m-acct">'
      + '<div class="m-eq">'
      + '<span class="m-eq-label">' + esc(T('equity')) + '</span>'
      + '<b class="m-eq-val">' + esc(vusdt(a.equity)) + '</b>'
      + '<span class="m-eq-unit">' + esc(unitLabel(opts)) + '</span>'
      + '<span class="m-eq-ret ' + toneFor(a.return_pct) + '">' + esc(pct(a.return_pct)) + '</span>'
      + '</div>'
      + '<p class="m-free">' + esc(T('{amount} free', { amount: vusdt(a.balance) })) + '</p>'
      + '</section>';
  }

  /**
   * One open position.
   *
   * `mark` and `pnl` are null when the price feed could not be read, and they
   * stay null on screen. A position whose mark we cannot see is not one that
   * is flat — printing 0.00 beside it would be the +0.00% sin with a virtual
   * stake attached, and it is the number a person decides whether to close on.
   */
  function positionHtml(p) {
    var pos = p || {};
    var dir = String(pos.direction || '').toUpperCase();
    var dirCls = dir === 'LONG' ? 'a-long' : dir === 'SHORT' ? 'a-short' : 'a-flat';
    var unreadable = num(pos.mark) === null;
    return '<li class="m-pos" data-pos-id="' + esc(pos.id) + '">'
      + '<div class="m-pos-head">'
      + '<span class="a-tick-dir ' + dirCls + '">' + esc(dir || '—') + '</span>'
      + '<b class="m-pos-sym">' + esc(pos.symbol || '—') + '</b>'
      + '<span class="m-pos-lev">' + esc(num(pos.leverage) === null ? '—' : num(pos.leverage) + '×') + '</span>'
      + '<span class="m-pos-pnl ' + toneFor(pos.pnl) + '">'
      + esc(num(pos.pnl) === null ? '—' : (num(pos.pnl) > 0 ? '+' : '') + vusdt(pos.pnl))
      + '</span>'
      + '</div>'
      + '<div class="m-pos-foot">'
      + '<span>' + esc(T('entry {price}', { price: price(pos.entry) })) + '</span>'
      // Said out loud rather than left as a dash to puzzle over: the number is
      // missing because the feed is, not because the position is worthless.
      + '<span>' + esc(unreadable ? T('mark unavailable') : T('mark {price}', { price: price(pos.mark) })) + '</span>'
      + '<span>' + esc(T('{amount} margin', { amount: vusdt(pos.margin) })) + '</span>'
      + '<button type="button" class="m-close" data-close-id="' + esc(pos.id) + '">' + esc(T('Close')) + '</button>'
      + '</div>'
      + '</li>';
  }

  /**
   * The positions list.
   *
   * An empty list is a MEASUREMENT — you hold nothing — and says so. The
   * caller never reaches this with a failed read; the account fetch throws
   * first, which is the only reason "no open positions" can be printed at all.
   */
  function positionsHtml(rows) {
    if (!rows || !rows.length) {
      return '<p class="m-none">' + esc(T('No open positions.')) + '</p>';
    }
    return '<ul class="m-pos-list">' + rows.map(positionHtml).join('') + '</ul>';
  }

  /**
   * The open form.
   *
   * Limits come from the server (`/account` sends them) rather than being
   * repeated here. A cap written into the UI drifts from the one that is
   * enforced, and then the form either rejects what the server allows or
   * offers what it refuses.
   *
   * Each field has a visible label above it. The three boxes had screen-reader
   * labels only, and placeholders ("BTC", "10", "lev") that looked like values
   * already filled in; a newcomer could not tell the form was empty. The
   * placeholders now read as examples, and one line says what long, short
   * and leverage do.
   */
  function openFormHtml(limits, opts) {
    var l = limits || {};
    var maxLev = num(l.max_leverage);
    var minMargin = num(l.min_margin);
    var unit = unitLabel(opts);
    return '<section class="m-open">'
      + '<h2>' + esc(T('Open a position')) + '</h2>'
      + '<div class="m-row m-row--fields">'
      + '<label class="m-field"><span class="m-label">' + esc(T('Market')) + '</span>'
      + '<input class="m-sym" type="text" inputmode="latin" autocapitalize="characters" '
      + 'placeholder="' + esc(T('e.g. BTC')) + '"></label>'
      + '<label class="m-field"><span class="m-label">' + esc(T('Amount ({unit})', { unit: unit })) + '</span>'
      + '<input class="m-margin" type="number" inputmode="decimal" '
      + 'placeholder="' + esc(minMargin === null ? T('e.g. 100') : T('min {n}', { n: minMargin })) + '" '
      + (minMargin === null ? '' : 'min="' + esc(minMargin) + '" ')
      + '></label>'
      + '<label class="m-field"><span class="m-label">' + esc(T('Leverage')) + '</span>'
      + '<input class="m-lev" type="number" inputmode="numeric" '
      + 'placeholder="' + esc(maxLev === null ? T('e.g. 2') : '1–' + maxLev) + '" '
      + (maxLev === null ? '' : 'max="' + esc(maxLev) + '" ') + 'min="1"></label>'
      + '</div>'
      + '<div class="m-row m-row--go">'
      + '<button type="button" class="m-long">' + esc(T('Long')) + '</button>'
      + '<button type="button" class="m-short">' + esc(T('Short')) + '</button>'
      + '</div>'
      + '<p class="m-open-how">' + esc(T('Long gains if the price rises, short if it falls. Leverage multiplies the move, the loss as much as the gain.')) + '</p>'
      + '<p class="m-open-note">'
      + (minMargin === null ? '' : esc(T('min {n} {unit}', { n: minMargin, unit: unit })) + ' · ')
      + (maxLev === null ? '' : esc(T('up to {n}×', { n: maxLev })) + ' · ')
      + esc(T('virtual funds only')) + '</p>'
      + '</section>';
  }

  /**
   * The result of an action, in the viewer's terms.
   *
   * The server's own error strings are used verbatim when it sends one: they
   * are written for a person ("Unknown symbol — use a listed USDT-M pair like
   * BTCUSDT") and replacing them with a generic failure would throw away the
   * only part that says what to do differently.
   */
  function actionNoteHtml(kind, text) {
    if (!text) return '';
    return '<p class="m-problem m-problem--' + esc(kind) + '">' + esc(text) + '</p>';
  }

  /** What the screen says while a host is asked (arena-session.js `waiting`), for the hosts that sign in without a tap. */
  function waitingFor(host) {
    return host === 'Telegram' ? [T('Checking your Telegram sign-in…')] : [];
  }

  return {
    waitingFor: waitingFor,
    signedOutHtml: signedOutHtml,
    signInProblemHtml: signInProblemHtml,
    accountHtml: accountHtml,
    positionsHtml: positionsHtml,
    positionHtml: positionHtml,
    openFormHtml: openFormHtml,
    actionNoteHtml: actionNoteHtml,
    openedText: openedText,
    closedText: closedText,
    unitLabel: unitLabel,
    T: T,
    sourceLine: sourceLine,
    legalHtml: legalHtml,
    vusdt: vusdt,
    price: price,
    pct: pct,
    toneFor: toneFor,
  };
}));
