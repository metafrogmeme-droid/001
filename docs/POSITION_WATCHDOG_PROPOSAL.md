# Position watchdog: deploy proposal

**Status: a proposal. Nothing here is installed or switched on.** The code is
`bot/position_watchdog.py`. The unit and the timer are in `scripts/systemd/`
and nothing in the repository installs, enables or starts them.

## What it does

One pass every five minutes, from a systemd timer, in its own process:

1. Reads the operator's open Bitget positions, the resting plan and TP/SL
   orders, and the account equity.
2. Checks that every open position has a stop-loss and a take-profit resting.
3. Adds the open losses to today's realized result and holds the sum to the
   daily stop (`MAX_DAILY_LOSS_PCT` of equity, with the
   `DAILY_LOSS_BREAKER_MIN_USD` floor, as the bot's own breaker reads them).
4. Sends one Telegram message for what it found. A finding that has not
   cleared is repeated at most once per re-alert window.

Each check has three answers. A stop is present, absent, or unread; an unread
one is reported in its own words and is never counted as "no stop". The same
holds for the daily stop: a figure that could not be read is said to be
unread, not taken as zero.

## What it never does

It never places, amends or cancels an order, and it changes no account
setting.

- Its client refuses every request that is not a GET before the request is
  sent.
- Nothing it imports, at any depth, contains a call that places, amends or
  cancels an order. The executor, the engine and the signed REST client are
  not in its import closure.
- It builds no engine and writes none of the bot's files. The one file it
  writes is its own record of what it last alerted,
  `data/position_watchdog_state.json`.

`tests/test_the_position_watchdog_only_reads.py` fails if any of the three
stops being true.

## What it reads, and with what

| What | Where from |
|---|---|
| Positions, plan orders, equity | Bitget, through the client the bot's venue adapter builds, with the operator's existing keys |
| Today's realized result | The bot's own saved risk state (`data/combined_state.json`), with the bot's day rule |
| The daily stop | `MAX_DAILY_LOSS_PCT`, `DAILY_LOSS_BREAKER_MIN_USD` |
| The alert chat | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`, as `scripts/health_check.sh` uses them |

No new credential and no change to how keys are handled. Realized results come
from the bot's record, not from the venue, so a close the bot never booked is
not in the figure.

## Settings

All in the bot's `.env`. None of them is a safety flag, and none exists until
it is added.

| Setting | Default | Meaning |
|---|---|---|
| `POSITION_WATCHDOG_ENABLED` | off | Off: a pass reads nothing and exits 0 |
| `POSITION_WATCHDOG_REALERT_MIN` | 60 | Minutes before a finding that has not cleared is repeated (5 to 1440) |
| `POSITION_WATCHDOG_GRACE_S` | 120 | A position younger than this is not reported for missing legs (0 to 3600) |

The interval between passes is in the timer file (`OnUnitActiveSec=5min`).

## Trying it first

With `POSITION_WATCHDOG_ENABLED=true` set, a dry run prints what a pass would
send and sends nothing:

```bash
.venv/bin/python -m bot.position_watchdog --dry-run
```

Do this before installing the timer. The venue's row shapes were taken from
the client library's parser and its documented samples; no Bitget call was
made while writing this. See "Not verified" below.

## Installing

```bash
sudo cp scripts/systemd/runeclaw-position-watchdog.service /etc/systemd/system/
```

```bash
sudo cp scripts/systemd/runeclaw-position-watchdog.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now runeclaw-position-watchdog.timer
```

Adjust `User=`, `WorkingDirectory=` and the `ExecStart` path in the service to
match the box first. To read the last passes:

```bash
journalctl -u runeclaw-position-watchdog -n 50
```

A pass exits 0 when it finished, 1 when an alert that was due could not be
sent, and 78 when it is switched on without the Bitget keys or the alert chat.

## Removing

```bash
sudo systemctl disable --now runeclaw-position-watchdog.timer
```

Or set `POSITION_WATCHDOG_ENABLED` back to off: the timer then starts passes
that read nothing.

## Not verified

No venue was contacted. These rest on the client library's parser, driven in
the tests with payloads shaped like its documented samples:

- That a closing trigger order on the Classic API states `tradeSide: close`
  (or `reduceOnly`) in the plan-order listing. If it states neither, the stop
  is reported as "not confirmed" rather than as present. The dry run shows
  which.
- The unified-account (UTA) row shapes. The operator's account answers as
  Classic, so this path is not the one in use.

## Not done here

- The watchdog reports. It does not repair: re-placing a stop stays the bot's
  job.
- It is not added to `runeclaw-status.sh` or the launcher template. That
  belongs with the decision to deploy it.
