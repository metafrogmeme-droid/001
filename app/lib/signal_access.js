'use strict';
/**
 * Who sees a live call: a signed-in member sees the trade; everyone sees a
 * resolved one.
 *
 * The engine's calls were public. Nine anonymous surfaces handed out the
 * direction, entry, stop and target of every call the moment it was made:
 * the signal stream, the sealed-call receipt, the scan's entry cards, the
 * mind-stream feed and its live stream, the MCP and ERC-8257 tool doors, the
 * embeddable board, a strategy's "scan now" plan and the flight records. A
 * call is now a member's while it is live.
 *
 * WHAT STAYS PUBLIC, AND WHY.
 *
 * - A RESOLVED call, in full. The track record is the proof that a call was
 *   worth having, and a record that hid its own calls would be a claim
 *   nobody could check. A call is resolved when its status is one of the
 *   model's terminal words or an outcome has been read for it.
 * - THE SEAL of a live call. Provable Calls is a commitment: the hash is
 *   published when the call is made and the payload it commits to is
 *   revealed once the call resolves, so anyone can still check that what
 *   was revealed is what was sealed before the market moved. The payload is
 *   withheld until then; the seal never is.
 * - The aggregate statistics (win rate, the setup board, calibration). They
 *   carry no call.
 *
 * WHAT A VISITOR SEES OF A LIVE CALL: that it exists, on which market, when,
 * and for how long it stays live. Not its side: "LONG BTC" is the trade.
 *
 * A SAMPLE ON THE LISTS (2026-10-18, the operator's call). A visitor sees
 * the newest TEASER_COUNT live calls in full on the two lists a visitor
 * browses, the signal stream and the scan's setup cards, and the first
 * TEASER_COUNT deep-scan reads, each marked `teaser`; the rest are locked
 * with the count and a free sign-up. The sample is the same for every
 * visitor and every query: it is chosen from the newest live calls overall
 * (`teaserKeys`), never from the rows a filter returned, or a filter on one
 * market would open that market's live call. Single-call doors (a receipt,
 * the thesis page, a flight record, a strategy's board, the tool doors) stay
 * locked: the sample is a look at the list, and the call itself is a
 * member's.
 *
 * ONE READING. Every route asks the functions below. Each takes the viewer's
 * answer as its first argument and opens only on `=== true`, the same strict
 * test `scanFor` applies to the operator check: a request object or a user
 * row passed by habit is truthy, and would have served the trade.
 *
 * UNKNOWN IS LOCKED. A status word this build does not know is not read as
 * resolved: the signal model files an unknown word as final so that no page
 * offers to act on it, and the same word must not open the trade here.
 */

const SignalStatus = require('../public/js/signal-status-model.js');
const { signalOutcome, publicSignal } = require('./public_signal');

/**
 * How many live calls a visitor sees in full on a list, as a sample. One
 * number for every list that teases; a page reads it from the payload's
 * `teaser` block rather than keeping a copy.
 */
const TEASER_COUNT = 3;

/** Where a visitor goes to sign up: the home page, on its create-account tab. */
const JOIN_HREF = '/#join';

/** The sentence a locked call carries, for any page that has no copy of its own. */
const LOCK_NOTE = 'Sign in to see this call. Its direction, entry, stop and '
  + 'target are shown to signed-in members while it is live, and every call '
  + 'is published in full once it resolves.';

/**
 * Is this request from a signed-in member? `req.user` is set only by
 * `authMiddleware` or `optionalAuth` (app/auth.js), from a verified, current
 * session. A route that never ran either reads as anonymous.
 */
function signedIn(req) {
  return Boolean(req && req.user && typeof req.user === 'object');
}

/** Has this signal row resolved? A terminal word this build knows, or an outcome read. */
function signalResolved(row) {
  if (!row || typeof row !== 'object') return false;
  if (signalOutcome(row.pnl) !== null) return true;
  if (typeof row.outcome === 'string' && row.outcome) return true;
  const st = SignalStatus.status(row);
  return st.known === true && st.final === true;
}

// What a visitor sees of a live call. An allowlist, like PUBLIC_FIELDS: a
// column added to the row later is a member's until somebody adds it here.
const LOCKED_SIGNAL_FIELDS = ['signal_key', 'symbol', 'status', 'created_at',
  'expires_at', 'seal'];

function lockedSignal(row) {
  const out = {};
  for (const k of LOCKED_SIGNAL_FIELDS) {
    if (row && k in row) out[k] = row[k];
  }
  out.outcome = null;
  out.locked = true;
  return out;
}

/**
 * The keys of the live calls a visitor is shown as the sample: the first
 * TEASER_COUNT unresolved rows of `rows`, which must be the newest rows
 * overall, newest first, read with no visitor filter applied.
 */
function teaserKeys(rows) {
  const keys = new Set();
  for (const r of Array.isArray(rows) ? rows : []) {
    if (keys.size >= TEASER_COUNT) break;
    if (r && typeof r === 'object' && r.signal_key && !signalResolved(r)) keys.add(String(r.signal_key));
  }
  return keys;
}

/**
 * One signal row for this viewer: the public row, or its locked shape. A
 * live row whose key is in `teased` (a Set from `teaserKeys`) is shown in
 * full and marked `teaser`; anything else, a missing set included, is not.
 */
function signalFor(seesCalls, row, teased) {
  if (!row || typeof row !== 'object') return row;
  if (seesCalls === true || signalResolved(row)) return publicSignal(row);
  if (teased instanceof Set && row.signal_key && teased.has(String(row.signal_key))) {
    return { ...publicSignal(row), teaser: true };
  }
  return lockedSignal(row);
}

/** The block a teased list carries so a page can say what the sample is. */
function teaserBlock(shown, locked) {
  return { shown, locked, of: TEASER_COUNT, join: JOIN_HREF };
}

/**
 * A `/api/call` receipt for this viewer. A live engine signal keeps its
 * seal, its sealing time and its anchor, and loses the payload and the
 * decision-time values until it resolves.
 *
 * Every other kind passes unchanged. A pre-signature scan receipt is not a
 * call. An Arena receipt is a member's own paper trade, and its key (72
 * random bits) reaches anyone only because its owner shared it: showing an
 * open trade is the owner's choice, not the engine's call.
 *
 * Resolution is the lookup's own reading (`outcome.resolved`, set from the
 * row by `lookupCall`), and anything but `true` is live: a receipt that does
 * not say it resolved does not open.
 */
const CALL_KINDS = new Set(['signal']);

function receiptFor(seesCalls, body) {
  if (!body || typeof body !== 'object' || seesCalls === true) return body;
  if (!CALL_KINDS.has(body.kind)) return body;
  const outcome = body.outcome || {};
  if (outcome.resolved === true) return body;
  const cur = body.current || {};
  const current = {};
  for (const k of ['signal_key', 'symbol', 'created_at']) {
    if (k in cur) current[k] = cur[k];
  }
  return {
    kind: body.kind,
    seal: body.seal,
    seal_payload: null,
    sealed_at: body.sealed_at,
    anchor: body.anchor === undefined ? null : body.anchor,
    current,
    outcome: { status: outcome.status === undefined ? null : outcome.status },
    locked: true,
    note: 'The seal is public now; what it commits to is revealed when the '
      + 'call resolves, and anyone can then check the two match. ' + LOCK_NOTE,
  };
}

/**
 * The feed's event types that carry a live call: the thesis (side, levels,
 * confidence), the agent's open (side and levels) and a trailing stop's
 * move (the new stop). A close, a scan summary, an alert and a stance carry
 * none and pass unchanged.
 */
const LOCKED_FEED_TYPES = Object.freeze({
  thesis: 'New call',
  trade_open: 'Position opened',
  sl_move: 'Stop moved',
});

function feedEventFor(seesCalls, ev) {
  if (!ev || typeof ev !== 'object' || seesCalls === true) return ev;
  const word = Object.prototype.hasOwnProperty.call(LOCKED_FEED_TYPES, ev.event_type)
    ? LOCKED_FEED_TYPES[ev.event_type] : null;
  if (!word) return ev;
  const sym = String(ev.symbol || '').slice(0, 32);
  const out = {
    event_type: ev.event_type,
    severity: ev.severity,
    symbol: sym,
    title: sym ? `${word} on ${sym}` : word,
    body: 'Sign in to see the levels.',
    data: {},
    locked: true,
  };
  if ('created_at' in ev) out.created_at = ev.created_at;
  return out;
}

/**
 * The scan payload for this viewer. Market readings stay (price, RSI,
 * volume, the book, and the setup word a market carries, which names no
 * side, as a locked signal row names its market); every reading of what the
 * engine would DO is a member's: the entry cards, each market's side and
 * score, and the operator's open positions beyond their symbol.
 */
const SCAN_SYMBOL_CALL_KEYS = ['direction', 'score'];

const baseSym = (s) => String(s || '').toUpperCase().replace(/[/:].*$/, '').replace(/USDT$/, '');

function scanCallsFor(seesCalls, scan) {
  if (!scan || typeof scan !== 'object' || seesCalls === true) return scan;
  const out = { ...scan };
  // The sample: the first TEASER_COUNT cards in the scan's own order (the
  // producer's, best first), in full; the rest name their market.
  const teased = new Set();
  if (Array.isArray(scan.entry_cards)) {
    out.entry_cards = scan.entry_cards.map((c, i) => {
      if (i < TEASER_COUNT && c && typeof c === 'object') {
        teased.add(baseSym(c.symbol));
        return { ...c, teaser: true };
      }
      return { symbol: c && c.symbol, locked: true };
    });
    const shown = Math.min(TEASER_COUNT, scan.entry_cards.length);
    out.entry_cards_teaser = teaserBlock(shown, scan.entry_cards.length - shown);
  }
  // A deep scan reads patterns and names no side, and it is a sample too.
  const ds = scan.deepscan;
  if (ds && typeof ds === 'object' && Array.isArray(ds.hits)) {
    const shown = Math.min(TEASER_COUNT, ds.hits.length);
    out.deepscan = {
      ...ds,
      hits: ds.hits.map((h, i) => (i < TEASER_COUNT
        ? { ...h, teaser: true }
        : { symbol: h && h.symbol, locked: true })),
      teaser: teaserBlock(shown, ds.hits.length - shown),
    };
  }
  if (scan.symbols && typeof scan.symbols === 'object') {
    const symbols = {};
    for (const k of Object.keys(scan.symbols)) {
      const row = scan.symbols[k];
      if (!row || typeof row !== 'object') { symbols[k] = row; continue; }
      // A sampled card's market keeps its side: the card beside it says it.
      if (teased.has(baseSym(k))) { symbols[k] = row; continue; }
      const kept = {};
      for (const f of Object.keys(row)) {
        if (!SCAN_SYMBOL_CALL_KEYS.includes(f)) kept[f] = row[f];
      }
      kept.locked = true;
      symbols[k] = kept;
    }
    out.symbols = symbols;
  }
  const cb = scan.circuit_breaker;
  if (cb && typeof cb === 'object' && Array.isArray(cb.open_positions)) {
    out.circuit_breaker = {
      ...cb,
      open_positions: cb.open_positions.map((p) => ({
        symbol: p && p.symbol, locked: true })),
    };
  }
  out.calls_locked = true;
  out.calls_note = LOCK_NOTE;
  return out;
}

/**
 * A strategy's "scan now" answer for this viewer: `{public, board}` as the
 * bot's gateway sends it (`handle_strategy_scan_public`). Every reading on
 * the board is the strategy's call now (a side, a signal, the market it
 * would hold, the limit it would rest, and the verdict that says so), so a
 * visitor gets the board's frame (which rule, which bar, which markets were
 * read and which could not be) and none of its rows.
 */
const BOARD_FRAME_KEYS = ['preset', 'label', 'command', 'rule', 'bar_close',
  'bar_close_ms', 'read', 'unread', 'places'];

function strategyScanFor(seesCalls, data) {
  if (!data || typeof data !== 'object' || seesCalls === true) return data;
  const board = data.board;
  if (!board || typeof board !== 'object') return data;
  const locked = {};
  for (const k of BOARD_FRAME_KEYS) {
    if (k in board) locked[k] = board[k];
  }
  locked.locked = true;
  locked.verdict = LOCK_NOTE;
  return { ...data, board: locked };
}

/**
 * A flight record for this viewer. A decision whose trade has a result is
 * history and stays whole, and so is one the risk gate refused: a refused
 * idea is never offered, so it was never a call (the engine returns before
 * publishing it, and `assemble_incident_records` files every outcome word
 * starting REJECTED as the gate's). Any other decision without a result is
 * a live call, an executed one or a failed attempt whose idea is still
 * pending, and its idea and the narration drawn from it are a member's.
 */
function flightRecordFor(seesCalls, rec) {
  if (!rec || typeof rec !== 'object' || seesCalls === true) return rec;
  if (rec.result !== null && rec.result !== undefined) return rec;
  if (typeof rec.outcome === 'string' && rec.outcome.startsWith('REJECTED')) return rec;
  const out = { ...rec };
  out.idea = { locked: true };
  if ('explanation' in out) out.explanation = null;
  out.locked = true;
  return out;
}

/**
 * The cache header for a response whose body depends on the viewer. A shared
 * cache (a CDN, a proxy) keyed on the URL alone would hand one viewer's
 * answer to the next, so it is never `public`.
 */
function viewerCacheControl(res, maxAgeSeconds) {
  res.set('Cache-Control', `private, max-age=${Math.max(0, maxAgeSeconds | 0)}`);
  res.vary('Cookie');
  res.vary('Authorization');
}

module.exports = {
  LOCK_NOTE, LOCKED_SIGNAL_FIELDS, LOCKED_FEED_TYPES, SCAN_SYMBOL_CALL_KEYS,
  TEASER_COUNT, JOIN_HREF, teaserKeys, teaserBlock,
  signedIn, signalResolved, lockedSignal, signalFor, receiptFor, feedEventFor,
  scanCallsFor, strategyScanFor, flightRecordFor, viewerCacheControl,
};
