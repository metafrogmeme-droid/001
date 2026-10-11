'use strict';
/**
 * Failure counters that every web instance shares.
 *
 * THE FINDING (2026-10-11). The web app's host replaces its instance about
 * every 70 seconds, and the outgoing and incoming instances overlap. The
 * per-account second-factor lockout (lib/second_factor_lockout.js) and the
 * per-IP login limiter (auth.js) kept their counts in a Map, so every new
 * instance started at zero and every overlapping instance kept its own count:
 * a guesser got a fresh budget at each rotation, and "8 failures per 15
 * minutes" held for about a minute. Both files already said a shared store was
 * the fix "if this is ever fronted by more than one process". It is.
 *
 * The count lives in the database (`auth_attempts`), incremented in one
 * statement, so every instance reads the same number. A row is keyed by a hash
 * of the counter's name and the caller's key, so no email or IP address is
 * stored.
 *
 * If the database cannot be reached, the counter keeps counting in this
 * process's memory: the old behaviour, degraded rather than open. A check
 * refuses when EITHER the shared count or this process's fallback count says
 * so, so failures counted during an outage still count once it ends.
 */

const crypto = require('node:crypto');

function _pool() {
  try { return require('../db').pool; } catch (_) { return null; }
}

/** The stored key: the counter's name and the caller's key, hashed. */
function rowKey(name, key) {
  return `${name}:${crypto.createHash('sha256').update(String(key)).digest('hex')}`;
}

/**
 * A counter that allows `max` failures per `windowMs` per key, and refuses for
 * `lockoutMs` after the one that reaches `max`. A lockout that expires inside
 * the window does not hand back a fresh budget: the count survives until the
 * window ends, as it always has (pinned in test/second_factor_lockout.test.js).
 */
function attemptCounter({ name, windowMs, max, lockoutMs }) {
  if (!/^[a-z][a-z0-9_]*$/.test(String(name || ''))) throw new Error('attemptCounter: name must be a short lowercase word');
  if (!(windowMs > 0) || !(max > 0) || !(lockoutMs > 0)) throw new Error('attemptCounter: windowMs, max and lockoutMs must be positive');

  const memory = new Map(); // rowKey -> { count, firstAttempt, lockedUntil }, used only when the database fails
  let warned = false;
  let lastPrune = 0;

  function fellBack(err) {
    if (warned) return;
    warned = true;
    // The class or code only: a driver message can carry a hostname.
    console.warn(`durable_attempts(${name}): the database did not answer `
      + `(${(err && (err.code || err.name)) || 'Error'}); counting in this process only until it does`);
  }

  /** May this entry try again at `now`? */
  function allows(entry, now) {
    if (!entry) return true;
    if (entry.lockedUntil && now < entry.lockedUntil) return false;
    if (now - entry.firstAttempt > windowMs) return true;
    return entry.count < max;
  }

  function memoryRecord(id, now) {
    let e = memory.get(id);
    if (e && now - e.firstAttempt > windowMs && (!e.lockedUntil || now > e.lockedUntil)) e = null;
    e = e || { count: 0, firstAttempt: now, lockedUntil: null };
    e.count += 1;
    if (e.count >= max) e.lockedUntil = now + lockoutMs;
    memory.set(id, e);
    if (memory.size > 10000) {
      const oldest = [...memory.entries()].sort((a, b) => a[1].firstAttempt - b[1].firstAttempt);
      for (let i = 0; i < oldest.length - 5000; i += 1) memory.delete(oldest[i][0]);
    }
  }

  async function prune(now) {
    if (now - lastPrune < 60_000) return;
    lastPrune = now;
    try {
      await _pool().execute(
        'DELETE FROM auth_attempts WHERE attempt_key LIKE ? AND first_at < ? AND (locked_until IS NULL OR locked_until < ?)',
        [`${name}:%`, now - windowMs, now]);
    } catch (_) { /* best effort: stale rows are also ignored on read */ }
  }

  /** True when `key` may make another attempt. A missing key never locks. */
  async function check(key) {
    if (!key) return true;
    const id = rowKey(name, key);
    const now = Date.now();
    if (!allows(memory.get(id), now)) return false;
    try {
      const [rows] = await _pool().execute(
        'SELECT fails, first_at, locked_until FROM auth_attempts WHERE attempt_key = ?', [id]);
      const r = rows && rows[0];
      if (!r) return true;
      return allows({
        count: Number(r.fails),
        firstAttempt: Number(r.first_at),
        lockedUntil: r.locked_until == null ? null : Number(r.locked_until),
      }, now);
    } catch (err) {
      fellBack(err);
      return true; // the memory verdict above already allowed it
    }
  }

  /** Count one failure against `key`. */
  async function record(key) {
    if (!key) return;
    const id = rowKey(name, key);
    const now = Date.now();
    try {
      const pool = _pool();
      // A stale, unlocked row is forgiven before the increment, as the
      // in-memory counter forgave it; the increment itself is one statement,
      // so two instances failing at once both count.
      await pool.execute(
        'DELETE FROM auth_attempts WHERE attempt_key = ? AND first_at < ? AND (locked_until IS NULL OR locked_until < ?)',
        [id, now - windowMs, now]);
      await pool.execute(
        'INSERT INTO auth_attempts (attempt_key, fails, first_at, locked_until) VALUES (?, 1, ?, NULL) '
        + 'ON DUPLICATE KEY UPDATE fails = fails + 1',
        [id, now]);
      await pool.execute(
        'UPDATE auth_attempts SET locked_until = ? WHERE attempt_key = ? AND fails >= ?',
        [now + lockoutMs, id, max]);
    } catch (err) {
      fellBack(err);
      memoryRecord(id, now);
      return;
    }
    await prune(now);
  }

  /** A success forgives `key`'s failures, everywhere. */
  async function clear(key) {
    if (!key) return;
    const id = rowKey(name, key);
    memory.delete(id);
    try {
      await _pool().execute('DELETE FROM auth_attempts WHERE attempt_key = ?', [id]);
    } catch (err) {
      fellBack(err);
    }
  }

  /** Test seam: forget this counter everywhere. Never called by production code. */
  async function _reset() {
    memory.clear();
    try {
      await _pool().execute('DELETE FROM auth_attempts WHERE attempt_key LIKE ?', [`${name}:%`]);
    } catch (_) { /* nothing to forget */ }
  }

  /** Test seam: plant a shared entry, as another instance would have left it. */
  async function _seed(key, entry) {
    await _pool().execute(
      'REPLACE INTO auth_attempts (attempt_key, fails, first_at, locked_until) VALUES (?, ?, ?, ?)',
      [rowKey(name, key), entry.count, entry.firstAttempt, entry.lockedUntil == null ? null : entry.lockedUntil]);
  }

  /** Test seam: forget this process's fallback counts, as a fresh instance has none. */
  function _clearMemory() {
    memory.clear();
  }

  return { check, record, clear, _reset, _seed, _clearMemory, _memory: memory };
}

module.exports = { attemptCounter, rowKey };
