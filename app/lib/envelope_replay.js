'use strict';
/**
 * Envelope replay — "what would this policy have refused?"
 *
 * The Intent page has always promised that an envelope PREVIEWS before it
 * binds. This is that preview: the user's own recorded Arena trades, walked
 * in order, with the same deterministic guard that gates a live open applied
 * at each entry.
 *
 * THE COUNTERFACTUAL HONESTY PROBLEM, AND HOW IT IS SOLVED HERE
 *
 * The tempting version of this feature lies. Refusing a past trade changes
 * everything after it — the balance, the drawdown, which slots were free,
 * possibly which setups the user even looked at. Any claim of the form "your
 * account would be up X%" is a fabricated history, and this codebase does not
 * ship fabricated histories.
 *
 * So the replay states only what it can know:
 *   - WHICH of your recorded opens this policy would have refused, and which
 *     rule refused each one (with the numbers that tripped it);
 *   - what those refused trades ACTUALLY returned — a fact about trades that
 *     really happened, not a simulation;
 *   - the balance used at each decision point is the balance the account
 *     ACTUALLY had at that moment, reconstructed from the recorded sequence —
 *     never a counterfactual balance that assumes the refusals happened.
 *
 * That last point is the subtle one and it cuts both ways: because the walk
 * uses the real path, a drawdown rule is evaluated against the drawdown the
 * user really experienced. The report says this plainly rather than implying
 * a clean simulation.
 *
 * What it deliberately does NOT produce: a hypothetical equity curve, a
 * "would have made/saved" headline, or any number that requires assuming the
 * un-taken path. Those are unknowable, so they are omitted.
 */

const { checkOpen, ENFORCEABLE } = require('./envelope_guard');

const round1 = (v) => Math.round(v * 10) / 10;

/** Return-on-margin percent for a recorded trade, or null if unreadable. */
function romPct(t) {
  const m = Number(t.margin);
  const p = Number(t.pnl);
  if (!(m > 0) || !Number.isFinite(p)) return null;   // unreadable ≠ zero
  return round1((p / m) * 100);
}

/**
 * @param rules       compiled envelope rules (intent-model output)
 * @param trades      recorded closed trades, OLDEST FIRST:
 *                    { symbol, direction, margin, leverage, pnl, opened_at }
 * @param startBalance the account's starting vUSDT (arena.START_BALANCE)
 * @returns {{
 *   checked, refused_count, allowed_count, refused, by_rule,
 *   refused_rom_pct_sum, refused_rom_pct_avg, unenforced, sample_note
 * }}
 */
function replay(rules, trades, startBalance) {
  const rs = Array.isArray(rules) ? rules : [];
  const list = Array.isArray(trades) ? trades : [];
  const start = Number(startBalance) > 0 ? Number(startBalance) : 0;

  // The real path: balance as it actually stood when each trade was opened.
  // Walking in order and adding each realized pnl reproduces it exactly,
  // because every recorded trade is closed — no open exposure to model.
  let balance = start;
  const refused = [];
  const byRule = {};
  let unenforced = [];
  let checked = 0;

  for (const t of list) {
    const margin = Number(t.margin);
    const leverage = Number(t.leverage);
    if (!(margin > 0) || !(leverage > 0)) {
      // An unreadable row is skipped, never counted as compliant — a silent
      // pass would flatter the policy.
      balance += Number(t.pnl) || 0;
      continue;
    }
    checked += 1;
    const g = checkOpen(rs, {
      symbol: t.symbol,
      direction: t.direction,
      margin,
      leverage,
      balance,
      startBalance: start,
    });
    if (g.unenforced && g.unenforced.length) unenforced = g.unenforced;
    if (!g.ok) {
      const v = g.violations[0];
      refused.push({
        symbol: String(t.symbol || ''),
        direction: String(t.direction || ''),
        opened_at: t.opened_at || null,
        rule_id: v.id,
        en: v.en,
        params: v.params,
        rom_pct: romPct(t),
      });
      byRule[v.id] = (byRule[v.id] || 0) + 1;
    }
    balance += Number(t.pnl) || 0;
  }

  const roms = refused.map((r) => r.rom_pct).filter((v) => v != null);
  return {
    checked,
    refused_count: refused.length,
    allowed_count: checked - refused.length,
    refused,
    by_rule: byRule,
    // Facts about trades that really happened — never a hypothetical curve.
    refused_rom_pct_sum: roms.length ? round1(roms.reduce((a, b) => a + b, 0)) : null,
    refused_rom_pct_avg: roms.length ? round1(roms.reduce((a, b) => a + b, 0) / roms.length) : null,
    unenforced,
    // Stated on every report, in the payload, so no surface can drop it.
    sample_note: (
      'Replayed against your own recorded opens, using the balance your account '
      + 'actually had at each one. It reports what this policy would have REFUSED '
      + 'and what those trades really returned — never what your account "would '
      + 'have been", because refusing a trade changes everything after it.'
    ),
  };
}

module.exports = { replay, romPct, ENFORCEABLE };
