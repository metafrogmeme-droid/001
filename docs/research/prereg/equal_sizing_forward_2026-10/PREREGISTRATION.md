# Equal-size positions against the bot's own sizing, forward: pre-registration (2026-10-30)

Written and sealed before the window opens and before any close in it exists.

## Why

Stage 2 of the signal-edge study (`docs/research/prereg/signal_edge_stage2_2026-10/REPORT.md`)
found the bot's out-of-sample positions slightly positive per position and negative once
weighted by the size the bot gave them: the larger positions lost. The historical data that
could test this without reuse is spent, so the test runs forward on the bot's own closes.

Equal sizing does not change which positions the bot takes, only how much goes into each. So
both arms are read off the same closes and the live bot is not changed.

## Data

The operator's live closed positions, `$RUNECLAW_STATE_DIR/closed_trades.json` (the file the
live executor writes and `bot/backtest/parity.py` reads), rows with `closed_at` in
**[2026-10-31T00:00Z, 2027-01-23T00:00Z)**: twelve weeks. A row is scored when its net
(`pnl_usd`, else `net_pnl`, else `net_pnl_usd`) reads and its notional (`entry_price` x
`quantity`, both above zero) reads. Any other row is left out and counted, never scored as zero.

## Arms

Per scored close, r = net / notional (the return on the position's notional, after its fees).
- **Equal size:** E = the mean of r over the closes (every position the same notional).
- **As sized:** W = sum of net / sum of notional (the bot's own sizing).

## Decision rule

Week blocks: the ISO calendar week of `closed_at`. Bootstrap: 10,000 draws over weeks, seed 7.
- **Q1, equal size makes money:** E > 0 and the one-sided 97.5% lower bound of E > 0.
- **Q2, equal size beats the bot's sizing:** E - W > 0 and the one-sided 97.5% lower bound of
  E - W > 0 (both recomputed inside each draw).
- Fewer than **40** scored closes in the window: **no verdict** on either question. The window
  is not extended.

Q1 decides whether the bot's calls may be described as making money at equal size. Q2 decides
whether to propose equal sizing for the live bot; that change would get its own review.

## Evaluated once

`python3 scripts/sizing_forward.py evaluate` refuses before 2027-01-23. `status` before then
prints counts only (scored, unscored, weeks), never a return.

## Limits

One book; live trading is admin-only and small, so the count may fall short of 40. Equal
notional ignores that the bot also sizes by stop distance (risk per trade); E answers "same
notional", not "same risk".
