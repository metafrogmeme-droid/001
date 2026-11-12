---
name: bot-status
description: Read-only health report for the RUNECLAW bot processes (supervisor state and restart counts, running instances, health ports, scrubbed log tails). Use when asked whether the bot is up, why it keeps stopping, or what its logs say. It never restarts, kills, starts or writes anything.
---

# bot-status

Report on the bot. Never act on it.

## Run

From the checkout the bot runs from:

```bash
python3 scripts/bot_status.py              # last 40 log lines per source
python3 scripts/bot_status.py --lines 100  # more context (at most 200)
```

The exit code is the verdict:
- 0: up, serving, nothing wrong found.
- 1: something is wrong.
- 3: a check could not run, and nothing was found wrong. That is incomplete, not healthy.

## Rules

- **Read only.** Do not run `kill`, `pkill`, `nohup`, `systemctl start|stop|restart`,
  `launch_all.sh`, or anything else that starts, stops or signals a process. Do not
  delete, truncate or rotate a log. Recovery belongs to the supervisor
  (`scripts/systemd/`, `Restart=always`). A relaunch by hand next to it starts a second bot.
- **Never print a secret.** Quote only the script's own output: its log lines have been
  through `bot/utils/secret_shapes.py`. Never `cat` the `.env` file, an env file or a
  unit's environment, and never echo a variable whose name holds TOKEN, KEY, SECRET or
  PASSWORD.
- **Patrick's live server is his.** Run this there only when he asks. On the assistant
  box, run it freely; it changes nothing.
- **A green line rules one cause out; it does not name the cause.** Report what the
  script found, with the lines it quoted, and say which checks could not run.

## Reading the report

Report in this order:
1. **Supervisor.** `CRASHLOOP?` (more than 5 restarts) or `DOWN` on a unit. Give the
   restart count and the "since" time.
2. **Processes.** `TWO+` on `bot.main` means two bots share one Telegram token. Say
   so; do not pick one to kill.
3. **Ports.** `NOT ANSWERING` on the gateway (8080) or the bridge (8000). An HTTP 403
   from the gateway is normal: it answers 403 without its secret.
4. **Logs.** The last error-looking line of each source, and what comes before it.

Then name the next step for a person, from `docs/BOT_STATUS_RUNBOOK.md`. Do not take it.
