# Running the daily trend paper forward test

Not sealed. The rule, the window and the pass rule are in `PREREGISTRATION.md`, `config.json`
and `ADDENDUM_1.md`. These notes cover running the two scripts on the paper process.

Paper only: the scripts place nothing on any venue and read no trading or signing key. They
refuse to run in a live mainnet environment. Run them with the paper process's environment.
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALERT_CHAT_ID` are needed for the daily head post.

## Before 2026-11-23

Check once that public fetch, the pinned rule and the post all work, writing to a throwaway
chain (never the test's own):

```bash
python3 scripts/daily_trend_paper_book.py selftest --chain /tmp/dt_selftest.jsonl --publish
```

The checkout must contain commit `035cf384c…`. A `git pull --ff-only` of `main` does.

## Every day (cron, UTC)

```cron
40 0 * * *  cd /path/to/checkout && python3 scripts/daily_trend_paper_book.py decide >> data/logs/dt_paper.log 2>&1
10 1 * * *  cd /path/to/checkout && python3 scripts/daily_trend_paper_book.py fill   >> data/logs/dt_paper.log 2>&1
```

- `decide` must finish by 01:00 UTC, or the day is not covered.
- `fill` records the 01:00 prices and refuses until that candle has closed.
- Both commands do nothing on a day that is already recorded.
- The chain is `data/learning/daily_trend_paper_chain.jsonl` (`DAILY_TREND_PAPER_CHAIN` overrides
  it). The writer's own data cache is `data/learning/daily_trend_paper_cache/`.

## Every week

Export the chain heads and commit them through a pull request:

```bash
python3 scripts/daily_trend_paper_book.py export-heads \
  --out docs/research/prereg/daily_trend_paper_forward_2026-11/heads/$(date -u +%G-W%V).txt
```

## Any time

```bash
python3 scripts/daily_trend_paper_eval.py status
```

`status` prints counts only and tells you when the window has closed.

## Once, after the close

```bash
python3 scripts/daily_trend_paper_eval.py evaluate --fetch
```

This fetches the public inputs once into `inputs/` and writes `result.json` once. Commit
`inputs/`, `result.json` and `result.json.sha256` through a pull request. Then add the study to
`benchmark/studies.json`, so that `scripts/verify_evidence.py` re-hashes it.
