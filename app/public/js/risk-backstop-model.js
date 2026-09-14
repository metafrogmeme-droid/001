/**
 * The risk backstop's reading, as a PURE function.
 *
 * Same argument as engine-status-model.js: the most expensive claim on the
 * page should be assertable, and a claim built inline in nine thousand lines
 * of browser script has no seam at which to assert it. This is the card an
 * operator reads to decide how much real money the bot may lose before it
 * halts, and it makes four separate claims about the live gate, each of
 * which is worse wrong than absent.
 *
 * FIVE outcomes at the top and three per row, because the repo has shipped a
 * bug from conflating each pair. The scan's AGE is read first: whatever the
 * scan carries — figures, the fault marker, or nothing at all — is a claim
 * made at the scan's own time, so it is a memory or undated whatever it
 * holds, and reading the block first would print "not published" or "engine
 * fault" off a four-hour-old scan as though it were true of the bot now.
 *
 *   undated    -> the scan carries no readable stamp, so nothing it carries
 *                 can be dated, and an undated backstop is not a reading of
 *                 now.
 *   stale      -> the scan is older than the page vouches for. Whatever it
 *                 carried is a MEMORY. Not shown.
 *   absent     -> this engine build publishes no backstop. A fact about the
 *                 BOT — fixed by a redeploy.
 *   unreadable -> the bot published the marker `{unreadable: true}`: it could
 *                 not assemble the reading. A fault in the engine, fixed by
 *                 looking at it, and not the same event as the one above.
 *   read       -> each row then decides for itself.
 *
 * WHAT THIS FILE DELIBERATELY DOES NOT DO. It holds no band table: the
 * verdict word arrives from the bot's `live_risk_status`, which bands off
 * the limit the breaker is ACTUALLY enforcing — a second copy here is a
 * second answer. It holds no staleness threshold: `maxAgeS` is passed in and
 * the caller passes EngineStatusModel.STALE_MAX_S, so the page has one age
 * vocabulary. And it draws a bar ONLY over numbers AND a verdict that were
 * all read — a track with an invisible fill reads as zero, and on this card
 * zero is the all-clear.
 *
 * The model returns KEYS, never English. Every word it can emit is in `W`
 * and the renderer lists each one as a literal `T('dd.…')` call.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RiskBackstopModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const W = {
    'dd.rb_h_absent': 'Not published',
    'dd.rb_h_unread': 'Could not be assembled',
    'dd.rb_h_undated': 'Undated',
    'dd.rb_h_stale': 'Last read {when}',
    'dd.rb_absent': 'This engine build does not publish a backstop reading, so nothing here is a measurement of it.',
    'dd.rb_unread_engine': 'The engine could not assemble its backstop reading — a fault in the engine, not a build that lacks the reading, and not a flat book.',
    'dd.rb_no_age': 'The engine scan carries no readable timestamp, so this reading cannot be dated — and an undated backstop is not a reading of the present.',
    'dd.rb_stale': 'The last engine scan is older than this card will vouch for, so the backstop figures it carried are not shown. They are a memory.',
    'dd.rb_dd': 'Drawdown backstop',
    'dd.rb_slots': 'Position slots',
    'dd.rb_gate': 'New entries',
    'dd.rb_override': 'Override',
    'dd.rb_operator': 'The operator engine’s backstop — read-only, the same numbers for every viewer. Not your account.',
    'dd.rb_gate_refused': 'Refused:',
    'dd.rb_dd_unread': 'The drawdown state is unknown — a failed read, not a flat equity curve, and it does not mean the backstop is clear.',
    'dd.rb_no_limit': 'No halt threshold on record, so this figure carries no verdict — it is a number, not a reading of how close the book is to stopping.',
    'dd.rb_no_dd': 'The current drawdown could not be read, so this card cannot say how much of that threshold is left.',
    'dd.rb_no_verdict': 'The engine returned no verdict for this reading, so no bar is drawn.',
    'dd.rb_src_live': 'live equity high-water mark',
    'dd.rb_src_paper': 'paper snapshot',
    'dd.rb_src_unknown': 'source unknown',
    'dd.rb_slots_unread': 'The open-position count could not be read. This is not a book with nothing in it.',
    'dd.rb_cap_unread': 'No position cap on record, so this count carries no headroom.',
    'dd.rb_floor': 'A floor, not a count — {note}.',
    'dd.rb_gate_paused': 'Paused',
    'dd.rb_gate_unknown': 'Unknown',
    'dd.rb_gate_active': 'Active',
    'dd.rb_gate_absent': 'not published',
    'dd.rb_gate_unknown_why': 'Could not read the full trading-gate status — this card cannot confirm entries are open.',
    'dd.rb_gate_absent_why': 'This engine build does not publish the trading-gate state.',
    'dd.rb_override_none': 'none (default {d})',
    'dd.rb_override_set': '{p} (default {d})',
    'dd.rb_hardening_off': 'Live hardening is OFF — the override only bites on live.',
  };
  const KEYS = Object.keys(W);

  // Number('') is 0 AND isFinite(0) is true — the trap app.js records for
  // pnlClass — so an empty string must not arrive as a measured zero;
  // Number(false) is 0 too. NaN is typeof 'number' and fails isFinite: the JS
  // spelling of the bot's `dd != dd` test, which exists because every
  // comparison against NaN is False and it would slip a band check as
  // "below the limit" — the calmest answer, from a broken read. A real 0.0
  // survives: a flat equity curve is a measurement.
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }
  function obj(v) { return (v && typeof v === 'object' && !Array.isArray(v)) ? v : null; }

  // The server's verdict WORD -> a class. A word this map does not know gets
  // NO class and NO bar: muted, never the calmest of the three.
  const VERDICT_CLS = { Healthy: 'rb-up', Warning: 'rb-warn', Critical: 'rb-down' };
  const SOURCE = { live: 'dd.rb_src_live', paper: 'dd.rb_src_paper' };

  function drawdownRow(b) {
    const pct = num(b.drawdown_pct);
    // `limit <= 0` is not a limit — the same rejection the bot makes. A zero
    // threshold would score every book Critical.
    const lim = num(b.limit_pct);
    const limit = (lim !== null && lim > 0) ? lim : null;
    const src = SOURCE[b.source] || 'dd.rb_src_unknown';
    if (pct === null && limit === null) {
      return { state: 'unread', pct: null, limit: null, src, cls: '', fill: null, verdict: null, why: 'dd.rb_dd_unread' };
    }
    if (pct === null) {
      return { state: 'partial', pct: null, limit, src, cls: '', fill: null, verdict: null, why: 'dd.rb_no_dd' };
    }
    if (limit === null) {
      // A drawdown with no limit to judge it against is a number, not a
      // verdict. Report the number; decline the bar and decline the colour.
      return { state: 'partial', pct, limit: null, src, cls: '', fill: null, verdict: null, why: 'dd.rb_no_limit' };
    }
    const verdict = (typeof b.verdict === 'string' && b.verdict) ? b.verdict : null;
    const cls = (verdict && VERDICT_CLS[verdict]) || '';
    // THE BAR IS DRAWN ONLY OVER A PAIR OF NUMBERS AND A VERDICT THAT WERE
    // ALL READ. Two numbers with a verdict word this page does not know would
    // otherwise paint a full-length track with an invisible fill — which on
    // this card reads as 0% drawdown, full headroom, from a verdict nobody
    // could read. Clamped, because a breach runs past the end of the track.
    return {
      state: 'read', pct, limit, src, cls, verdict,
      fill: cls ? Math.max(0, Math.min(100, (pct / limit) * 100)) : null,
      why: cls ? null : 'dd.rb_no_verdict',
    };
  }

  function slotsRow(b) {
    const used = num(b.slots_used);
    const c = num(b.slots_cap);
    const cap = (c !== null && c > 0) ? Math.round(c) : null;
    // A FLOOR is what the gate itself computes when a venue went unread, or
    // when the cross-venue aggregator raised: "under the cap proves nothing —
    // the venue that did not answer is exactly the one that might hold the
    // position putting this person over". A real magnitude — "at least n" —
    // so it keeps its track, wears --warn, and names why.
    const floor = b.slots_floor === true;
    const note = (typeof b.slots_note === 'string' && b.slots_note) ? b.slots_note : null;
    if (used === null) {
      return { state: 'unread', used: null, cap, floor: false, note, cls: '', valCls: '', fill: null, why: 'dd.rb_slots_unread' };
    }
    const n = Math.round(used);
    if (cap === null) {
      return { state: 'partial', used: n, cap: null, floor, note, cls: '', valCls: '', fill: null, why: 'dd.rb_cap_unread' };
    }
    // The fill's class and the figure's: a floor colours both, a count colours
    // the fill only (capacity is not a verdict). Both words are the model's,
    // so the renderer spells no colour class of its own.
    return {
      state: floor ? 'floor' : 'read', used: n, cap, floor, note,
      cls: floor ? 'rb-warn' : 'rb-cap',
      valCls: floor ? 'rb-warn' : '',
      fill: Math.max(0, Math.min(100, (n / cap) * 100)),
      why: null,
    };
  }

  // `blocked` and `unknown` are INDEPENDENT: a confirmed breaker plus one
  // unreadable field is still blocked, and the caller has no reason to hedge
  // about it. Blocked is therefore tested FIRST. ONLY a positive reading of
  // BOTH flags licenses the green word: a gate object whose flags are
  // missing, null, or a truthy string falls to Unknown — rounding an
  // unreadable field to "clear" is the bug the gate was written for.
  function gateRow(b) {
    const g = obj(b.gate);
    if (g === null) {
      return { state: 'absent', cls: 'chip--offline', label: 'dd.rb_gate_absent', why: 'dd.rb_gate_absent_why', reasons: [] };
    }
    const reasons = Array.isArray(g.reasons) ? g.reasons.filter((x) => typeof x === 'string' && x) : [];
    if (g.blocked === true) {
      return { state: 'blocked', cls: 'chip--down', label: 'dd.rb_gate_paused', why: null, reasons };
    }
    if (g.blocked === false && g.unknown === false) {
      return { state: 'active', cls: 'chip--up', label: 'dd.rb_gate_active', why: null, reasons: [] };
    }
    return { state: 'unknown', cls: 'chip--warn', label: 'dd.rb_gate_unknown', why: 'dd.rb_gate_unknown_why', reasons };
  }

  function overrideRow(b) {
    return {
      pct: num(b.override_pct),
      defaultPct: num(b.default_limit_pct),
      // Only claimed when the value IS the boolean false: a partial payload
      // (or the seam's own null for a non-bool) cannot manufacture an OFF.
      hardeningOff: b.hardening === false,
    };
  }

  /**
   * @param {*} b              circuit_breaker.backstop — absent, the marker, or the block
   * @param {number|null} ageSec  age of the SCAN that carried it, or null when it has no readable stamp
   * @param {number} maxAgeS   the shared staleness floor (EngineStatusModel.STALE_MAX_S)
   */
  function readBackstop(b, ageSec, maxAgeS) {
    const age = num(ageSec);
    const max = num(maxAgeS);
    if (age === null || max === null) {
      return { state: 'undated', head: 'dd.rb_h_undated', why: 'dd.rb_no_age', ageSec: null };
    }
    if (age > max) {
      // THE MUTATION THAT MATTERS. Delete this branch and a four-hour-old
      // drawdown renders as current, with every other assertion still
      // green — and renderPanel's "stale beats blank" would then keep it on
      // screen through any number of failed refreshes.
      return { state: 'stale', head: 'dd.rb_h_stale', why: 'dd.rb_stale', ageSec: age, maxAgeS: max };
    }
    const block = obj(b);
    if (block === null) {
      return { state: 'absent', head: 'dd.rb_h_absent', why: 'dd.rb_absent', ageSec: age };
    }
    if (block.unreadable === true) {
      return { state: 'unreadable', head: 'dd.rb_h_unread', why: 'dd.rb_unread_engine', ageSec: age };
    }
    return {
      state: 'read', ageSec: age,
      drawdown: drawdownRow(block), slots: slotsRow(block),
      gate: gateRow(block), override: overrideRow(block),
    };
  }

  return { readBackstop, drawdownRow, slotsRow, gateRow, overrideRow, num, VERDICT_CLS, W, KEYS };
}));
