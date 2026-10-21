/*
 * /claw-record: the agent's own calls, counted only where sealed before
 * their outcome (lib/claw_record.js, GET /api/public/claw-record).
 *
 * COLOUR IS A CLAIM. The win rate is never green: below the sample floor it
 * is muted and labelled too few to judge, and above it the number speaks for
 * itself. A resolved call's own outcome may carry its colour; it is a fact
 * the receipt beside it proves. Anchors are linked to /roots, which checks
 * them against their chains live, and are never painted here.
 *
 * render(data, T) is pure, for app/test/the_claw_record_counts_only_sealed_calls.test.js.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RCClawRecord = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function fill(s, vars) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? vars[k] : m; });
  }
  var tr = function (T) { return typeof T === 'function' ? T : function (k, en) { return en; }; };

  function num(v) { return v == null ? '—' : String(v); }

  /** The page body for one window's record. */
  function render(d, t) {
    var T = tr(t);
    if (!d || typeof d !== 'object' || typeof d.resolved !== 'number') {
      return '<p class="cr-note" data-state="unreadable">' + esc(T('cr.unreadable', 'The record could not be read just now. Try again shortly.')) + '</p>';
    }
    var out = [];
    var tooFew = d.verdict !== 'measured';
    out.push('<div class="cr-head">');
    out.push('<div class="cr-stat"><span class="cr-v">' + esc(num(d.sealed_calls)) + '</span><span class="cr-k">' + esc(T('cr.sealed', 'sealed calls')) + '</span></div>');
    out.push('<div class="cr-stat"><span class="cr-v">' + esc(num(d.resolved)) + '</span><span class="cr-k">' + esc(T('cr.resolved', 'resolved')) + '</span></div>');
    out.push('<div class="cr-stat"><span class="cr-v' + (tooFew ? ' cr-muted' : '') + '" data-field="win_rate">'
      + (d.win_rate == null ? '—' : esc(d.win_rate) + '%') + '</span><span class="cr-k">' + esc(T('cr.win_rate', 'win rate')) + '</span></div>');
    out.push('<div class="cr-stat"><span class="cr-v' + (tooFew ? ' cr-muted' : '') + '">' + esc(num(d.avg_r)) + '</span><span class="cr-k">' + esc(T('cr.avg_r', 'average R (gross)')) + '</span></div>');
    out.push('</div>');

    // The floor: how far the sample is from supporting a verdict.
    var pct = Math.max(0, Math.min(100, Math.round((d.resolved / d.floor) * 100)));
    out.push('<div class="cr-floor"><div class="cr-bar" role="progressbar" aria-valuemin="0" aria-valuemax="' + esc(d.floor)
      + '" aria-valuenow="' + esc(Math.min(d.resolved, d.floor)) + '"><span style="width:' + pct + '%"></span></div>'
      + '<span class="cr-chip" data-verdict="' + esc(d.verdict) + '">'
      + esc(tooFew ? T('cr.too_few', 'Too few to judge') : T('cr.measured', 'Measured')) + '</span> '
      + '<span class="cr-k">' + esc(fill(T('cr.floor', '{n} of {floor} resolved calls before this record is a verdict'), { n: d.resolved, floor: d.floor })) + '</span></div>');

    if (d.partial) out.push('<p class="cr-note">' + esc(T('cr.partial', 'This window hit the read limit: the counts are partial.')) + '</p>');

    var no = d.no_outcome || {};
    var noKeys = Object.keys(no);
    if (noKeys.length) {
      out.push('<p class="cr-k">' + esc(T('cr.no_outcome', 'No outcome yet, not in the rate')) + ': '
        + noKeys.sort().map(function (k) { return esc(k) + ' ' + esc(no[k]); }).join(' · ') + '</p>');
    }
    var ex = d.excluded || {};
    out.push('<p class="cr-k">' + esc(fill(T('cr.excluded', 'Not counted: {u} unsealed, {l} sealed after their outcome'),
      { u: num(ex.unsealed), l: num(ex.sealed_after_outcome) })) + '</p>');

    if (!d.sealed_calls) {
      out.push('<p class="cr-note" data-state="empty">' + esc(T('cr.empty', 'No sealed call was made in this window.')) + '</p>');
      return out.join('');
    }

    out.push('<h2>' + esc(T('cr.days_h', 'By day')) + '</h2><div class="cr-scroll"><table class="cr-table"><tbody>');
    (d.days || []).forEach(function (day) {
      var rootCell = day.root
        ? '<a href="/roots" title="' + esc(day.root) + '">' + esc(T('cr.root_link', 'root & anchors'))
          + (day.anchors && day.anchors.length ? ' (' + esc(day.anchors.join(', ')) + ')' : '') + '</a>'
        : '<span class="cr-k">' + esc(T('cr.no_root', 'no root yet')) + '</span>';
      out.push('<tr><td class="mono">' + esc(day.day) + '</td><td>' + esc(day.sealed) + ' ' + esc(T('cr.sealed', 'sealed calls'))
        + '</td><td>' + esc(day.resolved) + ' ' + esc(T('cr.resolved', 'resolved')) + '</td><td>' + rootCell + '</td></tr>');
    });
    out.push('</tbody></table></div>');

    if ((d.recent || []).length) {
      out.push('<h2>' + esc(T('cr.recent_h', 'Latest resolved calls')) + '</h2><div class="cr-scroll"><table class="cr-table"><tbody>');
      d.recent.forEach(function (c) {
        var word = c.outcome === 'win' ? T('cr.win', 'win') : c.outcome === 'loss' ? T('cr.loss', 'loss') : T('cr.flat', 'flat');
        out.push('<tr><td class="mono">' + esc(String(c.resolved_at || c.created_at).slice(0, 10)) + '</td><td>' + esc(c.symbol)
          + ' ' + esc(c.direction) + '</td><td><span class="cr-out" data-outcome="' + esc(c.outcome) + '">' + esc(word) + '</span> '
          + '<span class="mono">' + (c.r > 0 ? '+' : '') + esc(c.r) + 'R</span></td><td><a href="' + esc(c.receipt) + '">'
          + esc(T('cr.receipt', 'receipt')) + '</a></td></tr>');
      });
      out.push('</tbody></table></div>');
    }
    return out.join('');
  }

  return { render: render };
}));
