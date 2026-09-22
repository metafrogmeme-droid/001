'use strict';
//
// Read the repo-root `.env` into process.env — the half of the deployment that
// never did.
//
// THE OUTAGE THIS EXISTS FOR
//
// On 2026-09-22 the website returned 520 for hours. `.env` said `PORT=3000`,
// which was correct, and Node had never read it: the Python side calls
// `load_dotenv()` at import, the Express side required no such thing, so
// `process.env.PORT` was undefined and `server.js` fell back to its default of
// **8080** — which is the BOT's aiohttp gateway port on the same box. The
// listen lost the race, `EADDRINUSE` went unhandled, the process exited before
// binding anything, and Cloudflare had no origin to route to.
//
// The operator's configuration was present, correct, and unread. Two halves of
// one deployment disagreed about whether `.env` is configuration, and the half
// that said no was the half that serves the website.
//
// THE PRECEDENCE IS THE WHOLE DESIGN
//
// A value already in the environment ALWAYS wins. This only fills in what is
// missing — the same rule `secrets_vault.restoreFromVault()` states one layer
// down, and for the same reason: an operator who exports something at the
// launch site means it, and a file on disk must never silently overrule the
// command they just ran. Load order in `server.js` is therefore
//
//     real environment   >   .env   >   the encrypted vault
//
// which falls out of each step only filling gaps, in that sequence.
//
// IT CANNOT TAKE THE SITE DOWN
//
// Every failure is swallowed and nothing is ever thrown. A missing file is the
// ordinary case on a container that injects real env vars; an unreadable or
// malformed one must not become an outage, because a loader installed to END a
// boot failure would then be causing one. `loadEnvFile()` answers WHICH keys it
// filled so the caller can say so out loud — and never a value, not in a
// return, not in a log.

const fs = require('fs');
const path = require('path');

/** Repo root: this file is `<repo>/app/lib/env_file.js`. */
const REPO_ROOT = path.join(__dirname, '..', '..');

/**
 * Parse `.env` text into pairs. Deliberately conservative — this is a
 * fallback reader, not a shell.
 *
 * Handles `KEY=value`, a leading `export `, surrounding single or double
 * quotes, and `KEY=` (an explicit empty value). A line without `=`, or with an
 * empty key, is skipped rather than guessed at.
 *
 * An unquoted value keeps every character after the first `=`, including `#`.
 * Stripping a trailing "comment" would silently truncate any secret containing
 * a hash — which is most of them, and losing half a key is worse than keeping
 * a comment nobody wrote.
 */
function parseEnv(text) {
  const out = [];
  for (const raw of String(text == null ? '' : text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const body = line.startsWith('export ') ? line.slice(7).trim() : line;
    const eq = body.indexOf('=');
    if (eq <= 0) continue;
    const key = body.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let value = body.slice(eq + 1).trim();
    const q = value[0];
    if ((q === '"' || q === "'") && value.length >= 2 && value[value.length - 1] === q) {
      value = value.slice(1, -1);
    }
    out.push([key, value]);
  }
  return out;
}

/**
 * Fill `process.env` from `.env` for keys it does not already carry.
 *
 * @returns {string[]} the KEY NAMES filled, in file order. Never values.
 */
function loadEnvFile(file) {
  const target = file || path.join(REPO_ROOT, '.env');
  let text;
  try {
    text = fs.readFileSync(target, 'utf8');
  } catch (err) {
    return [];   // absent or unreadable is the ordinary case, never an error
  }
  const filled = [];
  try {
    for (const [key, value] of parseEnv(text)) {
      // `in` rather than truthiness: an exported empty string is a decision,
      // and overwriting it from a file would overrule the launch site.
      if (key in process.env) continue;
      process.env[key] = value;
      filled.push(key);
    }
  } catch (err) {
    return filled;   // partial is honest; throwing here would be an outage
  }
  return filled;
}

module.exports = { loadEnvFile, parseEnv, REPO_ROOT };
