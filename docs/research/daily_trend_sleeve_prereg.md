# Pre-registration — the daily trend sleeve on Bitget, written 2026-10-17 before its first record

The long-only daily trend rule passed its pre-registered bar on Kraken's spot
history (engineering log, "THE DAILY TREND RULE PASSED ON KRAKEN'S HISTORY":
development 2014-04 → 2021-05 Sharpe 2.12, holdout 2021-06 → 2026-06 Sharpe
0.63, 16 coins, costs 0.10% a side, no funding). On Bitget perps with a fixed
0.03%/day funding charge on a long book the same rule had scored −0.02 over
the same holdout years (research of 17 Oct, 20 coins). The open question is
which of those two is the rule on the venue the bot trades, with the funding
it really pays. This file fixes how that is measured before the first row of
the record exists.

## What is recorded
- `bot/core/daily_trend.py` is the rule; the `daily trend` preset carries its
  numbers (lookbacks 20/60/120 days, 30-day vol, 20%/yr target split across
  the markets readable that day, cap 1× notional, rebalance band 10%).
- Once a UTC day the engine reads the sleeve's board (`strategy_scan.scan`)
  and appends it to `data/learning/daily_trend_book.jsonl`
  (`DAILY_TREND_BOOK_PATH` overrides it): the day, when it was read, the bar
  it closed on, the markets it would hold with their weights, the gross, the
  number of markets that read, and the ones that did not. A day whose board
  could not be read records nothing, never an empty book.
- Nothing is placed. The record is what the sleeve would have held.

## What is measured, and when
- Not before 2027-10-17, twelve months of rows, and not by anyone who has
  looked at the rows before then: the first reading of the record is the
  measurement. Returns are rebuilt from the recorded weights and Bitget's
  daily closes (open-to-open from the day after each row), charged 0.10% a
  side on each change of weight and the funding Bitget actually settled on
  each long over each day (its public funding history, from July 2026).
- Reported: annualised Sharpe, CAGR, max drawdown, return by calendar month,
  turnover, costs and funding as a share of gross, each market's contribution;
  beside BTC buy-and-hold over the same days.

## Pass rule (fixed now)
The sleeve earns a live-eligibility conversation only if, net of costs and
real funding over those twelve months: Sharpe ≥ 0.5, max drawdown ≤ half of
BTC buy-and-hold's over the same days, and funding paid ≤ 40% of gross. One
test. A failure on any criterion is reported as a failure with its numbers,
and the sleeve stays a reading.

## What the funding study of 17 October adds (written after it, before any row)
Re-costing the same weights with Binance's real funding over 2021–2026
(`funding_study_2026-10-17.md`) measured what a long trend book pays: 2.24×
the venue's average rate on the coin-days it holds, 15–20%/yr of its long
notional. The pass rule above is unchanged. The figure to compare the
record's funding against is that conditional rate, not Bitget's average: a
year in which the sleeve's funding per unit of long notional runs well above
20%/yr is a year in which Bitget's longs were dearer than Binance's were, and
is reported as such rather than as the rule failing.

## Prediction, written now
Funding is the swing. If Bitget's realised funding on these longs averages
under 0.01%/day, the sleeve lands near the Kraken holdout (Sharpe about 0.6);
if it averages near the 0.03%/day the Bitget study assumed, it lands near
zero and fails. Either way it is a capital-preservation sleeve: its expected
CAGR is single digits, and nothing about it changes the verdict on the hourly
indicators.
