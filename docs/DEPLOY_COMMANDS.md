# Deploy

**The code on the box is never the source. Every deploy fetches from git first.**

## Production sequence (bot box + web container)

There are two bot-box processes and a separate web container. A deploy is not
complete until all three use the new source and the web runtime can reach both
bot processes. A box with `DUEL_BOT_TOKEN` set runs a third, optional process,
`python3 -m bot.duel_bot` (`runeclaw-duel.service`, health on :8081); restart
it with the other two. A box with `DAILY_DUEL_BOT_TOKEN` and
`DAILY_DUEL_ID_SALT` set runs a fourth, optional process,
`python3 -m bot.daily_duel_bot` (`runeclaw-daily-duel.service`, health on
:8082). It seals through `node` (`app/lib/daily_duel_gate.js`) and stores a
salted hash, never a Telegram id. Restart it with the others where installed.

The Duel's Telegram Mini App (`/tg`) is off until two settings are set
together:

- `DUEL_BOT_ID` in `~/runeclaw-persist/web.env`: the digits before the colon
  of `DUEL_BOT_TOKEN`, never the token. Publish `app/`.
- `DUEL_WEBAPP_URL` in the bot's `.env`: the site's `/tg` address over https.
  Restart `runeclaw-duel.service`.

The bot's `/start` and `/app` then carry an "Open the Duel app" button in
private chats. Setting `/tg` as the bot's Main Mini App in BotFather is
optional, and is done in BotFather, not here.

1. Fast-forward from the repository URL:
   `git pull --ff-only https://github.com/metafrogmeme-droid/001 main`.
   If the fast-forward fails, stop and ask. Do not reset.
2. Run `scripts/verify_deploy_source.sh` before starting anything. Exit 0 is
   the only pass. Exit 1, 2, or 3: do not start.
3. Run `./deploy.sh`. Besides restoring `.env`, `data/`, and `logs/`, it writes
   the persistent `~/runeclaw-persist/web.env` used for web publishes.
4. Restart `runeclaw-bot.service` and `runeclaw-bridge.service` (and
   `runeclaw-duel.service` where installed), each as its own command. Do not
   chain a probe onto a restart; verify afterward.
5. Publish `app/` using `~/runeclaw-persist/web.env`, not the bot `.env`.
   There is no install step unless a dependency manifest changed. When it
   did, on Python 3.11: `python3 -m pip install -r requirements.lock`.
6. Run `scripts/verify_deploy.sh` in a separate invocation.
7. Once Memeclaw has its own domain, run the Memeclaw checks in
   [Two websites, one web container](#two-websites-one-web-container), also
   as their own commands.

The generated web environment deliberately sets both web links to the same
public named-tunnel origin:

```text
BOT_GATEWAY_URL = PUBLIC_GATEWAY_URL
BOT_API_URL     = PUBLIC_GATEWAY_URL
```

Never copy the bot box's `BOT_GATEWAY_URL` into the web container. On the box
it correctly points to loopback `:8080`; in the web container that points back
at the web container. Tunnel ingress sends `/gateway/*` to the gateway on
`:8080`, and `/insight`, `/patterns`, `/lab`, and `/health` to the separate API
bridge on `:8000`.

The final verifier checks both content hashes and `/api/public/status`. A clean
result requires `bot_gateway=reachable`, `api_bridge=reachable` and
`arena_prices=fresh`, as measured from inside the web runtime. The last one
means the web container can read the live marks the Arena prices closes with;
on 2026-10-17 a deploy verified clean while it could not. Local port checks
alone are insufficient.

That is not a style preference. On 2026-08-20 a deploy ran `git fetch origin &&
git reset --hard origin/main`, reported success, and landed on a commit **255
commits stale** — `origin` on that box is a GitLab mirror and the real
repository is a remote named `backup`. Every other check passed, because each
was true of the stale tree: the pull "worked", the symlinks resolved, the user
store loaded, 18 users were present. The only thing wrong was *which code*, and
nothing asked.

So: fast-forward from the **URL** (`git pull --ff-only <url> main`), then
`scripts/verify_deploy_source.sh`. If the fast-forward fails, stop and ask.
Do not reset, and do not move the checkout from a remote-tracking ref.

---

## Two websites, one web container

Since 21 October 2026 there are **two websites**, and the **same web
container** serves both. There is no second app to publish and no new process
on the bot box.

| website | address | what it shows |
|---|---|---|
| RUNECLAW | `https://humanoid-traders.com` (and `www.`) | the whole site, as before; its `/memeclaw` page holds the operator's Memeclaw tools |
| Memeclaw | its own domain, named in `MEMECLAW_HOSTS` | the launchpad only: `/` is the launchpad; its API, scripts and stylesheet, a launch receipt, `/privacy`, `/terms`, health. Everything else is a 404. |

`app/lib/memeclaw_host.js` decides which site a request gets, from the host it
arrived on. On a Memeclaw host it refuses every RUNECLAW page (the presale
bar, `/token`, the dashboard, sign-in) and every write outside
`/api/memeclaw`. With `MEMECLAW_HOSTS` unset, nothing changes.

The bot box serves neither website and runs nothing for Memeclaw. No bot code
reads Memeclaw, and `tests/test_the_agent_never_reaches_memeclaw.py` holds
that.

### Setting it up, once, in this order

1. **The domain** is bought by the owner, after the trademark check
   (`docs/memeclaw/MEMECLAW_PLAN.md`).
2. **Point it at the web container**: add it, and its `www.` if used, as a
   custom domain on the same host that serves `humanoid-traders.com`, with
   TLS. Point it at neither the bot box nor the Cloudflare tunnel: the tunnel
   carries only the bot's gateway and bridge.
3. **Put the settings in the web environment**,
   `~/runeclaw-persist/web.env`, and not in the bot's `.env`:

   ```text
   MEMECLAW_HOSTS=<domain>,www.<domain>
   MEMECLAW_PUBLIC_URL=https://<domain>
   ```

   `deploy.sh` rebuilds `web.env` from the bot's `.env` on every deploy. It
   copies only the five `SHARED_KEYS` and derives the two gateway URLs
   (`scripts/prepare_web_env.py`). Every other key already in `web.env` is
   kept, so a Memeclaw setting placed
   there survives each deploy. One placed in the bot's `.env` never reaches
   the website.
4. **Publish `app/` with `web.env`**, exactly as in every deploy.
5. **Run the checks below.** Do not announce the domain until they pass.

**Later, when the operator has deployed the factory** from their own wallet on
`https://www.humanoid-traders.com/memeclaw`: add the two values its check
prints to `web.env` as well, then publish again.

```text
MEMECLAW_FACTORY=0x...
MEMECLAW_FACTORY_BLOCK=<block>
```

**No secret is involved, at any step.** Memeclaw has no key, no wallet seed
and no API token; the server never signs or sends a transaction. A step that
asks for one is the wrong step.

### Checking both websites after a publish

```bash
WEB_URL=https://humanoid-traders.com
MEMECLAW_URL=https://<domain>

curl -s "$WEB_URL/api/version"; echo; curl -s "$MEMECLAW_URL/api/version"; echo
curl -s "$MEMECLAW_URL/" | grep -c 'id="mc-root"'
curl -s -o /dev/null -w '%{http_code}\n' "$MEMECLAW_URL/token"
curl -s -o /dev/null -w '%{http_code}\n' "$MEMECLAW_URL/dashboard"
curl -s "$MEMECLAW_URL/api/memeclaw/config"; echo
curl -s "$WEB_URL/" | grep -c 'id="mc-root"'
```

| check | passes when | if not |
|---|---|---|
| both `/api/version` | the same `build` and the same `assets` | the domain points at a different deployment from the one serving `humanoid-traders.com`: fix the domain on the host |
| Memeclaw `/` | prints `1` | `0` with RUNECLAW's home page: `MEMECLAW_HOSTS` does not name this host (spelling, `www.`), or `web.env` was not the file published |
| Memeclaw `/token`, `/dashboard` | both `404` | `200` means the **whole RUNECLAW site, presale included, is on Memeclaw's domain**. Same causes as the row above; fix it before anyone is sent there |
| Memeclaw `/api/memeclaw/config` | JSON | `"enabled":false,"reason":"not_deployed"` is right until the factory is set; practice mode works without it. `"factory_unknown"` means the DogeOS testnet could not be read just now, not that anything is broken |
| RUNECLAW `/` | prints `0` | the launchpad has replaced RUNECLAW's home: `MEMECLAW_HOSTS` names the main domain. Remove it |

A connection or TLS error is **not a verdict on the code**: the domain or its
certificate has not reached the web container yet. Nothing was checked.

**Do not point `scripts/verify_deploy.sh` at the Memeclaw domain.** It asks
`/api/public/status`, which Memeclaw's domain refuses by design, so it can
only report "could not check" there. Run it as always, against
`humanoid-traders.com`; the table above covers the second website.

**Where the operator works.** The deploy, starter-coin and hide tools appear
only on `https://www.humanoid-traders.com/memeclaw`, signed in as the
operator. Sign-in is not served on Memeclaw's domain, so the tools never
appear there.

---

## The sequence

Run from the launcher, which lives **outside** the repo. An update of this
checkout overwrites anything tracked inside it. On 2026-08-01 a reset of the
checkout restored a flagless launcher on ~15 consecutive deploys and each one
printed success with nothing running.

### 1. Get the latest from git — by URL, fast-forward only

```bash
git pull --ff-only https://github.com/metafrogmeme-droid/001 main
```

If that command fails, **stop and ask**. Do not recover it with `git reset`.

A remote *name* is a per-machine nickname that can point anywhere, so "use the
right remote" is advice, and advice is what failed. The URL is the argument,
so the nickname is not what moves the checkout. `--ff-only` refuses when this
branch has local commits, has diverged from that URL's `main`, or is not
`main`. A refusal is the stop. The checkout must already be on `main`.

It also sidesteps the trap that `git fetch origin main` updates `FETCH_HEAD`
while leaving `refs/remotes/origin/main` untouched — a later move onto that
ref lands on stale code.

### 2. Verify the checkout is what the remote says it is

```bash
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
```

Reads the URL with `git ls-remote` and consults nothing local. Four outcomes,
because *could not check* is not *passed*:

| exit | meaning |
|---|---|
| 0 | `HEAD` is exactly the branch tip at that URL |
| 1 | differs — behind, ahead, or diverged. **Do not deploy.** |
| 2 | usage error — **nothing was checked** |
| 3 | could not determine (no network, not a git repo, `ls-remote` refused) — **nothing was checked** |

A gate that reads an unreachable network as "up to date" ships stale code on
the one day the network is down.

### 3. Restore persistent state

```bash
./deploy.sh                      # or: PERSIST_DIR=/srv/rc ./deploy.sh
```

Symlinks the real `.env` and `data/` back in from a directory the redeploy never
touches. Idempotent and non-destructive. It exists because a 2026-07-14 redeploy
re-cloned the repo directory and wiped both — the keys were hand-re-entered with
quotes, Bitget returned 40006, and a live AMD position sat unprotected.

### 4. Dependencies — on Python 3.11

```bash
python3 -V                       # must be 3.11.x
pip install -r requirements.lock
```

On an older interpreter, `numpy 2.3.x` declares `requires-python >=3.11` and pip
cannot *see* those releases. It then reports the newest version visible to that
Python and the message reads as a fact about PyPI rather than about the local
interpreter. Following it downgrades a correct pin for everybody.

### 5. Start, and gate `DEPLOY_DONE` on the process still being alive

```bash
# Copy once, outside the repo. Do not re-copy on later deploys.
#   cp scripts/launch_all.sh.template "$HOME/launch_all.sh"
#   chmod +x "$HOME/launch_all.sh"
test -x "$HOME/launch_all.sh" || { echo "STOP — copy scripts/launch_all.sh.template to ~/launch_all.sh and ask before starting"; exit 1; }
"$HOME/launch_all.sh"
```

`scripts/launch_all.sh.template` starts **both** processes: `bot.main --mode
telegram` (gateway on :8080) and `api_bridge.py` (uvicorn on :8000). It gates
`DEPLOY_DONE` on each still being alive (`scripts/verify_bot_alive.sh --pid`),
and it starts the optional duel bots only where their tokens are set. A line
that starts only `bot.main` and then prints success is not a deploy.

`python -m bot.main` defaults to `--mode telegram`. It used to default to `cli`,
which finds no TTY and **exits zero** — so a launcher that forgot the flag
printed `DEPLOY_DONE` and left nothing running. The template passes the flag.

Use `--pid`, not `pgrep -f`: the launcher knows what it started, and a pattern
match also matches the checking script's own command line — the first draft of
that script reported OK for a process that had never existed. It also treats a
**zombie as dead**: `kill -0` succeeds on a defunct process, and since the deploy
script is the unreaped parent, the naive check passes on exactly the failure it
exists to catch.

### 6. Confirm the web deploy actually landed

```bash
node -e "const v=require('./app/lib/version').buildInfo(); console.log(v.build, v.assets)"
```

| `build` | `assets` | means |
|---|---|---|
| moved | moved | full deploy landed |
| moved | unchanged | server-only change |
| unchanged | moved | client-only change |
| unchanged | unchanged | **nothing deployed**, whatever the log says |

A moved `assets` hash is still not a *fetched* file — browsers cache on the `?v=`
in the script tag. Any change to a bundle must bump that query in every page
referencing it; `app/test/cache_buster_ratchet.test.js` fails the build otherwise.

### 7. Confirm the *marketing site* deploy actually landed

Step 6 covers the platform. It says nothing about the site, which is a separate
artefact on a possibly separate host — and "we deployed but still don't see any
changes to website" was a real morning, with no way to tell a stale host from a
stale browser cache from a build that never contained the change.

The site now publishes its own fingerprint. **Ask the live URL, not the box** —
that is the whole point, since the box being right is exactly what is in doubt:

```bash
curl -s https://<site>/version.json                  # what is actually served
node site/scripts/site_fingerprint.mjs               # what this checkout builds
```

| `pages` | `assets` | means |
|---|---|---|
| moved | moved | full publish landed |
| moved | unchanged | content/markup change only |
| unchanged | moved | bundle change only |
| unchanged | unchanged | **nothing published**, whatever the log says |

If the two sides disagree, the served site is not this commit — republish. If
they agree and you still cannot see the change, the change is not in this
commit, and no amount of redeploying will produce it.

**Media is deliberately outside both hashes.** `pages` covers every served
`.html`, `assets` covers `website/assets/`. A publish that changed only an image
or the demo video moves neither, and this check must not be read as saying it
did.

For the host `api_bridge.py` serves, there is nothing separate to publish —
`website/` is served straight out of the checkout, so step 2 already answers it.
The fingerprint is for every other copy.

---

## One-liner

```bash
set -euo pipefail
git pull --ff-only https://github.com/metafrogmeme-droid/001 main \
  || { echo "FAST-FORWARD FAILED — stop and ask"; exit 1; }
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
./deploy.sh
python3 -m pip install -r requirements.lock
test -x "$HOME/launch_all.sh" || { echo "STOP — copy scripts/launch_all.sh.template to ~/launch_all.sh and ask before starting"; exit 1; }
"$HOME/launch_all.sh"
```

Keep this file's copy in the launcher outside the repo. The version in here is
the reference; the version that runs must not be a file this update overwrites.

## Pi Testnet on pi.humanoid-traders.com

MuleRun's custom-hostname quota is full. Only `www.humanoid-traders.com` is
registered there. `pi.humanoid-traders.com` is a Worker in the owner's own
Cloudflare account (the `humanoid-traders.com` zone). It proxies the Testnet
app to `https://www.humanoid-traders.com` and sends `X-Runeclaw-Pi-Proxy`.
The website treats that request as the Testnet app when the header matches
`PI_TESTNET_PROXY_SECRET`. www stays the Mainnet app. The secret is never
committed and never pasted into chat.

1. On your own machine, generate a secret of at least 48 characters. A value
   shorter than 32 characters is ignored, and the Worker then refuses to
   proxy rather than serve the Mainnet validation key on the Testnet hostname.
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. In the Cloudflare dashboard for `humanoid-traders.com`, delete the `pi`
   CNAME. It points at `pmvc58g2.mule.page` and that is what returns Error
   1014 (CNAME Cross-User Banned).
3. Create a Worker from `scripts/cloudflare/pi-testnet-worker.js`.
   `scripts/cloudflare/wrangler.toml` is the example config. Do not put the
   secret in that file. From that directory:
   ```bash
   wrangler deploy
   ```
4. Set the Worker secret (this prompts; it does not take the value on the
   command line) and attach the hostname. The example config names the
   Custom Domain; confirm it in the dashboard as `pi.humanoid-traders.com`.
   ```bash
   wrangler secret put PI_PROXY_SECRET
   ```
   In the dashboard, add the Worker as a Custom Domain:
   `pi.humanoid-traders.com`.
5. On the MuleRun host, set `PI_TESTNET_PROXY_SECRET` in
   `~/runeclaw-persist/web.env` to the same value, along with
   `PI_TESTNET_HOST=pi.humanoid-traders.com`, `PI_TESTNET_VALIDATION_KEY` and
   `PI_TESTNET_API_KEY`. Republish `app/`. The Worker only forwards the
   Testnet app (`/pi`, its validation file, the Pi API routes, and the
   static files that page loads). Other paths redirect to www.

## What the test baseline counts

`scripts/ci_test_gate.py` prints `known-baseline: N` for the node ids in
`tests/known_failures.txt`. A `#` line is not an entry. That file's baseline
is empty: the last green run of this gate on `main` printed
`known-baseline: 0`. The 53 in `tests/unreachable_methods_baseline.txt` is a
different ratchet, the public method names the receiver sweep leaves
unchecked. It follows the same two-way rule as `tests/known_failures.txt`
and it is not that file. The measured note, including which `53` in the CI
log is not a baseline, is in `docs/gitbook/demo-scenarios.md` under "Running
the Tests".
