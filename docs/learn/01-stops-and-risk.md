# Stops and risk — the number you choose before the trade

A stop-loss is the only part of a trade you fully control. Entries depend on
fills, exits in profit depend on the market — but the most you are willing to
lose is a decision, and it is made before the trade or not at all.

## Risk is an amount, not a feeling

For an isolated-margin perp position, the loss if your stop fills at its
trigger price is:

    loss = margin × leverage × |entry − stop| / entry

capped at the margin itself — an isolated position can never lose more than
its own margin. The Arena computes exactly this for you: the ticket shows
"your stop caps this trade at −X% of equity" before you click, and every open
position with a stop wears its risk chip.

## The honest caveats

- Stops fill **at their trigger price** in the Arena. Real markets gap: a
  stop is a plan for an orderly market, not a guarantee in a disorderly one.
- Fees and funding are not in the formula above. Real losses are slightly
  worse than the arithmetic.
- A position **without** a stop does not have "no risk" — it has *unbounded*
  risk until liquidation. The portfolio heat line counts these separately for
  exactly this reason.

## A working habit

Pick the percent of equity you are willing to lose per trade — many traders
use somewhere around 1% — then work backwards to position size:

    margin = equity × risk% / (leverage × |entry − stop| / entry)

The number itself matters less than the habit: decide it before entry, write
it in your diary, and compare what you decided with what you actually did.
That comparison is where the learning lives.

*Education, not investment advice. Past performance never predicts future
results.*
