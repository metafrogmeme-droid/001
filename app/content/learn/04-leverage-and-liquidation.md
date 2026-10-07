# Leverage and liquidation — the cliff you agreed to

Leverage does not change what the market does. It changes what the market
does *to you*. The same 2% move that barely registers at 1× ends the whole
position at 50×. This lesson is the arithmetic of that cliff — where it
sits, how fast you reach it, and why the exchange always gets there before
your stop does when you let it.

## What leverage actually is

An isolated-margin perp position puts up `margin` and controls
`margin × leverage` of notional. The profit and loss are computed on the
notional, but the money you posted is only the margin:

    pnl = margin × leverage × (move as a fraction of entry)

At 10×, a +1% move earns +10% on your margin. And a −1% move loses 10% of
it. Leverage is symmetric on paper and asymmetric in practice — because of
where the floor is.

## Where the cliff sits

An isolated position is liquidated when its loss reaches the margin — the
exchange closes it because the money backing it is gone. Ignoring fees and
maintenance margin (real exchanges liquidate slightly *earlier* than this,
never later), the adverse move that ends the position is:

    liquidation move ≈ 1 / leverage

- 2× → about a 50% adverse move
- 5× → about 20%
- 10× → about 10%
- 25× → about 4%
- 50× → about 2%

Read the last line again. At 50×, ordinary intraday noise — not a crash,
not news, just noise — reaches the cliff. High leverage does not mean "more
profit." It means "closer cliff."

## Why the cliff beats your stop

A stop-loss is your decision; liquidation is the exchange's. If your stop
sits *beyond* the liquidation price, it is fiction — the exchange closes
the position before your stop is ever touched, and it closes it at the
worst price of the whole trade, with a liquidation fee on top.

The order of prices must always be:

    entry → your stop → the liquidation price

with real distance between the last two. If the arithmetic puts your stop
past liquidation, the position is mis-sized: less leverage or a closer
stop — those are the only two honest fixes.

## The compounding trap

A −10% day needs +11.1% to get back. A −50% liquidation needs +100%.
Losses are not symmetric with gains, and every liquidation restarts you
further down the curve than the same loss taken as a planned stop —
because the liquidation price was, by construction, the worst available.
Traders who survive treat leverage as a *sizing* tool (control the same
notional with less capital locked up) — not as a way to want more.

## What the Arena and the Stress Lab model

The paper Arena and the Portfolio Stress Lab both model isolated-margin
legs with exactly the arithmetic above: a leg is liquidated when its loss
reaches its margin. When the Stress Lab says a scenario liquidates you, it
is not a prediction — it is this formula, applied to a shock you chose.
Run your real book's shape through it before the market does.

---

*Education, not investment advice. Formulas describe isolated-margin perps
and ignore fees, funding and maintenance margin — all of which make the
real cliff slightly closer than the ideal one, never further.*

## Self-check

1. At 25× leverage, roughly what adverse move liquidates an isolated position?
- [ ] 25%
- [x] 4%
- [ ] 1%

2. If your stop sits beyond the liquidation price, the stop is:
- [x] Fiction — the exchange closes the position first, at the worst price
- [ ] Extra protection
- [ ] Cheaper to execute

3. A −50% loss needs what gain to recover?
- [ ] 50%
- [ ] 75%
- [x] 100%
