# Addendum 2: the paper book enters as live enters (2026-11-08)

Written and sealed before the change it describes is deployed, and before any paper trade made
under that change exists.

## Why

Addendum 1 said the paper tracker "fills at the price the engine reads". That was wrong. A paper
confirm of a limit idea opened the position at once **at the limit price**, whether or not the
market ever came back to it. Live does not: it rests the limit, and from 2026-08-18 to 2026-10-14,
123 of the operator's 407 live records were limits that never filled (expired, cancelled, or pulled
for drift). The paper book booked every one of those, at the better price, so a paper reading was
not a reading of what live would have done. Addendum 1's description of paper fills is corrected
by this paragraph; its rule is not changed.

## What changes in the bot

`bot/core/paper_limits.py`, on live's own settings (`CONFIG.limit_orders`):

- A paper limit on its own side of the market **rests**. It fills, at its limit price, on a pass
  where the last price reaches it. It is cancelled after live's horizon (4h for the engine's ideas,
  24h for a hand-typed ticket), or once the market has moved `LIMIT_DRIFT_CANCEL_PCT` (2%) away
  since placement; an engine idea that drifted is chased at the market where live would chase it
  (`drift_chase_decision`, the reading the live executor now shares), stop and target shifted by
  the same percentage.
- A market order fills at the market price read at confirm, and a limit at or through the market
  fills at the market, no worse than its limit. Both used to fill at the idea's entry.
- The first paper confirm made under this code writes `data/paper_fill_model.json` under the state
  directory, `{"model": "rests_like_live", "since": <UTC time>}`, once, before it rests or fills
  anything. That time is **T0**. It is the code's record of the change, not a date chosen by hand.

## What is added

A third reading of the same two questions, on the same paper book as Addendum 1.

- **Data:** the operator's paper book as in Addendum 1 (`data/portfolio_<id>.json`, its
  `history`). A row is scored when `opened_at` reads and is **at or after T0**, `closed_at` reads
  and lies in **[T0, T0 + 12 weeks)**, its net `pnl` reads, and its notional `entry_price` x
  `quantity` reads above zero. A row opened before T0 is left out and counted as opened under the
  old fill model; any other unreadable row is left out and counted.
- **Arms, rule, bootstrap, the 40-close floor, evaluated once:** as in the pre-registration,
  unchanged. `python3 scripts/sizing_forward.py evaluate --book paper-limits` refuses before the
  window closes.
- **The record:** absent means no paper confirm has run under the new code, and the window has not
  opened. Unreadable means no verdict: the window is never placed by hand. A record already present
  is never rewritten by the bot.
- **Cap:** as in Addendum 1.

## Addendum 1's reading

It stands and is evaluated on its own window and rule. Its window now spans two fill models, so its
result is reported whole, labelled as mixing them, and also split: closes opened before T0 (filled
at once at the limit) and closes opened at or after T0 (resting limits). The split halves are
descriptive. When the two paper readings disagree, this addendum's is the paper reading, because
its fills are the ones live would have had.

## What a paper verdict means

Still simulated. The paper book reads the last price once per monitoring pass; live rests on the
venue and fills on any touch between passes, so paper can miss a fill live would have had. A paper
fill at the limit has no queue position and no partial fill, and a market fill has no slippage. A
paper Q1 says the bot's calls make money at equal size **on these simulated fills**; it is not a
claim about live money. A paper Q2 is a reason to propose a sealed live test, never a direct change
of the live setting.
