/*
 * An idea's reward:risk and what the fees leave of it, worded once.
 *
 * The bot computes both figures (`bot/core/trade_costs.py::rr_fields`) at the
 * idea's own order type, and its Telegram cards word them with `rr_words`:
 * "6.47 (5.74 after fees)". Every page here printed the price ratio alone, so
 * a call that pays 5.7 after a taker round trip read 6.5 on the website and
 * 6.5 (5.7 after fees) on Telegram. A page cannot work the net figure out for
 * itself: the fee table is the bot's, and the order type is not on every
 * record. So the bot sends it, and this module says it in the same words.
 *
 * Three states, never two (CLAUDE.md, "unreadable is never zero"):
 *   `net_rr` a number       -> "5.74 after fees"
 *   `rr_fee_losing` true    -> "the target does not cover the fees"
 *   neither                 -> no clause: a record written before the
 *                              fields, or a level the bot could not read,
 *                              says nothing about fees rather than a figure.
 * A database row carries `rr_fee_losing` as 1/0 and `net_rr` as a decimal
 * string; both are read here, and `false` or `0` is not a fee claim.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCRRWords = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** A finite number, or null. Never a fallback zero. */
  function num(v) {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function figure(n, places, suffix) {
    return n.toFixed(places) + suffix;
  }

  /**
   * The clause after the price ratio, or null when the payload states none.
   * `src` is any payload carrying `net_rr` / `rr_fee_losing`.
   */
  function afterFees(src, opts) {
    if (!src || typeof src !== 'object') return null;
    var places = (opts && Number.isInteger(opts.places)) ? opts.places : 2;
    var suffix = (opts && typeof opts.suffix === 'string') ? opts.suffix : '';
    var net = num(src.net_rr);
    if (net !== null) return figure(net, places, suffix) + ' after fees';
    if (src.rr_fee_losing === true || src.rr_fee_losing === 1 || src.rr_fee_losing === '1') {
      return 'the target does not cover the fees';
    }
    return null;
  }

  /**
   * "6.47 (5.74 after fees)"; "6.47" when the payload states nothing about
   * fees; null when the price ratio itself is not a number, so the caller
   * paints its own unread state.
   */
  function text(gross, src, opts) {
    var g = num(gross);
    if (g === null) return null;
    var places = (opts && Number.isInteger(opts.places)) ? opts.places : 2;
    var suffix = (opts && typeof opts.suffix === 'string') ? opts.suffix : '';
    var clause = afterFees(src, opts);
    var head = figure(g, places, suffix);
    return clause ? head + ' (' + clause + ')' : head;
  }

  return { num: num, afterFees: afterFees, text: text };
}));
