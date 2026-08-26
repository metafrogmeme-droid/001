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

/**
 * A number, or null when there isn't one.
 *
 * v1 uses `Number(x) || 0`, which seals an unreadable confidence as a
 * measured zero — the shape CLAUDE.md's table names, sitting in the canonical
 * contract itself. It cannot be fixed there: the payload string IS the thing
 * every historical seal was computed over, so changing a single byte of v1
 * invalidates every receipt ever issued. A new kind is the only place the fix
 * can go, and this is it.
 */
function numOrNull(x) {
  if (x === null || x === undefined || x === '') return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

/**
 * v4 — a signal call WITH ITS REASONING SEALED.
 *
 * `v` in this file is a KIND discriminator, not a version counter: 1 is the
 * original signal call, 2 arena_trade, 3 duel_pick. So this is 4, and the
 * `kind` field spells it out rather than leaving 1 and 4 to be told apart by
 * their key sets.
 *
 * WHY THE REASONING BELONGS INSIDE THE HASH. `thesis` has been transmitted at
 * decision time and stored since the stream existed — `website_sync.py` sends
 * it in the same POST that gets sealed — and it is published by
 * `lib/public_signal.js`, `routes/signals.js` and `routes/copy.js`. It was
 * never sealed. In practice it is immutable (the sync's ON DUPLICATE KEY
 * touches only status, pnl and resolved_at), but nothing PROVED that to a
 * reader: an edit to the row would have been undetectable, and an unverifiable
 * narrative published beside an unforgeable receipt is exactly what this
 * product exists to make impossible. "Every call is hashed before the market
 * moves" was true of the numbers and not of the reason for them.
 *
 * Inline rather than a digest. A digest would need the reasoning served as a
 * second artifact and hashed by the client to compare — one more moving part,
 * and one more thing that can go missing. A thesis is a sentence; inline means
 * the client verifies it VERBATIM with the code it already runs,
 * `sha256(seal_payload)`, and the verification path stays single. That path is
 * the product.
 *
 * Key insertion order is the canonical contract, as in v1: clients hash the
 * served string verbatim, so there is no re-canonicalisation to drift.
 */
function canonicalSignalPayload(s) {
  return JSON.stringify({
    v: 4,
    kind: 'signal_call',
    signal_key: String(s.signal_key),
    symbol: String(s.symbol),
    direction: String(s.direction),
    entry_price: numOrNull(s.entry_price),
    stop_loss: numOrNull(s.stop_loss),
    take_profit: numOrNull(s.take_profit),
    confidence: numOrNull(s.confidence),
    pattern: s.pattern ? String(s.pattern) : null,
    regime: s.regime ? String(s.regime) : null,
    // Absent reasoning seals as null, NOT "". An empty string asserts "the
    // reasoning was empty"; null says "none was recorded". The sync coalesces
    // a missing reasoning to "" upstream, so without this the difference would
    // be hashed away permanently.
    thesis: s.thesis ? String(s.thesis) : null,
    created_at: new Date(s.created_at || Date.now()).toISOString(),
  });
}

/**
 * { seal_payload, seal } for a decision-time signal object.
 *
 * New seals are v4. Old rows keep the exact v1 string they were sealed with —
 * verification hashes the STORED payload, never a recomputed one, so both
 * kinds verify through the same client code with no migration and no reseal.
 */
function sealCall(s) {
  const seal_payload = canonicalSignalPayload(s);
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

/**
 * v3 — Daily Duel calls. A call is sealed the moment it is made, long before
 * the market answers. The seal is what makes the duel board worth reading: it
 * fixes the direction, the price it was called from and the horizon it runs
 * to, so a good record cannot be assembled after the fact.
 *
 * §4: this payload is servable on a public verify page, so it carries the
 * round, the call, the market price and the times only — never a balance or an
 * amount of anything, and never the agent's own stance, which other players
 * must not be able to read out of somebody else's receipt.
 */
function canonicalDuelPayload(p) {
  return JSON.stringify({
    v: 3,
    kind: 'duel_pick',
    round_id: Number(p.round_id),
    day: String(p.day),
    symbol: String(p.symbol),
    handle: p.handle ? String(p.handle) : null,
    pick: String(p.pick),
    entry_price: Number(p.entry_price),
    resolves_at: new Date(p.resolves_at).toISOString(),
    picked_at: new Date(p.picked_at || Date.now()).toISOString(),
  });
}

/** { seal_payload, seal } for one duel call. */
function sealDuelPick(p) {
  const seal_payload = canonicalDuelPayload(p);
  return { seal_payload, seal: sealOf(seal_payload) };
}

/**
 * v5 — a PRE-SIGNATURE scan, sealed before the transaction was signed.
 *
 * `xray_transaction` and `scan_transaction` both end their own descriptions
 * with "nothing is stored". That is a privacy promise and it is also why no
 * agent can ever show what it was told before it acted: the verdict evaporates.
 * Sealing it turns "we warned you" into something a third party can check.
 *
 * THE INPUT IS COMMITTED TO BY HASH, NEVER CARRIED
 *
 * Calldata holds destination addresses and amounts, and this payload is served
 * on a PUBLIC verify page and hashed into a PUBLIC daily root. So the seal
 * commits to `input_sha256` and the byte length — enough for anyone holding
 * the original calldata to prove this receipt is about THAT transaction, and
 * useless to anyone who does not. Same reasoning that kept `owner_user_id` out
 * of the agent claim.
 *
 * `deterministic` IS THE MOST IMPORTANT FIELD HERE
 *
 * An xray decode is reproducible: anyone can re-run it on the same calldata
 * and get the same actions, forever, so the receipt proves what the
 * transaction MEANT. A firewall text scan is a heuristic, so its receipt
 * proves only what we SAID. Those are different claims and a reader must never
 * have to guess which one they are holding — so the payload states it rather
 * than leaving it to be inferred from `tool`.
 *
 * `unknown` IS CARRIED THROUGH FOR THE SAME REASON THE DECODER HAS IT
 *
 * The decoder answers UNKNOWN outside its known selector set and says in its
 * own words that "unknown is not the same as safe". A sealed, anchored,
 * official-looking receipt reading "nothing flagged" over calldata nobody
 * decoded is the worst thing this feature could produce, so the flag rides
 * INSIDE the hashed bytes where no renderer can drop it.
 */
function canonicalScanPayload(s) {
  return JSON.stringify({
    v: 5,
    kind: 'presign_scan',
    scan_key: String(s.scan_key),
    tool: String(s.tool),
    deterministic: !!s.deterministic,
    input_sha256: String(s.input_sha256),
    input_bytes: Number(s.input_bytes) || 0,
    // The decoded actions (xray) or matched patterns (firewall), as ids only —
    // the prose is a rendering concern and would drift between versions.
    actions: Array.isArray(s.actions) ? s.actions.map(String) : [],
    flags: Array.isArray(s.flags) ? s.flags.map(String) : [],
    // Three-valued and hashed: true = the decoder did not recognise it,
    // false = it did, null = the tool does not answer this question at all.
    unknown: s.unknown == null ? null : !!s.unknown,
    // The agent this scan was run for, when a bound key presented one.
    agent_slug: s.agent_slug ? String(s.agent_slug) : null,
    scanned_at: new Date(s.scanned_at || Date.now()).toISOString(),
  });
}

/** `{ seal_payload, seal }` for one pre-signature scan. */
function sealScan(s) {
  const seal_payload = canonicalScanPayload(s);
  return { seal_payload, seal: sealOf(seal_payload) };
}

/** sha256 of the exact bytes a caller sent, for `input_sha256`. */
function inputDigest(text) {
  const buf = Buffer.from(String(text == null ? '' : text), 'utf8');
  return { sha256: crypto.createHash('sha256').update(buf).digest('hex'),
           bytes: buf.length };
}

module.exports = {
  canonicalPayload, canonicalSignalPayload, numOrNull, sealOf, sealCall,
  canonicalArenaPayload, sealArenaTrade, newTradeKey,
  canonicalDuelPayload, sealDuelPick,
  canonicalScanPayload, sealScan, inputDigest,
};
