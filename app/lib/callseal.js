'use strict';
/**
 * Provable Calls — pre-commitment receipts for engine signals.
 *
 * A call is SEALED the moment it reaches the platform: a canonical JSON
 * payload of the DECISION-TIME facts (never outcome fields) is hashed with
 * SHA-256, and both payload and hash are stored beside the signal. Outcomes
 * attach to the SAME row later without touching the seal, so:
 *
 *   sha256(seal_payload) === seal            → the receipt is intact
 *   seal_payload fields === displayed fields → nothing was rewritten
 *
 * Anyone re-derives the hash in their browser on /call/:key — no backdated
 * calls, no deleted losers, no cherry-picking. "Don't trust the screenshot.
 * Verify the call."
 *
 * v1 honesty (stated on the verify page too): the seal proves internal
 * consistency and is broadcast at decision time (feed/SSE), so copies leave
 * the platform immediately; third-party timestamping (daily on-chain root
 * anchoring) is the planned v2.
 */

const crypto = require('crypto');

/**
 * Canonical decision-time payload. Key insertion order IS the canonical
 * contract (v:1) — clients hash the served string verbatim, so there is no
 * re-canonicalization to drift.
 */
function canonicalPayload(s) {
  return JSON.stringify({
    v: 1,
    signal_key: String(s.signal_key),
    symbol: String(s.symbol),
    direction: String(s.direction),
    entry_price: Number(s.entry_price) || 0,
    stop_loss: Number(s.stop_loss) || 0,
    take_profit: Number(s.take_profit) || 0,
    confidence: Number(s.confidence) || 0,
    pattern: s.pattern ? String(s.pattern) : null,
    regime: s.regime ? String(s.regime) : null,
    created_at: new Date(s.created_at || Date.now()).toISOString(),
  });
}

function sealOf(payload) {
  return crypto.createHash('sha256').update(payload, 'utf8').digest('hex');
}

/** { seal_payload, seal } for a decision-time signal object. */
function sealCall(s) {
  const seal_payload = canonicalPayload(s);
  return { seal_payload, seal: sealOf(seal_payload) };
}

/**
 * v2 — Arena trader receipts. A paper trade is sealed at OPEN time: the fill
 * facts are hashed before anyone knows how the trade ends, and the seal rides
 * the position onto the closed-trade row untouched. §4: the payload is served
 * on a PUBLIC verify page, so it carries prices/leverage/times ONLY — never
 * margin or any vUSDT amount. The handle is whatever the trader had opted
 * into at open time (null = anonymous receipt, still verifiable by whoever
 * holds the key).
 */
function canonicalArenaPayload(t) {
  return JSON.stringify({
    v: 2,
    kind: 'arena_trade',
    trade_key: String(t.trade_key),
    handle: t.handle ? String(t.handle) : null,
    symbol: String(t.symbol),
    direction: String(t.direction),
    entry: Number(t.entry) || 0,
    leverage: Number(t.leverage) || 0,
    tp: t.tp == null ? null : Number(t.tp),
    sl: t.sl == null ? null : Number(t.sl),
    opened_at: new Date(t.opened_at || Date.now()).toISOString(),
  });
}

/** Unguessable content key for one paper trade — the /call/:key address. */
function newTradeKey() {
  return 'arena:' + crypto.randomBytes(9).toString('hex');
}

/** { seal_payload, seal } for an arena open. */
function sealArenaTrade(t) {
  const seal_payload = canonicalArenaPayload(t);
  return { seal_payload, seal: sealOf(seal_payload) };
}

module.exports = {
  canonicalPayload, sealOf, sealCall,
  canonicalArenaPayload, sealArenaTrade, newTradeKey,
};
