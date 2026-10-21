/**
 * The "Weekly public record" panel on /agents, as PURE functions.
 *
 * GET /api/public/record serves the committed weeks (app/lib/weekly_record.js);
 * this decides what a reader sees, so a test can drive every state: a week
 * read, no week committed yet, a part of a week that could not be read when
 * it was written, and a record that cannot be read now.
 *
 * A COMPOSITE: each part of a week (live book, parity, sleeve) is painted on
 * its own, and a part that could not be read says so in its own place while
 * the others stand. The panel as a whole GUARDS: an unreadable record paints
 * a sentence, never an empty panel.
 *
 * Every figure is a percent, a ratio or a count, printed in the text colour:
 * colour is a claim, and a weekly figure is not a verdict. A value the week
 * did not record is a dash with a title that says so, never 0. The sleeve
 * shows counts and dates only: its pre-registration forbids a look at its
 * rows before the sealed date.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RecordPanel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const fill = (tpl, map) => String(tpl).replace(/\{(\w+)\}/g, (_, k) => (map[k] != null ? map[k] : '{' + k + '}'));
  const english = (k, en) => en;
  const REPO = 'https://github.com/metafrogmeme-droid/001/blob/main/';

  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const count = (v) => Number.isInteger(v) && v >= 0;
  const signed = (v, dp) => (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v).toFixed(dp);

  /** The words for parity's two verdicts, keyed by the bot's own words. */
  const EDGE = {
    nothing: ['wkr.edge.nothing', 'no strategy exit on record'],
    thin: ['wkr.edge.thin', 'too few exits to judge ({n} of {min})'],
    positive: ['wkr.edge.positive', 'positive: the 95% interval on the per-trade result is above zero'],
    negative: ['wkr.edge.negative', 'negative: the 95% interval on the per-trade result is below zero'],
    straddles: ['wkr.edge.straddles', 'not measurable either way: the 95% interval straddles zero'],
  };
  const BALLPARK = {
    no_benchmark: ['wkr.bp.no_benchmark', 'no benchmark on record to compare against'],
    benchmark_unreadable: ['wkr.bp.unreadable', 'the benchmark could not be read, so nothing was compared'],
    thin: ['wkr.bp.thin', "too few exits in the benchmark's markets to compare"],
    in: ['wkr.bp.in', "hit rate within the benchmark's range"],
    below: ['wkr.bp.below', "hit rate below the benchmark's range"],
    above: ['wkr.bp.above', "hit rate above the benchmark's range"],
  };

  function na(T) {
    return '<span class="rec-na" title="' + esc(T('wkr.na', 'not recorded')) + '">—</span>';
  }

  function row(label, value) {
    return '<tr><th scope="row">' + esc(label) + '</th><td>' + value + '</td></tr>';
  }

  /** A part that could not be read when its week was written. */
  function partUnread(sec, T) {
    const s = sec.exception
      ? fill(T('wkr.part_unread', 'This part could not be read when the week was written ({x}). That is not a zero.'), { x: sec.exception })
      : T('wkr.part_unread_bare', 'This part could not be read when the week was written. That is not a zero.');
    return '<p class="rec-unread">' + esc(s) + '</p>';
  }

  function span(sec, T) {
    if (!count(sec.days_recorded) || !sec.first_day || !sec.last_day) return '';
    return esc(fill(T('wkr.span', '{n} days recorded, {first} to {last}'), { n: sec.days_recorded, first: sec.first_day, last: sec.last_day }))
      + (count(sec.gap_days) && sec.gap_days > 0
        ? ' · ' + esc(fill(T('wkr.gaps', '{n} days with no reading'), { n: sec.gap_days })) : '');
  }

  function equityHtml(sec, T) {
    let h = '<section class="rec-part"><h3>' + esc(T('wkr.eq.h', 'Live book')) + '</h3>';
    if (!sec || sec.state === 'unreadable') return h + partUnread(sec || {}, T) + '</section>';
    if (sec.state !== 'read') {
      return h + '<p class="rec-empty">' + esc(T('wkr.eq.absent', 'No daily live equity has been recorded. The bot records it only while it trades live.')) + '</p></section>';
    }
    h += '<p class="rec-meta">' + span(sec, T) + '</p><table class="rec-table"><tbody>';
    h += row(T('wkr.eq.ret', 'Change over the record'), finite(sec.return_pct) ? esc(signed(sec.return_pct, 2) + '%') : na(T));
    h += row(T('wkr.eq.dd', 'Largest fall from a high'), finite(sec.max_drawdown_pct) ? esc(signed(sec.max_drawdown_pct, 2) + '%') : na(T));
    let sharpe;
    if (finite(sec.sharpe)) sharpe = esc(signed(sec.sharpe, 2));
    else if (count(sec.daily_returns) && count(sec.min_daily_returns) && sec.daily_returns < sec.min_daily_returns) {
      sharpe = '<span class="rec-wait">' + esc(fill(T('wkr.eq.sharpe_wait', 'after {min} daily returns ({n} so far)'), { min: sec.min_daily_returns, n: sec.daily_returns })) + '</span>';
    } else sharpe = na(T);
    h += row(T('wkr.eq.sharpe', 'Sharpe ratio (daily returns, annualised)'), sharpe);
    h += '</tbody></table>';
    // Said whenever the log does not say otherwise: a missing flag is not a
    // separated record.
    if (sec.flows_separated !== true) {
      h += '<p class="rec-note">' + esc(T('wkr.eq.flows', 'Deposits and withdrawals are not separated from trading in this record: a deposit reads as a gain and a withdrawal as a loss.')) + '</p>';
    }
    return h + '</section>';
  }

  function verdictWords(table, word, params, T) {
    const hit = Object.prototype.hasOwnProperty.call(table, word) ? table[word] : null;
    return hit ? esc(fill(T(hit[0], hit[1]), params)) : na(T);
  }

  function parityHtml(sec, T) {
    let h = '<section class="rec-part"><h3>' + esc(T('wkr.pa.h', 'Live exits against the backtest')) + '</h3>';
    if (!sec || sec.state === 'unreadable') return h + partUnread(sec || {}, T) + '</section>';
    if (sec.state !== 'read') {
      return h + '<p class="rec-empty">' + esc(T('wkr.pa.absent', 'No live strategy exit is on record.')) + '</p></section>';
    }
    const v = sec.verdict || {};
    h += '<table class="rec-table"><tbody>';
    h += row(T('wkr.pa.trades', 'Strategy exits'), count(sec.trades) ? esc(String(sec.trades)) : na(T));
    h += row(T('wkr.pa.wr', 'Hit rate'), finite(sec.win_rate) ? esc((sec.win_rate * 100).toFixed(1) + '%') : na(T));
    h += row(T('wkr.pa.pf', 'Profit factor'), finite(sec.pf) ? esc(sec.pf.toFixed(2)) : na(T));
    h += row(T('wkr.pa.fee', 'Fees against the model'), finite(sec.fee_vs_model) ? esc(sec.fee_vs_model.toFixed(2) + '×') : na(T));
    h += row(T('wkr.pa.edge', 'Live edge'), verdictWords(EDGE, v.edge, { n: v.n, min: v.min_trades }, T));
    h += row(T('wkr.pa.bp', 'Against the benchmark'), verdictWords(BALLPARK, v.ballpark, {}, T));
    h += '</tbody></table>';
    if (count(v.outside) && v.outside > 0 && count(v.n)) {
      h += '<p class="rec-note">' + esc(fill(T('wkr.pa.outside', "{n} of {total} exits are outside the benchmark's markets and are not in the comparison."), { n: v.outside, total: v.n })) + '</p>';
    }
    if (count(sec.inferred_fills) && sec.inferred_fills > 0) {
      h += '<p class="rec-note">' + esc(fill(T('wkr.pa.inferred', '{n} exits were priced from the ticker, not from a venue fill.'), { n: sec.inferred_fills })) + '</p>';
    }
    return h + '</section>';
  }

  function sleeveHtml(sec, T) {
    let h = '<section class="rec-part"><h3>' + esc(T('wkr.sl.h', 'Daily trend sleeve (paper)')) + '</h3>';
    if (!sec || sec.state === 'unreadable') return h + partUnread(sec || {}, T) + '</section>';
    h += sec.state === 'read'
      ? '<p class="rec-meta">' + span(sec, T) + '</p>'
      : '<p class="rec-empty">' + esc(T('wkr.sl.absent', "No day of the sleeve's book has been recorded yet.")) + '</p>';
    if (sec.measured_on) {
      h += '<p class="rec-note">' + esc(fill(T('wkr.sl.sealed', 'Its result is sealed: the pre-registration fixes the first measurement for {date}, by someone who has not looked at the rows before then. Until then this shows only that the record is being kept. It places no orders.'), { date: sec.measured_on }));
      if (sec.prereg) {
        h += ' <a href="' + esc(REPO + sec.prereg) + '" rel="noopener" target="_blank">' + esc(T('wkr.sl.prereg', 'Pre-registration')) + '</a>';
      }
      h += '</p>';
    }
    return h + '</section>';
  }

  /** The panel for a 200 from /api/public/record. */
  function render(payload, T) {
    T = T || english;
    if (!payload || !Array.isArray(payload.weeks)) return unreadable(null, T);
    let h = '<p class="rec-lede">' + esc(T('wkr.lede', "Once a week the bot's records are frozen into a file committed to the repository, and this panel shows that file. Percent, ratio and count only. A week once written is not rewritten; a correction goes in the next week's file.")) + '</p>';
    const rec = payload.record;
    if (!payload.weeks.length || !rec) {
      return h + '<div class="empty">' + esc(T('wkr.none', 'No week has been committed yet.')) + '</div>';
    }
    h += '<div class="rec-head"><span class="rec-week">' + esc(fill(T('wkr.week', 'Week {week}'), { week: rec.week })) + '</span>';
    if (rec.as_of) h += ' · ' + esc(fill(T('wkr.as_of', 'frozen {when}'), { when: String(rec.as_of).slice(0, 16).replace('T', ' ') + ' UTC' }));
    if (payload.weeks.length > 1) {
      h += ' <label class="rec-pick">' + esc(T('wkr.pick', 'Other weeks')) + ' <select id="rec-week">'
        + payload.weeks.map((w) => '<option value="' + esc(w) + '"' + (w === rec.week ? ' selected' : '') + '>' + esc(w) + '</option>').join('')
        + '</select></label>';
    }
    h += '</div><div class="rec-grid">' + equityHtml(rec.equity, T) + parityHtml(rec.parity, T) + sleeveHtml(rec.sleeve, T) + '</div>';
    if (payload.source) {
      h += '<p class="disc">' + esc(T('wkr.source', "Every week's file, as committed:")) + ' <a href="' + esc(payload.source) + '" rel="noopener" target="_blank">benchmark/record</a></p>';
    }
    return h;
  }

  /** The panel when the record could not be read: a sentence, never a blank. */
  function unreadable(exception, T) {
    T = T || english;
    let s = T('wkr.unreadable', 'The weekly record could not be read just now, so no week is shown. That is a read failure, not an empty record.');
    if (exception) s += ' (' + exception + ')';
    return '<div class="empty rec-unread">' + esc(s) + '</div>';
  }

  return { render, unreadable, EDGE, BALLPARK };
}));
