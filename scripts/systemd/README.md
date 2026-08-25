# Supervising the two RUNECLAW processes

`Restart=always` for `bot.main` and `api_bridge`, so a death at 03:00 is
recovered at 03:00:15 instead of whenever somebody next opens Telegram.

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
losing. Once the units are in place the deploy becomes

```bash
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
sudo systemctl restart runeclaw-bot runeclaw-bridge
scripts/systemd/runeclaw-status.sh || { echo "DEPLOY FAILED"; exit 1; }
```

which keeps every guarantee the launcher had — right code, both processes, both
ports answering — and adds the one it never had.

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
