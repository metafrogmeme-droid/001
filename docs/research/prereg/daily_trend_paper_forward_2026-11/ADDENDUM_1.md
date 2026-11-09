# Addendum 1 to the daily trend paper forward test: the writer and the evaluator (2026-11-09)

Written and sealed before the window opens (2026-11-23 00:00 UTC) and before any paper entry
exists. It names the bytes of the two scripts that run this test and states how they read the
pre-registration's words. It changes no threshold, no window, no cost and no criterion in
`PREREGISTRATION.md` or `config.json` (config sha256 `db1669e6…5cac74`, unchanged).

## The scripts, sealed

- `scripts/daily_trend_paper_book.py`: `c20a995d9acf01a40d6660ff593434fed76897dc4a14eb6cf13558233491055f`
- `scripts/daily_trend_paper_eval.py`: `ecf431926defa6814ddc7f4d70dfafeed2f246233dcfce49d1ca2e65735d886b`

`tests/test_daily_trend_paper.py` fails if either file changes after this seal. A change needs a
new addendum sealed before the changed script runs on the test's chain.

The window opens on 2026-11-23 as registered. If the writer is not running on the paper process
by then, Addendum 2 names the later Monday, as the shift rule says.

## How the scripts read the pre-registration

1. **The rule.** `read_book` runs in its own interpreter inside a `git archive` of commit
   `035cf384c…`, unpacked to a temporary directory outside the repository. Its environment carries
   only PATH and HOME. Before each run, the sha256 of the three rule files is checked against
   `config.json`. Each decision gets the 130 days of closed 1h candles before its 00:00 UTC cutoff;
   the rule needs 121 daily bars.
2. **The mark.** The mark is the close of the last 1h candle that opened in the 24 hours before
   00:00 UTC, which is the daily close the rule read. A held market with no such candle is carried
   at its last close and counted. A held market with no mark at all reads as unknown, never as
   zero weight: it can be exited, not rebalanced.
3. **The fill.** A trade whose 00:00-01:00 candle is missing is not made that day. It is counted,
   and the next day decides again.
4. **Funding.** Funding is charged at each published settlement on the quantity held at that
   moment. A settlement exactly at the fill time is paid on the quantity before the fill. The price
   is the close of the 1h candle ending at the settlement; if that candle is missing, an earlier
   close in the prior 24 hours is used and counted.
5. **Covered day (criterion 5).** Both the decision's `read_at` and the chain's own timestamp on
   the entry fall between 00:00 and 01:00 UTC of its day.
6. **Replay match (criterion 6).** For every market, the recorded target, held weight and
   after-trade weight equal the replay's within 1e-6. An unknown matches only an unknown.
7. **Anchored day (criterion 7).** A `DT_HEAD_PUBLISHED` entry for day D, stamped on D, names a
   head at or after D's decision. The post goes to the operator's alert chat
   (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`, as `scripts/health_check.sh` uses them). A
   failed post is recorded with its exception class only.
8. **The record also fails (NO VERDICT) in two more cases.** First, when `AuditChain.verify`
   reports a break. Second, when a head in a committed `heads/*.txt` file is not the chain's entry
   at that sequence. These are how items 3 and 4 of "How the results are logged" are checked.
9. **Counting.** For the close rule, an episode counts when its exit filled on a day before the
   close. Drawdown is measured on the daily marks from the open through the close day's mark. The
   window's decision days run from the open up to, not including, the close.
10. **Status and figures.** `status` counts closed episodes from the chain's decisions and fills,
    and prints no figure. Every figure in `result.json` comes from the evaluator's replay of public
    data, never from the writer's numbers.
11. **No key.** The writer reads no trading or signing key. The chain's attestation signing, which
    would read one, is never reached. The script refuses to run when the environment is live
    mainnet (`bot.core.testnet_book.deployment_kind`).

## Not decided here

`OPERATING.md` in this directory says how the scripts are scheduled and run. It is not sealed,
and nothing in it can change a reading above.
