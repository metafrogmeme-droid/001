# Named Cloudflare tunnel for the bot gateway

The website reaches the bot's user gateway (web chat, manual trades) over a
Cloudflare tunnel. Today that is a **quick tunnel**, and the URL it hands out
is bound to the `cloudflared` **process**:

```
https://<four-random-words>.trycloudflare.com
```

(The current one is deliberately not written down here. It changes, and a
committed value that changes is a wrong value with a plausible look.)

Restart `cloudflared` — a reboot, an OOM kill, an upgrade — and the URL
changes. The website's `BOT_GATEWAY_URL` still points at the old one, web chat
goes down, and it stays down until somebody notices and hand-edits two configs
on two hosts. That is the outage of 2026-07-28, and it will recur.

## Two failure modes, not one

Worth separating before you start, because fixing one and believing you fixed
both is the usual way this bites twice:

| failure | fixed by |
|---|---|
| the URL changes on restart | a **named** tunnel — the hostname is a DNS record you own, not a process artifact |
| the tunnel process is simply down | a **supervisor** — `runeclaw-gateway.service` here |

A named tunnel whose process died at 03:00 and was never restarted is exactly
as unreachable as a quick tunnel whose URL rotated. Do both.

## What you need first

`cloudflared tunnel login` authorises a **zone** — a domain already on a
Cloudflare account. There is no way to get a stable hostname without one; the
free plan is fine, but the domain is not optional.

- **You have a domain on Cloudflare** → start at step 1.
- **You have a domain elsewhere** → add it to Cloudflare and move its
  nameservers first. Propagation is usually under an hour.
- **You have no domain** → register one. Any TLD works; this is the only cost,
  and it is a few dollars a year to remove a recurring outage.

A subdomain is enough — `gateway.yourdomain.com`. It never needs to be
guessable or memorable, and it is not a secret: every request to the gateway
is checked against `WEB_GATEWAY_SECRET` regardless.

## Setup

Run these **on the bot host**, as the user the bot runs as.

```bash
# 1. Authorise. Opens a browser; pick the zone you want to use.
cloudflared tunnel login          # writes ~/.cloudflared/cert.pem

# 2. Create the tunnel. Prints a UUID — keep it.
cloudflared tunnel create runeclaw-gateway
#    → writes ~/.cloudflared/<UUID>.json  (a bearer credential; never commit it)

# 3. Ingress config.
cp scripts/cloudflared/config.example.yml ~/.cloudflared/config.yml
#    then replace TUNNEL_UUID (twice) and HOSTNAME

# 4. Point DNS at the tunnel.
cloudflared tunnel route dns runeclaw-gateway gateway.yourdomain.com

# 5. Supervise it. Naming the tunnel does not keep it running.
sudo cp scripts/cloudflared/runeclaw-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now runeclaw-gateway
systemctl status runeclaw-gateway
```

Then point both sides at the stable hostname and restart each:

| host | variable | value |
|---|---|---|
| website | `BOT_GATEWAY_URL` | `https://gateway.yourdomain.com` |
| bot | `PUBLIC_GATEWAY_URL` | `https://gateway.yourdomain.com` |

`PUBLIC_GATEWAY_URL` is what the bot's proactive monitor probes; it alerts
after two consecutive failures, so a dead tunnel announces itself instead of
being discovered by a user.

## Verify it, don't assume it

```bash
# The tunnel is up and routing (401 is CORRECT here — no shared secret sent).
curl -s -o /dev/null -w '%{http_code}\n' https://gateway.yourdomain.com/gateway/health

# It survives a restart — the whole point of the exercise.
sudo systemctl restart runeclaw-gateway && sleep 5
curl -s -o /dev/null -w '%{http_code}\n' https://gateway.yourdomain.com/gateway/health

# And it survives a reboot.
systemctl is-enabled runeclaw-gateway     # → enabled
```

A `000`/timeout means the tunnel is not routing. A `502` means the tunnel is
up and the **bot** is not listening on `127.0.0.1:8080`. Those are different
problems on different sides — the website's own error will say
`GATEWAY_UNREACHABLE` for the first and pass the 502 through for the second.

## What stays out of git

- `~/.cloudflared/cert.pem` — account credential
- `~/.cloudflared/<UUID>.json` — tunnel credential
- the filled-in `config.yml` (it names the credentials path and the UUID)

Only the two templates in this directory are committed, and a test asserts
they still contain placeholders rather than a real UUID or hostname.
