'use strict';
/**
 * What a timed job remembers between runs, shared by every web instance.
 *
 * Several jobs keep a memory that makes them correct, not just fast: the
 * board watch and the copy watch record a BASELINE on their first run so a
 * restart never replays old news; the arena watch remembers which positions it
 * already warned; the pattern watch remembers what it announced in the last
 * 12 hours. On a host that replaces its instance about every 70 seconds
 * (lib/shared_schedule.js), a memory held in a variable is gone before the
 * next run: the baseline jobs never got past their silent first run, and the
 * warning jobs could repeat themselves on every new instance.
 *
 * The state lives in the database (`job_state`), as JSON. Reads say which of
 * three things they found: a saved state, nothing saved yet, or a database
 * that did not answer. While the database does not answer, a job keeps its
 * state in this process (the old behaviour).
 */

const memory = new Map(); // job -> value, used only while the database does not answer

function _pool() {
  try { return require('../db').pool; } catch (_) { return null; }
}

/**
 * `{ found: true, value }`, `{ found: false }` when nothing is saved, or the
 * process's own copy (`{ found, value, local: true }`) when the database did
 * not answer.
 */
async function load(job) {
  try {
    const [rows] = await _pool().execute('SELECT state FROM job_state WHERE job = ?', [job]);
    if (!rows || !rows.length) return { found: false };
    return { found: true, value: JSON.parse(rows[0].state) };
  } catch (_) {
    if (memory.has(job)) return { found: true, value: memory.get(job), local: true };
    return { found: false, local: true };
  }
}

/** Save `value` (JSON-serialisable) as the job's state. */
async function save(job, value) {
  memory.set(job, value);
  try {
    await _pool().execute(
      'REPLACE INTO job_state (job, state, updated_at) VALUES (?, ?, ?)',
      [job, JSON.stringify(value), Date.now()]);
  } catch (_) { /* this process still holds it */ }
}

/** Forget the job's state everywhere (tests, and a deliberate re-baseline). */
async function clear(job) {
  memory.delete(job);
  try { await _pool().execute('DELETE FROM job_state WHERE job = ?', [job]); } catch (_) { /* nothing to forget */ }
}

/** Test seam: drop this process's copies, as a fresh instance has none. */
function _clearMemory() {
  memory.clear();
}

module.exports = { load, save, clear, _clearMemory };
