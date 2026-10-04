/*
 * The word on one setup cell.
 *
 *   readingForCell(cell, registrations) -> 'exploratory' | 'survives'
 *
 * 'survives' is painted only when the cell was pre-registered and that
 * registration was replicated on prospective data. A q-value under 0.05,
 * a mean-R interval clear of zero, and a planted word are not that record.
 * The cell's own mean_r_lo / mean_r_hi are a different quantity and are
 * not read here.
 *
 * A registration matches on all five published dimensions. A missing
 * dimension matches nothing: the cell is not given a label by this
 * function beyond 'exploratory', and the scoreboard omits a cell that
 * never had the dimension. Prospective replication is the interval on
 * the prospective window. Both ends have to be finite measurements, in
 * order, with the lower end strictly above zero. Touching zero is not
 * clear. An unreadable end is not a zero. One registration that did not
 * replicate keeps the cell exploratory even when another one did: two
 * answers are not a survives.
 *
 * The records this consults are passed in. The loader that reads the
 * tree is app/lib/cell_registrations.js. This file does not open one,
 * so the browser and the analytics route ask the same function.
 *
 * Exposed as window.RCSetupReading; module.exports in node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCSetupReading = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var EXPLORATORY = 'exploratory';
  var SURVIVES = 'survives';

  function num(v) {
    // null before Number: Number(null) is 0, and a missing bound is not
    // a measured zero. A boolean is not a bound either (Number(true) is 1).
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    var n = typeof v === 'number' ? v : Number(v);
    return (typeof n === 'number' && isFinite(n)) ? n : null;
  }

  function label(v) {
    if (typeof v !== 'string') return null;
    var text = v.trim();
    return text ? text : null;
  }

  function sameCell(cell, reg) {
    var keys = ['setup', 'regime', 'timeframe', 'source'];
    for (var i = 0; i < keys.length; i++) {
      var a = label(cell && cell[keys[i]]);
      var b = label(reg && reg[keys[i]]);
      if (a == null || b == null || a !== b) return false;
    }
    var da = label(cell && cell.direction);
    var db = label(reg && reg.direction);
    if (da == null || db == null || da.toUpperCase() !== db.toUpperCase()) return false;
    return true;
  }

  /**
   * The prospective window replicated when its interval is a measurement
   * strictly above zero. A missing end, a reversed pair, and a lower end
   * of 0 are not a replication. 0 is kept as 0 by `num` and still fails
   * the strict test.
   */
  function replicated(reg) {
    var lo = num(reg && reg.prospective_lo);
    var hi = num(reg && reg.prospective_hi);
    if (lo === null || hi === null) return false;
    if (!(hi >= lo)) return false;
    return lo > 0;
  }

  /**
   * The registrations that name this cell and were actually pre-registered.
   *
   * Extra keys are dropped. A dollar-named field on a record does not ride
   * onto the public cell. An unreadable bound is published as null, not 0.
   */
  function matchedRegistrations(cell, registrations) {
    var list = Array.isArray(registrations) ? registrations : [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var r = list[i];
      if (!r || r.preregistered !== true || !sameCell(cell, r)) continue;
      out.push({
        setup: label(r.setup),
        regime: label(r.regime),
        timeframe: label(r.timeframe),
        source: label(r.source),
        direction: label(r.direction).toUpperCase(),
        preregistered: true,
        prospective_lo: num(r.prospective_lo),
        prospective_hi: num(r.prospective_hi),
      });
    }
    return out;
  }

  function readingForCell(cell, registrations) {
    var hits = matchedRegistrations(cell, registrations);
    if (!hits.length) return EXPLORATORY;
    for (var i = 0; i < hits.length; i++) {
      if (!replicated(hits[i])) return EXPLORATORY;
    }
    return SURVIVES;
  }

  return {
    readingForCell: readingForCell,
    matchedRegistrations: matchedRegistrations,
    EXPLORATORY: EXPLORATORY,
    SURVIVES: SURVIVES,
  };
}));
