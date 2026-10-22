'use strict';
/**
 * Daily Duel flags and reviews (docs/design/VIRTUAL_STAKES_DESIGN.md §3.5),
 * the rules only: pure, like lib/duel.js and lib/duel_chips.js.
 *
 * A settlement is one player's (each call has its own entry and its own
 * settle instant), so a FLAG is the player saying "my settlement is wrong",
 * once, within the check window after it was written. A flag costs nothing
 * and is not a bond: a bond without real money is a number nobody can lose.
 * A flagged settlement's chips are held while the window runs, as a disputed
 * one's are (lib/duel_chips.js).
 *
 * A REVIEW is the operator's decision on a flagged or disputed settlement,
 * written as a receipt of its own (Provable Calls v9, `duel_review`) that
 * names the settlement and never rewrites it:
 *
 *   stands      the verdict was right
 *   push        no result: marks cleared, chips returned
 *   corrected   the market's answer was another one, named in the review;
 *               the call is scored again at it, marks and chips alike
 *
 * lib/duel.js verdictOf applies the decision wherever a stored call is
 * scored, so the card, the record, the board and the stack agree.
 */
const duel = require('./duel');
const { HOLD_MS } = require('./duel_chips');

/** How long after a settlement its player may flag it: the chips' check window. */
const WINDOW_MS = HOLD_MS;
/** How long after a settlement it may still be reviewed. */
const REVIEW_MAX_MS = 7 * 86400000;
const DECISIONS = Object.freeze(['stands', 'push', 'corrected']);
/** The longest evidence note a review receipt carries. */
const EVIDENCE_MAX = 500;

function settledMs(pick) {
  const t = Date.parse(pick && pick.settled_at);
  return Number.isFinite(t) ? t : NaN;
}

/** May the player flag this settlement now? → { ok: true } or { ok: false, status, reason, error }. */
function canFlag(pick, now = new Date()) {
  const no = (status, reason, error) => ({ ok: false, status, reason, error });
  if (!pick) return no(404, 'no_call', 'You made no call on that round.');
  if (pick.settle_state !== 'settled') {
    return no(409, 'not_settled', pick.settle_state === 'unresolved'
      ? 'That call was never priced, so there is no result to flag.'
      : 'That call has not settled yet.');
  }
  if (pick.review_state) return no(409, 'reviewed', 'That settlement has been reviewed already.');
  if (pick.flagged_at) return no(409, 'flagged', 'You have flagged that settlement already.');
  const at = settledMs(pick);
  if (!Number.isFinite(at)) return no(409, 'not_settled', 'That call has no readable settlement time.');
  if (new Date(now).getTime() > at + WINDOW_MS) {
    return no(409, 'window_closed', 'The window to flag that settlement has closed.');
  }
  return { ok: true };
}

/** Whether a settlement is waiting for the operator: flagged or disputed, settled, not reviewed. */
function needsReview(pick) {
  if (!pick || pick.settle_state !== 'settled' || pick.review_state) return false;
  const disputed = pick.settle_disputed != null && Boolean(Number(pick.settle_disputed));
  return Boolean(pick.flagged_at) || disputed;
}

/** Evidence as a receipt carries it: one line of plain text, bounded; '' when none. */
function evidenceOf(raw) {
  return String(raw == null ? '' : raw).replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim()
    .slice(0, EVIDENCE_MAX);
}

/**
 * May the operator record this decision? → { ok: true, outcome } or a refusal.
 * `outcome` is the corrected outcome for 'corrected' and null otherwise.
 */
function checkDecision(round, pick, decision, rawOutcome, evidence, now = new Date()) {
  const no = (status, reason, error) => ({ ok: false, status, reason, error });
  if (!pick) return no(404, 'no_call', 'No such call.');
  if (!round) return no(404, 'no_round', 'That call\'s round could not be read.');
  if (!needsReview(pick)) {
    return no(409, 'not_queued', pick.review_state ? 'That settlement has been reviewed already.'
      : 'Only a flagged or disputed settlement is reviewed.');
  }
  if (DECISIONS.indexOf(decision) < 0) return no(400, 'decision', `Decide ${DECISIONS.join(', ')}.`);
  if (!evidence) return no(400, 'evidence', 'Say what was checked: the review receipt carries it.');
  const at = settledMs(pick);
  if (Number.isFinite(at) && new Date(now).getTime() > at + REVIEW_MAX_MS) {
    return no(409, 'too_late', 'That settlement is too old to review.');
  }
  const kind = duel.kindOf(round);
  if (decision !== 'corrected') {
    if (rawOutcome != null && rawOutcome !== '') return no(400, 'outcome', 'Only a correction names an outcome.');
    return { ok: true, outcome: null };
  }
  const outcome = String(rawOutcome == null ? '' : rawOutcome).trim().toLowerCase();
  const allowed = kind ? duel.KINDS[kind].outcomes : [];
  if (allowed.indexOf(outcome) < 0) return no(400, 'outcome', `A correction names one of: ${allowed.join(', ')}.`);
  if (outcome === duel.callOutcome(round, pick)) {
    return no(400, 'outcome', 'That is the outcome the settlement already gave: decide "stands" instead.');
  }
  return { ok: true, outcome };
}

module.exports = { WINDOW_MS, REVIEW_MAX_MS, DECISIONS, EVIDENCE_MAX, canFlag, needsReview, evidenceOf, checkDecision };
