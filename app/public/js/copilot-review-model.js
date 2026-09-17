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

  /* THE ADVISORY FOOTER IS ONE SENTENCE IN TWO RUNTIMES. The bot renders the
   * same review onto a Telegram card, and `trade_copilot.COPILOT_FOOTER` is
   * this string byte for byte -- a guard pins the two equal, because a reader
   * who takes a green badge for a permission has read it wrong and two
   * surfaces wording that differently is two answers about what the badge
   * means. */
  var FOOTER = 'Advice only \u2014 the risk gate (and your Authority Envelope, for live) remain the authority.';

  /* WHAT THE BLOCK SAYS WHEN THERE IS NO REVIEW AT ALL. `null` is the bot
   * saying it could not produce one; a review carrying a verdict this build
   * cannot place is the bot saying something this PAGE cannot read. Different
   * facts, different sentences -- and neither is "nothing was found", because
   * the Confirm button beside the block is live either way. */
  var NO_REVIEW = 'The co-pilot did not review this ticket \u2014 nothing here has been checked.';
  var UNREADABLE = 'The co-pilot answered with a verdict this page cannot read \u2014 nothing here has been reviewed.';

  /* The block's HTML. ONE renderer: the dashboard ticket, the dashboard's
   * confirm modal and the chat drawer's trade card all call it, because a
   * second copy in a second bundle is a second answer about what the review
   * says -- and two of those three had no review on them at all until the
   * reading moved onto the proposal.
   *
   * `esc` is the CALLER's escaper (each bundle has its own) and is REQUIRED:
   * a renderer that silently stops escaping publishes a producer sentence as
   * markup, so an absent one throws rather than degrading. */
  function render(rev, esc) {
    if (typeof esc !== 'function') throw new Error('copilot render needs an escaper');
    if (!rev || typeof rev !== 'object') return '<span class="muted">' + esc(NO_REVIEW) + '</span>';
    var b = badge(rev);
    if (!b) return '<span class="muted">' + esc(UNREADABLE) + '</span>';
    if (b.key === 'invalid') {
      var first = (rev.flags && rev.flags[0] && rev.flags[0].msg) || 'Invalid geometry.';
      return '<span class="neg">\u26d4 ' + esc(first) + '</span>';
    }
    var out = '<span class="cop-badge ' + b.cls + '">' + esc(b.label) + '</span> ';
    var line = scoreLine(rev);
    if (line) out += '<b>' + esc(line) + '</b> \u00b7 ';
    out += 'R:R ' + esc(String(rev.rr == null ? '\u2014' : rev.rr))
        + ' \u00b7 stop ' + esc(String(rev.stop_pct == null ? '\u2014' : rev.stop_pct))
        + '% \u00b7 target ' + esc(String(rev.target_pct == null ? '\u2014' : rev.target_pct)) + '%';
    var i;
    var flags = Array.isArray(rev.flags) ? rev.flags : [];
    for (i = 0; i < flags.length; i++) {
      if (flags[i] && typeof flags[i].msg === 'string') {
        out += '<div class="kv-row"><span>\u26a0\ufe0f ' + esc(flags[i].msg) + '</span></div>';
      }
    }
    var notes = Array.isArray(rev.notes) ? rev.notes : [];
    for (i = 0; i < notes.length; i++) {
      if (typeof notes[i] === 'string') {
        out += '<div class="kv-row"><span class="muted">\u00b7 ' + esc(notes[i]) + '</span></div>';
      }
    }
    var rows = coverage(rev);
    for (i = 0; i < rows.length; i++) {
      out += '<div class="kv-row"><span class="cop-uncheck">\u25e6 Not checked \u2014 '
           + esc(rows[i].label) + ': ' + esc(rows[i].reason) + '</span></div>';
    }
    return out + '<p class="muted small" style="margin-top:var(--s1)">' + esc(FOOTER) + '</p>';
  }

  var api = { BADGES: BADGES, FOOTER: FOOTER, NO_REVIEW: NO_REVIEW,
              UNREADABLE: UNREADABLE, badge: badge, coverage: coverage,
              scoreLine: scoreLine, render: render };
  root.CopilotReviewModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
