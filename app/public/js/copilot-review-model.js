/* Co-pilot review — what the ticket's second opinion may CLAIM on this page.
 *
 * The bot sends a verdict, a score with its own span, and a list of the
 * subjects it could NOT check with a sentence each. This model decides one
 * thing: which badge the block wears. It deliberately derives no sentence and
 * no score of its own -- `score_line` and each unchecked `reason` arrive as
 * text from the producer, because a figure re-derived in the browser is the
 * second reading the seam exists to replace (the rule the arb panel states).
 *
 * A VERDICT THIS BUILD DOES NOT KNOW IS NOT `clear`. The block used to be
 * `d.verdict === 'clear' ? CLEAR : CAUTION`, so `partial` -- nothing flagged,
 * something unchecked -- would have worn the word for a finding, and any word
 * a later bot build invents would too. `badge()` answers null for one it
 * cannot place, and the renderer paints an unreadable state rather than
 * guessing which of the four it meant.
 */
(function (root) {
  'use strict';

  var BADGES = {
    clear:   { key: 'clear',   label: 'CLEAR',    cls: 'cop-badge--clear' },
    caution: { key: 'caution', label: 'CAUTION',  cls: 'cop-badge--caution' },
    // `partial` is muted, not green and not amber: nothing was found AND not
    // everything was looked at, and either colour would assert one half of
    // that as the whole of it. Colour is a claim.
    partial: { key: 'partial', label: 'PARTIAL',  cls: 'cop-badge--partial' },
    invalid: { key: 'invalid', label: 'BLOCKED',  cls: 'cop-badge--invalid' }
  };

  function badge(rev) {
    if (!rev || typeof rev !== 'object') return null;
    var v = rev.verdict;
    return Object.prototype.hasOwnProperty.call(BADGES, v) ? BADGES[v] : null;
  }

  /* The rows the coverage list prints: {label, reason}, in the producer's own
   * order and words. A row missing either is DROPPED rather than rendered with
   * a blank half -- "not checked — : " claims a subject nobody named. */
  function coverage(rev) {
    var rows = (rev && rev.unchecked) || [];
    if (!Array.isArray(rows)) return [];
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r || typeof r !== 'object') continue;
      var label = typeof r.label === 'string' ? r.label : '';
      var reason = typeof r.reason === 'string' ? r.reason : '';
      if (!label || !reason) continue;
      out.push({ label: label, reason: reason });
    }
    return out;
  }

  /* The score with its span, as the bot wrote it. Null when the bot sent none
   * -- an older build, or a review with no score -- and the renderer then
   * prints no score at all rather than a bare number whose basis is unstated,
   * which is the whole defect this slice is about. */
  function scoreLine(rev) {
    var s = rev && rev.score_line;
    return (typeof s === 'string' && s.trim()) ? s.trim() : null;
  }

  var api = { BADGES: BADGES, badge: badge, coverage: coverage, scoreLine: scoreLine };
  root.CopilotReviewModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
