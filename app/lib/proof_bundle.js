'use strict';
/**
 * A receipt's proof bundle (rcproof/1): every fact a third party needs to
 * check one receipt without asking this server again, in one document.
 *
 *   seal, seal_payload   the receipt, as GET /api/call/:key serves it to the
 *                        same viewer (a call still sealed carries no payload)
 *   merkle               the day, its root and the path from the seal to it
 *   anchors              every chain the day's root was recorded on: the
 *                        chain, how the root is carried there (the method),
 *                        the transaction, and what the chain is independent of
 *   bitcoin              the OpenTimestamps statement, its digest and the
 *                        proof file itself, when the day has one
 *
 * FACTS, NOT A VERDICT. The bundle carries nothing that says "verified": a
 * verifier recomputes the hash and the path itself and asks each chain
 * itself (tools/rcverify.js does, sharing no code with this server). GET
 * /api/verify/:key is this server's own live verdict over the same facts,
 * and it is offered beside the bundle, never inside it.
 *
 * NOTHING NEW IS DISCLOSED. The receipt is the one the receipt route would
 * serve this viewer, redaction included; the anchors and the Bitcoin proof
 * are on /api/roots already. A bundle is a convenience and a format, not a
 * wider door.
 */
const ots = require('./opentimestamps');
const { publicChains } = require('./root_anchor');
const { CONSTRUCTION } = require('./sealroot');

const FORMAT = 'rcproof/1';

const iso = (t) => {
  if (t == null || t === '') return null;
  const d = new Date(t);
  return Number.isFinite(d.getTime()) ? d.toISOString() : null;
};

/** One recorded anchor, described from the chain registry. A chain this build does not know is listed and counts for nothing. */
function anchorEntry(a, byKey) {
  const c = byKey.get(a.chain) || null;
  return {
    chain: a.chain,
    chain_name: c ? c.name : a.chain,
    method: c ? c.method : null,
    tx: a.tx,
    explorer: c && c.explorer_tx ? c.explorer_tx + a.tx : null,
    from: a.from || null,
    block_time: iso(a.block_time),
    independence_group: c ? c.independence_group : null,
    counts_toward_quorum: c ? c.counts_toward_quorum !== false : false,
    ...(c && c.eas ? { eas: c.eas } : {}),
    ...(c && c.reorg_depth ? { reorg_depth: c.reorg_depth } : {}),
  };
}

/**
 * The bundle for `key`, from the receipt the receipt route served (`receipt`,
 * after its viewer's redaction) and the day's OpenTimestamps row (`otsRow`,
 * or null). Pure: everything it says comes from its arguments.
 */
function bundleOf(key, receipt, { otsRow = null, otsUnread = false, chains = publicChains() } = {}) {
  const r = receipt || {};
  const a = r.anchor && typeof r.anchor === 'object' ? r.anchor : null;
  const byKey = new Map((chains || []).map((c) => [c.key, c]));
  const payload = typeof r.seal_payload === 'string' ? r.seal_payload : null;
  const merkle = a && Array.isArray(a.proof) && a.root
    ? { day: a.day, root: a.root, seal_count: a.seal_count == null ? null : Number(a.seal_count), proof: a.proof, construction: CONSTRUCTION }
    : null;
  const bitcoin = merkle && otsRow && otsRow.root === merkle.root && otsRow.proof
    ? {
      statement: ots.statementFor(merkle.day, merkle.root),
      digest: ots.digestFor(merkle.day, merkle.root).toString('hex'),
      ots_proof_base64: String(otsRow.proof),
      // What this server last read: a hint for a verifier, never the check.
      bitcoin_height: otsRow.bitcoin_height == null ? null : Number(otsRow.bitcoin_height),
    }
    : null;
  return {
    format: FORMAT,
    key: String(key),
    kind: r.kind || null,
    seal: r.seal || null,
    seal_payload: payload,
    // A receipt still sealed (a live call, a round not yet open) carries no
    // payload: its hash cannot be checked yet, its place in the root can.
    sealed: payload === null,
    sealed_at: iso(r.sealed_at),
    merkle,
    // No path is not "not anchored": the seal's day may still be open, the
    // seal may not be in the day's committed set, or the root could not be
    // read. Any of them leaves the hash checkable and the anchoring not.
    ...(merkle ? {} : { merkle_note: 'No Merkle path: the seal\'s UTC day is still open, the seal is not in that day\'s committed set, or the root could not be read.' }),
    anchors: merkle && Array.isArray(a.anchors) ? a.anchors.map((x) => anchorEntry(x, byKey)) : [],
    bitcoin,
    // The day's Bitcoin proof could not be read: absent here is unread, not "none".
    ...(merkle && otsUnread ? { bitcoin_unread: true } : {}),
    checks: [
      'sha256 over the UTF-8 bytes of seal_payload, verbatim, equals seal (when the payload is revealed).',
      'Replaying merkle.proof from seal (L: sha256(hash + current), R: sha256(current + hash), over the hex strings) reaches merkle.root.',
      'Each anchor\'s transaction, read from its own chain, carries the root as its method says (docs/PROVABLE_CALLS_SPEC.md §4.2).',
      'The OpenTimestamps proof, checked against Bitcoin block headers (`ots verify`), timestamps the statement.',
      'A day is multi-anchored when chains in two independence groups hold it: proof of when, never of how good.',
    ],
    verifier: 'tools/rcverify.js in the repository: no dependencies, shares no code with this server.',
  };
}

module.exports = { FORMAT, CONSTRUCTION, bundleOf, anchorEntry };
