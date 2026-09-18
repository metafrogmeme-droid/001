/**
 * THE PAPER-CARRY ROW — a percent of a stated stake, and never a dollar.
 *
 * §4: public surfaces carry percent / ratio / count only. `GET /api/reports`
 * is served to ANYONE — driven position-aware over the express dispatch
 * chain, it carries no auth middleware and no limiter — and it published
 * `arb.carries[].earned_usd` per coin.
 *
 * THE FIX REACHED THE VERDICT AND NOT THE ROWS BESIDE IT. The bot's
 * `_arb_section` strips the dollar out of `verdict` under a comment reading
 * "no dollar figure, because /api/reports is served to anyone" — three lines
 * above `carries`, which carried one per coin, and whose own `_carry_row`
 * popped the per-entry sample list while leaving the total it sums to. Ask
 * which OTHER field on the same payload makes the same claim.
 *
 * The producer emits `earned_pct` now — the same denominator
 * `verdict.mean_net_pct` already uses, so a row and the verdict beneath it
 * are in one unit. This module is what the panel reads it with, and it owns
 * three rules the panel had broken:
 *
 * UNREADABLE IS NOT ZERO. `Number(c.earned_usd) || 0` printed a carry nobody
 * could read as a measured break-even, and `pnlClass(c.earned_usd)` painted
 * it. COLOUR IS A CLAIM: a green accent says "this spread paid" as loudly as
 * the number does, and an absent carry has made no such claim. An unread row
 * gets a dash and a muted class.
 *
 * A PARTIAL TOTAL IS NOT A TOTAL. The footer summed `|| 0` across every row,
 * so rows nobody could read were counted as zeros and the total printed as
 * whole — the shapes table's own row. `total` carries `scored` and `n`, and
 * `sample` says so WHEN IT BITES and stays silent when every row was read: a
 * permanent caveat under a healthy table is how a reader learns to stop
 * reading the line.
 *
 * It is a module rather than a closure in the panel for the reason
 * `dashboard_helpers_are_in_scope.test.js` exists: a renderer reachable only
 * through a DOM event is one no test can drive, and a helper declared in one
 * function and called from another is a ReferenceError the moment that path
 * runs.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ArbCarryModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** A finite number, or null. A string, a NaN and an absent key are all null. */
  function read(v) {
    if (typeof v !== 'number' || !isFinite(v)) return null;
    return v;
  }

  /**
   * One carry as text. `0` is a MEASURED break-even — a spread that really
   * did pay nothing — so it prints, and only an unread one dashes.
   */
  function pct(v) {
    const n = read(v);
    if (n === null) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  /** The colour class. An unread carry claims neither direction. */
  function cls(v) {
    const n = read(v);
    if (n === null) return 'muted';
    if (n > 0) return 'up';
    if (n < 0) return 'down';
    return '';
  }

  /**
   * The total across the rows that could be READ, with the sample beside it.
   * `pct` is null when nothing was readable — a sum over an empty set is not
   * a measured zero.
   */
  function total(rows) {
    const list = Array.isArray(rows) ? rows : [];
    let sum = 0;
    let scored = 0;
    for (const r of list) {
      const n = read(r && r.earned_pct);
      if (n === null) continue;
      sum += n;
      scored += 1;
    }
    return { pct: scored ? Math.round(sum * 10000) / 10000 : null, scored: scored, n: list.length };
  }

  /**
   * The sample sentence, printed ONLY when rows were left out. Nothing was
   * read is its own case: the dash above has already said so, and a caveat
   * about a figure that is not there is a hedge rather than a disclosure.
   */
  function sample(t) {
    if (!t || !t.n) return '';
    if (!t.scored) return '';
    if (t.scored >= t.n) return '';
    return 'across ' + t.scored + ' of ' + t.n + ' coins that could be read';
  }

  /**
   * A whole-hour count, or a dash.
   *
   * The same rule as `pct`, on the same rows, for the same reason: the panel
   * reads a WIRE payload — `reports.js` forwards the bot's section verbatim
   * out of a DB row — so the reader cannot lean on the producer's invariants.
   * `compute_paper_carry` accumulates these from zero and they are real
   * floats today; a `|| 0` here still prints "held 0h" for a row whose hold
   * time did not arrive, which is a claim about how long a spread was held.
   * And `0h` is a MEASURED value too (a period observed and never entered),
   * so it must print rather than dash.
   */
  function hours(v) {
    const n = read(v);
    return n === null ? '—' : n.toFixed(0) + 'h';
  }

  /**
   * The sentence naming the stake every percent in the table is a percent OF.
   *
   * `Number(arb.notional_usd || 1000)` labelled the column `$1,000` whether or
   * not that was the basis the producer used. It is correct only while the
   * constant never moves — and a basis GUESSED for a payload that did not
   * state one is a claim about what the numbers beside it mean. Found by
   * re-reading this slice's own diff, on the sentence it had just rewritten to
   * explain the column.
   */
  function basis(v) {
    const n = read(v);
    if (n === null || n <= 0) {
      return 'Paper carry per spread, as a percent of the tracked stake '
        + '(the stake itself was not reported)';
    }
    return 'What $' + n.toLocaleString() + ' would have earned holding each '
      + 'spread, as a percent of that stake';
  }

  return {
    read: read, pct: pct, cls: cls, hours: hours,
    total: total, sample: sample, basis: basis,
  };
}));
