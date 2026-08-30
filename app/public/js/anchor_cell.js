'use strict';
/**
 * The anchor cell on /roots — a claim about the CHAIN, so only the chain may make it.
 *
 * WHAT WAS WRONG
 *
 * The row rendered its anchor state straight from the database column:
 *
 *     r.anchor_tx ? green("⛓ anchored on Base") : "not yet anchored on-chain"
 *
 * The unanchored half was honest. The other half painted `var(--up)` — the
 * profit green — on the strength of a field in our own table, asserting an
 * on-chain fact that nothing had re-checked. `GET /api/roots/verify/:day`
 * exists to substantiate exactly that and was called by nothing.
 *
 * COLOUR IS A CLAIM. Green says "verified" as loudly as the word does, and on
 * the one page whose entire purpose is "do not take our word for it", taking
 * our word for it was the implementation. A reorg, a replaced transaction, or
 * a corrupted column would all keep showing green.
 *
 * This mattered BEFORE the first anchor rather than after: while every day is
 * unanchored the bug is invisible, and it becomes a false claim the moment the
 * feature starts being used.
 *
 * FOUR STATES, because "we could not read the chain" is not a verdict:
 *
 *   unanchored  no transaction has been sent for this day     neutral
 *   verified    the chain confirmed the calldata is this root GREEN
 *   mismatch    a tx is recorded and it does NOT anchor this  RED — an alarm
 *   unknown     the chain could not be read just now          neutral, never green
 *
 * `mismatch` is the loud one on purpose. It means the recorded anchor and the
 * root disagree, which is either corruption or tampering, and it is the single
 * thing this page exists to make impossible to hide.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCAnchorCell = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  /**
   * Which of the four states a row is in.
   *
   * `verdict` is the parsed body of /api/roots/verify/:day, or null when the
   * lookup has not run or could not run. Null is deliberately NOT treated as
   * "fine" — an anchored row whose verification never happened is `unknown`,
   * not `verified`.
   */
  function anchorState(row, verdict) {
    if (!row || !row.anchor_tx) return 'unanchored';
    if (!verdict || typeof verdict !== 'object') return 'unknown';
    var s = verdict.status;
    if (s === 'verified') return 'verified';
    if (s === 'unanchored') return 'unanchored';
    // MATCHED EXACTLY, because the server's vocabulary is closed: root_anchor.js
    // documents and returns precisely three — verified / mismatch / unknown.
    // The REASON varies; the status does not.
    if (s === 'mismatch') return 'mismatch';
    // Everything else — 'unknown', null, or a word this build has never heard
    // of — is unknown. Not verified, because an unfamiliar word is not evidence
    // of agreement; and NOT an alarm either, because raising a tampering
    // warning over a vocabulary gap is a false alarm, and false alarms are how
    // a real one comes to be ignored.
    return 'unknown';
  }

  /**
   * The cell's HTML. `t` is the i18n lookup (key, fallback) so the page can
   * translate without this module importing anything.
   */
  function anchorCell(row, verdict, t) {
    var T = typeof t === 'function' ? t : function (k, en) { return en; };
    var state = anchorState(row, verdict);
    var tx = row && row.anchor_tx ? String(row.anchor_tx) : '';
    var href = 'https://basescan.org/tx/' + esc(tx);

    if (state === 'unanchored') {
      return '<span class="n">' + esc(T('rt.unanchored', 'not yet anchored on-chain')) + '</span>';
    }

    if (state === 'verified') {
      // The ONLY green path, and it is green because the chain said so.
      var when = verdict && verdict.block_time
        ? ' · ' + esc(String(verdict.block_time).slice(0, 10)) : '';
      return '<a class="n" style="color:var(--up)" href="' + href
        + '" target="_blank" rel="noopener">⛓ '
        + esc(T('rt.anchored', 'verified on Base')) + when + '</a>';
    }

    if (state === 'mismatch') {
      // Loud. This is corruption or tampering, and the page exists to surface
      // it rather than to keep a tidy row.
      return '<a class="n" style="color:var(--down);font-weight:600" href="' + href
        + '" target="_blank" rel="noopener">⚠ '
        + esc(T('rt.anchor_mismatch', 'recorded anchor does NOT match this root'))
        + '</a>';
    }

    // unknown — a transaction is recorded and we could not confirm it just
    // now. Neutral, never green, and it says which of the two it is.
    return '<a class="n" href="' + href + '" target="_blank" rel="noopener">⛓ '
      + esc(T('rt.anchor_unverified', 'anchor recorded — chain not reachable to confirm'))
      + '</a>';
  }

  return { anchorState: anchorState, anchorCell: anchorCell, esc: esc };
}));
