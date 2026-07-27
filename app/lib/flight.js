'use strict';
/**
 * Agent Flight Recorder — shared helpers for the authed (routes/guardian.js) and
 * PUBLIC (routes/public_flight.js) views of the sealed decision ledger.
 *
 * The ledger is a SHA-256 hash-chained, signed append-only log held bot-side;
 * the engine runs the real cryptographic verify() and pushes recent
 * DECISION↔OUTCOME records here. These helpers do NOT re-prove the chain — they
 * (a) sanity-check the visible window's hashes/sequence for transparency, and
 * (b) strip every dollar figure so the record is §4-safe to serve publicly:
 * percent / ratio / R-multiple only, never a dollar P&L or position size.
 */

const HEX64 = /^[0-9a-f]{64}$/;

/**
 * Transparency pass over the synced window (NOT the cryptographic proof — that
 * is the engine's verify(), surfaced separately as chain.ok). Confirms every
 * record carries a well-formed entry hash and that sequence numbers are unique
 * and strictly increasing; reorders / drops / malformed hashes show up here.
 */
function inspectWindow(records) {
  const problems = [];
  let lastSeq = -Infinity;
  let hashed = 0;
  const list = Array.isArray(records) ? records : [];
  for (let i = 0; i < list.length; i++) {
    // records arrive newest-first; walk oldest-first for monotonic sequence.
    const r = list[list.length - 1 - i];
    const ch = (r && r.chain) || {};
    const seq = ch.sequence;
    if (typeof seq === 'number') {
      if (seq <= lastSeq) problems.push(`sequence not increasing at ${seq}`);
      lastSeq = seq;
    }
    if (ch.entry_hash && HEX64.test(String(ch.entry_hash))) hashed++;
    else if (ch.entry_hash) problems.push(`malformed entry_hash at seq ${seq}`);
  }
  return { records_checked: list.length, well_formed_hashes: hashed, problems };
}

// Any key naming a dollar quantity — stripped from public records. We keep the
// key-name test broad (anything with "usd", plus a handful of bare names) so a
// new dollar field the engine adds later is redacted by default, not leaked.
const DOLLAR_KEY = /(usd|equity|balance|notional|margin|collateral|dollars?|account_value|pnl|cash|funds|wallet_value)/i;
// …and the keys that name a RATIO rather than an amount, which must survive:
// percent/R-multiple outcomes are the entire content of the public record.
//
// WHY `pnl` REPLACED `pnl_abs`. The comment above promises redaction "by
// default" for "a new dollar field the engine adds later", and the bare-name
// list did not deliver it: `pnl`, `net_pnl`, `daily_pnl`, `realized_pnl` and
// `total_pnl` all sailed through, because only `pnl_abs` and the `usd` suffix
// were listed. Broadening to `pnl` catches every one — and then needs this
// allowlist, because `pnl_pct` would otherwise be stripped too and the public
// flight view is made of exactly that field.
//
// Suffix-anchored on purpose: `pnl_pct` survives, `pct_pnl_usd` does not. A key
// that names a currency anywhere is never exempted, so a hypothetical
// `usd_ratio` stays stripped.
const RATIO_KEY = /(_pct|_percent|_ratio|_bps|_r|_multiple|_rate)$/i;
const CURRENCY_TOKEN = /(usd|dollars?|cash|equity|balance)/i;
const isRatioKey = (k) => RATIO_KEY.test(k) && !CURRENCY_TOKEN.test(k);
// A currency amount embedded in a free-text string (e.g. "closed +$12.50").
const DOLLAR_TEXT = /\$\s?-?\d[\d,]*(\.\d+)?/g;

function scrub(value) {
  if (value == null) return value;
  if (Array.isArray(value)) return value.map(scrub);
  if (typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value)) {
      if (DOLLAR_KEY.test(k) && !isRatioKey(k)) continue;  // drop amounts, keep ratios
      out[k] = scrub(value[k]);
    }
    return out;
  }
  // Redact with a $-free marker so the scrubbed text itself carries no '$'.
  if (typeof value === 'string') return value.replace(DOLLAR_TEXT, '⋯').trim();
  return value;
}

/**
 * Public-safe copy of one flight record: same decision chain (inputs, reasoning,
 * voters, risk verdict + checks, provenance, geometry prices, outcome as
 * percent/R, ledger hashes) with every dollar figure removed. Prices (entry/sl/
 * tp) are public market data — already exposed by /api/insight — so they stay.
 */
function sanitizeRecord(rec) {
  if (!rec || typeof rec !== 'object') return rec;
  return scrub(rec);
}

module.exports = { HEX64, inspectWindow, sanitizeRecord, scrub, DOLLAR_KEY };
