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
  ];
  for (const q of QUERIES) {
    try {
      const [rows] = await pool.execute(q, [lo, hi]);
      for (const r of rows) if (r.seal) out.push(String(r.seal));
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
    const [rows] = await pool.execute('SELECT day, root, seal_count, leaves, computed_at FROM seal_roots WHERE day = ?', [day]);
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
  try {
    const [rows] = await pool.execute(
      `SELECT day, root, seal_count, computed_at FROM seal_roots ORDER BY day DESC LIMIT ${Math.min(Number(limit) || 30, 90)}`);
    return rows;
  } catch (e) { return []; }
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
  return { day, root: rootRow.root, seal_count: rootRow.seal_count, proof };
}

module.exports = { sealsForDay, rootForDay, listRoots, anchorFor, dayOf };
