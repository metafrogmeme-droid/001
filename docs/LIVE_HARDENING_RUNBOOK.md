# ⚔️ RUNECLAW — Staged Live-Hardening Runbook

You're live. Almost every protective/learning feature shipped recently is
**default-OFF**, so the bot is currently running *without* them. This is the
order to turn them on, what each does, and what to watch after each step.

## Golden rules
- **One flag at a time.** Flip it, restart, watch for a defined window, then move on.
- **All of these are `.env` flags read at launch** — edit `.env` and **restart** the bot.
- **Rollback = set the flag back to `false` (or remove it) and restart.** Every
  feature here is strictly additive and gated, so reverting always returns to the
  prior behaviour. No data is lost.
- **Watch via Telegram:** `/health` (vitals), `/status` (engine + rejections),
  `/livepositions` (open live trades + SL/TP), `/slippage` (execution quality),
  `/positions`, `/calibration` (learner readiness), `/whynot SYMBOL` (why a trade
  was skipped).
- **Emergency controls (admin):** the kill-switch covers **every** account
  (operator + all per-user), not just the operator:
  - **Emergency Stop** (button / confirm) — trips the circuit breaker on **all**
    risk engines, clears queued ideas, and flattens open positions on **every**
    account. Say "resume" / `/reset` to clear all breakers and restart.
  - `/halt` — pauses **new** trades on all accounts (does not flatten).
  - `/closeall` — flattens open positions on all accounts (does not halt).

---

## Stage 0 — Confirm the baseline is live (no flags to flip)
These are already ON and should be verified before hardening:
- **Unprotected-position alert** — CRITICAL ping if any live position ever lacks an
  exchange stop past the grace window.
- **Slippage guard** (`SLIPPAGE_GUARD_ENABLED=true`) — flattens an over-slipped fill.
- **Unprotected escalation** (`UNPROTECTED_ESCALATION_ENABLED=true`) + SL/TP self-heal.
- **Proactive alerts** — drawdown tiers, WS health, stale balance, tick failures,
  macro lockdown, slippage drift.

**Watch:** run `/health` and `/livepositions` — confirm every open position shows a
SL **and** TP "on exchange". If anything is unprotected, fix that *before* hardening.

---

## Stage 1 — Risk tightening (turn on NOW — deterministic, no data needed)
These reduce risk immediately and don't depend on any learned data.

```bash
LIVE_RISK_HARDENING_ENABLED=true     # forces correlation-sizing + covariance-VaR ON
LIVE_MAX_DRAWDOWN_PCT=7              # tighter live DD cap than the 10% paper default
REGIME_HARD_GATES_ENABLED=true       # no-trade in CHOP/UNKNOWN; block counter-trend in strong trends
TIME_STOP_LIVE_AUTO_CLOSE=true       # auto-close dead/invalidated theses instead of riding to SL
```

**Effect:** fewer but cleaner trades; correlated stacking is sized down; the lowest-edge
regimes stop trading; failed setups exit early.
**Watch (a few days):** `/status` rejection reasons (you'll see `CORRELATION`, regime,
and time-exit actions), drawdown alerts, and that win rate / R isn't hurt by the regime
gates. If the regime gates feel too tight, raise `REGIME_STRONG_ADX` (e.g. 35) or revert.

---

## Stage 2 — Start the learning flywheel (turn on, then WAIT for data)
The learners need closed-trade history before they mean anything.

```bash
LEARNING_AUTO_REFIT_ENABLED=true            # refit calibration + voter-weights + expectancy from closed trades
UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED=true    # cap the unproven LLM's blend weight UNTIL calibration lands
LIVE_PERFORMANCE_GOVERNOR_ENABLED=true      # de-risk automatically when REALIZED results degrade (needs ~10+ closes)
```

**Effect:** the learners accrue their curves in **shadow** (logged, not yet applied);
the LLM can't dominate sizing while its confidence is still unproven (this cap
auto-lifts the moment calibration is enabled in Stage 3). The **performance
governor** is a separate, deterministic backstop: it watches the realized win
rate + net PnL of your most recent closed trades and, once ≥`LIVE_PERF_MIN_SAMPLES`
(default 10) have accrued, **shrinks size** when the recent window underperforms
and **pauses trading** if it's both losing often *and* net-negative. It can only
tighten — no effect while results are healthy or before enough trades exist.
Tune `LIVE_PERF_REDUCE_WINRATE` / `LIVE_PERF_PAUSE_WINRATE` if the defaults feel
too eager; `/whynot SYMBOL` shows `LIVE_PERF_GOVERNOR` when it acts.
**Watch:** let this run until you have a meaningful sample of **closed** trades
(≈50–100+). Check `/calibration` — it tells you when the calibrator `is_ready`.
**Do not proceed to Stage 3 until the calibrator reports ready.**

---

## Stage 3 — Apply the learned overlays (only after Stage 2 has data)
Enable these **one at a time**, watching between each. Order matters.

```bash
CONFIDENCE_CALIBRATION_ENABLED=true   # 1) confidence now reflects realized win rate
AUTO_CONFIRM_USE_CALIBRATED=true      # 2) the 0.85 admin auto-trade fires on the MEASURED win rate
VOTER_WEIGHT_LEARNING_ENABLED=true    # 3) reweight confluence voters by realized edge
SETUP_EXPECTANCY_ENABLED=true         # 4) nudge confidence by per-(symbol,regime,direction) history
```

**The big one is #2** — with it, an over-optimistic 0.90 idea whose *calibrated* value is
0.78 is held for manual confirm instead of being auto-placed with real money. It can only
*tighten* auto-trade.
**Watch:** auto-trade frequency should **drop** if the LLM was overconfident (this is the
point); win rate of *executed* trades should rise. Confirm `/calibration` and the
confidence distribution look sane. Re-refit periodically (auto-refit handles it).

---

## Stage 4 — Optional enhancers (lowest priority, enable last)
```bash
FUNDING_COST_AWARE_ENABLED=true   # haircut confidence when a swing would PAY adverse funding
EXTERNAL_SENTIMENT_ENABLED=true   # adds a Fear&Greed contrarian voter (makes a network call)
# Once calibration shows the rule engine ≈ the LLM, lean on the deterministic side:
# LLM_BLEND_WEIGHT=0.4
# CONFLUENCE_BLEND_WEIGHT=0.6
```
Each is bounded and reversible. Enable only after Stages 1–3 are stable.

---

## Position sizing (independent of the stages)
The live caps are MARGIN figures, hard-enforced in the executor. Start small and scale:
```bash
MICRO_MAX_POSITION_USD=100      # raise gradually as you trust the live behaviour
MICRO_MAX_TOTAL_EXPOSURE=500
MICRO_MAX_OPEN_POSITIONS=5
```
Raise these **after** Stage 1, not before — the risk tightening should be on first.

---

## Quick reference — recommended end state
| Stage | Flags | Status |
|---|---|---|
| 1 | `LIVE_RISK_HARDENING_ENABLED`, `REGIME_HARD_GATES_ENABLED`, `TIME_STOP_LIVE_AUTO_CLOSE` | **default ON** (2026-07) |
| 2 | `LEARNING_AUTO_REFIT_ENABLED`, `UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED`, `LIVE_PERFORMANCE_GOVERNOR_ENABLED`, `KELLY_SIZING_ENABLED`, `CORRELATION_SIZING_ENABLED` | **default ON** (2026-07) |
| 3 | `CONFIDENCE_CALIBRATION_ENABLED` (ON) → `AUTO_CONFIRM_USE_CALIBRATED` → `VOTER_WEIGHT_LEARNING_ENABLED` (run `validate_oos`, enable when hold_rate is good) → `SETUP_EXPECTANCY_ENABLED` (ON) | after ~50–100 closes |
| 4 | `FUNDING_COST_AWARE_ENABLED`, `EXTERNAL_SENTIMENT_ENABLED`, blend-weight tuning | operator choice |

If anything misbehaves: set the offending flag back to `false`, restart, and report
what you saw — every step is independently reversible.

---

## Deposits and withdrawals move the drawdown baseline

The live drawdown breaker measures against a **high-water mark of live
equity**. It has no way to tell a withdrawal from a loss — both simply reduce
equity — so moving funds OUT of a live account reads exactly like losing them.

Worked example: equity $495 → $450 after a $45 withdrawal is a 9.1% drop
against the peak. With `LIVE_MAX_DRAWDOWN_PCT=7` that trips the breaker and
halts new entries, even though nothing was lost.

**After any deposit or withdrawal on a live account, run `/reset`.** It
re-seeds the high-water mark from the next live evaluation, so the gate
measures against the new balance instead of the old one.

`/status` shows the drawdown the breaker **actually enforces** — the live
figure in live mode, the paper figure otherwise. (It previously showed the
paper number in both, which never moves in pure-live operation: an operator
could read 0.0% from a gate refusing trades at 9%.)

---

## Deployment-host hygiene (ops)

Two host-level practices that are outside this repository's control but bite
live operators:

1. **Pin the Python runtime.** The repo now ships `.python-version` (3.11) and
   `pyproject.toml` declares `requires-python = ">=3.11"`. Use `pyenv`/`uv` (or
   your image's base tag) so the host resolves the same interpreter — silent
   3.10 fallbacks break `datetime.UTC` and modern typing at import time.

2. **Keep any watchdog/restart script OUTSIDE the repo working tree.** If your
   deploy loop uses `git reset --hard`/`git clean` to update the checkout, any
   watchdog script stored inside the tree loses local edits and (on some
   setups) its execute bit. Install it to `/usr/local/bin` (or a systemd unit)
   instead, or re-assert `chmod +x` as a post-reset step in the deploy script.
   Nothing inside this repository performs `git reset` — this is purely about
   the host-side update loop some operators run.

---

## Website triage — is it the app, or the host?

**Step zero, before reading any reason code: did the app answer at all?**
Everything below assumes the probes RESPOND. On 2026-07-30 they did not, and
the tell was the byte count, not the status:

```
/healthz  → 200 in ~1.3s   x-envoy-upstream-service-time: 0
/readyz   → timeout, ZERO bytes
/         → timeout, ZERO bytes
static js → timeout, ZERO bytes
```

`/readyz` touches nothing but memory — if Node were executing requests it
would answer instantly, 200 or 503. **Zero bytes on `/readyz` means the
container is not running your code** (cold-start stuck, or crash-looping
while the edge holds connections). And the `/healthz` 200 was the PLATFORM's
envoy layer answering, not the app — `x-envoy-upstream-service-time: 0` is
the giveaway, so a green healthz alone must never be read as "the app is up".

The fix is at the host, not in the repo: restart the web container from the
console and look for the one boot line that settles it —
`RUNECLAW app running on port 8080`. Present → it booted; absent → the lines
just above the end of the log name where startup died. It is NOT the
database (the app listens before migrating and serves static pages through a
DB outage by design), so do not start at the allowlist.

Once the probes respond at all, the reason codes below take over:

| Probe | Means | Touches |
|---|---|---|
| `GET /healthz` | the process is alive and its event loop is turning | nothing at all |
| `GET /readyz` | it can serve database-backed traffic | the migration result |

`/readyz` returns `200 {"ready":true,…}` or `503` with a **coarse** reason code.
It never contains a driver message, hostname, port or credential, because it is
a public endpoint. The full error goes to the server log instead.

| Reason | What it means | Where to look |
|---|---|---|
| `starting` | no attempt has finished yet | normal for a few seconds after boot |
| `db_unreachable` | refused / timed out / no DNS / no route | network, firewall, host allowlist |
| `db_auth` | the server answered and rejected the credentials | username/password in `DATABASE_URL` |
| `db_tls` | the server answered and refused the transport | see below — the usual TiDB cause |
| `db_config` | the server answered; the URL names something absent | database name, host allowlist |
| `db_timeout` | the attempt was still running when its cap expired | the driver never came back at all |
| `db_url` | the failure carried **no driver code** — it never reached the network | the connection string, almost always the `ssl` parameter |
| `db_error` | reached, failed, cause not yet classified | grep the log (below) and send the code |

⚠️ **`db_timeout` during a FIRST migration is usually the cap, not the network.**
The first run creates 33 tables, each a real distributed DDL operation, on a
cluster that may also be cold-starting. The cap
(`MIGRATE_ATTEMPT_TIMEOUT_MS`, default 180s) has to clear all of it. If
`/diagz` shows `RC_MIGRATE_TIMEOUT`, read the `stmt=` — the statement's
POSITION in the migration is the tell:

- an EARLY table (`users`, `trades`, `equity_snapshots` — 1st to 4th) means the
  attempt is being cut off almost immediately: raise the cap;
- a LATE table means most of the migration succeeded and only the tail was cut:
  raise the cap by less;
- the SAME table every time, with a real driver code rather than
  `RC_MIGRATE_TIMEOUT`, means that statement is genuinely rejected — fix the DDL.

A network or allowlist fault cannot produce partial progress. If the migration
ever reached a late statement, the source address is not blocked — do not go
opening an allowlist on that evidence.

⚠️ **`starting` with `attempts: 0` long after boot is not "still starting".** It
means no attempt has ever *finished* — the driver is hanging. Each attempt is
capped (`MIGRATE_ATTEMPT_TIMEOUT_MS`, default 45s), so this should resolve into
`db_timeout` and keep retrying; if it does not, the cap has been disabled.

**TiDB requires TLS, and the `ssl` parameter must be valid JSON.** Append it to
`DATABASE_URL` **URL-encoded**, so nothing can mangle the braces or quotes on
the way through a shell or an env-var injection:

```
mysql://USER:PASS@HOST:4000/DB?ssl=%7B%22minVersion%22%3A%22TLSv1.2%22%2C%22rejectUnauthorized%22%3Atrue%7D
```

⚠️ **`ssl=true` does not work**, and neither does unquoted JSON. Both throw
before the driver reaches the network, so they report `db_url` — verified
against mysql2:

| `ssl` form | result |
|---|---|
| valid JSON (raw or URL-encoded) | reaches the socket; fails there with a real code |
| `{mangled}` — unquoted keys | no driver code → `db_url` |
| `ssl=true` | no driver code → `db_url` |

**This is how to tell a connection-string fault from an allowlist one.** A
blocked source address fails at the *network* layer, so it carries a code and
reports `db_unreachable` (or `db_timeout` if the peer simply never answers).
`db_url` means the driver never got that far — do not go hunting an IP
allowlist for it.

**Which build is actually serving.** `/api/version` reports the commit when it
can. On a bundled deploy it usually cannot — no `BUILD_SHA`, no
`build-info.json`, no `git` binary, no `.git` — and honestly says
`"sha":"unknown"`. The `build` field answers the question anyway:

```bash
curl -s https://YOUR_HOST/api/version
# {"sha":"unknown","build":"9fe79eaa6713+186","started_at":"…","uptime_s":1303}
```

There are **two** fingerprints, and the pair is the diagnosis:

| field | hashes | moves when |
|---|---|---|
| `build` | `server.js`, `auth.js`, `db.js`, `lib/*.js`, `routes/*.js` | server code changes |
| `assets` | `public/js/*.js`, `public/*.html`, `styles.css` | browser code changes |

```bash
curl -s https://YOUR_HOST/api/version
# {"sha":"…","build":"fa5e44a8231b+186","assets":"7be07ceb9c8c+61",…}
```

| `build` | `assets` | means |
|---|---|---|
| moved | moved | full deploy landed |
| moved | unchanged | server-only change — expected for a route/lib fix |
| unchanged | moved | client-only change — expected for a dashboard fix |
| unchanged | unchanged | **nothing deployed**, whatever the deploy log says |

Both are hashes of the source that shipped, not commit ids — they change when
the code changes and do not when it has not. Compute the expected values from a
checkout **before** deploying and compare:

```bash
node -e "const v=require('./app/lib/version').buildInfo(); console.log(v.build, v.assets)"
```

(Ask the host to pass `BUILD_SHA` as a build arg and `sha` becomes a real
commit id, which is nicer still.)

⚠️ **Why `assets` exists.** `build` alone was the deploy check for a while, and
it hashes server code only — so a fix living entirely in `public/js` deployed
with `build` completely unchanged, and the endpoint could not tell you whether
it had landed. Verifying it meant view-source on a `<script>` tag. An
instrument that needs a caveat about what it cannot see is one somebody will
eventually read without the caveat.

⚠️ **A moved `assets` does not mean browsers have it.** They cache by the
`?v=` on the script tag. If a change ships without bumping that, `assets`
moves and every open tab still runs the old bundle. Check both:

```bash
curl -s https://YOUR_HOST/dashboard | grep -o 'js/[a-z0-9-]*\.js?v=[0-9]*'
```

**Which build the BOT is running.** Everything above is the *web* half. The
Python half had no equivalent until 2026-08-20, and that day a deploy ran

```bash
git fetch origin && git reset --hard origin/main    # ← origin is a MIRROR
```

reported "Deploy-pull completed successfully", and landed on a commit **255
commits stale**. Every check the operator ran passed, because each was true of
the stale tree: the pull worked, the symlinks were right, the user store
loaded, eighteen users were present. The bot's only self-report was
`⚔️ RUNECLAW v0.1.0` — a hand-maintained constant that reads identically
before and after every deploy this repo will ever do.

Three surfaces now name the running commit, all from one renderer so they
cannot disagree:

```bash
grep Build: bot.log                 # the startup banner, above Mode/venue/balance
curl -s localhost:8080/health       # {"status":"ok","build":"0449bc7 (git)",…}
# /version in Telegram
```

Read the label, not just the sha:

| label | means |
|---|---|
| `0449bc7 (git)` | resolved from the tree, and tracked files match the commit |
| `0449bc7-dirty (git)` | **the box has been hand-patched** — the sha is not the code |
| `0449bc7 (build stamp, tree unchecked)` | from `BUILD_SHA`/`build-info.json`; the files were never compared to it |
| `unknown` | nothing resolved. Not a pass, and never rendered as blank |

`-dirty` counts tracked modifications only — `data/`, `logs/`, `.env` and a
stray `nohup.out` are not code, and a gate that cries wolf on every deploy is
one somebody disables.

**Check it BEFORE you start, too.** `scripts/verify_deploy_source.sh` asks the
canonical URL directly with `git ls-remote`, so a mis-pointed remote *name*
cannot fool it, and it exits 3 — never 0 — when it could not check at all.

```bash
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
```

**Where the web app's diagnostics actually are.** The Node app logs to stdout,
and `logs/*.log` belongs to the **Python bot** — docker-compose mounts `./logs`
into the bot and api_bridge containers, never the web one. So grepping
`logs/*.log` for a web-app message matches nothing even when the message was
printed. That is how a boot fatal read as a hang for hours.

The events that explain a non-serving app are therefore also appended to
`logs/web.log` (override with `WEB_LOG_DIR`):

```bash
grep -E 'listening|migration_' logs/web.log
# 2026-07-28T11:20:03.114Z listening port=8080
# 2026-07-28T11:20:48.902Z migration_failed reason=db_tls code=ER_SECURE_TRANSPORT_REQUIRED attempt=3 retry_in_ms=5000
# 2026-07-28T11:21:10.551Z migration_ok after_attempts=3
```

`listening` answers the question every incident today started with — *is this
build serving at all, or did the container never get that far?* Categories,
codes and the port only; never an error message, never a connection string,
never a secret.

The file writes are asynchronous and fail-open: a missing or unwritable
directory is silently tolerated, because a logger that can break boot is worse
than no logger.

⚠️ **On a managed page host that file is unreachable.** The web app runs in
its own container with no shell and no reachable disk, so `logs/web.log` lands
somewhere nobody can grep — which is how `/readyz` reported `db_error` for
hours with the driver code behind it out of reach. HTTP is the only channel
into that container, so set `DIAG_TOKEN` in the web environment and ask it
directly:

```bash
curl -s -H "X-Diag-Token: $DIAG_TOKEN" https://YOUR_HOST/diagz | jq
```

It returns the readiness snapshot, the build stamp, and the last 50 events with
their driver codes. The guard is a shared secret **from the environment**, not
the normal login: signing in needs the database, and the database being
unreachable is the exact circumstance this exists for. With `DIAG_TOKEN` unset
the route 404s as though unmounted, and a wrong token 404s identically — a
distinct code would confirm the route exists.

**When the reason is `db_error`,** the driver's own code appears beside the
category in both stdout and that file — one word, no stack-reading. Send it and
it can be classified properly, so the next occurrence names itself instead of
landing in the catch-all.

Read the combination:

- **`/healthz` 200, `/readyz` 200** — the app is fine. If pages still look
  wrong, it is the browser cache or the edge, not this process.
- **`/healthz` 200, `/readyz` 503** — the app is up and the database is not.
  Static pages, docs and learn pages keep serving; `/api/*` refuses with an
  explicit 503 rather than rendering an error as if it were data. The
  migration retries on a capped backoff, so a database that comes back heals
  the process **without a restart**. Fix the database; do not restart the app.
- **`/healthz` does not answer at all** — the request never reached this
  process. That is the host, the edge or the container, not the app.

⚠️ **A 200 from a `/healthz` you did not deploy proves nothing.** Some hosting
platforms answer `/healthz` at their own edge. Check for a header such as
`x-envoy-upstream-service-time`, or confirm `/api/version` reports the commit
you expect — that response can only come from this app.

**Why this exists:** the app used to run `migrate()` *before* `app.listen()`
and exit on failure, so an unreachable database meant the port was never bound.
That is not a degraded site, it is a blackout — every path hangs, static assets
included, and a restarting platform crash-loops with no healthy origin to route
to. The port is now bound first and migration retries in the background.

---

## Engine triage — is the loop healthy, and how do you know?

`/status` carries the loop's own instruments. Two lines matter when trades
start being rejected.

**`Tick phase timed out: <phase> (exceeded its Ns, xN)`** — a phase blew its
cap. Each failure feeds the warning-rate breaker, and enough of them trip it,
at which point the engine starts REJECTING trades:

```
WARNING_RATE_BREAKER: infrastructure warnings firing too
frequently (key='engine_tick_failure')
```

Read that as the breaker doing its job. The thing it is protecting the account
from is the loop, not the market.

**`Slowest tick phase: <phase> Ns peak of Ns (N%)`** — the headroom, reported
whether or not anything has broken. This exists because the cap used to be a
cliff: 299s of a 300s budget looked identical to 30s right up until the tick
died, so the first signal anyone ever got was a run of failures. Peak rather
than mean, because the tail is what trips a cap; ranked by ratio rather than
seconds, because a 40s phase against a 45s cap is in more danger than a 100s
one against 300s.

⚠️ **The percentage scales with the universe.** `TOP_MOVERS_COUNT` sets how
many symbols each cycle analyses. Roughly triple the universe, roughly triple
the phase time — a comfortable margin at 70 symbols is not a comfortable margin
at 200. Re-read this line after changing it.

**Start here: how far did the batch get?** When the analyze phase is
cancelled it now reports its own progress, in the log and on `/status`:

```
Tick phase 'analyze' exceeded its 300s cap … It had finished 41 of 200
signals (21%) in 300s — 1463s needed at that rate.

  ↳ 41/200 signals analysed before it was cancelled
```

That fraction is the first thing to read, because the two ends of it call for
opposite fixes and no amount of reasoning substitutes for it:

| the fraction | means | fix |
|---|---|---|
| small, and the projection far exceeds the cap | the universe is wider than the budget | lower `TOP_MOVERS_COUNT`, raise `SCAN_ANALYSIS_CONCURRENCY`, or raise `TICK_PHASE_TIMEOUT_SEC` |
| close to complete | the budget is nearly right | raise the phase cap modestly, or trim the universe slightly |
| stuck at a low number across several ticks | specific symbols are blocking | see the audit line below — it names them |

⚠️ **Two fixes were shipped against this before that number existed** (a
per-analysis cap, then bounding the fetch chain). Both corrected genuine
defects and neither stopped the timeouts, because the cause was inferred
rather than measured. Read the fraction first.

**The analysis-timeout audit line.** Each analysis is bounded individually
(`ANALYSIS_TIMEOUT_SEC`, default 90s). When any hit that cap the engine records
it and names the symbols:

```
Analysis timed out for 7 of 180 signals after 90s each.
The tick continues with the rest.    action=analysis_timeout
```

This is the line that tells you *which* problem you have, and the two answers
call for opposite fixes:

| what it names | means | fix |
|---|---|---|
| many symbols at once | exchange-wide slowness or rate-limiter queueing | tune the caps below, or narrow the universe |
| the same one or two, repeatedly | those symbols' endpoints are bad | drop them from the universe |

Do not skip this step and assume the first. A phase can reach a 300s cap with
nothing hanging at all: an analysis makes ~9 requests, and at a 30s per-request
cap six of them in series is 180s before the LLM turn is even reached.

### Were the stops actually watched? — the SL/TP monitor line

The degraded alert says open positions "could be **unmonitored**". That is a
hedge, and the process does not have to hedge: `_check_open_positions` runs
LAST in a tick, so a raised analyze phase unwinds the tick before reaching it,
and `_backstop_position_monitor` then runs it from `_tick_guarded`'s `finally`.
Whether that back-stop *completed* is the answer, and it is now on both screens
the alert points at — `/status` and `/positions`.

| shown | means | do |
|---|---|---|
| (nothing on `/status`) | the tick ran its own check and it finished | nothing |
| `SL/TP monitor: ran this tick` | same, stated explicitly on `/positions` | nothing |
| `SL/TP monitor: tick ended early — back-stop watched the stops` | **stops watched, loop failing.** Two facts; the second is the actionable one | triage the tick above |
| `SL/TP monitor: DID NOT RUN — open positions unwatched` | the back-stop ran and did not complete | **verify SL/TP on the venue directly** |
| `SL/TP monitor: back-stop FAILED — open positions unwatched` | the back-stop raised | as above |
| `SL/TP monitor: not recorded` | no tick has recorded a verdict yet | check the engine is running at all |

A `×N ticks` suffix counts *consecutive* ticks that ended unwatched. One is a
blip. A run of them is the case the back-stop was written for — a persistently
slow analyze phase leaves stops unwatched for as long as it keeps failing,
which is exactly when an exchange is struggling and a stop matters most.

Do not read a green `/status` headline as an answer to this. The loop being
**alive** is the state in which stops go unwatched: analyze blows its cap, the
tick unwinds, and the engine keeps ticking on schedule.

The same verdict has always been in the log, if you can reach it:

```
Tick ended before its position check — ran the SL/TP monitor as a backstop
                                              action=positions_backstop RAN
Tick ended before its position check AND the backstop SL/TP monitor did not
complete — open positions are unwatched for this tick
                                       action=positions_backstop INCOMPLETE
```

### The caps, and what each one bounds

| env | default | bounds |
|---|---|---|
| `ANALYSIS_TIMEOUT_SEC` | 90 | one symbol's whole analysis. `0` disables |
| `MARKET_DATA_TIMEOUT_MS` | 15000 | one request by the scanner's keyless data clients |
| `TICK_PHASE_TIMEOUT_SEC` | 300 | one phase of the tick (positions / scan / analyze) |
| `GATEWAY_CONNECT_TIMEOUT_MS` | 4000 | the website's TCP connect to the bot gateway |

`MARKET_DATA_TIMEOUT_MS` is deliberately **not** shared with the trading
clients. A cap tuned for a public market-data GET has no business bounding a
live order.

## Turning on high-conviction flat sizing

Above `HIGH_CONVICTION_MIN_CONFIDENCE`, every trade takes
`HIGH_CONVICTION_MARGIN_USD` of margin instead of the usual
`risk_budget / stop_distance_pct`. It is a TARGET: every existing ceiling still
reduces it and none can be bypassed.

**Do it in two steps.** Notional is margin x leverage, so changing both at once
multiplies the exposure change and leaves you unable to tell which half was
wrong if something surprises you.

**Step 1 — margin only** (needs a restart; config is read at boot):

```
HIGH_CONVICTION_ENABLED=true
HIGH_CONVICTION_MIN_CONFIDENCE=0.70
HIGH_CONVICTION_MARGIN_USD=100
```

Leave `DEFAULT_LEVERAGE` alone. At the standard 5x that is 500 notional per
trade — half the eventual size, enough to prove the path. Confirm it fired:

```bash
grep 'high_conviction_size' logs/*.log | tail -3
# ... confidence 78% >= 70% — margin $23.40 -> $100.00
```

If the line does not appear, the rule is not reaching the sizing path and
nothing else below matters yet.

**Step 2 — leverage** (no restart, admin, instantly reversible):

```
/leverage set 10        # every NEW position; open ones keep what they opened with
/leverage reset         # back to the configured default
```

### The arithmetic to check first

Work these out for YOUR equity before step 2, because they decide whether your
stop-loss or the drawdown breaker is the control that actually binds:

| | |
|---|---|
| drawdown budget | `equity x MAX_DRAWDOWN_PCT` |
| adverse move that spends it, one position | `budget / notional` |
| ...across `MICRO_MAX_OPEN_POSITIONS` | `budget / (notional x positions)` |

At $884 equity, a 7% limit and 1000 notional that is **6.2% on one position**
and **1.24% across five**. If your stops sit wider than the single-position
figure, the drawdown halt fires before any stop does — the account-level
breaker becomes your stop, which is not what either control is for.

`MICRO_MAX_OPEN_POSITIONS` is the lever that changes the second number, and it
is the one worth revisiting when per-trade notional goes up.

## Reading the dashboard honestly

The web app deliberately distinguishes "we could not read this" from "there is
nothing here", because collapsing the two turned every database blip into an
apparent trading outage. The vocabulary:

| shown | means |
|---|---|
| `● ENGINE LIVE / STALE / OFFLINE` | judged from a scan we actually read, by its age |
| `● ENGINE STATUS UNKNOWN` | **the site could not read its own feed.** Says nothing about the engine |
| `● NO ENGINE DATA` | the feed read fine and holds no scan yet |
| `● CONNECTING` | no read has finished yet |
| a panel's error state + Retry | the request failed |
| a panel's empty state | the request succeeded and the answer was empty |
| `Live updates disconnected` (pulse dot) | the SSE stream is down; figures on screen may be stale |

`ENGINE STATUS UNKNOWN` is a **website** symptom. Check `/readyz` — not the
bot.

## There are TWO processes, and one of them was never being started

    python -m bot.main    Telegram + engine. Also serves the GATEWAY on :8080
                          (chat, proof, cards) — this is what every deploy ran.
    python api_bridge.py  a SEPARATE uvicorn app on :8000. Three dashboard
                          panels read it: insight, patterns, lab.

On 2026-08-25 the bridge was down for hours. **Nothing crashed — nothing had
ever started it.** The deploy sequence started the bot, the bot restarted fine,
the gateway recovered, `/api/public/status` reported the system healthy, and
`/api/insight/*` and `/api/patterns/*` returned 502 the whole time. It surfaced
because an operator noticed broken pages.

Two separate defects made that possible, and both are closed:

- **The launcher started one process.** `scripts/launch_all.sh.template` starts
  both, smoke-tests both, and probes both PORTS — a process can be alive and
  not serving, and `kill -0` cannot tell those apart. Copy it OUTSIDE the repo
  before use; `tests/test_launch_all_starts_both.py` pins every guarantee.

- **The status page probed one link.** It reported `bot_gateway: reachable` and
  called that healthy while half the system was unreachable. A status page that
  probes one of two links reads as coverage while providing none. `api_bridge`
  is its own component now and counts toward the overall verdict.

`BOT_API_URL` addresses the bridge; unset reports `not_configured`, not
`unreachable`. Those are different faults with different fixes — "unreachable"
sends you hunting a dead process that was never addressed.

## The learning overlays, and why two of them read zero

`/calibration` shows three overlays. On 2026-08-25 it read:

```
Confidence calibration — SHADOW      calibration: NOT READY (0/30 samples)
Per-setup expectancy  — SHADOW      55 setups, 81 trades
Voter-weight learning — SHADOW      NOT READY (0/20 trades)
```

Three learners over the same trading activity, two with **nothing**. That is
not a bug, and it is worth understanding before anyone "fixes" it.

**Calibration and voter-weights JOIN a decision row to an outcome row by
`paper_trade_id`. Setup-expectancy reads outcome rows alone.** A paper close
writes only an outcome row unless `LEARN_CALIBRATION_FROM_PAPER` is on — so the
two joiners see nothing while the third sees every paper trade. The counts are
exactly what the design says they should be.

`LEARN_CALIBRATION_FROM_PAPER` defaults **OFF**, and the reason is sound: those
two learners feed live confidence and the admin auto-trade gate, so letting
paper-derived calibration reach live money is an operator decision rather than
a default.

### What that means in simulation-first operation

You are running a bot whose learners are configured for live-first evidence,
on a book that is almost entirely paper. They will read zero indefinitely.

The unlock, and it is safe:

```bash
LEARN_CALIBRATION_FROM_PAPER=true      # paper fills now write a decision row too
```

All three overlays stay **SHADOW** — computed and logged, never applied — so
this changes no trading behaviour at all. It only starts accumulating the
evidence. After ~30 joined samples, `/calibration` can answer the question that
matters: **does 0.85 confidence actually win 85% of the time?**

Until then it cannot, and neither can you. With 0 samples the calibrator is
exact identity, so a poor win rate is **not** evidence that the confidence
numbers are miscalibrated — it is evidence of nothing either way. Do not read
the two zeroes as a verdict on the model.

Enabling calibration to APPLY (`CONFIDENCE_CALIBRATION_ENABLED=true`) is a
separate, later, money decision. Collect first, read the curve, then decide.

> **Do not go hunting `LEARN_FROM_PAPER_CLOSES` for this.** It defaults ON and
> governs the outcome write, not the decision row. Its comment claimed
> "opt-in, default OFF" until 2026-08-25 and sent this exact investigation at
> the wrong flag. Four flags carried that stale audit annotation and
> `LearningConfig`'s docstring carried a worse one — it called
> `ADAPTIVE_CONFIDENCE_ENABLED` opt-in and default-OFF while the code read
> `True`, so a nudge that adjusts **live entry confidence** looked inert.
> `tests/test_flag_prose_matches_default.py` pins all of it now.

## Standing hazards

**An ephemeral Cloudflare quick tunnel is a single point of failure.** The
`*.trycloudflare.com` URL is bound to the `cloudflared` process: restart it and
the URL changes, at which point the website can no longer reach the bot and web
chat is down until both configs are hand-edited on two hosts.

Fix it with a **named** tunnel plus a supervisor — two different failure modes,
and a named tunnel whose process died overnight is exactly as unreachable as a
quick tunnel whose URL rotated. Templates, the ordered commands and the
verification steps live in `scripts/cloudflared/`.

The bot probes `PUBLIC_GATEWAY_URL` and alerts after two consecutive failures,
so this now announces itself — but an alert about a foreseeable, preventable
outage is worse than not having the outage.

## Operating habits (ops tips, 2026-07)

1. **Size like the backtest verdict is real.** The offline deterministic path
   has no proven standalone edge yet; keep `MICRO_MAX_POSITION_USD` small until
   the recorded-replay walk-forward is positive. The system's proven strength
   is loss control — lean on it while edge evidence accumulates.
2. **Run uninterrupted for 2–4 weeks.** The calibrator, setup expectancy,
   voter-weight OOS validation and replay backtests all unlock at ~50–100
   closed trades; restarts delay that.
3. **`/whynot` when the bot feels quiet.** Structured no-trade reasons now back
   it: all-`confidence` rejections → review per-strategy `min_confidence`;
   `llm_direction_guard` rejections → LLM and voters are fighting.
4. **Backtest invocation:** `python -m bot.backtest.runner --honest` (strict
   real data + next-open fills). Close-fill numbers flatter by ~0.9pp/run.
5. **Backups:** `./scripts/backup_data.sh` (cron daily; see the script header).
   Everything the bot cannot regenerate — positions, learning store, auth
   secret — lives in `data/` and compounds in value.
6. **Dead-man's switch:** create a check at healthchecks.io (or similar), set
   `HEALTHCHECK_PING_URL`, alarm grace ~30 min. Telegram can never report a
   dead process; this does.
7. **Key hygiene before scaling deposits:** rotate the Bitget key, confirm
   withdrawals are disabled on it, IP-allowlist it to the VPS.
8. **Payoff sequence once data accumulates:** (a) confirm the calibrator is
   fitting, (b) `VoterWeightLearner.validate_oos` — enable
   `VOTER_WEIGHT_LEARNING_ENABLED` only if hold_rate > ~0.6, (c) re-run the
   walk-forward with `--use-recorded-llm --use-recorded-order-flow` for the
   full-pipeline verdict, (d) only then consider `AUTO_CONFIRM_USE_CALIBRATED`.
