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
  function signedOutHtml(season) {
    var name = season && season.name ? season.name : null;
    return '<section class="m-gate">'
      + '<p class="m-gate-lead">'
      + (name ? 'Trade ' + esc(name) + ' with a virtual stake.' : 'Trade the season with a virtual stake.')
      + '</p>'
      + '<p class="m-gate-sub">Signing in with Farcaster creates a paper account. '
      + 'No exchange keys, no real funds — everyone starts on the same stake.</p>'
      + '<button type="button" class="m-signin">Sign in with Farcaster</button>'
      + '</section>';
  }

  /**
   * Why a sign-in did not complete, in the viewer's terms.
   *
   * The four outcomes are four different sentences, and 'rejected' is NOT an
   * error — the person was asked and said no. Telling them something failed
   * would be describing their own decision back to them as a fault.
   */
  function signInProblemHtml(status, reason) {
    var text;
    if (status === 'rejected') text = 'Sign-in cancelled.';
    else if (status === 'no-host') text = 'Open this from a Farcaster app to sign in.';
    else if (status === 'unknown') text = 'Sign-in did not come back. You can try again.';
    else if (reason === 'domain_mismatch') text = 'That signature was for a different app.';
    else if (reason === 'unknown_or_used_nonce') text = 'That sign-in expired. Try again.';
    else if (status === 'unavailable') text = 'Sign-in is unavailable right now — this is on our side.';
    else text = 'Sign-in could not be completed.';
    // No accent colour on a cancellation: nothing went wrong.
    var kind = (status === 'rejected') ? 'note' : 'warn';
    return '<p class="m-problem m-problem--' + kind + '">' + esc(text) + '</p>';
  }

  /**
   * The account header: equity, return, and what is still spendable.
   *
   * `return_pct` is the number that ranks you, so it is the one that gets size
   * and colour. Balance is context.
   */
  function accountHtml(acct) {
    var a = acct || {};
    return '<section class="m-acct">'
      + '<div class="m-eq">'
      + '<span class="m-eq-label">equity</span>'
      + '<b class="m-eq-val">' + esc(vusdt(a.equity)) + '</b>'
      + '<span class="m-eq-unit">vUSDT</span>'
      + '<span class="m-eq-ret ' + toneFor(a.return_pct) + '">' + esc(pct(a.return_pct)) + '</span>'
      + '</div>'
      + '<p class="m-free">' + esc(vusdt(a.balance)) + ' free</p>'
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
      + '<span>entry ' + esc(vusdt(pos.entry)) + '</span>'
      // Said out loud rather than left as a dash to puzzle over: the number is
      // missing because the feed is, not because the position is worthless.
      + '<span>' + (unreadable ? 'mark unavailable' : 'mark ' + esc(vusdt(pos.mark))) + '</span>'
      + '<span>' + esc(vusdt(pos.margin)) + ' margin</span>'
      + '<button type="button" class="m-close" data-close-id="' + esc(pos.id) + '">Close</button>'
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
      return '<p class="m-none">No open positions.</p>';
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
   */
  function openFormHtml(limits) {
    var l = limits || {};
    var maxLev = num(l.max_leverage);
    var minMargin = num(l.min_margin);
    return '<section class="m-open">'
      + '<h2>Open a position</h2>'
      + '<div class="m-row">'
      + '<input class="m-sym" type="text" inputmode="latin" autocapitalize="characters" '
      + 'placeholder="BTCUSDT" aria-label="Symbol">'
      + '<input class="m-margin" type="number" inputmode="decimal" '
      + 'placeholder="' + esc(minMargin === null ? 'margin' : String(minMargin)) + '" '
      + (minMargin === null ? '' : 'min="' + esc(minMargin) + '" ')
      + 'aria-label="Margin in vUSDT">'
      + '<input class="m-lev" type="number" inputmode="numeric" placeholder="lev" '
      + (maxLev === null ? '' : 'max="' + esc(maxLev) + '" ') + 'min="1" aria-label="Leverage">'
      + '</div>'
      + '<div class="m-row m-row--go">'
      + '<button type="button" class="m-long">Long</button>'
      + '<button type="button" class="m-short">Short</button>'
      + '</div>'
      + '<p class="m-open-note">'
      + (minMargin === null ? '' : 'min ' + esc(minMargin) + ' vUSDT · ')
      + (maxLev === null ? '' : 'up to ' + esc(maxLev) + '× · ')
      + 'virtual funds only</p>'
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

  return {
    signedOutHtml: signedOutHtml,
    signInProblemHtml: signInProblemHtml,
    accountHtml: accountHtml,
    positionsHtml: positionsHtml,
    positionHtml: positionHtml,
    openFormHtml: openFormHtml,
    actionNoteHtml: actionNoteHtml,
    vusdt: vusdt,
    pct: pct,
    toneFor: toneFor,
  };
}));
