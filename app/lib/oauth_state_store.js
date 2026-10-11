'use strict';
/**
 * The OAuth handoff, shared by every web instance.
 *
 * A login with a provider is two requests: /oauth/:provider/start mints a CSRF
 * `state` and a PKCE verifier and redirects to the provider; the provider sends
 * the browser back to /oauth/:provider/callback with that state, possibly
 * minutes later. A logged-in browser that wants to LINK a provider first gets
 * a one-time link key. All of this lived in two Maps in auth.js.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, with overlap. A callback that lands on another instance, or after a
 * rotation, found no state and answered "invalid or expired login attempt".
 * The records now live in the database (`oauth_states`), as the phone wallet
 * link's already do (lib/wallet_link_store.js), and this process's memory is
 * the fallback while the database does not answer.
 *
 * Each record is SINGLE-USE, and claimed by one DELETE: of two callbacks racing
 * on one state, on one instance or two, exactly one gets the row. The TTL is
 * enforced on read. The kind ('flow' or 'link') is part of the claim, so a link
 * key cannot be replayed as a login state or the reverse.
 */

const KINDS = new Set(['flow', 'link']);

const memory = new Map(); // state_key -> { kind, payload, expiresAt }

function _pool() {
  try { return require('../db').pool; } catch (_) { return null; }
}

async function _prune(now) {
  try { await _pool().execute('DELETE FROM oauth_states WHERE expires_at < ?', [now]); } catch (_) { /* best effort */ }
  for (const [k, v] of memory) if (v.expiresAt < now) memory.delete(k);
}

function _kind(kind) {
  if (!KINDS.has(kind)) throw new Error(`oauth_state_store: unknown kind ${kind}`);
  return kind;
}

/** Store a single-use record until `expiresAt` (epoch ms). */
async function put(kind, key, payload, expiresAt) {
  _kind(kind);
  memory.set(key, { kind, payload, expiresAt });
  if (memory.size > 5000) memory.clear();
  try {
    await _pool().execute(
      'REPLACE INTO oauth_states (state_key, kind, payload, expires_at) VALUES (?, ?, ?, ?)',
      [key, kind, JSON.stringify(payload), expiresAt]);
  } catch (_) { /* memory still holds it, for this instance */ }
  await _prune(Date.now());
}

/**
 * Claim the record: its payload, once, or null when it is missing, expired,
 * of another kind, or already claimed (here or on another instance).
 */
async function take(kind, key) {
  _kind(kind);
  if (!key) return null;
  const now = Date.now();
  const local = memory.get(key) || null;
  memory.delete(key);
  let row = null;
  let dbAnswered = false;
  try {
    const pool = _pool();
    const [rows] = await pool.execute(
      'SELECT kind, payload, expires_at FROM oauth_states WHERE state_key = ?', [key]);
    const r = rows && rows[0];
    if (r) row = { kind: r.kind, payload: JSON.parse(r.payload), expiresAt: Number(r.expires_at) };
    const [del] = await pool.execute('DELETE FROM oauth_states WHERE state_key = ? AND kind = ?', [key, kind]);
    dbAnswered = true;
    if (!del || del.affectedRows !== 1) return null; // missing, another kind, or claimed elsewhere
  } catch (_) {
    dbAnswered = false;
  }
  const rec = dbAnswered ? row : local; // memory decides only when the database did not answer
  if (!rec || rec.kind !== kind || !(rec.expiresAt > now)) return null;
  return rec.payload;
}

/** Test seam: drop this process's copies, as a fresh instance has none. */
function _clearMemory() {
  memory.clear();
}

module.exports = { put, take, _clearMemory };
