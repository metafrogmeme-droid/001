'use strict';
/**
 * Daily seal roots — the DB-facing service around lib/sealroot.js.
 *
 * A root is computed lazily the first time a COMPLETED UTC day is asked for,
 * then stored immutably in seal_roots (seals carry their sealed_at from
 * insert time, so a finished day's leaf set can never grow — recomputing
 * would always yield the same root). Today's root is never served: the day
 * is still open, so committing to it early would be a lie. Days with zero
 * seals are omitted, never invented.
 */

const { pool } = require('../db');
const { merkleRoot, merkleProof, canonicalLeaves } = require('./sealroot');

const dayOf = (ts) => new Date(ts).toISOString().slice(0, 10);
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** All seals minted on one UTC day, across every sealed surface. */
async function sealsForDay(day) {
  const lo = new Date(day + 'T00:00:00.000Z');
  const hi = new Date(lo.getTime() + 24 * 3600 * 1000);
  const out = [];
  // A position's seal rides onto its closed-trade row with sealed_at intact,
  // so a trade sealed on day D is found on D whether it's still open or
  // already closed — the dedupe in canonicalLeaves covers any overlap.
  const QUERIES = [
    'SELECT seal FROM signals WHERE sealed_at >= ? AND sealed_at < ?',
    'SELECT seal FROM arena_positions WHERE sealed_at >= ? AND sealed_at < ?',
    'SELECT seal FROM arena_trades WHERE sealed_at >= ? AND sealed_at < ?',
    // An agent claim is a dated public fact like any other: including it here
    // is what makes "this agent existed on this date" rest on a Base block
    // timestamp instead of on our own `created_at` column. Adding a surface
    // cannot disturb an existing root — a completed day's root is stored
    // immutably the first time it is asked for, and no agent row can carry a
    // `sealed_at` earlier than this table.
    'SELECT seal FROM agents WHERE sealed_at >= ? AND sealed_at < ?',
    // A pre-signature scan is the one seal whose whole value is its TIME: the
    // claim is "this agent was told this before it signed", and without the
    // day's root anchored on Base that ordering rests on our own clock —
    // exactly the thing it is being offered as evidence against.
    'SELECT seal FROM scan_seals WHERE sealed_at >= ? AND sealed_at < ?',
    // The Daily Duel (Provable Calls v6/v7, 2026-10-19): the round dealt, the
    // call made and the settlement written are three seals on three days
    // (the day each was minted), so a day's root commits to the Claw's stance
    // before anyone called, to every call before the market moved, and to
    // every outcome as it was scored. Picks before v6 carry a seal and no
    // round seal; both still fold in.
    'SELECT seal FROM duel_rounds WHERE created_at >= ? AND created_at < ?',
    'SELECT seal FROM duel_picks WHERE created_at >= ? AND created_at < ?',
    'SELECT settle_seal AS seal FROM duel_picks WHERE settled_at >= ? AND settled_at < ?',
  ];
  for (const q of QUERIES) {
    try {
      const [rows] = await pool.execute(q, [lo, hi]);
      for (const r of rows) {
        // The in-memory shim answers the settlement sweep with whole rows,
        // so the alias above is read either way.
        const leaf = q.includes('settle_seal') ? (r.settle_seal || r.seal) : r.seal;
        if (leaf) out.push(String(leaf));
      }
    } catch (e) { /* surface not sealed yet on this deployment — skip */ }
  }
  return canonicalLeaves(out);
}

/**
 * The stored root row for a completed day, computing + storing it on first
 * ask. Returns null for today/future days, malformed days, and empty days.
 */
async function rootForDay(day) {
  if (!DAY_RE.test(String(day))) return null;
  if (String(day) >= dayOf(Date.now())) return null;   // the day is still open
  try {
    const [rows] = await pool.execute('SELECT day, root, seal_count, leaves, computed_at, anchor_tx, anchored_at FROM seal_roots WHERE day = ?', [day]);
    if (rows[0]) return rows[0];
  } catch (e) { return null; }
  const leaves = await sealsForDay(day);
  if (!leaves.length) return null;                      // nothing sealed — omit
  const root = merkleRoot(leaves);
  // The leaf set is committed WITH the root: proofs are only ever derived
  // from what the root actually covers, so a row back-inserted into a
  // published day can never borrow a valid-looking proof.
  const row = { day, root, seal_count: leaves.length, leaves: JSON.stringify(leaves), computed_at: new Date() };
  try {
    await pool.execute(
      'INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
      [row.day, row.root, row.seal_count, row.leaves, row.computed_at]);
  } catch (e) { /* concurrent compute — the stored row is identical */ }
  return row;
}

/** Newest stored roots, backfilling up to `scan` recent completed days. */
async function listRoots(limit = 30, scan = 7) {
  const today = dayOf(Date.now());
  for (let i = 1; i <= scan; i++) {
    const d = dayOf(new Date(today + 'T00:00:00.000Z').getTime() - i * 24 * 3600 * 1000);
    await rootForDay(d);   // lazily fills any recent gap; empty days stay absent
  }
  let rows;
  try {
    [rows] = await pool.execute(
      `SELECT day, root, seal_count, computed_at, anchor_tx, anchored_at FROM seal_roots ORDER BY day DESC LIMIT ${Math.min(Number(limit) || 30, 90)}`);
  } catch (e) { return []; }
  // Every chain's anchor beside the row, so the feed and the pages read one
  // list rather than the Base column plus a table.
  for (const r of rows) r.anchors = await anchorsFor(r.day, r);
  return rows;
}

/**
 * Every chain's anchor of a day's root, sorted by chain key: the rows of
 * seal_root_anchors, plus the Base leg a deployment recorded in
 * seal_roots.anchor_tx before the table existed, read as `base` when the
 * table holds no Base row. A day with none answers an empty list — "not yet
 * anchored" is a fact about the chains, and an unreadable table is read as
 * none the same way the feed reads an unreadable root list as empty.
 */
async function anchorsFor(day, rootRow) {
  let rows = [];
  try {
    const [r] = await pool.execute(
      'SELECT day, chain, tx, from_addr, block_time, verified_at FROM seal_root_anchors WHERE day = ?', [day]);
    rows = (r || []).map((a) => ({
      chain: String(a.chain), tx: String(a.tx), from: a.from_addr || null,
      block_time: a.block_time || null, verified_at: a.verified_at || null,
    }));
  } catch (e) { rows = []; }
  const legacy = rootRow && rootRow.anchor_tx;
  if (legacy && !rows.some((a) => a.chain === 'base')) {
    rows.push({ chain: 'base', tx: String(legacy), from: null,
      block_time: rootRow.anchored_at || null, verified_at: null, legacy: true });
  }
  return rows.sort((a, b) => (a.chain < b.chain ? -1 : a.chain > b.chain ? 1 : 0));
}

/**
 * Record a VERIFIED anchor: first write per (day, chain) wins, immutable like
 * the root itself. The Base leg is mirrored into seal_roots' older columns,
 * which receipts written before the table still read. True when this call
 * recorded it; false when that chain already had one (a race with another
 * submitter, or a duplicate).
 */
async function recordAnchor(day, chain, tx, verdict) {
  const blockTime = verdict && verdict.block_time ? new Date(verdict.block_time) : null;
  let recorded = false;
  try {
    const [r] = await pool.execute(
      'INSERT IGNORE INTO seal_root_anchors (day, chain, tx, from_addr, block_time, verified_at) VALUES (?, ?, ?, ?, ?, ?)',
      [day, chain, tx, (verdict && verdict.from) || null, blockTime, new Date()]);
    recorded = !!(r && r.affectedRows);
  } catch (e) { recorded = false; }
  if (recorded && chain === 'base') {
    try {
      await pool.execute(
        'UPDATE seal_roots SET anchor_tx = ?, anchored_at = ? WHERE day = ? AND anchor_tx IS NULL',
        [tx, blockTime, day]);
    } catch (e) { /* the older columns mirror the table; the table is the record */ }
  }
  return recorded;
}

/**
 * Membership proof for one receipt: { day, root, seal_count, proof } — or
 * null while the seal's day is still open (stated honestly by the caller).
 */
async function anchorFor(seal, sealedAt) {
  if (!seal || !sealedAt) return null;
  const day = dayOf(sealedAt);
  const rootRow = await rootForDay(day);
  if (!rootRow) return null;
  // Proofs come from the COMMITTED leaf set only. A seal absent from it
  // (e.g. back-inserted after the day was published) gets no anchor — the
  // absence IS the honest signal.
  let leaves;
  try { leaves = JSON.parse(rootRow.leaves); } catch (e) { leaves = null; }
  if (!Array.isArray(leaves) || !leaves.length) return null;
  const proof = merkleProof(leaves, String(seal));
  if (!proof) return null;
  const anchors = await anchorsFor(day, rootRow);
  const base = anchors.find((a) => a.chain === 'base') || null;
  return { day, root: rootRow.root, seal_count: rootRow.seal_count, proof,
    // The on-chain leg, when the operator has anchored the day: the Base
    // transaction whose block time bounds every seal in the day.
    anchors,
    anchor_tx: base ? base.tx : null,
    anchored_at: base ? base.block_time : null };
}

module.exports = { sealsForDay, rootForDay, listRoots, anchorFor, anchorsFor, recordAnchor, dayOf };
