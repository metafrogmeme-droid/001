'use strict';

/**
 * The bot's published sealing key — the thing that turns the connect form on.
 *
 * The bot owns an RSA keypair (`bot/utils/creds_sealing.py`), keeps the
 * private half beside its master key, and POSTs the public half here over the
 * bot-secret sync channel on its first credential pull after boot and hourly
 * after. `routes/credentials.js` seals every submission to it, so this app
 * stores exchange keys it cannot itself read.
 *
 * IT IS PERSISTED, not held in memory, and that is the whole reason this file
 * exists rather than a module-level variable in the sync route. The web app
 * restarts on every deploy and on an idle scale-to-zero; a key that lived only
 * in the process would leave the connect form dead until the bot's next hourly
 * publish, which is the same "the form does not save" report in a new costume.
 *
 * READ FAILURE IS NOT ABSENCE. `readSealingKey()` returns null only for a
 * table that genuinely holds no key, and THROWS when the read itself failed —
 * so a database outage cannot render as "the bot has not published its key
 * yet", which is a different problem with a different fix. The caller decides
 * what to show; it must not be handed a confident negative to show.
 */

const { pool } = require('../db');
const { kidFor, publicKeyFrom, SEAL_ALG } = require('./creds_crypto');

/**
 * Validate a record the bot published. Returns the normalised record; throws
 * with a reason a log can carry.
 *
 * The bot-secret channel authenticates the SENDER and says nothing about the
 * CONTENT. A record accepted unchecked would be found out at submit time, on a
 * user's screen, with their API keys already typed in.
 */
function vetRecord(rec) {
  try {
    const r = rec || {};
    const alg = String(r.alg || '');
    if (alg !== SEAL_ALG) {
      throw new Error(`unsupported sealing algorithm ${JSON.stringify(alg)}`);
    }
    const pem = String(r.pem || '');
    publicKeyFrom(pem);               // throws on a non-RSA / undersized key
    const kid = kidFor(pem);
    if (r.kid && String(r.kid) !== kid) {
      throw new Error(`kid ${r.kid} does not match the published key (${kid})`);
    }
    return { kid, pem, alg };
  } catch (err) {
    // TAGGED, not string-matched. The route has to tell "this key is unusable"
    // (the bot's to fix; republishing the same record cannot help) from "the
    // write failed" (ours; the next publish retries into it), and deciding
    // that by grepping err.message would answer wrongly the first time a
    // driver error happened to contain the word "key".
    err.unusableSealingKey = true;
    throw err;
  }
}

/** Persist the published key (single row). Returns the normalised record. */
async function storeSealingKey(rec) {
  const clean = vetRecord(rec);
  await pool.execute(
    'REPLACE INTO bot_sealing_key (id, kid, pem, alg) VALUES (1, ?, ?, ?)',
    [clean.kid, clean.pem, clean.alg]);
  return clean;
}

/**
 * The key the bot published, or null when it genuinely has not published one.
 *
 * Throws on a read error — see the header. Also throws on a stored row that no
 * longer vets (an algorithm this build does not implement, a key that cannot
 * be parsed): sealing to it is not an option, and returning null would report
 * a broken record as a bot that never called.
 */
async function readSealingKey() {
  const [rows] = await pool.execute(
    'SELECT kid, pem, alg, updated_at FROM bot_sealing_key WHERE id = 1');
  const row = rows && rows[0];
  if (!row || !row.pem) return null;
  const clean = vetRecord(row);
  return { ...clean, updated_at: row.updated_at || null };
}

module.exports = { storeSealingKey, readSealingKey, vetRecord };
