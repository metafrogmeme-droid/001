# Weekend moves on Bitget US-stock perps: pre-registration (2026-10-30)

Step 0 of `RUNECLAW_Bitget_Hackathon_Kit.md` (Weekend Event Agent): before any agent
is built, test whether weekend price moves on US-stock perps carry a tradeable,
net-of-cost signal into the hours before the Monday cash open. Written and sealed
before any price data is fetched. Corrections go in dated addenda.

## What this can and cannot test

The kit's thesis is that an LLM reading weekend NEWS can position before the Monday
open. No historical news feed is available offline, so the news layer cannot be
backtested here. This tests the price layer the agent would trade on: whether the
early-weekend move of a stock perp (relative to the market) predicts its later
weekend move, net of fees and funding. A null result does not prove news has no
value; it means any edge would have to come from the news layer alone.

## Data (fetched after this file is sealed)

- Venue: Bitget USDT-M perpetuals, public API (`/api/v3/market/history-candles`,
  1-hour bars; funding history from the public funding endpoint).
- Universe, fixed by rule: perps with `symbolType` "stock" (stocks and ETFs), status
  online, launched on or before 2026-02-15, ranked by 24h quote turnover at fetch
  time; the top 30, excluding SPYUSDT, which is the hedge. The ranking uses current
  liquidity, which is a selection bias; it is disclosed, not corrected.
- Weekends: every weekend whose Friday is on or after 2026-02-20 and whose Monday is
  on or before 2026-10-26.
- Discovery: Fridays on or before 2026-06-30. Holdout: Fridays from 2026-07-01.
- A data file's sha256 is recorded before any outcome below is computed.

## Clock (US Eastern, converted to UTC per date)

Price at time t is the close of the hourly bar that ends at t.
- A = Friday 16:00 ET (cash close)
- B = Sunday 20:00 ET
- C = Monday 09:00 ET (the kit's agent is flat before 09:30)

For each symbol: signal s = r(A to B) minus r_SPY(A to B); outcome y = r(B to C) minus
r_SPY(B to C). A symbol-weekend with any missing bar at A, B or C, for the symbol or
SPY, is left out and counted.

## Rules tested (three, fixed now)

1. H1 momentum, SPY-hedged: long the symbol and short SPY (equal notional) when
   s > 0, the reverse when s < 0, entered at B and closed at C. P&L = sign(s) * y.
2. H2 reversal, SPY-hedged: the opposite position. P&L = -sign(s) * y.
3. H3 market momentum: SPYUSDT alone, unhedged, long when r_SPY(A to B) > 0 and short
   when < 0, entered at B, closed at C.

## Costs

Per position, per leg, per side: taker 0.06% plus 0.04% slippage = 0.10%. H1 and H2
have two legs (8 one-way fills of 0.10% = 0.40% per round trip); H3 has one leg
(0.20%). Funding: the actual settlements between B and C for each leg, from the
venue's funding history; if a settlement cannot be read, the symbol-weekend is
charged that symbol's mean absolute rate over its read settlements, and counted.
Sensitivity (reported, deciding nothing): 0.05% and 0.20% per side per leg.

## Decision rule

The unit is the weekend: each weekend's result is the equal-weight mean net P&L
over its symbols (H3: SPY's net P&L). For each rule, pass only if all hold:
- discovery mean > 0;
- holdout mean > 0;
- the holdout lower bound of a bootstrap over weekends (10,000 resamples, seed 7) at
  the 0.833rd percentile (95% two-sided, Bonferroni for three rules) is above 0.

A rule that passes is evidence to build a paper (Bitget Demo) agent around it, not
to trade it live. If none passes, the report says so plainly and the kit's agent
would have to earn its edge from the news layer alone, with nothing here
supporting it.

## Reported, deciding nothing

Hit rates; per-symbol results; the same rules without the SPY hedge; the
correlation of s and y; the share of symbol-weekends left out; whether the perps
actually trade on weekends (bars with zero volume).

## Power

About 35 weekends in all and about 17 in the holdout. Only a large effect can pass.
