# Addendum 1 to the daily trend sleeve pre-registration: the spot reading (2026-11-08)

Written before the sleeve's record is first read (`daily_trend_sleeve_prereg.md` forbids any reading
before 2027-10-17), and without looking at its rows.

## Why

The rule passed on spot prices with no funding (Kraken, holdout Sharpe 0.63). Re-costed with real
perpetual funding over the same years it scored 0.49 to 0.54 (`funding_study_2026-10-17.md`): the
funding a long trend book pays was 15 to 20% of its gross profit. The record measures the sleeve on
Bitget perps, with funding, so it can only say whether the perp version clears the bar. It cannot
say whether the same weights held as spot coins would have. That is a separate question with a
separate answer, and it is added here, before any row is read.

## What is added

A second reading of the same record, measured on the same date by the same person, in the same
first reading:
- **Weights:** the recorded rows, unchanged. Nothing is re-decided.
- **Prices:** Bitget spot daily closes for each recorded market's base coin against USDT, open to
  open from the day after each row, as the perp reading uses perp prices. A recorded market with no
  Bitget spot pair on a day holds cash that day; those coin-days are counted and reported.
- **Costs:** 0.10% a side on each change of weight, as the perp reading. **No funding.**
- **Pass rule for this reading:** Sharpe at least 0.5 and max drawdown at most half of BTC
  buy-and-hold's over the same days. The funding criterion does not apply.

The perp reading and its pass rule stand unchanged and decide the live-eligibility conversation for
the perp sleeve. The two readings are reported side by side; neither replaces the other.

## What a spot pass would mean

That the same weights, held as spot coins, would have cleared the bar. It would be a reason to
propose a spot sleeve, which needs an order path the bot does not have (the live executor places
perpetual orders only), its own paper period, and the owner's go. It changes nothing by itself.

## Expectation, written now

The gap between the two readings is the funding the perp book paid, less any difference between
spot and perp closes. If Bitget's funding on these longs runs like Binance's did, expect the spot
reading about 0.1 Sharpe above the perp one.
