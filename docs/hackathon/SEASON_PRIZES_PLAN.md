# Season prizes in Pi: the plan

*20 October 2026. What is built is behind a switch that is off. What is not built needs the owner's decision first, and says so.*

## What it is

A free contest. When a season ends, the top names on its board are paid in Pi. Entry is never paid and nothing is staked, so nobody can lose anything by playing. The board is the season's own: practice trades closed inside the season's window, ranked on percent return, under names people chose to show.

This is the strongest answer to the hackathon's "support the use of Pi" criterion that does not make the game a wager. Paid entry, or Pi on a call, would be a wager. It is not in this plan.

## What is built (off)

| Piece | Where | What it does |
|---|---|---|
| The prize table | `app/lib/pi_prizes.js::schedule` | Reads `PI_SEASON_PRIZES_ENABLED`, `PI_SEASON_PRIZES` and `PI_SEASON_PRIZES_MIN_TRADES`. Off unless the switch is exactly `1` and the table reads. A table that does not read (a bad amount, more than ten places, a lower place paying more than a higher one) is off, with the reason, so a typo never announces a different prize. |
| The payout plan | `app/lib/pi_prizes.js::plan` | Ranks the season's closed trades by name, for names with enough closed trades. Ties share the prizes of the places they span. A name whose closes include an unreadable result is held, and the plan is not final until the operator looks. |
| The player's view | `GET /api/pi/prizes`, the Pi app's Leaders tab | When on, the table ("#1 · 50 π …") and the rule in one sentence, in the reader's language. When off, nothing at all. |
| The operator's view | `GET /api/pi/prizes/plan` (operator only) | Who is owed what for the current season. A running season's plan is a preview; only an ended season's plan with nothing held is final. |

Nothing in this code moves Pi. No route pays, and no key is read or named.

## The rule, as players read it

> Free to enter. When the season ends, the top names on this board with at least *n* closed trades are paid in Pi. Names tied on return share their places' prizes.

## What the owner decides before turning it on

1. **How winners are paid.** Two ways, and neither is built:
   - **Pi App-to-User payments.** The app pays a Pioneer by their Pi uid, which we already hold. This needs the **app wallet's private seed on our server**: a custody change from "the server never signs". It needs a funded app wallet, a key-handling design (where the seed lives, who can read it, rotation), and its own review.
   - **The operator pays by hand** from the team's Pi wallet. Simple and holds no key, but the operator needs a winner's wallet address, which means asking the winner for one. That is new personal data: the privacy policy must say so first.
2. **The legal check.** Free prize contests are regulated differently from country to country (some require published terms, some forbid prizes in certain places, some tax them). Get advice for the countries Pioneers play from before announcing a prize.
3. **The prize terms page.** Published terms: eligibility, the season's dates, the ranking rule, tie-breaking, what happens to an unclaimed prize, how disputes are handled, and that it is void where prohibited.
4. **The budget.** The table's total, in Pi, per season.

## Turning it on

After the decisions above, set:

```
PI_SEASON_PRIZES_ENABLED=1
PI_SEASON_PRIZES=50,30,20
PI_SEASON_PRIZES_MIN_TRADES=5
```

and restart the website process. The Leaders tab shows the table at once. At the season's end, read `/api/pi/prizes/plan` signed in as the operator, resolve anything held, and pay by the chosen method.

## What stays true either way

- No paid entry, ever, and no Pi on a call.
- No dollar figure on any public surface; the table is shown to signed-in players only.
- The board's own ranking is unchanged: percent return under chosen names.
