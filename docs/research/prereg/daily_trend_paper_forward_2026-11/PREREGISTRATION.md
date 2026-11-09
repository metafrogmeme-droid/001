# Daily trend sleeve, paper forward test: pre-registration (2026-11-09)

Written and sealed before the window opens, before any paper fill exists, and before the code
that writes or evaluates the paper book is written. Paper only: nothing is placed on any venue,
mainnet or testnet, no key is read, and no live trading setting changes. Turning live trading off
or shrinking it is the owner's separate call and is not part of this test.

## Why

The weekly parity card of 2026-11-09 measured the live hourly book at a negative edge: mean net
per trade below zero over 249 strategy exits, with a 95% interval that excludes zero. Its
benchmark is also below break-even (profit factor 0.85), so the live book is following a model
with no edge. The only rule in this project's pre-registered line that passed its bar is the
long-only daily trend rule (H1b): development Sharpe 2.12, holdout 0.63 on Kraken spot history,
0.49 to 0.54 once re-costed with real perp funding (`docs/research/prereg/kraken_spot_history/`,
`docs/research/funding_study_2026-10-17.md`). Whether it makes money **per trade, after fees,
slippage and funding, on dates nobody has seen** is the question here. A PASS is the minimum
evidence before anyone writes a live proposal for this rule. It is not a live go.

## The strategy and its config hash (fixed now)

The whole configuration is `config.json` in this directory:

- **config sha256: `db1669e6faa6393d1957a88912c21dafc92652edd5a142c393a86c35e25cac74`**
  (also in `config.json.sha256`). Any change to `config.json` after this seal is a mismatch.
- **Rule code:** `bot.core.daily_trend.read_book` as it stands at commit
  `035cf384c99035ab81f895882369491d7b16db17`. The git blob and sha256 of
  `bot/core/daily_trend.py`, `bot/core/ma_trend.py` (the 1h-to-1d resampling) and
  `bot/core/strategy_gate.py` at that commit are in `config.json`. The evaluator runs the rule
  from an archive of that commit (`git archive`, unpacked outside the repository), not from
  whatever `bot/` holds later.
- **Preset:** the 'daily trend' preset as it stands at that commit: 16 USDT-M perps (ADA, ALGO,
  ATOM, BCH, BTC, DOGE, DOT, ETC, ETH, FIL, LINK, LTC, TRX, UNI, XLM, XRP), lookbacks 20/60/120
  days, 30-day realised vol, 20%/yr target vol split across the markets readable that day, cap 1x
  notional per market, rebalance band 10%. This is the pre-registered sizing, **not** the B1v
  testnet overlay (60-day vol, 2x leverage cap), which never passed a test of its own.
- **Rebalance:** per market, with `w` the held notional over book equity at the 00:00 UTC mark
  and `t` the rule's target weight: exit when `t = 0`, enter when `w = 0` and `t > 0`, otherwise
  trade to `t` only when `|t - w| > 0.10 * w`. A market that does not read that day is neither
  traded nor dropped.

## Paper execution

- **Decision:** once per UTC day, from the daily bar that closed at 00:00 UTC, built from closed
  1h bars as the preset says.
- **Fill price:** the close of the Bitget USDT-M 1h candle 00:00 to 01:00 UTC of the same day.
- **Costs:** fee 0.06% per side (Bitget's standard taker rate) plus slippage 0.05% per side, on
  traded notional: 0.11% per side, 0.22% a round trip. Stress case: both doubled.
- **Funding:** every settlement Bitget published for the symbol, on the notional held at that
  time. Longs pay a positive rate and receive a negative one.
- **Mark:** daily at the 00:00 UTC close. The book starts flat at equity 1.0; every figure is a
  fraction or a count.

## What a trade is

A **holding episode** in one market. It opens on the fill that takes the market's weight from
zero to above zero, and closes on the fill that returns it to zero. Every rebalance fill, fee,
slippage charge and funding payment in between belongs to it. **Episode net** is the sum of
those over the episode, divided by book equity on the day it opened. Episodes still open when the
window closes are marked at the final close. They count in book equity and drawdown, never in
the episode count or the expectancy.

## Window and minimum count

- **Opens:** 2026-11-23 00:00 UTC. If the paper writer (below) is not running on the paper
  process by then, the window opens on the first Monday 00:00 UTC after it is. That date goes
  in an addendum sealed before that Monday, and nothing else may change.
- **Closes:** on the first Monday 00:00 UTC on which **at least 26 weeks** have passed **and at
  least 150 episodes** have closed, read from the counts-only status. Hard close at **52 weeks**.
  A window that reaches 52 weeks with fewer than 150 closed episodes is **NO VERDICT** and is
  not extended.
- **Early stop:** the window closes as **FAIL** on the first daily mark at which the book's
  drawdown exceeds 15%.

## Pass rule (all must hold; any miss is a FAIL with its numbers)

1. **Expectancy:** the mean episode net is above zero, and the 5th percentile of a weekly-block
   bootstrap of that mean is above zero. Blocks are the ISO week of episode close; 10,000 draws,
   seed 7. This is a one-sided 95% lower bound.
2. **Cost stress:** the same mean, with fee and slippage doubled, is above zero.
3. **Max drawdown:** the largest peak-to-trough fall of the daily marked equity is at most 15%.
4. **Book net:** book equity at the close, open episodes marked, is above 1.0.

The record must also stand up. If any of these miss, the verdict is **NO VERDICT** (operational),
whatever the returns say:

5. At least 95% of window days have a paper-chain entry that was read at or before 01:00 UTC.
6. On at least 99% of those days, the recorded targets and trades equal the replay from public
   data at the pinned commit (weights within 1e-6).
7. At least 95% of window days had their chain head published outside the server that same day.

Reported beside the verdict, deciding nothing: the episode return on its own notional, win rate,
profit factor, median holding days, turnover, fees, slippage and funding as shares of gross, each
market's contribution, BTC buy-and-hold over the same days, and every number counted out
(missed days, unread markets, mismatched days).

## How the results are logged so they cannot be edited afterwards

1. **The plan is sealed in git.** This file, `config.json` and their sha256 sidecars go to `main`
   through a reviewed merge before the window opens. `main` takes no force-push. Anyone can
   check the seal with `sha256sum -c config.json.sha256 PREREGISTRATION.sha256` in this directory.
   When the result lands, the study is added to `benchmark/studies.json`, and from then on
   `scripts/verify_evidence.py` re-hashes it on every run.
2. **The decisions can be rebuilt by anyone.** Every target and trade is a fixed function of
   Bitget's public candles and funding and of the code at the pinned commit. The evaluator
   rebuilds the whole book from public data, so no figure depends on trusting a server file.
3. **The live paper record is hash-chained.** A paper process (never a live mainnet one) writes
   one entry per UTC day to its own file through `bot/utils/audit_chain.py`. The entry holds the
   day, `read_at`, the config sha256, the sha256 of the running rule files, every market's target
   and held weight, and each paper fill (price source, price, fee, slippage). Editing, deleting,
   reordering or inserting an entry breaks the chain, and `verify()` names the break.
4. **The chain head leaves the server every day.** After each entry the process posts the head
   (sequence number and sha256) to the operator's alert chat, where the messaging service
   timestamps it. Once a week the heads are also committed to this directory through a pull
   request. Rewriting the file and recomputing the whole chain would then disagree with heads
   already published.
5. **The result is written once.** The evaluator fetches the public inputs once, after the close,
   and records every input file's sha256 before computing anything. It writes `result.json`, and
   refuses to run again while a result exists. The result and its sha256 are committed through a
   pull request. Before the close, `status` prints counts only (days recorded, days missed,
   episodes closed, weeks elapsed, chain verifies yes/no, drawdown limit breached yes/no), never
   a return.

## What has to be built before the window opens (none of it touches live trading)

- **Paper writer:** runs only on a paper process, refuses to start on a live mainnet process,
  places nothing anywhere, and reads no key. It does items 3 and 4 above.
- **Evaluator** (`status` and `evaluate`), with tests that drive both arms of every criterion.
- **Addendum 1:** records the sha256 of the writer and the evaluator before the window opens,
  and the shifted open date if the shift rule applies.

## What each verdict leads to

- **PASS:** grounds for the owner to consider a separate, sealed live proposal for this rule,
  with its own caps, pause and eligibility record. It changes no setting by itself and says
  nothing about the hourly book.
- **FAIL:** the rule is not proposed for live. The 12-month sleeve reading
  (`docs/research/daily_trend_sleeve_prereg.md`) still stands on its own date.
- **NO VERDICT:** the same as FAIL for any live decision. The window is not re-run with changed
  numbers.

## Relation to the sleeve's 12-month reading

`docs/research/daily_trend_sleeve_prereg.md` records the same rule's target book daily and
forbids reading that record before 2027-10-17. This test does not read that record or change it.
But this window covers many of the same days, so evaluating it before 2027-10-17 means the
evaluator will have seen most of the sleeve's period. Both rules and both pass rules are fixed,
so nothing either one decides can be re-chosen after a look. Still, that reading's "first look"
clause would no longer hold literally. The owner decides this by merging this file: a merge
accepts it. The alternative is to hold this evaluation until on or after 2027-10-17, via an
addendum sealed before the window opens.

## Power, written now

On history already read (Bitget daily bars 2024-02 to 2026-10, the 7 of the 16 markets present;
for sizing the test only, never as evidence), the rule closed about 77 episodes a year on 7
markets. That suggests roughly 175 a year on 16. Episode net averaged +8.4 bp of book with a
standard deviation of 67.7 bp, and median holding was 4 days. If the true edge equals that
estimate, criterion 1 passes at 150 episodes with probability of about 0.45, and at 250 about
0.62. Those figures assume independent episodes; crypto markets move together, so the real
power is lower. A small real edge may well fail to show here. That asymmetry is deliberate:
going live on noise is what the parity card just measured.

## Prediction, written now

The mean episode net comes out slightly positive, and criterion 1's lower bound does not clear
zero. FAIL on criterion 1 is more likely than PASS. Drawdown stays under the limit; the
Kraken holdout drawdown was 10.1%.

## Limits

Paper fills at a 1h close assume the size trades at that price plus 0.05%. They do not measure
depth, and a testnet book would not either, so no testnet fill decides anything. One venue, one
universe chosen by past market cap (survivorship), one window. Funding is real; spreads are not
measured.
