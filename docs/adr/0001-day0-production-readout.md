# ADR 0001: Day-0 production readout (the D0 appendix)

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** D0 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "D. Capital gate and execution integrity"; "First two weeks", step 1)
- **Informed by:** nothing yet. **The readout has not been run on the bot box.** No figure in this ADR is a production measurement.

## Context

Every capital, budget and research decision in ADRs 0002 to 0010 assumes
production values the repository does not hold. The code defaults and the
documented install disagree: `bot/config.py` `AppConfig.auto_confirm_live_enabled`
defaults on and `AppConfig.auto_confirm_threshold` to 0.85, while `.env.example`
sets `AUTO_CONFIRM_LIVE_ENABLED=false` and `AUTO_CONFIRM_THRESHOLD=1.0`;
`LLMConfig.daily_budget_usd` defaults to 1.0 and `.env.example` sets 5.0. Which
of these production holds is unknown. C2 is runnable only if
`data/learning/llm_calibration.jsonl` holds enough LLM-sourced rows, which is
also unknown.

`scripts/production_readout.py` is the read-only readout. It reports each flag
the plan names as `bot.config` resolves it (an unparseable value is the default
in force, with `config.ENV_UNREAD`'s reason); row counts and first/last dates of
the calibration, order-flow, decision-memory and closed-trade ledgers; the
LLM/RULE_ENGINE share; the venue-priced share of the newest 50 filled closes
(`close_lookup.is_ticker_priced` plus the record's `fill_source`,
`close_price` and `close_lookup`); budget-exhausted and tick-health audit counts
from `logs/`; the eligibility verdict and strategy-hash prefix; and adopted
positions with no margin on record. Every field is a reading, `absent` or
`unreadable` with the exception class. It prints no secret and no P&L.
`tests/test_the_production_readout_says_what_it_read.py` drives it.

## How to run it

On the bot box, as the user the bot runs as, from the checkout it runs from
(the `WorkingDirectory` of `scripts/systemd/runeclaw-bot.service`), with the
interpreter the bot runs with (the unit's `ExecStart` interpreter, or
`.venv/bin/python` where the box has one, which the launcher prefers), so it
reads the same code, `.env`, `data/` and `logs/`:

```bash
"$PY" scripts/production_readout.py --markdown > /tmp/d0-readout.md
"$PY" scripts/production_readout.py --json > /tmp/d0-readout.json
```

where `$PY` is that interpreter. A `python3` without the bot's dependencies
cannot import `bot.config`, and every flag then reads `unreadable` with the
exception class; that is a failed run, not a reading.

The script must be the copy inside that checkout. `bot.config` reads the
`.env` beside its own package and resolves every value with its own code, so
run from another clone with `--root` pointed at the bot's checkout it would
state the clone's settings as the bot's. It refuses to: the ledgers and logs
are read from `--root` and every flag reads `unreadable (--root is not the
checkout this script imports bot.config from)`. Until the branch that adds the
script is deployed there, put a copy at `scripts/production_readout.py` in the
bot's checkout for the run and remove it afterwards (the strategy hash covers
`bot/` only, so the copy does not change it).

Paste `/tmp/d0-readout.md` under the appendix heading below, set the date it was
run, and commit. It reads the environment the shell hands it plus `.env`; the
bot's unit injects nothing else today, but a value exported only in a
supervisor's environment would not be seen, and the `Source` column says
`code default` for it.

## What it cannot read, stated

- The monitor-pass gap: this build records none (plan item D3 adds it), and
  nothing it does record stands in for one. The readout counts the tick-timeout
  audits and the `monitor_liveness` audit, which watches the proactive ALERT
  monitor's heartbeat, not the position monitor's passes.
- Closed trades older than the executor's cap: it keeps the newest 500 rows
  of a closed record, so a record at the cap is a floor and its first date is
  the oldest row kept. The readout says so on the row.
- A runtime override in the running process (`/golive`, `/leverage`, the
  adaptive auto-confirm threshold): it reads configuration, not process memory.
- Logs older than what rotation kept (10 MB x 5 per channel).

## Decision

Decision: pending operator. Accepting this ADR means running the command above
and committing its output below before any D1, E1 or C2 decision.

## Appendix: production readout

Not yet run. To be filled from `/tmp/d0-readout.md`.
