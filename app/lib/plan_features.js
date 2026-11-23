'use strict';
/**
 * What a plan includes, for the plan notice email, read from the bot at send
 * time: the same answer the dashboard's plan card draws (GET /api/tier, the
 * bot gateway's /tier, public/js/plan-card-model.js), never a list written
 * here. The bot is the one tier authority.
 *
 *   linesFor('web:<id>', 'pro') → { read: true, lines: [...] }
 *                               | { read: false, reason, lines: [fallback] }
 *
 * Three readings, never collapsed:
 *  - read, enforced: the tier's own lines, as the card prints them.
 *  - read, NOT enforced: every member can already use every command, so the
 *    command lines are left out (listing them would claim the plan unlocked
 *    them) and the card's own sentence saying the ladder is not enforced is
 *    added. The daily chat questions and the operator-funded model on
 *    /analyze stay: the bot's answer carries them per tier.
 *  - unreadable (no gateway, a failed or late call, an answer without the
 *    tier): one fallback line pointing at the dashboard. An unread ladder is
 *    not an empty plan.
 *
 * The call is short (3 s) because it runs inside the bot's own tier-notice
 * request, which waits 15 s in all and also has to wait for the SMTP send.
 * A side effect to know: the gateway registers the web:<id> member and
 * refreshes its last-seen time, as any gateway read for a member does.
 */
const PlanCard = require('../public/js/plan-card-model.js');
const { COPY } = require('./email_templates');

const TIMEOUT_MS = 3000;

function _fallback(reason) {
  return { read: false, reason, lines: [COPY.FEATURES_UNREADABLE] };
}

async function linesFor(memberKey, tier, { gateway = require('./gateway'), timeoutMs = TIMEOUT_MS } = {}) {
  if (!gateway.isConfigured()) return _fallback('gateway_not_configured');
  let r;
  try {
    r = await gateway.getGateway(`/tier?telegram_id=${encodeURIComponent(memberKey)}`, timeoutMs);
  } catch (e) {
    return _fallback('gateway_unavailable');
  }
  if (!r || r.status < 200 || r.status >= 300 || !PlanCard.readable(r.data)) {
    return _fallback('unreadable');
  }
  const ladder = r.data.ladder;
  const enforced = ladder.enforced === true;
  const lines = PlanCard.tierLines(ladder, tier, { commands: enforced });
  if (!lines || !lines.length) return _fallback('tier_not_on_ladder');
  if (!enforced) lines.push(`${PlanCard.NOT_ENFORCED}.`);
  return { read: true, enforced, lines };
}

module.exports = { linesFor, TIMEOUT_MS };
