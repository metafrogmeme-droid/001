'use strict';
/**
 * Authority Envelope enforcement — the Guardian principle made real on the
 * paper surface. The Intent Compiler turns words into typed rules; this
 * guard makes those rules REFUSE non-compliant Arena opens, deterministic
 * and server-side: the AI proposes, deterministic controls authorize.
 *
 * Honesty rules:
 * - Only rules this surface can truly enforce are enforced; everything else
 *   is returned in `unenforced` and SHOWN to the user as such — an envelope
 *   must never claim more protection than it delivers.
 * - Every refusal names its rule with a whole-line tid (`ev.<id>`) and the
 *   numbers that tripped it — recomputable from the order itself.
 * - No rules → ok (an unarmed account trades exactly as before).
 */

// Rule ids this surface enforces on an open. Confidence/approval/liquidity
// tiers need signals or human flows that a manual paper open doesn't carry.
const ENFORCEABLE = new Set(['size_max', 'dir_long', 'loss_dd', 'scope_majors', 'lev_max']);

/**
 * @param rules  compiled envelope rules (intent-model output)
 * @param ctx    { symbol, direction, margin, leverage, balance, startBalance }
 * @returns { ok, violations: [{ id, en, params }], unenforced: [id] }
 */
function checkOpen(rules, ctx) {
  const rs = Array.isArray(rules) ? rules : [];
  const violations = [];
  const unenforced = [];
  const base = String(ctx.symbol || '').replace(/USDT$/i, '').toUpperCase();

  for (const r of rs) {
    if (!ENFORCEABLE.has(r.id)) { unenforced.push(r.id); continue; }
    if (r.id === 'size_max') {
      const pct = ctx.balance > 0 ? (Number(ctx.margin) / Number(ctx.balance)) * 100 : 100;
      if (pct > Number(r.value) + 1e-9) {
        violations.push({ id: 'size_max',
          en: 'Envelope: this open is {pct}% of the book — your rule caps a position at {max}%.',
          params: { pct: Math.round(pct * 10) / 10, max: r.value } });
      }
    } else if (r.id === 'dir_long') {
      if (String(ctx.direction).toUpperCase() === 'SHORT') {
        violations.push({ id: 'dir_long',
          en: 'Envelope: your rule is long-only — shorts are refused.', params: {} });
      }
    } else if (r.id === 'loss_dd') {
      const dd = ctx.startBalance > 0
        ? ((ctx.startBalance - ctx.balance) / ctx.startBalance) * 100 : 0;
      if (dd >= Number(r.value)) {
        violations.push({ id: 'loss_dd',
          en: 'Envelope: the book is down {dd}% — your rule halts new opens at {max}% drawdown.',
          params: { dd: Math.round(dd * 10) / 10, max: r.value } });
      }
    } else if (r.id === 'scope_majors') {
      const allowed = Array.isArray(r.value) ? r.value.map((s) => String(s).toUpperCase()) : [];
      if (allowed.length && !allowed.includes(base)) {
        violations.push({ id: 'scope_majors',
          en: 'Envelope: {sym} is outside your majors-only scope ({list}).',
          params: { sym: base, list: allowed.join(', ') } });
      }
    } else if (r.id === 'lev_max') {
      if (Number(ctx.leverage) > Number(r.value)) {
        violations.push({ id: 'lev_max',
          en: 'Envelope: {lev}× exceeds your leverage cap of {max}×.',
          params: { lev: ctx.leverage, max: r.value } });
      }
    }
  }
  return { ok: violations.length === 0, violations, unenforced };
}

module.exports = { checkOpen, ENFORCEABLE };
