'use strict';

/**
 * Boot readiness — "is this process alive?" and "can it serve database-backed
 * traffic?" are two different questions, and answering them with one answer
 * took the entire website off the air.
 *
 * The old startup awaited migrate() BEFORE app.listen(), and exited on
 * failure. So an unreachable database meant the port was never bound at all:
 * not a degraded site, a total blackout — every path hung, including the
 * static marketing pages that need no database whatsoever. On a platform that
 * restarts a dead container that is a crash loop, which presents to the edge
 * as "no healthy origin" forever.
 *
 * This module holds the split. Liveness is unconditional; readiness is earned
 * by a successful migration and is re-earned by retry, so a database that
 * comes back heals the process without a restart.
 *
 * HONESTY / F-15: `reason` is a COARSE CODE from a fixed vocabulary. Driver
 * messages, hostnames, ports, credentials, connection strings and stack
 * traces never reach it — /readyz is a public endpoint, and an error string
 * from a database driver is exactly the kind of internal detail that must
 * never appear in user-facing text. Unclassifiable failures collapse to
 * 'db_error' rather than passing anything through.
 */

/** The complete vocabulary of `reason`. Nothing outside this set is emitted. */
const REASONS = Object.freeze([
  'starting',        // no migration attempt has finished yet
  'db_unreachable',  // refused / timed out / no such host / no route
  'db_auth',         // the server answered and rejected the credentials
  'db_error',        // reached, failed, cause not classifiable — no detail kept
  'ready',           // migration succeeded; database-backed traffic is served
]);

/** Driver/OS error codes → coarse reason. Anything unlisted → 'db_error'. */
const UNREACHABLE = new Set([
  'ECONNREFUSED', 'ETIMEDOUT', 'ENOTFOUND', 'EHOSTUNREACH',
  'ENETUNREACH', 'ECONNRESET', 'EPIPE', 'PROTOCOL_CONNECTION_LOST',
]);
const AUTH = new Set([
  'ER_ACCESS_DENIED_ERROR', 'ER_DBACCESS_DENIED_ERROR', 'ER_NOT_SUPPORTED_AUTH_MODE',
]);

let _ready = false;
let _reason = 'starting';
let _attempts = 0;
let _readySince = null;

/**
 * Classify a thrown value into one of REASONS. Reads ONLY the error's `code`
 * field — never its message — so no driver text can leak through this path
 * even by accident.
 */
function classify(err) {
  const code = err && typeof err.code === 'string' ? err.code : '';
  if (UNREACHABLE.has(code)) return 'db_unreachable';
  if (AUTH.has(code)) return 'db_auth';
  return 'db_error';
}

/** Record a successful migration. Idempotent; keeps the FIRST ready moment. */
function markReady() {
  if (!_ready) {
    _ready = true;
    _readySince = new Date().toISOString();
  }
  _reason = 'ready';
}

/**
 * Record a failed migration attempt. Returns the coarse reason stored, so a
 * caller can log the category without re-deriving it.
 *
 * Note this never clears a readiness already earned: once the schema is
 * migrated it stays migrated, and a later transient error is a request-time
 * problem, not a boot-time one.
 */
function markAttemptFailed(err) {
  _attempts += 1;
  const reason = classify(err);
  if (!_ready) _reason = reason;
  return reason;
}

function isReady() {
  return _ready;
}

/** The public shape served by /readyz. Coarse, small, and free of internals. */
function snapshot() {
  return {
    ready: _ready,
    reason: _reason,
    attempts: _attempts,
    ready_since: _readySince,
  };
}

/** Test seam — restores the module to its just-required state. */
function _reset() {
  _ready = false;
  _reason = 'starting';
  _attempts = 0;
  _readySince = null;
}

module.exports = {
  REASONS, classify, markReady, markAttemptFailed, isReady, snapshot, _reset,
};
