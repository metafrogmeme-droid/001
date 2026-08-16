'use strict';
/**
 * Arena keys — the credential an autonomous agent uses to paper-trade.
 *
 * The MCP server is public and unauthenticated because every tool it exposes
 * is read-only. Opening a position is not, so it needs an identity — and the
 * shape of that identity decides how much a leaked key can cost.
 *
 * IT REACHES THE PAPER ARENA AND NOTHING ELSE.
 *
 * Not "it is intended for the Arena": it is structurally incapable of anything
 * more. `verify()` returns a bare user id, the MCP tools are the only callers,
 * and each of those calls `openForUser` / `closeForUser` — functions that touch
 * `arena_positions`, `arena_trades` and `arena_accounts` and nothing else. It
 * is not a session, `authMiddleware` will not accept it, and there is no code
 * path from one of these keys to a live order, an exchange credential, a
 * wallet, or another user's row.
 *
 * The blast radius of a stolen key is therefore: somebody can lose your
 * VIRTUAL money and put bad trades on your public paper record. That is a real
 * cost — the Arena record is a reputation — which is why keys are revocable and
 * last-used is tracked, but it is not somebody's capital.
 *
 * STORED AS A HASH, SHOWN ONCE.
 *
 * sha256 with no salt and no stretching, deliberately: the secret is 32 bytes
 * from `randomBytes`, so there is no dictionary to attack and nothing for
 * bcrypt's work factor to buy. Stretching a high-entropy random token is a cost
 * paid on every single tool call for no security. (A PASSWORD would need bcrypt
 * — that is a different thing and it is why `auth.js` uses it.)
 *
 * A constant-time compare is still used on lookup, because the hash is being
 * matched against a value an attacker supplies.
 */

const crypto = require('node:crypto');
const { pool } = require('../db');

/** Prefix so a leaked key is greppable and obviously ours in a log or repo. */
const PREFIX = 'rcarena_';

/** Keys per user. A bound, so a loop cannot mint forever. */
const MAX_KEYS_PER_USER = 5;

const hash = (key) => crypto.createHash('sha256').update(String(key)).digest('hex');

/** True for a syntactically plausible key. Cheap reject before any DB read. */
function looksLikeKey(raw) {
  return typeof raw === 'string'
    && raw.startsWith(PREFIX)
    && /^[A-Za-z0-9_-]{43}$/.test(raw.slice(PREFIX.length));
}

/**
 * Mint a key. Returns `{ key, id, label }` — `key` is the ONLY time the
 * plaintext exists anywhere, and it is never written to the database or a log.
 */
async function mint(userId, label = '') {
  const [rows] = await pool.execute(
    'SELECT COUNT(*) AS n FROM arena_api_keys WHERE user_id = ? AND revoked_at IS NULL',
    [userId]);
  const n = Number(rows && rows[0] && rows[0].n) || 0;
  if (n >= MAX_KEYS_PER_USER) {
    const e = new Error(`At most ${MAX_KEYS_PER_USER} active keys — revoke one first.`);
    e.code = 'TOO_MANY_KEYS';
    throw e;
  }
  const clean = String(label || '').replace(/[^\w .\-]/g, '').slice(0, 40);
  // `key_hash` is UNIQUE, so a collision is a duplicate-key error rather than
  // a silent overwrite — which is the behaviour we want and the reason this is
  // NOT an upsert: ON DUPLICATE KEY UPDATE here would hand two people the same
  // key. Retried instead. With 32 random bytes a collision will not happen;
  // the loop exists so that if it somehow did, nobody gets a 500 and nobody
  // gets somebody else's key.
  for (let attempt = 0; attempt < 3; attempt++) {
    const key = PREFIX + crypto.randomBytes(32).toString('base64url');
    try {
      const [r] = await pool.execute(
        'INSERT INTO arena_api_keys (user_id, key_hash, label, created_at) VALUES (?, ?, ?, ?)',
        [userId, hash(key), clean, new Date()]);
      return { key, id: r && r.insertId, label: clean };
    } catch (err) {
      if (err && err.code === 'ER_DUP_ENTRY') continue;
      throw err;
    }
  }
  throw new Error('Could not allocate a key — try again.');
}

/**
 * The user this key belongs to, or null.
 *
 * Null for every failure — unknown, revoked, malformed, or a database that
 * could not be read. A key that cannot be checked is not a valid key, and the
 * caller must not be able to tell those cases apart: "revoked" and "never
 * existed" are the same answer to whoever is holding it.
 */
async function verify(raw) {
  if (!looksLikeKey(raw)) return null;
  try {
    const [rows] = await pool.execute(
      'SELECT id, user_id, key_hash FROM arena_api_keys WHERE key_hash = ? AND revoked_at IS NULL',
      [hash(raw)]);
    const row = rows && rows[0];
    if (!row) return null;
    // The lookup was by hash, so this compare is belt-and-braces — but it is
    // comparing against a value the caller supplied, so it is constant-time.
    const a = Buffer.from(String(row.key_hash));
    const b = Buffer.from(hash(raw));
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
    // Best-effort: a failed touch must never cost a valid caller their call.
    pool.execute('UPDATE arena_api_keys SET last_used_at = ? WHERE id = ?',
      [new Date(), row.id]).catch(() => {});
    return Number(row.user_id);
  } catch (err) {
    console.error('[arena_keys] verify failed:', err.stack || err.message);
    return null;
  }
}

/** Keys a user holds. Never returns a hash — there is nothing to show. */
async function list(userId) {
  const [rows] = await pool.execute(
    'SELECT id, label, created_at, last_used_at FROM arena_api_keys '
    + 'WHERE user_id = ? AND revoked_at IS NULL ORDER BY id DESC',
    [userId]);
  return (rows || []).map((r) => ({
    id: r.id,
    label: r.label || '',
    created_at: r.created_at,
    // `null`, not "never" and not a date: absent is not a measurement, and a
    // key that has never been used is a different fact from one used long ago.
    last_used_at: r.last_used_at || null,
  }));
}

/** Revoke one of the caller's own keys. True if a row was actually revoked. */
async function revoke(userId, id) {
  const n = Number(id);
  if (!Number.isInteger(n) || n <= 0) return false;
  const [r] = await pool.execute(
    'UPDATE arena_api_keys SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL',
    [new Date(), n, userId]);
  return Number(r && r.affectedRows) > 0;
}

/** The bearer token on a request, or null. Never throws. */
function bearerFrom(req) {
  const h = (req && req.headers && req.headers.authorization) || '';
  const m = /^Bearer\s+(\S+)$/i.exec(String(h));
  return m ? m[1] : null;
}

module.exports = {
  PREFIX, MAX_KEYS_PER_USER,
  mint, verify, list, revoke, bearerFrom, looksLikeKey,
};
