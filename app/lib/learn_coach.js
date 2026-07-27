'use strict';
/**
 * The diary's coach — nudges computed from the day's OWN closed trades by
 * deterministic rules, never by a model. Each nudge names the fact it came
 * from and (when one fits) points at the Study Room lesson that covers it.
 * "AI help" here is arithmetic on the record: the tutor exists for
 * questions; the coach only states what the day's numbers already say.
 *
 * Honesty rules:
 * - Facts only: every nudge is recomputable from the trades array.
 * - At most two nudges, priority-ordered — a bad day is not a lecture.
 * - No trades → no nudges (silence, not invented encouragement).
 * - The praise line fires only when EVERY close was a planned exit — and it
 *   still carries "today" because one day proves nothing.
 */

const PLANNED = new Set(['tp', 'sl', 'trail']);

/**
 * @param trades [{ reason, ... }] — the diary day's closed trades.
 * @returns [{ id, en, params, lesson }] — whole-line tids for the UI
 *          (`lc.<id>`), English fallback, and a lesson slug or null.
 */
function coachNudges(trades) {
  const t = Array.isArray(trades) ? trades : [];
  if (!t.length) return [];
  const nudges = [];
  const liq = t.filter((x) => x.reason === 'liquidated').length;
  const planned = t.filter((x) => PLANNED.has(x.reason)).length;
  const improvised = t.length - planned - liq;

  if (liq > 0) {
    nudges.push({
      id: 'liq',
      en: '{n} of today’s closes was a liquidation — the exchange chose the exit, at the worst price of the trade.',
      params: { n: liq },
      lesson: 'leverage-and-liquidation',
    });
  }
  if (improvised > planned && improvised > 0) {
    nudges.push({
      id: 'improv',
      en: 'Most of today’s exits ({i} of {t}) were improvised, not the planned ones.',
      params: { i: improvised, t: t.length },
      lesson: 'exit-discipline',
    });
  }
  if (t.length >= 6) {
    nudges.push({
      id: 'many',
      en: '{t} closes in one day — each one carried its own risk decision.',
      params: { t: t.length },
      lesson: 'stops-and-risk',
    });
  }
  if (!nudges.length && planned === t.length) {
    nudges.push({
      id: 'clean',
      en: 'Every exit today was the planned one. One day proves nothing — but this is what discipline looks like on a record.',
      params: {},
      lesson: null,
    });
  }
  return nudges.slice(0, 2);
}

module.exports = { coachNudges, PLANNED };
