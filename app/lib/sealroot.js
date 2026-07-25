'use strict';
/**
 * Provable Calls v3 — daily seal roots (Merkle).
 *
 * All seals minted on one UTC day are folded into a single Merkle root.
 * Publishing that one hash (mirror it, tweet it, put it in a chain memo)
 * commits to EVERY call and paper trade sealed that day: any receipt can
 * later prove membership with a log-sized proof, and no call can be
 * back-inserted into a published day without changing the root.
 *
 * Canonical construction (v3 contract — the verify page mirrors it):
 *   leaves  = the day's seals (64-hex), deduped, sorted ascending
 *   parent  = sha256(utf8(leftHex + rightHex))  — hex strings concatenated
 *   odd     = an unpaired last node is promoted unchanged
 *   root of a single leaf = that leaf
 *
 * Pure functions only — the DB-facing service lives in seal_roots.js.
 */

const crypto = require('crypto');

const sha256hex = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

/** Dedupe + ascending sort — the canonical leaf order. */
function canonicalLeaves(seals) {
  return [...new Set((seals || []).filter((s) => /^[0-9a-f]{64}$/.test(String(s))))].sort();
}

/** Merkle root of a day's seals (null when there are none). */
function merkleRoot(seals) {
  let level = canonicalLeaves(seals);
  if (!level.length) return null;
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(i + 1 < level.length ? sha256hex(level[i] + level[i + 1]) : level[i]);
    }
    level = next;
  }
  return level[0];
}

/**
 * Membership proof for one seal: [{ pos: 'L'|'R', hash }] — pos is where the
 * SIBLING sits relative to the running hash. Null when the seal isn't a leaf.
 */
function merkleProof(seals, seal) {
  let level = canonicalLeaves(seals);
  let idx = level.indexOf(String(seal));
  if (idx < 0) return null;
  const proof = [];
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 < level.length) {
        next.push(sha256hex(level[i] + level[i + 1]));
        if (i === idx) proof.push({ pos: 'R', hash: level[i + 1] });
        else if (i + 1 === idx) proof.push({ pos: 'L', hash: level[i] });
      } else {
        next.push(level[i]);   // odd node promoted — no sibling, no proof step
      }
      if (i === idx || i + 1 === idx) idx = next.length - 1;
    }
    level = next;
  }
  return proof;
}

/** Recompute the chain — what the browser does with WebCrypto. */
function verifyProof(seal, proof, root) {
  let h = String(seal);
  for (const step of proof || []) {
    h = step.pos === 'L' ? sha256hex(step.hash + h) : sha256hex(h + step.hash);
  }
  return h === String(root);
}

module.exports = { canonicalLeaves, merkleRoot, merkleProof, verifyProof, sha256hex };
