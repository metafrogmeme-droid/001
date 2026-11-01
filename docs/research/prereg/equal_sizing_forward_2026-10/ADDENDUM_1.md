# Addendum 1: the paper book (2026-10-31)

Written and sealed before any close in its window exists, and before the paper book was read.

## Why

Live trading was turned off on 2026-10-18 (`/golive OFF`) and has placed nothing since; the last
live close is 2026-10-14. The sealed test reads only live closes, so it would reach 2027-01-23
with no scored close and no verdict. Since the 18th every confirm, tapped or automatic, opens on
the operator's paper book (`docs/lessons/ENGINEERING_LOG.md`, "LIVE OFF MEANS PAPER").

## What is added

A second, separate reading of the same two questions on the paper book. The original live
reading stands unchanged and is still evaluated on its own window and rule; neither replaces the
other, and the two are reported side by side.

- **Data:** the operator's paper book, `data/portfolio_<id>.json` under the state directory, where
  `<id>` is the first id in `TELEGRAM_CHAT_ID`; its `history` list. A row is scored when
  `closed_at` reads and lies in **[2026-11-01T00:00Z, 2027-01-24T00:00Z)** (twelve weeks), its net
  `pnl` reads, and its notional `entry_price` x `quantity` reads above zero. Any other row is left
  out and counted.
- **Arms, rule, bootstrap, the 40-close floor, evaluated once:** as in the pre-registration,
  unchanged. `python3 scripts/sizing_forward.py evaluate --book paper` refuses before 2027-01-24.
- **Cap:** the paper tracker keeps its last 1,000 closes. If the history is at that cap when it is
  read, closes may have been dropped; the result says so (`history_at_cap`) and the count is
  reported as possibly short. The window is not extended.

## What a paper verdict means

Paper fills are simulated: the paper tracker fills at the price the engine reads and charges its
modelled commission, with no venue, queue or slippage. A paper Q1 says the bot's calls make money
at equal size **on simulated fills**; it is not a claim about live money. A paper Q2 is a reason to
propose a sealed live test of equal sizing, never a direct change of the live setting.
