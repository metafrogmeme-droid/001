'use strict';
/**
 * A day's anchors, checked live: the one reading /api/roots/verify,
 * /api/roots/ots, /api/roots/quorum and the anchor watch (lib/anchor_watch.js)
 * all answer from, so a page and the operator's diagnostics cannot tell two
 * stories about the same day.
 *
 * A verdict is kept (five minutes, or the chain's `verify_cache_ms`; six hours
 * for a Bitcoin proof found in a block). An unknown is never kept: a transient
 * failure must not be frozen into fact.
 */
const { anchorsFor } = require('./seal_roots');
const { verifyAnchor, chainInfo } = require('./root_anchor');
const rootOts = require('./root_ots');
const ots = require('./opentimestamps');
const { quorumOf, BITCOIN_WITNESS } = require('./anchor_quorum');

const verifyCache = new Map();   // `${day}|${chain}` -> { at, body }
const otsCache = new Map();      // day -> { at, body }, verified answers only
const OTS_KEEP_MS = 6 * 3600 * 1000;

/** The live check of a day's recorded anchor `have` on `chain`: the body /verify/:day answers. */
async function anchorCheck(row, chain, have) {
  const key = `${row.day}|${chain.key}`;
  const hit = verifyCache.get(key);
  if (hit && Date.now() - hit.at < (chain.verify_cache_ms || 5 * 60000)) return hit.body;
  const v = await verifyAnchor(have.tx, row.day, row.root, undefined, chain.key);
  const body = { day: row.day, root: row.root, chain: chain.key, chain_name: chain.name,
    anchor_tx: have.tx, explorer: chain.explorer_tx + have.tx, method: chain.method || 'calldata',
    status: v.status, block_time: v.block_time || null,
    from: v.from || null, reason: v.reason || null,
    // On a chain with a reorg depth: whether its `finalized` block has
    // passed the anchor (null when that could not be read).
    ...(v.final !== undefined ? { final: v.final } : {}),
    // Which readers agreed (lib/reader_agreement.js), or that they did not.
    ...(v.readers ? { readers: v.readers } : {}),
    ...(v.disagreement ? { disagreement: true } : {}) };
  if (v.status === 'verified') verifyCache.set(key, { at: Date.now(), body });
  return body;
}

/** The live Bitcoin check of a day's root: the body /ots/:day answers. */
async function bitcoinCheck(row) {
  const base = { day: row.day, root: row.root, statement: ots.statementFor(row.day, row.root),
    digest: ots.digestFor(row.day, row.root).toString('hex'),
    proof_url: `/api/roots/ots/${row.day}/proof`, statement_url: `/api/roots/ots/${row.day}/statement` };
  const hit = otsCache.get(row.day);
  if (hit && Date.now() - hit.at < OTS_KEEP_MS) return hit.body;
  const rec = await rootOts.rowFor(row.day);
  if (!rec) return { ...base, status: 'not_submitted', proof_url: null };
  if (rec.root !== row.root) return { ...base, status: 'mismatch', reason: 'the stored proof is for another root' };
  const v = await rootOts.checkProof(rec);
  const body = { ...base, status: v.status, bitcoin_height: v.height || null, block_time: v.block_time || null,
    reason: v.reason || null, calendars: String(rec.calendars || '').split(',').filter(Boolean),
    submitted_at: rec.submitted_at ? new Date(rec.submitted_at).toISOString() : null,
    ...(v.readers ? { readers: v.readers } : {}), ...(v.disagreement ? { disagreement: true } : {}) };
  if (v.status === 'verified') otsCache.set(row.day, { at: Date.now(), body });
  return body;
}

/**
 * The day's Anchor Quorum (lib/anchor_quorum.js) over every recorded anchor
 * and the Bitcoin timestamp, each checked live. Throws when the day's anchors
 * cannot be read: a quorum over an unreadable table would count witnesses it
 * could not see as absent.
 */
async function dayQuorum(row) {
  const recorded = await anchorsFor(row.day, row, { strict: true });
  const anchors = await Promise.all(recorded.map(async (a) => {
    const chain = chainInfo(a.chain);
    if (!chain) return { day: row.day, chain: a.chain, anchor_tx: a.tx, status: 'unknown', reason: 'a chain this build does not know' };
    return { ...(await anchorCheck(row, chain, a)),
      independence_group: chain.independence_group, counts_toward_quorum: chain.counts_toward_quorum !== false };
  }));
  const bitcoin = await bitcoinCheck(row);
  const q = quorumOf([
    ...anchors.map((a) => ({ key: a.chain, group: a.independence_group, counts: !!a.counts_toward_quorum, status: a.status })),
    { ...BITCOIN_WITNESS, status: bitcoin.status },
  ]);
  return { day: row.day, root: row.root, ...q, anchors,
    bitcoin: { ...bitcoin, independence_group: BITCOIN_WITNESS.group, counts_toward_quorum: true } };
}

function _resetCaches() { verifyCache.clear(); otsCache.clear(); }

module.exports = { anchorCheck, bitcoinCheck, dayQuorum, _resetCaches };
