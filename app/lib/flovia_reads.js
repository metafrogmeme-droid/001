'use strict';
/**
 * Remembered Flovia reads — one row per key in `flovia_reads`, so a read that
 * is good for a day (a token's smart-money flow) or ten minutes (a pool's
 * liquidity) is made once across every web instance, not once per instance
 * per page load. The host replaces its instance every seventy seconds, so an
 * in-memory cache would make the free allowance pay for every restart.
 *
 * WHAT IS REMEMBERED
 *
 * A successful read for `ttlMs`. A failed read (refused, unreachable,
 * malformed) for a short while too, REASON INCLUDED: a spent allowance asked
 * again on every render would stay spent, and a panel that re-asked every
 * second would read "could not be read" exactly as often while costing the
 * window the moment it reopened. `unconfigured` is never stored: it is a
 * fact about this deployment, not about the provider, and it costs nothing
 * to answer. A remembered failure keeps its reason, so the page still says
 * which absence it is.
 *
 * `peek` reads the table and never calls the producer: a feed that lists
 * fifty symbols must not open fifty lookups on one page load. The producer
 * runs only from `remember`, on the path that is allowed to spend.
 */

const { pool } = require('../db');

const FAILURE_TTL_MS = 10 * 60 * 1000;
const PAYLOAD_MAX = 200 * 1024;

function keyOf(k) { return String(k).slice(0, 190); }

/** A stored row as a hit, or null when it has expired or will not parse. */
function hitOf(row) {
  if (!row) return null;
  const exp = row.expires_at instanceof Date ? row.expires_at.getTime() : Date.parse(row.expires_at);
  if (!Number.isFinite(exp) || exp <= Date.now()) return null;
  let value;
  try { value = JSON.parse(row.payload); } catch (e) { return null; }
  return { value, read_at: row.read_at instanceof Date ? row.read_at.toISOString() : String(row.read_at), expires_at: new Date(exp).toISOString() };
}

async function readRow(k) {
  const [rows] = await pool.execute(
    'SELECT k, payload, read_at, expires_at FROM flovia_reads WHERE k = ?', [keyOf(k)]);
  return hitOf(rows && rows[0]);
}

/** The rows for many keys in ONE read: a feed of forty rows is one query, not forty. */
async function readRows(keys) {
  const ks = [...new Set(keys.map(keyOf))];
  if (!ks.length) return new Map();
  const [rows] = await pool.execute(
    `SELECT k, payload, read_at, expires_at FROM flovia_reads WHERE k IN (${ks.map(() => '?').join(', ')})`, ks);
  const out = new Map();
  for (const row of rows || []) {
    const hit = hitOf(row);
    if (hit) out.set(String(row.k), hit);
  }
  return out;
}

async function writeRow(k, value, ttlMs) {
  const payload = JSON.stringify(value);
  if (payload.length > PAYLOAD_MAX) return false;
  const now = new Date();
  await pool.execute(
    'REPLACE INTO flovia_reads (k, payload, read_at, expires_at) VALUES (?, ?, ?, ?)',
    [keyOf(k), payload, now, new Date(now.getTime() + Math.max(1000, Number(ttlMs) || 0))]);
  return true;
}

/** The remembered value, or null; never produces. */
async function peek(k) {
  try { return await readRow(k); } catch (e) { return null; }
}

/** The remembered values for many keys, as a Map of key -> hit; a key not remembered is absent. Never produces. */
async function peekMany(keys) {
  try { return await readRows(keys || []); } catch (e) { return new Map(); }
}

/**
 * The remembered value, else `producer()`'s result remembered for `ttlMs`
 * (a failure for FAILURE_TTL_MS, `unconfigured` not at all). The producer
 * answers a `{ read, ... }` result and never throws; a throw here is turned
 * into `{ read: false, reason: 'unreachable' }` naming the class.
 */
async function remember(k, ttlMs, producer, { failureTtlMs = FAILURE_TTL_MS } = {}) {
  const hit = await peek(k);
  if (hit) return { ...hit.value, read_at: hit.value.read_at || hit.read_at, remembered: true };
  let value;
  try {
    value = await producer();
  } catch (e) {
    value = { read: false, reason: 'unreachable', detail: (e && e.name) || 'Error' };
  }
  if (!value || typeof value !== 'object') value = { read: false, reason: 'malformed' };
  if (!value.read_at) value.read_at = new Date().toISOString();
  if (value.reason !== 'unconfigured') {
    try { await writeRow(k, value, value.read ? ttlMs : failureTtlMs); } catch (e) { /* a cache that cannot write is a slower cache */ }
  }
  return value;
}

/** Drop what has expired (a job, or a test). */
async function sweep() {
  try { await pool.execute('DELETE FROM flovia_reads WHERE expires_at < ?', [new Date()]); } catch (e) { /* fine */ }
}

module.exports = { peek, peekMany, remember, sweep, FAILURE_TTL_MS };
