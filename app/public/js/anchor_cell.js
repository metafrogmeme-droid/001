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
  /** The chain an anchor without a named chain is on: Base, as every anchor was before 2026-10-19. */
  var BASE = { key: 'base', name: 'Base', explorer_tx: 'https://basescan.org/tx/' };

  function anchorCell(row, verdict, t, chain) {
    var T = typeof t === 'function' ? t : function (k, en) { return en; };
    // A named chain is the chain, explorer or not: one the registry has not
    // described gets its key and NO link, never Base's explorer for its hash.
    var c = chain && chain.key ? chain : BASE;
    var state = anchorState(row, verdict);
    var tx = row && row.anchor_tx ? String(row.anchor_tx) : '';
    var href = c.explorer_tx ? c.explorer_tx + esc(tx) : '';
    // The cell is a link to the transaction when the chain has an explorer to
    // link, and the same words without one when it does not.
    var open = function (style) {
      return href
        ? '<a class="n"' + (style ? ' style="' + style + '"' : '') + ' href="' + href + '" target="_blank" rel="noopener">'
        : '<span class="n"' + (style ? ' style="' + style + '"' : '') + '>';
    };
    var close = href ? '</a>' : '</span>';

    if (state === 'unanchored') {
      return '<span class="n">' + esc(T('rt.unanchored', 'not yet anchored on-chain')) + '</span>';
    }

    if (state === 'verified') {
      // The ONLY green path, and it is green because the chain said so.
      var when = verdict && verdict.block_time
        ? ' · ' + esc(String(verdict.block_time).slice(0, 10)) : '';
      return open('color:var(--up)') + '⛓ '
        + esc(T('rt.anchored_on', 'verified on {chain}').replace('{chain}', c.name || c.key)) + when + close;
    }

    if (state === 'mismatch') {
      // Loud. This is corruption or tampering, and the page exists to surface
      // it rather than to keep a tidy row.
      return open('color:var(--down);font-weight:600') + '⚠ '
        + esc(T('rt.anchor_mismatch', 'recorded anchor does NOT match this root'))
        + close;
    }

    // unknown — a transaction is recorded and we could not confirm it just
    // now. Neutral, never green, and it says which of the two it is.
    return open('') + '⛓ '
      + esc(T('rt.anchor_unverified', 'anchor recorded — chain not reachable to confirm'))
      + close;
  }

  /**
   * Every chain's cell for one day, from the feed row's `anchors` list (or its
   * older `anchor_tx`, read as Base). `verdicts` is keyed by chain key, each
   * the parsed body of /api/roots/verify/:day?chain=, or absent; `chains` is
   * keyed by chain key from /api/roots (name and explorer). No anchor on any
   * chain is said once; an anchor is a cell per chain, each painted only from
   * what that chain said.
   */
  function anchorCells(row, verdicts, t, chains) {
    var T = typeof t === 'function' ? t : function (k, en) { return en; };
    var list = row && Array.isArray(row.anchors) ? row.anchors.slice() : [];
    if (!list.length && row && row.anchor_tx) list.push({ chain: 'base', tx: row.anchor_tx });
    if (!list.length) {
      return '<span class="n">' + esc(T('rt.unanchored', 'not yet anchored on-chain')) + '</span>';
    }
    return list.map(function (a) {
      var c = (chains && chains[a.chain]) || (a.chain === 'base' ? BASE : { key: a.chain, name: a.chain, explorer_tx: '' });
      var v = verdicts && Object.prototype.hasOwnProperty.call(verdicts, a.chain) ? verdicts[a.chain] : null;
      return anchorCell({ anchor_tx: a.tx }, v, T, c);
    }).join(' <span class="n">·</span> ');
  }
  /**
   * The day's Bitcoin cell (OpenTimestamps). `summary` is the feed row's `ots`
   * ({ submitted, bitcoin_height } from our own table, or null when it could
   * not be read); `verdict` is the parsed body of /api/roots/ots/:day, the
   * live re-check, or null when it has not run. Green only on the live
   * check's word, exactly as the chain cells: our column alone never paints
   * a day as in Bitcoin.
   */
  function otsCell(summary, verdict, t, day) {
    var T = typeof t === 'function' ? t : function (k, en) { return en; };
    var proof = function () {
      return ' <a class="n" href="/api/roots/ots/' + esc(day) + '/proof" download>'
        + esc(T('rt.btc_proof', 'proof file')) + '</a>';
    };
    var s = verdict && typeof verdict === 'object' ? verdict.status : null;
    if (s === 'verified' && verdict.bitcoin_height) {
      return '<span class="n" style="color:var(--up)">₿ '
        + esc(T('rt.btc_verified', 'in Bitcoin block {n}').replace('{n}', String(verdict.bitcoin_height)))
        + '</span>' + proof();
    }
    if (s === 'mismatch') {
      return '<span class="n" style="color:var(--down);font-weight:600">⚠ '
        + esc(T('rt.btc_mismatch', 'Bitcoin proof does NOT match this root')) + '</span>';
    }
    if (!summary || typeof summary !== 'object') {
      return '<span class="n">₿ ' + esc(T('rt.btc_unknown', 'Bitcoin proof recorded — not reachable to confirm')) + '</span>';
    }
    if (!summary.submitted) return '<span class="n">₿ ' + esc(T('rt.btc_none', 'not yet in Bitcoin')) + '</span>';
    // Submitted. Our row may already name a height; until the live check
    // agrees, that is "recorded, not confirmed", never green.
    if (s === 'pending' || (!s && summary.bitcoin_height == null)) {
      return '<span class="n">₿ ' + esc(T('rt.btc_pending', 'Bitcoin: waiting for a block (OpenTimestamps)')) + '</span>' + proof();
    }
    return '<span class="n">₿ ' + esc(T('rt.btc_unknown', 'Bitcoin proof recorded — not reachable to confirm')) + '</span>' + proof();
  }

  /** What each independence group is called on the page (lib/anchor_quorum.js names the groups). */
  var GROUP_NAMES = { bitcoin: 'Bitcoin', dogecoin: 'Dogecoin', 'ethereum-l2': 'Base', solana: 'Solana' };

  /**
   * A day's Anchor Quorum (GET /api/roots/quorum/:day) as one line. Green
   * only for "multi-anchored", which the server says only when k independent
   * chains verified the root live; and beside it, always, what it is not: a
   * timestamp proves when, never how well. An answer this cannot read is
   * said, never drawn as "not anchored".
   */
  function quorumCell(q, t) {
    var T = typeof t === 'function' ? t : function (k, en) { return en; };
    if (!q || typeof q !== 'object' || !Array.isArray(q.groups) || typeof q.k !== 'number') {
      return '<span class="n q-cell" data-state="unreadable">' + esc(T('rt.q_unread', 'quorum could not be checked')) + '</span>';
    }
    var fill = function (s) { return String(s).replace('{n}', String(q.groups.length)).replace('{k}', String(q.k)); };
    var names = q.groups.map(function (g) { return GROUP_NAMES[g] || g; }).join(' + ');
    var out;
    if (q.status === 'multi_anchored') {
      out = '<span class="n q-cell" data-state="multi" style="color:var(--up)">⛓ '
        + esc(fill(T('rt.q_multi', 'Multi-anchored: {n} independent chains'))) + ' · ' + esc(names) + '</span>'
        + ' <span class="n">(' + esc(T('rt.q_meaning', 'time-proven, not performance')) + ')</span>';
    } else if (q.status === 'unknown') {
      out = '<span class="n q-cell" data-state="unknown">⛓ '
        + esc(fill(T('rt.q_unknown', '{n} of {k} independent chains so far; some could not be checked'))) + '</span>';
    } else {
      out = '<span class="n q-cell" data-state="short">⛓ '
        + esc(fill(T('rt.q_short', '{n} of {k} independent chains'))) + (names ? ' · ' + esc(names) : '') + '</span>';
    }
    if (Array.isArray(q.mismatched) && q.mismatched.length) {
      out += ' <span class="n" style="color:var(--down);font-weight:600">⚠ '
        + esc(T('rt.q_mismatch', 'a recorded anchor no longer checks out')) + ': ' + esc(q.mismatched.join(', ')) + '</span>';
    }
    return out;
  }

  return { anchorState: anchorState, anchorCell: anchorCell, anchorCells: anchorCells, otsCell: otsCell, quorumCell: quorumCell,
    esc: esc, BASE: BASE };
}));
