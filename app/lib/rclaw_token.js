'use strict';
/**
 * The $RCLAW token that exists: one record, read, never retyped.
 *
 * `token/config/rclaw.mainnet.json` holds what was read back from Solana
 * mainnet for the mint and what has been announced about the presale (since
 * 2026-10-10, the Smithii sale's terms; a field still null is rendered "not
 * announced yet", never a blank cell). The bot reads the same file
 * (`bot/token/record.py`), so the /token page and the bot's /rclaw card
 * cannot name two mints.
 *
 * A record that cannot be read THROWS. The mint address is the one thing a
 * visitor comes for, and a guessed or defaulted address is worse than none:
 * the route answers 503 with the exception class and the page paints its
 * unread state with no address in it.
 *
 * The website reads its COPY, `app/content/rclaw.mainnet.json`, because the
 * web deploy ships `app/` alone: reading `../../token/config/` answered 503
 * on the live site for a file that was never deployed. The copy is written
 * by `app/scripts/sync_content.js`, and a test fails when it differs from
 * the original the bot reads.
 */

const fs = require('node:fs');
const path = require('node:path');

const RECORD_PATH = path.join(__dirname, '..', 'content', 'rclaw.mainnet.json');

const REQUIRED_KEYS = [
  'name', 'symbol', 'chain', 'cluster', 'mint', 'token_program', 'standard',
  'decimals', 'supply_tokens', 'mint_authority', 'freeze_authority',
  'explorer', 'presale',
];

const BASE58_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

class TokenRecordInvalid extends Error {
  constructor(message) {
    super(message);
    this.name = 'TokenRecordInvalid';
  }
}

/** The record, validated. A missing file or bad JSON throws as itself. */
function readRecord(file) {
  const data = JSON.parse(fs.readFileSync(file || RECORD_PATH, 'utf8'));
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new TokenRecordInvalid('the record is not an object');
  }
  const missing = REQUIRED_KEYS.filter((k) => !Object.prototype.hasOwnProperty.call(data, k));
  if (missing.length) throw new TokenRecordInvalid('the record lacks ' + missing.join(', '));
  if (typeof data.mint !== 'string' || !BASE58_ADDRESS.test(data.mint)) {
    throw new TokenRecordInvalid('the mint is not a base58 address');
  }
  if (!data.presale || typeof data.presale !== 'object' || Array.isArray(data.presale)
      || !Object.prototype.hasOwnProperty.call(data.presale, 'status')) {
    throw new TokenRecordInvalid('the presale block has no status');
  }
  return data;
}

/**
 * What the public route serves: the record minus its editor notes and the
 * basis column. Nothing here is a dollar figure or an account; a presale
 * price, once announced, is a term of the sale and arrives as whatever
 * string the announcement uses.
 */
function publicRecord(record) {
  const out = {};
  for (const [k, v] of Object.entries(record)) {
    if (k.startsWith('_') || k === 'basis') continue;
    out[k] = v;
  }
  if (out.presale && typeof out.presale === 'object') {
    const presale = {};
    for (const [k, v] of Object.entries(out.presale)) {
      if (!k.startsWith('_')) presale[k] = v;
    }
    out.presale = presale;
  }
  return out;
}

module.exports = { RECORD_PATH, REQUIRED_KEYS, BASE58_ADDRESS, readRecord, publicRecord, TokenRecordInvalid };
