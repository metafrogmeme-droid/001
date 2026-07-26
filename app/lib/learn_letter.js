'use strict';
/**
 * "Your study week" — the Study Room's weekly reflection letter: the diary
 * habit and the trading week, read together. You write daily; the room
 * reflects weekly.
 *
 * Built on the letters' parts architecture: every sentence ships as
 * { tid, params } beside assembled English, so the panel renders it in the
 * reader's language and an unknown tid falls back whole — never blank,
 * never mixed.
 *
 * Honest by construction:
 *   - Composed ONLY from recorded facts: diary days and word counts, closed
 *     trades, and the same discipline read the Arena panel uses. Nothing
 *     coaching-shaped is invented — the letter states what happened.
 *   - A blank week says so ("the page was blank, and that is recorded
 *     honestly") — no fabricated encouragement.
 *   - Discipline appears only past MIN_CLOSES, the same line the panel
 *     draws; below it, "too few closes to read" is a fact, not a failing.
 *   - PRIVATE only, recomputed on demand from append-only inputs, never
 *     stored.
 */

const { computeDiscipline, MIN_CLOSES } = require('./arena_discipline');

/**
 * @param {object} week { key, start, end } (lib/letter.weekRangeFromKey shape)
 * @param {object} data { entries: [{day, body}] in week, trades: closes in week }
 */
function composeStudyLetter(week, data) {
  const entries = (data && data.entries) || [];
  const trades = (data && data.trades) || [];
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(week.end.getTime() - 86_400_000);

  const parts = [];
  const add = (tid, en, params) => parts.push({ tid, en, params: params || {} });

  add('lw.h', 'Your study week, {start} → {end}.',
    { start: fmtDay(week.start), end: fmtDay(endInclusive) });

  // ── the writing ───────────────────────────────────────────────────────────
  const days = new Set(entries.map((e) => e.day)).size;
  if (days === 0) {
    add('lw.j_none', 'No diary entries this week — the page was blank, and that is recorded honestly.');
  } else {
    add('lw.j_days', 'You wrote on {n} of 7 days.', { n: days });
    // A "word" carries at least one letter or digit — a bare dash is not
    // reflection, and counting it would overstate the habit.
    const words = entries.reduce((a, e) =>
      a + String(e.body || '').split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length, 0);
    if (words > 0) add('lw.j_words', '{w} words of your own reflection, kept.', { w: words });
  }

  // ── the trading ───────────────────────────────────────────────────────────
  if (trades.length === 0) {
    add('lw.t_none', 'No paper trades closed this week.');
  } else {
    add('lw.t_closes', '{n} paper trade(s) closed.', { n: trades.length });
    const disc = computeDiscipline(trades);
    if (disc.enough) {
      add('lw.d_rate', 'Planned exits (TP / SL / trail) ended {p}% of them.',
        { p: disc.planned_rate_pct });
    } else {
      add('lw.d_thin', 'Too few closes to read exit discipline — a fact, not a failing (it needs {m}).',
        { m: MIN_CLOSES });
    }
  }

  // ── the pairing — only when both halves actually happened ────────────────
  if (days > 0 && trades.length > 0) {
    add('lw.pair', 'A week with both trades and words about them — every close you wrote about, you learned from twice.');
  }

  add('lw.f', 'The Study Letter is private, recomputed from your own records, and never stored. Education, not investment advice.');

  return {
    week: week.key,
    parts,
    text: parts.map((p) => Object.entries(p.params).reduce(
      (s, [k, v]) => s.split('{' + k + '}').join(String(v)), p.en)).join(' '),
    private: true,
  };
}

module.exports = { composeStudyLetter };
