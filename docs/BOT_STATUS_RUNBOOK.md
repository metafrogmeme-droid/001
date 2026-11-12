# Bot status: a read-only runbook

`scripts/bot_status.py` answers "is the bot up, and if not, why?" in one report. The
`bot-status` skill (`.claude/skills/bot-status/SKILL.md`) runs it and reads the report back.
Neither one restarts, kills, starts or writes anything. Recovery belongs to the supervisor
(`scripts/systemd/`, `Restart=always`) or to a person.

```bash
python3 scripts/bot_status.py              # exit 0 healthy · 1 something wrong · 3 could not tell
python3 scripts/bot_status.py --lines 100
```

## What it checks

| section | how | flags |
|---|---|---|
| Supervisor | `scripts/systemd/runeclaw-status.sh`, unchanged | `DOWN`, `NOT INSTALLED`, `CRASHLOOP?` (more than 5 restarts), `NOT SERVING` |
| Processes | `pgrep -f` per entry point, `ps -o etimes` for uptime | `DOWN` for `bot.main` or `api_bridge`; `TWO+` for more than one `bot.main` |
| Ports | one GET, no header, to `:8080/gateway/health`, `:8000/health`, `:8081`, `:8082` | `NOT ANSWERING` (any HTTP status, 403 included, is an answer) |
| Logs | `journalctl -u <unit> -n N`, system then user; launcher logs `bot.log`, `api_bridge.log`, `duel_bot.log`, `daily_duel_bot.log`, `logs/bot.log` | the last error-looking line of each source |

Every log line goes through `bot/utils/secret_shapes.py` (secret shapes redacted, URL
queries dropped) before it is printed. The script never opens `.env`, and it refuses an
env file passed with `--log`. Process command lines are read only to skip the script's
own processes, and are never printed.

## What to do with each finding (a person does these, not the skill)

- **`CRASHLOOP?`.** Do not restart it again. The supervisor already is. Read
  `journalctl -u runeclaw-bot -n 200 --no-pager` (add `--user` for a user unit), find the
  first error after a start, and fix that. A count that keeps climbing is the failure.
- **`DOWN` / `NOT INSTALLED`.** Nothing supervises the process. Install or enable the
  unit (`scripts/systemd/README.md`). Do not keep it alive with `nohup`: the launcher and
  the units are never run together (CLAUDE.md).
- **`TWO+` `bot.main`.** Two bots share one Telegram token and one set of state files.
  Find which one the supervisor owns (`systemctl status runeclaw-bot`, its MainPID) and
  stop the other through whatever started it.
- **`NOT ANSWERING` with the process running.** The process is up but not serving. Read
  its log tail. `scripts/systemd/wait_for_port.sh` is the start-time gate for the same
  question.
- **Exit 3 (could not tell).** A tool was missing (`systemctl`, `pgrep`, `journalctl`), or
  no log was found. Say which, and do not read the rest as healthy.

## Where to run it

- **Assistant box:** freely. It changes nothing.
- **Patrick's live server:** only when he asks.
