# Virtual stakes for the Daily Duel: the design

*Status: proposal for review, 20 October 2026. Nothing here is built. It
follows `docs/design/VIRTUAL_STAKES_INPUTS.md` (what the reference market
does, what Nansen exposes, what the duel already has) and the four decisions
proposed there, which this document takes as settled unless marked open.*

## 1 · What it is, in one paragraph

The Daily Duel stays what it is: a few sealed rounds a day, a call, a sealed
settlement, marks on a season board. Virtual stakes add a second layer on the
same rounds: every player holds a season float of **chips**, a play-money
count with no exchange rate, and may put some of them on a call at odds that
were fixed and sealed when the round was dealt. A correct call pays the chips
times the odds; a wrong one loses them; a push or an unresolved round returns
them. The board shows each player's return on their float as a percent and
the counts behind it. No chip is ever a dollar, a token or a transfer, and no
part of this touches the trading path.

## 2 · The word

The site's own copy reserves "stake" for the token, and the 19 October review
moved the Arena off the word. The game's unit is **chips**, the verb is
**put** ("put 50 chips on UP"), and a player's season float is their
**stack**. "Bet", "wager" and "stake" do not appear on any surface; "odds"
does, because it names the number the player is shown before they commit.

## 3 · The rules

### 3.1 · The float

- Every opted-in player starts a season with **1,000 chips**. A season is the
  duel's existing calendar month; the stack resets at the season's start and
  nothing carries over.
- A stack can reach zero. A player at zero keeps calling and keeps earning
  marks; they put no chips until the next season. There is no top-up, no
  purchase and no gift: a chip that can be bought is a price.
- The stack is a count. It is never rendered with a currency sign, a decimal
  or the word "balance" beside a dollar figure anywhere, and it never appears
  on a public or marketplace payload as anything but a count or a percent
  (CLAUDE.md, public-surface rules).

### 3.2 · Odds are dealt, not traded

- A round's odds are **fixed at the deal and sealed with the round**. They are
  decimal odds per allowed pick, for example `{"up": 1.9, "down": 1.9}` or
  `{"home": 2.4, "draw": 3.1, "away": 2.8}`.
- **The odds never depend on the agent's hidden stance.** A direction round
  seals the stance behind a nonce precisely so nobody can read it before
  they call; odds that leaned toward the agent's side would be that stance
  in another font. Odds come from a published per-category table, and for a
  football round from the match feed's own pre-match prices when the feed
  carries them, read at the deal and recorded in the payload with their
  source. A round whose odds could not be read is dealt **without chips**
  (`odds: null`): a player may still call it for marks, and the card says
  "no chips on this round".
- The table, as a starting point, to be tuned per season from recorded
  results and never mid-season:

| category | picks | odds |
|---|---|---|
| direction | up / down | 1.9 / 1.9 |
| range | break / hold | 2.2 / 1.7 |
| mover | first / second | 1.9 / 1.9 |
| back/fade | back / fade | 1.9 / 1.9 |
| sports | home / draw / away | from the feed, else none |

### 3.3 · Putting chips

- A player puts chips **with their call, once**. The call is already
  write-once while the round is open; the chips ride in the same write and
  are sealed in the same call receipt. There is no separate "put" after the
  call and no change of mind.
- **Minimum 10, maximum 200 chips** per round (a fifth of the starting
  float), and never more than the stack holds. The server enforces all three;
  the page shows the limits and refuses to send more.
- A round dealt without odds takes no chips; a call with chips on such a
  round is refused with its reason, not silently stripped.

### 3.4 · Paying out

- At settlement, in the same write that marks the call:
  - `correct` → the stack gains `round(chips × (odds − 1))`, chips returned;
  - `wrong` → the stack loses the chips;
  - `push` and `unresolved` → chips returned, nothing else moves.
- A settlement with `disputed: true` (spec §2.6) **holds** the chips: the
  verdict and the marks stand as today, the payout waits. It is released by
  the operator's review (§3.5) or, when no review happens, at the end of the
  challenge window at the venue's close, exactly as the verdict already
  stands. Held chips are shown as held, never as lost or won.
- Every payout is a row of its own (§4), so a stack is the sum of its rows
  and a disagreement between the two is a defect the tests can see.

### 3.5 · Flags, the window and the review

- After a settlement any player who called that round may **flag** it within
  a **two-hour** window. A flag costs nothing and counts; it is not a bond
  (a bond without real money is a number nobody can lose).
- A settlement goes to the **review queue** when it is `disputed`, or when it
  is flagged by at least three callers or a tenth of them, whichever is
  smaller. The queue is an operator surface (the dashboard's admin side and
  the bot's admin commands), not a public one.
- A review **writes a receipt of its own** (§5, `duel_review`): the reviewer,
  the instant, the evidence read (the second source, the venue's candle, the
  feed's final score), and the decision, which is one of `stands`, `push`
  (chips returned, marks cleared) or `corrected` (the other outcome; marks
  and chips re-settled). The original settlement receipt is never rewritten:
  the review names its seal and the day's root carries both.
- A settlement that was flagged but not reviewed by the window's end is
  released as it stood, and the flag count stays on its receipt page.

### 3.6 · The board

- The season board gains **return on float** (`(stack − 1000) / 1000`, as a
  percent) and **chips put / rounds with chips**, beside the marks it shows
  today. The public route stays `counts_only: true`; a percent and a count
  are the whole of what leaves the server.
- Marks and chips **coexist** for the first season. Marks remain the public
  record of being right; chips are the game of being right *with
  conviction*. Whether the stack replaces the mark is decided from that
  season's data, not now (open question 3 of the inputs doc, answered
  "coexist, then look").
- A player's own page shows their stack, their held chips, and each round's
  odds, chips and payout, three-valued: a figure, "not read" with its reason,
  or nothing. `Number(null)` is 0 and a stack nobody could read is not 0
  (`docs/lessons/ENGINEERING_LOG.md`, the honesty chapters).

## 4 · Schema

New tables, each with the reconcile entry the fast path needs
(`REQUIRED_*_COLUMNS` for a column added to a table production has;
`EXPECTED_TABLES` for a new table).

| table | columns | one row per |
|---|---|---|
| `duel_stacks` | `user_id`, `season`, `float INT`, `stack INT`, `held INT`, `updated_at`; PK `(user_id, season)` | player and season |
| `duel_chip_moves` | `id`, `pick_id`, `user_id`, `season`, `kind` (`put` `return` `win` `loss` `hold` `release`), `chips INT`, `at`, `receipt VARCHAR(64) NULL` | movement of chips |
| `duel_flags` | `id`, `pick_id`, `user_id`, `at`; UNIQUE `(pick_id, user_id)` | flag |
| `duel_reviews` | `id`, `round_id`, `reviewer_id`, `decision`, `evidence TEXT`, `seal`, `seal_payload`, `at` | review |

`duel_rounds` gains `odds TEXT NULL` (the JSON map, or NULL for a round dealt
without chips) and `odds_source VARCHAR(24) NULL`. `duel_picks` gains
`chips INT NULL` (NULL is "no chips were put", never 0) and
`payout_state VARCHAR(8) NULL` (`open`, `held`, `paid`, `returned`,
`released`). Every new column is reconciled on `migrate()`'s fast path in the
same commit (`app/test/fast_path_reconciles_every_later_column.test.js` holds
it).

## 5 · Seals (Provable Calls, `docs/PROVABLE_CALLS_SPEC.md`)

- **Round, `v: 8`.** The `v: 6` payload gains `odds` (the map or `null`) and
  `odds_source` after `dealt_at`, inside the hashed bytes. A round sealed
  before this carries neither and verifies as it did.
- **Call.** The call payload gains `chips` (an integer or `null`). The call
  is still free of outcome fields (rule 2).
- **Settlement, `v: 7` unchanged in kind.** Gains `chips`, `odds_used` (the
  decimal the payout used, copied from the round) and `payout` (the signed
  chip movement, or `null` while held) after `disputed`.
- **Review, `duel_review` (new kind).** `{v, kind: "duel_review", round_id,
  settle_seal, round_seal, decision, evidence, reviewed_at}`; a leaf in its
  day's root like any other.

The verify page replays all four in the browser; a receipt's `anchors` list
is unchanged.

## 6 · Surfaces

- **The duel page and the Arena panel** (`app/public/js/duel.js`, the shared
  renderer): the odds beside each pick, a chips field with the limits, the
  stack in the header, held chips named as held; the settled card shows
  "won 45 chips at 1.9" or "lost 50 chips" or "50 chips held (disputed,
  under review)". Strings in all fourteen languages.
- **The Pi app** (`app/public/js/pi-duel.js`): the same block, English only.
- **The Telegram duel bot and the main bot's card**
  (`bot/formatters/duel_card.py`): a chips row and three buttons (25, 50,
  100) beside the pick buttons; the stack on the season line.
- **Receipts** (`app/public/call.html`): the odds on a round receipt, the
  chips on a call receipt, the payout on a settlement, the review receipt.

## 7 · What stays out, on purpose

- No order book, no changing odds, no trading between players.
- No purchase, top-up, transfer or withdrawal of chips; no chip is a token
  and no copy says it could become one.
- Nothing on the trading path reads a chip: `bot/core/engine.py`,
  `bot/risk/` and `app/lib/duel_service.js`'s verdict are unchanged, and the
  guard test that keeps Flovia off that path
  (`tests/test_smart_money_is_a_line_and_never_a_verdict.py`) gets a sibling
  for chips.
- No DogeOS transfer in this step. The hackathon entry names "virtual stakes
  in testnet DOGE" as the step after; if that step comes, it is the player's
  own transaction, like the anchor, never the server's.

## 8 · Tests to write before anything ships

- A full season drive: deal with odds, call with chips, settle each result,
  hold on `disputed`, flag, review each decision, release at the window; the
  stack equals the sum of its moves at every step.
- Both arms of every refusal: chips over the maximum, over the stack, on a
  round without odds, after the call, by a player who did not call.
- The odds never read the stance: a direction round dealt with every stance
  produces the same odds (a fixture with the stance varied and the odds
  asserted equal).
- The board and the public route carry percent and counts only (the
  no-dollar sweep extended to the new payloads).
- Three-valued rendering on every surface: a stack that could not be read is
  "not read", a held payout is "held", an absent `chips` is nothing.
- Seals: a `v: 8` round, a call with chips and a settlement with a payout
  each verify in the browser; a `v: 6` round still does.
- Mutation targets: the maximum, the hold on `disputed`, the odds table
  lookup, the review decision switch, the fast-path reconcile.

## 9 · Rollout

1. Ship behind `DUEL_CHIPS_ENABLED` (off by default; the `.env.example` block
   regenerated from the inventory). Off, nothing changes anywhere.
2. Turn on for one season on the website and the Pi app; the Telegram duel
   bot the week after, once the cards have been seen in play.
3. Tune the odds table from that season's recorded results; never mid-season.
4. Decide marks-versus-stack from the same data.

## 10 · Open for the owner

1. The season float (1,000) and the per-round maximum (200): keep, or a
   smaller maximum so one round cannot swing a fifth of a season?
2. The review queue's threshold (three flags or a tenth of callers): keep, or
   send every `disputed` settlement to review and let flags only annotate?
3. The name "chips" and the verb "put": keep, or another pair? Whatever is
   chosen goes into fourteen languages, so the choice precedes the strings.
4. Should a review's `corrected` decision re-settle marks as well as chips,
   or chips only? This document says both; the spec would record it either
   way.
