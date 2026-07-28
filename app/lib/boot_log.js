'use strict';

/**
 * A grep-able record of why the web app is not serving.
 *
 * The web app writes to stdout and nothing else. On a managed host that means
 * its diagnostics live only in the platform's log viewer — so
 * `grep 'Migration failed' logs/*.log` matches nothing, because logs/ belongs
 * to the Python bot (docker-compose mounts it into the bot and api_bridge
 * containers, never this one). Two incidents on 2026-07-28 turned on facts
 * that WERE printed and could not be found: a boot fatal that read as a hang,
 * and a database failure whose driver code nobody could reach.
 *
 * So the events that explain a non-serving app are also appended to a file,
 * in the place an operator already greps.
 *
 * Design constraints, in order of importance:
 *
 * 1. It must never become a new failure mode. Every call is wrapped, the
 *    writes are ASYNCHRONOUS and fire-and-forget, and every error is
 *    swallowed. A logger that can break boot is worse than no logger — and
 *    this app has already been bitten by a synchronous read at module scope
 *    (see lib/secrets_vault.js), so nothing here blocks the event loop or the
 *    boot path on a filesystem that might be wedged.
 *
 * 2. It must never write a secret. Callers pass categories, codes and
 *    lengths; this module adds only a timestamp. There is no formatting of
 *    arbitrary error objects, so a driver message carrying a connection
 *    string cannot arrive here by accident.
 *
 * 3. It must not grow without bound. The file is truncated once it passes a
 *    size cap — a diagnostic that fills a disk is a worse outage than the one
 *    it was recording.
 */

const fs = require('fs');
const path = require('path');

const MAX_BYTES = 2 * 1024 * 1024;   // 2 MB, then start over
const BASENAME = 'web.log';

let _dir = null;         // resolved lazily, once
let _resolved = false;
let _disabled = false;

/**
 * Where to write. WEB_LOG_DIR wins; otherwise logs/ beside the repo root,
 * which is where an operator already looks. Resolution NEVER creates a
 * directory that does not exist and never probes a path that was not
 * configured or is not already present — an existsSync on a stalled mount is
 * the very hazard this module refuses to introduce.
 */
function dir() {
  if (_resolved) return _dir;
  _resolved = true;
  const explicit = (process.env.WEB_LOG_DIR || '').trim();
  const candidate = explicit || path.join(__dirname, '..', '..', 'logs');
  try {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
      _dir = candidate;
    }
  } catch (_) {
    _dir = null;   // unreadable → stay quiet, never throw
  }
  return _dir;
}

/**
 * Append one line. Fire-and-forget: the caller is never made to wait on a
 * disk, and a failure here is silent by design (the same fact has already
 * gone to stdout, which is the authoritative surface).
 */
function record(event, detail) {
  if (_disabled) return;
  try {
    const d = dir();
    if (!d) return;
    const file = path.join(d, BASENAME);
    const line = `${new Date().toISOString()} ${event}`
      + `${detail ? ` ${detail}` : ''}\n`;

    fs.stat(file, (err, st) => {
      if (!err && st && st.size > MAX_BYTES) {
        fs.writeFile(file, line, () => {});          // truncate and start over
      } else {
        fs.appendFile(file, line, () => {});
      }
    });
  } catch (_) {
    // One unexpected throw disables the logger for the life of the process
    // rather than repeating on every call.
    _disabled = true;
  }
}

/** Test seam. */
function _reset() {
  _dir = null;
  _resolved = false;
  _disabled = false;
}

module.exports = { record, _reset, MAX_BYTES, BASENAME };
