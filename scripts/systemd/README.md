# Supervising the RUNECLAW processes

`Restart=always` for `bot.main` and `api_bridge`, so a death at 03:00 is
recovered at 03:00:15 instead of whenever somebody next opens Telegram. A
third unit, `runeclaw-duel`, is optional: the Daily Duel's own Telegram bot,
installed only on a box that has created it (below).

## Why

Across the week of 2026-08-25 the bot died repeatedly and **every recovery
began with a human noticing**. One of those deaths was caused by a quickfix
that restarts the box's system services; nothing brought the bot back. The
bridge outage earlier the same day was worse in kind — nothing had crashed,
nothing had ever *started* it, and the status page called the system healthy
while three panels 502'd.

The launcher (`scripts/launch_all.sh.template`) already refuses to print
`DEPLOY_DONE` unless both processes are alive and both ports answer. That is a
**deploy-time** gate and it is doing its job. It has nothing to say about
Tuesday at 3am. These units are the other half, and they make the same argument
`scripts/cloudflared/runeclaw-gateway.service` already makes for the tunnel.

## Install

```bash
sudo cp scripts/systemd/runeclaw-bot.service    /etc/systemd/system/
sudo cp scripts/systemd/runeclaw-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now runeclaw-bot runeclaw-bridge
scripts/systemd/runeclaw-status.sh
```

### The optional third unit: `runeclaw-duel`

`python3 -m bot.duel_bot` runs the Daily Duel on its own BotFather token
(`DUEL_BOT_TOKEN` in the bot's `.env`), with no engine and none of the main
bot's state; health on `:8081`. Install it only once the token is set:

```bash
sudo cp scripts/systemd/runeclaw-duel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now runeclaw-duel
scripts/systemd/runeclaw-status.sh
```

A box without the token leaves the unit uninstalled, and the status script
lists it as `OPTIONAL`, which is not a failure. Installed with no token, or
with the main bot's token, the process exits 78 and the unit's
`RestartPreventExitStatus=78` keeps it from looping: one journal line, a
stopped unit, and `runeclaw-status.sh` says DOWN. Every other exit restarts,
as the other units do.

### The optional fourth unit: `runeclaw-daily-duel`

`python3 -m bot.daily_duel_bot` is the anonymous Daily Duel bot. Its token is
`DAILY_DUEL_BOT_TOKEN` and its id salt is `DAILY_DUEL_ID_SALT`, both in the
bot's `.env`, neither committed. Health on `:8082`. It builds no engine and
signs nothing. Sealing needs `node` on PATH, because the receipt is made by
`app/lib/daily_duel_gate.js`. Install it only once both values are set:

```bash
sudo cp scripts/systemd/runeclaw-daily-duel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now runeclaw-daily-duel
scripts/systemd/runeclaw-status.sh
```

A box without the token or the salt leaves the unit uninstalled, and the
status script lists it as `OPTIONAL`. Installed without either value, or with
a token equal to `TELEGRAM_BOT_TOKEN` or `DUEL_BOT_TOKEN`, the process exits
78 and does not restart.

The same process sends the approval hygiene digest. There is no fifth unit.
`/watch` opts a chat in (up to 5 addresses), `/digest` sends one now, and
`/stop` deletes the addresses and the schedule. The store is
`approval_watches.sqlite` under `DAILY_DUEL_STATE_DIR` (default
`data/daily-duel`). The chat id is not written there; it is an HMAC with
`DAILY_DUEL_ID_SALT`, same as a duel id. Chain reads use `ETHERSCAN_API_KEY`
when that is set, and the public Routescan explorer when it is not. The
weekly check is inside the process: `APPROVAL_DIGEST_INTERVAL_SEC` (default
604800) and `APPROVAL_DIGEST_POLL_SEC` (default 3600). After a restart the
next message from that chat binds delivery again, and a digest that came due
while the process was down is sent then.

Check three things in the unit files before installing — all three are box
facts this repo cannot know:

| line | check |
|---|---|
| `User=mulerun` | the account that owns `~/runeclaw` |
| `WorkingDirectory=` | the real repo path |
| `ExecStart=/usr/bin/python3` | **a virtualenv needs `<venv>/bin/python`** |

A wrong interpreter path fails at start, loudly, which is the good failure. A
*right-looking* one that resolves to a Python without the dependencies fails on
import — check `journalctl -u runeclaw-bot -n 50` after the first start.

`python3`, not `python`: the box has no `python`, as
`scripts/verify_bot_alive.sh` records.

## `systemctl status` will lie to you, and it is not a bug

Both units set `StartLimitIntervalSec=0`, so systemd **never gives up**. That
is the entire point — a supervisor that stops retrying after five attempts has
reproduced the outage it was installed to end.

The cost is visibility. Fifteen seconds after a crash the unit reads
`active (running)` again, so:

- a process that has died 200 times today, and
- a process that has run untouched for a week

are **indistinguishable** to `systemctl status`. `NRestarts` is the number that
separates them:

```bash
scripts/systemd/runeclaw-status.sh          # state + NRestarts + port, per unit
systemctl show -p NRestarts --value runeclaw-bot    # the raw number
journalctl -u runeclaw-bot -n 50 --no-pager         # why it died
```

`runeclaw-status.sh` reports three outcomes, not two — a unit that was never
installed is **not** a stopped one, and no systemd at all is not a verdict
about the bot. Printing OK for either would tell an operator their processes
are supervised when nothing is watching them.

## What is deliberately NOT in the units

**The source check.** `scripts/verify_deploy_source.sh` answers "is this the
code you think it is", and it belongs in the deploy path, where a wrong answer
should stop everything. It must **not** gate a restart: it reads the network,
it exits 3 when it cannot reach it, and a unit that refuses to restart the bot
during a network blip is a supervisor that fails exactly when it is needed. The
2026-08-20 stale-deploy incident is real and this is not its fix.

**A dependency between the two units.** They are independent processes on
independent ports. Ordering them would mean one failing to start keeps the
other down, and the whole reason the bridge was missing for hours is that its
fate was quietly tied to somebody remembering it.

**`Restart=on-failure`.** The 2026-08-01 failure was the bot exiting **zero** —
`--mode cli` finds no TTY and quits cleanly. `on-failure` would not restart
that. `always` does.

## After installing, stop starting them by hand

`launch_all.sh` and these units will fight: run the launcher after
`systemctl enable --now` and you get two bots, both bound to :8080, one of them
losing. Once the units are in place, keep each restart in its own invocation.
Chaining work after a restart has terminated the invoking SSH session with
exit 143; the units perform their own port waits, and verification follows.

```bash
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
```

Then restart through the manager that holds the units. Installed as above,
they are **system** units:

```bash
sudo systemctl restart runeclaw-bot
```
```bash
sudo systemctl restart runeclaw-bridge
```

The production box has been restarted with `systemctl --user` (2026-09-17),
which means its units live with the **user** manager instead:

```bash
systemctl --user restart runeclaw-bot
```
```bash
systemctl --user restart runeclaw-bridge
```

`runeclaw-status.sh` says which, on each unit's line (`system unit` or
`user unit`), so run it first if you are not sure. Then, as its own command:

```bash
scripts/systemd/runeclaw-status.sh || { echo "DEPLOY FAILED"; exit 1; }
```

which keeps every guarantee the launcher had — right code, both processes, both
ports answering — and adds the one it never had. For a user unit the logs are
`journalctl --user -u runeclaw-bot`.

## Logs move to journald

The units send stdout/stderr to the journal rather than to `./bot.log`. The
launcher template appends to the **repo root**, which is not in the persistent
store, so `git reset --hard` discards it — taking the log of the failure that
prompted the redeploy with it.

The engine's own audit trail is unaffected: `logs/*.jsonl` is symlinked into
the persistent store by `deploy.sh`, including the tamper-evident
`logs/audit_chain.jsonl`. This change is about console noise and tracebacks.

```bash
journalctl -u runeclaw-bot -f
journalctl -u runeclaw-bridge --since "1 hour ago"
```
