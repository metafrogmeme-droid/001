'use strict';
/**
 * Timed jobs on one schedule, shared by every web instance.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, with overlap. Every background job here was a `setInterval` in one
 * process, so a job ran only if an instance lived long enough to reach its
 * first tick: the two-minute pattern watch never did, the one-minute jobs ran
 * at most once per instance, and the hourly sweeps ran at every boot instead
 * of hourly. Overlapping instances could also run the same job twice.
 *
 * A job's last start lives in the database (`job_runs`). Every instance looks
 * every `tickMs` (15 s by default) and CLAIMS the job when it is due, in one
 * conditional UPDATE: of any number of instances, exactly one gets the row, and
 * the job runs on its interval for as long as any instance is alive, however
 * short each one's life is.
 *
 * While the database does not answer, an instance falls back to its own clock
 * (the old behaviour: degraded, not stopped).
 */

const local = new Map(); // job -> last start in this process, used only while the database does not answer
let warned = false;

function _pool() {
  try { return require('../db').pool; } catch (_) { return null; }
}

/**
 * Claim `job` if it has not started in the last `intervalMs`. True means this
 * caller runs it now; false means it is not due or another instance has it.
 */
async function claim(job, intervalMs, now = Date.now()) {
  try {
    const pool = _pool();
    const [upd] = await pool.execute(
      'UPDATE job_runs SET last_started = ? WHERE job = ? AND last_started <= ?',
      [now, job, now - intervalMs]);
    if (upd && upd.affectedRows === 1) return true;
    // No row yet: the first claim creates it. A duplicate key means another
    // instance created it a moment ago, and that instance has the run.
    const [rows] = await pool.execute('SELECT last_started FROM job_runs WHERE job = ?', [job]);
    if (rows && rows.length) return false;
    try {
      await pool.execute('INSERT INTO job_runs (job, last_started) VALUES (?, ?)', [job, now]);
      return true;
    } catch (e) {
      if (e && (e.code === 'ER_DUP_ENTRY' || e.errno === 1062)) return false;
      throw e;
    }
  } catch (err) {
    if (!warned) {
      warned = true;
      console.warn(`shared_schedule: the database did not answer (${(err && (err.code || err.name)) || 'Error'}); `
        + 'running jobs on this instance\'s clock until it does');
    }
    const last = local.get(job) || 0;
    if (now - last < intervalMs) return false;
    local.set(job, now);
    return true;
  }
}

/**
 * Run `run` every `intervalMs`, across all instances: each instance checks every
 * `tickMs` (first after `firstDelayMs`) and runs the job when it wins the claim.
 * `when()` may return false to skip a tick without claiming (e.g. push is not
 * configured). Returns a stop function.
 */
function every(job, intervalMs, run, { tickMs = 15_000, firstDelayMs = 5_000, when } = {}) {
  let busy = false;
  const tick = async () => {
    if (busy) return;                     // a slow run never overlaps itself here
    busy = true;
    try {
      if (when && !when()) return;
      if (await claim(job, intervalMs)) await run();
    } catch (_) {
      /* a job reports its own health; a throw must not stop the schedule */
    } finally {
      busy = false;
    }
  };
  const first = setTimeout(tick, firstDelayMs);
  const timer = setInterval(tick, Math.min(tickMs, intervalMs));
  if (first.unref) first.unref();
  if (timer.unref) timer.unref();
  return () => { clearTimeout(first); clearInterval(timer); };
}

/** Test seam: forget the local fallback clock. */
function _resetLocal() {
  local.clear();
  warned = false;
}

module.exports = { claim, every, _resetLocal };
