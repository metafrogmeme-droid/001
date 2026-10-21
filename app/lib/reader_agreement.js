'use strict';
/**
 * Several readers' answers about one anchor, as one verdict.
 *
 * A reader is an RPC endpoint or an explorer API. Each can be down, lag the
 * chain, or lie: an attacker who controls the one node a verifier trusts can
 * make any transaction look anchored. "Unreadable is never a verdict" keeps
 * silence from turning a day green; it does nothing against a reader that
 * answers wrongly. So a verdict here needs `required` readers giving the
 * same one, and none contradicting it; anything less is `unknown`.
 *
 * Each answer is { reader, status, reason?, same?, ...fields }:
 *   status 'verified' | 'mismatch' | 'unknown' (answered, not yet a verdict:
 *   too shallow, not mined) | 'unread' (did not answer);
 *   `same`: what verified readers must agree on besides the verdict (the
 *   block hash), so two readers on two sides of a reorg are not agreement.
 *
 * → { status: 'verified', ...fields of the first verified, readers }
 * → { status: 'mismatch', reason, readers }
 * → { status: 'unknown', reason, disagreement?: true }
 */

/** The number of readers that must agree when that many are configured. */
const READERS_REQUIRED = 2;

/** How many of `available` readers must agree: two, or every one when fewer are configured. */
function requiredOf(available) {
  return Math.max(1, Math.min(READERS_REQUIRED, available));
}

function agree(answers, required, what = 'the readers') {
  const verified = answers.filter((a) => a.status === 'verified');
  const mismatched = answers.filter((a) => a.status === 'mismatch');
  if (verified.length && mismatched.length) {
    return { status: 'unknown', reason: `${what} disagree`, disagreement: true };
  }
  if (new Set(verified.map((a) => a.same)).size > 1) {
    return { status: 'unknown', reason: `${what} place it in different blocks`, disagreement: true };
  }
  if (verified.length >= required) {
    const { reader, same, status, reason, ...fields } = verified[0];
    return { status: 'verified', ...fields, readers: verified.map((a) => a.reader) };
  }
  if (mismatched.length >= required) {
    return { status: 'mismatch', reason: mismatched[0].reason, readers: mismatched.map((a) => a.reader) };
  }
  // Not enough agreement: say why, most useful first. A reader that answered
  // "not deep enough yet" names the wait; then the readers that did not answer.
  const pending = answers.find((a) => a.status === 'unknown');
  if (pending) return { status: 'unknown', reason: pending.reason };
  const unread = answers.filter((a) => a.status === 'unread');
  const agreed = Math.max(verified.length, mismatched.length);
  const why = unread.length ? unread.map((a) => a.reason).join('; ') : 'too few readers answered';
  return { status: 'unknown', reason: agreed ? `${agreed} of the ${required} readers needed agree: ${why}` : why };
}

/** A reader's failure, by class: an exception's message can carry a URL, and a configured URL can carry a key. */
function unread(reader, err) {
  return { reader, status: 'unread', reason: `${reader} could not be read (${err && err.name ? err.name : 'Error'})` };
}

module.exports = { agree, requiredOf, unread, READERS_REQUIRED };
