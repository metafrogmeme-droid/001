'use strict';
/**
 * Liveness accounting for the background watchers.
 *
 * Six sweeps run on timers in this process -- the tripwire engine, the arena
 * liquidation watch, the copy-trade digest, the season ceremony, the board
 * digest, the pattern watch, the weekly letter -- and until the tripwire
 * engine got its own accounting every one of them ran under
 * `.catch(() => {})` with no log line and no surface. A dead ticker feed
 * silently ended near-liquidation warnings, and looked exactly like a calm
 * paper floor. This is the same accounting the tripwire engine keeps
 * (lib/alerts.js engineState), made shared: a watcher registers once and
 * reports each pass as ok, failed or skipped; the registry logs the first
 * failure and then every RELOG_MS, never once per pass forever; and
 * /diagz serves the snapshot so an operator can see which sweeps are alive.
 *
 * States, all three said rather than one of them implied by silence:
 *   never   no pass has completed yet (a fresh boot)
 *   ok      the last pass completed
 *   failed  the last N passes threw -- last_error names the first cause
 *   skipped the watcher is disabled by configuration (push not set up, no
 *           gateway) -- not a failure, not "ok" either
 */
const RELOG_MS = 10 * 60_000;
const registry = new Map();
const _fresh = () => ({
  last_outcome: null, last_run_at: null, last_ok_at: null, consecutive_failures: 0,
  last_error: null, skipped_reason: null, warned_at: 0,
});

function register(name) {
  if (!registry.has(name)) {
    registry.set(name, _fresh());
  }
  const s = registry.get(name);
  return {
    ran() { s.last_run_at = Date.now(); },
    ok() {
      s.last_outcome = 'ok';
      s.last_run_at = s.last_run_at || Date.now();
      s.last_ok_at = Date.now(); s.consecutive_failures = 0; s.last_error = null; s.skipped_reason = null;
    },
    skipped(reason) {
      // The LAST pass decided not to run; the state says so even after
      // earlier failures. The failure count is kept for the record.
      s.last_outcome = 'skipped';
      s.last_run_at = Date.now(); s.skipped_reason = String(reason || 'disabled');
    },
    failed(err) {
      s.last_outcome = 'failed';
      s.last_run_at = Date.now();
      s.consecutive_failures += 1;
      s.last_error = String((err && err.message) || err).slice(0, 200);
      s.skipped_reason = null;
      const now = Date.now();
      if (s.consecutive_failures === 1 || now - s.warned_at >= RELOG_MS) {
        s.warned_at = now;
        console.warn(`Watcher ${name} pass failed (${s.consecutive_failures} consecutive): ${s.last_error}`
          + ' — its notifications are not being evaluated until it recovers');
      }
    },
  };
}

/** {name: {state, last_run_at, last_ok_at, consecutive_failures, last_error, skipped_reason}} */
function snapshot() {
  const iso = (t) => (t ? new Date(t).toISOString() : null);
  const out = {};
  for (const [name, s] of registry) {
    out[name] = {
      state: s.last_outcome || 'never',
      last_run_at: iso(s.last_run_at), last_ok_at: iso(s.last_ok_at),
      consecutive_failures: s.consecutive_failures, last_error: s.last_error,
      skipped_reason: s.skipped_reason,
    };
  }
  return out;
}

// In place, not a clear: watchers hold their handle from module load, so a
// cleared map would orphan every one of them from the snapshot.
function __testReset() { for (const s of registry.values()) Object.assign(s, _fresh()); }

module.exports = { register, snapshot, RELOG_MS, __testReset };
