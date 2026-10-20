'use strict';
/**
 * The duel card's context lines, kept apart from the duel's rules and its
 * settlement on purpose: nothing here decides anything. A round's smart-money
 * line is a remembered Nansen read (lib/smart_money.js) that the card shows
 * beside the question, and `tests/test_smart_money_is_a_line_and_never_a_verdict.py`
 * holds that the rules, the seal and the settlement never read it.
 *
 * `primeSmartMoney` runs once a day, when the card is dealt, and is not
 * awaited: a deal must not wait on a rented data source, and the lines
 * appear on the next load. `attachSmartMoney` only peeks.
 */

const { smartMoneyReading, peekSmartMoney } = require('./smart_money');

/** Every market symbol on these rounds, once each. */
function symbolsOf(rounds) {
  const out = new Set();
  for (const r of rounds || []) {
    if (!r || r.kind === 'sports') continue;
    if (r.symbol) out.add(String(r.symbol));
    if (r.symbol_b) out.add(String(r.symbol_b));
  }
  return [...out];
}

/** Read (and remember) the line for each symbol; best effort, never awaited by a deal. */
function primeSmartMoney(rounds) {
  const symbols = symbolsOf(rounds);
  const run = (async () => {
    for (const s of symbols) {
      try { await smartMoneyReading(s); } catch (e) { /* a line that could not be read is a line not shown */ }
    }
  })();
  run.catch(() => {});
  return run;
}

/** The remembered line(s) on a public round view, when there are any. */
async function attachSmartMoney(view, round) {
  if (!view || !round || round.kind === 'sports') return view;
  const a = round.symbol ? await peekSmartMoney(round.symbol) : null;
  if (a) view.smart_money = a;
  const b = round.symbol_b ? await peekSmartMoney(round.symbol_b) : null;
  if (b) view.smart_money_b = b;
  return view;
}

module.exports = { primeSmartMoney, attachSmartMoney, symbolsOf };
