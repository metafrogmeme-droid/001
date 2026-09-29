'use strict';
/**
 * Practice-follow planner — mirror the engine's live signal stream into the
 * PAPER Arena account. The user picks a per-trade margin + leverage; each new
 * signal opens a paper position at the LIVE mark (never the stale signal
 * price — honesty over flattery: your fill is what you'd actually get now).
 *
 * §4 by construction: this plans PAPER opens only — the arena has no route to
 * any live venue, and enabling follow can never move real funds. Deciding is
 * pure (signals in / plan out) so every skip rule is exactly testable; the
 * route feeds live ticker marks and does the DB writes.
 */

const arena = require('./arena');
const { exchangeSymbol } = require('./agent_match');
const { callBlock } = require('./arena_signal_trade');

/**
 * The sweep is lazy -- it runs when the follower next reads their account --
 * so "each new signal" arrives here however old it has become by then. Two
 * rules the one-signal open route already made were missing, and both come
 * from the one reading of a call (`callBlock`): a call older than the Arena's
 * age bound, or one that has already ENDED (reached its target or stop, went
 * unfilled, ...), is skipped, never mirrored at today's mark. Driven: a
 * follower who came back after two days had a two-day-old call opened.
 *
 * And a skip only moves the cursor past a signal when it is a fact about THAT
 * signal. Marks that could not be read (`marksFresh: false`, the route's
 * ticker map past its fill bound, or empty) are a fact about this moment, so
 * the sweep does nothing and the cursor stays where it is: the next read
 * mirrors the same signals with a live price. Every signal used to be skipped
 * as `no_mark` and passed for good, so one slow feed on the read that found
 * a new call meant that call was never opened.
 *
 * @param {object} ctx
 *   signals    — unprocessed signal rows, OLDEST first ({ id, symbol, direction, status, created_at })
 *   positions  — currently open arena positions ({ symbol })
 *   balance    — free balance (margins already deducted)
 *   prefs      — { margin, leverage } the follower chose
 *   marks      — live ticker map { SYM: { price } }
 *   marksFresh — false when the route could not read a fillable map
 *   now        — Date, injected so age is testable
 * @returns { opens: [{signal_id, symbol, direction, margin, leverage, price}],
 *            skips: [{signal_id, reason}], last_id, deferred }
 */
function planFollows(ctx = {}) {
  if (ctx.marksFresh === false) return { opens: [], skips: [], last_id: 0, deferred: 'marks' };
  const signals = Array.isArray(ctx.signals) ? ctx.signals : [];
  const now = ctx.now instanceof Date ? ctx.now : new Date();
  const prefs = ctx.prefs || {};
  const marks = ctx.marks || {};
  const openSymbols = new Set((ctx.positions || []).map((p) => p.symbol));
  let balance = Number(ctx.balance) || 0;
  let slots = arena.MAX_OPEN - (ctx.positions || []).length;

  const opens = [], skips = [];
  let lastId = 0;
  for (const s of signals) {
    lastId = Math.max(lastId, Number(s.id) || 0);
    // A signal row is in the scanner's spelling ('SOL/USDT'); the marks and
    // the open positions are exchange-style ('SOLUSDT'). Reading the row raw
    // found no mark for ANY engine signal, skipped each as `no_mark` and
    // advanced the cursor past it for good, so practice-follow never opened.
    const symbol = exchangeSymbol(s.symbol);
    const direction = String(s.direction || '').toUpperCase();
    const margin = Number(prefs.margin), leverage = Math.round(Number(prefs.leverage));
    const block = callBlock(s, now);
    if (block) { skips.push({ signal_id: s.id, reason: block }); continue; }
    if (openSymbols.has(symbol)) { skips.push({ signal_id: s.id, reason: 'already_open' }); continue; }
    if (slots <= 0) { skips.push({ signal_id: s.id, reason: 'no_slot' }); continue; }
    if (!(margin >= arena.MIN_MARGIN) || balance < margin) { skips.push({ signal_id: s.id, reason: 'balance' }); continue; }
    if (!(leverage >= 1 && leverage <= arena.MAX_LEVERAGE)) { skips.push({ signal_id: s.id, reason: 'leverage' }); continue; }
    const price = marks[symbol] && Number(marks[symbol].price);
    if (!(price > 0)) { skips.push({ signal_id: s.id, reason: 'no_mark' }); continue; }
    opens.push({ signal_id: s.id, symbol, direction, margin, leverage, price });
    openSymbols.add(symbol);
    balance -= margin;
    slots -= 1;
  }
  return { opens, skips, last_id: lastId, deferred: null };
}

/** Validate follow prefs from the UI. */
function validateFollow(input) {
  const b = input || {};
  const enabled = !!b.enabled;
  const margin = Number(b.margin), leverage = Math.round(Number(b.leverage));
  if (enabled) {
    if (!(margin >= arena.MIN_MARGIN)) return { ok: false, error: `margin must be at least ${arena.MIN_MARGIN} vUSDT` };
    if (!(leverage >= 1 && leverage <= arena.MAX_LEVERAGE)) return { ok: false, error: `leverage must be 1–${arena.MAX_LEVERAGE}` };
  }
  return { ok: true, data: { enabled, margin: margin || arena.MIN_MARGIN, leverage: leverage || 1 } };
}

module.exports = { planFollows, validateFollow };
