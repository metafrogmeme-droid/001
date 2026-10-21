'use strict';
/**
 * A day's Anchor Quorum: how many INDEPENDENT chains hold its seal root, by
 * live check.
 *
 * One chain that holds a root is one witness, and a witness can be wrong:
 * the chain can reorg, its readers can lie (lib/reader_agreement.js covers
 * the readers, not the chain). A day is "multi-anchored" when at least `k`
 * witnesses in different independence groups verify it live. A group is
 * what a witness is independent OF: a calldata anchor and an EAS stamp on
 * Base are one witness, and so would be two Ethereum L2s; a testnet is shown
 * and never counted. Each chain's group is in the registry
 * (lib/root_anchor.js `independence_group`); Bitcoin, through
 * OpenTimestamps, is outside it and is its own group.
 *
 * The quorum says when the root existed, never how the calls in it did:
 * time-proven, not performance.
 *
 * Three values, never two:
 *   'multi_anchored'  k groups verified live
 *   'short'           fewer, and no witness that could not be read could
 *                     make up the difference (pending ones are listed)
 *   'unknown'         fewer, and a witness that could not be read might
 */

/** The independent witnesses a day needs before it is multi-anchored. */
const QUORUM_K = 2;

/** Bitcoin, through OpenTimestamps: not in the chain registry, its own group. */
const BITCOIN_WITNESS = Object.freeze({ key: 'bitcoin', group: 'bitcoin', counts: true });

/** What the quorum means, said beside it wherever it is shown. */
const MEANING = 'time-proven, not performance';

/**
 * witnesses: [{ key, group, counts, status }] with status from the live
 * checks ('verified' | 'mismatch' | 'unknown' | 'pending' | anything else:
 * nothing there to check).
 */
function quorumOf(witnesses, k = QUORUM_K) {
  const counting = (witnesses || []).filter((w) => w && w.counts && w.group);
  const groups = [...new Set(counting.filter((w) => w.status === 'verified').map((w) => w.group))].sort();
  // A witness that could not be read matters only in a group not already verified.
  const open = counting.filter((w) => w.status === 'unknown' && !groups.includes(w.group));
  const pending = counting.filter((w) => w.status === 'pending' && !groups.includes(w.group));
  const mismatched = (witnesses || []).filter((w) => w && w.status === 'mismatch').map((w) => w.key);
  let status = 'short';
  if (groups.length >= k) status = 'multi_anchored';
  else if (open.length && groups.length + new Set(open.map((w) => w.group)).size >= k) status = 'unknown';
  return {
    status, k, groups,
    unreadable: open.map((w) => w.key),
    pending: pending.map((w) => w.key),
    mismatched,
    meaning: MEANING,
  };
}

module.exports = { quorumOf, QUORUM_K, BITCOIN_WITNESS, MEANING };
