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

**The unauthenticated check alone could not fail, and that is how a missing
route survived a whole tunnel build (2026-08-17).** `secret_middleware` runs
BEFORE routing, so a request with no secret returns 403 whether the gateway has
56 routes or none. A working tunnel and an empty one give the identical answer.
Send the secret, or you have verified nothing past the edge.

```bash
# 1. It rejects an anonymous caller. 403 is CORRECT here — this is the bot's
#    own middleware, NOT Cloudflare Access. Read the BODY, not just the code:
#      {"error":"forbidden"}       → reached the bot, no secret. Good.
#      {"error":"gateway_disabled"} → WEB_GATEWAY_SECRET unset. Broken for the
#                                     website too, and it looks like success.
#      HTML / *.cloudflareaccess.com → an Access policy is in front of the
#                                     tunnel; the shared secret never gets a
#                                     chance and server-to-server calls 403 too.
curl -s -i https://gateway.yourdomain.com/gateway/health | head -20

# 2. It SERVES something to an authenticated caller. This is the step that
#    distinguishes a routing gateway from an empty one. Run it on the bot host,
#    where the secret already lives; expect 200 {"ok":true,"service":"gateway"}.
curl -s -w ' <- %{http_code}\n' \
  -H "X-Gateway-Secret: $(grep -m1 '^WEB_GATEWAY_SECRET=' ~/.env | cut -d= -f2-)" \
  https://gateway.yourdomain.com/gateway/health

# 3. It survives a restart — the whole point of the exercise.
sudo systemctl restart runeclaw-gateway && sleep 5
curl -s -o /dev/null -w '%{http_code}\n' https://gateway.yourdomain.com/gateway/health

# 4. It comes back UNAIDED after a crash. A unit that needs a human is not
#    supervision; this is the check that tells the two apart.
pkill -f 'cloudflared.*tunnel run'; sleep 8
systemctl is-active runeclaw-gateway      # → active

# 5. And it survives a reboot.
systemctl is-enabled runeclaw-gateway     # → enabled
```

A `000`/timeout means the tunnel is not routing. A `502` means the tunnel is
up and the **bot** is not listening on `127.0.0.1:8080`. A `404` on step 2
means the tunnel and the secret are both fine and the gateway is not serving
the path — which is what `bot/core/proactive_monitor.py` probes every five
minutes, so it would page continuously for a healthy bot. Those are different
problems on different sides — the website's own error will say
`GATEWAY_UNREACHABLE` for the first and pass the 502 through for the second.

> Running these as a `--user` unit? Drop the `sudo`, use `systemctl --user`,
> and check `loginctl show-user "$USER" --property=Linger` reads `Linger=yes`.
> Without lingering the user manager exits with your last session: the tunnel
> dies at logout and never starts at boot, while `systemctl --user is-enabled`
> still says `enabled`. That is supervision that looks installed and is not.

## The second origin: the self-hosted model

The in-house RUNECLAW model is served by Ollama or vLLM on a machine with a
GPU — usually NOT the bot host, which has none. The bot reaches it over a
tunnel, and everything above applies to that tunnel too. It has already rotated
and taken the model offline silently at least twice.

**Silently is the operative word.** The LLM fallback chain catches every failed
call and answers the tier from another provider, so there is no error in the
log and no alert from the trading side. The symptom is the in-house model
quietly never being used, and slightly slower analysis. `bot/core/
proactive_monitor.py` probes the endpoint every five minutes for exactly this
reason and pages after two consecutive failures — but the probe only removes
the surprise. The tunnel is what removes the outage.

**The tunnel runs on the GPU MACHINE, not on the bot host.** `cloudflared`
connects outbound from wherever the origin lives, so an ingress rule on the bot
host would route the hostname to a port nothing is listening on. Run a second
named tunnel there, or add the origin to the same account and route a second
hostname to it.

```bash
# ── on the GPU machine, not the bot host ──
cloudflared tunnel login
cloudflared tunnel create runeclaw-llm
cloudflared tunnel route dns runeclaw-llm llm.yourdomain.com
```

```yaml
# ~/.cloudflared/config.yml on the GPU machine
tunnel: TUNNEL_UUID
credentials-file: /home/you/.cloudflared/TUNNEL_UUID.json
ingress:
  # Ollama's default port; vLLM is usually 8000. Bind it to localhost — the
  # tunnel is the only way in, and the API key is checked on every request.
  - hostname: llm.yourdomain.com
    service: http://127.0.0.1:11434
  - service: http_status:404
```

Supervise it the same way (`systemctl --user`, `Linger=yes`), then on the BOT
host:

| variable | value |
|---|---|
| `RUNECLAW_LLM_BASE_URL` | `https://llm.yourdomain.com/v1` |
| `RUNECLAW_LLM_API_KEY` | the key the model server expects |

`PROVIDER_CATALOG` reads that URL **at import**, so the bot must be RESTARTED —
editing `.env` under a running process does nothing. That is unlike
`BOT_SYNC_SECRET`, which is deliberately read per request.

### Verify it the way that can actually fail

**Send the key.** An unauthenticated probe returns 401 for a healthy endpoint
AND for one behind a Cloudflare Access policy the bot can never pass, so it
cannot fail and proves nothing — the same trap that hid a missing `/gateway/
health` route through a whole tunnel build.

```bash
K=$(grep -m1 '^RUNECLAW_LLM_API_KEY=' ~/.env | cut -d= -f2-)
echo "key length: ${#K}"        # 0 means the bot sends the literal "not-needed"

curl -s -i -H "Authorization: Bearer ${K:-not-needed}" \
  https://llm.yourdomain.com/v1/models | head -20
```

- **200 + a model list** — working. Check the list actually contains the id in
  `LLM_TIER_*_MODEL`: an endpoint that is healthy but serves a different model
  404s every call while looking perfectly fine.
- **401 with the key** — wrong key, or read the body: HTML or
  `*.cloudflareaccess.com` means an Access policy is in front and no API key
  will ever pass.
- **`key length: 0`** — the endpoint wants auth and the bot is sending a
  placeholder. `is_configured()` returns True for RUNECLAW regardless, because
  "keyless" was a property of a LOCAL server and nothing rechecks it once the
  endpoint is remote.

Then confirm the bot is actually *using* it, which the curl does not prove:

```
/llmstatus          # the resolved provider + model per tier
```

> An operator env var only binds if it names a tier that exists. The tiers are
> `scan`, `thesis`, `learning`, `chat` — so `LLM_TIER_CHAT_PROVIDER` works and
> `LLM_TIER_CHATS_PROVIDER` is silently ignored. Setting three of the four
> leaves the fourth on the default routing table.

## What stays out of git

- `~/.cloudflared/cert.pem` — account credential
- `~/.cloudflared/<UUID>.json` — tunnel credential
- the filled-in `config.yml` (it names the credentials path and the UUID)

Only the two templates in this directory are committed, and a test asserts
they still contain placeholders rather than a real UUID or hostname.
