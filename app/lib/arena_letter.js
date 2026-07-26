'use strict';
/**
 * "Your Arena week" — a private weekly recap for a PAPER trader, composed
 * from their own recorded closes. The operator engine has had its letter for
 * weeks; the people learning in the Arena deserve the same mirror.
 *
 * Built on the letter's parts architecture from birth: every sentence ships
 * as { tid, params } beside its assembled English, so the panel renders it in
 * the reader's language and an unknown tid falls back to English — never
 * blank, never mixed.
 *
 * Honest by construction:
 *   - Composed ONLY from recorded closes and open-position counts — the rows
 *     the history table shows. Deterministic for a completed week, so it is
 *     recomputed on demand and never stored: append-only inputs, same output.
 *   - PRIVATE only. vUSDT figures are allowed here (owner's own account,
 *     virtual funds) and are labeled virtual; nothing from this letter may
 *     reach a public surface.
 *   - A quiet week says so. No closes → the honest flat-week line, not
 *     invented activity; the discipline section only appears when the week
 *     has enough closes to read (same MIN_CLOSES the discipline panel uses).
 *
 * Pure: rows in, letter out. The route feeds it and the tests drive it.
 */

const { computeDiscipline, MIN_CLOSES } = require('./arena_discipline');

const round2 = (v) => Math.round(v * 100) / 100;

/**
 * @param {object} week  { key, start, end } from lib/letter.weekRangeFromKey
 * @param {object} data  { trades: closed rows in [start,end), openCount }
 */
function composeArenaLetter(week, data) {
  const trades = (data && data.trades) || [];
  const openCount = (data && data.openCount) || 0;
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(week.end.getTime() - 86_400_000);

  const pnls = trades.map((t) => parseFloat(t.pnl) || 0);
  const net = round2(pnls.reduce((a, b) => a + b, 0));
  const wins = pnls.filter((p) => p > 0).length;
  const wr = trades.length ? Math.round(wins / trades.length * 100) : null;

  let best = null, worst = null;
  for (const t of trades) {
    const p = parseFloat(t.pnl) || 0;
    if (!best || p > best.pnl) best = { symbol: t.symbol, pnl: p };
    if (!worst || p < worst.pnl) worst = { symbol: t.symbol, pnl: p };
  }

  const sections = [];

  // ── The week ──
  if (!trades.length) {
    sections.push({ title: 'The week', title_tid: 'week', sep: ' ',
      html: 'No paper closes this week — a flat week is a fine week when nothing set up.',
      parts: [{ tid: 'aw_flat', params: {} }] });
  } else {
    sections.push({ title: 'The week', title_tid: 'week', sep: ' ',
      html: `${trades.length} paper close${trades.length === 1 ? '' : 's'}, ${wr}% winners, `
        + `${net >= 0 ? '+' : ''}${net} vUSDT net — virtual, and honestly counted.`,
      parts: [{ tid: 'aw_closes', params: { n: trades.length, wr, net: (net >= 0 ? '+' : '') + net } }] });
  }

  // ── Exit discipline (the week's own read, same rules as the panel) ──
  const disc = computeDiscipline(trades);
  if (disc.enough) {
    sections.push({ title: 'Exit discipline', title_tid: 'discipline', sep: ' ',
      html: `${disc.planned_rate_pct}% of this week's closes were made by a pre-set exit; `
        + (disc.liquidation_rate_pct > 0
          ? `${disc.liquidation_rate_pct}% ended in liquidation.`
          : 'none ended in liquidation.'),
      parts: [
        { tid: 'aw_disc', params: { p: disc.planned_rate_pct } },
        disc.liquidation_rate_pct > 0
          ? { tid: 'aw_disc_liq', params: { p: disc.liquidation_rate_pct } }
          : { tid: 'aw_disc_noliq', params: {} },
      ] });
  }

  // ── Best and worst call (only when both exist and differ) ──
  if (best && worst && trades.length >= 2 && best.pnl !== worst.pnl) {
    sections.push({ title: 'Calls', title_tid: 'calls', sep: ' ',
      html: `Best: ${String(best.symbol)} ${best.pnl >= 0 ? '+' : ''}${round2(best.pnl)} vUSDT. `
        + `Worst: ${String(worst.symbol)} ${worst.pnl >= 0 ? '+' : ''}${round2(worst.pnl)} vUSDT.`,
      parts: [
        { tid: 'aw_best', params: { sym: String(best.symbol), pnl: (best.pnl >= 0 ? '+' : '') + round2(best.pnl) } },
        { tid: 'aw_worst', params: { sym: String(worst.symbol), pnl: (worst.pnl >= 0 ? '+' : '') + round2(worst.pnl) } },
      ] });
  }

  // ── Looking ahead ──
  sections.push({ title: 'Looking ahead', title_tid: 'ahead', sep: ' ',
    html: (openCount
      ? `You carry ${openCount} open paper position${openCount === 1 ? '' : 's'} into the new week. `
      : 'You enter the week flat. ')
      + 'Set the exit before the entry — the discipline read is watching.',
    parts: [
      openCount ? { tid: 'aw_ahead_open', params: { n: openCount } } : { tid: 'aw_ahead_flat', params: {} },
      { tid: 'aw_ahead_line', params: {} },
    ] });

  const headline = !trades.length
    ? 'A flat paper week'
    : `${wr}% winners over ${trades.length} paper close${trades.length === 1 ? '' : 's'}`;
  const headline_part = !trades.length
    ? { tid: 'aw_h_flat', params: {} }
    : { tid: 'aw_h', params: { wr, n: trades.length } };

  return {
    week_key: week.key,
    period: { start: fmtDay(week.start), end: fmtDay(endInclusive) },
    headline, headline_part,
    sections,
    footer: 'Every figure is derived from your recorded paper closes — nothing hand-written. '
      + 'Virtual funds only; this letter is private to you.',
    footer_tid: 'aw_f',
    virtual: true,
  };
}

module.exports = { composeArenaLetter, MIN_CLOSES };
