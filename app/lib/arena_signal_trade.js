'use strict';
/**
 * Open ONE named engine signal as a paper position.
 *
 * The Arena already had two ways in: type a ticket yourself, or flip
 * practice-follow on and mirror every signal the engine emits from now on.
 * Neither is "I like THAT call — put it on paper." That is what this is: the
 * user picks a specific signal and it becomes a paper position carrying the
 * signal's own direction and its own exits.
 *
 * Three honesty rules decide almost everything here, and all three exist
 * because the tempting version of this feature lies:
 *
 *   1. The fill is the LIVE mark, never the signal's entry_price. A signal
 *      posted at 09:00 and traded at 14:00 did not fill at the 09:00 price.
 *      Back-filling it would manufacture a P&L the user never could have had.
 *      So the plan also reports `drift_pct` — how far the market has moved
 *      since the call — and the UI shows it before the user commits.
 *
 *   2. Exits the market has ALREADY passed are dropped, not clamped. If a
 *      long's take-profit now sits below the live mark, that target is behind
 *      us; carrying it forward would open a position that instantly "hits"
 *      its target. Dropping it leaves the level empty and says so.
 *
 *   3. A stale signal is refused outright. Past MAX_SIGNAL_AGE_MS the call is
 *      no longer the engine's current read of the market, and letting someone
 *      open it would put the engine's name on a decision it is not making.
 *
 * Pure: signal + market state in, decision out. The route does the I/O.
 */

const arena = require('./arena');

// A call older than this is history, not a position to take.
const MAX_SIGNAL_AGE_MS = 6 * 60 * 60 * 1000;   // 6 hours

// Beyond this much drift from the signal's own entry, the trade the user is
// about to take is materially not the trade the engine described. It is still
// allowed — it is their paper account — but the plan flags it so the UI can
// warn rather than quietly fill somewhere else.
const DRIFT_WARN_PCT = 1.5;

/**
 * @param {object} ctx
 *   signal    — { id, symbol, direction, entry_price, stop_loss, take_profit, created_at }
 *   positions — currently open arena positions ([{ symbol }])
 *   balance   — free balance (open margins already deducted)
 *   margin    — vUSDT the user wants on this one
 *   leverage  — their chosen leverage
 *   mark      — live price for the signal's symbol
 *   now       — Date, injected so age is testable
 * @returns {{ok: true, data: {...}} | {ok: false, error: string, code: string}}
 */
function planSignalOpen(ctx = {}) {
  const s = ctx.signal;
  if (!s) return { ok: false, code: 'no_signal', error: 'That signal no longer exists' };

  const symbol = String(s.symbol || '').trim().toUpperCase();
  const direction = String(s.direction || '').trim().toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return { ok: false, code: 'direction', error: 'That signal has no tradeable direction' };
  }

  const now = ctx.now instanceof Date ? ctx.now : new Date();
  const createdAt = s.created_at ? new Date(s.created_at) : null;
  const ageMs = createdAt && !isNaN(createdAt.getTime()) ? now.getTime() - createdAt.getTime() : null;
  if (ageMs != null && ageMs > MAX_SIGNAL_AGE_MS) {
    return { ok: false, code: 'stale',
      error: 'That call is more than 6 hours old — the engine has moved on. Open a current one.' };
  }

  const mark = Number(ctx.mark);
  if (!(mark > 0)) {
    // Never a fill at a guessed price. No mark, no trade.
    return { ok: false, code: 'no_mark',
      error: `No live price for ${symbol} right now — try again in a moment.` };
  }

  const positions = Array.isArray(ctx.positions) ? ctx.positions : [];
  if (positions.some((p) => String(p.symbol || '').toUpperCase() === symbol)) {
    return { ok: false, code: 'already_open',
      error: `You already have a ${symbol} position open — close it first.` };
  }

  // Reuse the ticket's own validator so a signal open can never do something a
  // manual open could not (margin floor, leverage ceiling, slot limit,
  // sufficient balance). One set of limits, not two that can drift apart.
  const v = arena.validateOpen(
    { symbol, direction, margin: ctx.margin, leverage: ctx.leverage },
    ctx.balance, positions.length);
  if (!v.ok) return { ok: false, code: 'limits', error: v.error };

  // Rule 2: inherit the signal's exits, but only where they still sit on the
  // correct side of the LIVE fill. validateTpSl is checked one level at a time
  // so a passed take-profit does not also discard a still-valid stop.
  const tpTry = arena.validateTpSl(direction, mark, s.take_profit, null);
  const slTry = arena.validateTpSl(direction, mark, null, s.stop_loss);
  const tp = tpTry.ok ? tpTry.data.tp : null;
  const sl = slTry.ok ? slTry.data.sl : null;
  const dropped = [];
  if (Number(s.take_profit) > 0 && tp == null) dropped.push('tp');
  if (Number(s.stop_loss) > 0 && sl == null) dropped.push('sl');

  // Rule 1: say how far this fill is from the call it came from.
  const signalEntry = Number(s.entry_price);
  const driftPct = signalEntry > 0
    ? Math.round((mark / signalEntry - 1) * 10000) / 100
    : null;

  return {
    ok: true,
    data: {
      signal_id: Number(s.id),
      symbol, direction,
      margin: v.data.margin, leverage: v.data.leverage,
      entry: mark,                       // the LIVE fill, not the signal's price
      tp, sl,
      dropped,                           // exits the market had already passed
      signal_entry: signalEntry > 0 ? signalEntry : null,
      drift_pct: driftPct,
      drift_warn: driftPct != null && Math.abs(driftPct) >= DRIFT_WARN_PCT,
      age_ms: ageMs,
    },
  };
}

/**
 * Decorate raw signal rows for the picker, without deciding anything the open
 * route would decide differently. `tradeable` here is only what can be known
 * from the signal itself plus the account's own state — a missing live mark is
 * deliberately NOT checked, because the list is cheap and the mark is not.
 */
function decorateForPicker(signals, ctx = {}) {
  const now = ctx.now instanceof Date ? ctx.now : new Date();
  const openSymbols = new Set((ctx.positions || []).map((p) => String(p.symbol || '').toUpperCase()));
  const marks = ctx.marks || {};
  return (Array.isArray(signals) ? signals : []).map((s) => {
    const symbol = String(s.symbol || '').toUpperCase();
    const direction = String(s.direction || '').toUpperCase();
    const createdAt = s.created_at ? new Date(s.created_at) : null;
    const ageMs = createdAt && !isNaN(createdAt.getTime()) ? now.getTime() - createdAt.getTime() : null;
    const mark = marks[symbol] && Number(marks[symbol].price);
    const signalEntry = Number(s.entry_price);
    let reason = null;
    if (direction !== 'LONG' && direction !== 'SHORT') reason = 'direction';
    else if (ageMs != null && ageMs > MAX_SIGNAL_AGE_MS) reason = 'stale';
    else if (openSymbols.has(symbol)) reason = 'already_open';
    return {
      id: Number(s.id), symbol, direction,
      // Prices are public market facts (§4) — levels, never amounts.
      signal_entry: signalEntry > 0 ? signalEntry : null,
      stop_loss: Number(s.stop_loss) > 0 ? Number(s.stop_loss) : null,
      take_profit: Number(s.take_profit) > 0 ? Number(s.take_profit) : null,
      // Confidence is the engine's own number and is shown as a percent.
      confidence: s.confidence == null ? null : Math.round(Number(s.confidence) * 1000) / 10,
      pattern: s.pattern || null,
      created_at: s.created_at || null,
      age_ms: ageMs,
      mark: mark > 0 ? mark : null,
      drift_pct: mark > 0 && signalEntry > 0
        ? Math.round((mark / signalEntry - 1) * 10000) / 100 : null,
      tradeable: reason == null,
      blocked_reason: reason,
    };
  });
}

module.exports = { planSignalOpen, decorateForPicker, MAX_SIGNAL_AGE_MS, DRIFT_WARN_PCT };
