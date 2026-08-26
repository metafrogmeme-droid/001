'use strict';
/**
 * The chain from an agent claim to a Base block timestamp — or an honest
 * account of exactly where it stops.
 *
 *   sha256(utf8(seal_payload)) === seal     the claim is intact
 *   seal ∈ day D's committed leaf set       Merkle proof
 *   D's root is calldata in a Base tx       anchor_tx
 *   → the claim existed by that block time
 *
 * WHY THIS IS ITS OWN MODULE
 *
 * It lived inside routes/public_agent_identity.js and destructured
 * `anchorFor`/`rootForDay` at module load, which meant no test could drive its
 * branches — the references were captured before any mock could reach them.
 * The branches are the entire point of the function, so a version of it that
 * cannot be driven is a version whose behaviour nothing checks.
 *
 * WHY THE ABSENCE OF AN ANCHOR IS FOUR ANSWERS
 *
 * `anchorFor()` returns null for unrelated reasons and cannot distinguish them
 * from the outside. One of them — a seal missing from a day's COMMITTED leaf
 * set — is the signature of a row inserted after the day was published, and it
 * should alarm a reader. The others are routine (the day is still open; the
 * root has not been computed; the day is not anchored yet). Rendering them all
 * as "not anchored" would file the alarming case under the reassuring ones.
 */

/** UTC day, matching lib/seal_roots.dayOf. */
const dayOf = (ts) => new Date(ts).toISOString().slice(0, 10);

/**
 * @param agent  { seal, claimed_at }
 * @param deps   { anchorFor, rootForDay, now } — injected so every branch is
 *               drivable; defaults to the real seal-roots service.
 */
async function chainFor(agent, deps = {}) {
  const roots = require('./seal_roots');
  const anchorFor = deps.anchorFor || roots.anchorFor;
  const rootForDay = deps.rootForDay || roots.rootForDay;
  const now = deps.now || (() => Date.now());

  try {
    const day = dayOf(agent.claimed_at);
    const anchor = await anchorFor(agent.seal, agent.claimed_at);
    if (anchor) {
      return {
        status: anchor.anchor_tx ? 'anchored' : 'rooted',
        day: anchor.day,
        root: anchor.root,
        seal_count: anchor.seal_count,
        proof: anchor.proof,
        anchor_tx: anchor.anchor_tx || null,
        anchored_at: anchor.anchored_at || null,
        verify_url: `/api/roots/verify/${anchor.day}`,
        note: anchor.anchor_tx
          ? 'The day\'s root is calldata in a Base transaction; its block time is '
            + 'the independent upper bound on when this claim was made.'
          : 'The day\'s root is computed and committed, but the day has not been '
            + 'anchored on-chain yet — so the claim date still rests on our clock.',
      };
    }

    if (day >= dayOf(now())) {
      return { status: 'day_open', day,
        note: 'Claimed today. Roots are computed only for COMPLETED UTC days — '
          + 'committing to a day still in progress would be a lie.' };
    }

    const row = await rootForDay(day);
    if (!row) {
      return { status: 'no_root', day,
        note: 'No root exists for that day yet. It is computed lazily the first '
          + 'time the day is asked for.' };
    }

    // The day HAS a committed leaf set and this seal is not in it.
    return { status: 'not_in_root', day, root: row.root,
      note: 'This claim\'s seal is NOT in that day\'s committed leaf set. A claim '
        + 'sealed on the day cannot be missing from it — treat this record as '
        + 'unproven.' };
  } catch (err) {
    console.error('[agent_chain] read failed:', err.stack || err.message);
    return { status: 'unknown',
      note: 'The seal roots could not be read, so nothing is claimed either way.' };
  }
}

/** Statuses that are a settled answer and therefore safe to cache. */
const SETTLED = new Set(['anchored', 'rooted', 'no_root', 'not_in_root', 'day_open']);

module.exports = { chainFor, dayOf, SETTLED };
