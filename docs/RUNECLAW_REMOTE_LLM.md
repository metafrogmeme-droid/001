# Bot in the cloud, model on your GPU — wiring the two together

Topology this covers: the bot runs on a remote host (MuleRun or any VPS),
the RUNECLAW model serves from a local machine (the RTX 5090 laptop or the
8GB box). `localhost` does not cross that gap; a tunnel does — and because
**Ollama checks no credentials**, the tunnel must be fronted by a token
gate or the URL is a free GPU for whoever finds it.

The chain, laptop side:

```
cloudflared  ──►  ollama_auth_proxy.py :11435  ──►  ollama :11434
(outbound tunnel)  (checks Bearer token)            (never exposed directly)
```

Everything here is user-level — no admin: cloudflared.exe is a portable
binary, the proxy binds 127.0.0.1 only (no firewall prompt), Ollama is the
per-user install.

## 1. Laptop: start the gate

```cmd
python -c "import secrets; print(secrets.token_urlsafe(32))"   :: keep this token
set RUNECLAW_PROXY_TOKEN=<token>
python ollama_auth_proxy.py
```

Refuses to start without a token ≥16 chars. Listens on `127.0.0.1:11435`,
forwards to Ollama on 11434, streams SSE/chunked responses through.

## 2. Laptop: tunnel to the gate port — never to 11434

Quick test (URL rotates on every restart — fine for today, wrong for prod):

```cmd
cloudflared tunnel --url http://localhost:11435
```

It prints `https://<four-words>.trycloudflare.com`. For a stable hostname,
follow the **named-tunnel procedure in `scripts/cloudflared/README.md`** —
it exists for exactly this class of problem (the gateway outage of
2026-07-28 was a rotated quick-tunnel URL) and applies unchanged here:
same steps, ingress service `http://localhost:11435`, hostname e.g.
`llm.yourdomain.com`. Its two-failure-modes warning (URL rotation vs dead
process) applies doubly on a laptop.

## 3. Bot host: three env vars

```bash
RUNECLAW_LLM_BASE_URL=https://<tunnel-hostname>/v1
RUNECLAW_LLM_MODEL=pbdes2022/HUMANOID-TRADERS:v7-8b   # byte-for-byte from `ollama list`
RUNECLAW_LLM_API_KEY=<the same token>                  # vault-managed like every key
```

No bot code changes: `create_llm_client` (bot/llm/provider.py) already
sends `RUNECLAW_LLM_API_KEY` as `Authorization: Bearer …` on every
request, which is precisely what the gate checks. Restart the bot —
these are read at import.

Verify from the bot host before routing any tier:

```bash
curl -s https://<tunnel-hostname>/v1/models -H "Authorization: Bearer <token>"   # 200 + model list
curl -s https://<tunnel-hostname>/v1/models                                      # must be 401
```

The second check is not optional: a 200 there means the gate is being
bypassed (tunnel pointed at 11434 directly) and the endpoint is open.

## 4. Then the normal rollout

`/settier chat runeclaw` → `/settier scan runeclaw` → `/llmstatus` →
shadow A/B → `/llmab` → THESIS. Unchanged from docs/RUNECLAW_LLM.md.

## Honest constraints of a laptop as LLM host

- **Latency:** cloud→laptop adds ~50–150 ms per request — noise against
  multi-second generations. Not a concern.
- **Availability is the real cost.** A laptop sleeps, reboots, moves
  networks, and gets used for CAD at 13:29 on a Tuesday. Every one of
  those is an LLM outage the bot will surface as provider errors and
  (if configured) degradation alerts. `keep_awake.py` helps; it does not
  make a laptop a server.
- The durable arrangement is the one already planned: the **8GB box**
  (always-on) serves production behind the same proxy+tunnel, the laptop
  serves during eval/experiments. The chain above is identical on both —
  copy the proxy, same token or a new one, same cloudflared recipe.
- If the tunnel is down, the bot's tier routing falls back per its normal
  provider-failure path — check `/llmstatus`; a dead tunnel must never
  read as "model got worse."
