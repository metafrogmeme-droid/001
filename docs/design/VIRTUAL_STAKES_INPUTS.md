# Virtual stakes: what the reference market does, and what the duel borrows

Inputs for the virtual-stakes design (the step after the Daily Duel), read on
2026-10-20 from Polymarket's own help page on resolution and from the
prediction-market data Nansen exposes through Flovia. Facts first, with their
source and date; the design decisions they feed are listed at the end and are
proposals, not commitments.

## How the reference market resolves (Polymarket, read 2026-10-20)

Source: help.polymarket.com, "How are prediction markets resolved". Quoted
where it matters; the page does not cover everything the design needs, and
what it does not say is listed as not said.

- **The rules are named up front.** "Markets are resolved according to the
  market's pre-defined rules," which appear in the market's order book.
- **An outcome is proposed, with a bond.** Resolution "involves putting up a
  bond in USDC.e which will be forfeited if the proposal is unsuccessful"; the
  page names a $750 bond and warns, "Do not propose a resolution unless you
  understand the process and are confident in your view."
- **A challenge window, then a dispute process.** "Once a market is proposed
  for resolution it goes into a challenge period of 2 hours." Anyone who
  disagrees can dispute; an unapproved proposal "will enter UMA's dispute
  resolution process". The oracle is UMA's Optimistic Oracle.
- **Payout is binary.** A winning share pays $1, a losing share nothing, and
  trading stops at resolution.
- **Not said on that page**: who may propose beyond the bond, what an
  ambiguous market settles to, and whether a resolution source is named at
  creation. These are not assumed here.

## What Nansen exposes about those markets (Flovia catalogue, 2026-10-20)

Twelve read-only endpoints under `prediction-market`: hourly candles, the
order book, top holders, recent trades, a market screener and an event
screener (volume over 24h/1w/1m, liquidity, open interest, unique traders,
age, end date), PnL by market and by address, positions per holder, a
wallet's trades and summary, and volume and open interest per category. All
read-only, all shared through the free allowance; none is on the trading
path.

## What the duel already has, and what it would borrow

The duel is a prediction game with no stakes: a call, a sealed round, a
sealed settlement, marks. Since 2026-10-20 a settlement also carries a
second price reading and a three-valued `disputed` flag (spec §2.6). Against
the reference market:

| Reference market | Duel today | Virtual stakes would add |
|---|---|---|
| Rules named at creation | The category's rules, sealed with the round (v6) | nothing new |
| A proposed outcome with a bond | The venue's hourly close, written by the settlement | nothing: no proposer, no bond |
| A 2-hour challenge window | none | a window a player may flag a settlement in (no bond; a flag is a count, not a stake) |
| UMA dispute process | `disputed` from a second price source, verdict unchanged | an operator review queue for flagged or disputed settlements, with its own receipt |
| $1 / $0 payout | marks (1, 0, push) | a virtual stake in a play-money unit, paid at fixed odds set when the round is dealt and sealed with it |

Decisions this proposes for the design review, none built:

1. **Odds are dealt, not traded.** The reference market prices continuously;
   the duel has no order book and should not grow one. A round's odds are
   fixed and sealed at the deal (the v6 round payload gains `odds`), so a
   player stakes against a number that cannot move after they commit.
2. **A stake is virtual and counts only.** The unit is play money with no
   exchange rate, the leaderboard shows a percent return and counts, and no
   dollar figure appears anywhere public (CLAUDE.md, public-surface rules).
   The word "stake" stays reserved for the token on the site's own copy, so
   the game's word needs choosing (the review doc of 19 Oct already moved
   the Arena off it).
3. **A dispute pays nothing until reviewed.** A settlement with
   `disputed: true` pays out only after the operator's review writes a
   receipt of its own; an unreviewed dispute pays at the venue's close after
   the window, as today's verdict already stands.
4. **A flag is not a bond.** Players may flag a settlement within a window;
   a flag counts and is shown, and costs nothing: a bond without real money
   is a number nobody can lose.

Open questions for the review: the size of the play-money float per season,
whether marks and stakes coexist or the stake replaces the mark, and whether
a flagged settlement should pause the player's record or only annotate it.
