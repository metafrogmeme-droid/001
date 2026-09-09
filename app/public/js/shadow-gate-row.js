/*
 * RCShadowGates — the shadow book's rows, where a colour is a claim about edge.
 *
 *   classify(gate)  -> { tone, established, netR, avgR, n, note }
 *   buildRows(gates) -> html
 *
 * The shadow book paper-trades every idea a risk gate REJECTS, so each gate
 * carries a running price tag: blocked trades that net POSITIVE R mean the
 * gate is eating edge, negative mean it saved money. The panel rendering that
 * did it like this:
 *
 *     <b class="num ${g.net_r > 0 ? 'neg' : 'pos'}">
 *
 * Three things wrong with one expression, and all of them paint.
 *
 *   * NO THRESHOLD AT ALL. `net_r` is a TOTAL. A gate at +4.1R over 97 blocked
 *     trades is +0.042R each — noise — and rendered exactly as red as a gate at
 *     +4.1R over 4. The Telegram scoreboard at least required 0.5R; this
 *     required nothing.
 *   * ZERO IS GREEN. The ternary's else-branch catches `net_r === 0`, so a gate
 *     with a measured break-even reads as "saved money".
 *   * SO IS MISSING. `undefined > 0` is false, so a row whose net_r never
 *     arrived takes the same green — the exact shape the table in CLAUDE.md
 *     lists as "unreadable WON".
 *
 * THE VERDICT IS NOT COMPUTED HERE. `verdict` comes off
 * `bot/core/shadow_book.py::gate_report` — a 95% interval on R per blocked
 * trade (`mean_r_interval`) plus a sample floor (`MIN_GATE_TRADES`) — and is
 * read, not recomputed, for the reason winrate-bar.js states about MIN_RATED:
 * a second copy of a threshold is a second answer, and then the dashboard and
 * the bot disagree about what counts as evidence.
 *
 * Below the bar the figures are still SHOWN — hiding a real measurement is its
 * own dishonesty — with no colour and a note saying the reading is not
 * established.
 *
 * Exposed as window.RCShadowGates; module.exports in node so it can be tested.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCShadowGates = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Rows rendered in the panel. */
  var MAX_ROWS = 8;

  /** Gate label width; the server already buckets to a category. */
  var MAX_LABEL = 30;

  /** The only verdicts that may carry a colour, and which one. */
  var TONE = { eating_edge: 'neg', saving: 'pos' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : Number(v);
    return (typeof n === 'number' && isFinite(n)) ? n : null;
  }

  function signed(n, d) {
    if (n === null) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(d === undefined ? 2 : d);
  }

  /**
   * What may honestly be said about one gate row.
   *
   * `tone` is '' for every row the server did not establish, INCLUDING a row
   * carrying no verdict field at all. An older cached scan, a hand-written
   * fixture and a genuinely undecided gate all land in the same place, which
   * is the point: none of them is evidence.
   */
  function classify(gate) {
    var g = gate || {};
    var verdict = typeof g.verdict === 'string' ? g.verdict : null;
    var tone = TONE[verdict] || '';
    var note = '';
    if (!tone) {
      note = verdict === 'undistinguished'
        ? 'not distinguishable from noise'
        : 'not established';
    }
    return {
      label: String(g.gate == null ? '' : g.gate).slice(0, MAX_LABEL),
      netR: num(g.net_r),
      avgR: num(g.avg_r),
      lowerR: num(g.lower_r),
      upperR: num(g.upper_r),
      n: num(g.n),
      verdict: verdict,
      tone: tone,
      established: !!tone,
      note: note,
    };
  }

  function rowHtml(c) {
    var count = c.n === null ? '—' : String(c.n);
    // The per-trade figure travels with the total ALWAYS. A net beside a count
    // that the reader has to divide is how +4.1R over 97 read as a finding.
    var per = c.avgR === null ? '' : ' ' + signed(c.avgR, 2) + 'R/tr';
    return '<div class="kv-row">'
      + '<span class="small" style="font-family:var(--font-data)">' + esc(c.label)
      + (c.note ? ' <span class="muted">· ' + esc(c.note) + '</span>' : '')
      + '</span>'
      + '<b class="num ' + (c.tone || 'muted') + '">'
      + (c.netR === null ? '—' : signed(c.netR, 1) + 'R')
      + '<span class="muted">' + per + ' ×' + esc(count) + '</span>'
      + '</b></div>';
  }

  function buildRows(gates, opts) {
    opts = opts || {};
    var list = Array.isArray(gates) ? gates : [];
    if (!list.length) return '';
    return list.slice(0, opts.max || MAX_ROWS).map(classify).map(rowHtml).join('');
  }

  /** How many of these gates carry an established verdict. For a caption. */
  function establishedCount(gates) {
    return (Array.isArray(gates) ? gates : [])
      .map(classify).filter(function (c) { return c.established; }).length;
  }

  return {
    buildRows: buildRows, classify: classify,
    establishedCount: establishedCount,
    MAX_ROWS: MAX_ROWS, MAX_LABEL: MAX_LABEL, TONE: TONE,
  };
}));
