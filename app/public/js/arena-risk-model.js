/**
 * Arena risk model — what a stop actually caps, and what the whole book
 * risks if every stop fills. Pure arithmetic on numbers the account payload
 * already carries; usable from the browser and via require() in tests (the
 * txray-model both-sides pattern).
 *
 * Honesty contract:
 * - Loss-at-stop mirrors the engine's own fill math (lib/arena.js pnlAt,
 *   clamped at -margin — isolated margin can never lose more than itself);
 *   the cross-pin test asserts equality against the engine, so the two can
 *   never drift apart silently.
 * - Stops fill AT the trigger price in the Arena; a gapping market can be
 *   worse. Every surface says so.
 * - A position without a stop has no cap: its risk is UNBOUNDED until
 *   liquidation. Heat never pretends otherwise — it reports what is capped
 *   and counts what is not, and an uncapped book never shows a reassuring
 *   total.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCArenaRisk = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Loss if the stop fills at its trigger price (positive number, vUSDT),
   *  clamped at margin. null when there is no stop — never 0: an absent cap
   *  is not a zero risk. */
  function lossAtStop(pos) {
    var entry = Number(pos && pos.entry), margin = Number(pos && pos.margin);
    var lev = Number(pos && pos.leverage), sl = Number(pos && pos.sl);
    if (!(entry > 0) || !(margin > 0) || !(lev > 0) || !(sl > 0)) return null;
    var dir = String(pos.direction).toUpperCase() === 'SHORT' ? -1 : 1;
    // The engine's pnlAt(pos, sl): margin × dir × (sl/entry − 1) × lev,
    // clamped at −margin. A stop on the PROFIT side of entry caps no loss.
    var pnl = Math.max(-margin, margin * dir * (sl / entry - 1) * lev);
    return pnl >= 0 ? 0 : -pnl;
  }

  /** Loss-at-stop as a percent of equity. null without a stop or equity. */
  function riskPct(pos, equity) {
    var loss = lossAtStop(pos);
    var eq = Number(equity);
    if (loss == null || !(eq > 0)) return null;
    return Math.round(loss / eq * 1000) / 10;
  }

  /**
   * Portfolio heat: the summed loss if EVERY stop filled at its trigger,
   * against current equity — plus the count of positions with no stop at
   * all, whose risk no arithmetic can cap.
   */
  function heat(positions, equity) {
    var eq = Number(equity);
    var loss = 0, stopped = 0, unbounded = 0;
    (positions || []).forEach(function (p) {
      var l = lossAtStop(p);
      if (l == null) { unbounded++; return; }
      stopped++; loss += l;
    });
    return {
      stopped: stopped,
      unbounded: unbounded,
      loss: Math.round(loss * 100) / 100,
      pct: eq > 0 && stopped > 0 ? Math.round(loss / eq * 1000) / 10 : null,
    };
  }

  return { lossAtStop: lossAtStop, riskPct: riskPct, heat: heat };
}));
