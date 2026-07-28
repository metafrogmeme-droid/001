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

The web app exposes two probes. They answer different questions, and the
difference is the whole diagnosis:

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
| `db_error` | reached, failed, cause not yet classified | grep the log (below) and send the code |

⚠️ **`starting` with `attempts: 0` long after boot is not "still starting".** It
means no attempt has ever *finished* — the driver is hanging. Each attempt is
capped (`MIGRATE_ATTEMPT_TIMEOUT_MS`, default 45s), so this should resolve into
`db_timeout` and keep retrying; if it does not, the cap has been disabled.

**`db_tls` — the most likely TiDB failure.** TiDB Cloud mandates encrypted
transport and refuses a plaintext connect with `ER_SECURE_TRANSPORT_REQUIRED`.
The database is fine; the connection string is missing its TLS options. Append
them to `DATABASE_URL`:

```
mysql://USER:PASS@HOST:4000/DB?ssl={"minVersion":"TLSv1.2","rejectUnauthorized":true}
```

**Which build is actually serving.** `/api/version` reports the commit when it
can. On a bundled deploy it usually cannot — no `BUILD_SHA`, no
`build-info.json`, no `git` binary, no `.git` — and honestly says
`"sha":"unknown"`. The `build` field answers the question anyway:

```bash
curl -s https://YOUR_HOST/api/version
# {"sha":"unknown","build":"9fe79eaa6713+186","started_at":"…","uptime_s":1303}
```

It is a hash of the `.js` source that shipped, not a commit id — it changes
when the code changes and does not when it has not. Compute the expected value
from a checkout **before** deploying and compare:

```bash
node -e "console.log(require('./app/lib/version').fingerprint())"
```

Same value → your build is live. Different → it is not, whatever the deploy log
says. (Ask the host to pass `BUILD_SHA` as a build arg and `sha` becomes a real
commit id, which is nicer still.)

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
