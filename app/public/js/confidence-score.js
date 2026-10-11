/*
 * RCConfidenceScore — a signal's confidence, worded once for the website.
 *
 *   text(v[, scale]) -> 'score 72 (uncalibrated)' | null
 *   number(v[, scale]) -> '72' | null
 *
 * Every page printed a signal's confidence as `72%`, which reads as a
 * probability, and it is not one. The figure the bot publishes on a signal row
 * is `displayed_confidence(idea).value` (bot/core/signal_confidence.py): the
 * analyzer's blend, snapshotted BEFORE any calibration curve, or a producer's
 * own score that never went through one. No figure that reaches these pages
 * has been mapped to outcomes, so it is printed as a score and says so.
 *
 * THIS IS A TWIN, NOT A SECOND READING. The two words are the bot's
 * `SCORE_WORD` and `UNCALIBRATED_WORD`, and `MEANING` is its `SCORE_MEANING`;
 * app/test/a_confidence_is_a_score_on_every_page.test.js reads all three out of
 * the Python source and fails the day either side changes alone.
 *
 * Unreadable is never zero: a value that is not a finite number answers null,
 * and the caller keeps its own absent wording (a dash, or the field omitted).
 * `scale` is 1 for a 0-1 figure (the signal row) and 100 for one a route has
 * already multiplied (the arena's signal list).
 *
 * Exposed as window.RCConfidenceScore; module.exports in node.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RCConfidenceScore = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SCORE_WORD = 'score';
  var UNCALIBRATED_WORD = 'uncalibrated';
  var MEANING = "A confidence score is the analyzer's own 0-100 rating of a setup. It is "
    + 'not a probability or a win rate: no fitted calibration curve has mapped '
    + 'it to outcomes, so never describe it as a chance or likelihood of '
    + 'winning.';

  /** The figure on the 0-1 scale, or null when it is not a finite number. */
  function read(v, scale) {
    if (v === null || v === undefined || typeof v === 'boolean') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    if (!isFinite(n)) return null;
    var s = scale === 100 ? 100 : 1;
    return n / s;
  }

  /** '72' for 0.72 (or 72 at scale 100), null when unreadable. */
  function number(v, scale) {
    var n = read(v, scale);
    return n === null ? null : String(Math.round(n * 100));
  }

  /** 'score 72 (uncalibrated)', or null when unreadable. */
  function text(v, scale) {
    var n = number(v, scale);
    return n === null ? null : SCORE_WORD + ' ' + n + ' (' + UNCALIBRATED_WORD + ')';
  }

  return {
    SCORE_WORD: SCORE_WORD,
    UNCALIBRATED_WORD: UNCALIBRATED_WORD,
    MEANING: MEANING,
    number: number,
    text: text,
  };
}));
