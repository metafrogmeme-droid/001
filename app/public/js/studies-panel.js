/**
 * The "Pre-registered studies" panel on /agents, as PURE functions.
 *
 * GET /api/public/studies serves the registry (app/lib/studies.js); this
 * decides what a reader sees. It is its own file so a test can drive every
 * state: studies read, a study with a field the registry did not record, and
 * a registry that could not be read.
 *
 * A single-source panel, so it GUARDS: an unreadable registry paints a
 * sentence that says so, never an empty list (which would read as "no study
 * was run"). The panel sits below the catalogue and its failure does not
 * touch the catalogue.
 *
 * Numbers are Sharpe ratios, percents and counts, never dollars. They are
 * printed in the text colour: a Sharpe ratio is not a profit, and colour is a
 * claim. The one coloured word is a pre-registered pass, which is the claim
 * the study was sealed to make; a failure is said in words, muted. A value
 * the registry did not record is a dash with a title that says so, never 0.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StudiesPanel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const REPO = 'https://github.com/metafrogmeme-droid/001/blob/main/';

  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const signed = (v, dp) => (v < 0 ? '−' : '') + Math.abs(v).toFixed(dp);
  const ratio = (v) => signed(v, 2);
  const percent = (v) => signed(v, 1) + '%';

  /**
   * The result columns, in order. A study shows the columns any of its rows
   * carries, so a re-costing run is not given a development Sharpe it never
   * had. [field, i18n key, English heading, format].
   */
  const COLUMNS = [
    ['dev_sharpe', 'studies.c_dev', 'Development Sharpe', ratio],
    ['holdout_sharpe', 'studies.c_hold', 'Holdout Sharpe', ratio],
    ['sharpe', 'studies.c_sharpe', 'Sharpe', ratio],
    ['holdout_window_sharpe', 'studies.c_hw', 'Holdout-window Sharpe', ratio],
    ['cagr_pct', 'studies.c_cagr', 'Yearly growth', percent],
    ['max_dd_pct', 'studies.c_dd', 'Max drawdown', percent],
    ['holdout_max_dd_pct', 'studies.c_hdd', 'Holdout max drawdown', percent],
    ['funding_share_of_gross_profit_pct', 'studies.c_fund', 'Funding share of gross profit', percent],
    ['years_up', 'studies.c_years', 'Years up', (v) => String(v)],
    ['bootstrap90_full_sharpe', 'studies.c_boot', '90% interval, full-period Sharpe',
      (v) => '[' + ratio(v[0]) + ', ' + ratio(v[1]) + ']'],
  ];

  const fill = (tpl, map) => String(tpl).replace(/\{(\w+)\}/g, (_, k) => (map[k] != null ? map[k] : '{' + k + '}'));

  const english = (k, en) => en;

  function cell(row, field, fmt, T) {
    const v = row[field];
    const ok = field === 'bootstrap90_full_sharpe'
      ? Array.isArray(v) && v.length === 2 && finite(v[0]) && finite(v[1])
      : field === 'years_up' ? typeof v === 'string' && v !== '' : finite(v);
    if (!ok) return '<td class="st-na" title="' + esc(T('studies.not_recorded', 'not recorded')) + '">—</td>';
    return '<td>' + esc(fmt(v)) + '</td>';
  }

  function verdictCell(row, T) {
    if (row.pass === true) return '<td><span class="st-pass">' + esc(T('studies.passed', 'passed')) + '</span></td>';
    if (row.pass === false) return '<td class="st-fail">' + esc(T('studies.failed', 'did not pass')) + '</td>';
    return '<td class="st-na" title="' + esc(T('studies.not_a_test', 'not a pass/fail test')) + '">—</td>';
  }

  function tally(study, T) {
    const n = study.tests;
    const p = study.passes;
    if (!Number.isInteger(n) || !Number.isInteger(p)) return esc(T('studies.tally_unread', 'pass count not recorded'));
    return esc(fill(T('studies.tally', '{p} of {n} passed'), { p: p, n: n }));
  }

  function studyHtml(study, T) {
    const rows = Array.isArray(study.results) ? study.results : [];
    const cols = COLUMNS.filter(([f]) => rows.some((r) => r && r[f] != null));
    const anyVerdict = rows.some((r) => r && typeof r.pass === 'boolean');
    let h = '<article class="st-card">';
    h += '<h3>' + esc(study.title || study.id) + '</h3>';
    h += '<div class="st-meta"><span class="st-tally">' + tally(study, T) + '</span>';
    if (study.sealed_at) {
      h += ' · ' + esc(fill(T('studies.sealed', 'sealed {date}'), { date: String(study.sealed_at).slice(0, 10) }));
    }
    const re = Array.isArray(study.resealed_for_addenda_at) ? study.resealed_for_addenda_at.length : 0;
    if (re) h += ' · ' + esc(fill(T('studies.resealed', 're-sealed for {n} addenda'), { n: re }));
    h += '</div>';
    if (study.question) h += '<p class="st-q">' + esc(study.question) + '</p>';
    if (study.verdict) h += '<p class="st-verdict"><b>' + esc(T('studies.verdict', 'Verdict')) + ':</b> ' + esc(study.verdict) + '</p>';
    if (rows.length) {
      h += '<div class="st-scroll"><table class="st-table"><thead><tr><th>'
        + esc(T('studies.c_rule', 'Rule')) + '</th>';
      for (const [, k, en] of cols) h += '<th>' + esc(T(k, en)) + '</th>';
      if (anyVerdict) h += '<th>' + esc(T('studies.c_pass', 'Result')) + '</th>';
      h += '</tr></thead><tbody>';
      for (const r of rows) {
        if (!r || typeof r.name !== 'string') continue;
        h += '<tr><th scope="row">' + esc(r.name) + '</th>';
        for (const [f, , , fmt] of cols) h += cell(r, f, fmt, T);
        if (anyVerdict) h += verdictCell(r, T);
        h += '</tr>';
      }
      h += '</tbody></table></div>';
    }
    h += '<details class="st-more"><summary>' + esc(T('studies.more', 'Data, windows, pass rule and limits')) + '</summary><dl>';
    const w = study.windows || {};
    if (w.development || w.holdout) {
      h += '<dt>' + esc(T('studies.windows', 'Windows')) + '</dt><dd>'
        + esc(fill(T('studies.windows_v', 'development {d} · holdout {h}'), { d: w.development || '—', h: w.holdout || '—' })) + '</dd>';
    }
    for (const [f, k, en] of [['data', 'studies.data', 'Data'], ['pass_rule', 'studies.pass_rule', 'Pass rule'], ['limits', 'studies.limits', 'Limits']]) {
      if (study[f]) h += '<dt>' + esc(T(k, en)) + '</dt><dd>' + esc(study[f]) + '</dd>';
    }
    if (study.prereg) {
      h += '<dt>' + esc(T('studies.prereg', 'Pre-registration')) + '</dt><dd><a href="' + esc(REPO + study.prereg)
        + '" rel="noopener" target="_blank">' + esc(study.prereg) + '</a>';
      if (study.prereg_sha256) h += '<br><span class="st-sha">SHA-256 ' + esc(study.prereg_sha256) + '</span>';
      h += '</dd>';
    }
    h += '</dl></details></article>';
    return h;
  }

  /** The panel for a 200 from /api/public/studies. */
  function render(payload, T) {
    T = T || english;
    const studies = payload && Array.isArray(payload.studies) ? payload.studies : null;
    if (studies === null) return unreadable(null, T);
    let h = '<p class="st-lede">' + esc(T('studies.lede', 'Each study below was written down and sealed (its SHA-256 recorded, with the time) before its result was computed: the question, the data, the costs, the windows and the pass rule. A rule that failed is listed beside the ones that passed. Figures are Sharpe ratios, percents and counts.')) + '</p>';
    if (!studies.length) {
      h += '<div class="empty">' + esc(T('studies.none', 'No study has been registered yet.')) + '</div>';
    } else {
      h += studies.map((s) => studyHtml(s, T)).join('');
    }
    if (payload.verify) {
      h += '<p class="disc">' + esc(T('studies.verify', 'Recompute every seal from the repository:')) + ' <code>'
        + esc(payload.verify) + '</code> · ' + esc(T('studies.english', 'Study text is shown in English, as it was sealed.')) + '</p>';
    }
    return h;
  }

  /** The panel when the registry could not be read: a sentence, never a list. */
  function unreadable(exception, T) {
    T = T || english;
    let s = T('studies.unreadable', 'The studies registry could not be read just now, so no study is shown. That is a read failure, not an empty record.');
    if (exception) s += ' (' + exception + ')';
    return '<div class="empty st-unread">' + esc(s) + '</div>';
  }

  return { render, unreadable, COLUMNS };
}));
