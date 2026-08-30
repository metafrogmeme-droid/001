# Knowing it broke without looking

Every alert path in RUNECLAW runs **inside** the thing being monitored —
`bot/core/system_health.py`, the proactive monitor, the Telegram degraded
alerts. They are good at what they do and they share one flaw:

> **A bot that has died cannot tell you it died.**

That is why every recovery in the week of 2026-08-25 began with a human
noticing something looked off, and why the gateway tunnel spent **eighteen
days** giving up after five restart attempts with nobody the wiser. The fix is
not more in-process monitoring. It is one check that lives somewhere else.

Two layers, because they fail differently.

## Layer 1 — dead-man's switch (catches: everything died)

`heartbeat.sh` runs on the box, checks the gateway and bridge, and pings an
external service **only when both answered**. The service alerts you when the
pings stop.

Silence is the signal, so it survives what an in-process alert cannot report:
the process died, the box died, cron died, the network died.

### Setup — about five minutes

1. Create a check at <https://healthchecks.io> (free tier is enough).
   **Period 5 minutes, grace 10 minutes.**
2. Put its ping URL on the box, outside the repo:

   ```bash
   echo 'https://hc-ping.com/YOUR-UUID-HERE' > ~/.runeclaw-heartbeat
   chmod 600 ~/.runeclaw-heartbeat
   ```

   It is a **secret** — anyone holding it can silence your alerting by pinging
   it themselves. It is never logged.

3. Add it to cron:

   ```bash
   crontab -e
   */5 * * * * /home/mulerun/runeclaw/scripts/monitoring/heartbeat.sh >> /home/mulerun/runeclaw/logs/heartbeat.log 2>&1
   ```

4. Point the check's notifications at whatever you actually read — email,
   Telegram, a phone push. An alert on a channel you do not open is not an
   alert.

Test it before trusting it:

```bash
scripts/monitoring/heartbeat.sh            # expect "healthy", check goes green
GATEWAY_URL=http://127.0.0.1:9 scripts/monitoring/heartbeat.sh   # expect a /fail ping
```

**A check you have never seen fire is a green light of unknown meaning.** Do
the second command.

### The trap this avoids

The naive version is one cron line:

```bash
*/5 * * * * curl -fsS https://hc-ping.com/<uuid>      # DON'T
```

That pings whenever **cron** is alive. Cron is alive when the bot is dead, the
bridge is dead, the gateway is unreachable — so it shows green through an
entire outage and you learn to trust it. A heartbeat that fires regardless of
health is worse than none: it is a confident all-clear manufactured from no
evidence.

`heartbeat.sh` checks first, and sends **nothing at all** when it cannot run
the checks (no curl, no config). It must not report failure on the strength of
a broken harness, and it must not report success either — so it goes quiet and
lets the dead-man's switch alert on the timer. Silence is the honest output
when you do not know.

## Layer 2 — external HTTP probe (catches: the box is fine, the world can't reach it)

The heartbeat runs *on* the box, so it cannot see a broken tunnel, expired DNS,
a Cloudflare misconfiguration, or an expired certificate. Something outside has
to look in.

Any uptime service works — UptimeRobot, Better Stack, Cloudflare Health Checks.
Point it at:

| URL | healthy response |
|---|---|
| `https://humanoid-traders.com/readyz` | `200` |
| `https://gw.humanoid-traders.com/gateway/health` | `200`, **`401` or `403`** |

That second row matters. The gateway requires a secret, so **403 is the healthy
answer** — the server is up and correctly refusing an unauthenticated caller.
Configure the check to accept it, or you will be paged every five minutes for a
working system, and an alert that cries wolf is how a real one gets ignored.

## What this does and does not buy

It catches: the bot dying, the bridge never starting, the tunnel giving up, the
box going away, DNS or certificate expiry.

It does **not** catch a process that is alive and wrong — a stalled engine, a
stale deploy, a panel rendering an unreadable value as zero. Those need
`scripts/verify_deploy.sh`, `scripts/systemd/runeclaw-status.sh`, and the guard
tests. A green heartbeat rules one cause out. It does not name the cause.
