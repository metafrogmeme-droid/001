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
  'db_unreachable',  // refused / timed out / no such host / no route / no DNS
  'db_auth',         // the server answered and rejected the credentials
  'db_tls',          // the server answered and refused the transport
  'db_config',       // the server answered; the URL names something that isn't there
  'db_timeout',      // the attempt was still running when its cap expired
  'db_error',        // reached, failed, cause not classifiable — no detail kept
  'ready',           // migration succeeded; database-backed traffic is served
]);

/**
 * Marker code for an attempt the caller CAPPED rather than one the driver
 * failed. Distinct from ETIMEDOUT (which the driver raises when it gives up
 * on its own) because the two mean different things to an operator: a driver
 * timeout says the server did not answer, while this says the driver never
 * came back at all — the failure mode that produced `attempts: 0` after seven
 * minutes of uptime in production, with the retry loop parked on its first
 * call and no possibility of recovery.
 */
const TIMEOUT_CODE = 'RC_MIGRATE_TIMEOUT';

/** Driver/OS error codes → coarse reason. Anything unlisted → 'db_error'. */
const UNREACHABLE = new Set([
  'ECONNREFUSED', 'ETIMEDOUT', 'ENOTFOUND', 'EHOSTUNREACH',
  'ENETUNREACH', 'ECONNRESET', 'EPIPE', 'PROTOCOL_CONNECTION_LOST',
  'EAI_AGAIN', 'PROTOCOL_SEQUENCE_TIMEOUT',
]);
const AUTH = new Set([
  'ER_ACCESS_DENIED_ERROR', 'ER_DBACCESS_DENIED_ERROR', 'ER_NOT_SUPPORTED_AUTH_MODE',
]);
// TLS is its own category because its remedy is its own: a managed MySQL that
// mandates encrypted transport (TiDB Cloud does) rejects a plaintext connect
// with ER_SECURE_TRANSPORT_REQUIRED, and a certificate problem is a different
// fix again. Collapsed into 'db_error' these all read as "the database is
// broken" when the database is fine and the CONNECTION STRING is not.
const TLS = new Set([
  'ER_SECURE_TRANSPORT_REQUIRED', 'HANDSHAKE_NO_SSL_SUPPORT', 'HANDSHAKE_SSL_ERROR',
  'EPROTO', 'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED',
  'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'SELF_SIGNED_CERT_IN_CHAIN',
  'DEPTH_ZERO_SELF_SIGNED_CERT',
]);
// The server answered and the URL points at something that does not exist —
// an operator fix, not an outage.
const CONFIG = new Set([
  'ER_BAD_DB_ERROR', 'ER_NO_SUCH_TABLE', 'ER_HOST_NOT_PRIVILEGED', 'ER_HOST_IS_BLOCKED',
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
  if (code === TIMEOUT_CODE) return 'db_timeout';
  if (UNREACHABLE.has(code)) return 'db_unreachable';
  if (AUTH.has(code)) return 'db_auth';
  if (TLS.has(code)) return 'db_tls';
  if (CONFIG.has(code)) return 'db_config';
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
  REASONS, TIMEOUT_CODE, classify, markReady, markAttemptFailed, isReady,
  snapshot, _reset,
};
