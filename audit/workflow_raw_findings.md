# Raw findings from the completed audit dimensions

**Read the section header before the finding.** This file has two halves and
they carry very different weight. The banner that used to sit here said
"Status: UNVERIFIED" and applied to the whole file; that was written before the
verifiers ran and stayed true only of the first 22 items. Leaving it up made
162 verified findings read as unverified, which is the opposite of this audit's
usual failure but is the same defect: a status line that stopped tracking what
it describes.

| section | items | verification |
|---|---|---|
| `W-01` … `W-22` | 22 | **UNVERIFIED.** The first run's two verifiers per dimension died on the session rate limit. Nothing in this section has been through a refutation pass. Treat as SUSPECTED. |
| `M-*`, `B3-*` … `B7-*` | 162 | **VERIFIED.** Each finding was put to two independent adversarial verifiers with different lenses, both instructed to default to `refuted`. Refuted by both → dropped and recorded as refuted in the batch summary; by one → SUSPECTED; by neither → CONFIRMED. |

Across the six verified batches: **172 raw → 162 CONFIRMED, 6 SUSPECTED, 4
REFUTED.** Only CONFIRMED findings are written up as blocks below, which is why
the block count and the confirmed count are the same number.

**The lead-auditor register is `verified_findings.md`** (`RC-2026-NNN`): the
subset re-verified by hand — code re-read, reachability established from outside
the file, reproduced where reproduction was cheap. A finding here that is not
there has been through the verifiers but not through that.


## W-01 — /risk/halt swallows the halt failure and returns a hardcoded success, and never does what its own docstring promises

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: fail-open-control / dishonest-success
- **File**: `api_bridge.py:1035-1044`
- **Fix class**: REVIEW_REQUIRED

**Observed**: `circuit_breaker_active: True` is a hardcoded literal in the response dict — it is never read from the engine, so the endpoint asserts a state it did not measure. Separately, the docstring says "activate circuit breaker, close all positions" and the handler closes nothing: the real kill switch is `RuneClawEngine.emergency_halt_all` (bot/core/engine.py:2437-2478), which sets `self._halted = True`, halts every per-user `RiskEngine` in `self._user_risk`, clears `_pending_ideas`/`_pending_atr`/`_pending_pyramid`, and calls `flatten_all_positions`. `/risk/halt` calls only `self.risk.emergency_halt` on the shared engine, so per-user risk engines stay open, queued ideas stay queued, and open positions stay open — while the operator is told the emergency halt is done.

**Expected**: The response should report the breaker state actually read back from the engine (`engine.risk.circuit_breaker_active`), and a halt that raised should be a non-200 / explicit "could not halt" — three outcomes, not two. It should also do what the docstring says.

**Root cause**: A bare `except Exception: pass` around the only action the endpoint performs, plus a response body built from literals instead of from a read-back of engine state; and the handler was never updated to call `emergency_halt_all` after that method was added.

**Standard**: CLAUDE.md: "Unreadable is never zero, and absent is never a measurement" / "guard or omit — never neither". The same file records `_status_lines` and `_cmd_escape` failing this way on risk surfaces.

**Remediation**: Let the exception propagate (or catch it and return an explicit failure), and build the response from a read-back: `return {"ok": True, "circuit_breaker_active": bool(engine.risk.circuit_breaker_active), ...}`. If the endpoint is meant to be the emergency stop the docstring describes, call `await engine.emergency_halt_all("Emergency halt from dashboard")` and return its structured summary; otherwise correct the docstring to say it only trips the shared breaker.

**Reachability check**: Reachable. Route is registered on the app that docker-compose.yml:96-98 runs under uvicorn, and nginx.conf:100-108 proxies /api/* to api_bridge:8000 after stripping /api. Token-gated via `require_dashboard_token` (api_bridge.py:376-388), which fails closed with 503 when DASHBOARD_TOKEN is unset — so the caller is the operator, which is exactly who is misinformed. No upstream guard prevents the swallowed exception or replaces the hardcoded literal.

**Existing-test check**: grep of tests/ and app/test/ for `risk/halt`, `risk_halt`, `emergency_halt` finds only tests/test_ci_covers_what_ships.py (a CI-coverage note naming the route) and unit tests of `RiskEngine.emergency_halt` itself. Nothing exercises this handler, and nothing pins the response body against a failing halt. scripts/guard_lint.py:617 mentions the route only to justify the token dependency.

**Evidence**:

```
api_bridge.py:1035-1044

    @app.post("/risk/halt")
    async def risk_halt(_token: str = Depends(require_dashboard_token), _rl: None = Depends(_require_rate_limit)):
        """Emergency stop — activate circuit breaker, close all positions."""
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        try:
            engine.risk.emergency_halt("Emergency halt from dashboard")
        except Exception:
            pass
        return {"ok": True, "circuit_breaker_active": True, "message": "Emergency halt activated"}
```

## W-02 — Account purge reads a TelegramHandler attribute that does not exist, so the bot's user record is never deleted and every web account deletion is permanently blocked

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: broken-wiring / data-deletion
- **File**: `bot/web/user_gateway.py:2885`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: `getattr(tg_handler, "user_store", None)` is always None in production, so `user_record` is unconditionally "error", `ok = all(v in ("deleted","none") ...)` is always False, and the endpoint always answers 409 with `purged: false`. Two consequences: (1) the bot's UserStore record for the person is never erased on any purge; (2) app/auth.js:1707-1715 (`if (!purge || purge.status !== 200 || body.purged !== true)`) aborts and returns 502 "Your account was NOT deleted" — so DELETE /account is broken for every user with a linked telegram_id whenever the gateway is configured.

**Expected**: `store = tg_handler.users` — the record is deleted, `user_record` reports "deleted"/"none", `purged` is true, HTTP 200, and app/auth.js proceeds with the web-side erasure.

**Root cause**: A wrong attribute name behind a `getattr(..., None)` default, which converts a typo into a silent permanent "error" instead of an AttributeError anyone would have seen.

**Standard**: CLAUDE.md: "A module nothing calls is indistinguishable from one that does not work" and "Write the assertion, then re-run the search" — the purge tests source-scan the handler rather than running it, which is exactly the gap this fell through.

**Remediation**: Replace `getattr(tg_handler, "user_store", None)` with `getattr(tg_handler, "users", None)` (every other line in this module uses `tg_handler.users`). Add a behavioural test that calls `handle_account_purge` against a handler exposing `.users` and asserts `user_record == "deleted"` and HTTP 200 — the existing tests in tests/test_account_purge.py all read the source with `inspect.getsource` and cannot see this.

**Reachability check**: Reachable and unconditional. The route is registered (bot/web/user_gateway.py:3628 `app.router.add_post("/account/purge", handle_account_purge)`), mounted by bot/web/dashboard_server.py:519, proxied by nginx.conf:147-150 (/gateway/ -> runeclaw-bot:8080), and called by app/auth.js:1690. No upstream guard supplies a `user_store` attribute: grep across the whole tree finds `.user_store` referenced only at this one line.

**Existing-test check**: tests/test_account_purge.py has a whole class for this handler, but every assertion is `inspect.getsource(...)` string matching ("test_every_store_answers_and_the_answers_are_returned", "test_a_raising_store_is_error_not_silence", "test_a_partial_purge_is_409_and_not_200"). None constructs a handler and calls the coroutine, so all of them pass against the broken attribute.

**Evidence**:

```
bot/web/user_gateway.py:2884-2892

    try:
        store = getattr(tg_handler, "user_store", None)
        if store is None:
            result["user_record"] = "error"
        else:
            result["user_record"] = "deleted" if store.forget(tg_id) else "none"
    except Exception as exc:                      # pragma: no cover - defensive
        system_log.warning("purge: user record failed for %s: %s", tg_id, exc)
        result["user_record"] = "error"

The attribute is `users`, not `user_store` — bot/skills/telegram_handler.py:846:

        self.users = UserStore()
```

## W-03 — Redis unreachable at process start silently downgrades JWT revocation to in-process, so /auth/logout reports success on a non-durable revocation and refresh-replay detection disappears

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: session-revocation / silent-degradation
- **File**: `bot/api/token_store.py:106-110`
- **Fix class**: REVIEW_REQUIRED

**Observed**: The connect failure happens once, in `__init__`, and sets `self._redis = None` permanently (the store is a process-wide singleton via `get_token_store()` at bot/api/token_store.py:189-197, with no reconnect anywhere). From then on `bump_epoch` takes the `self._redis is None` branch at line 158-159 and returns a number, and `try_consume_jti` takes the branch at 183-186 and returns True for a jti no other worker knows about. So (a) POST /auth/logout returns `{"ok": True}` while every worker that DID connect to Redis keeps honouring the access and refresh tokens the user just revoked, and (b) a replayed refresh token routed to this worker is accepted, because the replay guard's shared state is gone — the exact failure the docstring at lines 32-34 says it will not permit ("an accepted replay costs the account").

**Expected**: When REDIS_URL/REDIS_HOST is set, durability is promised, so a revocation that cannot reach Redis must raise `RevocationNotDurable` — which auth_routes.py:417-422 turns into a 503 "Could not complete sign-out" and auth_routes.py:384-389 turns into a 503 on refresh. That is the documented and intended posture.

**Root cause**: The read/write posture split is implemented per-operation but not at the connect seam: a connect-time failure is treated identically to "no Redis configured", losing the fact that durability was promised. There is also no reconnect, so a single Redis blip during a rolling restart makes that worker permanently degraded.

**Standard**: CLAUDE.md: "Unreadable is never zero, and absent is never a measurement" — and the module's own M16 note: "'we tried to log you out' and 'you are logged out' stop being the same answer."

**Remediation**: Record that Redis was configured (e.g. `self._redis_configured = bool(url or host)`) and, in `bump_epoch` / `try_consume_jti`, raise `RevocationNotDurable` whenever `self._redis is None and self._redis_configured`. Add a lazy reconnect attempt on each write so a recovered Redis is picked up. Add a test that constructs `TokenStore()` with REDIS_URL pointing at a dead port — every existing test bypasses the constructor.

**Reachability check**: Reachable. `get_token_store()` is called from auth_routes.py:182 (`_revoke_user_tokens`), :195 (`_check_and_record_refresh`), :228 (`_verify`) and :245 (`create_jwt`); auth_router is mounted at /auth by api_bridge.py:341 and is internet-reachable as /api/auth/* via nginx.conf:100-108. docker-compose.yml:107-109 gives api_bridge `depends_on: redis: service_healthy`, but that only orders the first boot — it does not prevent a Redis outage during a restart, and there is no reconnect afterwards.

**Existing-test check**: tests/test_audit_v5_followup_auth.py covers this area but every test builds the store through the helper `_store_with_redis` (lines 220-229), which does `TokenStore.__new__(TokenStore)` and assigns `store._redis` directly — deliberately "bypass env connect". So `_maybe_connect_redis` is never exercised, and `test_a_revocation_that_did_not_persist_is_not_reported_as_done` / `test_replay_detection_fails_closed_rather_than_falling_back` pass while the real configured-and-unreachable path does the opposite of what they assert.

**Evidence**:

```
bot/api/token_store.py:103-110 (inside `_maybe_connect_redis`, called once from `__init__`)

            client.ping()
            logger.info("JWT revocation store: Redis backend active")
            return client
        except Exception as exc:
            logger.warning(
                "Redis unavailable — JWT revocation falls back to in-process: %s", exc
            )
            return None

which contradicts the module's own contract at bot/api/token_store.py:55-58:

        Raised ONLY when Redis is configured and unreachable — that is, when
        durability was promised and not delivered. With no Redis configured the
        in-process store IS the backend, nothing is promised, and nothing raises.
```

## W-04 — Unauthenticated /ready echoes the raw exception string from the health subsystem

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: information-disclosure (CWE-209)
- **File**: `bot/web/dashboard_server.py:369-371`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: `str(exc)[:120]` is returned unfiltered on an endpoint that `auth_middleware` (bot/web/dashboard_server.py:452) does not guard, because it only checks `request.path.startswith("/api/")`.

**Expected**: A coarse reason code from a fixed vocabulary, as CLAUDE.md states for /readyz: "driver messages never reach it". `handle_health` two functions up already does this correctly ("every failure path inside it yields 'unknown' rather than raising").

**Root cause**: An exception-to-JSON passthrough on a route that sits deliberately outside the auth prefix.

**Standard**: CLAUDE.md, Public-surface rules: "Never put secrets, API keys, private keys or internal config into user-facing text, logs, or the repo. /readyz returns a coarse reason code from a fixed vocabulary for exactly this reason — driver messages never reach it."

**Remediation**: Log the exception server-side and return a fixed code, e.g. `{"ready": False, "reason": "health_unavailable"}`. Also correct scripts/guard_lint.py:639, whose exclusion reason for this handler is "readiness, boolean only" — that justification is false as written and is why the rule does not catch it.

**Reachability check**: Reachable without any credential. Registered at bot/web/dashboard_server.py:514 (`app.router.add_get("/ready", handle_ready)`); `auth_middleware` only guards `/api/*` (line 452); bot/main.py:455-456 binds this app to `DASHBOARD_BIND_HOST` defaulting to `0.0.0.0` on port 8080. So anything that can reach the container's port 8080 — the docker network, and the host network if the port is published — can read it.

**Existing-test check**: tests/test_ops_endpoints.py TestReady covers only the status codes (200/503) including `test_fails_closed_without_health`; it never inspects the response body. Nothing pins the error text. scripts/guard_lint.py explicitly excludes `handle_ready` from the dashboard-route-placement rule with a reason that no longer matches the code.

**Evidence**:

```
bot/web/dashboard_server.py:358-371

        engine = request.app["engine"]
        try:
            snap = engine.health.snapshot()
            ...
            return web.json_response(body, status=200 if ready else 503)
        except Exception as exc:
            return web.json_response(
                {"ready": False, "error": str(exc)[:120]}, status=503)
```

## W-05 — dashboard_api.py authenticates the snapshot WRITE but not the READ, publishing every user's dollar balances, PnL and open positions

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: missing-authentication / data-exposure
- **File**: `dashboard_api.py:203-213`
- **Fix class**: REVIEW_REQUIRED

**Observed**: `do_GET` has no authentication of any kind on `/api/snapshot`, `/api/feed` or `/api/health`. The module's `__main__` guard refuses to start without DASHBOARD_API_KEY, which creates the impression the service is authenticated; it is authenticated only for writes.

**Expected**: The read path should require the same `X-API-Key` the write path does (or a separate reader token), since the payload is aggregate multi-user financial data — the posture bot/web/dashboard_server.py:440-450 spells out for the equivalent /api/* surface ("MUST stay (a) token-gated and (b) bound to a trusted network").

**Root cause**: Auth was added to the ingest path (the one that could corrupt state) and not to the egress path.

**Standard**: CLAUDE.md Public-surface rules ("No dollar amounts on public... payloads") and the RC-AUD-017 posture recorded in bot/web/dashboard_server.py:440-450 for the sibling aggregate surface.

**Remediation**: Require `hmac.compare_digest` on `X-API-Key` (or a bearer token) in `do_GET` for the `/api/*` paths, and default `DASHBOARD_BIND_HOST` to 127.0.0.1 as bot/main.py's comment recommends for host-run processes. If the endpoint is genuinely meant to be public, strip the per-user dollar fields from `_build_snapshot` before publishing.

**Reachability check**: LATENT, NOT CURRENTLY LIVE — stated plainly because it changes the severity. grep confirms `dashboard_api` appears in no docker-compose service, no nginx upstream, no Dockerfile and no deploy script, and port 9090 appears in neither docker-compose.yml nor nginx.conf. bot/core/dashboard_pusher.py:28-43 documents the same ("NOTHING IN THIS REPO DEPLOYS THE CONSUMER") and the pusher no-ops without DASHBOARD_API_KEY. The exposure becomes live the moment an operator sets DASHBOARD_API_KEY and runs this module — which bot/core/engine.py:3619 anticipates: "the day someone sets DASHBOARD_API_KEY is the day it starts."

**Existing-test check**: tests/test_dashboard_api_hardening.py drives a live server but only covers path traversal, HEAD/GET routing parity, and unreadable-vs-missing JSON. It sets DASHBOARD_API_KEY and issues `GET /api/snapshot` with no key (line 167) expecting a 503 for a corrupt file — i.e. it exercises the unauthenticated read and never asserts that authentication is required. Nothing pins GET auth.

**Evidence**:

```
dashboard_api.py:203-213 (no auth anywhere in do_GET)

        if path == "/api/snapshot":
            snap = read_json(DATA_FILE)
            ...
            else:
                self._json_response(snap)
            return

contrasted with dashboard_api.py:278-284 (do_POST):

        if not API_KEY:
            self._json_response({"error": "DASHBOARD_API_KEY not configured"}, 403)
            return
        key = self.headers.get("X-API-Key", "")
        if not key or not hmac.compare_digest(key, API_KEY):
            self._json_response({"error": "bad key"}, 403)
            return
```

## W-06 — Unauthenticated /api/lab/run lets an internet caller keep a backtest subprocess permanently running and grow an unbounded job registry in the 1-CPU/1G container that also serves /risk/halt

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: missing-authentication / resource-exhaustion
- **File**: `bot/api/lab.py:55-57, 108-117, 167-177`
- **Fix class**: REVIEW_REQUIRED

**Observed**: `lab_run` and `lab_status` take no auth dependency and no `_require_rate_limit`. The only limiter is a process-global 5-second submit gap, so one anonymous caller can (a) hold the single job slot indefinitely, denying the Lab to everyone else via the 409 at line 115-117, (b) keep a CPU-bound subprocess running inside a container capped at `cpus: '1.0'` (docker-compose.yml:118), starving the single-worker uvicorn event loop that also serves /health, /scan, /confirm, /portfolio/close and /risk/halt, and (c) grow `_jobs` without bound in a container capped at `memory: '1G'` with `restart: unless-stopped`.

**Expected**: Either a `require_dashboard_token` dependency (as every other state-touching api_bridge route has), or a per-IP budget via `_require_rate_limit` (api_bridge.py:146-153), plus an eviction policy on `_jobs`.

**Root cause**: The router was designed to be safe by input-bounding (whitelisted datasets, clamped numerics, one job at a time) but the bounds constrain what a run computes, not how continuously an anonymous caller may demand runs, and nothing ever frees a finished job.

**Standard**: The module's own rationale at bot/api/lab.py:12-14: "one job at a time, process-wide (a backtest saturates a core; a queue of them would starve the live engine sharing the host)" — the concern is stated, and the mitigation only bounds concurrency, not duty cycle or lifetime.

**Remediation**: Add `Depends(_require_rate_limit)` (per-IP) to `/lab/run`, or gate it behind the dashboard token like every other non-market route. Independently, cap `_jobs` — evict by age or keep the last N — since 'results are kept for the session' is unbounded for a long-lived server process.

**Reachability check**: Reachable from the internet. lab_router is included at api_bridge.py:344 (`app.include_router(lab_router, tags=["lab"])`); api_bridge is the uvicorn app in docker-compose.yml:96-98; nginx.conf:100-108 proxies /api/* to it after stripping the prefix, so /api/lab/run resolves. nginx's `limit_req zone=rc_api` is documented as a 30r/m drip, which does not constrain one submission per 5 s. scripts/guard_lint.py:610-613 deliberately excludes the three lab routes from the token rule, so no gate is expected to catch this.

**Existing-test check**: tests/test_lab_api.py exists and monkeypatches `_jobs` to `{}` per test (line 24), so it cannot observe unbounded growth; nothing in tests/ asserts an auth or per-IP limit on these routes.

**Evidence**:

```
bot/api/lab.py:54-57 — the registry that never evicts

    # Single-slot job registry. Results are kept for the session (small dicts).
    _jobs: dict[str, dict] = {}
    _running_id: Optional[str] = None
    _last_submit: float = 0.0

bot/api/lab.py:108-114 — the only throttle, global and 5 seconds, with no auth dependency

    @lab_router.post("/lab/run")
    async def lab_run(req: LabRunRequest):
        global _running_id, _last_submit
        now = time.monotonic()
        if now - _last_submit < _MIN_SUBMIT_GAP_SEC:
            raise HTTPException(status_code=429, detail="Slow down — one submission "
                                "every few seconds.")

bot/api/lab.py:167-172 — the job dict is stored and never removed; `_run_job` later does `job.update(status="done", ..., result=result)` with the full backtest JSON.
```

## W-07 — /lab/status returns the backtest subprocess's stderr tail to unauthenticated callers

- **Severity (claimed)**: LOW · **Confidence (claimed)**: CONFIRMED
- **Category**: information-disclosure (CWE-209)
- **File**: `bot/api/lab.py:197-200, 226-228`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: The full tail, including any traceback, is serialised to a caller who presented no credential.

**Expected**: A coarse failure code to the caller and the tail written to the server log only — the same rule bot/web/user_gateway.py:2044-2048 applies to the unauthenticated leaderboard ("Coarse code only. The driver's message never reaches a caller — same rule /readyz follows") and that handle_guardian_review_tighten applies at bot/web/user_gateway.py:3358-3360 ("F-15: never leak an exception string (it can carry secrets) to the caller").

**Root cause**: Debug output intended for an operator is returned on a route that is deliberately public.

**Standard**: CLAUDE.md: "Never put secrets, API keys, private keys or internal config into user-facing text"; the repo's own F-15 convention applied elsewhere in the same codebase.

**Remediation**: Log `tail` with `logging` and drop `log_tail` from the response, or gate `/lab/status` behind the dashboard token so the tail only reaches an authenticated operator.

**Reachability check**: Reachable — same path as the previous finding: lab_router included at api_bridge.py:344 and exposed as /api/lab/status/{job_id} through nginx.conf:100-108. scripts/guard_lint.py:612-613 excludes `lab.py:GET /lab/status/{job_id}` from the token rule on the stated grounds that it "Reads no account", which is true of the result and not of the subprocess log.

**Existing-test check**: tests/test_lab_api.py contains no assertion about `log_tail` (grep for `log_tail` in tests/ returns nothing).

**Evidence**:

```
bot/api/lab.py:185-200 — stderr is merged into stdout and the tail is stored

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        ...
        tail = (stdout or b"").decode(errors="replace")[-2000:]
        if proc.returncode != 0 or not out_file.exists():
            job.update(status="error",
                       error="Backtest run failed.", log_tail=tail)

bot/api/lab.py:225-228 — and returned verbatim

        elif job["status"] == "error":
            out["error"] = job.get("error")
            if job.get("log_tail"):
                out["log_tail"] = job["log_tail"]
```

## W-08 — handle_policy_clear swallows the failure and still answers ok: true

- **Severity (claimed)**: LOW · **Confidence (claimed)**: CONFIRMED
- **Category**: dishonest-success
- **File**: `bot/web/user_gateway.py:2616-2623`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: `ok: true` is a literal that is returned on the exception path as well as the success path, and `removed: false` collapses "there was no policy file" and "the removal raised" into one answer. Because `clear_intent_policy` unbinds in memory inside its `finally` but the on-disk file survives, the operator is told the policy is gone while it will be reloaded on the next restart.

**Expected**: Three outcomes, not two: cleared / nothing to clear / could not clear. A raise should produce a non-200 or an explicit error field, so "there was no policy" and "the delete failed" are distinguishable.

**Root cause**: An exception caught for logging only, followed by a response body built from a literal rather than from the outcome.

**Standard**: CLAUDE.md: "guard or omit — never neither"; and the same file's account of `_status_lines`, where "two layers swallowed the same fault" on a risk control.

**Remediation**: Catch the exception, return `web.json_response({"error": "clear_failed"}, status=500)`, and record `result="ERROR"` in the audit entry — matching the sibling `handle_policy_mode` at lines 2603-2605, which already does exactly this.

**Reachability check**: Reachable but operator-only: `_policy_op_guard` (bot/web/user_gateway.py:2545-2554) requires `_is_admin_id`, and the route sits behind `secret_middleware`. The failure direction is toward MORE restriction (a policy that stays bound), so this is a misreport rather than a bypass — which is why it is LOW and not higher.

**Existing-test check**: tests/test_policy_web_authoring.py exists; grep of tests/ finds no assertion covering the raising path of handle_policy_clear.

**Evidence**:

```
bot/web/user_gateway.py:2612-2623

    async def handle_policy_clear(request: web.Request) -> web.Response:
        engine, tg_id, body = await _policy_op_guard(request)
        if engine is None:
            return body
        removed = False
        try:
            removed = engine.clear_intent_policy()
        except Exception as exc:
            system_log.debug("Web policy clear failed: %s", exc)
        audit(system_log, "Web operator cleared intent policy",
              action="web_policy_clear", result=str(bool(removed)))
        return web.json_response({"ok": True, "removed": bool(removed)})
```

## W-09 — Unauthenticated POST /api/auth/validate-token binds an attacker-chosen Telegram id to the attacker's own web account, letting them act as any Telegram-linked user (incl. the operator) on every bot-gateway route

- **Severity (claimed)**: CRITICAL · **Confidence (claimed)**: CONFIRMED
- **Category**: broken-object-level-authorization / identity-spoofing (OWASP API1 + API5, ASVS V4.2)
- **File**: `app/auth.js:867-889 (route), 886-889 (the write); app/lib/identity.js:18-27; app/routes/credentials.js:81-83,102-110; app/routes/controls.js:75-80; app/routes/staking.js:55-71; app/routes/sync.js:542-546; bot/utils/control_pull.py:78-105; bot/utils/credential_pull.py:82-97; bot/web/user_gateway.py:92-100`
- **Fix class**: REVIEW_REQUIRED

**Observed**: `chat_id` is trusted verbatim from an unauthenticated request body. Any registered user can bind their web account to an arbitrary Telegram id — including the operator's — and the victim's row is left untouched, so two rows now share one telegram_id (no UNIQUE index exists on that column; app/db.js:2239 adds it as a plain `VARCHAR(32) DEFAULT NULL` while wallet_address/referral_code/leaderboard_handle/farcaster_fid all get `CREATE UNIQUE INDEX`).

**Expected**: A Telegram link must prove control of BOTH sides: the web session (via the link token) AND the Telegram account (via the bot's shared secret, since the bot is the only party that can vouch for `chat_id`). The endpoint should require X-Bot-Secret exactly as routes/sync.js does (`router.use(botAuth)` at app/routes/sync.js:288), and refuse a chat_id already bound to a different user id.

**Root cause**: The endpoint conflates two authentications. The link token proves the WEB half; nothing proves the TELEGRAM half, and the design assumed the only caller would be the bot (bot/skills/user_middleware.py:196-208 posts {token, chat_id} with no secret). scripts/guard_lint.py:530-532 explicitly whitelists this route — "auth.js:POST /validate-token  # answers 'is this token valid' — the token IS the credential being checked" — which is true of the READ and false of the WRITE two lines further down: the route does not answer a question, it performs an identity binding from an unauthenticated field.

**Standard**: OWASP API1:2023 Broken Object Level Authorization; API5:2023 Broken Function Level Authorization; OWASP ASVS v4 4.2.1 ("Verify that sensitive data and APIs are protected against IDOR attacks targeting creation, reading, updating and deletion of records") and 2.7/2.8 (out-of-band authenticator binding must be verified on both sides).

**Remediation**: 1. Gate the route with the existing bot shared secret: move it behind the same `botAuth` check routes/sync.js:273-288 uses (constant-time compare of `x-bot-secret` against BOT_SYNC_SECRET), and have bot/skills/user_middleware.py send that header. The bot is the only party that can attest a chat_id.
2. Refuse a chat_id already bound elsewhere: `SELECT id FROM users WHERE telegram_id = ? AND id <> ?` -> 409, mirroring the check auth.js:1071-1075 already performs for wallet_address.
3. Add `CREATE UNIQUE INDEX idx_users_telegram_id ON users (telegram_id)` in app/db.js beside the existing wallet/referral/handle indexes, so the invariant is enforced by the database rather than by convention — note app/routes/sync.js:545-546 (`UPDATE users SET plan = ? WHERE telegram_id = ?`) writes by that key and today can update several rows.
4. Add a regression test in app/test/ asserting that an unauthenticated POST /api/auth/validate-token is rejected, and that a chat_id already on another row cannot be re-bound.

**Reachability check**: Reachable. `authRouter` is mounted at app/server.js:318 (`app.use('/api/auth', authRouter)`) and the route carries no middleware of any kind (`router.post('/validate-token', async (req, res) => {`). There is no global auth middleware in server.js — auth is per-router — so nothing sits in front of it. `resolveBotIdentity` is the consumer and it is imported by 14 routers: authority.js, botstrategy.js, chat.js, contract.js, ingest.js, learn.js, llm.js, positions.js, profile.js, sentry.js, staking.js, web3_execute.js, webtrade.js, guardian_review.js. The credentials/controls `telegram_required` gates (credentials.js:81-83, controls.js:78-80) are satisfied because validate-token also sets `telegram_linked = TRUE`. The one place a step-up could stop the money path does not: app/routes/staking.js:58-64 reads `totp_enabled/totp_secret` from the ATTACKER's own row (`WHERE id = ?`, `[req.user.user_id]`), so an attacker with no 2FA enrolled passes `stepUpBlock` and then posts `telegram_id: ident.id` (the operator's) to the gateway's admin-only /staking/fixed. The engine's flatten isolation guard (bot/core/engine.py:4003-4005, `if per_user and ex is self.live_executor and not self._is_operator_user(tg)`) is likewise evaluated against the impersonated `tg`, so it permits rather than blocks.

**Existing-test check**: No test covers this. `grep -rn 'validate-token' app/test/ tests/` returns nothing under app/test/ — only bot/skills/user_middleware.py:198 (the caller) and scripts/guard_lint.py:530 (the exemption). app/test/authed_queries_scope_to_session.test.js is the repo's authz guard, and it checks a different property by design: whether a user_id-scoped SQL query binds a session-derived value. It cannot see this defect, because the WHERE clause here IS session-derived (`WHERE id = ?, [user.id]`) — what is attacker-controlled is the VALUE being written into the identity column. app/test/two_factor.test.js and app/test/auth_session.test.js cover neither.

**Evidence**:

```
app/auth.js:867-870 — the route takes BOTH halves of the binding from an unauthenticated body:
```
867	router.post('/validate-token', async (req, res) => {
868	  try {
869	    const { token, chat_id } = req.body;
870	    if (!token || !chat_id) return res.status(400).json({ error: 'token and chat_id required' });
```
app/auth.js:886-889 — and writes the caller-supplied chat_id straight into the identity column:
```
886	    await pool.execute(
887	      'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
888	      [String(chat_id).slice(0, 32), user.id]
889	    );
```
The `token` authenticates WHICH WEB ROW is written. Nothing authenticates the `chat_id` — no X-Bot-Secret, no authMiddleware, no signature, no check that the Telegram account is unclaimed. And the attacker mints the token for their own row with app/auth.js:850-858 (`router.post('/link-token', authMiddleware, ...)` returns `res.json({ token })`).

The value written is the app's ONLY notion of who a caller is on the bot — app/lib/identity.js:18-27:
```
18	async function resolveBotIdentity(req) {
19	  const uid = req.user.user_id;
20	  const [rows] = await pool.execute(
21	    'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
22	  const u = rows[0];
23	  if (u && u.telegram_linked && u.telegram_id) {
24	    return { id: String(u.telegram_id), linked: true, email: u.email || '' };
25	  }
26	  return { id: `web:${uid}`, linked: false, email: (u && u.email) || '' };
27	}
```
The bot's admin check is keyed on exactly that string — bot/web/user_gateway.py:92-100:
```
92	def _is_admin_id(tg_handler, tg_id: str) -> bool:
93	    """TelegramHandler._is_admin semantics keyed by raw telegram id."""
94	    user = tg_handler.users.get(tg_id)
95	    if user is not None and user.get("role") == "admin":
96	        return True
97	    raw = CONFIG.telegram.admin_ids
98	    if raw:
99	        return tg_id in {s.strip() for s in str(raw).split(",") if s.strip()}
```
and the bot applies queued web controls keyed on the same string — bot/utils/control_pull.py:79-104:
```
79	        uid = r.get("user_id")
80	        tg = str(r.get("telegram_id") or "")
...
87	            if live is not None:
88	                store.set_live_trading(tg, live)
...
103	                m = float(margin)
104	                store.set_max_margin(tg, m if m > 0 else None)  # 0 clears the cap
```
and imports/deletes exchange API keys keyed on it — b
```

## W-10 — POST /api/auth/2fa/disable has no throttle, lockout or attempt counter — a stolen session can brute-force the 6-digit second factor and strip the step-up that guards live trading

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: broken-authentication / missing anti-automation on a second factor (OWASP API4 + ASVS V2.2.1)
- **File**: `app/auth.js:799-821 (the route); 125-141 and 542,599,1005,1404 (where the only limiter is, and is not, applied)`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: Unlimited attempts. /2fa/disable, /2fa/enable, /change-password (which verifies `current_password` with bcrypt.compare at line 1320), /validate-token, /verify-email, /telegram and /google are all unthrottled; only /register, /login, /wallet/nonce and /forgot-password call `checkRateLimit`.

**Expected**: Verification of a second factor must be rate-limited and account-locked, like the password path already is (app/auth.js:143-158 `checkAccountLockout` / `recordAccountFailure`, 8 failures then a 5-minute lock). ASVS V2.2.1 requires anti-automation on every authentication factor.

**Root cause**: The bespoke limiter in auth.js was bolted onto the four routes that were obviously 'login-shaped'. The 2FA endpoints were added later and were never wired to either counter, and app/lib/rate_limit.js — which every other router uses — was never imported into auth.js at all.

**Standard**: OWASP ASVS v4 2.2.1 (anti-automation controls on authentication) and 2.8.x (OTP verifier rate limiting); NIST SP 800-63B §5.2.2 (throttling of authenticator verification attempts, max 100 consecutive failures); OWASP API4:2023 Unrestricted Resource Consumption.

**Remediation**: Apply the module's existing limiter to the verifying routes and count failures against the per-account counter that already exists:
- `const { rateLimit, ipKey, userKey } = require('./lib/rate_limit');` then attach e.g. `rateLimit({ windowMs: 60000, max: 5, key: userKey })` to /2fa/disable, /2fa/enable and /change-password (the userKey helper already falls back to ipKey when req.user is absent).
- On a failed code in /2fa/disable, call the existing `recordAccountFailure(user.email)` and gate entry on `checkAccountLockout(user.email)`, so failures across sessions and IPs aggregate the way login failures do.
- Add an app/test/ case asserting a 429 after N wrong codes — the two_factor.test.js lifecycle test currently sends exactly one wrong code (line 144) and asserts only the 401.

**Reachability check**: Reachable. `app.use('/api/auth', authRouter)` at app/server.js:318; the route needs only a valid session (authMiddleware). No limiter sits in front of it: server.js mounts no global rate limiter, and auth.js declares no `router.use`. Confirmed empirically — 500 requests, zero 429s. The `express.json({limit:'1mb'})` at server.js:276 and the not-ready gate at 308-315 are the only middleware in the path and neither counts attempts.

**Existing-test check**: app/test/two_factor.test.js is the only 2FA test; `grep -n 'disable|rate|429|attempt' app/test/two_factor.test.js` shows it sends a single wrong code (line 144, `code: '000000'`) and asserts the 401, then a valid backup code, then a clean login — it never asserts a limit. No test file in app/test/ matches 'rate' for the auth router. app/test/stepup.test.js unit-tests the pure helper only.

**Evidence**:

```
app/auth.js:799 — the route carries `authMiddleware` and nothing else:
```
799	router.post('/2fa/disable', authMiddleware, async (req, res) => {
```
app/auth.js:806-813 — an unlimited verify-or-401 loop, with the backup-code list as a second guessable space:
```
806	    const code = String((req.body || {}).code || '').trim();
807	    let ok = totp.verifyTotp(user.totp_secret, code);
808	    if (!ok) {
809	      let backups = [];
810	      try { backups = JSON.parse(user.totp_backup_codes || '[]'); } catch (e) { backups = []; }
811	      ok = totp.consumeBackupCode(code, backups) !== null;
812	    }
813	    if (!ok) return res.status(401).json({ error: 'A valid current code (or backup code) is required to disable 2FA.' });
```
There is no counter on this path. `grep -n "router.use\|rateLimit\|checkRateLimit(" app/auth.js` returns only:
```
125:function checkRateLimit(ip) {
542:    if (!checkRateLimit(clientIp)) {      <- /register
599:    if (!checkRateLimit(clientIp)) {      <- /login
1005:  if (!checkRateLimit(clientIp)) ...     <- /wallet/nonce
1404:    if (!checkRateLimit(clientIp)) {     <- /forgot-password
```
and `grep -n "rate_limit" app/auth.js` returns one hit, a comment on line 123 — app/lib/rate_limit.js is never imported here, unlike the 30+ routers that do use it. The per-account counter `recordAccountFailure` (app/auth.js:152-158) is called only from /login (599-624).

This defeats the control lib/stepup.js:4-7 was written for: "A stolen web session (the primary infostealer target — the JWT lives in localStorage) must not be enough to move real money or unlock live trading." Once totp_enabled is 0, `stepUpBlock` returns null on line 25 (`if (!enrolled) return null;`) for every gated action.
```

## W-11 — POST /api/push/subscribe silently re-assigns another account's push subscription row, and the victim's correctly-scoped unsubscribe then reports success while deleting nothing

- **Severity (claimed)**: LOW · **Confidence (claimed)**: CONFIRMED
- **Category**: broken-object-level-authorization (write) + unreadable-is-never-zero on the response
- **File**: `app/routes/push.js:59-64 (the re-owning upsert); 77-80 (the scoped delete that then matches nothing); app/db.js:2480 (endpoint is the UNIQUE key)`
- **Fix class**: REVIEW_REQUIRED

**Observed**: Ownership transfers on conflict, and the victim's unsubscribe is a confident false success.

**Expected**: An upsert whose conflict key is not the caller's own row must not silently change ownership. Either scope the conflict to (user_id, endpoint), or reject the insert when the existing row belongs to someone else — the pattern auth.js:1071-1075 uses for wallet_address (`if (rows.length && rows[0].id !== req.user.user_id) return res.status(409)`). And per the repo's own rule, a delete that matched nothing is not a successful unsubscribe.

**Root cause**: The table's uniqueness is on `endpoint` alone while the authorization boundary is `user_id`. `ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)` was written to make re-subscribing idempotent for the same user and does not distinguish that case from a different user presenting the same key.

**Standard**: OWASP API1:2023 Broken Object Level Authorization (write path); and the repo's own CLAUDE.md rule — 'a failed read must not render as an empty result... or a confident negative' — applied to a write whose zero affectedRows renders as ok:true.

**Remediation**: Two independent changes: (a) make the row un-stealable — add `UNIQUE KEY uniq_push_user_endpoint (user_id, endpoint)` and drop the endpoint-only UNIQUE (or keep it and 409 when `SELECT user_id FROM push_subscriptions WHERE endpoint = ?` returns a different user); (b) make the unsubscribe honest — read `result.affectedRows` from the DELETE at line 77 and answer 404 when it is 0, the same way routes/alerts.js:70-71 and routes/trades.js:147-148 already do.

**Reachability check**: Reachable by any authenticated caller: `app.use('/api/push', require('./routes/push'))` at app/server.js:367; `router.use(authMiddleware)` at push.js:21 and `subLimit` (10/min per user) at line 39. The only real barrier is knowledge of the target endpoint URL, which is a browser-held bearer-style capability and is not enumerable — so this is not a practical remote attack. It IS reachable without any attacker: two accounts used from the same browser profile (a shared or family device, or a user with two accounts) produce the collision naturally, which is the case that will actually be observed in production.

**Existing-test check**: app/test/ has no push test file (`ls app/test | grep -i push` is empty; the only 'push' matches in app/test are unrelated). app/test/authed_queries_scope_to_session.test.js does not catch it: the query at line 59 binds the session `uid`, which is exactly what that scan asks for — the defect is that the CONFLICT key is not session-scoped, which a `user_id = ?` regex cannot see.

**Evidence**:

```
app/routes/push.js:59-64 — the upsert's conflict target is `endpoint`, and the conflict action reassigns `user_id`:
```
59	    await pool.execute(
60	      `INSERT INTO push_subscriptions (user_id, endpoint, keys_json)
61	       VALUES (?, ?, ?)
62	       ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), keys_json = VALUES(keys_json)`,
63	      [uid, endpoint, JSON.stringify({ p256dh: String(keys.p256dh).slice(0, 200),
64	                                       auth: String(keys.auth).slice(0, 100) })]);
```
The only unique key on the table is the endpoint, so the row that conflicts may belong to anyone — app/db.js:2477-2483:
```
2477	      CREATE TABLE IF NOT EXISTS push_subscriptions (
2478	        id BIGINT AUTO_INCREMENT PRIMARY KEY,
2479	        user_id INT NOT NULL,
2480	        endpoint VARCHAR(500) NOT NULL UNIQUE,
```
The sibling delete IS correctly scoped, and its comment states the property the upsert breaks — app/routes/push.js:76-80:
```
76	    // Scoped to the caller: nobody can unsubscribe someone else's browser.
77	    await pool.execute(
78	      'DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?',
79	      [req.user.user_id, endpoint]);
80	    res.json({ ok: true });
```
The result of the DELETE is never inspected, so after the row has been re-owned the original owner's unsubscribe affects zero rows and still answers `{ ok: true }`.
```

## W-12 — GET /api/lab/status/:id records no owner and checks none — any logged-in user can read another user's backtest parameters, results and error log tail

- **Severity (claimed)**: INFORMATIONAL · **Confidence (claimed)**: CONFIRMED
- **Category**: missing object-level authorization (OWASP API1)
- **File**: `app/routes/lab.js:89-95; bot/api/lab.py:215-228 (the upstream handler); bot/api/lab.py:154,167 (job id + job record)`
- **Fix class**: REVIEW_REQUIRED

**Observed**: No ownership is recorded anywhere, so no ownership can be checked. The job id is the only thing standing between one user's strategy configuration and another's.

**Expected**: A per-user job should either be keyed to its submitter (record the requesting user id on the job at bot/api/lab.py:167 and have routes/lab.js forward `req.user.user_id`, refusing a mismatch), or the surface should be documented as shared. Today neither is true: it reads as private (it is behind authMiddleware) and behaves as global.

**Root cause**: The Lab was designed as a single-slot operator tool ("one job at a time") and later put behind user login without the job record gaining an owner. app/routes/lab.js:6-7 says the web layer 'adds login + a per-IP rate limit', which is authentication, not authorization.

**Standard**: OWASP API1:2023 Broken Object Level Authorization; ASVS v4 4.2.1.

**Remediation**: Record the submitter on the job (`_jobs[job_id]["user"] = <caller id>` at bot/api/lab.py:167, with routes/lab.js:66-84 forwarding `req.user.user_id` in the POST body) and 404 in `lab_status` when the caller does not match. Alternatively, if shared visibility is intended, say so in the module header and stop relaying `log_tail` to non-operators.

**Reachability check**: Reachable by any logged-in user: `app.use('/api/lab', labRouter)` at app/server.js:363, `router.use(authMiddleware)` at lab.js:19, then a 60/min per-IP limiter (lib/lab.js:23-32). Practical exploitation is bounded by the id: bot/api/lab.py:154 uses `uuid.uuid4().hex[:12]`, i.e. 48 bits of entropy, which is not enumerable at 60 req/min — so this needs the id to leak (shoulder-surfing, a shared link, a log) rather than being guessable.

**Existing-test check**: tests/test_lab_api.py exercises the endpoint (line 71 polls `/lab/status/{job_id}`, line 84 asserts a 404 for an unknown id) but asserts nothing about ownership — there is no owner to assert on. No file under app/test/ covers routes/lab.js. app/test/authed_queries_scope_to_session.test.js cannot see this: lab.js issues no SQL at all, it relays HTTP.

**Evidence**:

```
app/routes/lab.js:89-95 — the only check on the path is a format check; the caller's identity never enters the request:
```
89	router.get('/status/:id', async (req, res) => {
90	  if (!/^[a-f0-9]{6,32}$/.test(req.params.id)) {
91	    return res.status(400).json({ error: 'Bad job id' });
92	  }
93	  const r = await relay('GET', `/lab/status/${req.params.id}`);
94	  res.status(r.status).json(r.data);
95	});
```
Upstream, the job record carries no owner and the handler returns the submitter's parameters and results to whoever asks — bot/api/lab.py:215-228:
```
215	@lab_router.get("/lab/status/{job_id}")
216	async def lab_status(job_id: str):
217	    job = _jobs.get(job_id)
218	    if job is None:
219	        raise HTTPException(status_code=404, detail="Unknown job.")
220	    out = {"job_id": job_id, "status": job["status"],
221	           "params": job.get("params"),
222	           "elapsed_sec": round(time.time() - job["started_at"], 1)}
223	    if job["status"] == "done":
224	        out["result"] = job.get("result")
225	    elif job["status"] == "error":
226	        out["error"] = job.get("error")
227	        if job.get("log_tail"):
228	            out["log_tail"] = job["log_tail"]
```
The web layer relays the body verbatim (line 94), including `log_tail` on the error branch. Note also that POST /api/lab/run (lab.js:66-84) forwards no identity either, so the bot could not scope it even if it wanted to.
```

## W-13 — Backup set omits the Fernet master key AND the entire per-user exchange-credential store — an off-host restore silently yields undecryptable secrets and zero user API keys

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: credential-durability / CWE-522
- **File**: `bot/utils/backup.py:35-47`
- **Fix class**: REVIEW_REQUIRED

**Observed**: The archive carries the ciphertext and leaves the only key that opens it behind. Restoring onto a rebuilt host (`docs/DURABILITY.md`: "A backup on the same disk protects against bad deploys, not dead disks" — off-host restore is the stated purpose) produces a vault every entry of which fails to decrypt: `bot/core/secrets_vault.py:_load_vault` logs `"secrets vault: could not decrypt %s (stale master key?)"` per key and returns `{}`, so `seed_and_restore()` restores nothing and the bot boots with no BITGET_API_KEY/SECRET/PASSPHRASE — the exact wiped-credential state the vault exists to prevent. Separately, `data/exchange_creds.enc` is not archived at all, so every linked user's exchange keys and agent private keys are lost outright with no recovery path.

**Expected**: An archive of `secrets_vault.enc` + `runeclaw.db` restores to readable secrets, and the store holding every BYOK user's exchange api_key/api_secret/passphrase and Hyperliquid/Paradex agent private keys is itself backed up. `data/attestation_key.bin` is already in `_CRITICAL`, so shipping key material inside the archive is established practice here, not a new decision.

**Root cause**: `_CRITICAL` was written as a list of *state* files. The master key is treated as infrastructure rather than state, and `exchange_creds.enc` (added later, by bot/core/exchange_credentials.py) was never added to the list. `docs/DURABILITY.md`'s "What is irreplaceable" table repeats the same omission, so the runbook cannot catch it either, and its restore verification (step 4: check `/anchor` still VERIFIED) probes the attestation key — which IS in the archive — and nothing that requires the Fernet key.

**Standard**: CLAUDE.md: "Unreadable is never zero, and absent is never a measurement" — a backup that reports success while being unrestorable is the same defect one layer out. Also CWE-522 (insufficiently protected credentials) / recovery integrity.

**Remediation**: Add `data/exchange_creds.enc` to `_CRITICAL`, and add the master key — either `data/.exchange_secret.key` in `_CRITICAL` (accepting that the archive then holds key+ciphertext together, which it already does for attestation_key.bin) or, if key/ciphertext separation is wanted, have `create_backup()` refuse to archive `secrets_vault.enc`/`exchange_creds.enc` unless it can record that the key is externally managed, and say so in the manifest. Update the `docs/DURABILITY.md` irreplaceable table in the same commit, and add a restore-verification step that actually decrypts one vault entry. A test over `backup._CRITICAL` asserting both paths are present would pin it — `tests/test_backup_durability.py` currently asserts round-trip/rotation/throttle and never asserts the contents of the critical set.

**Reachability check**: `create_backup()` is reached two ways in production code: `bot/proofofpnl/scheduler.py:121-122` calls `maybe_daily_backup()` opportunistically, and `bot/skills/telegram_handler.py:4754` runs `bkp.create_backup` for the admin `/backup` command. The omitted files are populated in normal operation: `data/secrets_vault.enc` and `data/.exchange_secret.key` both exist on this box right now (data/, 0600), and `exchange_creds.enc` is written by `ExchangeCredentialStore._save` on every `/connect` (telegram_handler.py:6435 `store.set_venue(tg_id, venue, fields)`) and on every website credential pull (`bot/utils/credential_pull.py:126-128`) — neither of which is behind `PER_USER_LIVE_ENABLED`, which only gates trading, not storage.

**Existing-test check**: tests/test_backup_durability.py defines test_create_then_verify_roundtrip, test_tampered_archive_fails_verification, test_missing_manifest_is_honest, test_rotation_keeps_newest, test_daily_hook_throttles, test_restore_stays_manual_and_wiring_exists. `grep -n '_CRITICAL\|critical_paths\|exchange_creds\|exchange_secret'` over tests/test_backup_durability.py and tests/test_backup_follows_symlinks.py returns nothing — no test asserts what the critical set contains.

**Evidence**:

```
bot/utils/backup.py:35-47
_CRITICAL = [
    "logs/audit_chain.jsonl",
    "data/attestation_key.bin",
    "data/anchor_state.json",
    "data/proofofpnl_publication.json",
    "data/runeclaw.db",
    "data/secrets_vault.enc",
    "data/shadow_book.json",
    "data/proactive_watch.json",
    "data/venue_override.json",
    "data/catalog_seen.json",
]
_CRITICAL_GLOBS = ["data/learning/*", "data/portfolio_*", "data/risk_state_*"]

Neither `data/.exchange_secret.key` (bot/core/exchange_credentials.py:41 `_KEY_FILE`) nor `data/exchange_creds.enc` (line 40 `_CREDS_FILE`) appears in either list, and no glob matches them.
```

## W-14 — gitleaks path allowlist silently disables the Solana keypair rules under tests/ and app/test/, contradicting the config's own comment and the comment in tests/test_crypto_ciphertext_compat.py

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: secret-scanning-coverage / CWE-1059 (incomplete guard)
- **File**: `.gitleaks.toml:26-46`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: It is not caught. The global allowlist short-circuits the whole file before any rule runs, so tests/ and app/test/ are secret-scan blind spots for every rule in the config, including the two Solana rules written specifically because no generic rule matches a bare 64-integer array.

**Expected**: Per the comment at .gitleaks.toml:41-43 and the docstring at tests/test_crypto_ciphertext_compat.py:17-20 ("the Solana-specific rules are deliberately NOT path-allowlisted, so a real keypair committed here would still be caught"), a real Solana keypair committed under tests/ or app/test/ is caught by the PR-scope and full-history scans.

**Root cause**: gitleaks' top-level `[allowlist]` is global by construction — rule-scoping requires either a `[[rules.allowlist]]` block inside each rule or (8.19+) `[[allowlists]]` with `targetRules`. The config author intended a rule-scoped exemption and wrote a global one; the comment records the intent, and nothing tested the behaviour.

**Standard**: CLAUDE.md: "a gate whose coverage is overstated is the failure this file exists to prevent" — stated verbatim in this repo about the methods-reachability ratchet, and .gitleaks.toml:92-98 records the same lesson being learned once already, when a value-scoped `[[allowlists]]` block was mutation-tested and found to be broader than it read.

**Remediation**: Replace the global `paths` entries for `^tests/` and `^app/test/` with a rule-scoped allowlist on the noisy rule only — e.g. a `[[rules.allowlist]]` under a redefined `generic-api-key`, or an `[[allowlists]]` block carrying `targetRules = ["generic-api-key"]` — so the two `solana-*` rules keep scanning those directories. Then mutation-test it the way .gitleaks.toml:92-98 describes: write a real-shaped keypair under tests/ and confirm the scan goes red.

**Reachability check**: The config is the one gitleaks reads in CI: .github/workflows/ci.yml:452-457 passes `GITLEAKS_CONFIG: .gitleaks.toml` to gitleaks-action on pull_request, and the full-history step below it uses the same config with `--baseline-path`. Both scans inherit the blind spot. tests/ and app/test/ are ordinary committed directories a keypair fixture could plausibly land in — tests/test_crypto_ciphertext_compat.py already commits frozen key material there by design, which is precisely why the allowlist was added.

**Existing-test check**: Grepped tests/ and scripts/ for gitleaks: only tests/test_claude_md_accuracy.py:106 and tests/test_preflight_matches_ci.py:152-157 mention it, and both only assert that gitleaks is named as a job preflight cannot run locally. No test exercises the config's rule/path interaction.

**Evidence**:

```
.gitleaks.toml:26-46
[allowlist]
description = "Paths and shapes that legitimately contain key-shaped data"
...
paths = [
  '''\.env\.example$''',
  ...
  # Test fixtures are deliberately fake credentials. The Solana keypair rules
  # above are NOT path-allowlisted, so a real keypair committed under tests/
  # is still caught.
  '''^tests/''',
  '''^app/test/''',
]

This is a top-level `[allowlist]` table (not `[[rules.allowlist]]`), so gitleaks applies its `paths` to every finding from every rule — including `solana-keypair-json` (line 9-18), described one comment earlier as "the single highest-value pattern in the repo: that file holds mint, metadata, presale and LP authority plus the entire supply".
```

## W-15 — An LLM key the master key can no longer decrypt is returned as ciphertext and reported as a connected key — then double-encrypted on the next save

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: unreadable-rendered-as-a-value / CWE-522
- **File**: `bot/db/models.py:384-394`
- **Fix class**: REVIEW_REQUIRED

**Observed**: The raw Fernet ciphertext is handed back as the user's API key. Three consequences follow: (1) bot/web/user_gateway.py:2706-2710 does `key = (s.llm_api_key or '').strip(); if key: connected = True; fingerprint = _llm_fingerprint(key)` — so /gateway/llm reports `connected: true` with a fingerprint computed over ciphertext, a confident positive built from an unreadable value; (2) bot/core/analyzer.py:707-714 builds `LLMConfig(provider=provider, api_key=key)` from the same field, so the ciphertext is sent to a third-party provider (Anthropic/OpenAI/Groq) in an auth header; (3) the next `save_user_settings` re-encrypts the ciphertext, permanently burying the real key under two layers.

**Expected**: A stored value the current master key cannot open is UNREADABLE. It should surface as absent/error — `connected: false` with a distinct reason, or a refusal — not as a key. Fernet tokens are trivially distinguishable from legacy plaintext (version byte 0x80, so they always start `gAAAAA`), so the legacy-migration path can keep working while ciphertext-that-will-not-open is refused.

**Root cause**: One `except Exception` covers two distinguishable causes — legacy plaintext (a value that is not Fernet at all) and a valid Fernet token this key cannot open — and resolves both to the more optimistic one. The same module's `_encrypt_llm_key` is explicitly fail-CLOSED ("if encryption is unavailable, store nothing rather than leak plaintext"); the read path is fail-open.

**Standard**: CLAUDE.md: "Unreadable is never zero, and absent is never a measurement" and "A heuristic is never a verdict." app/lib/totp.js:88-95 already states and implements the correct handling for the identical situation in this repo.

**Remediation**: In `_decrypt_llm_key`, treat a value that parses as a Fernet token (base64url decoding to a leading 0x80 byte, i.e. the `gAAAAA` prefix) but fails to decrypt as UNREADABLE — return a sentinel the callers can distinguish from both "" and a key, log at error level, and have `handle_llm_status` report a third state (`connected: false, reason: 'key_unreadable'`) instead of `connected: true`. Keep the non-Fernet passthrough exactly as-is so legacy plaintext migration is unchanged. Then extend tests/test_llm_key_encryption.py (which currently pins only `test_legacy_plaintext_passthrough`) with a rotated-key case.

**Reachability check**: Master-key rotation is a supported, documented operation and is reached by ordinary operator action: bot/core/exchange_credentials.py:91-95 explicitly OVERWRITES `data/.exchange_secret.key` whenever `RUNECLAW_SECRETS_KEY` is set to a value that differs from the file, so changing that env var orphans every existing ciphertext while leaving the DB rows intact. The same state arises from restoring a backup (finding #1: the archive carries runeclaw.db but not the key file) onto a host that then auto-generates a fresh key at exchange_credentials.py:110-136. The status surface (`handle_llm_status`) is reachable regardless of `per_user_llm_enabled`; only the analyzer consumption at analyzer.py:707 is behind that flag.

**Existing-test check**: tests/test_llm_key_encryption.py covers round-trip, empty, ciphertext-is-not-plaintext, legacy plaintext passthrough, save/get round trip and the stored column being ciphertext. It has no rotated-key / undecryptable-ciphertext case — the passthrough is asserted, its failure mode is not.

**Evidence**:

```
bot/db/models.py:384-394
def _decrypt_llm_key(stored: str) -> str:
    """Decrypt a stored LLM key. A value that isn't valid Fernet ciphertext is
    assumed to be a legacy plaintext key and returned as-is (it will be
    re-encrypted on the next save)."""
    if not stored:
        return ""
    try:
        return _llm_cipher().decrypt(stored.encode()).decode()
    except Exception:
        # Legacy plaintext (pre-encryption) or unreadable — pass through.
        return stored

The comment names both cases and then treats them identically. Compare app/lib/totp.js:88-95, which faces the same choice and refuses it: "NULL, NOT THE CIPHERTEXT. A wrong or rotated key must not fall through to 'try the envelope as a secret'".
```

## W-16 — The master-key warning tells the operator that pinning RUNECLAW_SECRETS_KEY keeps the key outside the data dir, while the code writes it into the data dir on that exact path

- **Severity (claimed)**: LOW · **Confidence (claimed)**: CONFIRMED
- **Category**: misleading-security-guidance / CWE-522
- **File**: `bot/core/exchange_credentials.py:86-102 and 128-135`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: The one message an operator reads at the moment they are deciding how to manage the key states the opposite of what the code does. Following it produces exactly the posture it claims to prevent.

**Expected**: Either the env-var mode genuinely keeps the key outside the persisted data dir (as the warning promises), or the warning says plainly that the key is mirrored into `data/` in every configuration so the operator can decide with accurate information. docs/SECRETS_VAULT.md:43 and bot/core/secrets_vault.py:22 both state the mirroring correctly; the log line the operator actually sees does not.

**Root cause**: The mirroring write (added so a wiped .env does not orphan ciphertext — a real and correct goal) was introduced without updating the older warning text that predates it.

**Standard**: CLAUDE.md's central rule about surfaces that make a claim the code does not support; F-15 adjacency. At-rest encryption is meant to protect ciphertext when a volume, backup or mount leaks and the key does not; here one read of `data/` yields both halves.

**Remediation**: Rewrite the warning at lines 128-135 to say the key is persisted to the data dir in every configuration and that RUNECLAW_SECRETS_KEY buys reproducibility across a wiped data dir, not key/ciphertext separation. If separation is actually wanted, gate the mirroring write behind an explicit opt-out (e.g. RUNECLAW_SECRETS_KEY_MIRROR=0) and document the trade-off it makes with the wiped-.env self-heal.

**Reachability check**: `_load_or_create_master_key` is the single key loader for all three at-rest stores: bot/core/secrets_vault.py:_cipher imports it, ExchangeCredentialStore._cipher calls it, and bot/db/models.py:_llm_cipher calls it. The warning branch fires on any boot where RUNECLAW_SECRETS_KEY is unset; the mirroring branch fires on any boot where it is set. Both are ordinary startup paths.

**Existing-test check**: tests/test_exchange_credentials.py and tests/test_secrets_vault.py exercise the env-key and file-key precedence and the wipe-survival property (tests/test_secrets_vault.py:94-102 deliberately deletes RUNECLAW_SECRETS_KEY and asserts recovery), which is what the mirroring write exists to make true. No test asserts anything about the warning text.

**Evidence**:

```
bot/core/exchange_credentials.py:91-95 (env-key branch)
        try:
            p = Path(key_file)
            if not p.exists() or p.read_bytes().strip() != env_key.encode():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(env_key.encode())

bot/core/exchange_credentials.py:128-133 (the warning the operator reads)
    log.warning(
        "RUNECLAW_SECRETS_KEY is not set — generated a new exchange-encryption "
        "key and persisted it to %s (0600), fingerprint %s. For production, set "
        "RUNECLAW_SECRETS_KEY explicitly so the key is managed outside the data "
        "dir and survives it being wiped. ..."

_KEY_FILE (line 41) is `os.path.join(_STATE_DIR, '.exchange_secret.key')` and _CREDS_FILE (line 40) is `os.path.join(_STATE_DIR, 'exchange_creds.enc')` — both under `data/`. So the advertised remedy writes the key next to the ciphertext it protects.
```

## W-17 — /connect and /setexchange echo a raw ccxt exception to the user with html.escape only, bypassing the repo's own _safe_exc_text scrubber — and the source-scan guard cannot see it

- **Severity (claimed)**: LOW · **Confidence (claimed)**: HIGH
- **Category**: sensitive-data-in-error-message / CWE-209
- **File**: `bot/skills/telegram_handler.py:6412-6416 and 6511-6513`
- **Fix class**: REVIEW_REQUIRED

**Observed**: These two handlers — the two that exist specifically to handle API keys — use `html.escape` alone. They are the highest-context sites in the file for an exception string containing credential material, and they are the ones that skipped the scrubber.

**Expected**: The repo defines exactly one sanctioned path for putting exception text in a user-facing reply: `_safe_exc_text` (telegram_handler.py:182), whose docstring says "A ccxt error carries the request URL" and which redacts key=value credentials, bot-token shapes and URL query strings BEFORE escaping. tests/test_exception_leak_guard.py is built entirely around the rule that `html.escape` is not a secret filter.

**Root cause**: The scrubber's enforcement is a source scan over the same file: `re.findall(r"_(?:send|reply)\([^\n]*?(?:html\.escape\()?str\(exc", src)`. It matches only handlers that render `str(exc)` inline on the `_send(` line. Here the exception text is laundered through a return value into a local named `detail`, in a different module, so the regex cannot reach it. This is the failure mode CLAUDE.md names directly: "The grep tells you where you looked; the test tells you where you didn't."

**Standard**: CLAUDE.md F-15 ("Never put secrets, API keys, private keys or internal config into user-facing text") and the explicit rule in tests/test_exception_leak_guard.py: "Escaping is the wrong control for the wrong threat."

**Remediation**: Sanitize at the boundary rather than at each call site — have the venue probes in bot/core/exchange_credentials.py pass their `str(exc)` through a shared redactor before returning it, or wrap `detail` in `_safe_exc_text`-equivalent scrubbing at telegram_handler.py:6414 and :6513. Then re-run the search the way CLAUDE.md prescribes: extend the guard in tests/test_exception_leak_guard.py to also flag `html.escape(<var>)` inside a `_send` where `<var>` is bound from a `(ok, detail)` tuple, so laundering through a variable stops hiding the site.

**Reachability check**: /connect is reachable by any user holding the `status` permission (`await self._guard(update, 'status')` at telegram_handler.py:6358), and the failure branch fires on every mistyped or wrong-environment key — a common, expected path, not an edge case. /setexchange is admin-only. Both branches run before anything is stored, so they are hit precisely when a key is being typed. Note the honest limitation: for the venues in `_VENUE_FIELDS`, ccxt places the API key in a request HEADER rather than the query string, so the api_key itself is not expected in these particular URLs — what is echoed is the venue URL, signature/timestamp query parameters, and the raw venue response body. That is why this is rated LOW rather than higher; the confirmed defect is the bypass of the mandated scrubber on the two credential-handling commands, not a demonstrated key disclosure.

**Existing-test check**: tests/test_exception_leak_guard.py::TestNoHandlerBypassesIt::test_no_user_reply_renders_a_raw_exception is the guard, and its regex (quoted above) does not match either site — I read the full test file; there is no other test covering the /connect or /setexchange error string.

**Evidence**:

```
bot/skills/telegram_handler.py:6410-6416 (/connect)
        ok, detail = await validate_venue_credentials(
            venue, fields, sandbox=CONFIG.exchange.sandbox)
        if not ok:
            await self._send(update,
                f"🔴 Could not authenticate with {label}. Nothing was stored.\n"
                f"<code>{html.escape(detail)}</code>\n\n"
                "Check the credentials and their trading permissions.")

bot/skills/telegram_handler.py:6511-6513 (/setexchange)
            await self._send(update,
                "🔴 Could not authenticate with Bitget. Nothing was changed.\n"
                f"<code>{html.escape(detail)}</code>")

`detail` is raw exception text: every probe in bot/core/exchange_credentials.py returns it from `except Exception as exc: return False, str(exc)[:200]` (lines ~262, ~683, ~721, ~760, ~798).
```

## W-18 — /news, /funding, /duel and /approvals are registered with no authorization gate; /news discloses the operator's open-position symbols to any Telegram user

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: broken-access-control
- **File**: `bot/skills/telegram_handler.py:9331-9338 (handler), 9315-9328 (digest), 9269-9292 (_held_symbols)`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: _cmd_news, _cmd_funding, _cmd_duel and _cmd_approvals run for any Telegram user with no allowlist check and no rate limit. /news additionally emits the operator's held-position symbols whenever a fresh high-impact headline names one of them.

**Expected**: Every command handler passes the F-2 allowlist gate documented in _is_allowlisted (bot/skills/telegram_handler.py:3017-3046) and the per-user rate limiter, exactly as the ~130 @guard(...)-decorated handlers do. A non-admitted stranger should receive the access_denied_notice, not operator position data.

**Root cause**: The allowlist gate is applied per-handler via the @guard decorator or an inline self._guard call rather than by a global pre-handler, so adding a command defaults to UNGATED and four commands were added without one. tests/test_command_audience_matches_permission.py only checks catalog-'admin' commands against ROLE_PERMISSIONS; a catalog-'user' command with no permission string at all passes both of its assertions vacuously (verified: its 5 tests pass on the current tree).

**Standard**: CLAUDE.md 'Public-surface rules' — private per-user state must not reach an unauthenticated surface; and the F-2 lockdown documented in _is_allowlisted, whose stated purpose is that only the env allowlist or an admin's /approve may reach the bot.

**Remediation**: Add @guard("macro") to _cmd_news and @guard("scan")/@guard("status") to _cmd_funding, _cmd_duel and _cmd_approvals (all four permissions are already held by trader/paper/viewer, so no role loses a documented command). Then add a test that walks the (cmd, handler) registration list in build_app and asserts every handler either carries a guard decorator, calls self._guard inline, or is on an explicit ALLOWED_UNGATED tuple ({start, help, version} today) — the same ratchet shape the repo already uses for command_audience_backlog.json.

**Reachability check**: Reachable. The (cmd, handler) tuple list in build_app registers ('news', self._cmd_news), ('funding', self._cmd_funding), ('duel', self._cmd_duel) and ('approvals', self._cmd_approvals) via app.add_handler(CommandHandler(cmd, handler)) with no filters argument, so every private-chat command message reaches them. No upstream gate exists: app.add_error_handler is the only other global hook and there is no group -1 handler. The bot runs polling only (bot/main.py:465, app.updater.start_polling), so all updates from all users are fetched.

**Existing-test check**: grep of tests/ and app/test/ for _cmd_news, allowlist and guard: tests/test_news_radar_honesty.py, test_news_radar.py, test_news_standdown_alert.py and test_news_web_gateway.py all exercise the radar/rendering, none touches authorization. tests/test_command_audience_matches_permission.py (run: 5 passed) only compares catalog audience to ROLE_PERMISSIONS and cannot see a handler with no permission string. Nothing pins the allowlist on these four commands.

**Evidence**:

```
bot/skills/telegram_handler.py:9331-9338 — no decorator, no _guard, no _is_allowlisted, no _check_auth, no rate limit:

    async def _cmd_news(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """NEWS-1b: /news — public-RSS headline radar with high-impact alerts on
        the positions you hold. Advisory only; never moves or blocks a trade."""
        try:
            await update.effective_chat.send_chat_action(ChatAction.TYPING)
        except Exception:
            pass
        await self._send(update, await self._news_digest_text())

what it renders, bot/skills/telegram_handler.py:9315-9328:

        held = self._held_symbols()
        watch = held or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        ...
        return render_news_digest(
            radar.recent(8), radar.standdown(held, now) if held else [], now,

with bot/skills/telegram_handler.py:9269-9271:

    def _held_symbols(self) -> list:
        """Base symbols the operator currently holds (paper + live), de-duped.
        Best-effort — a source that isn't present is simply skipped."""

and bot/core/news.py:255-259, which prints the symbol:

        lines.append("\n⚠️ <b>On your positions:</b>")
        for r in standdown_recs[:5]:
            ...
            f"🔴 <b>{_esc(r['symbol'])}</b> — {_esc(r['headline'])[:120]}"

Three sibling commands are registered the same way with no gate: _cmd_funding (bot/skills/telegram_handler.py:5442), _cmd_duel (5217), _cmd_approvals (7285).
```

## W-19 — Callback ownership tag is caller-controlled, so the confirm/reject/setlimit IDOR guard authorizes on a value the attacker supplies

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: HIGH
- **Category**: broken-access-control
- **File**: `bot/skills/telegram_handler.py:2984-2996 (_uid_matches), 14064-14066 (confirm), 14197-14199 (reject), 14005-14007 (setlimit), 13676-13678 (pos_close)`
- **Fix class**: REVIEW_REQUIRED

**Observed**: The guard compares the caller to a string the caller transmitted. Any payload of the form '<action>:<victim_id>:<attacker_uid>' satisfies it. The check only blocks the case of an honest client replaying an unmodified button belonging to someone else.

**Expected**: The owner of a pending idea is a server-side fact. The guard should compare the caller against an owner recorded next to the idea (e.g. engine._pending_ideas[trade_id].owner_uid, or a signed callback token), so no value the client sends can satisfy it.

**Root cause**: Owner identity is carried in the callback_data round-trip instead of being stored server-side beside the pending idea. The comments at 14060-14063 and 13673-13676 correctly identify crafted/replayed callbacks as the threat model, but the mitigation chosen — a self-describing tag — is not integrity-protected.

**Standard**: The repo's own RC-AUD-004 fail-closed rule quoted at bot/skills/telegram_handler.py:14060-14063, and CLAUDE.md's rule that a guard must actually be reached and actually decide.

**Remediation**: Record the owner on the idea at creation time (register_manual_idea and the scan/analyze paths already build the TradeIdea) and change the four call sites to compare self._get_tg_id(update) against engine._pending_ideas[trade_id].owner_uid, denying when the idea carries no owner. Keep the payload tag as a cheap first filter if desired, but do not let it be the authority. tests/test_per_user_position_isolation.py is the natural home for the regression test.

**Reachability check**: All four call sites are live in _handle_callback, registered at bot/skills/telegram_handler.py:1043 via app.add_handler(CallbackQueryHandler(self._handle_callback)). pos_close_ is partially mitigated: line 13694 routes the close through self._caller_executor(update), which returns None for a non-owner when PER_USER_LIVE_ENABLED is on (3223-3243), and the paper branch reads self.engine.user_portfolios.get(user_id) keyed by the CALLER. confirm:, reject: and setlimit: have no such second layer — engine._pending_ideas is a single global dict with no owner field.

**Existing-test check**: grep of tests/ and app/test/ for _uid_matches, setlimit, callback_idor and _confirmed_ids returns only tests/test_pos_details_stale_sync.py and tests/test_per_user_position_isolation.py, and neither references _uid_matches or the owner tag — they cover executor routing for pos_details/pos_close. No test exercises the confirm/reject/setlimit ownership guard.

**Evidence**:

```
The predicate, bot/skills/telegram_handler.py:2984-2996:

    @staticmethod
    def _uid_matches(caller_uid: str | None, expected_uid: str | None) -> bool:
        ...
        if not expected_uid:
            return True
        if not caller_uid:
            return False
        return caller_uid in {s.strip() for s in expected_uid.split(",") if s.strip()}

and its use, bot/skills/telegram_handler.py:14064-14066 — expected_uid is read out of the very callback payload the client sent:

            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not expected_uid or not self._uid_matches(caller_uid, expected_uid):

The engine-side lookup that follows performs no ownership check of its own, bot/core/engine.py:5631-5633:

        idea = self._pending_ideas.get(trade_id, None)
        if idea is None:
            return "Trade not found or expired."
```

## W-20 — setlimit: callback is fail-OPEN on a missing owner tag, letting any authorized caller rewrite the entry price of another user's pending trade and execute it

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: HIGH
- **Category**: broken-access-control
- **File**: `bot/skills/telegram_handler.py:14002-14010`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: An absent owner tag is treated as 'allow all' (the documented behaviour of _uid_matches: 'Returns True if ... expected_uid is empty/None (allow all)'), so the guard is a no-op for exactly the payload it was written to catch.

**Expected**: Identical fail-closed treatment to confirm:/reject: — a payload with no owner tag is a crafted callback and must be denied, since all four legitimate button construction sites always emit the uid.

**Root cause**: _uid_matches was designed with an allow-all-on-empty semantic for the multi-id auto-scan chat_id case, and setlimit: calls it through an `if expected_uid and ...` conditional that inherits that semantic instead of overriding it. confirm: and reject: were retrofitted with the extra `not expected_uid or` clause; setlimit: was missed.

**Standard**: The repo's own RC-AUD-004 fail-closed rule, stated verbatim in the comment three branches below at bot/skills/telegram_handler.py:14060-14063.

**Remediation**: Change line 14007 to `if not expected_uid or not self._uid_matches(caller_uid, expected_uid):`, matching lines 14066 and 14199. All four button construction sites (bot/skills/telegram_handler.py:2797, 8440, 9578, 11483) already emit `setlimit:{idea.id}:{uid}`, so no legitimate button breaks. Add the owner-on-the-idea check from the related finding for defence in depth.

**Reachability check**: Live branch in _handle_callback (registered at bot/skills/telegram_handler.py:1043). Verified that every legitimate producer emits the tag — grep 'setlimit:' across the repo returns exactly the four construction sites listed above plus this handler — so the untagged form is reachable only as a crafted payload, which is the case the guard claims to cover. self._pending_limit_input is keyed by caller_uid so no upstream invariant re-checks ownership before confirm_trade runs; engine.confirm_trade (bot/core/engine.py:5631) does no ownership check either.

**Existing-test check**: No test in tests/ or app/test/ references 'setlimit' (grep returns zero hits outside the handler source). tests/test_per_user_position_isolation.py covers executor routing only.

**Evidence**:

```
bot/skills/telegram_handler.py:14002-14010 — the guard runs only `if expected_uid`, so an absent tag is treated as permission:

        if data.startswith("setlimit:"):
            parts = data.split(":")
            trade_id = parts[1]
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if expected_uid and not self._uid_matches(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>", edit=True)
                return

The two sibling handlers are explicitly fail-CLOSED for the same shape, bot/skills/telegram_handler.py:14060-14066:

            # RC-AUD-004: fail-closed. Every legitimate confirm button is built as
            # "confirm:<id>:<uid>" (see button construction sites), so a missing
            # owner tag means a crafted/replayed callback — deny rather than allow.
            expected_uid = parts[2] if len(parts) > 2 else None
            ...
            if not expected_uid or not self._uid_matches(caller_uid, expected_uid):

and what the un-owned caller then gets to do, bot/skills/telegram_handler.py:2585-2588:

                old_price = idea.entry_price
                idea.entry_price = custom_price
                # Force limit order type
                idea.order_type = "limit"
```

## W-21 — The confirm/reject double-tap guard marks a trade consumed before the ownership check and never clears it on failure, so a denied or failed confirm answers the real owner with 'Already confirmed'

- **Severity (claimed)**: MEDIUM · **Confidence (claimed)**: CONFIRMED
- **Category**: correctness
- **File**: `bot/skills/telegram_handler.py:14046-14066, 14088-14096, 14183-14199`
- **Fix class**: SAFE_AUTO_FIX

**Observed**: A denied callback and a failed execution both leave the trade permanently marked consumed. The subsequent legitimate tap is answered with the words 'Already confirmed' — a claim that a confirmation occurred, made at the moment none did.

**Expected**: Per CLAUDE.md ('a failed read must not render as ... a confident negative'), the dedup marker should be set only after the caller is authorized and only after the execution attempt is known to have happened; a failed attempt must leave the button live and say so.

**Root cause**: The dedup marker is a side effect placed at the top of the branch, ahead of both the authorization check and the execution attempt, with no compensating discard on any failure path. A secondary defect sits in the same block: the cap at 14057-14058 trims with `set(list(self._confirmed_ids)[-50:])`, and set iteration order is unspecified, so the 50 ids kept are arbitrary rather than the most recent.

**Standard**: CLAUDE.md — 'Unreadable is never zero, and absent is never a measurement'; a failed action must not be rendered as the completed action.

**Remediation**: Move `self._confirmed_ids.add(trade_id)` below the ownership check (past line 14066 / 14199) and discard it again on every failure return: the except branch at 14090-14096 and the `is_failure` branch. Replace the set with a bounded ordered structure (OrderedDict or deque) so the cap evicts oldest-first. Then assert the property directly: drive confirm_trade to raise, tap twice, assert the second tap still attempts execution.

**Reachability check**: Live branches in _handle_callback (registered bot/skills/telegram_handler.py:1043). The failure path is reached by any exception out of engine.confirm_trade — the handler already catches and reports it, so it is an expected outcome, not a hypothetical. The denial path is reached by the ownership check that sits below the add. No upstream guard clears _confirmed_ids; grep across the repo shows the only writes are the two adds and the trim in this function.

**Existing-test check**: grep of tests/ and app/test/ for _confirmed_ids returns no test files. tests/test_pos_details_stale_sync.py and tests/test_per_user_position_isolation.py are the only callback-related suites and neither touches the dedup set.

**Evidence**:

```
bot/skills/telegram_handler.py:14046-14066 — the id is added at 14055, eleven lines before the ownership check at 14066:

            # Double-tap guard: skip if this trade was already confirmed
            if not hasattr(self, '_confirmed_ids'):
                self._confirmed_ids: set[str] = set()
            if trade_id in self._confirmed_ids:
                try:
                    await query.answer("Already confirmed")
                except Exception:
                    pass
                return
            self._confirmed_ids.add(trade_id)
            ...
            if not expected_uid or not self._uid_matches(caller_uid, expected_uid):

and nothing discards the id when execution fails, bot/skills/telegram_handler.py:14088-14096:

            try:
                result = await self.engine.confirm_trade(trade_id, user_id=caller_uid or "")
            except Exception as exc:
                audit(system_log, f"confirm_trade raised: {exc}",
                      action="confirm_trade", result="ERROR")
                await self._send(update,
                    f"❌ <b>Trade execution failed:</b> {_safe_exc_text(exc)}",
                    edit=True)
                return

The reject: branch repeats the identical ordering at bot/skills/telegram_handler.py:14183-14199 (add at 14192, ownership check at 14199).
```

## W-22 — The /risk panel's 'Safe Mode' button changes no state but replies 'Safe mode is on' and records an audit entry with result=OK

- **Severity (claimed)**: HIGH · **Confidence (claimed)**: CONFIRMED
- **Category**: false-safety-claim
- **File**: `bot/skills/telegram_handler.py:13157-13163`
- **Fix class**: REVIEW_REQUIRED

**Observed**: A pure no-op that reports success in the first person and leaves an audit record asserting the control was activated. The next /risk card shows unchanged caps and the engine keeps taking the same setups.

**Expected**: Either the button applies a real tightening (a confidence floor, a size cap, a scoped risk-engine change) and says what it applied to whose account, or it is removed. A control on the risk panel must not announce an effect it did not have.

**Root cause**: The branch was written as a UI stub next to two branches that were implemented, and the audit() call gives it the same evidentiary weight as the real ones — so neither the chat reply nor the audit log distinguishes it from a control that worked.

**Standard**: CLAUDE.md — 'Colour is a claim' / a control must not assert an outcome it did not produce; and the file's own rule that a card which announces itself and then does nothing is the third failure mode its guard/omit table warns about.

**Remediation**: Remove the button from the /risk keyboard (bot/skills/telegram_handler.py:10164) and delete the branch, or implement it against a real knob — e.g. raise the caller's confidence floor through the same _control_scope(update) path /pause uses, so the reply can name the scope ('your engine' vs 'the shared engine') the way wr_pause(scope=...) already does. Pin it with a planted-state test in tests/test_surface_scenarios.py: activate safe mode, then assert the engine's confidence floor actually moved.

**Reachability check**: Reachable by any caller holding the 'halt' permission — the destructive-callback map at bot/skills/telegram_handler.py:12890-12891 gates 'risk_safe_mode' on 'halt', which the trader role holds (bot/utils/user_store.py ROLE_PERMISSIONS['trader']). The keyboard is attached to every /risk card, and /risk is @guard("risk"), held by trader, paper and viewer. Confirmed there is no second, working implementation: bot/warroom/warroom_bot.py:710-711 returns only a text dict and mutates nothing either.

**Existing-test check**: grep of tests/ for safe_mode returns only tests/test_i18n_risk_controls.py, which asserts the button's LABEL is translated ('btn_safe_mode') and says nothing about behaviour. No test asserts any state change.

**Evidence**:

```
bot/skills/telegram_handler.py:13157-13163 — the entire handler; no engine, risk, RUNTIME or config value is written:

        if data == "risk_safe_mode":
            await self._send(update,
                "Safe mode is on.\n\n"
                "I'll only take high-confidence setups from here.",
                edit=True)
            audit(system_log, "Safe mode activated", action="safe_mode", result="OK")
            return

The button that reaches it, bot/skills/telegram_handler.py:10163-10166, sits between the two controls that do work:

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_safe_mode", lang), callback_data="risk_safe_mode"),
             InlineKeyboardButton(t("btn_pause", lang), callback_data="risk_pause")],
            [InlineKeyboardButton(t("btn_stop_bot", lang), callback_data="risk_emergency_stop")],

Compare the neighbouring branch, which does mutate state, bot/skills/telegram_handler.py:13165-13172:

        if data == "risk_pause":
            ...
            _risk, _scope = self._control_scope(update)
            if _risk is None:
                await self._refuse_shared_control(update, "pause")
                return
            _risk.emergency_halt("pause_risk_panel")
```

---

Total: 22 raw findings across 4 of 25 dimensions.


========================================================================

# Money-path batch — ai-to-money, order-exec, risk-engine, market-data

**27 raw · 25 CONFIRMED · 0 SUSPECTED · 2 REFUTED**, each judged by two
independent adversarial verifiers defaulting to refute.


## M-01 [HIGH] Shared pending-idea book: a paper-only user's proposed trade is auto-confirmed LIVE on the operator's account as user_id="auto", bypassing every per-user gate

- **Dimension**: ai-to-money · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/engine.py:4330-4334 (auto-confirm selection); 5702 (strategy gate skipped for "auto"); 6289 (manual margin applied); bot/skills/manual_trade.py:101,112`
- **Standard**: CLAUDE.md: "A GATE WITH FIVE CONDITIONS AND SIX RENDERINGS OF IT WILL DISAGREE WITH ITSELF" — here the confirm-time gates that isolate a user's trade are all keyed on user_id, and the auto path erases the user_id. Also the repo's own custody discipline (bot/web/web_live_gate.py header): "no live web order exists outside" an enforce-mode Authority Envelope.

**Observed**: The idea is confirmed as `user_id="auto"`, which the confirm path treats as the operator: the per-user strategy gate is skipped (5702), `per_user_live_eligibility("auto")` returns `(True, "operator/auto path")` (2272-2273), `_per_user_margin_cap("auto")` returns None (924), and `_executor_for("auto")` returns the shared operator LiveExecutor (1772). The Lock-5 human-approval token is auto-minted because `auto_confirm_live_enabled` is True (6058). The user's own `margin` value becomes the position margin at 6289-6294. Simultaneously bypassed: the gateway's proposer-isolation check (`user_gateway.py:1395-1396` only runs inside handle_trade_confirm), the fail-closed web-live gate + Authority Envelope (`user_gateway.py:1408-1424`), and the 2FA step-up in `app/routes/webtrade.js:96-118`.

**Expected**: An idea proposed by a non-operator, paper-only identity must never reach the live executor. Either the auto-confirm loop restricts itself to ideas the engine's own scan produced, or the confirm inherits the proposer's identity so the per-user gates run.

**Root cause**: `_pending_ideas` is one engine-wide dict written by five different producers (analyzer tick, force_scan, manual_trade, scan_skill, skill_registry) and read by an auto-confirm loop that has no notion of provenance. The repo already noticed half of this — bot/utils/user_store.py:50-53 records "the idea book is already shared by everyone who can scan" — but reasoned only about who may WRITE to it, never about the autonomous consumer that turns entries into real orders on a different account.

**Business impact**: Real money on the operator's exchange account is committed to a symbol, direction, entry/SL/TP and margin chosen by an unvetted, self-admitted account that the product explicitly classifies as paper-only. The operator's audit row for the order reads `user_id: "auto"`, so nothing in the trail attributes the position to the person who authored it.

**Reachability**: Confirmed reachable. `/api/trade/propose` -> `handle_trade_propose` -> `_propose_from_text` requires only `_guard_user(..., command="trade")`; verified at runtime that the auto-provisioned `paper` role holds `trade`. Telegram `/trade` reaches the same helper at telegram_handler.py:10083 behind `self._guard(update, "trade")`. No upstream guard filters `_pending_ideas` by origin: I read the full body of both auto-confirm loops (engine.py:4326-4380 and 6581-6600) and the whole of `_confirm_trade_inner` (5687-6420). The only limiter is the tick's early return at engine.py:4109 when the book is already non-empty, which makes this a race rather than a deterministic call — stated explicitly in the reproduction.

**Existing tests**: No test pins idea provenance. `grep -rn auto_confirm tests/` returns 7 files; tests/test_calibrated_autotrade.py covers only the calibrated gate value, tests/test_audit_v5_fixes.py:33-39 only asserts the defaults, tests/test_audit_v7_fixes.py:337 only asserts the Lock-5 mint expression is present. tests/test_web_gateway.py:643 pins the OPPOSITE direction (a web user must not confirm an engine-generated idea) — the reverse direction, engine auto-confirming a user's idea, is untested.

**Remediation**: Stamp provenance on registration and filter it in both auto-confirm loops. `register_manual_idea` (and the analyze_asset/scan_skill writers) already have the caller's id at hand — record `idea._proposer = user_id`. Then in engine.py:4331-4334 and 6583-6584 select only ideas with no proposer (engine-generated) — an idea a human proposed must be confirmed by that human, through confirm_trade(user_id=<them>), so the strategy gate, eligibility gate, per-user cap, envelope and 2FA all run. This is a few lines in three files and churns no ratchet baseline.

**Evidence**:

```
bot/skills/manual_trade.py:96-116 — every manual proposal is stamped max confidence and written straight into the ENGINE-GLOBAL pending book:
```
        confidence=1.0,
        reasoning="Manual trade placed by user",
        signals_used=["manual"],
        source="manual",
...
def register_manual_idea(engine, idea, margin_usd: Optional[float] = None) -> None:
    engine._pending_ideas[idea.id] = idea
    if margin_usd and margin_usd > 0:
        ...
        engine._manual_margin_override[idea.id] = margin_usd
```
bot/core/engine.py:4330-4334 — the autonomous tick's auto-confirm selection reads that same dict with NO filter on who created the idea or what its `source` is:
```
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_ideas = [
            (tid, tidea) for tid, tidea in list(self._pending_ideas.items())
            if self._auto_confirm_gate_value(tidea) >= auto_threshold
        ]
```
bot/core/engine.py:4364 — `result = await self.confirm_trade(tid, user_id="auto")`
bot/core/engine.py:5702 — the per-user strategy veto is skipped for that caller: `if user_id and user_id != "auto":`
bot/core/engine.py:1770-1772 (`_executor_for`) — `if not user_id or user_id in ("auto", ""): return self.live_executor` (the OPERATOR's account)
bot/core/engine.py:922-924 (`_per_user_margin_cap`) — `if not user_id or user_id in ("auto", "") or self._is_operator_user(user_id): return None`
```

## M-02 [MEDIUM] force_scan's auto-confirm loop omits the AUTO_CONFIRM_LIVE_ENABLED suppression the tick applies, and is reachable by the lowest-privileged role

- **Dimension**: ai-to-money · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/engine.py:6581-6600`
- **Standard**: CLAUDE.md: "A GATE WITH FIVE CONDITIONS AND SIX RENDERINGS OF IT WILL DISAGREE WITH ITSELF. THE FIX FOR A CLAIM MADE IN SIX PLACES IS ONE PLACE."

**Observed**: The force_scan path runs the entire confirm pipeline (risk recheck, critique, drift/SL revalidation) and is only stopped at the very end by compliance Lock 5 — `_authorize_live` at bot/compliance/compliance_engine.py:313-329 fails closed when no approval token was minted, and engine.py:6058 only mints for `human or CONFIG.auto_confirm_live_enabled`. So no order is placed, but the operator's switch is enforced by a different layer with a different audit signature (AUTH_DENIED rather than SUPPRESSED_LIVE), and the suppression that is supposed to be the first line of defence simply is not there. With the shipped default (AUTO_CONFIRM_LIVE_ENABLED=1) the divergence is invisible and the same command hands any `scan`-permission user on-demand control over WHEN the operator's account opens real positions, plus the ability to wipe the operator's pending-confirm queue (`self._pending_ideas.clear()` at engine.py:6537).

**Expected**: Both auto-confirm sites obey the same operator switch, and the operator sees one consistent SUPPRESSED_LIVE trail.

**Root cause**: The auto-confirm decision is written out twice, and only one copy was updated when the RC-AUD-002 live suppression was added.

**Business impact**: An operator who deliberately turned off unattended live execution has one of two code paths still driving the full confirm pipeline, and gets a different audit signature than the one the flag's documentation describes. On the default configuration, the lowest-privileged role can time the operator's real-money entries and clear the operator's confirm queue.

**Reachability**: Reachable: `force_scan` has exactly two callers (telegram_handler.py:5782 under @guard("admin") /forcescan, and telegram_handler.py:11440 under @guard("scan") _cmd_latest_signal). Verified at runtime that ROLE_PERMISSIONS["viewer"] contains "scan". The Lock-5 backstop is real and I verified it by reading `_authorize_live` in full — that is why this is MEDIUM and not HIGH.

**Existing tests**: tests/test_audit_v7_fixes.py:337 asserts only that `"if human or CONFIG.auto_confirm_live_enabled:"` appears in engine source — a source scan that passes regardless of which auto-confirm loop is missing the suppression. No test exercises force_scan with auto_confirm_live_enabled off.

**Remediation**: Extract the selection+suppression into one helper (e.g. `_auto_confirmable_ideas()`), returning [] with the SUPPRESSED_LIVE audit when `CONFIG.is_live() and not CONFIG.auto_confirm_live_enabled`, and call it from both engine.py:4331 and engine.py:6583. Separately, reconsider whether `_cmd_latest_signal` should be able to drive a scan that auto-executes — routing it to a read-only refresh would be the tighter fix.

**Evidence**:

```
bot/core/engine.py:4330-4344 — the autonomous tick refuses to auto-confirm in live mode unless the operator opted in, and says so in the audit trail:
```
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_ideas = [...]
        if auto_ideas and CONFIG.is_live() and not CONFIG.auto_confirm_live_enabled:
            for tid, tidea in auto_ideas:
                audit(trade_log, f"Auto-confirm SUPPRESSED in live mode ...",
                      action="auto_confirm", result="SUPPRESSED_LIVE", ...)
            auto_ideas = []
```
bot/core/engine.py:6580-6594 — the force_scan copy of the same loop has no such branch:
```
        from bot.config import RUNTIME
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_confirmed = 0
        for tid, tidea in list(self._pending_ideas.items()):
            if self._auto_confirm_gate_value(tidea) >= auto_threshold:
                _et_ok, _et_why = self._pending_timing.get(tid, (True, ""))
                ...
                    result = await self.confirm_trade(tid, user_id="auto")
```
bot/skills/telegram_handler.py:11372-11373 — the caller that reaches it is guarded on the weakest permission in the table:
```
    @guard("scan")
    async def _cmd_latest_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
```
```

## M-03 [MEDIUM] /autoconfirm status card reports the frozen CONFIG threshold while the money path gates on the RUNTIME one, so the card asserts a bar that is not in force

- **Dimension**: ai-to-money · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/skills/telegram_handler.py:5726`
- **Standard**: CLAUDE.md: "Ask which OTHER surface makes the same claim" and the corollary that a claim made in several places will disagree with itself. Also the direct precedent in the same file's own history: /start reading one of five gate conditions.

**Observed**: The card states a value that no gate consults. The dangerous direction is the adaptive one: the operator is told real-money unattended execution needs 85% confidence while the loop is firing at 60%. `engine.py:1745` already renders the correct source (`"auto_confirm_threshold": round(RUNTIME.auto_confirm_threshold, 2)`), so two surfaces of the same control disagree.

**Expected**: The card states the threshold the auto-confirm loop actually tests against — RUNTIME — or says it cannot read it.

**Root cause**: One control, two storage locations, and the display picked the wrong one. `RUNTIME.auto_confirm_threshold` is seeded from `CONFIG.auto_confirm_threshold` at construction (bot/config.py:2596), so the two agree until the first write and the bug is invisible in a fresh process — including in any test that never mutates it.

**Business impact**: The operator's only readout of the unattended-live-execution bar can be wrong in both directions on a control that decides whether real orders are placed with no human press. A green 'ON at 85%' shown over a gate actually set to 1.0, or over an adaptively-lowered 0.60, is a status card manufacturing a number nobody measured.

**Reachability**: `_cmd_autoconfirm` is @guard("admin") and is the operator's only interactive view of this control. Both the setter and the two money-path readers were read in full; the divergence is unconditional after any write. There is no upstream sync that copies RUNTIME back into CONFIG — CONFIG is a frozen dataclass field read directly.

**Existing tests**: grep of tests/ for `autoconfirm` returns only tests/test_no_hardcoded_risk_check_count.py:80 (a doc-string mention), tests/fixtures/command_audience_backlog.json (a catalogue note), and tests/test_operator_controls_are_derived.py:206 (which notes /autoconfirm assigns to RUNTIME but does not check the read). Nothing pins the displayed value.

**Remediation**: Change telegram_handler.py:5726 to `threshold = RUNTIME.auto_confirm_threshold` (RUNTIME is already imported at 5721). Then add an assertion that plants a RUNTIME value diverging from CONFIG and reads the card back — the source-grep alone would not have caught this, since both names are spelled `auto_confirm_threshold`.

**Evidence**:

```
bot/skills/telegram_handler.py:5723-5738 — the status branch reads CONFIG:
```
        if not args:
            # Show current state
            threshold = CONFIG.auto_confirm_threshold
            if threshold >= 1.0:
                status = "\U0001f534 <b>OFF</b> — all trades require manual confirmation"
            else:
                status = f"\U0001f7e2 <b>ON</b> — trades with confidence ≥ <b>{threshold*100:.0f}%</b> auto-execute"
```
The same command's own setters write RUNTIME (telegram_handler.py:5743 `RUNTIME.auto_confirm_threshold = 1.0`, 5759 `RUNTIME.auto_confirm_threshold = new_threshold`), and both money-path readers read RUNTIME (engine.py:4330 and engine.py:6581 `auto_threshold = RUNTIME.auto_confirm_threshold`). A third writer moves it behind the operator's back — engine.py:4304-4319, the adaptive-threshold block:
```
                        if new_thresh != RUNTIME.auto_confirm_threshold:
                            audit(system_log, f"Adaptive threshold: ...")
                            RUNTIME.auto_confirm_threshold = new_thresh
```
with bounds `ADAPTIVE_THRESHOLD_MIN=0.60` / `MAX=0.90` (bot/config.py:1704-1705) and `ADAPTIVE_THRESHOLD_ENABLED` default True (bot/config.py:1700).
```

## M-04 [MEDIUM] ExecutePaperTradeSkill ignores the `confirmed` parameter its own spec calls "the human-in-the-loop gate", and routes to the LIVE confirm path despite its name

- **Dimension**: ai-to-money · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/skills/skill_registry.py:884-890`
- **Standard**: CLAUDE.md "A module nothing calls is indistinguishable from one that does not work" and the #999 precedent (code present, never reached, no scan can tell those apart). Also bot/skills/skill_permissions.py's own fail-closed rule.

**Observed**: The advertised human-in-the-loop parameter is not implemented at all, and the call it makes carries no user attribution, so it takes the `user_id=""` branch of every per-user gate: no strategy gate (engine.py:5702), no per-user margin cap (engine.py:924), operator executor, auto-minted Lock 5. Whoever eventually adds a permission line for this skill — the exact action tests/unreachable_skills_baseline.txt invites — turns a chat sentence into a live order on the operator's account.

**Expected**: Either the skill enforces the `confirmed` flag it advertises and refuses without it, or the spec stops claiming a gate the code does not have. And a skill named/described "paper" should not be the one that reaches the live executor.

**Root cause**: The skill was specified with a safety parameter and implemented without it, and being unreachable meant no caller ever exposed the gap. This is the CLAUDE.md pattern verbatim: a module nothing calls is indistinguishable from one that does not work.

**Business impact**: A published skill contract promises a human-in-the-loop gate that does not exist, on a skill whose one statement places a real order. The name and description ("paper") actively mislead whoever triages it for wiring.

**Reachability**: Currently NOT reachable — I verified all three doors: not in SKILL_PERMISSION (bot/skills/skill_permissions.py:24-62), therefore excluded from WEB_CHAT_SKILLS (line 66) and refused by the Telegram free-text gate (telegram_handler.py:2727-2731 denies when `permission_for()` returns None); and bot/mcp/server.py's tool list deliberately omits execute (lines 100-105). It is recorded in tests/unreachable_skills_baseline.txt. Severity reflects that: this is a latent contract violation, not a live bypass.

**Existing tests**: tests/test_registered_skills_are_reachable.py enforces the baseline file, which pins that the skill is dark — it says nothing about the `confirmed` parameter. grep of tests/ for `execute_paper_trade` returns only that baseline entry. tests/test_mcp_doc_matches_the_code.py covers MCP docs, not skill_definitions.yaml.

**Remediation**: Two lines and a rename, before anyone decides to wire it: reject unless `kwargs.get("confirmed") is True`, and pass the caller's id through (`engine.confirm_trade(trade_id, user_id=kwargs.get("user_id", ""))` — the chat dispatch sites already pass `user_id=tg_id`, see telegram_handler.py:2771) so the per-user gates run. If the skill is meant to stay dark, delete the registration rather than leaving a live-path caller sitting behind a paper-sounding name.

**Evidence**:

```
bot/skills/skill_registry.py:883-890 — the whole implementation:
```
class ExecutePaperTradeSkill(BaseSkill):
    name = "execute_paper_trade"
    description = "Confirm and execute a pending paper trade"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        trade_id = kwargs.get("trade_id") or kwargs.get("symbol", "")
        if not trade_id:
            return "Provide a trade_id to confirm."
        return await engine.confirm_trade(trade_id)
```
`kwargs.get("confirmed")` never appears. Its published contract, bot/prompts/skill_definitions.yaml:160-167, says otherwise:
```
      - name: confirmed
        type: boolean
        required: true
        description: >
          Must be true to proceed. The system will reject execution if this
          is false or missing. This is the human-in-the-loop gate.
```
And `confirm_trade(trade_id)` with no user_id is the LIVE path — engine.py:6118-6121 refuses paper outright: `if not CONFIG.is_live(): ... return "⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY."`, while `user_id=""` makes `_human_confirmed("")` False (engine.py:5537) so the Lock-5 token is auto-minted under the default `CONFIG.auto_confirm_live_enabled` (engine.py:6058) and `_executor_for("")` returns the operator's live executor (engine.py:1772).
```

## M-05 [LOW] MCP Shield renders an omitted confidence as a measured 0.65 — above the 0.60 trade floor — and reports it back as the trade's confidence

- **Dimension**: ai-to-money · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/mcp/server.py:432-459`
- **Standard**: CLAUDE.md: "Unreadable is never zero, and absent is never a measurement", and the `.get("pnl", 0)` row of the shapes table — an absent field rendered as a value. bot/risk/confidence_floor.py already implements the correct rule (`is None` -> does not clear) for the engine path.

**Observed**: An absent input becomes a measurement that clears the floor and scales the size, and is echoed to the caller as if measured. The tool's own description calls this "an immutable safety decision" that "any external agent can call", which is the surface where an invented input matters most.

**Expected**: Per bot/risk/confidence_floor.py's own docstring — "An idea with no confidence at all is a different thing and does not clear — an unmeasured setup is not a passing one" — an omitted confidence should make `confidence` a required parameter, or produce a third answer (unmeasurable) rather than a passing default.

**Root cause**: A convenience default on an optional parameter, chosen just above the gate it feeds.

**Business impact**: An external agent integrating against the Shield to decide whether a trade is safe can receive an APPROVED verdict and a position size partly derived from a confidence value it never provided, presented as its own.

**Reachability**: Reachability is WEAK and I state it plainly: `RuneClawMCPServer` has no non-test importer anywhere in the tree (`rg 'bot.mcp|MCPServer'` finds only bot/mcp/server.py's own docstring/definition and live_e2e_test.py), and the Node app's /mcp route (app/routes/mcp.js) is a separate implementation that does not expose a shield tool. So this is a defect in code that is currently only reachable by running bot/mcp/server.py directly. The tool is read-only — it places no order — which is why this is LOW.

**Existing tests**: tests/test_mcp_doc_matches_the_code.py checks documentation/code agreement for MCP tools, not the semantics of the default. No test in tests/ exercises `_shield_evaluate` with confidence omitted.

**Remediation**: Make `confidence` required (`required=True`, no default) so an omitting caller gets a validation error, or accept None and return a distinct `{"approved": false, "verdict": "UNSCORABLE", "confidence": null, "reason": "no confidence supplied — an unmeasured setup is not an approved one"}`. Do not echo a default as the caller's value.

**Evidence**:

```
bot/mcp/server.py:126-160 declares the parameter as optional with a fabricated default:
```
            MCPToolParam(
                name="confidence", type="number",
                description="Signal confidence 0.0-1.0.",
                required=False,
                default=0.65,
            ),
```
and bot/mcp/server.py:432-459 feeds it straight into the risk gate and then reports it as a fact:
```
    async def _shield_evaluate(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit: float, confidence: float = 0.65,
    ) -> str:
        ...
        idea = TradeIdea(..., confidence=confidence, ...)
        result = self._engine.risk.evaluate(idea, atr=atr)
        approved = result.verdict == RiskVerdict.APPROVED
        return json.dumps({
            "approved": approved,
            ...
            "confidence": round(confidence, 3),
```
The risk engine gates on exactly that field — bot/risk/risk_engine.py:1664-1669:
```
                from bot.risk.confidence_floor import min_confidence_for
                min_conf = min_confidence_for(idea)
                if idea.confidence < min_conf:
                    failed.append(f"CONFIDENCE: {idea.confidence} < {min_conf} minimum")
```
and also SIZES against it — risk_engine.py:2426 `fraction = self.kelly_position_size(idea.confidence, win_rate, avg_win, avg_loss)`.
```

## M-06 [CRITICAL] Bitget v3 channel always signs with the OPERATOR's credentials — per-user executors place stop-losses and flash-closes on the operator's account

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/bitget_v3_client.py:47-55`
- **Standard**: CLAUDE.md, per-account isolation: 'Per-user live trading runs one LiveExecutor per user, each bound to its own position/closed-trade files so accounts never share state' (live_executor.py:618-628). Placing one account's protective and closing orders on another account's key violates that invariant at the money layer, not just the state layer.

**Observed**: The raw-HTTP v3 half of the executor is hard-wired to the operator account. A per-user position's stop-loss/take-profit strategy order is posted to the operator's account; the emergency flash-close posts `{"category":"USDT-FUTURES","symbol":<user's symbol>,"posSide":<user's side>}` to the operator's account; and `sync_positions_from_exchange` reads the OPERATOR's open positions and writes their leverage (and a recomputed `cost_usd`) onto the USER's tracked positions whenever the symbols collide (live_executor.py:5023-5033).

**Expected**: A per-user executor must sign every venue call — ccxt and raw v3 alike — with that user's own credentials. The v3 client should be constructed from the executor's `self._credentials` (falling back to CONFIG.exchange only for the shared operator executor), exactly as `Venue.create_exchange(cfg, self._credentials)` already does.

**Root cause**: The v3 transport was extracted into `BitgetV3Client` with a single `from_config()` constructor at a time when only the shared operator executor existed. Per-user executors were added later and threaded credentials through `Venue.create_exchange` only; the five raw-v3 call sites were never given a credential seam. `_fetch_v3_positions_raw` is additionally a @staticmethod, so it structurally cannot see `self._credentials` or `self._venue` — it even checks the GLOBAL `get_venue().id != 'bitget'` rather than the executor's own venue, so a per-user Hyperliquid executor will still query Bitget v3 positions.

**Business impact**: Real money on the wrong account. Three concrete outcomes: (a) a user's SL/TP is posted with `tpslMode: "full"` against the operator's position on the same symbol, replacing the operator's own stop/target with levels computed for someone else's entry; (b) the emergency flash-close closes the OPERATOR's position on that symbol while the user's position stays open; (c) the user's tracked leverage and margin are silently overwritten with the operator's numbers, corrupting every downstream risk, exposure and PnL calculation. Where no operator position exists on the symbol, the SL/TP simply never lands (31008 ladder exhaustion) and the user's leveraged position runs with no venue-side stop.

**Reachability**: Reachable. `engine._executor_for` (bot/core/engine.py:1843) constructs `LiveExecutor(user_id=..., credentials=creds, venue=venue)` whenever `CONFIG.per_user_live_enabled` is true; `engine.balance_view_executor` (bot/core/engine.py:1909) builds per-user executors regardless of that flag (those are view-only and do not reach the v3 order paths). `use_v3` is set by `_detect_hold_mode`, which `_ensure_leverage` calls at live_executor.py:1209 on the live entry path, and Bitget UTA accounts are exactly the accounts that return 40085 there. The master flag PER_USER_LIVE_ENABLED defaults to False (bot/config.py:2261), which bounds today's exposure but is a one-env-var flip, and the wiring is complete on the other side of it.

**Existing tests**: tests/test_bitget_v3_client.py::TestFromConfig::test_from_config_reads_exchange_credentials pins the OPPOSITE property — that from_config reads CONFIG.exchange — and there is no test anywhere that exercises the v3 path on an executor carrying `credentials`. tests/test_sltp_variant_ladder.py stubs `from_config` out entirely (line 207), so it cannot see whose key is used. grep of tests/ for BitgetV3Client returns only those two files.

**Remediation**: Give `BitgetV3Client` a per-executor factory: store `self._v3 = BitgetV3Client(api_key, api_secret, passphrase)` built from `self._credentials or CONFIG.exchange` in `LiveExecutor.__init__`, and replace all five `BitgetV3Client.from_config()` call sites (live_executor.py:1390, 4903, 5115, 8672 and the `_fetch_v3_positions_raw`/`_fetch_position_margin_mode_v3` static pair) with it. Those two statics must become instance methods so they can also use `self._venue.id` instead of the global `get_venue()`. Until that lands, a fail-closed guard is the minimum: force `use_v3 = False` whenever `self._credentials` is set, so a per-user executor never signs with the operator key.

**Evidence**:

```
bot/core/bitget_v3_client.py:47-55
    @classmethod
    def from_config(cls) -> "BitgetV3Client":
        """Build a client from the live exchange credentials in CONFIG.
        ...
        """
        cfg = CONFIG.exchange
        return cls(cfg.api_key, cfg.api_secret, cfg.passphrase)

Every v3 call site constructs it that way, inside methods that run on a PER-USER executor:

bot/core/live_executor.py:5115 (_place_sl_tp_v3 -> POST /api/v3/trade/place-strategy-order)
                return cast(dict, BitgetV3Client.from_config().request("POST", path, body_dict))

bot/core/live_executor.py:8671-8672 (_flash_close_position -> POST /api/v3/trade/close-positions)
            result = await asyncio.to_thread(
                BitgetV3Client.from_config().request, "POST", path, body_dict)

bot/core/live_executor.py:4900-4903 (_fetch_v3_positions_raw)
        if get_venue().id != "bitget":
            return []
        from bot.core.bitget_v3_client import BitgetV3Client
        client = BitgetV3Client.from_config()

while the ccxt half of the same executor is correctly per-user — bot/core/live_executor.py:812-813:
            self._exchange = self._venue.create_exchange(cfg, self._credentials)
```

## M-07 [HIGH] BitgetV3Client ignores CONFIG.exchange.sandbox — a demo-configured bot sends live-account strategy and close orders

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/bitget_v3_client.py:33-44`
- **Standard**: CLAUDE.md's operational rule that a check answering about one half cannot report the other (the 2026-08-25 two-target deploy incident). Here it is worse than a reporting gap: one half of the order path is demo and the other half is live, within the same trade.

**Observed**: The v3 transport is unconditionally production: fixed `https://api.bitget.com`, fixed `category: USDT-FUTURES` (never the demo `SUSDT-FUTURES`), and no PAPTRADING header on any request. A bot the operator believes is paper-trading issues real strategy orders and real close-positions calls against the live account, using the same API key — Bitget demo trading is the same key plus the header, which is why ccxt implements it as a header and not a base-URL swap.

**Expected**: The demo/live environment selector must apply to every transport the executor uses. Either the v3 client sends `PAPTRADING: 1` when `CONFIG.exchange.sandbox` is set (matching ccxt), or the v3 path is refused outright in sandbox mode so a demo run cannot emit a live-account request.

**Root cause**: `from_config()` copies only the three credential fields off `CONFIG.exchange` and drops `cfg.sandbox`. The v3 channel was extracted from inline `urlopen` blocks that had the same omission, so the defect was preserved rather than introduced — but the ccxt side WAS fixed for the flag (venues.py:210-215, whose comment notes BITGET_SANDBOX had been dead config) and the v3 side was not, leaving the two halves of one executor pointed at two different matching engines.

**Business impact**: A demo-mode bot can close a real position (flash-close) and can overwrite a real position's TP/SL (tpslMode 'full'), because the same API key authorizes both environments. Conversely, in the ordinary case the demo position has no live counterpart, so the SL/TP ladder exhausts on 31008 and the demo run reports 'no exchange stop could be placed' — teaching the operator that a code path is broken when it is only pointed at the wrong environment, and hiding whether the real SL path works.

**Reachability**: Reachable whenever BITGET_SANDBOX=true on a UTA account. `use_v3` is set by `_detect_hold_mode` (live_executor.py:1382) via `_ensure_leverage` (line 1209) on the ordinary live entry path; `_flash_close_position` is reached from `_close_position_inner`'s 25227 branch (live_executor.py:7945). CONFIG.exchange.sandbox defaults to False (tests/test_core.py:2202 pins that), so this needs a deliberate demo configuration — which is exactly the configuration in which the operator is least expecting a live order.

**Existing tests**: No test covers it. grep of tests/ for BitgetV3Client returns tests/test_bitget_v3_client.py and tests/test_sltp_variant_ladder.py; neither mentions sandbox. tests/test_data_loader_venue.py::test_loader_never_opts_into_sandbox pins the DATA loader's sandbox behaviour, a different module. tests/test_exchange_credentials.py exercises the ccxt validation probes only.

**Remediation**: Add a `sandbox: bool` to `BitgetV3Client.__init__`, read `cfg.sandbox` in `from_config()`, and emit `"PAPTRADING": "1"` from `_headers()` when set — mirroring ccxt's rule (skip it when the request already names an S* productType). Then have `_place_sl_tp_v3` / `_fetch_v3_positions_raw` / `_flash_close_position` use `SUSDT-FUTURES` for `category` in sandbox mode. Until that lands, fail closed: refuse `use_v3` when `CONFIG.exchange.sandbox` is true so the demo run cannot reach the live channel at all.

**Evidence**:

```
bot/core/bitget_v3_client.py:33-44
BITGET_BASE_URL = "https://api.bitget.com"


class BitgetV3Client:
    """Signed HTTP transport for Bitget v3 REST endpoints."""

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 base_url: str = BITGET_BASE_URL) -> None:

bot/core/bitget_v3_client.py:69-77 — the full header set, with no demo marker:
    def _headers(self, timestamp: str, signature: str) -> dict[str, str]:
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

The ccxt half DOES honour the flag — bot/core/venues.py:210-215:
        # Explicit demo-trading activation (PAPTRADING header). ...
        if cfg.sandbox:
            exchange.set_sandbox_mode(True)

and ccxt turns that into a per-request header (.venv-audit/.../ccxt/bitget.py:10644-10651):
        sandboxMode = self.safe_bool_2(self.options, 'sandboxMode', 'sandbox', False)
        if sandboxMode and ...:
                headers['PAPTRADING'] = '1'
```

## M-08 [HIGH] Post-fill leverage-overshoot guard is structurally inert on the limit-fill, partial-fill and drift-fallback paths — it passes a spot-form symbol that ccxt filters out

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/live_executor.py:1896-1901`
- **Standard**: CLAUDE.md: 'A module nothing calls is indistinguishable from one that does not work', and the #999 lesson — 'The code was *present*. It was never reached, and no scan can tell those apart.' Also the helper's own docstring claim (live_executor.py:1863-1876) that these three paths now have a verdict.

**Observed**: `fetch_positions(['APT/USDT'])` returns [] (ccxt filters by the unified symbol, which carries the settle suffix), so `_verify_position_exists` falls through to its default `{'confirmed': False, 'leverage': 0}`. `int(verify.get('leverage') or 0)` is 0, `leverage_overshoot_verdict` returns decision='unknown' for a non-positive actual, and the guard logs 'Leverage unverified on limit fill' and returns None. A 5x-approved position filled at 20x is kept, on all three paths the helper was written to cover.

**Expected**: `_guard_fill_leverage` should read the venue's applied leverage and flatten a confirmed overshoot beyond `leverage_overshoot_max_ratio`, exactly as the market path does — i.e. it should pass `self._venue.swap_symbol(pos.symbol)` to `_verify_position_exists`, as every other position query in the file does.

**Root cause**: Symbol-spelling drift between the two halves of the executor — the exact class of bug this file's own `_rest_key` docstring documents ('The rest was set, was correct, and could never be found', live_executor.py:1422-1443). `execute` reassigns `symbol` to the perp form before its own verification, so the market path works; the shared helper reads `pos.symbol`, which is `idea.asset` (spot form) for a bot-opened position and only happens to be perp-form for adopted positions (`symbol=raw_sym` from the exchange payload, live_executor.py:2372) and manual trades (`pair = f"{symbol}/USDT:USDT"`, bot/skills/manual_trade.py:94).

**Business impact**: The concrete loss the guard was written for, from the file's own incident note: a 5x target filled at 20x has a liquidation distance ~5.0% adverse instead of ~20.0%, i.e. a 1.9-point buffer behind a 3.1% stop instead of 16.9 points. On the limit path — the default order type when CONFIG.limit_orders is enabled — the position is kept at that leverage with no verdict, and the operator sees only a debug-level 'Leverage unverified' line.

**Reachability**: Reachable and hot: `_guard_fill_leverage` is called on every limit fill (live_executor.py:6528), every adopted partial fill (6878) and every drift->market fallback (7044). The upstream `sync_positions_from_exchange` does NOT compensate — it writes the venue leverage onto `pos.leverage` but takes no action, which is precisely the gap the helper's docstring says it exists to close ('a log entry, not a decision'). The only positions for which the guard does work are adopted ones and manual /trade ideas, whose `pos.symbol` already carries the ':USDT' suffix.

**Existing tests**: tests/test_fill_leverage_guard_all_paths.py covers this helper but cannot see the defect: it replaces the dependency wholesale (`ex._verify_position_exists = AsyncMock(return_value={...'leverage': actual_leverage...})`, line 58) and builds its fixture position with the perp form (`def _pos(symbol="APT/USDT:USDT", ...)`, line 71). tests/test_leverage_overshoot_guard.py tests the pure `leverage_overshoot_verdict` function, which is correct. Nothing exercises the real symbol resolution.

**Remediation**: One line: `verify = await self._verify_position_exists(exchange, self._venue.swap_symbol(pos.symbol), ...)`. Better, make `_verify_position_exists` normalise its own input (`symbol = self._venue.swap_symbol(symbol)` at the top, and pass `params=self._venue.futures_params()`) so both callers and any future one inherit the correct behaviour — the same 'guard at the boundary' move the file already made for `_fmt_price(None)` and `_rest_key`.

**Evidence**:

```
bot/core/live_executor.py:1896-1901
            verify = await self._verify_position_exists(
                exchange, pos.symbol,
                "LONG" if pos.direction == "LONG" else "SHORT")
            actual = int(verify.get("leverage") or 0)
            verdict = leverage_overshoot_verdict(
                intended_leverage, actual, _max_ratio)

and the identity test it depends on, bot/core/live_executor.py:1994-2002:
            positions = await exchange.fetch_positions([symbol])
            for p in (positions or []):
                ...
                p_symbol = p.get("symbol", "")
                ...
                if p_symbol == symbol and contracts > 0 and p_side == expected_side:

Every OTHER position-fetch call site in the file converts first, e.g. bot/core/live_executor.py:8767-8769:
            ccxt_sym = self._venue.swap_symbol(pos.symbol)
            positions = await exchange.fetch_positions(
                [ccxt_sym], params=self._venue.futures_params())

and `execute`'s own use of the same helper passes the already-converted perp symbol (live_executor.py:4120-4123).
```

## M-09 [MEDIUM] The drift-to-market fallback opens a live position with no clientOid — the one entry path that bypasses _create_order_idempotent

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/live_executor.py:6958-6960`
- **Standard**: CLAUDE.md: 'Ask which OTHER surface makes the same claim — before calling the fix done.' The claim here is 'an entry order can never double-submit or be silently lost'; the idempotent wrapper makes it on four paths and this one does not.

**Observed**: This is the only position-opening `create_order` in the file that does not use the idempotent wrapper. Grep of create_order call sites: 4841/4864 (SL/TP triggers, reduceOnly), 5384 (_partial_close, reduceOnly), 7171 (_update_exchange_sl, reduceOnly), 7576 (close, reduceOnly), 1766 (inside `_create_order_idempotent` itself) — and 6958, which opens. It also passes `pos.symbol` and `self._venue.futures_params()` directly instead of `self._venue.order_symbol(pos.symbol)` and without the `price` that `market_order_needs_price` venues require, so on a non-Bitget venue it sends a USDT-quoted symbol to a USDC perp exchange with no reference price.

**Expected**: Every order that OPENS exposure goes through `_create_order_idempotent`, so a lost response is reconciled by clientOid rather than treated as a non-event. That is the stated design: 'Reused for every order/cancel below so a timeout-retry can never double-submit' (live_executor.py:3025-3027).

**Root cause**: The fallback was written as a self-contained 'cancel then market' routine and never re-based onto the idempotent submitter that the main entry path grew. The venue-dialect helpers (`order_symbol`, `market_order_needs_price`) were added later during the multi-venue work and this call site was missed — every other order call in the file was converted.

**Business impact**: A momentum-chase entry whose response is lost leaves real leveraged exposure on the venue with no stop-loss and no local record, for up to 5 minutes (orphan sweep) or, if the sweep also fails, until the 8-hour stale-pending timeout books a flat close for a position that is actually live. On a non-Bitget venue the order cannot be placed at all, after the resting limit has already been cancelled — the setup is lost without a trade.

**Reachability**: Reachable. Gated on `CONFIG.limit_orders.drift_market_fallback` and reached from `_check_pending_limit` at live_executor.py:6628 on any drifted resting limit with aligned ADX momentum, inside the ordinary per-tick monitor. The `trading_halted()` last-mile check immediately above it (line 6947) confirms the maintainers treat this as a real entry path. Verified that a repeat call cannot double-market: the second pass's `cancel_order` raises and the inner `fetch_order` reports 'canceled', which returns None at line 6919 — so the risk is the lost/orphaned fill, not a re-submission loop.

**Existing tests**: No test drives the create_order at 6958. tests/test_fill_leverage_guard_all_paths.py names `_execute_drift_market_fallback` in its docstring but only exercises `_guard_fill_leverage` with mocks. grep of tests/ for 'drift_market' finds no test that asserts an idempotency key on this order.

**Remediation**: Route it through the wrapper: `order = await self._create_order_idempotent(exchange, symbol=self._venue.order_symbol(pos.symbol), type='market', side=side, amount=qty, coid=self._client_oid(trade_id + '-drift'), price=(cur_price if self._venue.market_order_needs_price else None), params=self._venue.futures_params())`. The distinct coid suffix keeps it from colliding with the original limit's key while staying deterministic, so a retry of the fallback cannot double-fill.

**Evidence**:

```
bot/core/live_executor.py:6958-6960
            order = await exchange.create_order(
                pos.symbol, "market", side, qty,
                params=self._venue.futures_params())

compared with the entry path's own contract, bot/core/live_executor.py:1745-1753:
        """Place an order with an idempotency key, recovering from timeouts.

        Flow:
          1. Inject clientOid into params (Bitget dedups on it).
          2. Try create_order normally.
          3. On ANY exception, query the exchange by clientOid. If the order
             actually landed, return it (so a timed-out-but-filled order is
             reconciled instead of lost — and never re-submitted). ...

and the failure handler that turns a timeout into 'nothing happened', bot/core/live_executor.py:7069-7074:
        except Exception as exc:
            audit(trade_log,
                  f"Market fallback execution failed for {pos.symbol}: {exc}",
                  action="limit_drift_market_fallback", result="ERROR",
                  data={"trade_id": trade_id, "error": str(exc)})
            return None
```

## M-10 [MEDIUM] A failed close leaves the position reopened with stale SL/TP order ids for orders it already cancelled — recorded as protected while naked on the venue

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/live_executor.py:7968-7974`
- **Standard**: CLAUDE.md, 'Unreadable is never zero, and absent is never a measurement', applied to state rather than numbers: a cancelled order id rendered as protection is a confident positive built from an absence. The file states the rule itself at 6042-6053.

**Observed**: The position is returned to 'open' carrying ids for two cancelled orders. Local price-monitoring (the static SL/TP check at live_executor.py:6161-6188) still closes it on breach, so it is not wholly unmanaged — but the venue-side stop is gone, nothing re-places it on the next tick, no operator alert is raised, and every surface that reads `sl_order_id` reports the position as protected.

**Expected**: A cancel that succeeded followed by a close that failed must null the ids and mark the position unprotected — the same treatment the retry path gives at live_executor.py:6053 (`self._mark_stop_absent(pos, trade_id, had_stop=_had_stop)`), so the per-tick retry re-places the stop immediately and the operator alert fires.

**Root cause**: The H-01 revert was written to make the position retryable and only touched `status`. The protective-order fields are cleared on the success path (they go with the closed record) and on the residual path (live_executor.py:7629-7630 explicitly sets both to None), but not on the failure path.

**Business impact**: A leveraged position the bot tried and failed to close is left with no venue-side stop while every card, alert gate and self-heal check reads it as protected. Protection degrades to the local price monitor, which depends on the process staying alive and on a readable ticker — the exact single point of failure the exchange stop exists to remove.

**Reachability**: Reachable on any close that raises after the cancel loop — the cancel loop is unconditional whenever ids are stored, and `create_order` failures are ordinary (the handler exists precisely for them). Partially mitigated: `verify_and_fix_sltp` runs on a throttle from bot/core/engine.py:6816-6820 and, for the classic two-order case, `_missing_classic_legs` (live_executor.py:8895) will see the ids are no longer live and re-place — but only when `verify_classic_sltp_on_restart` is on (bot/config.py:1835, default True) and only once per `_SLTP_VERIFY_INTERVAL`, and the local record is wrong for the whole window in between. That mitigation is why this is MEDIUM and not HIGH.

**Existing tests**: grep of tests/ for 'CLOSE FAILED' and for the H-01 revert finds no test asserting the state of `sl_order_id` after a failed close. The tests that cover `_mark_stop_absent` exercise the sltp_retry path, not the close path.

**Remediation**: In the failure handler at live_executor.py:7968, before reverting status, clear whichever legs were actually cancelled and mark the state honestly: `if pos.sl_order_id and pos.sl_order_id not in cancel_failed: self._mark_stop_absent(pos, trade_id, had_stop=True, why=f'close failed: {exc_str[:80]}')` plus `pos.tp_order_id = None` for any TP that was cancelled. `_mark_stop_absent` already nulls the id, sets `unprotected`, saves and audits at WARNING.

**Evidence**:

```
The close cancels both protective legs first — bot/core/live_executor.py:7526-7534:
            # Cancel SL/TP orders BEFORE closing — prevents race condition where
            # a trigger fires between close-fill and cancel, opening an opposite pos.
            cancel_failed = []
            for oid in [pos.sl_order_id, pos.tp_order_id]:
                if oid:
                    ...
                        cancel_resp = await exchange.cancel_order(oid, self._venue.order_symbol(pos.symbol))

and the failure exit puts the position back without clearing them — bot/core/live_executor.py:7968-7974:
            # H-01 FIX: Revert status so position is retried next cycle
            pos.status = "open"
            self._save_positions()
            audit(trade_log, f"Live close failed: {exc}",
                  action="live_close", result="ERROR",
                  data={"trade_id": trade_id, "error": exc_str})
            return f"CLOSE FAILED for {trade_id}: {exc}"

The per-tick self-heal then skips it, because its gate is the stored id — bot/core/live_executor.py:6012-6013:
                    if (not pos.sl_order_id or not pos.tp_order_id) and pos.stop_loss > 0 and pos.take_profit > 0:

The file already knows this is the wrong shape; the sibling path spells it out at live_executor.py:6042-6053 — 'Leaving sl_order_id naming the cancelled order made every downstream signal read "protected" ... A cancelled stop that could not be replaced is an ABSENT stop, and the field has to say so.' — and calls `_mark_stop_absent`. The close path does not.
```

## M-11 [MEDIUM] Weekend/off-session stop widening inflates approved risk 40% on Stock-class perps, with no compensating size reduction

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/order_rules.py:34-35, 126-133`
- **Standard**: CLAUDE.md, risk-path invariant: adjustments after sizing must be reduce-only with respect to risk. The repo enforces exactly this elsewhere — bot/core/user_sizing.py's docstring ('A self-declared appetite must never RAISE risk ... Only reductions (mult < 1.0) applied pre-cap; never increases') and bot/core/leverage.py's reduce-only clamp.

**Observed**: Stock-class perps — the class with the LONGEST off-hours window, roughly 15 hours of every weekday plus all weekend — take a 40% wider stop at unchanged size. The risk engine approved a dollar risk it will not get. `ASSET_RULES` in the same module records `"Stock": {"min_sl_pct": 2.0, "weekend_sl_pct": 3.0, "max_leverage": 10}` (order_rules.py:161-167), but grep shows ASSET_RULES has zero references anywhere in bot/ or tests/ — it is a comment in dict form, so neither the weekend SL cap nor the 10x leverage cap for stocks is enforced.

**Expected**: Widening a stop after the position has been sized must be paired with a size reduction that holds dollar risk constant (size x 1/1.40 ~= 0.71), or the idea must be re-sized. For Metal/Commodity/ETF the 35% cut roughly does this (1.40 x 0.65 = 0.91). Stock gets the widening and none of the cut.

**Root cause**: `is_weekend_queued` collapses two different states — 'closed for the weekend' and 'outside today's session' — into one boolean, and the two adjusters then key off different class sets: the SL widener excludes only Crypto and Pre-IPO, while the size reducer includes only the three weekday-only classes. Stock falls through the gap in both directions.

**Business impact**: Every off-session stock-perp entry risks 1.40x the amount the risk engine approved and reported. The risk engine's per-trade % cap, its drawdown accounting and the R-multiples every downstream report is built on are all computed against the pre-widening stop, so the discrepancy is invisible in the audit trail — the `weekend_sl_widen` record logs the new stop but nothing recomputes size or risk from it.

**Reachability**: Reachable by default. `SCAN_CLASS_STOCKS` defaults to True (bot/config.py:2380) so stock perps are in the scanned universe; `_classify_symbol` returns 'Stock' for anything in `_STOCK_PERP_SET`/`_STOCK_SET` or any auto-discovered `*STOCK` base (bot/core/market_scanner.py:104-107). The widening block at live_executor.py:2918 runs before any order is placed and is not behind a feature flag. Note the widened stop is applied to `idea`, which is a copy (live_executor.py:2877), so it does not corrupt the caller's object — only this order's geometry.

**Existing tests**: grep of tests/ for `adjust_sl_for_gap_risk` and `adjust_size_for_weekend` returns nothing — neither function has any test. grep for ASSET_RULES returns only its own definition.

**Remediation**: Either add `Stock` (and any `_SESSION_HOURS` class) to the size-reduction set, or — better — derive the reduction from the widening so they cannot drift: return the widen factor from `adjust_sl_for_gap_risk` and have `execute` apply `size_usd /= widen_factor` whenever the stop was actually widened, for every class. Separately, either wire `ASSET_RULES` into the leverage/SL caps or delete it, so a table that reads as policy is not in fact inert.

**Evidence**:

```
bot/core/order_rules.py:34-35 — the two class sets are disjoint:
_WEEKDAY_ONLY = {"Metal", "Commodity", "ETF"}  # closed weekends
_SESSION_HOURS = {"Stock"}  # specific daily window

bot/core/order_rules.py:126-133 — the size reduction only covers _WEEKDAY_ONLY:
def adjust_size_for_weekend(
    size_usd: float,
    asset_class: str,
    is_weekend: bool,
) -> float:
    ...
    if not is_weekend:
        return size_usd
    if asset_class not in _WEEKDAY_ONLY:
        return size_usd

bot/core/order_rules.py:96-101 — but the SL widening covers every non-crypto class:
    if not is_weekend:
        return stop_loss
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return stop_loss
    ...
    # Widen by 40% (midpoint of GetClaw's 25-50% range)
    widen_factor = 1.40

and `execute` applies the widening to an already-sized idea — bot/core/live_executor.py:2918-2926:
        if is_weekend:
            old_sl = idea.stop_loss
            new_sl = adjust_sl_for_gap_risk(
                idea.stop_loss, idea.entry_price,
                idea.direction.value, asset_class, is_weekend,
            )
            if new_sl != old_sl:
                idea.stop_loss = new_sl
```

## M-12 [LOW] PartialTPState.__post_init__ discards persisted ladder state on every reload — remaining_qty, current_sl and runner_trail_best reset each monitor tick

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/partial_tp.py:47-50`
- **Standard**: CLAUDE.md, 'Write the assertion, then re-run the search' — the persisted dict is the reader and `asdict` is the writer, and they disagree, which is the same writer/reader drift `closed_trade_row` was made module-level and pure to prevent (live_executor.py:324-336).

**Observed**: The ladder's own bookkeeping is reset every tick. `close_qty = min(close_qty, state.remaining_qty)` (partial_tp.py:127, 145) therefore never clamps against what is actually left; the runner block's `new_sl = max(trail_sl, state.current_sl)` compares against the original loose stop rather than the ratcheted one and re-emits a move_sl action every tick; and `partial_tp_summary` (partial_tp.py:207-217) reports `pct_remaining` as 100% and `pct_closed` as 0% for a position that has already banked TP1.

**Expected**: Rehydrating a dataclass from its own `asdict` output must round-trip. `__post_init__` should seed those three fields only when they are unset (e.g. `if not self.remaining_qty: self.remaining_qty = self.original_qty`), or the seeding should move into `create_partial_tp_state`, where a fresh state is built.

**Root cause**: `__post_init__` was written for the fresh-construction case only (`create_partial_tp_state` sets no values for those three fields), and the persistence round-trip in `_run_partial_tp` was added later. The nearby comment at live_executor.py:5421-5428 shows the author reasoning carefully about a DIFFERENT rehydration hazard (initial_risk collapsing toward 0 on a ratcheted stop) in the `except` branch, while the happy-path branch one line above silently discards three fields.

**Business impact**: The partial-TP ladder's persisted state is wrong for the entire life of any position past TP1: the runner's trailing stop is recomputed from the current price each tick rather than advanced through `runner_trail_best`, and any surface built on `partial_tp_summary` reports 100% of the position still open after half of it has been banked. If either downstream clamp is ever removed in a refactor, the same defect becomes an over-close and a loosened stop.

**Reachability**: Reachable on every tick of every live position: `CONFIG.partial_tp.enabled` defaults True (bot/config.py:1685) and `_run_partial_tp` is called unconditionally for open positions at live_executor.py:6006-6007. The damage is bounded by two downstream guards, which is why this is LOW rather than higher: `_run_partial_tp` clamps every close to the live position (`qty = min(act.qty_to_close, pos.quantity)`, live_executor.py:5475), and `_would_tighten` (line 5447) refuses any SL move that would loosen `pos.stop_loss` — so the reset cannot cause an over-close or a loosened stop, only wrong internal bookkeeping and a trail that recomputes from scratch each tick instead of ratcheting through state.

**Existing tests**: grep of tests/ for PartialTPState / check_partial_tp finds tests that build state in-process and call `check_partial_tp` directly; none constructs a `PartialTPState(**asdict(st))` round trip, which is why the clobber has never been observed.

**Remediation**: Change `__post_init__` to seed only unset fields: `if not self.remaining_qty: self.remaining_qty = self.original_qty`, `if not self.current_sl: self.current_sl = self.original_sl`, `if not self.runner_trail_best: self.runner_trail_best = self.entry_price`. Add a round-trip test — `PartialTPState(**asdict(st)) == st` — since that single assertion covers every field including any added later.

**Evidence**:

```
bot/core/partial_tp.py:47-50 — the post-init overwrites three fields that are also constructor arguments:
    def __post_init__(self):
        self.remaining_qty = self.original_qty
        self.current_sl = self.original_sl
        self.runner_trail_best = self.entry_price

and bot/core/live_executor.py:5417-5419 rehydrates the ladder by passing the persisted dict straight into that constructor:
        else:
            try:
                st = PartialTPState(**pos.partial_tp_state)

with the state written back each tick at bot/core/live_executor.py:5504:
        pos.partial_tp_state = _dc.asdict(st)
```

## M-13 [LOW] Tier-C size reduction recomputes quantity after the exchange-minimum and notional checks have already run

- **Dimension**: order-exec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/live_executor.py:3528-3534`
- **Standard**: CLAUDE.md's guard-at-the-boundary principle (`_fmt_price(None)` returning an em dash rather than a dozen call sites remembering to check) and the file's own stated rule about never surfacing raw venue errors.

**Observed**: The reduced quantity is re-rounded outside a try/except and is never re-validated against min amount or min notional, so a sub-minimum tier-C order produces a raw venue error string instead of the clean `BLOCKED: ... position too small for the exchange` message the same condition produces 180 lines above.

**Expected**: Any change to `quantity` must be followed by the same three checks the first computation gets: the exchange-minimum resolution (`resolve_exchange_min_quantity`), the guarded `amount_to_precision`, and `_validate_order_limits`. The file's own comment at 3376-3381 states the standard: 'never surface a raw venue precision error — classify it as a clean skip'.

**Root cause**: The confluence/tier logic was inserted after the validation block rather than before it, so the size decision now happens downstream of the checks written to bound it.

**Business impact**: An operator sees a raw ccxt or Bitget error string instead of the actionable 'position too small for the exchange' message the codebase produces for the identical condition on the unreduced path — the exact diagnosability regression the guarded call at 3376 was added to prevent. No capital moves either way.

**Reachability**: Reachable whenever `CONFIG.limit_orders.enabled` and a limit order needs price recalculation with a valid ATR (live_executor.py:3480-3495). No capital is at risk: both failure modes are pre-fill refusals — an exception before submission, or a venue rejection of the order. That is why this is LOW.

**Existing tests**: grep of tests/ for 'limit_tier_c' / 'SIZE_REDUCED' finds no test that drives the tier-C branch through `execute` with a market whose minimums the reduced size would breach.

**Remediation**: Move the whole minimum/precision/notional validation block below the confluence recalculation, or extract it into a small `self._finalize_quantity(...)` helper called from both places, so the tier-C branch inherits the same guarded behaviour instead of re-implementing half of it.

**Evidence**:

```
bot/core/live_executor.py:3528-3534 — the reduction and its unguarded re-rounding:
                        # Recalculate quantity with new size
                        quantity = (size_usd * leverage_mult) / current_price
                        if market:
                            _re_rounded = active_exchange.amount_to_precision(symbol, quantity)
                            if _re_rounded:
                                quantity = float(_re_rounded)

compared with the first rounding, ~180 lines earlier, which the file deliberately wrapped — bot/core/live_executor.py:3376-3386:
                try:
                    _rounded = active_exchange.amount_to_precision(symbol, quantity)
                except Exception as _prec_exc:
                    # Defense-in-depth: never surface a raw venue precision
                    # error — classify it as a clean skip.
                    ...
                    return (f"BLOCKED: {symbol} position too small for the "
                            f"exchange's precision rules ({quantity:.8f}). Skipped.")

The minimum-quantity guard (live_executor.py:3327-3372) and `_validate_order_limits` (line 3437) both run on the PRE-reduction quantity and are never re-run.
```

## M-14 [CRITICAL] Unreadable live equity silently reroutes the DAILY_LOSS and DRAWDOWN breakers to the paper book, which is structurally flat in LIVE-ONLY mode — both print "0.0% OK"

- **Dimension**: risk-engine · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/risk/risk_engine.py:1413-1418, 1475-1486 (and bot/core/engine.py:5325, bot/core/engine.py:895)`
- **Standard**: CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement.' Also the guard-or-omit table: a single-source panel must guard, never render a confident negative.

**Observed**: live_equity=None (or 0.0) falls through to the PAPER PortfolioTracker snapshot. In LIVE-ONLY mode (bot/core/engine.py:6111 'This bot is LIVE-ONLY'; the confirm success branch at bot/core/engine.py:6395 is commented 'Exchange is single source of truth — no paper duplicate') that book holds no positions and no history, so _snapshot_locked() (bot/risk/portfolio.py:445-452) yields daily_pnl=0.0 and current_drawdown=0.0. Both gates emit a confident measured pass and the trade is APPROVED while the real account is 50% below its peak with the day's loss cap already spent.

**Expected**: An unreadable live equity is not a measurement. Both gates should refuse (or report UNKNOWN and let the caller refuse), as bot/risk/venue_aggregate.py:cap_verdict already does for the multi-venue caps.

**Root cause**: The live branch is selected on `live_equity is not None and live_equity > 0`, and the else-branch is a real fallback rather than an unknown state. The two feeder sites read `self._live_balance_cache` directly — engine.py:5325 (scan risk gate) and engine.py:895 (_live_recheck_context, the pre-execution re-check) — with no age check. The cache is emptied by _invalidate_live_balance_cache() on every position close (bot/core/engine.py:827-830), so a venue outage or auth blip right after a close, or any window before the first successful fetch, yields {} -> live_eq=None. The comments beside both gates already state the paper numbers are meaningless in live ('the paper snapshot's daily_pnl is ~0 because live fills never touch the paper portfolio'; 'not the paper snapshot which never moves in pure-live mode (audit CRITICAL)').

**Business impact**: The two controls that decide how much real money is lost before the bot halts can be defeated by a venue timeout. An account already 50% below its high-water mark is told 'DAILY_LOSS: 0.0% OK / DRAWDOWN: 0.0% OK' and permitted to open further positions.

**Reachability**: Reached on both live money paths: bot/core/engine.py:5355 (scan-time risk gate) and bot/core/engine.py:5937 (confirm-time re-check, whose verdict gates executor.execute at bot/core/engine.py:6381). No upstream guard blocks trading on an absent balance: set_live_auth_status is only ever called from bot/main.py (boot preflight) — grep over the tree shows no runtime caller — so a balance-fetch failure does not mark venue auth down and does not engage the auth halt at engine.py:6372.

**Existing tests**: tests/test_live_account_breakers.py and tests/test_live_drawdown_reporting.py always pass a positive live_equity. tests/test_live_balance_age_gate.py pins the age-gated accessor and asserts only display surfaces (scan_skill, web_reports, skill_registry, telegram_handler) read through it — bot/core/engine.py is not in its reader list, so the two money-path reads are unpinned. No test drives evaluate() in live with live_equity=None.

**Remediation**: Make the live branch three-valued: when CONFIG.is_live() and live equity cannot be read, append a FAILED check ('DAILY_LOSS/DRAWDOWN: live equity unreadable — cannot measure') rather than evaluating the paper snapshot; select the paper branch only when not is_live(). Separately route engine.py:5325 and engine.py:895 through the existing age-gated accessor engine.live_balance_cached() so a frozen cache is also reported as unknown rather than as current equity.

**Evidence**:

```
bot/risk/risk_engine.py:1413-1418
            if live_equity is not None and live_equity > 0:
                _daily_pnl = self._live_daily_pnl
                loss_base = live_equity
            else:
                _daily_pnl = state.daily_pnl
                loss_base = min(sizing_equity, state.equity_usd) if sizing_equity > 0 and state.equity_usd > 0 else max(sizing_equity, state.equity_usd)

bot/risk/risk_engine.py:1475-1486
            if live_equity is not None and live_equity > 0:
                self._last_live_equity = live_equity
                if live_equity > self._live_equity_peak:
                    self._live_equity_peak = live_equity
                _cur_dd = (100.0 * (self._live_equity_peak - live_equity)
                           / self._live_equity_peak) if self._live_equity_peak > 0 else 0.0
            else:
                _cur_dd = getattr(state, "current_drawdown_pct", state.max_drawdown_pct)

bot/core/engine.py:5325
        live_eq = self._live_balance_cache.get("total", 0.0) if (CONFIG.is_live() and self._live_balance_cache) else None
```

## M-15 [HIGH] The LIVE daily-loss accumulator is dropped by the combined-state restore that production actually uses — the daily-loss breaker gets a full fresh budget on every restart

- **Dimension**: risk-engine · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/risk/risk_engine.py:3478-3491`
- **Standard**: The file's own comment at bot/risk/risk_engine.py:302-310: 'Lose 4.5% against a 5% cap, redeploy, lose 4.5% again, and the gate reads 4.5% while the day is really 9.0% — the breaker that exists to stop exactly that never trips, and this deployment redeploys often.'

**Observed**: _load_from_state_dict restores circuit_open, streak, trip cause/day, the drawdown override and the live peak, but not the live daily-loss accumulator — even though _export_state_dict (bot/risk/risk_engine.py:3455-3462) writes live_daily_pnl and live_daily_day into the very dict it is handed.

**Expected**: A mid-day restart must hand the engine back the day's realized live loss, as tests/test_daily_loss_survives_restart.py requires: 'A fresh DAY starts flat. A restart is not a fresh day.'

**Root cause**: Two restore paths that must agree; only one was updated when _restore_live_daily was added. bot/core/engine.py:2635-2638 loads combined_state.json through _load_from_state_dict, and bot/core/engine.py:2668-2669 then wires _combined_saver on both portfolio and risk, so bot/risk/risk_engine.py:3407-3409 routes every subsequent _save_state() into combined_state.json and data/risk_state.json is never written again. The constructor's _load_state() therefore reads a file frozen at migration time whose stored day no longer matches today, so _restore_live_daily correctly ignores it — and the combined restore that carries today's value never calls it.

**Business impact**: On a deployment CLAUDE.md describes as redeploying often, the 5% daily-loss circuit breaker can be reset to a full budget several times in one UTC day. Three redeploys after 4.5% losses is a ~13.5% real day against a 5% cap, with the breaker never tripping.

**Reachability**: bot/core/engine.py:2617-2638 — _wire_combined_state_saver is called unconditionally from RuneClawEngine.__init__ (bot/core/engine.py:497); when data/combined_state.json exists it calls self.risk._load_from_state_dict(combined['risk']). That file is created by the first _save_state after wiring, so every boot after the first takes this path. This is the operator/shared engine, which is what risk_for() returns whenever PER_USER_LIVE_ENABLED is off (the default) — the engine that gates every live trade.

**Existing tests**: tests/test_daily_loss_survives_restart.py exercises only `RiskEngine(PortfolioTracker(), state_file=...)` via its _engine helper, i.e. the individual-file path. Grep for 'live_daily_pnl' across tests/ returns only that file, test_surface_scenarios.py and test_live_account_breakers.py, none of which touch the combined path.

**Remediation**: Add `self._restore_live_daily(data)` to _load_from_state_dict alongside _restore_dd_override/_restore_live_peak, and extend tests/test_daily_loss_survives_restart.py with a case that round-trips through _export_state_dict -> _load_from_state_dict rather than only through the individual state file.

**Evidence**:

```
bot/risk/risk_engine.py:3478-3488 (the path production loads from)
    def _load_from_state_dict(self, data: dict) -> None:
        """C2-34: Restore risk state from a dict (no file I/O).
        Uses fail-closed semantics matching _load_state."""
        self._circuit_open = data.get("circuit_open", False)
        ...
        self._restore_dd_override(data)
        self._restore_live_peak(data)

bot/risk/risk_engine.py:3334-3336 (the path only the legacy individual file uses)
            self._restore_dd_override(data)
            self._restore_live_daily(data)
            self._restore_live_peak(data)
```

## M-16 [HIGH] Correlation, portfolio-exposure, symbol-exposure, PCA-concentration and VaR gates measure the paper portfolio, which is never populated in LIVE-ONLY mode — five of the 23 caps report measured passes over an empty book

- **Dimension**: risk-engine · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/risk/risk_engine.py:1853, 1865, 3115-3118`
- **Standard**: CLAUDE.md: 'A module nothing calls is indistinguishable from one that does not work' — here a gate whose only input is never populated. And 'absent is never a measurement': 'CORRELATION: no concentrated exposure' is a confident negative computed from an empty set.

**Observed**: With an empty paper book, group_count is always 0 so #8 never rejects; open_value is always 0.0 so #14 only ever scores the single new position; symbol_value is always 0.0 so #15 likewise; #20 always reports 'fewer than 2 open positions'; #21's current_exposure loop over self._portfolio.open_positions contributes 0. All five append confident 'OK' strings to checks_passed, so the audit trail and /whynot record a cap as verified when nothing was measured.

**Expected**: CONFIG.risk.max_correlation_per_group (default 2, bot/config.py:436), max_portfolio_exposure_pct (80.0, bot/config.py:434) and max_symbol_exposure_pct (20.0, bot/config.py:435) should bind against the account actually holding the positions.

**Root cause**: The daily-loss and drawdown gates were each given an explicit live-mode data source (self._live_daily_pnl, self._live_equity_peak) when the pure-live paper-book problem was found; the exposure/correlation/concentration/VaR gates were not, and still read PortfolioTracker. The live book lives in LiveExecutor._positions, which the RiskEngine holds no handle on.

**Business impact**: With MICRO_MAX_OPEN_POSITIONS=5 a live account can hold five positions all in one correlation group and one direction (e.g. five ALT_L1 longs) against a configured max_correlation_per_group of 2. A single correlated move then hits every position at once — the exact concentration the cap exists to prevent.

**Reachability**: All five run on every live evaluation (bot/core/engine.py:5355 and :5937). Partial upstream coverage exists but does not substitute: LiveExecutor._preflight_check (bot/core/live_executor.py:1493-1497, 1541-1542) enforces MICRO_MAX_TOTAL_EXPOSURE ($500) and MICRO_MAX_OPEN_POSITIONS (5) from its own _positions, and its duplicate-symbol guard (lines 1544-1555) blocks a second position on the same symbol. Nothing anywhere enforces the correlation-group cap on live positions: the only place live positions are mapped to correlation groups is bot/core/engine.py:1407 inside _twin_positions, feeding run_digital_twin, whose docstring says 'Read-only foresight + fail-open: it never proposes, blocks, or alters a trade.'

**Existing tests**: tests/test_core.py, tests/test_audit_f3_reconciliation.py and tests/test_roadmap_p0.py exercise these checks by populating a PortfolioTracker directly (paper semantics). No test drives them with a live executor book, and none asserts the live-mode behaviour.

**Remediation**: Either inject a live-position provider into RiskEngine (same shape as set_person_totals_fn) so #8/#14/#15/#20/#21 read the executor's book in live mode, or — if the intent is that the executor's dollar caps are the live control — make these checks report SKIPPED/NOT-MEASURED in live so the audit record and /whynot stop claiming a cap was verified.

**Evidence**:

```
bot/risk/risk_engine.py:1852-1857 (check #14, portfolio exposure)
            margin_equiv_position_usd = position_usd
            open_value = self._portfolio.get_position_value()
            exposure_pct = (open_value / sizing_equity * 100) if sizing_equity > 0 else 0
            new_exposure = exposure_pct + (margin_equiv_position_usd / sizing_equity * 100 if sizing_equity > 0 else 0)
            if new_exposure > CONFIG.risk.max_portfolio_exposure_pct:

bot/risk/risk_engine.py:3114-3120 (check #8, correlation cap)
        new_group = self._correlation_group(idea.asset)
        open_groups: list[str] = [
            self._correlation_group(pos.asset)
            for pos in self._portfolio.open_positions
        ]

        group_count = open_groups.count(new_group)

bot/risk/risk_engine.py:1865 (check #15, per-symbol exposure)
            symbol_value = self._portfolio.get_position_value(asset=idea.asset)
```

## M-17 [MEDIUM] The combined-state load path is not fail-closed despite its docstring — a corrupt combined_state.json silently clears an open circuit breaker instead of halting

- **Dimension**: risk-engine · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/engine.py:2645-2652 (with bot/risk/risk_engine.py:3478-3481)`
- **Standard**: CLAUDE.md: 'Unreadable is never zero' — an unreadable risk state is not a clear one; and _fail_closed_restore's own rationale, 'an unknown risk state is not a safe one'.

**Observed**: A corrupt combined_state.json produces a warning line and a boot with whatever the constructor's _load_state() left behind — in a deployment that was never migrated from legacy files, that is a fresh start with _circuit_open=False. An open breaker is silently cleared by a corrupt file, which is precisely what _fail_closed_restore exists to prevent.

**Expected**: Parity with _load_state's documented contract (bot/risk/risk_engine.py:3311-3316): 'Corrupt file (non-empty but invalid JSON) -> assume breaker TRIPPED (fail-closed)', with cause state_unreadable recorded and the damaged file preserved.

**Root cause**: Two restore paths with one documented contract; the fail-closed behaviour was implemented only in the file-reading path, and the combined path's docstring asserts a property the code does not have. The caller's recovery ('fall back to individual files') was true when both files were live, and stopped being true once the combined saver became the only writer.

**Business impact**: A halt that exists precisely because the bot lost money can be erased by a corrupt state file at restart — the failure mode _fail_closed_restore was written to prevent, arriving through the door that path does not cover.

**Reachability**: bot/core/engine.py:2617-2652 runs on every boot once data/combined_state.json exists, and that file is created by the first _save_state after wiring. The corruption case itself requires a torn or garbled file — atomic_write_json plus fsync_dir make that unlikely but not impossible (disk-full, filesystem corruption, an operator restoring a truncated backup).

**Existing tests**: tests/test_combined_state_per_user_intent.py pins that per-user state stays out of the combined file. Grep across tests/ finds no test that feeds a corrupt combined_state.json through _wire_combined_state_saver and asserts the breaker ends up open.

**Remediation**: Wrap _load_from_state_dict's body so a malformed dict calls self._fail_closed_restore('Combined state unreadable', 'COMBINED_FAIL_CLOSED'), and make _wire_combined_state_saver's except branch do the same rather than logging and continuing. Consider reading the .bak written at bot/core/engine.py:2697-2704, which is currently produced and never consumed.

**Evidence**:

```
bot/risk/risk_engine.py:3478-3481
    def _load_from_state_dict(self, data: dict) -> None:
        """C2-34: Restore risk state from a dict (no file I/O).
        Uses fail-closed semantics matching _load_state."""
        self._circuit_open = data.get("circuit_open", False)

bot/core/engine.py:2645-2652
            except Exception as exc:
                # Combined file corrupt — fall back to individual files
                # (which were already loaded by each component's __init__)
                system_log.warning(
                    "C2-34: Combined state corrupt (%s), using individual files",
                    exc,
                )
```

## M-18 [MEDIUM] A circuit-breaker trip or /halt never cancels resting limit ENTRY orders, so new exposure can still open on an account that has just breached its drawdown or daily-loss limit

- **Dimension**: risk-engine · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/live_executor.py:576-579`
- **Standard**: The one-rule framing in bot/core/trade_gate.py ('A gate with five conditions and six renderings of it will disagree with itself'), applied to enforcement rather than reporting: the rule stops the bot's next order but not the order the bot already sent.

**Observed**: The halt is honoured only on code paths the bot itself drives (trading_halted() at live_executor.py:3759, 3873, 6949). An entry order already resting at Bitget is filled by the venue and adopted as a real position on a halted account, with SL/TP placed as normal.

**Expected**: A breaker that exists to stop the account taking more risk should also withdraw the orders that would add risk, or at minimum surface that N resting entries remain live at the venue.

**Root cause**: The kill switch was implemented as a decision gate on the bot's own code paths rather than as an action on venue state. The last-mile checks correctly close the race between the engine's check and order submission, but nothing addresses orders that were already submitted before the trip.

**Business impact**: An account that has just hit its max-drawdown limit can acquire additional positions minutes later because an order placed before the trip finally fills, while every operator surface reports trading as Paused.

**Reachability**: Reachable whenever limit entries are in use (CONFIG.limit_orders.enabled; bot/core/live_executor.py:4052 records them with status='pending_fill', and bot/core/live_executor.py:2607 adopts orphaned limit orders into the same state). The window is as long as the limit-order expiry.

**Existing tests**: Tests reference trading_halted and the last-mile refusals (the BLOCKED_HALTED strings). Grep across tests/ finds no test asserting that a breaker trip cancels or blocks resting limit entries.

**Remediation**: On a breaker trip with cause in (daily_loss, drawdown, streak), cancel every pending_fill entry order on the affected executor — the cancel logic already exists in _close_position_inner's pending_fill branch — or at minimum add a halt check to the pending_fill->open transition so a fill arriving while halted is immediately flattened rather than adopted. Either way the trip card should state how many resting entries were live at the moment of the trip.

**Evidence**:

```
bot/core/live_executor.py:576-579 (the module's own statement of the gap)
# Consequence, all defaults: a resting limit order placed before a `/halt` or an
# automatic breaker trip would still convert to a market BUY on drift, opening
# NEW exposure on a halted account. Neither /halt nor any breaker cancels resting
# limits; only /emergency_stop does.

bot/risk/risk_engine.py:3604-3612 (what a trip actually does)
    def _trip_circuit_breaker(self, reason: str, cause: str = "manual") -> None:
        if not self._circuit_open:
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            self._circuit_trip_cause = cause
            self._circuit_trip_day = datetime.now(UTC).strftime("%Y-%m-%d")
```

## M-19 [MEDIUM] get_exchange_position_count fabricates a position count from local state when the venue is unreachable, caches it for 30s, and MAX_POSITIONS then prints a confident "OK"

- **Dimension**: risk-engine · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/exchange_sync.py:440-452`
- **Standard**: CLAUDE.md: 'absent is never a measurement'; bot/risk/venue_aggregate.py: 'a floor below the cap proves nothing. The unreadable venue could hold anything. ACCEPT is not safe.'

**Observed**: The failure returns an integer indistinguishable from a real reading, and check #5 reports it as a measured pass.

**Expected**: An unreadable venue position count must be reported as unknown so risk check #5 can refuse — exactly as bot/risk/venue_aggregate.py:cap_verdict does: 'under it, INCOMPLETE -> REFUSE, naming the venue that went unread ... Allowing on a floor is how a cap gets loosened by a timeout.'

**Root cause**: The comment's premise is inverted: max(local_live, local_port) is a FLOOR of the true exchange count, not a ceiling. Local tracking can sit below the truth after a restart before adopt_exchange_positions runs, when a position was opened outside the bot, or when the executor's persisted book was lost — precisely the situations in which an authoritative count matters. The repo's own bot/risk/venue_aggregate.py documents this floor-vs-cap distinction at length and reaches the opposite conclusion.

**Business impact**: During a venue outage the max-open-positions cap can be evaluated against a count lower than reality, admitting positions on top of an already-full book — and the audit record states the count as measured, so the operator cannot tell afterwards.

**Reachability**: Called from bot/core/engine.py:5341 (scan risk gate) and bot/core/engine.py:897 (_live_recheck_context, the pre-execute re-check). The executor's own MICRO_MAX_OPEN_POSITIONS check (bot/core/live_executor.py:1540-1542) counts the same local _positions map, so it shares the blind spot rather than covering it.

**Existing tests**: Grep across tests/ for get_exchange_position_count finds no test of the exception branch; nothing asserts what MAX_POSITIONS reports when the venue count is unreadable.

**Remediation**: Return Optional[int] (None on failure), have callers propagate that as 'not measured' rather than as a count, and give risk check #5 an unreadable branch that fails closed the way its person-level sibling already does at bot/risk/risk_engine.py:1549-1557. Do not cache a fabricated count.

**Evidence**:

```
bot/core/exchange_sync.py:440-452
    except Exception as exc:
        audit(system_log,
              f"Could not fetch exchange position count: {exc}",
              action="exchange_position_count", result="ERROR")
        # Fall back to local state maximum so we never accidentally
        # exceed limits when the exchange API is unreachable.
        local_live = len(getattr(engine.live_executor, "open_positions", {}))
        local_port = len(getattr(engine.portfolio, "_positions", {}))
        fallback = max(local_live, local_port)
        # Cache fallback too to prevent repeated failed API calls
        _position_count_cache["count"] = fallback
        _position_count_cache["timestamp"] = now
        return fallback
```

## M-20 [MEDIUM] Person-level multi-venue caps (drawdown, daily loss, open positions) are computed from PAPER portfolio snapshots, so they measure an empty book for a live user and enforce nothing

- **Dimension**: risk-engine · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/engine.py:2186-2188 (with bot/risk/multi_portfolio.py:273-281)`
- **Standard**: CLAUDE.md's reachability rule applied to data rather than callers: a control whose input is never populated is indistinguishable from one that does not work.

**Observed**: _person_open_positions() returns 0 and _person_daily_loss_pct() returns 0.0 for a live user; both are folded in with max() at bot/risk/risk_engine.py:1440-1442 and 1541-1543, so they never raise the measured value and the cap is a no-op. _person_drawdown_pct() (bot/risk/risk_engine.py:938-963) feeds the paper equity into get_person_peak_store().drawdown_pct, seeding the shared person high-water mark from a paper balance and returning ~0.0%, which the tighten-only max() at bot/risk/risk_engine.py:1493-1494 then discards.

**Expected**: Per bot/risk/venue_aggregate.py's own statement of intent: 'Two venues each holding their own "max 5 open positions" is ten positions against one person's money, and nobody chose ten.' The person totals must reflect the books that actually hold the money.

**Root cause**: The Phase 3 aggregation was wired to MultiUserPortfolio because that is where a per-(user, venue) book object exists, but in LIVE-ONLY mode the live book is LiveExecutor._positions plus the venue balance, not the paper tracker. The abstraction is right; the data source is the wrong one. A related quiet edge: if _totals raises, _person_totals() returns None (bot/risk/risk_engine.py:876-886) and _person_totals_incomplete() then also returns '', so the incompleteness that would otherwise force a refusal is never reported.

**Business impact**: The stated purpose of Phase 3 — one person's caps counted once across every venue — is not delivered for live trading. A user on two venues under per-user live can hold MICRO_MAX_OPEN_POSITIONS on each, which is the 'ten positions against one person's money' the design document says must not happen.

**Reachability**: Bound only in risk_for() (bot/core/engine.py:2153-2196), which returns the shared engine unless CONFIG.per_user_live_enabled is set — default False (bot/config.py:2261). So this bites only deployments that have enabled per-user live trading, which is also the only configuration in which one person has multiple venue books. The failure direction is inert rather than actively loosening, because every fold-in uses max().

**Existing tests**: venue_aggregate and person_peak are unit-tested as pure functions. Grep across tests/ finds no test asserting that the person totals reflect a LIVE book, and none that exercises _totals against a live executor.

**Remediation**: Build VenueReadings from each venue's live executor (open position count) and its fetched balance / daily realized PnL, keeping the existing None-on-failure semantics so an unreadable venue still marks the total incomplete and forces a refusal. The aggregate/cap_verdict layer needs no change. Separately, distinguish 'no totals provider' from 'the provider raised' in _person_totals so the latter reports incompleteness.

**Evidence**:

```
bot/core/engine.py:2186-2188
            def _totals(_uid=str(user_id)):
                from bot.risk.venue_aggregate import aggregate
                return aggregate(self.user_portfolios.venue_readings(_uid))
            eng.set_person_totals_fn(_totals)

bot/risk/multi_portfolio.py:273-281
        for venue, book in books:
            try:
                snap = book.snapshot()
                out.append(VenueReading(
                    venue=venue,
                    open_positions=int(snap.open_positions),
                    equity_usd=float(snap.equity_usd),
                    daily_pnl_usd=float(snap.daily_pnl),
                ))
```

## M-21 [HIGH] US stock-perp trading window is 11 hours wrong — `is_market_open('Stock')` reports OPEN overnight and CLOSED during the real US session, widening stops 40% and forcing limit orders on live equity-perp trades

- **Dimension**: market-data · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/order_rules.py:56-65`
- **Standard**: CLAUDE.md — 'Ask which OTHER surface makes the same claim'; a market-clock claim must agree across the surfaces that act on it. Also DST-aware session handling (docs/RUNECLAW_v3.4.0_ROADMAP.md C2-06 records this class of bug once already).

**Observed**: The gate is open 02:30–09:00 UTC (22:30–04:00 ET — the dead of night for the underlying) and closed for the entire real cash session. Whoever wrote it converted a Beijing-time listing (21:30–04:00 CST = 09:30–16:00 ET) as if those digits were US Central time, shifting the window 11 hours.

**Expected**: 9:30–16:00 ET is 13:30–20:00 UTC under EDT (14:30–21:00 under EST). `is_market_open('Stock')` should be True there and False overnight, matching `stock_trading.get_market_session` and `config.us_regular_open_hour_utc()`.

**Root cause**: A hard-coded UTC minute window derived from a mis-converted local-time listing, with no zoneinfo conversion and no cross-check against the two other modules in the same repo that already compute the session correctly.

**Business impact**: Real money on live equity-perp trades. During the actual liquid US session every stock-perp entry is treated as an off-hours weekend queue: the stop is widened by 40% (a 2% stop becomes 2.8% — 40% more loss per stopped trade), market orders are silently converted to limit orders (fills missed / entries not taken), and TP/SL are deferred until after fill, leaving the position briefly unprotected. Conversely at 03:00–09:00 UTC the bot believes the market is open and sends market orders into the thinnest overnight book, with no gap-risk widening.

**Reachability**: Reachable on the live money path: bot/core/live_executor.py:38 imports `is_market_open, is_weekend_queued, adjust_sl_for_gap_risk` and line 2892 calls `mkt_open, mkt_reason = is_market_open(asset_class)` inside the open-position routine, with `asset_class = _classify_symbol(idea.asset)` (market_scanner classifier, which returns 'Stock' for US_STOCK_SYMBOLS / STOCK_PERPETUALS / *STOCK bases). The three downstream effects (market→limit override, `adjust_size_for_weekend`, `adjust_sl_for_gap_risk`, `should_defer_tp_sl`) all read `is_weekend_queued`, defined as `not is_market_open(...)`. Note the engine's own stock gate at bot/core/engine.py:6232-6234 uses the CORRECT clock (`get_market_session`), so the two gates disagree by 11 hours inside one order flow.

**Existing tests**: grep of tests/ for `is_market_open` and `order_rules` returns nothing — the module has no test coverage at all. docs/AUDIT_REPORT_V4.md:211 ('M-04: Stock market hours check off by 30 minutes') looked at this exact function and only questioned the 30-minute rounding, never the 11-hour offset.

**Remediation**: Delete the hard-coded window and delegate to the already-correct source: `from bot.core.stock_trading import get_market_session` (or `config.us_regular_open_hour_utc()/us_regular_close_hour_utc()`), returning `session.is_regular_hours` for `_SESSION_HOURS`. Add a test asserting `is_market_open('Stock', <Tue 15:00 UTC>)[0] is True` and `... 03:00 UTC ... is False`, and that it agrees with `get_market_session(...).is_regular_hours` for 24 hourly samples — there is currently no test anywhere touching order_rules.py.

**Evidence**:

```
bot/core/order_rules.py:56-65
    if asset_class in _SESSION_HOURS:
        if weekday >= 5:
            return False, "Stock perps are closed on weekends"
        # Stock perps: 02:30 – 09:00 UTC (US market hours during EDT)
        minutes_today = now.hour * 60 + now.minute
        # Stocks: 02:30 - 09:00 UTC (9:30 AM - 4:00 PM ET during EDT)
        if 150 <= minutes_today < 540:  # 150 = 2*60+30, 540 = 9*60
            return True, ""

The same repo states the correct window twice. bot/core/stock_trading.py:83-85
    # Regular session: 13:30 - 20:00 UTC
    regular_open = 13.5   # 13:30 UTC = 9:30 ET
    regular_close = 20.0  # 20:00 UTC = 16:00 ET

and bot/config.py:1040-1041
    def us_regular_open_hour_utc() -> int:
        return _us_market_hour_utc(9, 30)
```

## M-22 [MEDIUM] Market scanner turns an unreported 24h move into a measured 0.00% — the uncured copy of the exact expression two other producers were fixed for

- **Dimension**: market-data · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/market_scanner.py:645`
- **Standard**: CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement'; the shapes table lists `float(x or 0)` as 'unreadable is break-even'. Also 'Ask which OTHER surface makes the same claim — before calling the fix done.'

**Observed**: The scanner never emits None, so it prints `◇ +0.0%` — the glyph whose comment says it means FLAT, a measured zero — and the row is counted in neither `bullish`, `bearish` nor `unread` (skill_registry.py:384-386). `_momentum_score(0.0, spike)` also returns 0.0, so the symbol sorts last on `abs(momentum_score)` and is quietly dropped from the top-movers list on the strength of a number nobody measured.

**Expected**: `MarketSignal.change_pct_24h` is `Optional[float] = None` (bot/utils/models.py:73) precisely so an unreported move can be omitted; the scan card should print `?` / `—` and count the row under `unread`.

**Root cause**: `.get(k, 0)` plus `or 0` — CLAUDE.md's banned shape twice over — in the scan-loop's primary MarketSignal producer, which the earlier sweep's source scans never covered.

**Business impact**: The scan card — the operator's first read of the market — states a 24h move for a symbol the venue reported none for, and the honest 'unread' tally it renders beside it can never fire. A symbol with an unreadable move is also silently ranked out of the top-movers list, so it is never analysed.

**Reachability**: `_process_ticker` is the only builder in `_scan_spot` (line ~546) and `_scan_futures` (line 605), i.e. every signal from `MarketScanner.scan()`. `ScanMarketSkill.execute` (skill_registry.py:335-336) renders those signals directly through `_spark`/`_chg` and stashes them on `engine._last_scan_signals`. No upstream guard filters a null percentage: only `price <= 0` and `volume < min_vol` reject a ticker.

**Existing tests**: tests/test_absent_move_is_not_a_short_setup.py and tests/test_absent_move_is_not_a_bearish_move.py exist for this exact expression, and their source scans read `bot/skills/scan_skill.py`, `bot/skills/skill_registry.py`, `bot/core/analyzer.py` and `app/public/js/app.js` — never `bot/core/market_scanner.py`. tests/test_scan_volume_floor.py touches `_process_ticker` but only for the volume floor.

**Remediation**: Use the existing helper: `change = _maybe_pct(tick.get("percentage"))` (lift `_maybe_pct` into a shared util rather than importing skills from core), keep `change_pct_24h=change`, and pass a real 0.0 only when the venue reported one. `_momentum_score` needs a matching third outcome — an unread move must not score 0.0 alongside a measured flat one. Then extend the source scan in tests/test_absent_move_is_not_a_short_setup.py to `bot/core/market_scanner.py`.

**Evidence**:

```
bot/core/market_scanner.py:644-651
        try:
            change = float(tick.get("percentage", 0) or 0)
            volume = float(tick.get("quoteVolume", 0) or 0)
            price = float(tick.get("last", 0) or 0)
        except (TypeError, ValueError):
            return None

        if price <= 0 or volume < min_vol:
            return None

The cure already exists one package over — bot/skills/skill_registry.py:98-105:
    def _maybe_pct(raw: object) -> Optional[float]:
        """A reported percentage, or None. Never a manufactured zero.
        `float(ticker.get("percentage", 0) or 0)` collapsed absent, null, empty
        string and a genuine 0.0% into one value ..."""

and the renderers have a None branch that this producer makes unreachable — skill_registry.py:81-95:
    def _spark(v: Optional[float]) -> str:
        # "◇" is FLAT — a measured zero — so it cannot also stand for "the venue
        # reported no percentage". ...
        if v is None: return "?"
    def _chg(v: Optional[float]) -> str:
        """The 24h move as text. `+0.0%` is a claim; an unread move gets none."""
        return "—" if v is None else f"{v:+.1f}%"
```

## M-23 [MEDIUM] Duel calls settle off a still-forming 1h candle, so the recorded settle price is 'the price when someone happened to load the page' — the outcome the module's own header says it exists to prevent

- **Dimension**: market-data · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `app/lib/duel_service.js:119-132`
- **Standard**: CLAUDE.md: an incomplete read must not be recorded as a measurement; and the module's own stated determinism contract.

**Observed**: Nothing anywhere requires `now >= bucket + HOUR_MS`, so for the first hour after each horizon the settle price is whichever mid-candle print the earliest page load happened to catch. Whether a player's call scores a win can depend on when someone opened the page.

**Expected**: Per the module header (app/lib/candles.js:5-11) and settleDue's own docstring: 'running this late produces the same number as running it on time' — a settle price that is a deterministic function of the timestamp. A bucket that has not closed should answer null and leave the call pending, exactly as an unreadable read does.

**Root cause**: `isDue` gates on the horizon instant, while `closeAt` addresses the containing hour bucket; the two are only equivalent once that bucket has closed, and nothing enforces the gap.

**Business impact**: A public-facing accuracy record and streak/quest state built partly from prices that are not reproducible from the public API — the exact 'silently record the price when someone happened to load the page' failure the module was written to avoid. No direct order flow.

**Reachability**: settleDue is called on ordinary page loads: duel_service.js:231 (`cardFor`) and :259 (`recordFor`), reached from app/routes/duel.js:45 and :75 and app/routes/sync.js:1245. The written price feeds `duel.pickOutcome` → `publicPick.outcome`, `accuracy`, `computeMarks`, `duelStreak` and `weeklyDuelQuests` in `recordFor`.

**Existing tests**: app/test/duel_settlement.test.js covers null-on-failure, business-error-in-200, bucket selection and 'does not depend on when settlement runs' — the last one with `setCandleFetcher(async () => [row(HORIZON, 111)])`, a fixture that returns the same close on both calls, so the real variable is removed from the test. Nothing asserts the bucket has closed.

**Remediation**: Gate on bucket completion, not on the horizon: in `settleDue`, skip a pick unless `Date.now() >= hourStart(at) + HOUR_MS` (both are already exported from candles.js), or have `closeAt` take `now` and return null for a bucket that has not ended. Then extend app/test/duel_settlement.test.js with a fetcher whose row for the current bucket CHANGES between calls and assert the two settlements agree — the present 'the answer does not depend on when settlement runs' test (lines 42-51) returns a fixed row, so it cannot see this.

**Evidence**:

```
app/lib/duel.js:348-352 — due the instant the horizon passes, at an arbitrary minute:
    function isDue(pick, now = new Date()) {
      if (!pick || !pick.resolves_at) return false;
      const t = Date.parse(pick.resolves_at);
      return Number.isFinite(t) && new Date(now).getTime() >= t;
    }

app/lib/duel_service.js:124-131 — the first read that finds it due writes the price permanently:
    const at = Date.parse(p.resolves_at);
    const close = (symbol && Number.isFinite(at)) ? await closeAt(symbol, at) : null;
    if (close != null) {
      await pool.execute(
        'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
        [close, 'settled', new Date(now).toISOString(), p.id]);

app/lib/candles.js:79-86 — closeAt returns the matching bucket's row with no test that the bucket has ENDED:
    for (const row of rows) {
      if (!Array.isArray(row) || row.length < 5) continue;
      const ts = Number(row[0]);
      if (!Number.isFinite(ts) || hourStart(ts) !== bucket) continue;
      const close = Number(row[4]);
```

## M-24 [LOW] Open-interest reads answer `oi_change_pct: 0.0` — 'OI unchanged' — on a failed fetch, an unavailable exchange, and a first-ever observation, and the consumer's `is not None` branch can never fire

- **Dimension**: market-data · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/exchange_flow.py:206-210, 228-236`
- **Standard**: CLAUDE.md: 'Unreadable is never zero'; and the guard/omit table — this is neither, it is a fabricated measurement.

**Observed**: A failed or impossible read is reported as a measured 0.0% change, and the stale `oi_usd` beside it carries no age or stale flag. `_assess_squeeze_risk` then evaluates `oi_change_pct > 5` as False, downgrading what would have been a HIGH squeeze risk to MEDIUM on the strength of a number nobody measured; the Telegram line renders '(+0.0%)'.

**Expected**: `oi_change_pct` should be None when it could not be computed — its only renderer already tests `if oi_chg is not None`, and `_assess_squeeze_risk` already types it `Optional[float]` and handles None (`oi_rising = (oi_change_pct is not None and oi_change_pct > 5)`).

**Root cause**: A zero literal used as the 'no information' value on three separate return paths, in a module whose header promises 'errors degrade to None / 0.0'.

**Business impact**: Diagnostic runs and any future consumer of `get_flow_summary` read 'open interest flat' from a fetch that failed, and a squeeze-risk assessment silently loses its OI amplifier.

**Reachability**: `get_open_interest` is reached only through `get_flow_summary` (line 285), whose non-test callers are scripts/e2e_pipeline.py:163 and scripts/live_deep_analysis.py:157, plus `ExchangeFlowProvider.format_for_telegram` (line 360), which has no caller in the tree. The engine's live trading path gets OI from bot/core/order_flow.py:507-529 instead, which correctly leaves `sig.oi_change_pct` as None when there is no previous observation. Severity is LOW for that reason — diagnostics/reporting, not the order path.

**Existing tests**: tests/test_exchange_flow_seed.py, tests/test_orderflow_gates.py and tests/test_intelligence_upgrades.py touch this module; none assert anything about `oi_change_pct` on a failure path.

**Remediation**: Return `"oi_change_pct": None` on all three paths (unavailable exchange, fetch failure, no previous observation), and add a `stale`/`oi_updated_at` field to the returned dict so a reader can tell a cached OI from a live one — the pattern bot/core/market_cap.py already uses (`stale: bool` + `get_cached(..., allow_stale=True)`).

**Evidence**:

```
bot/core/exchange_flow.py:206-210 (exchange unavailable → stale cache, change asserted as zero)
        exchange = await self._get_exchange()
        if exchange is None:
            if entry["oi_usd"] is not None:
                return {"oi_usd": entry["oi_usd"], "oi_change_pct": 0.0}
            return None

bot/core/exchange_flow.py:231-235 (fetch raised → same claim)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_open_interest(%s) failed: %s", swap, exc)

        if entry["oi_usd"] is not None:
            return {"oi_usd": entry["oi_usd"], "oi_change_pct": 0.0}

and the consumer written expecting None, in this same file's format_for_telegram:
            chg_str = f" ({oi_chg:+.1f}%)" if oi_chg is not None else ""
```

## M-25 [LOW] The exchange position-count cache is seeded `{count: 0, timestamp: 0.0}` against `time.monotonic()`, so a bot started within 30s of host boot serves a fabricated count of 0 to the max-open-positions risk gate

- **Dimension**: market-data · **Confidence**: HIGH · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/exchange_sync.py:35-39, 428-432`
- **Standard**: CLAUDE.md: 'absent is never a measurement'; and onchain.py's own comment forbidding a 0.0 sentinel against `time.monotonic()`.

**Observed**: For the first 30 seconds of host uptime the function answers 0 open positions on the exchange without asking, and `invalidate_position_count_cache()` (lines 455-458, which resets the timestamp to 0.0 specifically to 'force a fresh exchange query') is a no-op under the same condition.

**Expected**: A count that has never been fetched must not be servable as a fresh measurement; the sentinel should be None (or `-inf`), forcing the first call to hit the exchange.

**Root cause**: A zero-valued sentinel for 'never measured', compared against a clock whose origin is also near zero at boot.

**Business impact**: During the affected window the position-limit check evaluates as if the exchange account were flat, so the bot can open past its configured max_open_positions — real money, though only on a boot-time start.

**Reachability**: `get_exchange_position_count` is called at bot/core/engine.py:897 (live equity/open-count report) and bot/core/engine.py:5337, where `live_open = exchange_count + pending_count` is passed to `self.risk.evaluate(..., live_open_count=live_open)` — the max_open_positions gate. Both are in the live path (`if CONFIG.is_live()`). The precondition (process start within 30s of host boot) is narrow: a deploy onto a long-running box is unaffected, a bot launched by an init unit on a freshly booted VM/microVM is not.

**Existing tests**: tests patch `bot.core.engine.get_exchange_position_count` (tests/test_core.py:1343,1400; tests/test_per_user_live_equity.py:162,176; tests/test_audit_v7_followups.py:87) rather than exercising the cache, so the seeded-sentinel path is untested.

**Remediation**: Seed `"timestamp": None` (or `float('-inf')`) and treat it as always-expired: `ts = _position_count_cache["timestamp"]; if ts is not None and (now - ts) < _POSITION_COUNT_TTL:`. Same change in `invalidate_position_count_cache`.

**Evidence**:

```
bot/core/exchange_sync.py:35-39
    _position_count_cache: dict[str, Any] = {
        "count": 0,
        "timestamp": 0.0,
    }
    _POSITION_COUNT_TTL = 30.0  # seconds — refresh at most every 30s

bot/core/exchange_sync.py:428-432
        now = time.monotonic()

        # Return cached value if fresh enough
        if (now - _position_count_cache["timestamp"]) < _POSITION_COUNT_TTL:
            return _position_count_cache["count"]

The repo names this exact trap elsewhere — bot/core/onchain.py:204-208:
    # Radar cache shared across symbols (one pull covers every base). None
    # sentinel, NEVER 0.0 — time.monotonic() starts near zero on fresh boots.
```

## Refuted in this batch (2)

- **A WS ticker with no usable exchange timestamp is stamped with the local clock, so it can never be filtered as stale — the freshness guard's 'unreadable timestamp → treat as stale' branch is unreachable for exactly that case** — `bot/core/ws_feed.py:556-560`
  - The quotes are verbatim (bot/core/ws_feed.py:556-560, 239-246, 202-227) but the described harm does not follow. `_process_ticker` runs when a ticker message is RECEIVED (called from _handle_message at line 549), and it writes the tick once; nothing refreshes that timestamp afterwards. So `datetime.now(UTC)` there is not a fabricated measurement of an unknown quantity — it is a real, locally measured arrival time, and a tick stamped that way ages normally in `_ticks`. The guard's purpose, stated in get_prices' own docstring ('so a silently-stalled feed can't serve a stale price to stop logic'),
- **The repaint guard decides whether a candle has closed by comparing the venue's bar timestamp to the LOCAL wall clock, and fails silently in both directions under clock skew** — `bot/utils/candles.py:96-101`
  - The quote is verbatim at bot/utils/candles.py:96-101 and the docstring at 78-86 is as described, but this is not a defect in the code — it is the standard and, for the last bar, the only available test. The finding's own proposed alternative ('a bar is closed when a later bar exists') cannot apply to `ohlcv[-1]`, which is precisely the bar in question. Behaviour only changes when the host clock is skewed by more than the time remaining in the final bar, so on the 5m/15m/1h timeframes used (engine.py:4421-4425 delegates to it; live_executor.py:3475 uses '1h') a few seconds of NTP drift changes 


========================================================================

# Batch 3 — ai-injection, injection, browser-sec, honesty-py

**22 raw · 22 CONFIRMED · 0 SUSPECTED · 0 REFUTED.** Every finding in this
batch survived both adversarial verifiers — the only batch so far where
nothing was refuted.

> Recovered from the workflow journal after a worker restart swallowed the
> completion notification. The findings below were produced and verified
> normally; only the delivery was lost.


## B3-01 [HIGH] Operator DASHBOARD_TOKEN (trade-confirm / close / halt authority) is read from the URL fragment and persisted to localStorage on a page served with no CSP, no X-Frame-Options and no nosniff

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/web/dashboard.html:532-536 (and bot/web/performance_chart.html:95-99)`
- **Standard**: OWASP A05 Security Misconfiguration; CWE-522 Insufficiently Protected Credentials; CWE-1021 Improper Restriction of Rendered UI Layers.

**Observed**: The token is taken from `location.hash` (so it also lands in browser history and in any screenshot of the address bar), written to `localStorage` permanently, and the page is served with zero security headers. Any script that executes on that origin — now or after a future edit — reads the token with one `localStorage.getItem`, and there is no CSP to stop the read or the exfiltration. The page is also framable by anyone, since neither X-Frame-Options nor frame-ancestors is set.

**Expected**: An operator secret that can confirm and close live trades should not be persisted in a JavaScript-readable store, and the page holding it should carry at minimum a CSP that forbids inline script and an X-Frame-Options/frame-ancestors deny — the same treatment app/server.js:200-209 gives the user-facing app.

**Root cause**: `dashboard_server.create_app` installs only CORS and bearer-auth middleware. Security headers were added to the Express app (app/server.js:200-209), to nginx (nginx/snippets/security-headers.conf) and to dashboard_api.py (`_security_headers`, dashboard_api.py:141-144), but the aiohttp dashboard — the one surface that actually holds a money-capable secret in the browser — was never given any.

**Business impact**: The DASHBOARD_TOKEN is the single credential that authorises `/confirm` (execute a proposed trade), `/close/{symbol}` and `/halt` on api_bridge.py, plus read access to aggregate multi-user positions, equity and rejection history. Any theft of it is direct control over real-money order flow and a full read of every user's financial state.

**Reachability**: Reachable and default-on. bot/main.py:440-458 calls `create_app(engine, tg_handler=handler)` unconditionally inside `_run_all()` and binds it to `0.0.0.0:8080` by default. `handle_index` is registered at dashboard_server.py:505 (`app.router.add_get("/", handle_index)`). `auth_middleware` gates only paths starting with `/api/` (dashboard_server.py:452), so the HTML itself is served to anyone who can reach the port.

**Existing tests**: grep of tests/ and app/test/ found no test asserting security headers on the aiohttp dashboard. tests/test_nginx_security_headers.py covers nginx.conf only; app/test/csp_no_unsafe_inline.test.js covers app/server.js and app/public/*.html only. tests/test_dashboard_api_hardening.py covers dashboard_api.py path traversal, not headers on bot/web.

**Remediation**: Add a security-headers middleware to `create_app` alongside `cors_middleware` that sets `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin` on every response. Separately, stop persisting the token: hold it in a module-scoped variable for the page's lifetime (the pattern app/routes/miniapp.js already argues for at length) and clear `location.hash` with `history.replaceState` once read, so the secret does not outlive the tab or leak through history. Keep DASHBOARD_BIND_HOST guidance as-is.

**Evidence**:

```
bot/web/dashboard.html:531-536:
```js
// Auth token — read from URL hash or prompt
const _token = (new URLSearchParams(window.location.hash.slice(1))).get('token')
  || localStorage.getItem('dashboard_token')
  || prompt('Enter dashboard token:');
if (_token) localStorage.setItem('dashboard_token', _token);
const AUTH_HEADERS = _token ? {'Authorization': 'Bearer ' + _token} : {};
```
bot/web/performance_chart.html:95-99 repeats the identical block.

The page is served with no security headers at all — bot/web/dashboard_server.py:299-304:
```python
async def handle_index(request: web.Request) -> web.Response:
    """Serve the dashboard HTML."""
    html_path = pathlib.Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return web.FileResponse(html_path, content_type="text/html")
```
and the only middlewares are bot/web/dashboard_server.py:503:
```python
app = web.Application(middlewares=[cors_middleware, auth_middleware])
```
`cors_middleware` (dashboard_server.py:481-485) sets only three `Access-Control-*` headers; nothing sets Content-Security-Policy, X-Frame-Options, X-Content-Type-Options or Referrer-Policy anywhere in that file.

The secret being stored is the same `DASHBOARD_TOKEN` that authorises money-moving endpoints on the bridge — bot/web/dashboard_server.py:28 `_DASHBOARD_TOKEN: str = os.environ.get("DASHBOARD_TOKEN", "")` and api_bridge.py:704/760/1036:
```python
async def confirm_trade(req: ConfirmRequest, _token: str = Depends(require_dashboard_token), ...)
async def close_position(symbol: str, _token: str = Depends(require_dashboard_token), ...)
async def risk_halt(_token: str = Depends(require_dashboard_token), ...)
```
```

## B3-02 [MEDIUM] Raw LLM web-research HTML is injected into the dashboard with innerHTML and no sanitizer, while the identical value is tag-stripped on the Telegram path

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/public/js/dashboard.js:7311-7318`
- **Standard**: OWASP A03 Injection; CWE-79 Improper Neutralization of Input During Web Page Generation.

**Observed**: `r.data.web_html` is concatenated into a template literal and assigned to `innerHTML` unsanitized. Content that originated on third-party web pages, laundered through the model, becomes live markup in the operator's authenticated dashboard.

**Expected**: Model output rendered as HTML should pass through `sanitizeBotHtml`, the whitelist sanitizer this same file already imports and uses for `scan.key_call` at dashboard.js:4148 and that chat.js applies to every bot reply (chat.js:212, 297, 582).

**Root cause**: A second rendering path for LLM output was added without reusing the sanitizer the first path established. CLAUDE.md's own corollary — 'ask which OTHER surface makes the same claim' — is the rule that was missed: the Telegram surface got a stripper, the web surface did not.

**Business impact**: Injected markup renders inside the authenticated operator dashboard — the surface used to read risk state and reach trade controls. Even with script blocked, an attacker-authored block can impersonate engine output, fabricate a 'circuit breaker OK' style claim, or present a link the operator is invited to trust.

**Reachability**: Reachable. The button is bound at dashboard.js:7300 (`const webBtn = document.getElementById('hubResWeb')`) and the route exists at app/routes/research.js:24 (`router.post('/:symbol/web', ...)`), mounted in app/server.js. Gated to the operator: bot/web/user_gateway.py:1122-1126 returns 403 `admin_only` for non-admins, which caps who can trigger it but does not make the sink safe. Script execution is blocked by the app CSP (app/server.js:184-185: `script-src` carries no 'unsafe-inline' and no 'unsafe-hashes'), so the residual impact is HTML/UI injection rather than script execution — that mitigation is why this is MEDIUM and not HIGH.

**Existing tests**: grep of app/test/ and tests/ for `web_html` returns only tests/test_research_web_gateway.py (asserts citations ride through) and tests/test_telegram_web_parity.py:68 (`test_research_strips_web_html_to_telegram_subset`). Neither asserts anything about the web renderer; no app/test file references `hubResWeb`.

**Remediation**: Wrap the value: `<div>${sanitizeBotHtml(r.data.web_html)}</div>`. `sanitizeBotHtml` is already destructured into scope at dashboard.js:13.

**Evidence**:

```
app/public/js/dashboard.js:7311-7316:
```js
          if (r?.ok && r.data?.web_html) {
            out.innerHTML = `<div class="panel" style="background:var(--surface-2,#12151c)">
                <div class="small muted" style="margin-bottom:6px">🌐 Live web research — ${esc(sym)}${r.data.model ? ' · ' + esc(r.data.model) : ''}</div>
                <div>${r.data.web_html}</div>
                <div class="small muted" style="margin-top:8px;font-style:italic">${esc(r.data.disclaimer || '')}</div>
              </div>`;
```
Every neighbouring interpolation is wrapped in `esc(...)`; `web_html` alone is not, and `sanitizeBotHtml` (app/public/js/app.js:209, the whitelist b/i/code/pre/br sanitizer this file imports at dashboard.js:13 and uses at dashboard.js:4148) is not applied.

The value is the model's raw answer, relayed verbatim — bot/web/user_gateway.py:1146-1153:
```python
        answer, meta = await tg_handler._llm_chat(
            prompt, user_id=tg_id, is_admin=True, return_meta=True)
...
    return web.json_response({
        "read_only": True,
        "base": base,
        "web_html": answer,
```
and app/routes/research.js:33-35 simply relays it: `const r = await gateway.postGateway('/research/web', { telegram_id: ident.id, base }, 30000); return gateway.relay(res, r);`

The Telegram consumer of the same field does strip it — bot/skills/telegram_handler.py:4657-4661:
```python
    def _web_html_to_tg(s: str) -> str:
        """Web panel HTML → Telegram-safe HTML: <br> to newline, keep only
        <b>/<i>/<code>, drop everything else."""
        s = re.sub(r"<br\s*/?>", "\n", str(s or ""), flags=re.I)
        return re.sub(r"<(?!/?(?:b|i|code)>)[^>]*>", "", s)
```
```

## B3-03 [MEDIUM] CSP script-src omits accounts.google.com, so the Google Identity Services script the login page injects can never load — Google sign-in silently never appears

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/server.js:185 (policy) vs app/public/index.html:1700 (blocked script)`
- **Standard**: OWASP A05 Security Misconfiguration; CWE-16 Configuration.

**Observed**: An operator who configures Google login gets a sign-in area with a blank space where the Google button should be, and nothing anywhere reports why. This is the exact failure app/test/csp_no_unsafe_inline.test.js warns about in its own docstring — 'the server returns 200, the HTML is intact, the tests pass, and the script simply never runs' — applied to an external host rather than an inline block.

**Expected**: Either `https://accounts.google.com` is in `script-src` (and `frame-src`/`connect-src`) so Google sign-in works, or the page does not advertise a Google sign-in it cannot deliver.

**Root cause**: lib/csp.js hard-codes the external-host allowlist to `https://telegram.org` only. The guard test enumerates inline-block hashes and inline `on*` handlers but never enumerates `<script src="https://...">` (static or dynamically created) and checks it against the policy, so a second external dependency could be added with nothing noticing.

**Business impact**: One of the advertised sign-in methods is dead on arrival, with no error surfaced and no log entry. Users bounce off a blank button; the operator has no signal to diagnose from.

**Reachability**: Reachable whenever GOOGLE_CLIENT_ID is set. app/server.js:447 serves index.html at `/`, and the global header middleware at app/server.js:201-209 applies the CSP to it. The Telegram widget on the same page (index.html:1712) is unaffected because telegram.org IS allowlisted, which is why the gap is invisible in a deployment that only uses Telegram login.

**Existing tests**: app/test/csp_no_unsafe_inline.test.js asserts `src.includes('https://telegram.org')` and nothing about any other external host; it has no test that walks page script sources and checks them against the policy. No other test in app/test/ references accounts.google.com.

**Remediation**: Add `https://accounts.google.com` to `scriptSrc()` in app/lib/csp.js, and add it to `frame-src` and `connect-src` in app/server.js (GIS opens an iframe and makes XHRs to that origin). Then extend app/test/csp_no_unsafe_inline.test.js with a case that scans every served page and every file under public/js for `https://` script sources — static attributes and `.src = 'https://...'` assignments alike — and asserts each host appears in `csp.scriptSrc()`.

**Evidence**:

```
The policy, app/server.js:183-186:
```js
const CSP = [
  "default-src 'self'",
  `script-src ${require('./lib/csp').scriptSrc()}`,
```
and app/lib/csp.js:118-121 fixes the host list:
```js
function scriptSrc() {
  if (_cached === null) _cached = scriptHashes();
  return ["'self'", ...(_cached), 'https://telegram.org'].join(' ');
```
Running it confirms the shipped value: `node -e "console.log(require('./lib/csp').scriptSrc())"` yields `'self' <49 sha256 hashes> https://telegram.org` — `accounts.google.com` is absent.

The page loads exactly that host, app/public/index.html:1698-1706:
```js
  if(cfg.google_client_id){
    const s=document.createElement('script');
    s.src='https://accounts.google.com/gsi/client';s.async=true;
    s.onload=()=>{
      try{
        google.accounts.id.initialize({client_id:cfg.google_client_id,callback:handleGoogleCredential});
        google.accounts.id.renderButton(document.getElementById('google-btn'),
```
`grep -o "src=['\"]https://[^'\"]+"` across app/public/*.html and app/public/js/*.js returns exactly two external hosts: `accounts.google.com` and `telegram.org`. Only one of them is in the policy.

`frame-src https://oauth.telegram.org` (app/server.js:195) and `connect-src 'self' blob:` (app/server.js:191) likewise exclude accounts.google.com, so even a loaded GIS would be unable to open its iframe or call home.
```

## B3-04 [MEDIUM] CSP img-src blocks every remote image the dashboard renders, and the onerror fallbacks that would hide them are inline handlers CSP also blocks — unreadable images render as permanent broken tiles

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/public/js/dashboard.js:5337, 5359, 2146 (policy at app/server.js:190)`
- **Standard**: OWASP A05 Security Misconfiguration; CWE-16 Configuration. (CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement.')

**Observed**: Every remote image is blocked, and the recovery path for a blocked image is blocked too. The result is a row of broken-image icons with no explanation, and one dead click-suppression on a control that sits inside another clickable control.

**Expected**: Either the remote image hosts are in `img-src`, or the code does not attempt remote images; and in every case a failed image load resolves to the honest fallback the code already contains. Per CLAUDE.md, an unreadable source must render as an error/omission — not as a broken tile that reads as a corrupt asset.

**Root cause**: Two policies that are individually correct were never checked against the JS renderers. The inline-handler guard was written when all inline script lived in .html files and was scoped to those; `public/js/*.js` builds markup at runtime and falls entirely outside it, so three inline handlers and three remote-image dependencies landed with nothing objecting.

**Business impact**: Cosmetic on its own, but it is the repo's stated failure mode in miniature: a failed read rendering as a broken artifact rather than an honest absence, and a control (stopPropagation on the receipt link) that is present in source and never reached — the #999 shape CLAUDE.md documents.

**Reachability**: All three sites are reachable. `nftCard` is called from `grid()` at dashboard.js:5342 inside the collectibles panel, backed by app/routes/web3.js:37 (`GET /api/web3/collectibles`) which is mounted in app/server.js. The ENS block is in the same identity panel. The signals table renderer around dashboard.js:2140-2156 is the main dashboard signals view. The header middleware at app/server.js:201-209 applies the CSP to every response including these pages.

**Existing tests**: app/test/csp_no_unsafe_inline.test.js is the only guard and, as quoted above, it scans `.html` files only — I confirmed by reading `htmlFiles()` at lines 63-72 and the `for (const file of PAGES)` loop in 'no served page carries an inline event handler'. No test in app/test/ asserts anything about img-src, and app/test/opensea_nft.test.js / web3_worlds.test.js contain no reference to rendering or CSP.

**Remediation**: Two independent fixes. (a) Replace the three inline `on*` attributes with delegated listeners or with a `querySelectorAll('img').forEach(el => el.onerror = ...)` pass after the innerHTML assignment — the same conversion index.html already made. (b) Decide the image policy deliberately: either widen `img-src` to the specific hosts (`https://i.seadn.io` and the ENS avatar gateway) or proxy them same-origin; leaving it as-is means the NFT and ENS panels can never show an image. Then extend app/test/csp_no_unsafe_inline.test.js's `PAGES` walk to include `public/js/**/*.js` so the next inline handler fails loudly.

**Evidence**:

```
Policy — app/server.js:190: `"img-src 'self' data: blob:",` (no remote host, no `https:`).

Renderers that need remote images — app/public/js/dashboard.js:5336-5337:
```js
      const img = it.image_url
        ? `<img src="${esc(it.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none';this.parentElement.classList.add('nft-noimg')" ...>`
```
and dashboard.js:5358-5359:
```js
      const avatar = d.avatar
        ? `<img src="${esc(d.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(document.createTextNode('🧑‍🚀'))" ...>`
```
The `referrerpolicy="no-referrer"` on both proves remote origins were intended. They are: app/lib/opensea.js:116 `image_url: typeof n.image_url === 'string' ? n.image_url : null,` (OpenSea CDN URLs) and app/lib/ens.js:68 `if (name) avatar = await p.getAvatar(name).catch(() => null);` (an https/IPFS-gateway URL). dashboard.js:4487 does the same for `p.minted_image`.

The fallbacks are inline event handlers, which the same policy forbids — app/server.js:184-185 builds `script-src` from app/lib/csp.js, whose own header states: 'Inline event handlers (`onclick="..."`) are not reachable by hash at all — they need `'unsafe-hashes'`, which re-opens a weaker version of the same hole.'

A third inline handler is dead the same way — dashboard.js:2146:
```js
${s.seal ? ` · <a href="/call/${encodeURIComponent(s.signal_key)}" title="Cryptographically sealed at decision time — verify in your browser" onclick="event.stopPropagation()">🔏 verify</a>` : ''}
```
inside a `<td ... role="button" tabindex="0" ... style="cursor:pointer">` (dashboard.js:2142-2144) that carries `data-geo`/`data-sym` for the chart handler.

The guard that should have caught all three scans only HTML — app/test/csp_no_unsafe_inline.test.js:66-72 builds `PAGES` from `htmlFiles(csp.PUBLIC_DIR)` filtered on `e.name.endsWith('.html')`, and the inline-handler test iterates `for (const file of PAGES)`. public/js/*.js is never read.
```

## B3-05 [LOW] Permissions-Policy is set nowhere in the stack, on an app that uses the Web Speech API

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/server.js:201-209`
- **Standard**: OWASP A05 Security Misconfiguration; CWE-693 Protection Mechanism Failure.

**Observed**: No Permissions-Policy on any response from any of the four HTTP surfaces.

**Expected**: A deny-by-default Permissions-Policy naming the features the app does not use, so an injected or framed context cannot silently reach camera, geolocation, payment or USB.

**Root cause**: The header set was chosen before Permissions-Policy was widely supported and never revisited; tests/test_nginx_security_headers.py's REQUIRED_HEADERS list (lines 48-54) codifies the same five headers, so the omission is pinned rather than noticed.

**Business impact**: Defence in depth only. It does not create an exposure by itself; it removes one layer that would blunt a future injection or a framing bug on the embed/miniapp surfaces, which deliberately allow `frame-ancestors *`.

**Reachability**: The middleware at app/server.js:201 runs on every request (it is registered before the static handler and every router), so the omission is universal rather than route-specific.

**Existing tests**: tests/test_nginx_security_headers.py:48-54 lists the five headers the snippet must define; Permissions-Policy is not among them, and no app/test file asserts any response header.

**Remediation**: Add `res.setHeader('Permissions-Policy', 'camera=(), geolocation=(), payment=(), usb=(), microphone=(self)')` to app/server.js's middleware, mirror it in nginx/snippets/security-headers.conf, and add the name to REQUIRED_HEADERS in tests/test_nginx_security_headers.py so the two agree (that file's own docstring makes agreement the contract).

**Evidence**:

```
app/server.js:201-209 is the complete header middleware:
```js
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('Strict-Transport-Security', 'max-age=15552000');
  next();
});
```
`rg -n "Permissions-Policy|Feature-Policy" -g '!node_modules' .` over the whole repository returns nothing — it is absent from app/server.js, from nginx/snippets/security-headers.conf, from dashboard_api.py's `_security_headers` (dashboard_api.py:141-144) and from bot/web/dashboard_server.py.

The app does use a permission-gated API — app/public/js/chat.js:364:
```js
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
```
(mic dictation, chat.js:356) plus `navigator.clipboard.writeText` (app/public/trader.html:158, app/public/index.html:1574).
```

## B3-06 [LOW] api_bridge serves the whole marketing site with no security headers at all; dashboard_api serves static HTML with three headers but no CSP

- **Dimension**: browser-sec · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `api_bridge.py:1080 (and dashboard_api.py:141-144, 185-193)`
- **Standard**: OWASP A05 Security Misconfiguration; CWE-1021 Improper Restriction of Rendered UI Layers; CWE-693.

**Observed**: Two of the three Python HTTP surfaces serve HTML with a weaker header set than the nginx path, and one serves it with none.

**Expected**: The same page should carry the same headers regardless of which server hands it out — that is the property tests/test_nginx_security_headers.py exists to defend one layer up.

**Root cause**: Header policy was implemented per-server rather than as a shared decision. api_bridge's static mount was added as a catch-all (api_bridge.py:1078-1080, 'This must be LAST so API routes take priority') and never given the middleware treatment the Express app and nginx received.

**Business impact**: The affected content is static marketing and the archived submission — no session, no user data, no actions — so the practical impact is framing/sniffing of public pages. The real cost is the inconsistency: a reader auditing nginx.conf reasonably concludes the site is covered, and it is only covered on one of three paths.

**Reachability**: api_bridge.py:1078 guards the mount with `if os.path.isdir(_WEBSITE_DIR)` and `website/` exists in the checkout, so the mount is active. Whether it is internet-facing depends on deployment: api_bridge.py's `__main__` block comments that it binds loopback by default, and docker-compose.yml:170 mounts `./website` into nginx, so the production path is nginx. That is why this is LOW and not higher — the exposure is limited to deployments that reach api_bridge or dashboard_api directly.

**Existing tests**: tests/test_nginx_security_headers.py covers nginx.conf only. tests/test_dashboard_api_hardening.py covers path traversal in dashboard_api.py's static handler, not headers. No test asserts headers on api_bridge responses.

**Remediation**: Add a small FastAPI middleware to api_bridge.py setting the same five headers the nginx snippet defines, and add a `Content-Security-Policy` line to dashboard_api.py's `_security_headers`. Note that `dashboard_static/index.html` carries an inline `<script>` (its `/platform-url` fetch, lines 96-110), so a `script-src 'self'` policy on that path must either hash that block or the block must move to an external file — otherwise the platform CTA button silently never appears, which is the same class of defect as the accounts.google.com finding.

**Evidence**:

```
api_bridge.py:1080:
```python
    app.mount("/", StaticFiles(directory=_WEBSITE_DIR, html=True), name="website")
```
`grep -n "Content-Security-Policy|X-Frame-Options|Referrer-Policy|X-Content-Type-Options" api_bridge.py` returns nothing — the only middleware added is CORSMiddleware at api_bridge.py:357-363. Every byte of `website/` (index.html, privacy.html, the hackathon archive) therefore goes out with no CSP, no X-Frame-Options and no nosniff when this process serves it directly.

dashboard_api.py does better but still has no CSP — dashboard_api.py:141-144:
```python
    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
```
called from `_serve_static` (dashboard_api.py:186) which serves `dashboard_static/` and `website/`.

The same content served through nginx does get the full set — nginx/snippets/security-headers.conf defines all five including `Content-Security-Policy "default-src 'self'; script-src 'self'; ..."`. So the protection depends entirely on which front door a given deployment uses.
```

## B3-07 [MEDIUM] Authenticated SSRF: web-push subscription endpoint is an unvalidated attacker-supplied URL the server later POSTs to

- **Dimension**: injection · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/routes/push.js:45-50 (sink at app/lib/push.js:80 -> app/lib/push.js:35-36)`
- **Standard**: CWE-918 (Server-Side Request Forgery) / OWASP A10:2021 — Server-Side Request Forgery

**Observed**: Only `startsWith('https://')` and a 500-char cap. Any host, any port, any path is accepted and is subsequently dereferenced by the server on every broadcast until pruned.

**Expected**: The endpoint should be constrained to the known push-service hosts a real browser PushManager can produce (fcm.googleapis.com / updates.push.services.mozilla.com / *.notify.windows.com / web.push.apple.com, plus any operator-configured self-hosted service), and non-public IP literals refused, before the row is stored.

**Root cause**: The route treats `subscription.endpoint` as if it were browser-attested data, but it arrives in a plain JSON request body from the client, so it is fully user-controlled. `web-push` deliberately does no host validation (it cannot know the operator's push service), so there is no second line of defence anywhere on this path.

**Business impact**: This host runs beside the money path: the same box reaches the bot gateway, the operator's internal services and whatever else sits on its network. A registered user — free-tier, paper-only, no operator approval — can make the platform issue authenticated-looking TLS requests to internal addresses and enumerate which internal https endpoints exist via the 404/410 prune oracle. It is blind (no response body is returned to the attacker), which is what keeps this below CRITICAL, but it is a reconnaissance and request-forwarding primitive inside the trust boundary of a system that holds exchange credentials and signing keys. It also allows a subscriber row to be pointed at an attacker-run collector, which then receives the VAPID JWT and the encrypted broadcast payload for every public feed event.

**Reachability**: Reachable and exercised. The write path is `router.post('/subscribe')` behind `authMiddleware` only (app/routes/push.js:21) — no operator/admin gate. The read/send path `notifySubscribers` is called from seven production modules listed above, including the bot sync ingest, so an attacker's row is dereferenced by ordinary system activity without any further attacker action. There is no upstream guard: I grepped app/routes and app/lib for any endpoint host allowlist and found none, and the library itself (app/node_modules/web-push/src/web-push-lib.js:274, 348-369) parses and uses the URL directly. The one precondition is that the operator has configured VAPID keys (`configured` at app/lib/push.js:24-30); with no keys the route returns 503 and the whole module is a no-op, so this is a defect only on deployments that have push enabled.

**Existing tests**: app/test/push.test.js is the only test covering this route. Its negative case is `body: { subscription: { endpoint: 'http://evil', keys: {} } }` (app/test/push.test.js:84) — it pins that plain `http://` is rejected, and nothing more. Its fixtures use `https://push.example/${n}` (line 65), i.e. an arbitrary non-push-service https host, which the suite asserts is ACCEPTED. So no existing test pins a host allowlist, and the current tests would need one line changed if one is added.

**Remediation**: Validate the endpoint at app/routes/push.js:45 before the INSERT: `new URL(endpoint)`, require `protocol === 'https:'`, require the hostname to be in an allowlist of push-service suffixes (make it configurable via an env var so self-hosted services still work), and reject hostnames that parse as IP literals or resolve into private/link-local/loopback ranges. Keep the existing length cap. This is a few lines in one route and touches no ratchet baseline. Optionally also stop the 404/410 prune from being observable by an unauthenticated-to-that-endpoint party — but fixing the allowlist removes the oracle's value.

**Evidence**:

```
app/routes/push.js:44-50 —
```js
    const sub = (req.body || {}).subscription || {};
    const endpoint = String(sub.endpoint || '');
    const keys = sub.keys || {};
    if (!endpoint.startsWith('https://') || endpoint.length > MAX_ENDPOINT_LEN
        || !keys.p256dh || !keys.auth) {
      return res.status(400).json({ error: 'invalid subscription' });
    }
```
app/lib/push.js:35-36 (the transport) —
```js
let sender = (subscription, payload) =>
  webpush.sendNotification(subscription, payload, { TTL: 3600 });
```
app/lib/push.js:79-85 (the call, per stored row) —
```js
      await sender({ endpoint: row.endpoint, keys }, body);
      sent++;
    } catch (err) {
      const code = err && (err.statusCode || err.status);
      if (code === 404 || code === 410) await prune(row.endpoint);
```
And the library dereferences the URL verbatim — app/node_modules/web-push/src/web-push-lib.js:348-369:
```js
      const urlParts = url.parse(requestDetails.endpoint);
      httpsOptions.hostname = urlParts.hostname;
      httpsOptions.port = urlParts.port;
      httpsOptions.path = urlParts.path;
...
      const pushRequest = https.request(httpsOptions, function(pushResponse) {
```
```

## B3-08 [MEDIUM] Web chat computes the Guardian prompt-injection verdict and then discards it — defang_if_flagged is wired on Telegram only, so hidden-character smuggling and fake `Assistant:` role turns reach the LLM prompt intact on the surface that can dispatch skills and propose trades

- **Dimension**: ai-injection · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/web/user_gateway.py:307-327, 555-558`
- **Standard**: OWASP GenAI LLM01 (Prompt Injection); repo rule in bot/guardian/firewall.py:190-197 that a detector's finding must reach the prompt, not just telemetry.

**Observed**: Only Telegram hardens. On the web path the zero-width character and the `Assistant:` role turn survive verbatim into the LLM prompt, because bot/nlp/sanitize.py's INJECTION_PATTERNS covers `system\s*:` but not `assistant:` and has no hidden-character rule (tests/test_firewall_hardens_the_prompt.py:36-46 asserts exactly that gap as the measurement the fix was built on).

**Expected**: Both chat transports harden the prompt with the verdict the firewall just produced, as bot/guardian/firewall.py:190-225 documents ('Detection that alters nothing is telemetry, not a control') and as the user_gateway comment at line 307-309 promises.

**Root cause**: Two dispatch sites for one behaviour. defang_if_flagged was added to the Telegram handler and its wiring test (tests/test_firewall_hardens_the_prompt.py, class TestItIsActuallyReached) source-scans bot/skills/telegram_handler.py only — so the second surface that makes the same claim was never checked. This is the exact failure mode CLAUDE.md names ('Ask which OTHER surface makes the same claim — before calling the fix done').

**Business impact**: Defence-in-depth, not a boundary — bot/nlp/sanitize.py and firewall.py both state that LLM chat output has no execution authority and trades still pass confirm_trade -> compliance -> executor. The realistic impact is that the web agent, which can dispatch skills and propose trades, can be steered by smuggled instructions (a pasted block from a website, a forwarded message) with the hardening the operator believes is running silently absent on that surface, and the tamper-evident chain records a verdict that changed nothing.

**Reachability**: handle_chat is registered on the gateway router and is the target of app/routes/chat.js POST /api/chat (authMiddleware + per-user rate limit), i.e. the primary product surface for signed-in web users. The path reached is the LLM-chat fallback at line 555, which runs whenever the intent router does not match at >=0.8 confidence — the ordinary case for free-form text. The firewall block at line 315-316 is gated on CONFIG.risk.guardian_firewall_block_high, so by default nothing is refused either.

**Existing tests**: tests/test_firewall_hardens_the_prompt.py exists and is thorough, but every wiring assertion in TestItIsActuallyReached reads bot/skills/telegram_handler.py. grep of tests/ and app/test/ for 'defang' returns only that file. No test covers the web path.

**Remediation**: In bot/web/user_gateway.py::handle_chat, initialise `fw_verdict = None` before the try at line 313, and at line 555 replace `sanitize_chat_input(text)` with `sanitize_chat_input(defang_if_flagged(text, fw_verdict)[0])` — keeping `text` itself untouched so the intent router and trade intercepts still see what the user typed (the same split telegram_handler.py:2894-2899 makes). Do the same for the vision path (line 347) and consider it for handle_public_chat (line 622), which has no firewall scan at all. Extend tests/test_firewall_hardens_the_prompt.py::TestItIsActuallyReached to source-scan user_gateway.py so the third surface cannot repeat this.

**Evidence**:

```
bot/web/user_gateway.py:307-314 — the verdict is computed and the comment states the web path is equivalent to Telegram:

    # Guardian firewall pre-scan — the web chat can ACT (propose trades,
    # dispatch skills) exactly like Telegram, so the same input-provenance gate
    # applies here. ...
    try:
        fw_verdict = engine.firewall_scan(text, source="web", user_id=tg_id)

bot/web/user_gateway.py:555-558 — the verdict is never used again; the raw text goes straight to the thin denylist:

    answer, meta = await tg_handler._llm_chat(
        sanitize_chat_input(text), user_id=tg_id, user_name=name,
        is_admin=_is_admin,
        profile_note=profile_note, reply_lang=reply_lang, return_meta=True)

bot/skills/telegram_handler.py:2897-2900 — the ONLY production caller of the hardening step:

        from bot.guardian.firewall import defang_if_flagged
        _prompt_text, _ = defang_if_flagged(text, fw_verdict)
        answer = await self._llm_chat(
            _sanitize_chat_input(_prompt_text), user_id=tg_id, ...)

`grep -rn 'defang_if_flagged' --include=*.py` outside tests returns exactly bot/guardian/firewall.py (the definition) and bot/skills/telegram_handler.py:2897-2898. Nothing in bot/web/user_gateway.py.
```

## B3-09 [MEDIUM] MCP argument validator applies a blanket 200-character cap to every string, silently breaking all four public Guardian security tools and contradicting the inputSchema they advertise (`maxLength: 100000`)

- **Dimension**: ai-injection · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/routes/mcp.js:1196`
- **Standard**: OWASP GenAI LLM06 (Excessive Agency — tool contract must match declared schema); MCP tools/list inputSchema is the caller's contract.

**Observed**: Every string argument on every tool is rejected above 200 characters with a JSON-RPC -32602. scan_transaction ('Paste anything the agent is about to act on — a message, a token name or metadata, a URL, an address, a signing request'), xray_transaction (multicall/permit calldata), compile_intent (a mandate) and plan_escape/stress_portfolio's nested `asset`/`where` strings are all constrained to 200 chars, so realistic payloads — the ones the tools exist to scan — cannot be submitted at all.

**Expected**: validateArgs honours each field's declared `maxLength` (or at least the handler's own cap), so scan_transaction accepts up to 20 000 characters and xray_transaction accepts up to 100 000 as advertised in tools/list.

**Root cause**: validateArgs was written as a minimal shape checker ('object shape, known keys, primitive types, string caps' — its own docblock at app/routes/mcp.js:1170-1176) with a hardcoded 200 constant, and was added after the tools; it never consults `spec.maxLength`. The per-tool tests exercise the handlers directly (app/test/mcp_guardian_tools.test.js:51 calls `TOOLS.scan_transaction.handler({...})`), bypassing validateArgs entirely, so the divergence is invisible to the suite.

**Business impact**: The four Guardian tools are the product's public agent-safety offering, advertised in the MCP initialize instructions and in the ERC-8257 on-chain manifest. An external agent calling scan_transaction on a real signing request (which is routinely >200 chars) gets a protocol error instead of a scan; the intended pre-signature safety check simply does not run, and the agent may proceed unscanned.

**Reachability**: app/routes/mcp.js:1204-1226 mounts POST /mcp publicly (per-IP rate limit only, no auth for the read/computesOnInput family). handleRpc -> tools/call -> validateArgs at line 1145 runs before every handler, so every real MCP client hits it. Confirmed by executing handleRpc directly above.

**Existing tests**: grep of app/test/ for 'validateArgs' and 'too long (200' returns nothing. app/test/mcp_guardian_tools.test.js, app/test/scan_seal.test.js and app/test/tool8257_families.test.js all invoke `TOOLS.<name>.handler(...)` directly, so none of them pass through the validator.

**Remediation**: In validateArgs, use `const cap = Number.isInteger(spec.maxLength) ? spec.maxLength : 200;` and compare against that. Add `maxLength` to scan_transaction.text (20000) and compile_intent.mandate (4000) so the published schema states the real bound. Add a test that drives a >200-char payload through `handleRpc` (not the handler) for each computesOnInput tool — the existing tests call handlers directly and cannot see this.

**Evidence**:

```
app/routes/mcp.js:1193-1197 — the blanket cap, which reads no per-field maxLength:

    if (spec.type === 'string') {
      if (typeof v !== 'string') return `${k} must be a string`;
      if (v.length > 200) return `${k} too long (200 max)`;
    } else if (spec.type === 'number' || spec.type === 'integer') {

app/routes/mcp.js:153 — the schema the server publishes for the same field:

        data: { type: 'string', maxLength: 100000, description: 'The transaction calldata, 0x-hex.' },

app/routes/mcp.js:104 — scan_transaction's own handler cap, three orders of magnitude larger:

      const t = String(text == null ? '' : text).slice(0, 20000);

app/routes/mcp.js:275 — compile_intent's handler cap:

      const m = String(mandate == null ? '' : mandate).slice(0, 4000);
```

## B3-10 [MEDIUM] Every free-text prompt is silently truncated to 500 characters after the surface has accepted 2000 — the model answers a quarter of the question, and Contract Studio drafts Solidity from a quarter of the spec, with no truncation notice anywhere

- **Dimension**: ai-injection · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/nlp/sanitize.py:35, 44-47`
- **Standard**: OWASP GenAI LLM05 (Improper Output Handling); repo rule 'a partial total, printed as whole' (CLAUDE.md shapes table) and skill_memory.py's announced-truncation precedent.

**Observed**: A 2000-character web chat message, a 4096-character Telegram message, and a 2000-character Contract Studio spec are all cut to 500 characters before the model sees them. The response is generated from the fragment and returned with no marker, so it reads as an answer to the whole question. For Contract Studio the user receives a Solidity draft (plus a compile and, for the operator, a testnet deploy path) built from the first quarter of their requirements.

**Expected**: Either the accepted length and the prompted length agree, or the truncation is announced — the same rule bot/nlp/skill_memory.py:32-38 already applies on the memory side ('Truncation is ANNOUNCED rather than silent. A long scan card cut at a fixed length and presented whole is a partial printed as a total').

**Root cause**: MAX_CHAT_INPUT_LEN was chosen inside the sanitizer as an injection-surface bound and never reconciled with the per-surface length limits that were set independently four times (user_gateway._MAX_TEXT_LEN, chat.js, public_chat.js). Truncation is applied silently and after the denylist substitution, so nothing downstream can tell a short question from a cut one.

**Business impact**: A trader pasting a position description, a multi-part question, or a contract spec gets a confident answer to a fragment. In Contract Studio the artefact is code the user can compile and (as operator) deploy to testnet, generated from requirements that were dropped without notice — the highest-cost instance of the repo's own 'partial printed as whole' rule.

**Reachability**: sanitize_chat_input is called on every LLM chat path: bot/web/user_gateway.py:556 (authed web chat), :623 (public anonymous chat), :672 (contract studio), :338 (vision), and bot/skills/telegram_handler.py:2899 (Telegram free text). All are live product surfaces.

**Existing tests**: tests/test_audit_v5_followup_telegram.py:61 asserts the truncation happens (`out = _sanitize_chat_input(long_text)`), i.e. the 500 cap is pinned as intended behaviour. No test anywhere asserts that the caller is told, or that the surface limit and the prompt limit agree.

**Remediation**: Return the truncation flag from sanitize_chat_input (e.g. `(text, truncated: bool)` or expose `sanitize_chat_input_ex`) and have callers either reject over-length input at the surface with the real limit, or append an explicit marker to the prompt ('[the user's message was cut here at 500 of N characters]') and surface a notice in the reply. For Contract Studio, do not truncate the spec at 500 at all — align the cap with _MAX_TEXT_LEN, since a drafting spec is exactly the payload that needs length.

**Evidence**:

```
bot/nlp/sanitize.py:35,38-47:

    MAX_CHAT_INPUT_LEN = 500

    def sanitize_chat_input(text: str) -> str:
        """Sanitize free-form user text before sending to LLM.
        - Strips prompt-injection patterns FIRST
        - Then truncates to 500 characters
        """
        sanitized = INJECTION_PATTERNS.sub("[filtered]", text)
        truncated = sanitized[:MAX_CHAT_INPUT_LEN]
        return truncated.strip()

The surfaces that feed it accept four times as much, and say so:
bot/web/user_gateway.py:53  `_MAX_TEXT_LEN = 2000`
bot/web/user_gateway.py:300 `if len(text) > _MAX_TEXT_LEN: return web.json_response({"error": "message too long"}, status=400)`
app/routes/public_chat.js:31 `const MAX_TEXT_LEN = 2000;`

And the Contract Studio path, where the loss is largest:
bot/web/user_gateway.py:644,672:

    if len(spec) > _MAX_TEXT_LEN:
        return web.json_response({"error": "spec too long"}, status=400)
    ...
    prompt = build_generation_prompt(sanitize_chat_input(spec), license=lic,
                                     pragma=pragma)
```

## B3-11 [MEDIUM] Free-tier chat quota deliberately exempts skill intents, but the analyze_asset intent runs a billable LLM thesis on the engine's SHARED daily call/dollar budget — one free web account can drive the autonomous trading brain to the rule engine for the rest of the day

- **Dimension**: ai-injection · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/web/user_gateway.py:446, 514-520`
- **Standard**: OWASP GenAI LLM10 (Unbounded Consumption) / denial-of-wallet and denial-of-service on a shared model budget.

**Observed**: The chat quota is applied only to the LLM-chat fallback. The analyze_asset intent — the single most LLM-expensive skill in the registry — is on the exempt side, and it debits the same `Analyzer._llm_calls_today` and `CostTracker.llm_cost_usd` counters the autonomous scan cycle depends on. Once exhausted, every autonomous thesis for the rest of the UTC day returns `source="RULE_ENGINE_BUDGET"`.

**Expected**: Any chat-initiated path that can trigger a billable LLM call is metered against that user, or at minimum the user-invoked and autonomous budgets are separated so a chat user cannot exhaust the trading engine's analysis budget.

**Root cause**: The quota was scoped to 'the operator-funded xAI Grok budget' (chat) and the exemption list was written in terms of transports ('skill/news/trade intents above are free') rather than in terms of which paths spend LLM tokens. analyze_asset is both a skill intent and an LLM caller, so it fell into the gap.

**Business impact**: A single free-tier account, or an automation loop, can exhaust LLM_DAILY_LIMIT (500) or LLM_DAILY_BUDGET_USD ($1.00) and force every subsequent autonomous trade thesis to RULE_ENGINE_BUDGET for the remainder of the UTC day — degrading the quality of live trading decisions on real money, silently, from an unprivileged surface.

**Reachability**: Confirmed the full chain by reading each hop: chat.js POST / -> gateway.postGateway('/chat') -> handle_chat line 424-446 -> AnalyzeAssetSkill.execute (skill_registry.py:406) -> engine._analyze_signal (engine.py:4881) -> analyzer.analyze -> _llm_thesis (analyzer.py:3873). The `background` flag is False on this path, so the LLM_BACKGROUND_SCANS throttle at analyzer.py:3945-3955 does not apply — the call is a real LLM call. _web_skill_denied only checks role permission and the (default-off) token tier gate.

**Existing tests**: grep of tests/ and app/test/ for chat_quota shows the quota is tested for the chat fallback only; no test asserts that an LLM-spending skill intent is metered. bot/web/chat_quota.py is consumed at user_gateway.py:519 and :661 (contract studio) and nowhere on the skill-dispatch branch.

**Remediation**: Either (a) consume a quota unit for LLM-spending skill intents (analyze_asset, and the scan_* aliases where they reach the analyzer) before dispatch at bot/web/user_gateway.py:424, or (b) give user-invoked analyses their own budget counter in bot/core/analyzer.py so `_llm_calls_today` for the autonomous cycle cannot be drained from chat. (b) is the safer one for the money path: the trading brain should never lose its LLM because a chat user was busy. Mirror whichever is chosen on the Telegram free-text dispatch, which has the same shape.

**Evidence**:

```
bot/web/user_gateway.py:513-520 — the quota's own scope note, and where it is consumed:

    # Fallback: LLM chat — same append-around-call pattern as _handle_message.
    # Free-tier chat quota: bound the operator-funded xAI Grok budget. Only the
    # LLM fallback (this path) consumes a "question" — skill/news/trade intents
    # above are free. ...
    _is_admin = _is_admin_id(tg_handler, tg_id)

bot/web/user_gateway.py:446 — the exempted dispatch, reached at intent confidence >= 0.8:

                result = await skill.execute(engine, user_id=tg_id, **intent.kwargs)

bot/skills/skill_registry.py:500-503 — analyze_asset goes straight into the engine's analysis pipeline:

        idea = await engine._analyze_signal(sig, is_admin=kwargs.get("is_admin", False),
                                            user_id=kwargs.get("user_id"),
                                            user_tier=kwargs.get("user_tier"))

bot/core/analyzer.py:3983-3996 — the budget guards that path shares with the autonomous engine:

        if today != self._llm_day:
            self._llm_day = today
            self._llm_calls_today = 0
        if self._llm_calls_today >= CONFIG.llm.daily_call_limit:
            audit(trade_log, f"LLM daily budget exhausted ({self._llm_calls_today} calls), using rules",
                  action="analyze", result="LLM_BUDGET")
            result = self._rule_based_thesis(signal, indicators)
            ...
            result["source"] = "RULE_ENGINE_BUDGET"

bot/config.py:1132,1139 — the caps are global, not per user:

    daily_call_limit: int = int(_env_float("LLM_DAILY_LIMIT", 500))
    daily_budget_usd: float = _env_float("LLM_DAILY_BUDGET_USD", 1.0)
```

## B3-12 [LOW] LLM shadow A/B makes billable model calls that no budget or cost counter ever sees, and its in-flight cap is read before the counter it guards is incremented, so a concurrent analysis batch can exceed it several-fold

- **Dimension**: ai-injection · **Confidence**: HIGH · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/llm/shadow_eval.py:96-111, 118-140`
- **Standard**: OWASP GenAI LLM10 (Unbounded Consumption); repo rule that measured spend must be recorded (bot/llm/usage.py header: 'llm_complete threw response.usage away and returned the text').

**Observed**: The bound is advisory only under concurrency, and shadow spend is invisible to both accounting systems: `bot.llm.usage` (never called, because llm_complete is bypassed) and `engine.cost` / `Analyzer._llm_calls_today` (never called). A shadow provider is therefore billed with no cap and no visibility.

**Expected**: The in-flight counter is incremented at spawn time (in maybe_spawn, before create_task) so the documented bound holds, and shadow token spend is recorded so `/costs` and the daily budget guards see it — the module's own header promises 'fire-and-forget with a bounded in-flight count'.

**Root cause**: The counter guarding the spawn decision lives inside the coroutine being spawned, and the shadow path was written against the raw SDK clients rather than the shared llm_complete helper that carries the usage recording.

**Business impact**: When an operator enables shadow A/B to evaluate the in-house runeclaw model, the resulting spend does not appear in /costs and does not count toward LLM_DAILY_BUDGET_USD, so the configured budget stops being the real bound — the same class of gap that LLM_FALLBACK_COST_ACCOUNTING was added to close for the fallback chain (bot/config.py:1145-1157).

**Reachability**: maybe_spawn is called from bot/core/analyzer.py:4348-4352 on every successful primary thesis (inside `if as_of is None`). It is a no-op unless LLM_SHADOW_ENABLED is truthy AND LLM_SHADOW_PROVIDER is set (bot/llm/shadow_eval.py:48-52), so it is default-off — which is why this is LOW rather than higher.

**Existing tests**: grep of tests/ for shadow_eval shows tests covering load_records / score_against_trades / format_ab_html (the scoring half). No test drives maybe_spawn concurrently or asserts the in-flight bound or any cost recording.

**Remediation**: Increment `self._in_flight` in maybe_spawn immediately before `loop.create_task(...)` and drop the increment from `_run` (keep the decrement in `_run`'s finally, guarded so a task that never starts still releases). Add `from bot.llm import usage as _usage; _usage.record_from_response(cfg.model, resp)` after each create call, or route the call through `llm_complete` which already does it.

**Evidence**:

```
bot/llm/shadow_eval.py:96-105 — the cap is checked in maybe_spawn:

        try:
            if not _enabled() or self._in_flight >= _MAX_IN_FLIGHT:
                return
            sample = float(os.environ.get("LLM_SHADOW_SAMPLE_PCT", "100") or 100)
            if sample < 100 and random.random() * 100 >= sample:
                return
            loop = asyncio.get_running_loop()
            loop.create_task(self._run(analyzer, prompt, prompt_hash,
                                       symbol, dict(primary)))

bot/llm/shadow_eval.py:108-111 — but incremented only once the task actually starts:

    async def _run(self, analyzer, prompt: str, prompt_hash: str,
                   symbol: str, primary: dict) -> None:
        self._in_flight += 1
        t0 = time.monotonic()

bot/llm/shadow_eval.py:122-140 — the calls are made directly on the SDK client, with no cost/usage recording anywhere in the method:

                resp = await asyncio.wait_for(
                    client.messages.create(
                        model=cfg.model, max_tokens=512, system=sys_content,
                        messages=[{"role": "user", "content": prompt}]),
                    timeout=25)
                ...
                resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=cfg.model, ... max_tokens=512),
                    timeout=25)

Compare bot/llm/provider.py:1251-1256 and 1279-1284, where every llm_complete call does `_usage.record_from_response(config.model, response)` — shadow_eval bypasses llm_complete entirely, and never calls `self._cost.record_llm` either.
```

## B3-13 [LOW] Raw LLM output is interpolated unescaped into a Telegram parse_mode="HTML" message with no plain-text fallback, so an ordinary model phrase containing '<' (e.g. 'RSI < 30') can drop the entire /scan deepall result

- **Dimension**: ai-injection · **Confidence**: HIGH · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/skills/scan_skill.py:1201-1206, 1231-1233`
- **Standard**: OWASP GenAI LLM05 (Improper Output Handling) — treating model output as trusted markup for a downstream renderer.

**Observed**: An unescaped '<' or '&' in the summary makes msg.edit_text raise; the exception propagates out of _scan_batch with no local handler, so the user's /scan deepall produces no result card at all — the scan ran, cost the LLM call, and rendered nothing.

**Expected**: Model text is HTML-escaped (or the send is wrapped with the same plain-text fallback TelegramHandler._send already implements) before being placed in a parse_mode="HTML" message.

**Root cause**: scan_skill.py builds and sends its own Telegram messages instead of going through TelegramHandler._send, and therefore inherits neither its redaction chokepoint nor its HTML-parse fallback. Model output is the one value in the message that is not machine-formatted.

**Business impact**: Low — an operator-facing rendering failure on one command, costing the LLM call and the scan. Included because it is the one place in the LLM surface where provider output reaches a markup renderer unescaped and unguarded; it is also the shape that would matter if the summary prompt ever carried third-party text.

**Reachability**: The `ai=True` argument is passed only by `/scan deepall` (bot/skills/scan_skill.py:996), so this is one command rather than every scan. _ai_summary itself is live — the audit note at line 1342-1344 records that it previously raised TypeError on every call and was silently dead; that has been fixed, so the path now really returns model text.

**Existing tests**: grep of tests/ for _ai_summary / scan_skill AI summary returns no test that renders model output through the HTML path. tests/test_surface_scenarios.py covers other cards.

**Remediation**: Escape the summary before interpolation (`html.escape(summary)`), or wrap the two `edit_text`/`send_message` calls in the same try/except-to-plain-text pattern as bot/skills/telegram_handler.py:1159-1179. Escaping is the smaller change and keeps the '🤖 AI Summary:' label intact.

**Evidence**:

```
bot/skills/scan_skill.py:1201-1206 — model text spliced into an HTML-parsed message:

    if ai and results and not card_sent:
        text += "\n\n⏳ <i>Generating AI summary...</i>"
        await msg.edit_text(text, parse_mode="HTML")
        summary = await _ai_summary(results[:15])
        text = text.replace("⏳ <i>Generating AI summary...</i>",
                            f"\U0001f916 <b>AI Summary:</b>\n{summary}")

bot/skills/scan_skill.py:1231-1233 — the same on the card path:

        if ai and results:
            summary = await _ai_summary(results[:15])
            btn_text += f"\n\n\U0001f916 <b>AI:</b> {summary}"

bot/skills/scan_skill.py:1364 — the value is the provider's text verbatim:

        s = await llm_complete(client, cfg, system_prompt, "\n".join(lines))
        return s.strip() if s else "<i>No summary generated.</i>"

The send here is a bare `await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)` (bot/skills/scan_skill.py:1240) — unlike TelegramHandler._send, which wraps every send and falls back to plain text on a parse error (bot/skills/telegram_handler.py:1159-1179):

            try:
                await send_method(chunk, parse_mode="HTML", reply_markup=markup)
            except Exception as e:
                ...
                plain = re.sub(r"<[^>]+>", "", chunk)
```

## B3-14 [INFORMATIONAL] The public MCP server's file header states 'Every tool is READ-ONLY' and 'no tool can act', which the same file's WRITE_TOOLS registry (arena_open / arena_close / arena_my_positions) contradicts

- **Dimension**: ai-injection · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/routes/mcp.js:8-17`
- **Standard**: OWASP GenAI LLM06 — accurate declaration of agent capability.

**Observed**: The header is a stale absolute. The actual controls are sound — WRITE_TOOLS require a verified Arena key resolved once per request (line 1214-1220), tools/list annotates them `readOnlyHint: false` (line 1124-1129), and tool8257.js is deliberately given only the read-only registry (line 1247-1250) — but the first thing a reader or auditor of this file sees asserts the opposite.

**Expected**: The header describes the capability surface accurately, as the initialize instructions at app/routes/mcp.js:1095-1105 already do ('two of them can open and close PAPER positions... require an Arena key').

**Root cause**: The write family was added later with its own local justification comment (line 938-942) and the file header was not revisited. CLAUDE.md's own note applies: 'a number in prose is the part that rots first'.

**Business impact**: None directly; it misleads anyone auditing the public agent surface about whether the endpoint can take actions.

**Reachability**: Documentation only — no runtime behaviour depends on the header. Reported because the enforcement it misdescribes (arena keys, readOnlyHint annotations) is the exact thing an integrator relies on when deciding what to auto-approve.

**Existing tests**: app/test/mcp_public_records.test.js and app/test/tool8257_families.test.js pin the tool families programmatically, and app/test/mcp_v2.test.js exercises the RPC surface — none read the file header. tests/test_mcp_doc_matches_the_code.py covers bot/mcp/server.py, not this file.

**Remediation**: Update the header to describe three families (intelligence, Guardian-computes-on-input, and key-gated paper-write), mirroring the wording already used in the initialize instructions. Consider a test in the style of tests/test_mcp_doc_matches_the_code.py (which pins the Python MCP server's doc) for this file.

**Evidence**:

```
app/routes/mcp.js:8-17 (file header):

 * Scope is deliberate. Every tool is READ-ONLY and falls in one of two
 * families:
 *   - intelligence — serves data this site already publishes without auth ...
 *   - Guardian safety — evaluates input the CALLER supplies (marked
 *     `computesOnInput: true`), storing nothing and reading no account.
 * No tool can see a user's account, and no tool can act — trade-capable MCP
 * tools are a separate, gated decision for the operator.

app/routes/mcp.js:943-945, 1015-1017, 1043-1044:

    const WRITE_TOOLS = {
      arena_open: {
        requiresKey: true,
    ...
      arena_close: {
        requiresKey: true,
    ...
      arena_my_positions: {
        requiresKey: true,
```

## B3-15 [HIGH] SystemHealthMonitor is fed by nothing, so /health, /ready and /metrics publish a permanent HEALTHY / "Exchange: 🟢 Connected" / "0.0% error" all-clear manufactured from zero measurements

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/core/system_health.py:101-112 (snapshot), 56/70/75 (the three unfed feeders), 128-167 (format_telegram)`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-a-heuristic-is-never-a-verdict; CLAUDE.md-registration-is-not-reachability; CWE-754

**Observed**: `/health` prints ✅ SYSTEM HEALTH: HEALTHY with 0ms latency, 0.0% error rate (0/0) and a green "Exchange: Connected" for the entire life of the process, whatever the bot is actually doing. GET /ready (bot/web/dashboard_server.py:352) computes `_is_ready` = exchange_connected AND status != CRITICAL — both constants — so it returns 200 unconditionally, despite its own docstring "Fails CLOSED — if health can't be determined the bot is reported NOT ready". /metrics emits runeclaw_exchange_connected 1, runeclaw_api_error_rate_pct 0, runeclaw_ready 1 as constants (bot/web/dashboard_server.py:388-399).

**Expected**: A monitor with no samples has no reading. Latency/error-rate should render as "—" or "not measured", the exchange line should say "unknown" until something actually probed it, and the verdict should be a third state (UNKNOWN) rather than HEALTHY — the repo's own rule: absent is never a measurement, and a green sub-check that rules no cause out must not be painted as a verdict.

**Root cause**: Two defects compounding, one from each of CLAUDE.md's headline rules. (1) Reachability: the monitor's three write methods were never wired to any caller, so the class is a module that "nothing calls" one granularity down — indistinguishable from one that does not work. (2) The empty-window branch fills the readings with 0.0 instead of None, and the verdict ladder then reads those manufactured zeros as evidence of health.

**Business impact**: The operator's first triage command, the orchestrator readiness probe and the Prometheus scrape all report a healthy, exchange-connected bot during a total exchange outage. CLAUDE.md records 37 timed-out ticks spent on the wrong subsystem after reading one green sub-check; here every signal is green by construction. A load balancer can never take this instance out of rotation.

**Reachability**: REACHABLE AND ALWAYS-ON. /health is registered at bot/skills/telegram_handler.py:970 ("health", self._cmd_health) and renders engine.health.format_telegram() (telegram_handler.py:8347). engine.health is constructed at bot/core/engine.py:444. The /ready and /metrics routes are mounted at bot/web/dashboard_server.py:514-515 and the app is created from bot/main.py:440. No upstream guard exists — the failure is not conditional on any error path; it is the only behaviour the class has.

**Existing tests**: tests/test_ops_endpoints.py imports HealthSnapshot and constructs _HEALTHY/_CRITICAL/_DEGRADED_DISCONNECTED literals (lines 42-45); it never calls SystemHealthMonitor.snapshot() on an unfed monitor, so the constant-HEALTHY path is untested. tests/test_monitoring_is_honest.py is about scripts/monitoring/heartbeat.sh and verify_deploy.sh, not this class. tests/unreachable_methods_baseline.txt records the three dead feeders but nothing asserts the consequence.

**Remediation**: Make the empty-window case tri-state: return None for api_latency_ms / api_latency_p99_ms / error_rate_pct and status="UNKNOWN" when `recent` is empty, and make `exchange_connected` Optional[bool] defaulting to None until something calls set_exchange_status. `_is_ready` should then treat UNKNOWN as not-ready (it already claims to). Separately, wire record_api_call/set_exchange_status at the exchange call sites (or delete the class and stop publishing the card) — and remove the three names from tests/unreachable_methods_baseline.txt in the same commit, per the two-way ratchet rule.

**Evidence**:

```
bot/core/system_health.py:101-112 —
            else:
                avg_lat = 0.0
                p99_lat = 0.0
                err_rate = 0.0

            # Determine status
            if not self._exchange_ok or err_rate > 50:
                status = "CRITICAL"
            elif err_rate > 10 or avg_lat > 5000:
                status = "DEGRADED"
            else:
                status = "HEALTHY"

The three methods that would ever move those inputs have ZERO callers in the whole tree (`rg 'record_api_call|set_exchange_status|record_scan' .` returns only the definitions at bot/core/system_health.py:56,70,75 and tests/unreachable_methods_baseline.txt:160-162). `self._samples` is therefore always empty and `self._exchange_ok` is always its constructor default:

bot/core/system_health.py:52-53 —
        self._exchange_ok = True
        self._ws_ok = False

Only `set_ws_status` is wired (bot/core/engine.py:4059), so one of five signals on the card is real.
```

## B3-16 [HIGH] /livebalance renders a FAILED exchange balance read as a complete $0.00 account statement — Cash, Equity and NET all "$0.00"

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/skills/telegram_handler.py:7756-7771, 7861-7863, 7896`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-guard-or-omit-never-neither; CWE-754

**Observed**: A read failure that fetch_balance swallowed into a zeros dict is rendered as a confident, fully-formed account statement showing an empty account. The user reading /livebalance to find out whether their money is there is told it is gone.

**Expected**: A failed balance read is not a zero balance. The card should paint an error state (guard) — the outer `except` at line 7914 already does exactly that with "❌ Balance fetch failed: …" — or omit the balance block and say the read failed.

**Root cause**: fetch_balance converts an exception into a success-shaped dict carrying an "error" key plus zero-valued money fields, and the only caller that inspects that key does so for the cache write, not the display. The exception never reaches line 7914 because `_get_exchange()` returns the CACHED ccxt instance (bot/core/live_executor.py:809-820 — `if self._exchange is None: … return self._exchange`), so a fetch_balance-only failure leaves every later call working.

**Business impact**: The most-read money surface tells a live trader their exchange account is empty during a transient venue/auth failure. That is the reading most likely to provoke a panic manual intervention on a real account.

**Reachability**: REACHABLE. /livebalance is registered at bot/skills/telegram_handler.py:967 and listed in bot/skills/command_catalog.py:34. The handler is reached by any user with the "portfolio" permission; `balance_view_executor` routes linked users to their own account. No upstream guard inspects bal["error"] before the render; verified by grepping the whole function body (lines 7742-7917) for "error" — only line 7760 matches.

**Existing tests**: tests/test_telegram_commands.py:146 test_livebalance_returns_balance mocks a SUCCESSFUL fetch_balance ({"total": 123.45, …}) and asserts "123.45" appears. No test in tests/ plants the {"error": …} payload for this handler.

**Remediation**: In `_cmd_livebalance`, immediately after line 7756 add `if bal.get("error"): raise RuntimeError(bal["error"])` (or render the existing "❌ Balance fetch failed" branch directly) so the failure takes the guard path that already exists twelve lines further down. Better still, have `LiveExecutor.fetch_balance` re-raise or return None instead of a zeros dict, so no caller can mistake the failure for a reading — the same change the /portfolio and /balance realized-total work already made with `realized_totals` returning None.

**Evidence**:

```
bot/core/live_executor.py:8933-8934 — fetch_balance's failure return:
        except Exception as exc:
            return {"error": str(exc), "free": 0, "used": 0, "total": 0, "holdings": []}

bot/skills/telegram_handler.py:7768-7771 — the display path reads it without ever asking about "error":
            total = bal.get("total", 0)
            free = bal.get("free", 0)
            used = bal.get("used", 0)
            holdings = bal.get("holdings", [])

bot/skills/telegram_handler.py:7861-7863 —
                f"- Cash: <code>${free:,.2f}</code>",
                f"- Used: <code>${used_display:,.2f}</code>",
                f"- Equity: <code>${total_usd:,.2f}</code>",

The ONLY "error" check in the whole handler is the cache-write guard at line 7760 (`if is_operator_view and ("error" not in bal or bal.get("total", 0) > 0)`), which protects the engine's cache and not the card.
```

## B3-17 [HIGH] The web gateway reports `unprotected: false` for a live position that has NO stop at all, and the dashboard paints it "🤖 bot-managed" under a "🛡️ All positions have their stop-loss on the exchange" banner

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/web/user_gateway.py:1589-1597 (esp. 1596), 1675-1676`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-ask-which-OTHER-surface-makes-the-same-claim; CWE-754

**Observed**: `unprotected: false`, `sl_order: "manual"`, `sl_dist_pct: 0.0`. The dashboard row shows the neutral "🤖 bot-managed" chip (asserting the bot is managing a stop that does not exist) and, because unprotected_count is 0, the page-level banner reads "🛡️ All 0 live positions have their stop-loss on the exchange."

**Expected**: "No stop anywhere" is the strongest form of unprotected, not a protected state. It should set unprotected=True (or a third value, `sl_order: "none"`), and sl_dist_pct should be None rather than 0.0 — 0.0% away reads as a stop sitting exactly at entry.

**Root cause**: The serializer is two-valued where the fact is three-valued. Its own docstring (user_gateway.py:1583-1586) enumerates only two cases — "non-null ⇒ protected; None with a stop price set ⇒ UNPROTECTED" — and the third case, no stop price at all, falls through the `sl > 0` guard into the safe answer. The exact `(x or 0) > 0` shape from CLAUDE.md's table, leaning toward safety-looking.

**Business impact**: The web dashboard's protection banner is the answer to "is my money stopped out if this gaps?". An adopted or reclaimed position carrying no stop is counted as fine and shown with a reassuring chip, while the bot's own Telegram alert is simultaneously calling it CRITICAL — two surfaces disagreeing about the single most expensive safety fact.

**Reachability**: REACHABLE. handle_positions (user_gateway.py:1641) maps `_live_position_row` over `executor.open_positions` (lines 1660-1661) for any live, non-web caller, and the gateway is mounted by bot/web/dashboard_server.create_app. Positions with stop_loss == 0 are produced by the two adoption constructors cited above; the limit-order one (live_executor.py:2800-2815) never runs the safety-default block at all, and the position one skips it whenever entry_price is 0 (`need_sl = lp.stop_loss <= 0 and entry_price > 0`, live_executor.py:2484), which the `or 0` entry-price chain at :2340-2344 can produce. Two other surfaces get the same question right, which is what makes this a divergence rather than a design choice: /livepositions prints "⚠️ NOT SET" (telegram_handler.py:8049) and the proactive alert fires POSITION_UNPROTECTED on `has_sl = bool(sl_order_id)` alone.

**Existing tests**: tests/test_positions_web_gateway.py covers three cases (sl_order_id present; sl_order_id None WITH stop_loss=95.0; the runtime `unprotected` marker) — its `_live()` fixture always sets `stop_loss=95.0`. No test plants stop_loss=0, so the case is unpinned in both directions.

**Remediation**: Change line 1596 to `unprotected = (not sl_protected) or bool(getattr(pos, "unprotected", False))` — matching bot/core/proactive_monitor.py:1897-1901, which already gates the CRITICAL alert on `sl_order_id` alone — and return `stop_loss`/`sl_dist_pct` as None (not 0.0) when no stop is recorded so the client cannot format a price it does not have. Add the missing scenario to tests/test_positions_web_gateway.py.

**Evidence**:

```
bot/web/user_gateway.py:1588-1597 —
    entry = float(getattr(pos, "entry_price", 0) or 0)
    sl = float(getattr(pos, "stop_loss", 0) or 0)
    …
    sl_protected = bool(getattr(pos, "sl_order_id", None))
    tp_protected = bool(getattr(pos, "tp_order_id", None))
    unprotected = (not sl_protected and sl > 0) or bool(getattr(pos, "unprotected", False))

The `sl > 0` conjunct means "there is no stop price recorded at all" resolves to NOT-unprotected. Positions with stop_loss == 0 are constructed on the adoption paths: bot/core/live_executor.py:2377 (`stop_loss=0,` for an adopted exchange position) and :2807 (`stop_loss=0,` for an adopted/reclaimed limit order, status="pending_fill"), and both appear in `open_positions` (live_executor.py:8938 — `p.status in ("open", "pending_fill")`).

Downstream, app/public/js/dashboard.js:1130-1132 —
      if (p.unprotected) chip = `<span class="chip chip--down">⚠️ unprotected</span>`;
      else if (p.sl_order === 'exchange') chip = `<span class="chip chip--up">🛡️ on exchange</span>`;
      else chip = `<span class="chip">🤖 bot-managed</span>`;

and app/public/js/dashboard.js:1123 — with unprotected_count 0 the banner is the all-clear:
    else if (d.live) banner = `<div class="lpos-alert lpos-alert--ok">🛡️ All ${prot} live position${prot === 1 ? '' : 's'} have their stop-loss on the exchange.</div>`;
```

## B3-18 [MEDIUM] /performance prints an all-time realized total of "$+0.00" when nothing could be priced — the `_total_known` flag that exists to prevent it is computed and never used

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/skills/telegram_handler.py:12447-12449, 12525-12528`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-ask-which-OTHER-surface-makes-the-same-claim

**Observed**: "All-time $+0.00" (and Today / 7-Day) printed as measured figures, one line above a win rate that honestly says "n/a" and names the two unpriced closes. The card contradicts itself: the rate knows nothing could be scored, the total beside it claims break-even.

**Expected**: The total should read "unknown" (or "—") with no sign and no arrow, exactly as its sibling /balance already does: `_realized_str = (f"${pnl_sign}{realized_pnl:.2f}" if _realized_known else "unknown")` (telegram_handler.py:7832-7833), with the unpriced count stated beside it.

**Root cause**: The None-preserving fix was applied to the rate (line 12446, with a comment explaining that None must travel) and half-applied to the total: the flag was computed at 12449 and the wiring into the card was never finished. render_performance has no way to recover it — it is handed a measured-looking 0.0, the exact situation the comment at 12441-12445 describes for the win rate.

**Business impact**: The headline lifetime P&L figure on the performance card is fabricated from no measurements. Combined with the honest "n/a" win rate on the same card, the operator sees a break-even record where the truth is "we cannot price any of these closes".

**Reachability**: REACHABLE. /performance is registered in the command table and guarded by @guard("portfolio") at line 12366. The live branch runs whenever CONFIG.is_live() and the engine has a live_executor. `pnl_usd` is Optional BY DESIGN and survives restarts as JSON null — bot/core/live_executor.py:9368 `pnl_usd=(None if item.get("pnl_usd") is None else float(item["pnl_usd"]))`, under a comment saying `float(x or 0)` "silently converted 'we could not price this' into 'this broke even'".

**Existing tests**: tests/test_unpriced_closes_are_not_break_even.py exercises render_performance and render_daily_report directly with hand-built dicts; it never drives `_cmd_performance`, so the layer that manufactures the 0.0 above the renderer is untested. audit/generate_artifact.py's RC-2026-009 covers the PAPER branch's hardcoded week_pnl (line 12555) — a different branch and a different value.

**Remediation**: Pass the tri-state through: put `total_pnl` in `data` as None when `_total_known` is False (plus the `_tot["unpriced"]`/`_tot["total"]` counts), and teach render_performance to render an unknown total as "unknown" with a neutral glyph — the treatment it already gives an unknown win rate at bot/warroom/warroom_bot.py:380-386. Do the same for `today_pnl`/`week_pnl`, whose `_today_unpriced`/`_week_unpriced` counters (lines 12461-12496) are likewise computed and never surfaced.

**Evidence**:

```
bot/skills/telegram_handler.py:12447-12449 —
            _tot = realized_totals(user_trades)
            total_pnl = _tot["net"] if _tot["net"] is not None else 0.0
            _total_known = _tot["net"] is not None

`_total_known` appears exactly once in the whole file (`grep -n '_total_known' bot/skills/telegram_handler.py` → 12449 only). The manufactured 0.0 is then published verbatim:

bot/skills/telegram_handler.py:12525-12527 —
            data = {
                "today_pnl": round(today_pnl, 2),
                "week_pnl": round(week_pnl, 2),
                "total_pnl": round(total_pnl, 2),

realized_totals returns None for exactly this case and says why (bot/formatters/realized_totals.py:26-30): "In the all-unpriced case it printed `$+0.00 🟢`: a measured break-even, in green, built from zero measurements. … an unreadable total is None, and None has no colour."
```

## B3-19 [MEDIUM] /performance crashes with `TypeError: type NoneType doesn't define __round__` when an adopted-orphan close has no recorded P&L

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/skills/telegram_handler.py:12436, 12538`
- **Standard**: CLAUDE.md-test-is-None-not-falsiness; CLAUDE.md-ask-which-OTHER-surface-makes-the-same-claim; CWE-476

**Observed**: The handler raises before the data dict is built, so nothing is sent. `_cmd_performance` has no try/except of its own — the @guard decorator (telegram_handler.py:743-764) only runs the permission gate — so the exception escapes to the PTB error handler and the user gets no card at all.

**Expected**: The excluded-orphans line should render "(P&L not recorded)" the way /balance does, and the command should complete.

**Root cause**: One of the three sibling call sites of realized_totals was left with a caller that assumes a float. The comment sitting directly above the line names the defect ("this total beside it was not") and the fix was applied to /balance and /portfolio but not here.

**Business impact**: /performance becomes unusable for any account carrying an unpriced adopted orphan — the operator loses the P&L surface entirely and gets no explanation, on exactly the accounts (positions the bot did not open) where the numbers are least well understood.

**Reachability**: REACHABLE. Requires (a) live mode, (b) at least one closed position whose trade_id starts with 'TI-adopted' or 'TI-injected', and (c) that row's pnl_usd being None. Adopted orphans are created by bot/core/live_executor.py:2369 (`trade_id = f"TI-adopted-{…}"`), and pnl_usd is Optional and round-trips as JSON null (live_executor.py:9368). No upstream guard filters unpriced adopted rows — lines 12430-12431 select them purely by trade_id prefix.

**Existing tests**: No test in tests/ drives `_cmd_performance` with an adopted orphan (grep for ORPHAN_PREFIXES in tests/ returns filter tests, not this handler). audit/generate_artifact.py RC-2026-010 records a DIFFERENT crash on the same command — `f"{_wr:.0f}%"` on a None win rate at lines 12567/12574 — which is caught by the surrounding try/except and merely drops the PNG; this one is outside any handler and kills the command.

**Remediation**: Mirror /balance: keep `adopted_pnl` Optional in `data` (`"adopted_pnl": None if adopted_pnl is None else round(adopted_pnl, 2)`) and have bot/warroom/warroom_bot.py:418-424 render the excluded-orphans line as "(P&L not recorded)" when it is None rather than formatting it with `_money`.

**Evidence**:

```
bot/skills/telegram_handler.py:12432-12436 —
            # Third copy of the same parenthetical (see /balance and
            # /portfolio). The win rate six lines below was carefully made to
            # pass None through; this total beside it was not.
            from bot.formatters.realized_totals import realized_totals
            adopted_pnl = realized_totals(adopted_trades)["net"]

bot/skills/telegram_handler.py:12538 —
                "adopted_pnl": round(adopted_pnl, 2),

`realized_totals(...)["net"]` is documented as "None when rows exist and none are priced" (bot/formatters/realized_totals.py:57-58), and `round(None, 2)` raises. The sibling call site handles it — telegram_handler.py:7909-7910:
                    + (f" ({'+' if adopted_pnl >= 0 else ''}{adopted_pnl:.2f})</i>"
                       if adopted_pnl is not None else " (P&L not recorded)</i>")
```

## B3-20 [MEDIUM] /classpf scores every unpriced close as a break-even trade: the win rate is diluted downward and a partial net is printed as the whole

- **Dimension**: honesty-py · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/skills/telegram_handler.py:4569, 4576, 4585-4593`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-test-is-None-not-falsiness

**Observed**: WR 33% (1 win out of 3), computed over a denominator that includes two rows nobody could price; and "net $+12.00" presented as the class total over all 3 trades with no indication that 2 contributed nothing.

**Expected**: "1 of 1 priced close was a win; 2 closes carry no recorded P&L and are scored neither way" — WR 100% over a scored population of 1, with the shortfall stated. That is exactly what bot/utils/win_rate.py's win_stats returns and what the /portfolio and /performance cards already print.

**Root cause**: `float(getattr(tr, "pnl_usd", 0) or 0)` collapses None to 0.0 at the top of the loop, so by the time the bucket is scored there is no way to tell an unpriced close from a genuine break-even. `is_filled_close` cannot help: a close_reason of "TP"/"SL" is not in NON_FILL_CLOSE_REASONS (bot/utils/close_reason.py:64-79), so the manufactured zero passes straight through into the bucket.

**Business impact**: The card is described in its own docstring as the evidence base for growing or pruning the non-crypto universe. A win rate pushed down by unscorable rows and a partial net printed as whole are the inputs to a decision about which asset classes the bot keeps trading.

**Reachability**: REACHABLE. /classpf is registered at bot/skills/telegram_handler.py:950 ("classpf", self._cmd_classpf) and listed in bot/skills/command_catalog.py:101; the handler is @guard("portfolio"). It reads `self.engine.live_executor.closed_positions` directly (line 4557). `pnl_usd` is Optional by design and preserved as JSON null across restarts (bot/core/live_executor.py:9360-9369). No upstream guard drops unpriced rows.

**Existing tests**: grep for `classpf` in tests/ returns no test file. The behaviour is covered nowhere; the cure (bot/utils/win_rate.py) is heavily tested but this call site does not use it.

**Remediation**: Read the field with `bot.utils.win_rate.trade_pnl(tr)` (which returns Optional and exists precisely so callers cannot get the field wrong), keep unpriced rows out of the bucket lists, count them separately, and append the coverage note the other cards use (`bot.utils.win_rate.coverage_note`). PF and the net should be omitted or marked partial when the priced count is short of the trade count.

**Evidence**:

```
bot/skills/telegram_handler.py:4567-4576 —
        for tr in trades:
            try:
                pnl = float(getattr(tr, "pnl_usd", 0) or 0)
                if not is_filled_close(getattr(tr, "close_reason", None), pnl):
                    skipped_non_fills += 1
                    continue  # never filled — no capital was at risk
                cat = category_for_symbol(getattr(tr, "symbol", "") or "")
            except Exception:
                continue
            buckets.setdefault(cat, []).append(pnl)

bot/skills/telegram_handler.py:4585-4593 —
            wins = [p for p in pnls if p > 0]
            losses = [-p for p in pnls if p < 0]
            gw, gl = sum(wins), sum(losses)
            pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
            …
            wr = 100.0 * len(wins) / len(pnls) if pnls else 0.0
            lines.append(
                f"{category_icon(cat)} <b>{cat}</b>: {len(pnls)} trades · "
                f"PF <b>{pf_s}</b> · WR {wr:.0f}% · net ${sum(pnls):+.2f}")

This is `getattr(o, "pnl", 0)` plus a sum over a set containing unreadable rows — two rows of CLAUDE.md's shape table — in a file that already imports the cure: `from bot.utils.win_rate import win_stats as _win_stats` (telegram_handler.py:684).
```

## B3-21 [MEDIUM] /livepositions' exchange-fallback list renders an unreadable entry, mark and unrealized P&L as $0.0000 / $+0.00 — on the orphan list the bot has a purpose-built honest renderer for

- **Dimension**: honesty-py · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/skills/telegram_handler.py:8017-8029`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-ask-which-OTHER-surface-makes-the-same-claim

**Observed**: "- Mark: $0.0000", "- uPnL: $+0.00" and "- Entry: $0.0000" rendered as measurements, under the header "LIVE POSITIONS (from exchange)" and the caveat "⚠️ Showing exchange data — local tracking out of sync" (line 8030), which explains that tracking is stale but asserts the numbers themselves are real.

**Expected**: The same three-valued treatment `orphan_position_row` gives: an em dash or "unknown" for a field the venue did not report, no sign, and no colour. A missing unrealizedPnl must not print as a measured break-even.

**Root cause**: This fallback branch was not migrated when the orphan-row renderer was extracted. It is the second surface answering the same question from the same venue payload, and only the first was cured — the corollary CLAUDE.md states as "Ask which OTHER surface makes the same claim — before calling the fix done."

**Business impact**: This list is shown precisely when local tracking is out of sync — the moment the operator least knows what is open. A fabricated $+0.00 unrealized on an untracked live position asserts break-even for a position whose P&L nobody read.

**Reachability**: REACHABLE. The branch at telegram_handler.py:8003-8032 runs when the caller's executor has no locally tracked positions but the exchange reports open ones — i.e. exactly the orphan case. `_render_livepositions_cards` cannot pre-empt it: it returns False immediately when both lists are empty (telegram_handler.py:8182-8183). /livepositions is registered in the command table and listed in bot/skills/command_catalog.py.

**Existing tests**: tests/test_telegram_commands.py:165 test_livepositions_empty asserts the "no live positions" message with `_positions = {}` and no exchange fallback data; nothing plants an exchange position dict with a missing unrealizedPnl. tests/ has no coverage of this branch.

**Remediation**: Route this branch through `bot.formatters.orphan_position.orphan_position_row` (or at minimum through its `_f()` helper) so a missing field renders as "—", and drop the `or 0` on entryPrice / markPrice / unrealizedPnl. The row builder is already pure and unit-tested, so the change is a call, not a rewrite.

**Evidence**:

```
bot/skills/telegram_handler.py:8017-8029 —
                        contracts = float(p.get("contracts") or 0)
                        entry = float(p.get("entryPrice") or 0)
                        mark = float(p.get("markPrice") or 0)
                        upnl = float(p.get("unrealizedPnl") or 0)
                        lev = int(float(p.get("leverage") or 1))
                        …
                            f"- Entry: <code>${entry:,.4f}</code>\n"
                            f"- Mark: <code>${mark:,.4f}</code>\n"
                            f"- Qty: <code>{contracts:.6f}</code>\n"
                            f"- uPnL: <code>${upnl:+,.2f}</code>\n"

The cure for this exact payload already exists and documents the exact claim being made here — bot/formatters/orphan_position.py:80-83:
    # The venue omits unrealizedPnl more often than it reports a real 0.00, and
    # for an orphan "break-even" is the single worst thing to assert. A genuine
    # 0 from the venue still reads as 0; only a missing field is unknown.
    unrealized = _f(pos.get("unrealizedPnl")) if pos.get("unrealizedPnl") is not None else None
```

## B3-22 [MEDIUM] The Daily Alpha card publishes an unreadable funding rate as a measured "+0.0000% (flat)", while the open-interest and long/short fields beside it correctly omit themselves

- **Dimension**: honesty-py · **Confidence**: HIGH · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/alpha_card.py:180-184, 278-281`
- **Standard**: CLAUDE.md-unreadable-is-never-zero; CLAUDE.md-test-is-None-not-falsiness

**Observed**: "⚖️ Positioning — Funding: +0.0000% (flat)" on the text card and "Funding +0.0000% (flat)" in the POSITIONING block of the PNG, asserting a measured neutral funding regime built from a field the venue never sent.

**Expected**: Omit the funding line when the rate could not be read, exactly as the open-interest and long/short lines already do — or read it through `bot.risk.funding_clock.read_funding_rate`, which returns None for absent / empty / NaN and a real 0.0 only for a genuine zero.

**Root cause**: `float(x or 0)` on an Optional venue field, in the one of three positioning reads that was not given a presence guard. The falsiness collapse also makes the "flat" label unrecoverable downstream — by the time the renderer sees 0.0 there is nothing left to distinguish it from a genuine zero.

**Business impact**: The card's own footer reads "Same data the bot trades on — not investment advice." A fabricated flat funding rate on the positioning block misrepresents crowding for a symbol whose venue reports none — and 0% funding is itself a meaningful signal in this codebase (the executor reads it as "market likely closed").

**Reachability**: REACHABLE. `build_alpha_insight` / `format_alpha_card` are imported and called at bot/skills/telegram_handler.py:10464 and 10483, with the PNG variant at 10474-10475 (bot/formatters/signal_card.py:1799 render_alpha_card). Nothing between the fetch and the render inspects whether the rate was readable. DISPLAY ONLY — the trading gate reads funding independently through the null-preserving `read_funding_rate` (bot/core/live_executor.py:45, 2954), so no order decision is affected; scored MEDIUM for that reason.

**Existing tests**: grep for `funding_rate` in tests/ and app/test/ turns up no test that plants a null fundingRate against build_alpha_insight or format_alpha_card; tests/test_alpha_card.py exercises the formatter with populated dicts.

**Remediation**: Replace bot/core/alpha_card.py:182 with `_r = read_funding_rate(fr)` (bot/risk/funding_clock.py) and `if _r is not None: d["funding_rate"] = _r`. That is a one-line change using a reader the repo already ships and tests, and it fixes both renderers at once because both key on the field's presence.

**Evidence**:

```
bot/core/alpha_card.py:180-200 — funding is stored unconditionally; its two neighbours are guarded:
    try:
        fr = await exchange.fetch_funding_rate(symbol)
        d["funding_rate"] = float(fr.get("fundingRate") or 0)
    except Exception:
        pass
    …
        if oi_usd > 0:
            d["open_interest_usd"] = oi_usd

bot/core/alpha_card.py:278-281 — the text renderer keys on presence, not on a reading:
    if "funding_rate" in d:
        f = d["funding_rate"] * 100
        payer = "longs pay" if f > 0 else ("shorts pay" if f < 0 else "flat")
        pos_lines.append(f"  Funding: {f:+.4f}% ({payer})")

and the PNG renderer repeats it — bot/formatters/signal_card.py:1974-1977:
        if "funding_rate" in data:
            f = data["funding_rate"] * 100
            payer = "longs pay" if f > 0 else ("shorts pay" if f < 0 else "flat")
            row.append(f"Funding {f:+.4f}% ({payer})")

The repo already owns the null-preserving reader and says why 0 is not a neutral filler — bot/risk/funding_clock.py:41-48: "an absent field, a null, an empty string and a genuine 0.0 all became 0.0 … In this domain 0 is not a neutral filler: the executor's own comment says '0% funding on metals/stocks = market likely closed', so an unreadable rate was impersonating a real and quite specific signal."
```


========================================================================

# Batch 4 — backtest, honesty-js, data-db, concurrency

**33 raw · 31 CONFIRMED · 2 SUSPECTED · 0 REFUTED**


## B4-01 [BLOCKER] Default backtest fills every entry at an un-touched limit price — 73% of fills are at a price the decision bar never traded

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/engine.py:593`

**Observed**: CONFIG.limit_orders defaults to enabled=True / default_order_type="limit", so the analyzer sets idea.entry_price to a pullback level up to 1.0*ATR below the close for LONG (floor 0.15*ATR, else close-0.1*ATR). bot/backtest/ contains zero references to order_type (verified by grep), so _execute_fill books the position at that limit price unconditionally on the signal bar. 35 of 48 fills (73%) were at a price outside the decision bar's entire high/low range — buying ~1,900 below the bar's low on a ~107k instrument, roughly 2% of free price improvement granted on every entry.

**Root cause**: The backtest has no order-type model. It treats idea.entry_price as an achieved fill instead of as a resting limit that must be touched, so it captures every trade a real limit order would have missed — precisely the entries that ran away favourably.

**Business impact**: Every backtest number the repo produces in default mode is inflated by an unconditional ~1 ATR price improvement per trade. backtest_deep_results.json's headline (avg_return +2.92%, avg win rate 0.7, avg Sharpe 3.28, 393/500 profitable runs) is built on it. Flipping only the fill convention on one of those runs turns +5.57% into -2.17%.

**Reachability**: fill_mode defaults to 'close' (bot/backtest/models.py:59, pinned by tests/test_audit_batch3.py:96). Every default-mode consumer is affected: run_deep_backtest.py (which wrote the committed backtest_deep_results.json), run_deep_backtest_full.py, backtest_audit.py, run_realdata_backtest.py, backtest_realdata.py, bot/backtest/engine.py::walk_forward_backtest, and the Telegram /backtest and /walk_forward cards (bot/skills/skill_registry.py:1062, 1224). Confirmed by running the engine, not inferred.

**Remediation**: Honour idea.order_type in _execute_fill: for order_type == 'limit', queue the idea and fill only when a subsequent bar's low (LONG) / high (SHORT) reaches the limit, expiring it after CONFIG.limit_orders.expire_seconds and cancelling on price_drift_cancel_pct; fill order_type == 'market' at the bar close (or next open). Add a test asserting every recorded entry_price lies within [bar.low, bar.high] of some bar at or after the signal bar. Until then, no artifact produced in fill_mode='close' should be quoted as a performance figure.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→CRITICAL — The mechanism is confirmed from source alone and needs no downgrade. Two caveats on the write-up: (a) I did not re-run the engine, so the specific "35 of 48 fills (73%)" and "~1,900 below the bar's low" figures are unverified — the qualitative claim (a limit at up to 1.0*ATR below the close, minimum 0.1*ATR, filled without ever being touched) is what the code proves; (b) severity BLOCKER is slightly overstated for this system: no live order path is affected and no money moves on this code — it corrupts every published backtest/scorecard number, which is CRITICAL rather than ship-stopping. Note also the related upstream fact I confirmed while checking this: because order_type is "limit" for essentially every analyzer idea, bot/risk/risk_engine.py:1601-1607 SKIPS the risk-reward gate entirely, so the backtest is also not gating R:R — see the "missed" list.

- refuted=False sev→HIGH — Two corrections. (1) The exact ratio is setup-dependent — I measured 21/36 (58%), not 35/48 (73%); the phenomenon and its direction are confirmed but the specific count should not be quoted. (2) BLOCKER overstates the blast radius: bot/backtest/runner.py:546-548 forces fill_mode='next_open' under --honest, and every published figure (benchmark/scorecards/*.json, docs/FROZEN_BENCHMARK.md, bot/api/lab.py, the Telegram real-data card) goes through --honest. So this does NOT taint the marketplace scorecards; it taints the default-mode artifacts (backtest_deep_results.json, the synthetic /backtest and /walk_forward cards). HIGH, not BLOCKER.

**Evidence**:

```
bot/backtest/engine.py:587-593
        # 5. Execute (no human confirmation in backtest). With
        # fill_mode="next_open" (audit fix #15) the approved idea is queued and
        # filled at the NEXT bar's open instead of this bar's close.
        if getattr(self.config, "fill_mode", "close") == "next_open":
            self._pending_entry = (idea, risk_check)
            return
        self._execute_fill(idea, risk_check, idea.entry_price, bar)

bot/core/analyzer.py:1801-1814
        order_type = CONFIG.limit_orders.default_order_type if CONFIG.limit_orders.enabled else "market"
        limit_entry = None
        if CONFIG.limit_orders.enabled:
            limit_entry = _compute_limit_entry(
                entry, atr, direction, indicators, closes
            )
            # If no pullback level found but default is "limit", use a small
            # offset (0.1 ATR) from market price to get price improvement
            if limit_entry is None and order_type == "limit":
                offset = 0.1 * atr

bot/config.py:1982-1984
    enabled: bool = _env_bool("LIMIT_ORDERS_ENABLED", True)
    # Default order type: "market" or "limit"
    default_order_type: str = _env("DEFAULT_ORDER_TYPE", "limit")
```

## B4-02 [CRITICAL] /walk_forward's out-of-sample window is shorter than the indicator warmup, so every fold reports an unrun test as a measured +0.00% and 0% consistency

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/engine.py:1537`

**Observed**: fold_size = 1440//3 = 480; split_point = 336; embargo = 48; test_bars = fold_bars[384:480] = 96 bars. BacktestEngine._run iterates `for i in range(lookback_size, len(bars))` = range(100, 96), i.e. zero iterations, so no signal is ever generated. _compile_result then returns total_return_pct 0.0, win_rate 0.0, max_drawdown 0.0 from an empty run, and those zeros are published as the out-of-sample result. The card at bot/skills/skill_registry.py:1237-1250 prints 'Avg Test +0.00%', 'Consist. |╌╌╌╌╌╌╌╌| 0%', a Gap computed against the fabricated zero, and its per-fold TRADES column prints `f['train_trades'] + f['test_trades']` so the reader cannot see test_trades == 0.

**Root cause**: The min-fold guard's arithmetic ('200 bars per fold = 100 lookback + 100 tradeable') is computed as if the whole fold were traded, but the fold is then split 70/30 with a two-sided embargo, leaving ~0.3*fold_size - embargo test bars. A fold needs fold_size > 500 for the test window to exceed lookback_size at all; the guard admits fold_size >= 200.

**Business impact**: The overfitting guard is the control an operator uses to decide whether a strategy generalises. It currently answers with a fabricated flat out-of-sample result and a fabricated 0% consistency for every fold, and then draws an 'Overfitting risk detected' verdict from the difference.

**Reachability**: walk_forward_backtest has exactly one non-test caller: WalkForwardSkill.execute at bot/skills/skill_registry.py:1225, reached from Telegram. Its defaults (bars=1440, folds=3) are the failing case — verified by running it, not inferred. bot/backtest/portfolio_engine.py::portfolio_walk_forward is a different function and does prepend warmup correctly, which is what makes the omission here visible.

**Remediation**: Require len(test_bars) > config.lookback_size + a minimum tradeable count when sizing folds (or prepend lookback_size warmup bars to each test slice the way bot/backtest/portfolio_engine.py::portfolio_walk_forward already does at lines 254-259), and make a fold that cannot be measured return None for its test metrics so the card prints 'not measured' rather than 0.00%. Report train_trades and test_trades in separate columns.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→HIGH — Confirmed as written. Severity CRITICAL is one notch high for this system: /walk_forward is an operator-facing diagnostic card, not a trading control — nothing sizes a position off it. It is nonetheless a textbook violation of the repo's own rule (an unrun test rendered as a measured +0.00% and 0% consistency, with the per-fold TRADES column summing train+test so the zero is invisible), so HIGH. Two extras the finder did not spell out: the same zero also poisons `consistency_score` (a never-run fold is counted as "not profitable", bot/backtest/engine.py:1600-1601) and `train_test_gap`, which can then print "Overfitting risk detected" from a fold that never ran.

- refuted=False sev→HIGH — CRITICAL is a notch high for a Telegram diagnostic card that moves no money; HIGH is right. The substance is exactly as reported — a fabricated +0.00% / 0% consistency published as an out-of-sample measurement, plus a green overfitting verdict computed against it.

**Evidence**:

```
bot/backtest/engine.py:1535-1553
        total_bars = len(bars)
        fold_size = total_bars // n_folds
        if fold_size < 200:
            # Need at least 200 bars per fold (100 lookback + 100 tradeable)
            n_folds = max(1, total_bars // 200)
            fold_size = total_bars // n_folds if n_folds > 0 else total_bars
...
        split_point = int(len(fold_bars) * train_ratio)
        embargo = min(50, max(10, len(fold_bars) // 10))
        train_bars = fold_bars[:split_point - embargo]  # stop before embargo zone
        test_bars = fold_bars[split_point + embargo:]    # start after embargo zone

bot/skills/skill_registry.py:1219-1225
        bars_count = min(int(kwargs.get("bars", 1440)), 5000)
        seed = int(kwargs.get("seed", 42))
        folds = int(kwargs.get("folds", 3))
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)
        result = await walk_forward_backtest(bars, config, n_folds=folds)
```

## B4-03 [CRITICAL] Under --honest (next_open), positions are opened at the next bar's open while SL/TP stay anchored to the un-filled limit — 57% of opened positions have a realized R:R below the 1.2 gate that approved them

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/engine.py:614`

**Observed**: model_copy(update={'entry_price': ...}) rewrites only entry_price. stop_loss and take_profit stay at the levels the analyzer computed for the limit entry (shifted by entry_shift at bot/core/analyzer.py:1851-1853). Filling at the next bar's open — typically ~1 ATR above the limit for a LONG — therefore widens the effective stop and shrinks the effective target: median R:R collapses from 4.34 to 1.06, and 27 of 47 positions are opened with a realized R:R below CONFIG.risk.min_risk_reward, the very gate that approved them. risk_check.position_size_usd was also computed off the intended (narrower) stop distance, so the dollar risk actually taken exceeds the sizing model.

**Root cause**: The next_open fill path treats entry price as an independent scalar rather than as one leg of a geometry the analyzer and risk engine both reasoned about together.

**Business impact**: Every published 'honest' figure (benchmark/scorecards/*.json, the +0.49% OOS / PF 1.24 baseline in docs/FROZEN_BENCHMARK.md, the Strategy Lab, the Telegram real-data card) is measured on positions with roughly a quarter of the intended reward-to-risk and more than the intended dollar risk. It is not the strategy live runs.

**Reachability**: _apply_honest_fidelity (bot/backtest/runner.py:546-548) sets args.fill_mode = 'next_open' for every --honest run. --honest is used by scripts/gen_agent_scorecards.py:70, bot/api/lab.py:164 (web Strategy Lab), and bot/skills/skill_registry.py:990 (the Telegram real-data backtest card) — i.e. every published performance figure in benchmark/scorecards/*.json and docs/FROZEN_BENCHMARK.md. Verified by running the engine.

**Remediation**: In _execute_fill, when fill_price differs from idea.entry_price, either (a) shift stop_loss/take_profit by the same delta so R:R is preserved, and re-run risk.evaluate on the shifted idea, or (b) re-evaluate the idea against the fill and skip it if it no longer clears min_risk_reward. Add a test asserting abs(tp-entry)/abs(entry-sl) >= CONFIG.risk.min_risk_reward for every recorded BacktestTrade.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Keep the underlying observation (a fill displaced from idea.entry_price leaves SL/TP and the sizing model's stop distance un-re-anchored), drop the risk-gate framing entirely — the R:R gate is bypassed for limit ideas, so nothing was "approved" on R:R and no gate is being violated. This is also not an independent defect: it is a downstream consequence of finding 0 (entry_price being an untouched limit price). If finding 0 is fixed so a limit only fills when touched, the displacement disappears; fixing this one in isolation (shifting SL/TP to the fill) would paper over finding 0 by making a never-touched entry look self-consistent. The proposed fix (b) — re-gate against min_risk_reward — would also be a no-op today for the same order_type reason. Severity MEDIUM, and it should be filed as a sub-item of finding 0, not as a second CRITICAL.

- refuted=False sev→HIGH — Numbers differ from the finder's (20 fills / 9 below gate / 3.67→1.26, vs their 47/27/4.34→1.06) because of the bar series used — the effect is confirmed, the counts are not quotable. One partial mitigation the finding omits: the partial-TP ladder's R multiples DO use the real fill (engine.py:919 and :1212 compute risk_dist from bt_meta['adjusted_entry']), so only the absolute SL/TP levels and the sizing are mis-anchored. CRITICAL→HIGH: the mis-anchoring makes results worse, not flattering, so it corrupts rather than inflates the benchmark.

**Evidence**:

```
bot/backtest/engine.py:610-616
        slippage = fill_price * (self.config.slippage_pct / 100)
        if idea.direction == Direction.LONG:
            adjusted_entry = fill_price + slippage
        else:
            adjusted_entry = fill_price - slippage

        # Create a slippage-adjusted copy of the idea for portfolio
        slipped_idea = idea.model_copy(update={"entry_price": round(adjusted_entry, 6)})

bot/core/analyzer.py:1849-1855
        if limit_entry is not None and limit_entry != entry:
            # Shift SL/TP by the same offset so R:R stays the same
            entry_shift = limit_entry - entry
            entry = limit_entry
            stop_loss = stop_loss + entry_shift
            take_profit = take_profit + entry_shift
            order_type = "limit"
```

## B4-04 [HIGH] --honest win rate and trade count are per scale-out LEG, not per position, inflating every published scorecard win rate

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/engine.py:1272`

**Observed**: _partial_close appends a full BacktestTrade for each TP1/TP2 leg, and _compile_result counts len(self._trades) as total_trades and classifies each row independently. TP1 and TP2 legs are profitable by construction (they only fire at +1.5R and +2.5R), so every position that reaches TP1 contributes one or two guaranteed winning rows while a position stopped before TP1 contributes a single losing row. benchmark/scorecards/full-scan.json publishes "win_rate": 0.5333 with "total_trades": 30 and "honest": true; dip-sniper.json publishes 0.4737 / 19. docs/FROZEN_BENCHMARK.md:83 compares '57%' (single-exit, per position) against '64%' (partial-TP, per leg) as though they were the same statistic, in the table that supports 'The bot is backtest-profitable on this window when measured honestly.'

**Root cause**: The ladder was ported into the engine as additional trade rows without a corresponding position-level rollup, and _compile_result was never taught the difference.

**Business impact**: Marketplace Strategy-Agent scorecards and the frozen-benchmark table publish a win rate that is structurally biased upward, on public surfaces sold as reproducible design backtests.

**Reachability**: BACKTEST_PARTIAL_TP defaults False, but bot/backtest/runner.py:556 sets it for every --honest run, and --honest is used by scripts/gen_agent_scorecards.py, bot/api/lab.py and RunBacktestSkill._run_dataset_backtest. The committed scorecards all carry "honest": true. Also note bot/backtest/scorecard.py::pipeline_rows computes `remainder = ideas - (risk + timing + pending + trades)` — with legs in `trades` that goes negative and the card prints 'OVER-COUNTED ... the funnel is not trustworthy'.

**Remediation**: Group self._trades by trade_id in _compile_result and score win/loss on the position's total realized PnL (runner net_pnl_usd + banked_net_pnl), reporting positions as total_trades and legs separately (e.g. total_fills). Regenerate benchmark/scorecards/*.json and correct the win-rate column in docs/FROZEN_BENCHMARK.md. Also feed the position count, not the leg count, to bot/backtest/scorecard.sample_note (MIN_SAMPLE=10) — dip-sniper's 19 legs may be fewer than 10 positions.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→HIGH — Confirmed as written; severity stands. One refinement: the note about bot/backtest/scorecard.py::pipeline_rows going negative is plausible but I did not observe it — `remainder = ideas - (risk + timing + pending + trades)` at scorecard.py:136 does use total_trades (legs), so legs can push it negative, but in the portfolio path the same line is already broken by finding 11 (unsummed counters), so the two interact and the sign is not predictable from reading alone. Treat that sub-claim as plausible, not confirmed.

- refuted=False sev→HIGH — Confirmed and, if anything, understated: on the reproduction the leg-based rate (40%) overstated the position-based rate (30.8%) by 9 points. The dip-sniper sample_note speculation ('19 legs may be fewer than 10 positions') is unverified — leave it out.

**Evidence**:

```
bot/backtest/engine.py:1272-1279
        total = len(trades)
        # BT-L: treat exact-breakeven (net_pnl == 0) as neither win nor loss,
        # matching the risk engine's neutral handling. Previously net_pnl <= 0
        # counted breakeven as a loss, depressing win rate / inflating the
        # consecutive-loss streak.
        winners = [t for t in trades if t.net_pnl_usd > 0]
        losers = [t for t in trades if t.net_pnl_usd < 0]
        win_rate = len(winners) / total if total > 0 else 0

bot/backtest/engine.py:1207-1209  (_partial_close, one row per scale-out leg)
            signal_type=getattr(idea, "signal_type", ""),
        )
        self._trades.append(bt_trade)

bot/backtest/runner.py:556
        os.environ.setdefault("BACKTEST_PARTIAL_TP", "1")
```

## B4-05 [HIGH] Regenerating the published agent scorecards writes to a directory nothing reads, so stale marketplace performance figures can never be refreshed

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `scripts/gen_agent_scorecards.py:33`

**Observed**: _scorecard_dir() prefers benchmark/scorecards, which exists and holds the four committed cards. gen_agent_scorecards writes into data/benchmark/scorecards, which does not exist here and, on a deployed box, is under the data/ -> ~/runeclaw-persist symlink that deploy.sh creates and that git cannot traverse (the exact problem tests/test_benchmark_location.py was written to solve). An operator who reruns the generator after changing a preset sees 'Wrote 4 scorecards', while the marketplace keeps serving the old numbers indefinitely.

**Root cause**: The output path was left pinned to the retired location when the benchmark moved, and the guard that was supposed to catch exactly this cannot see it.

**Business impact**: Published per-agent performance figures on a public marketplace surface silently freeze. A preset whose gates change keeps advertising the old backtest, and nothing reports the divergence.

**Reachability**: bot/core/strategy_catalog.py is the marketplace catalogue loader; the four cards in benchmark/scorecards/ are what it returns today. The generator is the documented way to refresh them (its own module docstring and usage block). Confirmed by resolving both paths at runtime.

**Remediation**: Set OUT_DIR from the resolver (e.g. bot.core.strategy_catalog._scorecard_dir() or a snapshot-anchored benchmark/scorecards path), and add `'"data" /'`-style pathlib spellings to TestNoConsumerPinsTheOldPath.PINNED so the scan sees the operator form.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed. Severity HIGH → MEDIUM: nothing served today is wrong, and the failure needs an operator to change a preset and regenerate before it bites — it is a latent staleness trap, not a currently-false number. The claim about the data/ symlink is inference about a deployed box I cannot inspect from here, but it does not change the verdict: benchmark/scorecards exists and is preferred, so the write target is dead regardless of what data/ is.

- refuted=False sev→MEDIUM — HIGH→MEDIUM: no wrong number is produced, and the served cards keep working; the harm is that a regeneration silently no-ops and (because data/ is gitignored) leaves nothing to commit. Worth adding to the report: this is very likely why the committed cards no longer reproduce (see my missed-item 1).

**Evidence**:

```
scripts/gen_agent_scorecards.py:7
writes a percent/ratio-only scorecard to ``benchmark/scorecards/<slug>.json``.

scripts/gen_agent_scorecards.py:33
OUT_DIR = REPO / "data" / "benchmark" / "scorecards"

bot/core/strategy_catalog.py:48-52
    for parts in (("benchmark", "scorecards"), ("data", "benchmark", "scorecards")):
        cand = os.path.join(_REPO_ROOT, *parts)
        if os.path.isdir(cand):
            return cand
    return os.path.join(_REPO_ROOT, "benchmark", "scorecards")
```

## B4-06 [HIGH] backtest_realdata.py — the README's 'real-data strategy validation' harness — raises AttributeError on every run

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `backtest_realdata.py:76`

**Observed**: run_single builds its return dict from five attributes BacktestResult does not have; the accesses are outside the try/except (which only wraps the data fetch), so the AttributeError propagates out of asyncio.run and the script dies on the first symbol. Even if that were fixed, print_results at lines 128-138 places the `if isinstance(bnh, float) else` between implicitly-concatenated literals, so the else-branch absorbs the remaining six format fields and the true branch prints only four columns.

**Root cause**: Field names were never reconciled with BacktestResult, and no test or CI step ever executes this file.

**Business impact**: The only harness README presents as validating strategy performance on real Bitget data cannot produce a single number, so nobody has ever actually run the real-data validation the README claims exists.

**Reachability**: README.md:403 lists it in the backtest-mode table as 'Real-data | backtest_realdata.py | Bitget historical OHLCV | Strategy performance validation', and README.md:410/413 give the exact commands. grep of tests/, scripts/, .github/ and Makefile for 'backtest_realdata' returns nothing — no test and no CI job exercises it, which is why it has stayed broken.

**Remediation**: Rename to the real fields (win_rate*100, total_signals_generated, total_ideas_generated, total_ideas_rejected_risk, total_ideas_rejected_confidence), fix the table by moving the ternary into a single value rather than splitting a concatenated literal, and add a smoke test that runs the script against a small fixture bar series. If the harness is redundant with bot/backtest/runner.py, delete it and remove the README rows rather than leaving a documented entry point that cannot run.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed. Severity HIGH → MEDIUM: this is a standalone, README-documented harness that crashes on first use — embarrassing and a reachability failure of exactly the class CLAUDE.md describes, but it produces no wrong number (it produces no number at all), touches no live path, and corrupts no artifact. A loud crash is the honest failure mode; the LOW-severity sibling (finding 13) that silently writes zeros is the more dangerous one.

- refuted=False sev→MEDIUM — HIGH→MEDIUM. It is a documented dev harness that has never been in CI and moves no money; on a network-less box every symbol short-circuits to the {'symbol','error'} branch so nothing crashes. Note the ternary bug is only reachable after the AttributeError is fixed, since an errored row hits `continue` at :121.

**Evidence**:

```
backtest_realdata.py:76-85
        "win_rate_pct": result.win_rate_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "calmar_ratio": result.calmar_ratio,
        "profit_factor": result.profit_factor,
        "signals_generated": result.signals_generated,
        "ideas_generated": result.ideas_generated,
        "risk_rejected": result.risk_rejected,
        "confidence_rejected": result.confidence_rejected,
```

## B4-07 [HIGH] Live↔backtest parity report scores an unpriced live close as break-even, counting unscorable trades as losses in the win rate and as zeros in the printed total

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/backtest/parity.py:49`

**Observed**: pnl_usd is Optional and is legitimately null for a close nobody could price — bot/core/live_executor.py:9368 deserialises it as `pnl_usd=(None if item.get("pnl_usd") is None else float(...))` with a comment recording that `float(x or 0)` on that field 'silently converted "we could not price this" into "this broke even"'. _net() reintroduces exactly that: None is not an int/float, so it returns 0.0. is_filled_close then keeps the record (its close_reason is not in NON_FILL_CLOSE_REASONS), so it lands in `n` and, being not > 0, is counted as a non-win, and contributes 0 to net_pnl and to the profit-factor numerator/denominator.

**Root cause**: parity.py reimplements P&L reading instead of calling bot/utils/win_rate.py, which exists precisely because five surfaces each wrote their own copy of this mistake.

**Business impact**: This is the report that decides whether execution or the strategy is the leak on a live money account. Unpriced closes push the reported live win rate down and the reported net total is a partial sum printed as a whole, so an operator can conclude 'live is far below the backtest, halt' from records that were simply never priced.

**Reachability**: parity.py's only entry point is `python -m bot.backtest.parity`, documented in docs/FROZEN_BENCHMARK.md:176-179 as the tool that answers 'is live tracking the benchmark'. It reads data/closed_trades.json, written by bot/core/live_executor.closed_trade_row (line 345: "pnl_usd": pos.pnl_usd) where pnl_usd is Optional[float] = None (line 535) and the reload path explicitly preserves null. Reachable and demonstrated.

**Remediation**: Use bot/utils/win_rate.trade_pnl / the module's rate and pnl_stats helpers: exclude records whose P&L is None from nets, count them separately, and print the unscorable count beside the win rate and the net total in format_report.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed, exactly the shape CLAUDE.md's table names. Severity HIGH → MEDIUM: parity.py is a read-only CLI observability tool (`python -m bot.backtest.parity`) with no gate, no automation and no money downstream; its worst effect is an operator reading a slightly depressed win rate and a partial total printed as whole. The fix is cheap and correct (route through bot/utils/win_rate.py and print the unscorable count) — the impact just isn't HIGH.

- refuted=False sev→MEDIUM — HIGH→MEDIUM: read-only observability, no order path. The claim is otherwise exactly right and matches the repo's own canonical rule; the fix is a two-line switch to bot/utils/win_rate.trade_pnl plus reporting the unscorable count beside the rate.

**Evidence**:

```
bot/backtest/parity.py:49-55
def _net(t: dict) -> float:
    """Realized net PnL for a trade, tolerant of field naming."""
    for k in ("pnl_usd", "net_pnl", "net_pnl_usd"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0

bot/backtest/parity.py:128-137
    nets = [_net(t) for t in trades]
...
    wins = sum(1 for n in nets if n > 0)
    n = len(trades)
```

## B4-08 [MEDIUM] run_deep_backtest_full.py and backtest_audit.py still average the PF_UNDEFINED sentinel into 'avg profit factor' — the exact bug, and key name, bot/backtest/metrics.py was written to kill

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `run_deep_backtest_full.py:157`

**Observed**: run_deep_backtest_full.py (67 symbols x 5 regimes x 5 seeds = 1675 runs, the largest sweep in the repo) computes a plain arithmetic mean over profit_factor, printing it as 'Avg profit factor:' and saving it under the key `avg_profit_factor` — the very key run_deep_backtest.py deliberately renamed because 'silently changing what a key means is worse than breaking it'. Its per-regime table (line 195) does the same. backtest_audit.py:188 feeds the raw list to a Min/Max/Mean/Median/Std line. Neither file imports bot.backtest.metrics.

**Root cause**: The 2026-08-07 fix was applied to run_deep_backtest.py only; the two sibling harnesses that compute the same statistic were not audited.

**Business impact**: The largest robustness sweep in the repo would republish the 19.17-shaped headline: on the committed sample the same arithmetic yields 43.73 against an honest median of 3.46, on the number a reader checks first when asking whether the strategy works.

**Reachability**: Both are top-level scripts with __main__ guards; README.md:639 and :761 document backtest_audit.py as the synthetic sanity check. run_deep_backtest_full.py imports run_deep_backtest as rdb and reuses its per-run dict, so its inputs carry the same 999.99 sentinel that appears 18 times in the committed 500-run sample.

**Remediation**: Replace both call sites with profit_factor_summary((r['profit_factor'], r['total_trades']) for r in valid) and render .median / .mean / .n_undefined / .n_no_trades, as run_deep_backtest.py:283-284 already does. Rename the saved key away from avg_profit_factor.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed as written, severity unchanged. Minor: backtest_audit.py:178-180 prints Median alongside Mean, so its damage is smaller than run_deep_backtest_full.py's — the latter prints and SAVES a bare mean under the retired key name and has no median anywhere. If only one is fixed, fix run_deep_backtest_full.py.

- refuted=False sev→MEDIUM — Accurate as written. One de-escalating fact worth stating: neither harness's output is committed (run_deep_backtest_full writes backtest_deep_full_results.json, which is not in git ls-files), so the damage is confined to whoever runs the sweep. backtest_audit.py at least prints Median alongside the poisoned Mean/Std.

**Evidence**:

```
run_deep_backtest_full.py:149-157
    def avg(k):
        return sum(r[k] for r in valid) / n
...
    avg_pf = avg("profit_factor")

run_deep_backtest_full.py:208
                "avg_profit_factor": round(avg_pf, 2), "crashed_runs": crashed,

backtest_audit.py:188
    stat_line("Profit Factor", pfs)
```

## B4-09 [MEDIUM] Deep-backtest aggregate means fold zero-trade runs in as 0.0, and avg_win_rate is published rounded to one decimal

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `run_deep_backtest.py:279`

**Observed**: Five of the seven aggregates still divide by len(valid), so each of the 83 idle runs contributes win_rate 0.0, sharpe 0.0, sortino 0.0, drawdown 0.0 and return 0.0 to the mean. 'avg_win_rate: 0.7' is therefore not the win rate of anything: the mean over runs that actually traded is 0.781 and over all runs 0.6513. round(0.6513, 1) then discards a further digit, turning 65.1% into a published 0.7 while every neighbouring metric in the same block is rounded to two places.

**Root cause**: The 2026-08-26 idle-run fix was applied to the profit-factor and profitable-share lines and stopped there; the other five means were left summing over a set that includes non-measurements.

**Business impact**: The headline robustness numbers of the repo's largest committed artifact are means over a set that includes 83 non-measurements, and the published win rate is off by 13 percentage points from the win rate of the runs that traded.

**Reachability**: These lines write the committed backtest_deep_results.json summary block, quoted as the repo's robustness evidence and cited by bot/backtest/metrics.py and tests/test_backtest_metrics_honesty.py. Verified numerically against the committed file.

**Remediation**: Compute the five means over the runs that traded, report the idle count beside them (the code already has pf.n_no_trades / shr.idle), and round avg_win_rate to 4 places like run_deep_backtest_full.py:206 does. Regenerate backtest_deep_results.json, whose summary block also predates the current script — it is missing the runs_no_trades / runs_that_traded / runs_idle keys the code now emits.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed, severity unchanged. The 2026-08-26 partial-fix reading is right: profit_factor_summary and share_profitable both take (value, total_trades) precisely so an idle run is excluded and counted, and the five raw sums beside them ignore that signal. Note the same defect exists one file over in backtest_audit.py's robustness table (its stat_line filters only None, not zero-trade runs) — see the "missed" list.

- refuted=False sev→MEDIUM — Confirmed exactly, including both numbers (0.6513 and 0.7810). No correction.

**Evidence**:

```
run_deep_backtest.py:275-281
    total_trades = sum(r["total_trades"] for r in valid)
    avg_return = sum(r["total_return_pct"] for r in valid) / len(valid)
    avg_dd = sum(r["max_drawdown_pct"] for r in valid) / len(valid)
    avg_wr = sum(r["win_rate"] for r in valid) / len(valid)
    avg_sharpe = sum(r["sharpe_ratio"] for r in valid) / len(valid)
    avg_sortino = sum(r["sortino_ratio"] for r in valid) / len(valid)

run_deep_backtest.py:334
                "avg_win_rate": round(avg_wr, 1),
```

## B4-10 [MEDIUM] backtest_deep_results.json carries no data provenance — 500 synthetic GBM runs stamped with real ticker names and realistic prices

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `backtest_deep_results.json:1`

**Observed**: The 480 KB artifact contains the string 'synthetic' zero times. Every one of the 500 rows is labelled with a real ticker and a real project name ('BTC/USDT' / 'Bitcoin') at a realistic price, and the summary reports avg_return +2.92%, avg_win_rate 0.7, avg_sharpe 3.28, 393/500 profitable — while every bar was produced by DataLoader.generate_synthetic. run_deep_backtest.run_single_backtest copies 30 result fields into its per-run dict and includes neither provenance field.

**Root cause**: The per-run dict was written before the provenance fields existed and was never extended; the aggregate meta block has no provenance either.

**Business impact**: A reader who opens the largest committed performance artifact sees 500 named-crypto runs with a 70% win rate and a 3.28 Sharpe and nothing telling them the prices are a random walk.

**Reachability**: The file is committed at the repo root and is cited by bot/backtest/metrics.py, bot/backtest/engine.py:1325 and tests/test_backtest_metrics_honesty.py as the repo's robustness evidence. README.md:763 does correctly describe run_deep_backtest.py as synthetic, so the disclosure exists — just not anywhere in the artifact itself.

**Remediation**: Add "data_source": "synthetic", "used_synthetic": true and the generator parameters (start_price, volatility, trend, seed) to meta and to each row, and regenerate. README.md:417 and :765 already carry the caveat in prose — put it in the artifact, since the artifact is what gets read and quoted on its own.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed as written; severity stands. The repo's own models.py comment is the argument for the fix, so this is not a matter of taste. One addition the finder gestured at but did not pin: `seed` IS present per row, so a regenerated artifact only needs data_source/used_synthetic plus start_price/volatility/trend to be fully self-describing.

- refuted=False sev→LOW — MEDIUM→LOW. README.md:763 explicitly labels run_deep_backtest.py as 'Synthetic (GBM+GARCH)' and the paragraph under it says synthetic backtests 'cannot validate alpha-generating modules', so the disclosure exists — the gap is that the artifact is not self-describing when read alone. Real, but the smallest of the provenance gaps; see my missed-item 2 for the larger one on the portfolio path.

**Evidence**:

```
run_deep_backtest.py:105-111 (every run's bars)
        return DataLoader.generate_synthetic(
            bars=BARS,
            start_price=sym_info["price"],
            volatility=vol,
            trend=regime["trend"],
            seed=seed,
        )

bot/backtest/models.py:206-211
    # Data provenance (deep-audit medium): make a saved result self-describing so
    # a synthetic-fallback run is never mistaken for a real backtest. data_source
    # is one of "csv" | "bitget_real" | "synthetic" | "synthetic_fallback";
    # used_synthetic is True for the latter two. Stamped by the runner.
    used_synthetic: bool = False
    data_source: str = "unknown"
```

## B4-11 [MEDIUM] The parity report prints a hardcoded benchmark the frozen-benchmark doc explicitly marks as superseded

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/backtest/parity.py:183`

**Observed**: Three places retype the number (bot/backtest/parity.py:4, :183, and bot/backtest/runner.py:554 as a comment), and the doc that owns it has already moved on to +0.49% / PF 1.24. An operator comparing live realized PF against 1.14 is comparing against a benchmark that has been superseded twice (time-stop and fee-model fidelity), so a live PF of 1.18 reads as 'beating the benchmark' when it is below it.

**Root cause**: A performance figure duplicated as prose in code, which is the part that rots first.

**Business impact**: The operator's live-vs-backtest verdict is anchored to a superseded benchmark, biasing the 'is execution the leak' decision on a real-money account.

**Reachability**: format_report is what `python -m bot.backtest.parity` prints; docs/FROZEN_BENCHMARK.md:176-179 documents that command as the live-vs-benchmark check. The line is unconditional (not inside any branch).

**Remediation**: Load the comparison target from a single source (a committed baseline JSON, or a module constant that docs/FROZEN_BENCHMARK.md is tested against), and pin it with a test the way tests/test_claude_md_accuracy.py pins the CLAUDE.md gate count.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Confirmed but MEDIUM is too high. This is a stale prose constant in a read-only diagnostic print, in a tool whose own docstring frames the comparison as "is live in the same ballpark?" rather than a pass/fail gate — the error band of that question is wider than the 0.31→0.49 drift. LOW. The proposed fix is sound; the test-pinning half (pin the constant the way tests/test_claude_md_accuracy.py pins the gate count) is the part actually worth doing, since the number will rot again.

- refuted=False sev→LOW — MEDIUM→LOW. The line is a soft prompt ('is live in the same ballpark?'), not a pass/fail verdict, and the doc it contradicts is one file away. Real staleness, low consequence.

**Evidence**:

```
bot/backtest/parity.py:182-184
             + (f"  ({s['excluded_non_fills']} never-filled records excluded)"
                if s.get("excluded_non_fills") else ""),
             "  Backtest benchmark (majors_1h, --honest): +0.31% / PF 1.14 — "
             "is live in the same ballpark?"]

docs/FROZEN_BENCHMARK.md:110-112
| **on** | **0.06%** | **+0.49%** | **+$294** | 63% | **1.24** |
...
**+0.49% OOS / PF 1.24 is the current baseline — every future A/B beats this
number, not the +0.31% one above.**
```

## B4-12 [MEDIUM] Portfolio backtests report the preset-rejection, per-gate and entry-timing counters for only the first symbol

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/backtest/portfolio_engine.py:219`

**Observed**: total_ideas_rejected_preset, total_ideas_timing_unfilled, total_entries_pending_at_end, rejections_by_gate and stateful_rejections are taken from the first symbol only, while total_signals_generated / total_ideas_generated / total_ideas_rejected_risk / total_ideas_rejected_confidence are summed across all of them. The funnel therefore cannot reconcile, and bot/backtest/scorecard.py::pipeline_rows will print 'UNACCOUNTED — ideas left the pipeline somewhere this card cannot name'.

**Root cause**: The counters were added to BacktestEngine after the portfolio orchestrator's merge list was written, and the merge list was never revisited.

**Business impact**: An A/B on the frozen benchmark can show zero stateful rejections while a non-first symbol tripped the breaker repeatedly, so a parameter change is credited with a metric move that came from a different trade set.

**Reachability**: PortfolioBacktester is what bot/backtest/runner.py::_run_portfolio drives, which is the path taken by scripts/gen_agent_scorecards.py (which sets --regime-filter / --rsi-max / --confidence-threshold, i.e. the preset gates whose counter is under-reported), bot/api/lab.py, and RunBacktestSkill._run_dataset_backtest.

**Remediation**: Sum the remaining counters in the same block: _ideas_rejected_preset, _et_disarmed_invalidated, _et_disarmed_expired, len(_armed_setups) and pending entries across engines, and merge _rejections_by_gate with a per-key sum. Add a test that a two-symbol portfolio run's total_ideas_rejected_preset equals the sum of the two single-symbol runs'.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Confirmed, severity unchanged. One qualifier: the finding asserts scorecard.pipeline_rows "will print UNACCOUNTED" — the direction and magnitude of `remainder = ideas - (risk + timing + pending + trades)` (scorecard.py:136) depends on the interaction with finding 3's leg-counted `trades`, so the funnel is definitely unreconcilable but which of the two messages fires is not determinable by reading. Also note _rejections_by_gate needs a per-key merge, not a sum of totals, which the proposed fix gets right.

- refuted=False sev→MEDIUM — Confirmed as written. Worth adding the observed evidence: the funnel already prints UNACCOUNTED on a plain 3-symbol run, so the failure is visible today, not hypothetical. Note the single-symbol path (the Telegram /backtest card) is unaffected — one engine means `first` is the whole set.

**Evidence**:

```
bot/backtest/portfolio_engine.py:214-223
        first._trades = merged_trades
        first._equity_curve = self._equity_curve
        first._rr_values = [rr for eng in self._engines.values()
                            for rr in eng._rr_values]
        first._signals_generated = sum(e._signals_generated for e in self._engines.values())
        first._ideas_generated = sum(e._ideas_generated for e in self._engines.values())
        first._ideas_rejected_risk = sum(e._ideas_rejected_risk for e in self._engines.values())
        first._ideas_rejected_confidence = sum(
            e._ideas_rejected_confidence for e in self._engines.values())
```

## B4-13 [LOW] PF_UNDEFINED_FLOOR reclassifies a genuine profit factor as 'had no losing trades' — the committed artifact overstates the undefined count

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/metrics.py:51`

**Observed**: The >= 999.0 floor swallows it. ProfitFactorSummary.render() then emits '18 run(s) had no losing trades — ratio undefined', which is a false statement about the sample, and the genuine 1385.11 observation is dropped from the median/mean of defined runs.

**Root cause**: A magnitude threshold is being used to identify a sentinel whose real signature is 'net_loss == 0'. bot/backtest/engine.py:1336 sets PF_UNDEFINED only when net_loss == 0, so the information that distinguishes the two cases (the losing-trade count) exists and is already passed into profit_factor_summary as part of the row.

**Business impact**: Small numerically, but the published sentence 'N run(s) had no losing trades' is untrue for one of the 18 it names, in the module whose entire purpose is telling a sentinel apart from a measurement.

**Reachability**: profit_factor_summary is called from run_deep_backtest.py:247, :270 and :283 — the per-symbol, per-regime and global tables — and its render() output is printed to the operator. The misclassified row is in the committed 500-run sample, so this is realised, not hypothetical.

**Remediation**: Either emit a JSON null (or a separate flag / losing_trades count) for the undefined case instead of a magic float, or extend the row tuple with losing_trades and classify on that. If the floor must stay for backward compatibility, narrow the render text to 'ratio undefined or above the reporting ceiling' so it stops asserting something false about the sample.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Confirmed exactly, severity LOW is right. Worth flagging that the proposed fix collides with a pinned test: tests/test_backtest_metrics_honesty.py:69-76 asserts the magnitude floor, so the row-tuple/losing-trades variant is the fix that does not churn that baseline. The cheapest honest change is the render-text narrowing ("ratio undefined or above the reporting ceiling"), which stops the false claim without touching the pinned classifier.

- refuted=False sev→LOW — Confirmed, severity correct. Emphasise the cheap half of the fix: the floor can stay (the test pins it on purpose); only render()'s wording asserts something false about the sample, and narrowing it to 'ratio undefined or above the reporting ceiling' costs nothing and churns no baseline.

**Evidence**:

```
bot/backtest/metrics.py:49-51
#: At or above this, a value is the sentinel rather than a measurement. A plain
#: `== PF_UNDEFINED` would miss a value that survived a float round-trip.
PF_UNDEFINED_FLOOR = 999.0

bot/backtest/metrics.py:133-138
        if f >= PF_UNDEFINED_FLOOR:
            undefined += 1
        else:
            defined.append(f)
```

## B4-14 [LOW] run_realdata_backtest.py records signals and rejection counts as 0 from three field names BacktestResult does not have

- **Dimension**: backtest · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `run_realdata_backtest.py:128`

**Observed**: getattr with a default of 0 on names that do not exist returns 0 unconditionally, so every saved run reports 'signals_generated: 0, risk_rejections: 0, confidence_rejections: 0' — a confident zero manufactured from a wrong attribute name, for runs that generated hundreds of signals.

**Root cause**: Attribute probes written against remembered field names rather than against bot/backtest/models.py, with a getattr default that converts the miss into a plausible-looking number.

**Business impact**: Any saved real-data backtest artifact understates the pipeline as having produced no signals and rejected nothing, which is the exact opposite of what happened and would read as a broken engine.

**Reachability**: run_realdata_backtest.py is documented in README.md:762 and :769-772 (including `--output results.json`), and unlike its sibling backtest_realdata.py it does not crash, so these zeros actually reach the saved artifact. Confirmed against BacktestResult.model_fields.

**Remediation**: Use the real field names. Where a default is genuinely needed, use None so the absence is visible rather than a 0 that reads as a measurement.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Confirmed, severity unchanged. If anything this is the more insidious of the two script bugs (it and finding 5): the crashing one announces itself, this one writes a plausible-looking 0 into a JSON someone may quote. The `getattr(..., 0)` default is the exact shape CLAUDE.md's table lists as `getattr(o, "pnl", 0)` — absent field is zero.

- refuted=False sev→LOW — Confirmed, severity correct.

**Evidence**:

```
run_realdata_backtest.py:126-131
        "profit_factor": round(result.profit_factor, 3) if result.profit_factor else 0,
        "signals_generated": getattr(result, "signals_generated", 0),
        "risk_rejections": getattr(result, "risk_rejections", 0),
        "confidence_rejections": getattr(result, "confidence_rejections", 0),
    }
```

## B4-15 [CRITICAL] buildDefiPositions returns an all-clear on Aave liquidation risk when every chain RPC is dead — no marker distinguishes it from a position-free wallet

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/lib/defi.js:130-149`

**Observed**: A wallet whose every chain RPC is down produces a payload byte-identical to a wallet that genuinely holds no DeFi positions: `aave: []`, `warnings: []`, no error field anywhere. Downstream: app/public/js/dashboard.js:3806 `if (!bits.length) return null;` sends it to the panel's empty state, app/public/js/dashboard.js:3811 `empty: { … text: 'No Aave, Lido or Uniswap v3 positions found on the tracked chains.' }` — and mustRead() at dashboard.js:3778 passes, because the HTTP read genuinely succeeded. The chat intercept says the same (app/lib/defi.js:225-227: `no Aave, Lido or Uniswap v3 positions found on the tracked chains.`).

**Root cause**: `.catch(() => undefined)` collapses a failed read into the same sentinel space as a successful read of an empty position, and `filter(Boolean)` then discards both. The composition is neither of CLAUDE.md's two honest strategies: it is not a guard (nothing throws) and it is not an omit-with-notice (nothing records what was omitted).

**Business impact**: The DeFi panel's stated purpose is liquidation-risk warning ('your Aave, Lido and Uniswap positions appear here with liquidation-risk warnings', dashboard.js:3781-3782). During an RPC outage a user with an Aave health factor below 1.1 is shown 'No Aave, Lido or Uniswap v3 positions found', receives no CRITICAL warning, and their configured health-factor push alert stays silent — an all-clear on imminent liquidation, assembled from a failure to read. This is the same shape as the `_cmd_escape` 'no open positions to unwind' case CLAUDE.md records on the bot side.

**Reachability**: Reachable from three live callers, all wired: GET /api/defi (app/routes/defi.js:22-28, mounted authed), the dashboard DeFi panel (app/public/js/dashboard.js:3776-3811), and the chat intercept `maybeHandleDefiChat` (app/lib/defi.js:195). It is ALSO the input to the user-configured `health_factor` alert: app/lib/alerts.js:349-355 does `const hfs = (d?.aave || []).map(x => x.health_factor)…; if (!hfs.length) continue;` — so during an RPC outage a user's Aave liquidation alert silently does not fire and nothing anywhere says why. No upstream guard prevents any of this: readAave's rejection is the only signal and it is discarded on the spot.

**Remediation**: Distinguish the two outcomes in readAave/readUniswapCount/readLido's callers: keep `null` = no position, and on a rejection push a row `{ chain, label, error: 'rpc unreadable' }` (or collect the failed chain keys into an `unreadable_chains: []` + `partial: true` pair on the returned object), mirroring app/lib/wallet.js:274-277 / app/lib/holdings.js:93-101. Then (a) the dashboard panel at dashboard.js:3792-3811 must not fall into its empty state while `unreadable_chains.length`, and (b) the health_factor alert path (app/lib/alerts.js:352-355, `if (!hfs.length) continue;`) must be able to tell "healthy" from "nobody could look". Also stop caching an all-unreadable read: app/lib/defi.js:179 caches it for CACHE_MS = 60_000, freezing a transient outage into "no positions" — app/lib/gas_read.js:105 already refuses to do this (`if (Object.keys(body.chains).length) cached = …`).

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→HIGH — Finding stands as written. Severity trimmed CRITICAL->HIGH: the surface is read-only advisory and the alert it silences is user-opt-in; nothing here can move funds or place an order. The 60s cache of an all-unreadable read (defi.js:173-183) is real and correctly noted.

- refuted=False sev→HIGH — Severity CRITICAL is inflated for this system: /api/defi is a read-only advisory surface — nothing here sizes, opens or closes a position, and the alert path's failure is a non-firing notification during an outage rather than a wrong trade. It is still a textbook instance of the repo's central rule (a confident negative about a leveraged lending book, manufactured from a failed read) and the sibling modules already carry the fix, so HIGH.

**Evidence**:

```
app/lib/defi.js:135-143
  const [aaveRes, uniRes, lidoRes] = await Promise.all([
    Promise.all(chains.map(c => readAave(c, address).catch(() => undefined))),
    Promise.all(chains.map(c => readUniswapCount(c, address).catch(() => undefined))),
    chains.some(c => c.key === 'ethereum')
      ? readLido(address, tickers).catch(() => undefined) : null,
  ]);

  const aave = aaveRes.filter(Boolean);

readAave returns `null` for "no position on this chain" (app/lib/defi.js:87: `if (collateral <= 0 && debt <= 0) return null;   // no position on this chain`) and the `.catch(() => undefined)` returns `undefined` for "the RPC would not answer". `filter(Boolean)` erases the difference and nothing else in the payload records it (app/lib/defi.js:163-176 returns only read_only/address/aave/lido/uniswap/warnings/note/generated_at).
```

## B4-16 [HIGH] buildExposure renders a failed `trades` query as a flat book — "No directional exposure found — no open positions" and $0 net/gross

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/lib/exposure.js:106-114`

**Observed**: `openTrades` stays `[]`, computeExposure produces `assets: []`, `net_total_usd: 0`, `gross_total_usd: 0`, `warnings: []`, `open_positions: 0`, and the route answers HTTP 200 (app/routes/exposure.js:16-17). The dashboard panel's mustRead() at app/public/js/dashboard.js:3694 therefore passes, `if (!d || !(d.assets || []).length) return null;` (dashboard.js:3695) sends it to the empty state, and dashboard.js:3716 renders 'Exposure appears once you have open positions or non-stable wallet holdings.' — the exact same empty state is wired a second time at dashboard.js:7227. The chat reply asserts 'no open positions' outright.

**Root cause**: An unconditional `catch (e) { /* section empty */ }` with no out-of-band record. "I could not read your positions" and "you have no positions" become the same value, and every consumer downstream is structurally unable to tell them apart.

**Business impact**: A user checking 'am I overexposed?' during a database hiccup is told they hold nothing directional, at $0 net and $0 gross, on their own real money — and the stacked_long / concentrated risk-desk warnings (app/lib/exposure.js:70-86) all silently disappear with it. CLAUDE.md names this class directly: 'a 500 on /api/holdings told the user they held nothing. That is a lie about their own money.'

**Reachability**: Three live callers: GET /api/exposure (app/routes/exposure.js:15-22, authed, mounted at app/server.js:403 `app.use('/api/exposure', require('./routes/exposure'))`), the dashboard panel (app/public/js/dashboard.js:3691-3716 and again at 7217-7227), and `maybeHandleExposureChat` from the chat route. The one consumer that survives this by accident is app/routes/guardian_readiness.js:96-100, which requires `exp.assets.length >= 2` and so leaves its concentration axis null. No upstream guard: `pool.execute` rejecting on a DB outage is the ordinary failure mode, and the catch is unconditional.

**Remediation**: Track it the way the wallet half already is: set a flag in the catch (`let positionsReadable = true; … catch (e) { positionsReadable = false; }`) and return it beside `wallet_included`. Then app/public/js/dashboard.js:3695 must paint an unreadable state rather than the empty one when `positions_readable === false`, and the chat branch at app/lib/exposure.js:143 must say the positions could not be read instead of 'no open positions'. Note `computeExposure` itself is pure and correct — the fix is entirely in buildExposure + its two renderers, so no ratchet baseline is disturbed.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — None. Only nuance: the trigger is a failing `pool.execute` on the trades table, so in a full DB outage several sibling panels would also be visibly broken — but nothing structurally prevents a query-scoped failure (lock/timeout) producing this lie on its own.

- refuted=False sev→MEDIUM — The reachability claim overstates the trigger. app/auth.js:237-244 runs `tokenIsCurrent` — a `SELECT token_epoch FROM users WHERE id = ?` — on every authed request and returns **503 auth_unavailable** when it throws, so a full DB outage never reaches buildExposure; the request 503s and mustRead() paints the error state correctly. The defect therefore needs a PARTIAL failure (a lock/deadlock or statement timeout on `trades` specifically, or pool exhaustion hitting one query) rather than 'the ordinary failure mode'. That is real but narrower, so MEDIUM rather than HIGH.

**Evidence**:

```
app/lib/exposure.js:105-114
/** Load the caller's open positions + wallet and compute. Fails soft. */
async function buildExposure(userId) {
  let openTrades = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, size_usd FROM trades
        WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC`, [userId]);
    openTrades = rows;
  } catch (e) { /* section empty */ }

and the claim built on it, app/lib/exposure.js:143-147
    if (!e.assets.length) {
      return {
        reply_html: 'No directional exposure found — no open positions'
          + (e.wallet_included ? ' and no non-stable wallet holdings.' : ', and no wallet linked.'),
```

## B4-17 [HIGH] Escape planner reads a 502 wallet response as "No linked wallet found", and an all-RPC-down wallet as "no priced balances, so there is no book to build"

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/public/escape.html:341-342`

**Observed**: An HTTP 502 from /api/wallet/portfolio (its catch-all, app/routes/wallet.js:58-61) renders as 'No linked wallet found. Link one in Account, then come back.' — telling a user who HAS linked a wallet to go link one. Separately, a wallet whose every chain RPC is down renders as 'The linked wallet mirrors no priced balances, so there is no book to build.' — a confident statement that the user owns nothing, even though the payload it just parsed carries `error: 'rpc unreadable'` on every chain and the code never looks at it.

**Root cause**: `!r.ok` is folded into the same branch as `!d.linked`, so transport failure and a genuine absence share one sentence; and the second branch reads only the flattened `assets` array while ignoring the per-chain `error` markers that `readChain` deliberately attaches (app/lib/wallet.js:274-277).

**Business impact**: This is the emergency-exit screen — the surface a user opens precisely because something is going wrong. Telling them their book is empty, or that no wallet is linked, at the moment they are trying to plan an unwind is the same failure CLAUDE.md records for `_cmd_escape`: 'An all-clear on the emergency-exit screen, assembled from a failure, shown to someone reading it precisely because something is wrong.'

**Reachability**: Reachable: the handler is bound to the page's `loadWallet` button (app/public/escape.html:325-326, `var btn = $('loadWallet'); btn.disabled = true;`) and /escape is a served public page (app/public/escape.html exists in the static root; guardian-console.js:134 links to it as 'Open the Escape planner →'). The upstream 502 is real and unconditional (app/routes/wallet.js:58-61). The all-chains-unreadable payload is real and was produced by running lib/wallet.js above. Nothing upstream prevents either.

**Remediation**: Split the first branch: `if (!r.ok) { walletNote(T('sx.w_failed', …), 'bad'); return; }` before the `!d.linked` check. In the second, compute `var unreadable = (d.chains || []).filter(function (c) { return c.error; })` and, when it is non-empty, say the chains could not be read rather than that the wallet is empty. Both changes are local to app/public/escape.html; bump its script cache-buster per CLAUDE.md's 'Verifying a deploy' note.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Stands. Severity trimmed HIGH->MEDIUM: the planner is a client-side planning aid that cannot act, the page keeps a working sample book, and the misdirection is 'go link a wallet' rather than a number the user could trade on. The two-line fix and its placement are correct as described.

- refuted=False sev→MEDIUM — Two corrections. (a) Severity: this is a planning-page hint on a page that changes nothing (the sample book still works, and `sx.w_failed` itself says 'nothing was changed') — misdirection, not a money-moving claim, so MEDIUM. (b) Scope: the finding is filed against escape.html only, but app/public/stress.html:236-248 contains the byte-identical conflation — including the same `!r.ok || !d || !d.linked` fold — and stress.html is the file whose test's own comment says 'Signed out, no wallet, empty wallet and a failed read are four different answers. Collapsing them would tell a signed-out user they have no wallet.' Any fix must land on both pages or the test's stated intent stays false on the page it was written for.

**Evidence**:

```
app/public/escape.html:339-342
        headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
      var d = await r.json().catch(function () { return null; });
      if (!r.ok || !d || !d.linked) { walletNote(T('sx.w_none', 'No linked wallet found. Link one in Account, then come back.'), 'bad'); return; }
      var assets = (d.assets || []).filter(function (a) { return Number(a.usd) > 0 && a.symbol && a.chain; });
      if (!assets.length) { walletNote(T('sx.w_empty', 'The linked wallet mirrors no priced balances, so there is no book to build.'), 'bad'); return; }
```

## B4-18 [MEDIUM] Wallet chat reply says "no balances found across the tracked chains" when every chain reported `rpc unreadable`, and sums dead chains' zeros into a printed total

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/lib/wallet.js:356-365`

**Observed**: Two separate claims from an unread wallet. (1) The `!withAssets.length` branch prints 'no balances found across the tracked chains among the tracked assets' with no mention that nothing could be read. (2) In the populated branch, app/lib/wallet.js:372-373 `const total = chainFilter ? withAssets.reduce((a, c) => a + (c.total_usd || 0), 0) : p.total_usd;` — `p.total_usd` sums `total_usd: 0` from every dead chain (confirmed above: `total_usd = 0` with both chains flagged `rpc unreadable`), and it is then printed as 'Total (priced): $X' with the unreadable note relegated to a muted trailing line.

**Root cause**: The readability evidence (`chain.error`) is produced correctly by readChain but consulted in only one of the two rendering branches, and the total is computed before the readability question is asked.

**Business impact**: A user asking the chat 'what's in my wallet' during an RPC outage is told their wallet is empty, or is given a dollar total silently missing whole chains. Lower blast radius than the DeFi and exposure findings because the wallet is read-only mirror data and the populated branch does eventually print the unreadable note — but it is still a false statement about the user's own holdings.

**Reachability**: Reachable: `maybeHandleWalletChat` is exported (app/lib/wallet.js:392-401) and driven by the chat route's intercept chain. The all-chains-unreadable payload was produced by running lib/wallet.js directly. No upstream guard — readWallet never throws by design (each chain fails soft into `error: 'rpc unreadable'`), which is precisely why the caller must read the flag.

**Remediation**: Hoist `const unreadable = sections.filter(c => c.error).map(c => c.label);` (currently app/lib/wallet.js:373) above the `!withAssets.length` branch and, when it is non-empty, reply that those chains could not be read instead of that no balances were found. For the total, follow networth.js: when any in-scope chain carries `error`, render the total as unreadable (or state 'partial — N chains unread') rather than a summed number.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — Claim (1) is solid. Claim (2) is weaker than stated: the populated branch does name the unreadable chains one line under the total, so it is an omit-with-notice rather than a silent lie — the objection is that networth.js chose GUARD for the same payload. Fix the empty branch first; the total is a consistency argument, not an independent defect.

- refuted=False sev→LOW — Part (2) of this finding is wrong and should be dropped. `p.total_usd` is NOT a sum that includes dead chains' zeros — readWallet computes it as `round2(priced.reduce((a, x) => a + x.usd, 0))` over the flattened ASSET list (wallet.js:289-296), and a dead chain contributes no assets at all, so it adds nothing rather than adding a zero. More importantly the populated branch already discloses the omission on the same reply: `(unreadable.length ? '<br><span class="muted">' + unreadable.join(', ') + ' unreadable right now (RPC).</span>' : '')` (wallet.js:378). That is CLAUDE.md's 'omit with the omission stated' strategy, which is legitimate for a composite view; calling for networth.js-style guard here is a preference, not a defect. Only the empty-branch half survives, and since the same chat surface already tells the user elsewhere, LOW.

**Evidence**:

```
app/lib/wallet.js:356-365
    const sections = (p.chains || [])
      .filter(c => !chainFilter || c.chain === chainFilter);
    const withAssets = sections.filter(c => c.assets.length);
    if (!withAssets.length) {
      const scope = chainFilter
        ? `on ${sections[0] ? sections[0].label : chainFilter}` : 'across the tracked chains';
      return {
        reply_html: `👛 <b>${short}</b> — no balances found ${scope} among the tracked assets.`,
        intent: 'wallet',
      };
    }

The evidence it ignores is computed nine lines later, app/lib/wallet.js:373:
    const unreadable = sections.filter(c => c.error).map(c => c.label);
```

## B4-19 [MEDIUM] Idle-yield reports `available: true` with "No priced idle assets found in your wallet" when every chain RPC failed; the wallet-address lookup failing renders as "Link a wallet"

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/lib/idle_yield.js:43-63`

**Observed**: Two conflations. (1) All chains unreadable → `available: true, wallet_linked: true, recommendations: []` and the dashboard renders app/public/js/dashboard.js:3609 `<p class="muted">${esc(d.note || 'No idle assets matched a known rate right now.')}</p>` — a confident negative about the user's own holdings. The panel's own comment two lines up (dashboard.js:3595-3598: 'available:false is the scanner FAILING soft inside a 200 … a failure wearing an empty state's clothes') describes precisely the case it is then not protected against. (2) `walletAddressOf` throwing leaves `address = null` → 'Link a wallet (Sign-In with Ethereum)…' shown to a user who has one; app/routes/cross_yield.js:30 spells the same conflation explicitly with `.catch(() => null)`.

**Root cause**: A single `catch` covering two different reads (the DB address lookup and the chain sweep) resets both to their 'nothing here' value, and `holdingsFromWallet` (app/lib/idle_yield.js:20-30) correctly skips unpriced assets but its caller cannot tell 'skipped because unpriced' from 'skipped because unread'.

**Business impact**: A user is told they have no idle capital to put to work when the truth is that nothing could be read; the cross-yield planner separately tells a user with a linked wallet to go link one. Advisory surfaces, so no funds move — but both are confident negatives about the user's own money, and `available: true` is an explicit assertion that the read worked.

**Reachability**: Reachable: `buildIdleYield` is called by the /api/idleyield route and by `maybeHandleIdleYieldChat` (app/lib/idle_yield.js:83), and again by app/routes/cross_yield.js:64. The dashboard panel at app/public/js/dashboard.js:3592-3612 renders it. The failing path was produced by running the module. `getWalletPortfolio` never throws (readChain fails soft), so branch (1) — the all-RPC-down case — is the reachable one; branch (2) requires the users-table read to fail, which is the ordinary DB-outage mode.

**Remediation**: Separate the two try blocks: let a failing `walletAddressOf` produce `available: false` rather than `wallet_linked: false`. Read the per-chain markers from the portfolio (`p.chains.some(c => c.error)`) and return `available: false` (or `partial: true` + `unreadable_chains`) instead of the 'No priced idle assets' note when the emptiness came from failed reads. Apply the same to app/routes/cross_yield.js:30, replacing `.catch(() => null)` with a branch that answers 'could not check whether a wallet is linked'.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — None. Additional (same line): getWalletPortfolio returns null for a non-0x address (wallet.js:306), so a Solana-only linked wallet also lands in the 'No priced idle assets' branch — same conflation, another door.

- refuted=False sev→LOW — Branch (2) — 'a throwing walletAddressOf renders as Link a wallet' — is effectively guarded upstream and should be dropped from the finding. walletAddressOf runs `SELECT * FROM users WHERE id = ?` (wallet.js:314-317), and app/auth.js:237-244 has already run `SELECT token_epoch FROM users WHERE id = ?` on the same table for the same request, returning 503 auth_unavailable if it throws; the same applies to app/routes/cross_yield.js:30's `.catch(() => null)`, which sits behind the same authMiddleware. So the users-table read failing while auth's identical read succeeded is a narrow race, not 'the ordinary DB-outage mode'. With only branch (1) surviving on a recommendation-only surface that moves no funds, LOW.

**Evidence**:

```
app/lib/idle_yield.js:42-62
  let holdings = [];
  let address = null;
  try {
    address = await wallet.walletAddressOf(userId);
    if (address) {
      const p = await wallet.getWalletPortfolio(address);
      holdings = holdingsFromWallet(p);
    }
  } catch (e) {
    holdings = [];
  }
  if (!address) {
    return { read_only: true, available: true, wallet_linked: false,
      recommendations: [], note: 'Link a wallet (Sign-In with Ethereum) to '
        + 'scan your idle on-chain assets for the best non-custodial rate.' };
  }
  if (!holdings.length) {
    return { read_only: true, available: true, wallet_linked: true,
      recommendations: [], note: 'No priced idle assets found in your wallet '
        + 'on the tracked chains.' };
  }

and the same conflation in the planner, app/routes/cross_yield.js:30:
    const address = await wallet.walletAddressOf(uid).catch(() => null);
```

## B4-20 [MEDIUM] Agent-picks panel: a failed /api/copy/picks read hides the whole panel, and a failed `signals` query renders as "No live signal matches this agent's gates right now"

- **Dimension**: honesty-js · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/public/js/dashboard.js:6300-6310`

**Observed**: (a) A failed panel read hides the panel with no error state and no Retry. (b) A failed read of the `signals` table renders, per followed agent, 'No live signal matches this agent's gates right now' — a statement about what the engine is currently signalling, produced by a DB failure. The payload carries no marker distinguishing this from a genuinely quiet market.

**Root cause**: `loadAgentPicks` is a direct-innerHTML writer outside `renderPanel`, so it inherits none of the mustRead machinery; and `routes/copy.js` swallows the same `signals` read that `routes/signals.js` was already hardened for.

**Business impact**: A user who follows agents to copy their calls is told, agent by agent, that nothing matched — when the signal stream simply could not be read. Lower severity than the money-reading panels because no position is opened on this evidence, but it is a false negative about the product's core output, and the panel-vanishing path removes the surface entirely without saying so.

**Reachability**: Reachable: `loadAgentPicks` is defined at app/public/js/dashboard.js:6280 and invoked at dashboard.js:6332 and again at 6716; it only runs for users with `_agentFollows.size > 0` (dashboard.js:6296), i.e. users who actually follow an agent — the exact audience the claim misleads. GET /api/copy/picks is a live authed route (app/routes/copy.js:105).

**Remediation**: In app/routes/copy.js, mark the failure: set a `signals_readable: false` flag in the catch at line 118 (or let the route answer 503 as routes/signals.js does) and have `loadAgentPicks` render 'the signal stream could not be read' rather than 'No live signal matches…'. In app/public/js/dashboard.js:6300, distinguish `!r.ok` from an empty list — show an error line with a Retry instead of `panel.hidden = true`.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — None. Worth folding in: the route's OUTER catch (app/routes/copy.js:149-151) also answers 200 `{agents: [], note: ''}` on any failure, which the same client line turns into a vanished panel — a second door to the same lie.

- refuted=False sev→MEDIUM — One reachability caveat worth recording: /api/copy/picks calls `followingIds(req.user.user_id)` before the signals query and the outer catch turns a throw into a 500, and authMiddleware already 503s on a dead users table — so the 'No live signal matches this agent's gates right now' lie needs the signals query specifically to fail (statement timeout on the LIMIT 100 scan, a lock, pool exhaustion) while the follows query succeeds. Half (a), the vanishing panel, needs no such condition and is the more reachable of the two.

**Evidence**:

```
app/public/js/dashboard.js:6299-6310
      let d = null;
      try { const r = await fetchJSON('/api/copy/picks', { timeoutMs: 16000 }); d = r.ok ? r.data : null; } catch (_) {}
      const groups = (d && d.agents) || [];
      if (!groups.length) { panel.hidden = true; return; }
      …
        if (!g.picks || !g.picks.length) {
          return head + `<p class="small muted">No live signal matches this agent's gates right now${…}</p>`;
        }

and the server half, app/routes/copy.js:111-118
    let signals = [];
    try {
      const [rows] = await pool.execute(
        `SELECT signal_key, symbol, direction, confidence, score, pattern, regime,
                entry_price, stop_loss, take_profit, rr, thesis, created_at
         FROM signals WHERE status = ? ORDER BY created_at DESC LIMIT 100`, ['OPEN']);
      signals = rows;
    } catch (e) { /* empty stream is fine */ }
```

## B4-21 [LOW] Fear & Greed: a non-numeric value is coerced to 0 ("Extreme Fear") instead of being omitted, inside a function that omits every other unreadable input

- **Dimension**: honesty-js · **Confidence**: HIGH · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/routes/macro.js:184-186`

**Observed**: An unreadable sentiment value becomes a measured 0/100. It is then printed as a number in the deterministic brief (app/routes/macro.js:100: `Crypto sentiment is ${fg.classification} at ${fg.value}/100${d}`), given the largest weight in the blended risk score (app/routes/macro.js:197: `if (out.fear_greed) parts.push({ w: 0.62, v: out.fear_greed.value });`), and fed verbatim to the LLM brief prompt (app/routes/macro.js:216). `previous` has the same shape, so a bad previous reading prints 'down 54 from yesterday' from no data.

**Root cause**: `?? 0` re-introduces the zero that `num()` was written to avoid, on the one field in the function that uses it.

**Business impact**: A public macro read would announce 'Risk-Off / Extreme Fear' at 0/100 from a field nobody could parse, and would hand that number to the LLM brief as fact. No funds move on this surface, hence LOW — but it is a market claim manufactured from an absence, on the one page whose sibling (site/src/live.ts) is built entirely around not doing this.

**Reachability**: Reachable: GET /api/macro (app/routes/macro.js:249) is public and unauthenticated, `assembleMacro` is called at app/routes/macro.js:275 and exported at :286. The route's own try/catch around the fetch (app/routes/macro.js:263-267) only degrades on a network/JSON failure — a successful fetch carrying an unparseable `value` is exactly the case this does not cover. I could not demonstrate alternative.me actually returning a non-numeric value, so the trigger is upstream-shape-dependent; the coercion itself is certain.

**Remediation**: Replace with `const v = num(fng.value); if (v != null) { out.fear_greed = { value: clamp(v, 0, 100), … , previous: (() => { const p = num(fng.previous); return p == null ? null : clamp(p, 0, 100); })() }; out.sources.push('fear_greed'); }`. The blend already handles the section being absent.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — The coercion is certain; the trigger is not demonstrated and is narrower than implied (an empty string coerces to 0 through Number(), not through the `?? 0`). Treat as a real consistency defect worth a two-line fix, not as an observed failure.

- refuted=False sev→LOW — Confidence HIGH is too strong, and for a reason the finder did not notice: the most plausible garbage input does not even reach the `?? 0`. `num('')` is `Number('') === 0`, which is finite, so an empty-string value yields 0 through `num` itself, not through the `??`; only genuinely non-numeric text ('n/a', an object) takes the `?? 0` arm. Either way the trigger requires alternative.me to change shape, which the finder could not demonstrate. Real and worth the two-line fix, but LOW / MEDIUM-confidence, not a defect anyone has hit.

**Evidence**:

```
app/routes/macro.js:182-188
  if (fng && fng.value != null) {
    out.fear_greed = {
      value: clamp(num(fng.value) ?? 0, 0, 100),
      classification: String(fng.classification || '').trim() || 'Unknown',
      previous: fng.previous != null ? clamp(num(fng.previous) ?? 0, 0, 100) : null,
    };
    out.sources.push('fear_greed');
  }

`num` is defined at app/routes/macro.js:71 as
  const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : null; };
so it already answers null for unreadable — and every other field in the same function keeps that null (app/routes/macro.js:153-160: `market_cap_usd: num(g.mcap_usd)`, `btc_dominance: btcDom`, `structure: btcDom == null ? null : …`).
```

## B4-22 [CRITICAL] Migration fast path checks TABLE existence only, so all 64 column/index migrations are permanently skipped on any existing deployment

- **Dimension**: data-db · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/db.js:2219-2222`

**Observed**: The fast path answers "is the schema current?" with "do all 38 tables exist?". Every column, unique index and PRIMARY KEY change is invisible to that question, so on any database that already has the tables the migration is a no-op forever. app/test/migration_ddl.test.js pins the fast path against a missing TABLE (`EXPECTED_TABLES matches the DDL exactly`, `the fast path requires EVERY table, not merely some`) and never against a missing COLUMN, so the gap is not covered.

**Root cause**: `schemaIsCurrent()` uses table presence as a proxy for schema version. `EXPECTED_TABLES` is derived from the CREATE TABLE statements only (pinned by app/test/migration_ddl.test.js:109), so the 64 ALTER/CREATE INDEX statements have no representation in the check that gates them.

**Business impact**: On an existing production database this is a silent, permanent divergence between the code's assumptions and the schema. Concretely: the `trades.event_id` UNIQUE index that the code documents as the durable authority for trade-delivery idempotency does not exist, so a bot retry after a lost response double-inserts a trade into the P&L history; and if `users.token_epoch` is absent, every authenticated request 500s because `tokenIsCurrent()` fails closed. Every future column-only release is guaranteed to be a no-op on production while passing CI (which starts from an empty database and therefore always runs the full DDL).

**Reachability**: `migrate()` is the app's boot-time schema step and `schemaIsCurrent()` is only reachable from it (app/db.js:2219). The consequences are on hot paths: `app/auth.js:196-197` runs `SELECT token_epoch FROM users WHERE id = ?` on EVERY authenticated request via `tokenIsCurrent()`, and `app/routes/sync.js:461-497` inserts `event_id` on every bot trade-open/close delivery, treating only ER_DUP_ENTRY as recoverable (`_isDuplicateKey`, app/routes/sync.js:421-424) — an ER_BAD_FIELD_ERROR from a missing column is rethrown and becomes a 500.

**Remediation**: Extend the check past table names. Two low-churn options: (a) add an `EXPECTED_COLUMNS` map (table -> required columns) built beside the DDL and pinned by the same test, and require both sets before taking the fast path; or (b) keep a `schema_migrations` table and record a version, taking the fast path only when the recorded version equals the code's. Either way the ALTERs are already individually idempotent (`try { ... } catch (e) { /* exists */ }`), so running them is cheap — the fast path only needs to stop hiding them.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Retitle to the latent form: 'the fast path uses table presence as a schema version, so the NEXT column-only migration will be skipped on every existing deployment.' The current 64 ALTERs are not in fact missing anywhere — each one landed alongside a later CREATE TABLE that forced the full DDL. Severity CRITICAL -> MEDIUM: no current data or request path is broken; the cost is the next migration, silently. The proposed fix (EXPECTED_COLUMNS pinned by the same test, or a schema_migrations version row) is still the right one and is cheap because the ALTERs are already individually idempotent.

- refuted=False sev→MEDIUM — Retitle to the latent form: 'schemaIsCurrent uses table presence as a schema-version proxy, so a future column-only migration is skipped forever on existing databases.' Drop the claim that all 64 ALTERs are currently skipped and drop the derived claim that auth.js token_epoch and sync.js event_id are missing columns in production today — that would send an engineer hunting a prod schema gap that the evidence does not establish. The proposed fix (EXPECTED_COLUMNS, or a schema_migrations version row) is sound and low-churn.

**Evidence**:

```
app/db.js:2189-2196 (the check):
```
async function schemaIsCurrent() {
  try {
    const [rows] = await pool.query(
      'SELECT TABLE_NAME AS t FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()');
    const have = new Set((rows || []).map((r) => String(r.t || r.TABLE_NAME || '').toLowerCase()));
    return EXPECTED_TABLES.every((t) => have.has(t.toLowerCase()));
```
app/db.js:2219-2222 (the skip — it returns before a single ALTER runs):
```
    if (await schemaIsCurrent()) {
      console.log('Schema already current — skipping DDL');
      return;
    }
```
app/db.js:2923-2924 — the durable trade idempotency key exists ONLY as an ALTER (it is absent from the `CREATE TABLE IF NOT EXISTS trades` block at app/db.js:2350-2371):
```
    try { await pool.execute('ALTER TABLE trades ADD COLUMN event_id VARCHAR(64) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE trades ADD UNIQUE INDEX idx_trades_event_id (event_id)'); } catch (e) { /* present */ }
```
```

## B4-23 [HIGH] Arena position close has no transaction and no affected-rows claim — concurrent closes write duplicate sealed trade receipts and can double-credit the balance

- **Dimension**: data-db · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/routes/arena.js:753-777`

**Observed**: Two `arena_trades` rows for one position, each with an identical Provable-Calls `trade_key` and `seal`. Both requests return 200 with `ok:true`. Nothing checks that the DELETE actually removed a row, and nothing makes the read-modify-write of `arena_accounts.balance` atomic.

**Root cause**: `closeForUser` (and `settleLiquidations`) does check-then-act across five awaits — including a network fetch (`getTickers()`) — with no transaction, no row lock, and no idempotency key. `arena_trades.trade_key` is indexed but NOT unique (app/db.js:2789-2814: `INDEX idx_arena_tr_key (trade_key)`), so the database does not reject the second receipt either. The balance is written as an absolute value computed from a snapshot read before the writes, so an interleaving where the second request's `loadAccount` lands after the first request's UPDATE credits margin+pnl twice.

**Business impact**: The duplicated row is a sealed Provable-Calls receipt: `computeLeaderboard` (app/routes/arena.js:805-818) publishes `COUNT(*) AS n FROM arena_trades GROUP BY user_id` as the trader's close count and `COUNT(*) ... WHERE seal IS NOT NULL` as the count of verifiable receipts. So the public leaderboard advertises trades that never happened, and advertises them as cryptographically verifiable. The paper win/loss record and the weekly Arena letter (app/routes/arena.js:668-670) are built from the same table. In the wider interleaving the paper balance is credited twice, which is percent-return the ranking is computed from.

**Reachability**: `closeForUser` is reachable from `POST /api/arena/close` (app/routes/arena.js:789, authMiddleware + tradeLimit 20/min — a per-minute cap does not serialize two simultaneous requests) and from the MCP tool surface (app/routes/mcp.js references arena_positions/arena_trades/arena_accounts). `settleLiquidations` runs inside `GET /api/arena/account` (app/routes/arena.js:248, authMiddleware ONLY, no rate limit) — a route the file's own comment at line 120 says "the Arena page and the dashboard's Arena card can both" hit. Both are same-user endpoints, so a double-click or two open tabs is enough; no second account is needed.

**Remediation**: Make the DELETE the claim. Move `DELETE FROM arena_positions WHERE id = ? AND user_id = ?` to the front and only proceed when `res.affectedRows === 1`; the loser returns 404 as it should. That is one atomic statement in MySQL and it also works in the in-memory shim (which returns affectedRows). Then settle the balance with a relative update — `UPDATE arena_accounts SET balance = ROUND(balance + ?, 2) WHERE user_id = ?` — so no snapshot is carried across an await. Apply the same claim to `settleLiquidations` (app/routes/arena.js:227-231) and use a relative update in `openForUser`/`sweepFollows` too.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Severity HIGH -> MEDIUM: this is the paper arena, so the loss is a corrupted virtual balance and a duplicated tamper-evident receipt, not real funds. The proposed fix is sound and minimal — make the DELETE the claim (move it first, proceed only on affectedRows === 1) and switch to a relative `SET balance = ROUND(balance + ?, 2)` so no snapshot crosses an await. Note the same absolute-write-from-snapshot also appears in sweepFollows (arena.js:193).

- refuted=False sev→MEDIUM — Keep the finding and the fix (DELETE-as-claim on affectedRows === 1, then a relative `balance = ROUND(balance + ?, 2)` update). Reframe the impact as record/leaderboard integrity in a virtual-balance arena rather than money loss, and state the outcome as race-dependent — the code shape is CONFIRMED, the duplicate receipt is inferred from it, not observed.

**Evidence**:

```
app/routes/arena.js:753-777 — SELECT, then a network round trip, then INSERT/DELETE/UPDATE with nothing claiming the position:
```
    const [rows] = await pool.execute(
      'SELECT id, user_id, symbol, ... FROM arena_positions WHERE id = ? AND user_id = ?', [posId, userId]);
    const p = rows[0];
    if (!p) return { status: 404, body: { error: 'Position not found' } };
    let marks;
    try { marks = await getTickers(); } catch (e) { ... }
...
    const acct = await loadAccount(userId);
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, ... ) VALUES (...)', [...]);
    await pool.execute('DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + p.margin + pnl), userId]);
```
The same shape in the auto-settle sweep, app/routes/arena.js:229-241:
```
    await pool.execute(
      'DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    if (exit.reason !== 'liquidated') credit += p.margin + pnl;
  }
  if (credit !== 0) {
    const acct = await loadAccount(userId);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + credit), userId]);
```
`grep -n 'affectedRows' app/routes/arena.js` → no matches. `grep -rn 'getConnection|beginTransaction|START TRANSACTION|commit()|rollback()' app/ --include=*.js` (excluding node_modules) → no matches anywhere in the Node app.
```

## B4-24 [HIGH] Bot sync acks delete the pending row by user_id unconditionally, silently discarding a control or credential change submitted during the pull→ack window

- **Dimension**: data-db · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `app/routes/sync.js:1063`

**Observed**: The DELETE is scoped only by user_id, so it retires whatever proposal is sitting in the row at ack time. Because `user_id` is UNIQUE, a change submitted during the window overwrites the pulled row rather than queueing, so it is destroyed rather than merely delayed.

**Root cause**: No optimistic-concurrency token on the queue row. The pull returns `created_at` (which the UPSERTs at app/routes/controls.js:131-135 and 245-251 refresh to CURRENT_TIMESTAMP on every submit) but the ack body never carries it back, so the delete cannot distinguish "the row I applied" from "a newer row".

**Business impact**: For pending_controls: a user's emergency stop, live-trading disable, pause, or margin-cap reduction is acknowledged to them and then silently discarded — the bot keeps trading live under the old cap. For pending_credentials: a user who submits a `disconnect` (revoke my exchange API keys) inside the window has that revocation deleted, and the ack then writes `exchange_status.connected = true` from the ORIGINAL connect action (app/routes/sync.js:1014-1022), so the badge says connected, the keys stay in the bot's Fernet store, and the user believes they revoked them.

**Reachability**: Both endpoints are live and bot-secret authed (`router.use(botAuth)`, app/routes/sync.js:287). The bot half is `bot/utils/control_pull.py:191-203` (`pull_and_apply_controls`), which does pull → per-row apply → ack as three separate steps with two HTTP round trips between them. The writing side is user-facing and unauthenticated by the bot secret: `POST /api/controls` (app/routes/controls.js:128), `POST /api/controls/venues` (:181), `POST /api/controls/stop` (:245). Nothing serialises the two.

**Remediation**: Return `created_at` from the pull into the ack body and scope the delete to it: `DELETE FROM pending_controls WHERE user_id = ? AND created_at <= ?`. No schema change is needed — both tables already carry `created_at` and the pull already selects it. Apply the identical change to the credentials ack at line 1013. Where the ack cannot carry it (an older bot), deleting nothing and letting the next cycle re-apply is the safe direction.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Severity HIGH -> MEDIUM. Real and correctly diagnosed, but the exposure is a one-round-trip window, and the ack's user_controls mirror makes the loss visible to the user rather than silent to them (it is silent to the system). The proposed fix — echo created_at through the ack and scope the DELETE with `AND created_at <= ?` — is correct and needs no schema change.

- refuted=False sev→MEDIUM — Keep the finding and the created_at-scoped delete fix. Lower the severity: the loss window is a single pull round trip, the UI shows the un-applied old state afterwards rather than a false success, and the emergency-stop flatten survives in its own table. Also correct 'the writing side is unauthenticated by the bot secret' — those routes sit behind the user auth middleware; they are simply not serialised against the bot channel, which is the real point.

**Evidence**:

```
app/routes/sync.js:1059-1063 — the ack deletes whatever is in the row now, not the row that was pulled:
```
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      await pool.execute('DELETE FROM pending_controls WHERE user_id = ?', [uid]);
```
Same shape for exchange credentials, app/routes/sync.js:1013:
```
      await pool.execute('DELETE FROM pending_credentials WHERE user_id = ?', [uid]);
```
The pull already carries the discriminator it would need — app/routes/sync.js:1037-1040:
```
      `SELECT user_id, telegram_id, live_enabled, max_margin, paused, venues, created_at
       FROM pending_controls ORDER BY created_at ASC LIMIT 200`
```
and the queue is one row per user — app/db.js:2670-2672 / 2644-2646: `user_id INT NOT NULL UNIQUE` on both tables, so a new proposal REPLACES the pulled one in place rather than queueing behind it.
```

## B4-25 [HIGH] Bot portfolio sync deletes all of a user's trades and equity snapshots before re-inserting them, with no transaction — a mid-loop failure leaves a truncated history that renders as a real record

- **Dimension**: data-db · **Confidence**: HIGH · **Fix class**: REVIEW_REQUIRED
- **File**: `app/routes/sync.js:310-355`

**Observed**: Delete-then-insert across up to N+M separate autocommitted statements. A failure at any point leaves the user's trade history permanently truncated (the bot only heals it on the next successful full sync), and every downstream surface computes a confident percentage over the fragment. Concurrent readers during the window see the same partial state.

**Root cause**: The route was written against a backend abstraction (`pool.execute`) that the in-memory fallback does not implement transactions for, and the code chose ordering-based safety for the single-trade endpoint (`/trade-event`, app/routes/sync.js:466-486, whose comment explicitly reasons about this) but never revisited the replace-all endpoint, where ordering alone cannot help.

**Business impact**: The user's track record — trade count, net P&L, win rate, equity curve — is the product's most load-bearing claim. A failed sync can erase it entirely (all trades deleted, no inserts land) or truncate it, and the dashboard will publish a win rate over the survivors as though it were the whole record. The equity curve is deleted in the same unguarded way whenever a real equity reading is present.

**Reachability**: `POST /api/bot/sync` is live behind `botAuth` (app/routes/sync.js:273-288) and is the bot's normal periodic portfolio push — its own docstring says "Replaces all trade data for the authorized bot user." The readers are `GET /api/portfolio/summary` (app/routes/sync.js:230-260) and app/routes/portfolio.js, both of which query `trades` directly with no notion of sync state.

**Remediation**: Two parts. (a) Make the rebuild atomic where the backend allows it: acquire a connection, `beginTransaction()`, do the deletes and inserts, `commit()`, `rollback()` on error — and for the in-memory shim (which the /trade-event comment correctly notes has no transactions), stage the inserts and swap only on success. (b) Independently of (a), record a per-user sync generation/timestamp on success and have `GET /api/portfolio/summary` refuse to report totals when the last sync for that user failed, rather than summing what is there. (b) alone removes the "partial total printed as whole" half and is the smaller change.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Severity HIGH -> MEDIUM, and the title's 'permanently' is wrong — the next successful sync heals it. Lead with the always-present read window rather than the mid-loop failure: the delete-all/insert-all pattern exposes a partial history to concurrent readers on every single sync, not only on failure. Fix (b) in the finding (a per-user sync generation that readers consult) addresses both halves and does not require transactions the in-memory backend cannot provide.

- refuted=False sev→MEDIUM — Keep the finding; drop 'permanently' (the next periodic full sync heals it) and drop the claim that GET /api/portfolio/summary reads the fragment in the normal case — it serves the previous complete in-memory summary during the window and only falls through to the DB after a restart. Fix (b) from the proposal (a per-user sync generation, and refusing to report totals after a failed sync) is still the right small change; (a) is blocked by the in-memory shim having no transactions.

**Evidence**:

```
app/routes/sync.js:310-322 — the destructive half runs first, unguarded:
```
    // Clear existing trades and snapshots for this user
    await pool.execute('DELETE FROM trades WHERE user_id = ?', [user_id]);
...
    if (eq !== null) {
      await pool.execute('DELETE FROM equity_snapshots WHERE user_id = ?', [user_id]);
    }

    // Insert closed trades
    if (closed_trades && closed_trades.length > 0) {
      for (const t of closed_trades) {
        await pool.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at, venue)
```
and the only failure handling, app/routes/sync.js:385-388:
```
  } catch (err) {
    console.error('Sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Sync failed' });
  }
```
There is no rollback because there is no transaction anywhere in the app: `grep -rn 'getConnection|beginTransaction|START TRANSACTION|\.commit()|\.rollback()' app/ --include=*.js` (excluding node_modules) returns nothing.
```

## B4-26 [MEDIUM] Trade journal writes non-atomically and loads partially in silence — a single malformed row truncates the record permanently and the weekly review reports a confident win rate over the fragment

- **Dimension**: data-db · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/trade_journal.py:296-297`

**Observed**: A single unparseable row silently stops the load at that point; `get_weekly_review()` then computes wins/losses/win_rate/total_pnl over the fragment and returns them as measured facts; and `_save()` writes the fragment back, destroying the unread rows. The only trace is `logger.debug`, below the default level.

**Root cause**: Two separate misses. (1) `_save` uses `open(path,'w')`, which truncates before writing, so a crash or a full disk mid-`json.dump` leaves a truncated file — the exact corruption `bot/utils/atomic_write.py` was written to eliminate. (2) `_load` mutates `self._entries` incrementally inside a broad `try`, so a partial parse is indistinguishable from a complete one to every caller, and the failure is logged below INFO.

**Business impact**: The journal is the bot's own record of every closed trade and the substrate for the weekly performance review an operator reads to decide whether the strategy works. A partial read publishes a specific, wrong win rate (100% in the reproduction) and the next save makes the loss permanent — there is no second copy. The 2026-07-31 incident CLAUDE.md records was exactly this class: published win rates composed from incomplete data.

**Reachability**: `TradeJournal` is instantiated once by the engine (`bot/core/engine.py:650-651: from bot.core.trade_journal import TradeJournal; self.journal = TradeJournal()`), so this is the live journal, not a test fixture. `get_weekly_review` is the data behind the `/journal` command (skill `trade_journal`, bot/skills/skill_registry.py:1323-1324). Single-process, so I am NOT claiming a multi-writer race here — the failure modes are crash-truncation and partial-load, both of which are single-process.

**Remediation**: Three small changes, none of which touch the ratchets. (1) `_save`: `from bot.utils.atomic_write import atomic_write_json` and `atomic_write_json(self._journal_file, data, separators=(",", ":"))` — the helper is already a dependency of 20+ modules. (2) `_load`: parse into a LOCAL list and assign to `self._entries` only after the whole file parses; on failure log at WARNING and set a `self._load_failed = True` flag. (3) `get_weekly_review`: when `_load_failed` is set, return the failure rather than statistics, so `/journal` paints an error state instead of a win rate. Note also that `_max_entries` is 1000 while `_save` writes `self._entries[-500:]` (line 283), so a restart silently halves a full journal — worth aligning while there.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Fix the reachability line: the caller is bot/skills/telegram_handler.py:10642 (_cmd_journal, admin-gated), not the trade_journal skill at skill_registry.py:1323, which reads portfolio._history instead. Severity MEDIUM -> LOW: the journal is analysis-only and admin-only, the total-failure path is already caught honestly by the _journal_gap_closes branch, and the partial-parse path needs a malformed row that _save cannot write. The two mechanical fixes still stand on their own merits — atomic_write_json in _save, and parsing into a local list before assigning self._entries.

- refuted=False sev→LOW — Fix the reachability sentence: get_weekly_review is reached from `_cmd_journal` at bot/skills/telegram_handler.py:10642 (admin-only), not from the `trade_journal` skill, which reads the executor's `portfolio._history` instead. Note the existing gap-check at telegram_handler.py:10663-10681 already covers the all-empty case honestly, which narrows the exposure to a partial load and drops the severity — this is an admin diagnostic over a secondary store whose own comment says /portfolio and /performance are the authoritative record.

**Evidence**:

```
bot/core/trade_journal.py:296-299 — truncate-then-write, while 20+ other stores in this repo use `bot.utils.atomic_write`:
```
            with open(self._journal_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.debug("Journal save failed: %s", exc)
```
bot/core/trade_journal.py:307-326 — entries are appended inside the try, so a failure mid-list leaves a partial list and says so only at DEBUG:
```
            for d in data:
                self._entries.append(JournalEntry(
                    trade_id=d["trade_id"], symbol=d["symbol"],
...
            logger.info("Loaded %d journal entries", len(self._entries))
        except Exception as exc:
            logger.debug("Journal load failed: %s", exc)
```
```

## B4-27 [MEDIUM] Two per-user preference stores fall back to a non-atomic truncate-then-write in clear() only, while their sibling save path uses atomic_write_json

- **Dimension**: data-db · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/core/user_leverage_store.py:89-91`

**Observed**: One of two mutation paths in each module still truncates in place. Because both stores hold a dict keyed by user id and rewrite the WHOLE dict, a failed `clear()` for one user destroys the preferences of EVERY user in that file, not just the one being cleared.

**Root cause**: Incomplete migration. `tests/test_atomic_write.py::TestTheShapeDoesNotComeBack` scans only for the `path + ".tmp"` / `with_suffix(".tmp")` family (its FORBIDDEN tuple, line 237-243), so a plain truncating write is invisible to the guard that exists to stop exactly this.

**Business impact**: `user_leverage_store` holds a per-user leverage preference that bot/core/leverage.py applies "only ever ... as a reduce vs the operator default" — so losing the file silently restores every BYOK live user to the HIGHER operator default leverage, with no error anywhere (`_load` returns `{}`, `get` returns None, the caller uses the default). That is a silent, global increase in position risk arising from one user clearing their own preference at the wrong moment. `user_strategy_store` losing its file silently un-gates every user's confirm flow.

**Reachability**: Both stores are live per-user preference stores reached from the Telegram/gateway command surface, and both `clear()` functions are the documented revocation path ("Remove a user's preference (→ back to the operator default)", "Revocable is the whole point; clearing always works"). Each is guarded by a module-level `threading.Lock`, so this is NOT a multi-writer race claim — the exposure is crash/ENOSPC during the truncate-write window. I checked the multi-process angle and ruled it out: Dockerfile:68 and docker-compose.yml:99 both now run `--workers 1`.

**Remediation**: Replace both blocks with the helper already imported in each file:
```
        try:
            atomic_write_json(_path(), d, indent=None)
        except Exception as exc:
            log.warning("user_leverage clear failed: %s", exc)
            return False
```
And widen `TestTheShapeDoesNotComeBack.FORBIDDEN` (or add a companion assertion) to catch `open(<state path>, "w")` in modules that already import atomic_write, so the next one is caught by the guard rather than by an audit.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Severity MEDIUM -> LOW. Correct, cheap, and worth doing — but it is an incomplete-migration tidy-up on a preference store, not a money-path defect, and losing the file degrades to the operator default rather than to a wrong trade. The companion suggestion (widen the atomic_write guard to flag open(<state path>, 'w') in modules that already import atomic_write) is the more valuable half, since it is what stops the next one.

- refuted=False sev→LOW — Keep as written; downgrade severity to LOW. The fix is two lines per module plus widening the atomic_write guard's FORBIDDEN set — worth doing precisely because it is cheap, not because the loss is likely.

**Evidence**:

```
bot/core/user_leverage_store.py:66-72 — `set_pref` was migrated:
```
    with _LOCK:
        d = _load()
        d[uid] = n
        try:
            atomic_write_json(_path(), d, indent=None)
```
bot/core/user_leverage_store.py:84-94 — `clear` was not:
```
    with _LOCK:
        d = _load()
        if uid not in d:
            return False
        del d[uid]
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("user_leverage clear failed: %s", exc)
```
Identical omission in bot/core/user_strategy_store.py:163-173, whose own `_save` helper at lines 47-53 does use `atomic_write_json`:
```
        del d[uid]
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("user_strategy clear failed: %s", exc)
```
```

## B4-28 [LOW] siwf_nonces has no retention: an unauthenticated endpoint inserts a permanent row per call and nothing ever deletes one

- **Dimension**: data-db · **Confidence**: CONFIRMED · **Fix class**: SAFE_AUTO_FIX
- **File**: `app/routes/farcaster_auth.js:75-78`

**Observed**: Monotonic growth with no ceiling. `siwf_nonces` (app/db.js:2965-2972) is `nonce VARCHAR(64) PRIMARY KEY, created_at, expires_at, used_at` with no additional index and no cleanup path.

**Root cause**: The design decision to mark rather than delete on use ("the row is not deleted on use, so a replayed nonce is DISTINGUISHABLE from one that never existed", app/db.js:2960-2964) is correct and deliberate, but the corresponding retention policy for rows that are long past `expires_at` was never added.

**Business impact**: Slow-burn operational cost rather than a correctness failure: unbounded growth on a serverless/managed MySQL bills for storage and eventually degrades the sign-in path's primary-key lookups. It also gives an unauthenticated caller a cheap, permanent write into the operator's database.

**Reachability**: `POST /api/farcaster/nonce` is mounted and unauthenticated by design (it is the first step of sign-in). The rate limiter at line 40 bounds per-IP rate but not total rows and not the number of IPs. I confirmed there is no cleanup elsewhere: the only other DELETE-style retention in the app is the agent_events prune at app/routes/sync.js:772.

**Remediation**: Add an opportunistic prune on the write path, mirroring wallet_link_store: after a successful INSERT in `issue()`, best-effort `DELETE FROM siwf_nonces WHERE expires_at < ?` with a cutoff well past NONCE_TTL_MS (e.g. 24h) so the replay-vs-never-existed distinction is preserved for any realistic replay window. Add `INDEX idx_siwf_expires (expires_at)` in the same change — but note that as a column-only/index-only migration it will be skipped on existing deployments until the schemaIsCurrent finding is fixed, so land it after that one.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — No correction to the mechanism; LOW is right, arguably INFORMATIONAL. One note on the proposed fix: it says to land the prune after fixing finding 0 because the new INDEX idx_siwf_expires is column/index-only and would be skipped. That sequencing advice is over-cautious — siwf_nonces is the newest table in EXPECTED_TABLES, so any deployment predating it still runs the full DDL; and the prune DELETE itself works without the index at this table size.

- refuted=False sev→LOW — None. Accurate as written, including the sequencing caveat that the accompanying index would itself be skipped on existing deployments until finding 0's fast path is fixed.

**Evidence**:

```
app/routes/farcaster_auth.js:74-78 — every nonce request writes a row:
```
      const nonce = siwf.newNonce();
      try {
        await pool.execute(
          'INSERT INTO siwf_nonces (nonce, created_at, expires_at) VALUES (?, ?, ?)',
          [nonce, now, new Date(now.getTime() + siwf.NONCE_TTL_MS)]);
```
and consumption marks rather than removes — app/routes/farcaster_auth.js:100-102:
```
    const [res] = await pool.execute(
      'UPDATE siwf_nonces SET used_at = ? WHERE nonce = ? AND used_at IS NULL', [now, nonce]);
```
`grep -n 'siwf_nonces' app/routes/farcaster_auth.js app/lib/*.js app/*.js` returns only those three statements plus the DDL — there is no DELETE anywhere. Contrast the sibling store, app/lib/wallet_link_store.js:25-27, which does have one:
```
async function _prune(table) {
  try { await _pool().execute(`DELETE FROM ${table} WHERE expires_at < ?`, [_now()]); } catch (_) { /* best-effort */ }
}
```
```

## B4-29 [HIGH] No SIGTERM handler: the entire graceful-shutdown path is unreachable on every deploy and watchdog restart

- **Dimension**: concurrency · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `/home/user/001/bot/main.py:524-545 (the finally block and the run_until_complete that guards it); 18 (signal imported only for os.kill)`

**Observed**: Nothing in that block executes. The process dies at the instant the signal is delivered. python-telegram-bot's own `run_polling()` installs SIGINT/SIGTERM/SIGABRT handlers (site-packages/telegram/ext/_application.py:1038-1044, `loop.add_signal_handler(sig, self._raise_system_exit)`), but bot/main.py deliberately drives `app.initialize()/app.start()/app.updater.start_polling()` by hand (lines ~470-476) and therefore inherits none of that.

**Root cause**: The shutdown sequence was written as a coroutine `finally` block, and nothing ever converts a process signal into the cancellation or exception that would run it. `run_telegram()` imports `signal` only to kill a stale predecessor, never to register `loop.add_signal_handler(signal.SIGTERM, ...)` for itself.

**Business impact**: Every redeploy and every watchdog restart is a hard kill. Concretely: (a) the Telegram long-poll is never released, which is the documented source of the 409 getUpdates conflicts the poller watchdog at bot/main.py:490-506 exists to recover from; (b) `logs/audit_chain.jsonl` is appended with a plain buffered `fh.write` (bot/utils/audit_chain.py:194-195) with no atomic rename, so a kill mid-append truncates a hash-chained record that watchdog.sh:66-70 calls "unrecoverable and indistinguishable from tampering"; (c) a kill between an exchange `create_order` and the local `_save_positions()` leaves a live position the local book does not know about until the next boot's orphan-adoption sweep. The ten-second grace both callers pay for buys nothing.

**Reachability**: Fully reachable on the production path: docker-compose.yml:20 makes `python -m bot.main --mode telegram` PID 1 of the container (so `docker stop` SIGTERMs it directly), and watchdog.sh:71-83 SIGTERMs the same pattern on every restart cycle. The `finally` block itself is reachable ONLY if an exception escapes the try (e.g. `app.initialize()` failing at boot) — never on an operator or supervisor stop. Boot-time reconciliation (engine.py:2792-2825: reconcile_positions / sync_positions_from_exchange / verify_and_fix_sltp) does bound the position-level damage, which is why this is not a BLOCKER.

**Remediation**: In `run_telegram()`, after `asyncio.set_event_loop(loop)`, register handlers that cancel the top-level task so the existing `finally` runs, e.g. hold `main_task = loop.create_task(_run_all())` and for `sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, main_task.cancel)`, then `loop.run_until_complete(main_task)` catching `asyncio.CancelledError`. Add a `except asyncio.CancelledError: pass` around the `while True` body's `await engine_task` so cancellation reaches the `finally` rather than the restart branch. Wrap the whole shutdown in a bounded `asyncio.wait_for(..., timeout=8)` so it still finishes inside the watchdog's ten-second grace.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→MEDIUM — Severity lowered HIGH->MEDIUM. Nothing in the unreached finally block persists trading state: it stops the alert monitor, cleans the aiohttp dashboard runner, stops the Telegram updater, and closes ccxt/aiohttp sessions — all of which the OS reclaims when the process dies. There is no flush-to-disk of positions or the audit chain in that path, and boot reconciliation (reconcile_positions / sync_positions_from_exchange / verify_and_fix_sltp) bounds the position-level damage, as the finder concedes. The residual real risks are a partially-appended audit_chain.jsonl line and a stale getUpdates poller on the Telegram side (mitigated by drop_pending_updates=True on restart). Real but not HIGH for a money-moving system. The proposed fix is otherwise sound.

- refuted=False sev→MEDIUM — Severity is inflated at HIGH because the finding overstates what the missed shutdown actually costs. I read RuneClawEngine.stop() (bot/core/engine.py:3611-3635) end to end: it sets `_running = False`, stops the WS feed, closes the scanner, stops the dashboard pusher, closes the operator + per-user executors, transitions to IDLE and writes one audit line. It persists NO trading state — there is no book to close, no flush, no position write. On a dying process the OS reclaims every socket the executor `close()` calls would have released, and `app.updater.stop()` is redundant once the process's sockets drop (and the poller watchdog at bot/main.py:487-506 plus `drop_pending_updates=True` recovers a 409 on the next boot). So the real residue is: no orderly Telegram updater stop, and — because atexit also does not run on SIGTERM — the PID file at bot/main.py:400-414 is left stale. Real lifecyc

**Evidence**:

```
bot/main.py:524-545 —
        finally:
            try:
                poller_state["stopping"] = True
                watchdog_task.cancel()
            except NameError:
                pass  # boot failed before the watchdog was created
            await handler.stop_monitor()
            if dashboard_runner:
                await dashboard_runner.cleanup()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await engine.stop()

    try:
        loop.run_until_complete(_run_all())
    except KeyboardInterrupt:
        print("\nShutting down...")

The only occurrence of a signal constant in the whole tree outside a test is bot/main.py:385, which is the PID-file guard killing a PREVIOUS instance:
    os.kill(old_pid, signal.SIGTERM)
`rg -n "signal\.(SIGTERM|SIGINT)|add_signal_handler|signal\.signal"` over the repo (excluding .venv/node_modules) returns exactly bot/main.py:385 and tests/test_deploy_smoke_guard.py:221 — no handler is ever installed.
```

## B4-30 [MEDIUM] The autonomous tick never ACQUIRES the scan lock — force_scan's single-flight guard only works in one direction

- **Dimension**: concurrency · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `/home/user/001/bot/core/engine.py:4124 (tick checks the lock) vs 6517-6524 (force_scan is the only holder)`

**Observed**: Mutual exclusion holds only when force_scan wins the race. When the tick starts first — the common case, since the tick runs every 60-90s and its scan phase can occupy minutes — force_scan sees a free lock and runs concurrently with it. The two pipelines then interleave on `_pending_ideas`, `_pending_atr`, `_pending_timing`, `_pending_pyramid` and `_cooldown_until`, and the state machine is driven from two places at once (force_scan ends with `_transition(AgentState.IDLE, "force scan complete")` at 6603 while the tick is still ANALYZING).

**Root cause**: The guard was implemented as a test-only check on the tick side and a test-and-acquire on the force_scan side. A lock that one participant never holds provides no mutual exclusion for the other.

**Business impact**: Doubled exchange/LLM load during a scan (the rate-limiter pressure the semaphore at 4575 exists to avoid); a user's pending idea card silently invalidated mid-flight by the other pipeline's `clear()`; the post-loss cooldown (`_cooldown_until = 0.0`, 6542) cleared out from under a running tick; and on the paper path two same-symbol entries from the two pipelines, since the duplicate-entry re-check in confirm_trade is live-only.

**Reachability**: Both entry points are live: `_tick` is the engine's main loop (`run()` at 2851 calls `_tick_guarded()`), and `force_scan` is called from the Telegram handler's 'Latest Signal' button and /forcescan, which run concurrently because `build_app()` enables `.concurrent_updates(True)` (pinned by tests/test_scan_concurrency_and_killswitch.py:60-64). Mitigation that limits blast radius: `confirm_trade` (engine.py:5634-5636) serialises per symbol, and its duplicate re-check at 5637 catches an already-open live position — but that check is gated on `if (CONFIG.is_live() and ...)`, so the paper path has no equivalent, and neither guard prevents the state-clobbering or the doubled scan/LLM/exchange load the lock exists to prevent.

**Remediation**: Have the tick hold the same lock for its scan section: replace the `if self._scan_lock.locked(): ... return` at 4124 with a non-blocking acquire — keep the early-return when it is already held, then wrap the scan/analyze/auto-confirm block (4129-4373) in `async with self._scan_lock:`. The position-monitoring phase at 4374-4377 must stay OUTSIDE the lock so SL/TP enforcement is never gated on a scan. Add a driven (not source-scanned) test that starts a slow fake tick scan and asserts a concurrent `force_scan()` returns `skipped: scan_already_running`.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→UNCHANGED — No correction. MEDIUM is the right level: the per-symbol entry lock plus the live duplicate re-check prevent the double-order outcome on the live path, so the residual damage is state clobbering of _pending_ideas/_cooldown_until, a state machine driven from two places, and doubled LLM/exchange load. The proposed fix's caveat is important and correct — the position-monitoring phase must stay outside the lock so SL/TP enforcement is never gated on a scan.

- refuted=False sev→UNCHANGED — MEDIUM is right. Note one refinement to the finder's blast-radius claim: the duplicate re-check they call live-only also has the paper path partly covered by the analysis-time duplicate gate, and both concurrent pipelines share the same `_symbol_entry_locks`, so the concrete worst case is clobbered `_pending_ideas`/`_pending_atr`/`_cooldown_until` state, a contradictory AgentState (force_scan's `_transition(AgentState.IDLE, ...)` while the tick is still analyzing), and a doubled LLM/exchange bill — not duplicate live orders.

**Evidence**:

```
engine.py:4118-4128 — the tick only TESTS the lock and then scans without holding it:
        # Don't scan while a Telegram-triggered force_scan holds the scan lock —
        # both mutate _pending_ideas and run auto-confirm. ...
        # Same-symbol double orders are separately impossible via
        # the per-symbol entry locks in confirm_trade.
        if self._scan_lock.locked():
            self._transition(AgentState.MONITORING, "checking positions (force_scan in progress)")
            await self._phase(self._check_open_positions(), "positions (scan in progress)")
            self._transition(AgentState.IDLE, "tick cycle complete (scan in progress)")
            return

        self._transition(AgentState.SCANNING, "beginning scan cycle")

engine.py:6517-6524 — the only acquire in the file:
        if self._scan_lock.locked():
            audit(system_log, "Force scan skipped — scan already in progress", ...)
            return {... "skipped": "scan_already_running"}
        async with self._scan_lock:
            return await self._force_scan_locked(max_symbols=max_symbols, lightweight=lightweight)

`grep -n "_scan_lock" bot/core/engine.py` returns exactly five lines: 525 (construction), 4124 (test), 6517 (test), 6523 (acquire), 6526 (the locked body). There is no `async with self._scan_lock` anywhere in `_tick`.
```

## B4-31 [MEDIUM] Per-user ccxt/aiohttp sessions are dropped without close(): balance-view executors are never closed anywhere, including at shutdown

- **Dimension**: concurrency · **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `/home/user/001/bot/core/engine.py:1966-1981 (invalidate drops both caches), 1908-1916 (view cache rebuilt on credential change), 3628-3633 (shutdown closes only _user_executors)`

**Observed**: `invalidate_user_executor` is a synchronous method that only pops, and `stop()` iterates only `_user_executors`. `grep -rn "_balance_view_executors"` over the repo shows the only non-test references are engine.py:435, 1908, 1916, 1979, 1981 — construction, get, set, and the two pops. No call site ever closes one.

**Root cause**: Two caches were added for two different purposes (order placement vs read-only balance views, deliberately kept in separate dicts per the docstring at 1888-1892), but only one of them was wired into the shutdown sweep, and the invalidation path was written as a plain `def` so it structurally cannot await `close()`.

**Business impact**: Each leaked ccxt client keeps an aiohttp ClientSession and its TCP connections alive for the life of the process, and the bot is designed to run for weeks between restarts. Growth is slow (one per credential rotation per user, plus one per user at shutdown) but unbounded, and aiohttp's 'Unclosed client session' warnings on a live-trading log are the kind of noise that masks real faults.

**Reachability**: `balance_view_executor` is reachable by DEFAULT: its docstring (engine.py:1880-1882) states it deliberately ignores `PER_USER_LIVE_ENABLED` (which is False by default per bot/config.py:2261) because "viewing your own balance is read-only and must work the moment you /connect". So every /connect + /livebalance user creates one. `invalidate_user_executor` has three live callers: telegram_handler.py:6465 and :7660, and engine.py:3937/3954 (the website credential and control pumps). Note the leak only materialises once the executor has actually called `_get_exchange()` — an executor built and never used holds no session.

**Remediation**: Make `invalidate_user_executor` async (or have it schedule closes) and `await ex.close()` for each popped executor before dropping it — with a `try/except` mirroring switch_venue's best-effort close. Note the credential-pull path calls it from a worker thread (engine.py:3936-3937 inside `asyncio.to_thread`), so that call site needs a `run_coroutine_threadsafe` or a queued-close list drained on the loop. Separately, add `for _ex in list(getattr(self, "_balance_view_executors", {}).values()): await _ex.close()` to `RuneClawEngine.stop()` beside the existing `_user_executors` loop.

**Verifier corrections** (these override the finder where they conflict):

- refuted=False sev→LOW — Severity lowered MEDIUM->LOW. This is a bounded resource leak with no path to incorrect trading behaviour: view-only executors are excluded from all_executors() by design, the leak only materialises after _get_exchange() has actually been called, and it is driven by human-paced /connect//disconnect//venue events rather than any loop. The shutdown half is nearly free of consequence given the process is exiting (and, per finding 0, usually SIGKILLed anyway). One caveat on the fix: engine.py:3948-3952 carries an explicit comment that the control-pull callbacks run on a worker thread and are safe precisely BECAUSE they "only pop from plain dicts" — making invalidate_user_executor async would invalidate that reasoning at both to_thread call sites (3936-3937 and 3951-3954), so the queued-close variant the finder mentions is the only safe shape.

- refuted=False sev→LOW — Downgrade MEDIUM -> LOW. I traced the invalidation callers to bound the leak: engine.py:3936-3937 and 3951-3954 pass `on_change=self.invalidate_user_executor` to `pull_and_apply` / `pull_and_apply_controls`, and that callback fires only on an actual change (the pull itself is throttled to once per 30s at engine.py:3915-3917), so this is one leaked ClientSession per /connect, /disconnect or web credential change per user — bounded by user actions, not per-tick. And the leak only materialises if that executor already called `_get_exchange()`, which the finding correctly concedes. The shutdown half is cosmetic on top of finding 0: the process usually dies on SIGTERM without running `stop()` at all, and the OS reclaims the sockets either way. Real hygiene defect, no money or availability impact demonstrated.

**Evidence**:

```
engine.py:1966-1981 — both caches are popped, neither entry is closed (the method is sync, so it cannot await `close()`):
    def invalidate_user_executor(self, user_id: str) -> None:
        uid = str(user_id)
        for k in [k for k in list(self._user_executors)
                  if k == uid or k.endswith(f"/{uid}")]:
            self._user_executors.pop(k, None)
        for k in [k for k in list(self._balance_view_executors)
                  if k == uid or k.endswith(f"/{uid}")]:
            self._balance_view_executors.pop(k, None)

engine.py:1908-1916 — the view cache silently replaces an executor when credentials change:
        ex = self._balance_view_executors.get(key)
        if ex is None or (ex._credentials or {}) != creds:
            ex = LiveExecutor(user_id=user_id, credentials=creds, venue=venue)
            ex._ws_feed = self.ws_feed
            self._balance_view_executors[key] = ex

engine.py:3626-3633 — shutdown closes the operator executor and `_user_executors`, and nothing else:
        if hasattr(self, 'live_executor') and self.live_executor:
            await self.live_executor.close()
        for _ex in list(getattr(self, "_user_executors", {}).values()):
            try:
                await _ex.close()

The session being leaked is real: live_executor.py:807-820 lazily builds `self._exchange = self._venue.create_exchange(...)` (a `ccxt.async_support` client, which owns an aiohttp ClientSession + TCPConnector), and live_executor.py:1405-1409 is the only thing that closes it:
    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
There is no `__del__` on LiveExecutor (`grep -n "__del__" bot/core/live_executor.py` → no match).
```

## Suspected in batch 4 (one verifier refuted)

- **[LOW]** Background tasks are created with no retained reference and no cancellation path (fire-and-forget in the monitor loop and the self-audit spawn) — `/home/user/001/bot/core/proactive_monitor.py:548 and 607 (proactive_monitor); bot/core/self_audit.py:229`
- **[LOW]** DashboardPusher decides it is configured from live env but starts from import-time constants, and stop() cancels its task without awaiting it — `/home/user/001/bot/core/dashboard_pusher.py:24-26 (import-time constants), 43-44 (call-time check), 55-64 (start reads the constants), 66-70 (stop), 180-183 (_loop reads the constants)`


========================================================================

# Batch 5 — infra-cicd, deps, privacy, observability

**33 raw · 31 CONFIRMED · 2 SUSPECTED · 0 REFUTED**


## B5-01 [HIGH] CI installs the Solana toolchain by piping an unverified remote script into sh, in the same job that produces and "proves" the deployable staking bytecode

- **Dimension**: infra-cicd · **Fix class**: REVIEW_REQUIRED · **File**: `.github/workflows/ci.yml:216-219 (staking), 554-557 (token-tooling)`

**Observed**: The installer script is fetched and executed unverified in two jobs. In the `staking` job it installs `cargo-build-sbf`, which then produces `target/deploy/rclaw_staking.so` — the artifact `scripts/build_provenance_gate.py` immediately certifies as reproducible and mint-pinned.

**Root cause**: The workflow applies checksum discipline only to the step it labelled "a supply-chain control" (gitleaks) and not to the two steps that install a compiler for the deployed program. The provenance argument is circular: build_provenance_gate.py's reproducibility check builds twice with the SAME installed toolchain, so a compromised toolchain produces two byte-identical compromised artifacts and the gate reports green.

**Business impact**: A compromise or MITM of the installer endpoint yields arbitrary code execution inside a job that holds GITHUB_TOKEN with undeclared permissions and emits the deployable bytecode of the staking program holding staked $RCLAW. A substituted cargo-build-sbf could strip enforce_pinned_mint while the provenance gate — rebuilding with the same toolchain — still reports reproducible and pinned. build_provenance_gate.py's docstring states the stakes: a verifier "cannot distinguish 'the deployer legitimately set the pin' from 'the deployer stripped the mint check before deploying'."

**Remediation**: Download the Anza release tarball for v1.18.26 to a file, verify a committed SHA256 with `sha256sum -c -` (the pattern already at ci.yml:491), then extract and add to $GITHUB_PATH. Do it in both jobs. While there, pin `cargo install cargo-audit --locked` (ci.yml:259) to an explicit `--version`, since that step installs the SCA gate's own binary at whatever version crates.io serves that day.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — The framing 'produces the deployable staking bytecode' overstates the blast radius. I grepped the whole workflow for `upload-artifact`/`actions/upload` and there is none, and .github/workflows/ ci.yml is the only workflow file — the .so built at :221 is `ls -l`'d and discarded. Nothing is published or released from CI, so a compromised installer buys arbitrary code execution on the runner with GITHUB_TOKEN in scope (which is what F2 is about), NOT a poisoned artifact reaching a chain. Real, worth fixing, but MEDIUM not HIGH.

- sev→MEDIUM — Severity HIGH is inflated for this system, for two reasons the finder under-weighted. (1) The installer URL is itself version-pinned — https://release.anza.xyz/v1.18.26/install, not a floating `stable` endpoint — so this is not the usual `curl|sh` of a moving target; the residual risk is Anza's CDN serving different bytes at a fixed version path. (2) I read the whole staking job (ci.yml:165-296): there is no `actions/upload-artifact`, no release publish, and no deploy step — the SBF build ends at `ls -l target/deploy/rclaw_staking.so` (:221-223). CI never ships the .so anywhere, so a compromised toolchain cannot put bytecode on chain; it can only make CI's reproducibility claim untrustworthy, which a human deployer rebuilding locally would not inherit. That is a real erosion of the provena

**Evidence**:

```
  .github/workflows/ci.yml:216-219
      - name: Install the SBF toolchain (pinned to Anchor.toml's solana_version)
        run: |
          sh -c "$(curl -sSfL https://release.anza.xyz/v1.18.26/install)"
          echo "$HOME/.local/share/solana/install/active_release/bin" >> "$GITHUB_PATH"

  ...and the next steps are the ones that produce and certify the artifact:
  .github/workflows/ci.yml:221 and :259-260
      - name: Build — SBF bytecode (deployable artifact)
      - name: Build provenance — reproducible, and the mint pin is compiled in
        run: python3 scripts/build_provenance_gate.py

  Contrast, in the SAME workflow, .github/workflows/ci.yml:479-491:
      # The binary is pinned and checksum-verified rather than installed from a
      # floating tag: this step is a supply-chain control, so it must not itself
      # execute an unverified download.
      - name: gitleaks (full history)
        env:
          GITLEAKS_SHA256: a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb
        run: |
          curl -sSLo "$tarball" ...
          echo "${GITLEAKS_SHA256}  ${tarball}" | sha256sum -c -
```

## B5-02 [MEDIUM] Third-party GitHub Actions are referenced by mutable branch/tag, not commit SHA — including a branch ref on the Rust toolchain action

- **Dimension**: infra-cicd · **Fix class**: SAFE_AUTO_FIX · **File**: `.github/workflows/ci.yml:173, 176, 454`

**Observed**: All three third-party actions run whatever the referenced branch or major tag points at on the day the job runs. gitleaks/gitleaks-action@v2 additionally receives secrets.GITHUB_TOKEN explicitly (:456).

**Root cause**: No pinning policy for `uses:` refs and no test enforcing one. The author reasoned about download integrity for the one step labelled "a supply-chain control" and did not extend it to the actions themselves, which execute earlier and with the same privileges.

**Business impact**: A compromise of any of the three action repositories (or of a maintainer account able to move a branch/tag) executes attacker code in this repo's CI. The staking job builds the deployable on-chain artifact; the secrets job is handed GITHUB_TOKEN. Tag/branch hijack of popular actions is a demonstrated real-world attack class.

**Remediation**: Replace each third-party `uses:` with `owner/repo@<40-char-sha> # vX.Y.Z` and add a Dependabot/Renovate github-actions rule so SHAs are bumped deliberately. Optionally add a pytest that parses ci.yml and asserts every non-`actions/*` `uses:` matches `^[^@]+@[0-9a-f]{40}$` — tests/test_preflight_matches_ci.py and tests/test_ci_covers_what_ships.py already parse this file with PyYAML, so the seam exists.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Verbatim at ci.yml:172-176 (`- uses: dtolnay/rust-toolchain@stable` with `components: clippy, rustfmt`, then `- uses: Swatinem/rust-cache@v2`) and :452-457 (`uses: gitleaks/gitleaks-action@v2` under `if: github.event_name == 'pull_request'`, with `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`). `@stable` on dtolnay/rust-toolchain is a branch ref, `@v2` a floating major tag — GitHub re-resolves both at job start. The finding correctly scopes to third-party actions and does not accuse actions/checkout@v4 or actions/setup-node@v4. grep across tests/ for dtolnay/Swatinem/gitleaks-action returns nothing; the only tree-wide hits are docs/TOKEN_SECURITY_AUDIT.md:1627/1630/2796, which are remediation snippets proposing these very steps, not pinning findings — exactly as the finding states.

- sev→LOW — MEDIUM is inflated here. This is a generic hardening recommendation, not a live defect, and the blast radius in THIS repo is small: the workflow triggers are pull_request / push-to-main / workflow_dispatch (:3-7) with no `pull_request_target`, so fork PRs get no repository secrets; the only secret any of these three actions touches is GITHUB_TOKEN; and — as established under finding 0 — no job publishes an artifact, pushes a package, or deploys. A compromised action could poison CI verdicts, which is real, but it cannot exfiltrate a deploy key or ship bytecode. LOW.

**Evidence**:

```
  .github/workflows/ci.yml:172-176
      # Toolchain comes from rust-toolchain.toml so CI and a local build agree.
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy, rustfmt
      - uses: Swatinem/rust-cache@v2

  .github/workflows/ci.yml:453-456
        if: github.event_name == 'pull_request'
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## B5-03 [MEDIUM] No workflow-level GITHUB_TOKEN permissions block — least privilege is asserted in a comment but depends on a repository setting the tree cannot pin

- **Dimension**: infra-cicd · **Fix class**: SAFE_AUTO_FIX · **File**: `.github/workflows/ci.yml:1-13 (no permissions key), 405-419`

**Observed**: Seven of eight jobs run with an inherited, tree-invisible token scope. Two of them (staking, token-tooling) execute an unverified remote installer (:218, :556) while that token is live.

**Root cause**: The workflow reasoned about permissions only where a step demanded them (gitleaks needed pull-requests: read), and the resulting comment records the observed default as if it were a guarantee. A default is a setting; a permissions: key is a declaration.

**Business impact**: If the repository or organisation default is read/write (the pre-2023 default, still selectable), a compromised action ref or a compromised toolchain installer in any of the seven undeclared jobs receives a token that can push commits, move tags and create releases on a repository that publishes on-chain program source and a trading bot.

**Remediation**: Add `permissions:\n  contents: read` immediately after the `concurrency:` block. The existing secrets-job block at :417-419 already widens correctly and needs no change. Two lines, zero behaviour change under the current default, and a real control if the default is ever permissive.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Downgrade to LOW. The workflow's own comment records empirical evidence that the repository default is already restrictive: gitleaks-action died on `403 Resource not accessible by integration` asking for pull_requests=read, which is what a contents-only token does. So the missing `permissions:` key is a durability/declaration gap (protects against a future settings change), not a live over-privilege. Two lines, zero behaviour change today — that is hardening, not a defect that moves money.

- sev→LOW — Downgrade to LOW. This is defence-in-depth against a repo setting that the finder concedes they cannot read and that is read-only by GitHub's own default for repositories created after Feb 2023. There is no path in this workflow that uses the token to write — no artifact publish, no release, no `gh` call, no bot commit — so even a permissive default has nothing here to abuse beyond what a compromised third-party action (finding 1) already implies. The two-line fix is worth taking; the impact rating is not MEDIUM on a system that moves real money, because nothing in the money path passes through it.

**Evidence**:

```
  .github/workflows/ci.yml:1-13 — the entire workflow preamble, with no `permissions:` key:
    name: CI

    on:
      pull_request:
      push:
        branches: [main]
      workflow_dispatch:

    concurrency:
      group: ci-${{ github.ref }}
      cancel-in-progress: true

    jobs:

  .github/workflows/ci.yml:405-419 — the only permissions declaration in the file:
      # The workflow declares no permissions, so GITHUB_TOKEN arrives with the
      # repository default — contents only. gitleaks-action calls
      # ...
        permissions:
          contents: read
          pull-requests: read
```

## B5-04 [MEDIUM] The GitLab failover pipeline installs a requirements.txt that does not exist, so all seven of its Python gates abort in before_script

- **Dimension**: infra-cicd · **Fix class**: SAFE_AUTO_FIX · **File**: `.gitlab-ci.yml:33-38`

**Observed**: before_script exits non-zero on all seven jobs. The pipeline whose own header (:6-8) says it exists because "the GitHub account was suspended and six checks went with it ... This restores enforcement on a host that is not the one that went away" enforces nothing.

**Root cause**: The GitLab file was written as a translation of ci.yml without being executed, and the one filename that differs between the two hosts was transcribed from habit rather than from the source file.

**Business impact**: The documented backstop for a repeat of the 2026-08-02 GitHub suspension is inert. If GitHub goes away again, this repository has zero automated enforcement of the ruff floors, the mypy gates, bandit, pip-audit, the baseline test gate and guard_lint — on a codebase that moves real money — while a file at the repo root states the opposite.

**Remediation**: Change .gitlab-ci.yml:37 to `- pip install -q -r requirements-ci.txt`. Then either fix the rest of the file (see the two related findings) or delete it — a failover that cannot run is worse than none, because it is documented as coverage. Add a test that parses .gitlab-ci.yml and asserts every `-r <file>` and every script path it names exists on disk; nothing in tests/ reads this file today.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Downgrade to LOW on impact grounds. `git remote -v` shows only the GitHub origin — no GitLab remote is configured, so nothing runs this file today, and if it ever is adopted the failure is maximally loud: before_script exits non-zero on the first job, red on the first push, impossible to mistake for coverage. That is the opposite of the silent-undercoverage shape this repo actually bleeds from (see finding 4, which is the dangerous one).

- sev→LOW — The facts are exact; the framing overstates impact. A failing `before_script` makes each job RED, not green — GitLab blocks the MR and the pipeline is visibly broken. So this is not the repo's signature failure (a subset reported as the whole, silently); it is a loudly-broken dormant failover. The header's claim at :6-8 that it "restores enforcement" is false, which is a documentation-honesty defect worth fixing, but nothing can be merged on a false green because of it. LOW. Note also this is now the third audit to report it unfixed, which argues for deleting the file rather than repairing it.

**Evidence**:

```
  .gitlab-ci.yml:33-38
    .python:
      image: python:3.11
      before_script:
        - pip install -q --upgrade pip
        - pip install -q -r requirements.txt
        - pip install -q ruff mypy bandit pip-audit pytest pytest-asyncio pytest-cov
```

## B5-05 [MEDIUM] GitLab's token-tooling job runs from the repo root against paths that only exist under token/, and its `rules: changes` list can never match

- **Dimension**: infra-cicd · **Fix class**: REVIEW_REQUIRED · **File**: `.gitlab-ci.yml:92-99`

**Observed**: The job never tests the token tooling. On the only trigger that can fire it (a root package.json/lock change) it installs the wrong workspace and globs paths that do not exist. It also omits ci.yml's `node scripts/audit_gate.mjs` npm advisory ratchet entirely.

**Root cause**: The translation dropped ci.yml's `working-directory: token` and did not adjust the paths, so the job reads as a correct port while pointing at a directory layout that does not exist.

**Business impact**: On the failover host, the tooling that mints the $RCLAW SPL token and runs the Genesis presale — code that signs privileged Solana transactions — has no test execution and no npm advisory ratchet, while the pipeline lists a job named test:token-tooling. The token tree carries 9 high advisories per token/.audit-baseline.json; nothing on GitLab would print them.

**Remediation**: Rewrite as `script: [cd token, npm ci --no-audit --no-fund, node --test presale/*.test.mjs scripts/*.test.mjs, node scripts/audit_gate.mjs]` with `rules: - changes: [token/**/*]`. Or delete the job and state the omission in the header, which the header (:16-20) promises is the convention.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Two corrections, one of which makes this WORSE. (1) The title says the `rules: changes` list 'can never match', which the finding's own Reachability section correctly contradicts — package.json and package-lock.json exist at root, so it does match. Fix the title, not the body. (2) I tested the runtime behaviour: `node --test nosuch/*.test.mjs` prints `1..0 / # tests 0` and exits 0. So on the trigger that fires it, this job installs the wrong workspace, runs zero tests, and reports GREEN — it is a silently-passing gate, not a loudly-broken one, which is the exact failure shape CLAUDE.md's header paragraph is about. (Confirmed on node v22; the pipeline pins node:20, where the runner's handling of non-existent explicit paths may differ, so treat the silent-green half as HIGH-confidence-not-CO

- sev→LOW — Same correction as finding 3. "A job that runs and tests nothing" implies a silent pass; in fact a non-matching glob passes the literal string to `node --test`, which errors and exits non-zero, so the job goes RED. The defect is a job that can only ever fail, on a pipeline that is already wholly broken by finding 3. Real, documented three audits running, and worth deleting — but LOW impact, not MEDIUM.

**Evidence**:

```
  .gitlab-ci.yml:92-99
    test:token-tooling:
      stage: test
      image: node:20
      script:
        - npm ci --no-audit --no-fund
        - node --test presale/*.test.mjs scripts/*.test.mjs
      rules:
        - changes: [presale/**/*, scripts/*.mjs, package.json, package-lock.json]
```

## B5-06 [MEDIUM] The GitLab pipeline silently omits nine GitHub gates while its header promises that every omission is stated

- **Dimension**: infra-cicd · **Fix class**: REVIEW_REQUIRED · **File**: `.gitlab-ci.yml:10-20, 57-62, 81-90`

**Observed**: The header names two omissions (cargo, solidity). Nine further gates are dropped without mention, including both red teams (the gates on the risk engine and on the custody authority gate) and every npm advisory ratchet in the repo.

**Root cause**: The file was written against an earlier ci.yml and never re-derived. CLAUDE.md documents six occasions where a new ci.yml step appeared in the local preflight plan "for free" because preflight PARSES ci.yml; .gitlab-ci.yml restates it by hand, so each of those six additions widened the gap silently.

**Business impact**: A reader of .gitlab-ci.yml is told, in the file itself, that a green pipeline there means what a green pipeline on GitHub meant. It does not: on GitLab the risk engine is never attacked, the custody authority gate is never attacked, no npm tree is advisory-checked, the marketing site is never built, and app/ route files are never parse-checked. That is the precise class of false assurance this repository's guard tests exist to prevent.

**Remediation**: Port the missing steps, or update the NOT-PORTED block to name all of them. Replace `python3 scripts/mypy_gate.py || mypy ...` with the two separate steps ci.yml runs (`python3 scripts/mypy_gate.py`, then the six-target mypy invocation) so the ratchet is blocking and the floor has the same scope. Then add a test that parses BOTH files and asserts every `run:` step in ci.yml is either present in .gitlab-ci.yml or named in its NOT-PORTED list — scripts/preflight.py:100-123 already implements exactly this shape (`uncovered()`) for the local plan and is the model.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — One omission the finder missed, in the same file and the same shape: .gitlab-ci.yml:106 runs `bandit -r bot/ --severity-level high --confidence-level high -q`, while ci.yml:95 runs `bandit -r bot/ api_bridge.py dashboard_api.py scripts/`. ci.yml's comment at :85-94 exists specifically because 'the module the production image RUNS by default was never statically analysed'. The GitLab translation silently reinstates that exact gap. Add it to the list.

- sev→LOW — Downgrade to LOW on the same grounds as 3 and 4: today this pipeline cannot report green at all (four Python jobs abort in before_script, test:token-tooling errors), so the "green here means what green meant there" claim cannot currently mislead anyone. The finding becomes MEDIUM the moment finding 3 is fixed without also fixing this — which is worth saying explicitly in the fix note, because repairing requirements.txt alone would convert a loud failure into precisely the quiet parity lie the header disclaims.

**Evidence**:

```
  .gitlab-ci.yml:10-20 — the claim:
    # It is a TRANSLATION of .github/workflows/ci.yml, not a redesign. Same tools,
    # same flags, same order, so a green pipeline here means the same thing it
    # meant there. Where a job is omitted it is stated, rather than quietly
    # dropped — a CI file that silently covers less than it appears to is the
    # exact failure mode this repo keeps finding elsewhere.
    #
    # NOT PORTED YET (both need toolchains heavier than a default runner):
    #   * Staking program (cargo)  — cargo test / clippy / SBF build / cargo-audit
    #   * Rune NFT (solidity)      — hardhat suite under contracts/

  .gitlab-ci.yml:57-62 — the mypy gate, weakened and rescoped:
    lint:mypy:
      extends: .python
      stage: lint
      # Money modules only, ratcheted — matches the GitHub job's scope.
      script:
        - python3 scripts/mypy_gate.py || mypy bot/risk bot/core --ignore-missing-imports

  .gitlab-ci.yml:81-87 — the web job, tests only:
    test:web:
      stage: test
      image: node:20
      script:
        - cd app && npm ci --no-audit --no-fund && npm test
```

## B5-07 [MEDIUM] health_check.sh's auto-restart launches a `python` the repo documents the box does not have, never verifies survival, and exits 0 regardless

- **Dimension**: infra-cicd · **Fix class**: REVIEW_REQUIRED · **File**: `scripts/health_check.sh:42-51`

**Observed**: With RUNECLAW_RESTART=1 the script prints `RESTART: launched (pid N)` and exits 0 for a process that exec-failed a millisecond earlier. A cron job or monitor reading the exit code sees success forever.

**Root cause**: The lesson recorded in verify_bot_alive.sh, watchdog.sh, docker-compose.yml and both systemd units — "starting is not running" — was applied to every other launcher in the repo and never carried across to this one; the same shape as watchdog.sh:36-38's own note about the zombie check not being carried across.

**Business impact**: On a box where auto-restart is enabled, a crashed live-trading bot is never actually restarted (wrong interpreter) and the health check reports success every five minutes. This is the 2026-08-01 failure mode CLAUDE.md documents — a launcher that reports success and leaves nothing running — reproduced in the script whose job is to catch it.

**Remediation**: In scripts/health_check.sh:42-51: resolve `PY="$RUNECLAW_DIR/.venv/bin/python"` with a python3 fallback (copy watchdog.sh:91-92); create the log directory or write to logs/ (which deploy.sh symlinks to the persistent store); capture `NEW_PID=$!` and replace the bare `exit 0` with `scripts/verify_bot_alive.sh --pid "$NEW_PID" || { echo "$STAMP RESTART FAILED"; exit 1; }`.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Minor: the log-directory half of the proposed fix is weaker than stated — data/ is what deploy.sh symlinks into the persistent store per CLAUDE.md, so `$RUNECLAW_DIR/data/logs/bot_restart.log` is already a persistent path, unlike the launcher in finding 8. The interpreter and survival-gate halves are the real content.

- sev→LOW — LOW rather than MEDIUM. Three mitigations compound: the branch is off by default; the alert fires before it; and — the one the finder missed — watchdog.sh at the repo root is the actually-installed cron recovery path (crontab line at watchdog.sh:3, and docs/AUDIT_2026-08-12.md:105 discusses it as such), and it already does the correct venv-preferring, survival-gated restart. health_check.sh's restart branch is a redundant second restarter that nothing in the repo installs with RUNECLAW_RESTART=1. The fix is cheap and should still be made; the exposure is not MEDIUM. Also worth folding into the fix: :47 redirects into `$RUNECLAW_DIR/data/logs/` with no `mkdir -p`, so on a box where that directory is absent the redirect itself fails and the launch never happens, still exiting 0.

**Evidence**:

```
  scripts/health_check.sh:42-51
    if [ "$RUNECLAW_RESTART" = "1" ]; then
      echo "$STAMP RESTART: launching bot.main --mode $RUNECLAW_MODE"
      cd "$RUNECLAW_DIR"
      # nohup + disown so the restarted bot survives this cron shell exiting.
      nohup python -m bot.main --mode "$RUNECLAW_MODE" \
        >> "$RUNECLAW_DIR/data/logs/bot_restart.log" 2>&1 &
      disown || true
      echo "$STAMP RESTART: launched (pid $!)"
      exit 0
    fi

  The header states the contract, scripts/health_check.sh:15
    # Exit code: 0 = healthy (or restarted), 1 = down and not restarted.
```

## B5-08 [MEDIUM] verify_deploy.sh's bot-box half claims to compare the running commit and compares nothing — it never reads the build field the bot serves for exactly this purpose

- **Dimension**: infra-cicd · **Fix class**: REVIEW_REQUIRED · **File**: `scripts/verify_deploy.sh:175-182`

**Observed**: Any directory that is a git checkout produces `OK  checkout at <sha>` and contributes a pass. `scripts/verify_deploy.sh --box-only` on a box whose bot process is still running last week's code — the restart-did-not-apply case — prints `DEPLOY VERIFIED on every target checked.` (:187).

**Root cause**: The bot half was written as liveness probes plus a printed sha, and the comment describing the intended comparison was written before the comparison existed. bot/utils/build_info.py and dashboard_server.py's `build` field were added specifically to make this comparison possible and were never wired into the script that needs them — the repo's own "a module nothing calls" pattern, one level up.

**Business impact**: The tool CLAUDE.md points operators at for "Verifying a deploy" gives a clean bill of health to the exact failure it was written for. On 2026-08-20 a stale-code deploy passed every check; this script's bot half would pass it again, because printing a local sha and comparing a sha differ by precisely the check that incident needed. An operator then applies new configuration to an engine running old code that manages live positions.

**Remediation**: In scripts/verify_deploy.sh:175-182, curl `$GATEWAY_URL/health`, sed out `"build":"([^"]*)"`, and branch three ways: equal -> ok; different -> fail with live=/expected= notes (mirroring :132-143); field absent or endpoint unreadable -> unk, never ok. The web half at :86-146 is the template and already handles the omitted-field trap correctly.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Verified verbatim at scripts/verify_deploy.sh:175-182: the comment says 'Compared against the local checkout, because a deploy that pulled the wrong commit passes every other check' and the body is `if head="$(cd "$REPO" && git rev-parse --short HEAD ...)"; then ok "checkout at $head"` — it prints one value and compares it to nothing, so any git checkout contributes a pass and worst stays 0, yielding 'DEPLOY VERIFIED on every target checked.' at :187. CHECK_BOX defaults to 1 at :59. bot/web/dashboard_server.py:333-349 is verbatim including the 255-commits-stale paragraph and `return web.json_response({"status": "ok", "build": build_short(), "timestamp": _ts()})`. bot/utils/build_info.py's docstring does say it 'answers the half no pre-launch gate can: AFTER a restart, what is running right

- sev→UNCHANGED — One factual overreach to strike: "bot/utils/build_info.py and dashboard_server.py's build field were... never wired in — the repo's own 'a module nothing calls' pattern." That is wrong. `grep -rn build_short` shows three live non-test callers: bot/main.py:25/46, bot/skills/telegram_handler.py:1274/1282, and bot/web/dashboard_server.py:21/349. The module is thoroughly reachable; it is only THIS script that fails to consume it. Drop the reachability-pattern framing and the finding is otherwise exact. MEDIUM stands: this is the deploy-verification path CLAUDE.md builds a whole section around, the comment asserts a check that does not exist, and `--box-only` prints "DEPLOY VERIFIED" after asking only two liveness probes.

**Evidence**:

```
  scripts/verify_deploy.sh:175-182
      # Which code the box is actually on. Compared against the local checkout,
      # because a deploy that pulled the wrong commit passes every other check —
      # 2026-08-20, 255 commits stale, everything green.
      if head="$(cd "$REPO" && git rev-parse --short HEAD 2>/dev/null)"; then
        ok "checkout at $head"
      else
        unk "not a git checkout here, so the running commit could not be confirmed."
      fi

  What the bot already publishes, bot/web/dashboard_server.py:340-349
      `build` names WHICH COMMIT answered — the machine-readable twin of the web
      app's /api/version ... On 2026-08-20 the bot could not be asked that at all;
      a deploy had reset to a mirror 255 commits stale and every other check
      agreed it was fine.
      """
      return web.json_response(
          {"status": "ok", "build": build_short(), "timestamp": _ts()})
```

## B5-09 [LOW] The launcher template starts both processes with bare `python` and logs to the repo root, contradicting the two rules the repo wrote after those exact incidents — and a test pins the wrong form

- **Dimension**: infra-cicd · **Fix class**: SAFE_AUTO_FIX · **File**: `scripts/launch_all.sh.template:63-64, 72-73`

**Observed**: On the box the repo describes, the launcher cannot start either process. The failure is loud — verify_bot_alive.sh --pid at :69 and :77 catches it and calls die — so this is LOW, not a silent-success defect. The log-path half is silent: on a box that does have `python`, both logs land in the repo root and are erased by the next `git reset --hard`, which is the redeploy path.

**Root cause**: The launcher template predates the interpreter and log-path rules and was never brought in line; the test written alongside it transcribed the launch line verbatim as a regex, so the regression is now pinned rather than caught.

**Business impact**: A deploy on a box matching the repo's own description cannot start either process, and the tracebacks explaining a failed start are written to a path the next redeploy erases — which is how the 2026-08-01 and 2026-08-25 incidents stayed undiagnosed. Low severity because the failure is loud and the systemd units are the newer, correct path.

**Remediation**: Update scripts/launch_all.sh.template:63-64 and 72-73 to the venv-preferring interpreter and logs/ destinations, and loosen tests/test_launch_all_starts_both.py:59,65 to `nohup [^ ]*python3?` so the assertion pins that BOTH processes are launched (its actual subject, per the file's docstring) rather than the interpreter spelling.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Verified verbatim at scripts/launch_all.sh.template:63-64 (`log "starting bot.main"` / `nohup python -m bot.main --mode telegram >> bot.log 2>&1 &`) and :72-73 (`log "starting api_bridge"` / `nohup python api_bridge.py >> api_bridge.log 2>&1 &`). The documented-correct form at scripts/verify_bot_alive.sh:45-52 is verbatim, including 'python3, not python — the box has no `python`' and the logs/ rationale. The mitigation is real: :69 and :77 call verify_bot_alive.sh --pid and `die` on failure, so the interpreter half fails loudly. The test claim holds — tests/test_launch_all_starts_both.py:59 asserts `re.search(r"nohup python -m bot\.main", code)`, :65 asserts `nohup python api_bridge\.py`, and :166 does `code.index("nohup python")`, so the wrong spelling is pinned. LOW is the right call.

- sev→UNCHANGED — No correction — LOW is the right call and the finder reasoned to it correctly. One addition to the proposed fix: loosening the two regexes to `nohup [^ ]*python3?` also has to cover the ordering assertion at :166, which uses the bare literal `"nohup python"` via str.index and will raise ValueError (not a clean assertion failure) once the launch line changes. And while editing this template, see the far more serious defect in the same file that this finding walked past — item 1 in "missed".

**Evidence**:

```
  scripts/launch_all.sh.template:63-64 and 72-73
    log "starting bot.main"
    nohup python -m bot.main --mode telegram >> bot.log 2>&1 &
    ...
    log "starting api_bridge"
    nohup python api_bridge.py >> api_bridge.log 2>&1 &

  The documented-correct form it is supposed to implement, scripts/verify_bot_alive.sh:45-52
    # Correct:
    #     cd ~/runeclaw
    #     nohup python3 -m bot.main --mode telegram >> logs/bot.log 2>&1 &
    #     scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
    #
    # python3, not python — the box has no `python`. And logs/bot.log, not
    # bot.log: logs/ is symlinked into the persistent store, the repo root is
    # not, so a log written there is lost on the next `git reset --hard`.
```

## B5-10 [HIGH] The production image and `make install` install a manifest that omits three packages `requirements.lock` guarantees present — charts are permanently dead in the container

- **Dimension**: deps · **Fix class**: REVIEW_REQUIRED · **File**: `bot/requirements.txt:1-13 (with Dockerfile:21-23 and Makefile:28-29)`

**Observed**: The container and `make install` install a strict subset. `charts_available()` is False for the entire life of the image, `/chart` and every signal card's chart composite silently send text, and the only trace is an INFO log line. This is the 2026-08-17 incident recorded in requirements.lock:29-42 and tests/dep_policy.py:1-16, reproduced verbatim on the containerised path.

**Root cause**: Four Python manifests exist (requirements.lock, requirements-ci.txt, bot/requirements.txt, pyproject.toml). The 2026-08-17 fix added matplotlib/mplfinance/pandas to two of them — the lock and the CI file — and to neither of the two that actually install software on a running box. The guard tests that were written for that incident check only lock↔requirements-ci: tests/test_ci_env_matches_lock.py:70 is `missing = sorted(_dists(LOCK) - _dists(CI) - set(CI_EXEMPT))`, and no test computes `_dists(LOCK) - _dists(BOT_REQS)`.

**Business impact**: The chart is the artefact an operator looks at before approving or rejecting a trade idea. On the container path they get a text message instead and nothing tells them a rendering was attempted and skipped — the same silent degradation that ran undetected for months in the recorded incident.

**Remediation**: Add matplotlib==3.11.1, mplfinance==0.12.10b0 and pandas==3.0.5 to bot/requirements.txt (or make the Dockerfile and Makefile install requirements.lock), and extend tests/test_manifests_agree.py with a set-coverage assertion in the same shape as tests/test_ci_env_matches_lock.py:70 — `_dists(LOCK) - _dists('bot/requirements.txt')` must be empty or carry a written exemption. Do not touch any ratchet baseline; this is a manifest edit plus one new test.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Facts stand; severity is inflated. The blast radius is one display feature — charts silently degrade to text — not order routing, risk or custody. No unguarded import of the three libs exists anywhere in bot/, so nothing crashes. Also worth stating precisely: this is proven for the container and for `make install`/the documented README-and-gitbook setup path; CLAUDE.md's actual bot-box deploy uses `nohup python -m bot.main` against a venv whose provenance is not recorded anywhere, and requirements.lock's own header says to install the lock in production — so whether the LIVE bot has the libs is unknown from the tree, and the finding should say that rather than asserting the whole fleet is affected. MEDIUM.

- sev→MEDIUM — Two scope corrections that lower the impact from HIGH. (1) The bot box, not the container, is the documented live path in CLAUDE.md ('nohup python -m bot.main'), and deploy.sh:28-40 discusses `pip install -r requirements.lock` on that box — so the affected paths are the container build and `make install`, not necessarily the box that trades. (2) The user-visible failure is a downgrade to a text card plus a log line, not a false claim about money — it is feature loss, not a wrong number, so it sits below the pnl-rendering class of defect. Also worth adding to the writeup: the same manifest is missing fastapi and uvicorn, which the Dockerfile and Makefile paper over inline (and see my `missed` item about how badly the Dockerfile does it), and pyproject.toml:33-37 declares the three chart lib

**Evidence**:

```
bot/requirements.txt:1-13 — the file the Dockerfile COPYs — ends at redis and contains no charting libraries:
```
10	cryptography>=48.0.1
11	Pillow>=10.3.0
12	# RC-AUD-020: optional — durable JWT revocation store (token_store.py import-guards it).
13	redis>=5.0.0
```
Dockerfile:21-23:
```
COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    fastapi>=0.110 "uvicorn[standard]>=0.29"
```
Makefile:28-29 (`make install`, the documented setup target):
```
	$(PYTHON) -m pip install -r bot/requirements.txt
	$(PYTHON) -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.29"
```
requirements.lock:40-42 pins the three that are missing:
```
matplotlib==3.11.1
mplfinance==0.12.10b0
pandas==3.0.5
```
bot/skills/chart_renderer.py:76-88 turns their absence into a boolean, not an error:
```
# Optional dependencies — resolved once at import.
try:
    import matplotlib
    ...
    import mplfinance as mpf
    import pandas as pd
    _CHARTS_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 — any import failure ⇒ graceful text fallback
    _CHARTS_AVAILABLE = False
```
bot/skills/telegram_handler.py:1329-1331:
```
            if not chart_renderer.charts_available():
                system_log.info("chart libs not available, skipping")
                return
```
```

## B5-11 [HIGH] pip-audit audits `requirements.lock`; the deployed manifest range-pins the same packages and its floors carry 27+ known advisories the gate never evaluates

- **Dimension**: deps · **Fix class**: REVIEW_REQUIRED · **File**: `.github/workflows/ci.yml:161-163 (with bot/requirements.txt:10-13, Dockerfile:22-23, tests/test_manifests_agree.py:49)`

**Observed**: `pip-audit` evaluates `cryptography==50.0.0` and `Pillow==12.3.0` while the image installs `cryptography>=48.0.1` and `Pillow>=10.3.0`, resolved at build time with `--no-cache-dir` and no constraints file — so the image's contents are neither reproducible nor audited. `fastapi` and `uvicorn[standard]` are installed from a bare command line in Dockerfile:23 and appear in no manifest, so no gate reads them at all. The agreement test that exists is structurally blind to all of it, because its regex requires `==`.

**Root cause**: `_PIN` at tests/test_manifests_agree.py:49 matches only `==`. Every `>=` line in a manifest is therefore invisible to `test_shared_pins_agree_across_manifests` (:69), which skips any package it sees in fewer than two manifests. cryptography appears as `==50.0.0` in the lock and `>=48.0.1` in the deployed file, so `seen` has length 1 and the check passes vacuously on the one comparison it exists to make.

**Business impact**: The SCA gate reports green for a dependency set the deployed container does not contain. An advisory in the HTTP front door (fastapi/uvicorn/starlette), the image-rendering path (Pillow) or the crypto library used by the secrets vault (cryptography) can be present in the running image with the CI check still green.

**Remediation**: Either (a) make the Dockerfile and Makefile install `requirements.lock` so the audited file is the installed file, or (b) point pip-audit at the shipped set too: `pip-audit -r requirements.lock -r bot/requirements.txt`. Independently, widen `_PIN` to capture `>=`/`~=` and add a check that a package pinned `==` in requirements.lock is not range-pinned below that version in any manifest. Move the Dockerfile's inline `fastapi`/`uvicorn` into bot/requirements.txt so they are covered by test_manifests_agree.py at all.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — The title's headline number — 'its floors carry 27+ known advisories the gate never evaluates' — is unsupported by anything in the finding; no advisory query was run or shown, and the finding's own reachability paragraph concedes the floors are not the versions a build resolves. pip's resolver installs the NEWEST compatible release, so a build today gets cryptography/Pillow/redis at or above the lock's pins; the demonstrated defect is that the image is unreproducible and unaudited, not that 27 advisories ship. Drop the count and retitle to the reproducibility/scope claim. Severity MEDIUM.

- sev→LOW — Two evidence claims in this finding are FALSE and must not reach an engineer. (a) 'fastapi and uvicorn[standard] ... appear in no manifest at all, so no gate reads them' is wrong: requirements.lock:26-27 pins `fastapi==0.141.1` and `uvicorn==0.52.3` — so pip-audit DOES evaluate them — and they also appear in requirements-ci.txt:31-32 and pyproject.toml:20-21. (b) 'its floors carry 27+ known advisories the gate never evaluates' is unverified and mechanically misleading: `pip install 'cryptography>=48.0.1'` with no ceiling and no constraints file resolves to the NEWEST release, not the floor, so the image almost certainly carries a version at or above the audited pin. The finding itself concedes this in its reachability note, which contradicts its own title. Strip the advisory count entirely

**Evidence**:

```
.github/workflows/ci.yml:161-163 — the only Python SCA gate:
```
      - name: SCA — dependency vulnerability audit (pip-audit)
        if: always()
        run: pip-audit -r requirements.lock
```
The file that is actually installed, bot/requirements.txt:10-13, does not pin those packages:
```
cryptography>=48.0.1
Pillow>=10.3.0
# RC-AUD-020: optional — durable JWT revocation store (token_store.py import-guards it).
redis>=5.0.0
```
and Dockerfile:22-23 adds two more that appear in no manifest at all:
```
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    fastapi>=0.110 "uvicorn[standard]>=0.29"
```
The test written to close exactly this gap can only see `==` lines — tests/test_manifests_agree.py:49:
```
_PIN = re.compile(r'^\s*"?([A-Za-z0-9_.\-]+)==([0-9][^"\s,;]*)', re.M)
```
```

## B5-12 [HIGH] The GitLab CI that exists because GitHub was suspended cannot run: it installs a `requirements.txt` that does not exist, and its Node job runs the wrong paths

- **Dimension**: deps · **Fix class**: REVIEW_REQUIRED · **File**: `.gitlab-ci.yml:37, 96-99`

**Observed**: No Python job can start. `sca:pip-audit` — the only Python dependency audit on this host — is one of them. Separately, and unstated in the "NOT PORTED YET" list at :16-20, this pipeline has zero npm advisory ratchet for any of the five workspaces and zero cargo-audit, while .github/workflows/ci.yml runs the npm ratchet five times (:333 root, :359 site, :621 token, :653 contracts/rune, :702 app) and cargo_audit_gate.py at :262. `scripts/preflight.py` parses only `.github/workflows/ci.yml` (LOCAL_JOBS at :59-60), so none of this is visible from the local loop, and `grep -rln gitlab tests/ scripts/` returns nothing — no test reads this file.

**Root cause**: The GitLab file was written as a hand translation and never executed against this tree. The root `requirements.txt` it installs is a filename from an older layout (docs/DEEP_AUDIT_2026-08-14.md:437 records the same observation), and the token job was copied without the `token/` working directory that .github/workflows/ci.yml:516-517 supplies.

**Business impact**: The redundancy that was built after losing CI once does not exist. If GitHub goes away again, there is no dependency audit, no lint ratchet, no baseline test gate and no secret scan actually running — only the belief that there is.

**Remediation**: Change :37 to `pip install -q -r requirements-ci.txt` (the file whose contents the known-failures baseline was generated against). Give test:token-tooling `cd token &&` before both commands and change its `changes:` globs to `token/**/*`. Then either port the five npm advisory ratchets and cargo_audit_gate.py, or extend the "NOT PORTED YET" paragraph at :16-20 to name them, since it currently names only cargo and solidity. A test asserting that every `run:` step name in ci.yml has a counterpart in .gitlab-ci.yml or an explicit exemption would keep the two from drifting again.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Severity is inflated to HIGH on a mirror whose pipeline nobody in this tree can show is enabled — the finding says so itself. .github/workflows/ci.yml is intact and is what scripts/preflight.py parses, so the enforcement layer that actually runs is unaffected; what is broken is a standby. The `changes:` globs at :98-99 are a second, independent reason the token job is dead (nothing at those paths ever changes), so the job would be skipped rather than failing loudly — worth stating, since a never-triggered job is quieter than a red one. MEDIUM.

- sev→MEDIUM — Downgrade from HIGH on the failure mode. Every defect here fails LOUDLY — a missing requirements.txt makes before_script exit non-zero and the pipeline goes red, and an unexpandable `presale/*.test.mjs` glob makes `node --test` error. This is not the silent-false-green shape the repo's other supply-chain findings share, and it cannot ship stale or vulnerable code the way Finding 0's manifest gap can. It is also a secondary enforcement layer whose current enablement state is unobservable from here, which the finding correctly admits. The one part that is genuinely quiet is the coverage claim: the header at :16-20 asserts that omissions are stated, and the five npm advisory ratchets plus cargo_audit_gate.py are omitted without being named — a CI file that overstates its own coverage is the r

**Evidence**:

```
.gitlab-ci.yml:33-38, the base every Python job extends:
```
.python:
  image: python:3.11
  before_script:
    - pip install -q --upgrade pip
    - pip install -q -r requirements.txt
    - pip install -q ruff mypy bandit pip-audit pytest pytest-asyncio pytest-cov
```
There is no `requirements.txt`. `git ls-files | grep -i requirements` returns exactly: `bot/requirements.txt`, `requirements-ci.txt`, `requirements.lock`, `tests/test_requirements_cover_imports.py`; `ls /home/user/001/requirements.txt` → No such file or directory, and `.gitignore` contains no `requirements` pattern.

.gitlab-ci.yml:93-99:
```
test:token-tooling:
  stage: test
  image: node:20
  script:
    - npm ci --no-audit --no-fund
    - node --test presale/*.test.mjs scripts/*.test.mjs
  rules:
    - changes: [presale/**/*, scripts/*.mjs, package.json, package-lock.json]
```
`ls -d presale` and `ls scripts/*.mjs` at the repo root both return "No such file or directory"; the token tests live at `token/presale/*.test.mjs` and `token/scripts/*.test.mjs`.
```

## B5-13 [MEDIUM] The cargo advisory ratchet never re-checks the `shipped` classification it baselines six advisories on

- **Dimension**: deps · **Fix class**: SAFE_AUTO_FIX · **File**: `scripts/cargo_audit_gate.py:214`

**Observed**: The classification is recorded once at `--update` time and never re-asserted. If a Cargo.toml or Anchor version change moves `ring 0.16.20`, `rustls-webpki 0.101.7` (three advisories) or `quinn-proto 0.10.6` (two) out of `solana-program-test` dev-dependencies and into the normal tree, the gate prints "No new advisories" and exits 0 — the six advisories baselined *because* they were dev-only stay baselined once they no longer are.

**Root cause**: `collect()` (:138) recomputes `shipped` for every advisory on every run, and `main()` discards that recomputation for any id already present in the baseline. The comparison is `set(found) - set(known)`, a set difference over keys, so no per-entry field is ever diffed.

**Business impact**: The deployed staking program's supply chain could acquire a TLS certificate-validation bypass (RUSTSEC-2026-0098/0099) or a reachable CRL-parsing panic (RUSTSEC-2026-0104) with the gate green, because those three were excused on a classification that is never revisited.

**Remediation**: In `main()`, after computing `new_ids`, also compute `promoted = [i for i in found if i in known and known[i].get("shipped") is not True and found[i]["shipped"] is not False]` and fail on it with the same message as a new shipped advisory. This adds no baseline churn: today all nine classifications match, so the check is green on the current tree.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Two corrections. (1) Count: SEVEN of nine baseline entries are `"shipped": false` (ring 0.16.20, quinn-proto 0.10.6 ×2, rustls-webpki 0.101.7 ×3, and h2 0.3.27 — the finding omits h2), not six. (2) Severity: the reclassification is not invisible. Line 226-227 prints `RustSec advisories in Cargo.lock: N (X in the shipped tree, Y dev-only)` on every run, so a dev-only→shipped move changes that printed line even though nothing asserts on it. That plus the narrowness of the trigger (a Cargo.toml/Anchor change moving a crate out of solana-program-test) makes this LOW, not MEDIUM. The proposed `promoted` check is sound and costs no baseline churn.

- sev→LOW — Downgrade MEDIUM→LOW. The trigger is entirely hypothetical — it needs a Cargo.toml/Anchor change that moves ring, rustls-webpki, quinn-proto or h2 from solana-program-test dev-deps into the normal graph, which the repo pins deliberately against (rust-toolchain.toml / Anchor.toml, per the module docstring at :5-13). And it is not fully silent: `shipped_now` is recomputed from the live tree every run and printed as 'RustSec advisories in Cargo.lock: N (X in the shipped tree, ...)', so a promotion changes the number an operator sees on that line — it just does not gate. Correct the count in the writeup: the baseline is 2 shipped / 7 dev-only, not 'six of nine'. The proposed `promoted` check is sound and green on today's tree; note that the `is not False` polarity should be kept so a classific

**Evidence**:

```
scripts/cargo_audit_gate.py:210-224 — the gate's entire failure condition is the advisory-id set:
```
    baseline = json.loads(BASELINE.read_text())
    known = baseline.get("advisories", {})

    new_ids = sorted(set(found) - set(known))
    gone_ids = sorted(set(known) - set(found))
    ...
    shipped_now = sum(1 for e in found.values() if e["shipped"] is True)
```
`shipped_now` is computed and printed but never compared to `known[id]["shipped"]`. The baseline stakes six of nine entries on that field, e.g. .cargo-audit-baseline.json:
```
    "RUSTSEC-2026-0098": {
      "crate": "rustls-webpki",
      "version": "0.101.7",
      "title": "Name constraints for URI names were incorrectly accepted",
      "shipped": false
    },
```
```

## B5-14 [MEDIUM] NOTICE asserts blanket licence compatibility over a dependency set it enumerates 6 of, while the real tree carries LGPL-3.0-only, MPL-2.0 and 17 packages with no declared licence

- **Dimension**: deps · **Fix class**: REVIEW_REQUIRED · **File**: `NOTICE:62-72`

**Observed**: A categorical assertion — "All third-party licenses are compatible" — over a set that was never enumerated. It omits the entire npm dependency graph (~950 packages across five workspaces), the entire Rust graph, and 11 of the 17 Python packages in requirements.lock, including Pillow, matplotlib, pandas, cryptography, aiohttp and redis. No licence gate runs anywhere: `.github/workflows/ci.yml` has no licence-check step, and neither does .gitlab-ci.yml.

**Root cause**: The NOTICE was written against an early 6-package Python dependency list and never revisited as the Node, Rust and Solidity workspaces were added. Its own parenthetical, "(see bot/requirements.txt)", points at a file that is itself no longer the full Python set.

**Business impact**: The project sells commercial licences (NOTICE:56-57) and converts to GPL-2.0-or-later in 2030. A commercial licensee relying on the compatibility sentence is relying on a claim nobody has checked, and 17 unlicensed packages in the token-signing path are, by default, all-rights-reserved.

**Remediation**: Generate the third-party list rather than hand-writing it (`npm ls --all --json` per workspace plus `pip-licenses` over requirements.lock and `cargo license`), publish it as a NOTICE appendix or an SBOM, and replace the blanket sentence with the actual findings — in particular that rpc-websockets is LGPL-3.0-only in a runtime path and that 17 token/ packages declare no licence. Add a CI step that fails on a new copyleft or unlicensed package, in the same ratchet shape as token/scripts/audit_gate.mjs.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — One sub-claim is wrong and one is weaker than stated. (a) '17 packages with no declared licence' does not reproduce: I walked every installed node_modules tree reading each package.json — token 380 packages / 3 with no licence field (@wormhole-foundation/sdk-definitions, sdk-definitions-ntt, text-encoding-utf-8), root 183/1, app 144/0, site 111/0, contracts/rune 57/0. Four, not seventeen. Drop or re-derive that number. (b) The compatibility claim is not shown to be FALSE, only unverified: the Change License is GPL-2.0-OR-LATER, under which LGPL-3.0-only combines fine via GPLv3, and MPL-2.0 is file-level copyleft with an explicit GPL secondary-licence compatibility clause. NOTICE already lists python-telegram-bot as LGPL-3.0 itself. So what survives is 'a blanket assertion over a set nobody

- sev→LOW — One number in this finding is fabricated and one line reference is wrong. '17 token/ packages declare no licence' is FALSE: parsing token/package-lock.json, exactly 4 non-root entries lack a `license` field (@wormhole-foundation/sdk-definitions, @wormhole-foundation/sdk-definitions-ntt, eyes, text-encoding-utf-8). Tree-wide it is 7, not 17 — root 2 (eyes, text-encoding-utf-8), token 4, contracts/rune 1 (memorystream, dev-only), app 0, site 0. Anyone acting on '17' will go looking for thirteen packages that do not exist. Also, web-push is at app/package.json:16, not :14 (I confirmed it resolves to MPL-2.0 in app/node_modules). Downgrade MEDIUM→LOW: this is a legal-hygiene documentation defect with no runtime or money impact, node_modules is not vendored so nothing copyleft is actually redis

**Evidence**:

```
NOTICE:62-72:
```
This project uses the following open-source libraries (see bot/requirements.txt):
  - ccxt (MIT) -- cryptocurrency exchange trading library
  - openai (Apache-2.0) -- OpenAI Python client
  - pydantic (MIT) -- data validation using Python type annotations
  - numpy (BSD-3-Clause) -- numerical computing
  - python-telegram-bot (LGPL-3.0) -- Telegram Bot API wrapper
  - python-dotenv (BSD-3-Clause) -- .env file loading

All third-party licenses are compatible with source-available distribution
under the Business Source License 1.1 and with the GPL v2.0-or-later Change
License.
```
LICENSE:26 sets that Change License: `Change License:       GNU General Public License, version 2.0 or later`.
```

## B5-15 [LOW] No SBOM is produced for any artefact, including the container image

- **Dimension**: deps · **Fix class**: REVIEW_REQUIRED · **File**: `.github/workflows/ci.yml:1-720 (absence)`

**Observed**: The image records a git SHA and a date. Given Dockerfile:22's unconstrained `pip install` of a range-pinned manifest (see the pip-audit finding above), the git SHA does not determine the dependency versions in the layer, and nothing else records them — so for any given running container the question "which version of cryptography is in it?" has no answer outside the container.

**Root cause**: SBOM generation was never part of the build; provenance work went into the Rust artefact only.

**Business impact**: When the next advisory lands against a transitive Python or npm package, there is no record of which versions any deployed image contains, so the blast radius cannot be determined from artefacts — only re-derived from a manifest that does not pin.

**Remediation**: Add `pip freeze > /app/sbom-python.txt` inside the builder stage and COPY it into the production image (cheap, exact, and it also makes the image's resolved versions auditable), and/or emit a CycloneDX SBOM per workspace in CI with `cyclonedx-py` and `@cyclonedx/cyclonedx-npm`. This adds no ratchet churn.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — None. Accurate as written, correctly scoped as an absence, and correctly rated LOW. Note only that it is the same root cause as finding 1 viewed from the artefact side — the cheap half of the proposed fix (`pip freeze` captured in the builder stage) resolves the 'which cryptography is in this container' question that finding 1 raises, so the two should be fixed together rather than counted as independent work.

- sev→INFORMATIONAL — This is a missing best practice rather than a defect — there is no incorrect behaviour, no wrong number shown to anyone, and nothing to 'fix' so much as to add. Downgrade LOW→INFORMATIONAL. Its supporting argument is also weaker than stated: only 5 of the 17 packages the image installs are range-pinned (cryptography, Pillow, redis, plus the inline fastapi/uvicorn); the other 12 are `==` in bot/requirements.txt, so the git SHA determines most of the dependency set, not none of it. If it is actioned at all, the `pip freeze` into the image is the cheap half and is worth doing precisely because it also records what the unconstrained installs resolved to.

**Evidence**:

```
`grep -rin "sbom\|cyclonedx\|spdx" .github/ scripts/ Makefile CLAUDE.md docs/` returns no matches, and `find . -maxdepth 3 \( -iname "*sbom*" -o -iname "*cyclonedx*" -o -iname "*spdx*" \) -not -path "*/node_modules/*"` returns nothing. The image records only two labels — Dockerfile:34-36:
```
ARG BUILD_SHA=dev
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
```
```

## B5-16 [LOW] GitHub Actions are pinned to mutable tags, including a third-party action and a moving `@stable` branch ref

- **Dimension**: deps · **Fix class**: SAFE_AUTO_FIX · **File**: `.github/workflows/ci.yml:173, 176, 454`

**Observed**: Three third-party actions run from refs their maintainers can repoint at any time. The blast radius is genuinely limited here — the gitleaks job scopes its token to `contents: read` / `pull-requests: read` (ci.yml:415-417) and no other job passes a secret — but a repointed tag still executes arbitrary code on a runner with the full checkout, and `Swatinem/rust-cache@v2` writes the cache the `staking` job's builds read.

**Root cause**: Default GitHub Actions idiom; never hardened.

**Business impact**: A compromised action tag runs on a runner with the full source tree of a trading bot at build time.

**Remediation**: Pin each third-party action to a full commit SHA with the tag in a trailing comment (`uses: gitleaks/gitleaks-action@<sha> # v2.3.9`), and enable Dependabot for `github-actions` so the SHAs are bumped deliberately. The `actions/*` first-party ones are lower risk and can follow the same pattern for consistency.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Two counts are off and should be fixed before anyone quotes them: actions/checkout@v4 appears EIGHT times (19, 170, 302, 340, 421, 505, 628, 660), not seven, and actions/setup-node@v4 FIVE times (303, 341, 506, 629, 661), not four; setup-python@v5 appears once (line 21). The substance and the LOW rating are right — a repointed tag on Swatinem/rust-cache or dtolnay/rust-toolchain executes on a runner holding only the default read token, and the one job that receives a secret has it narrowed to read-only.

- sev→LOW — LOW is the right level and I would not move it, but two details tighten the writeup. actions/checkout@v4 appears 8 times, not 7. And the finding's own framing overstates the exposure slightly: the gitleaks job is the only one with an explicit token grant, and it is read-only; the two rust actions run in the `staking` job which handles no secret. The genuine residual risk is arbitrary code execution on a runner holding a full checkout plus a writable rust-cache, which is real but speculative. This is a hardening item, not a live defect — it belongs in a backlog, not ahead of anything in this audit.

**Evidence**:

```
Third-party actions referenced by mutable ref:
```
173:      - uses: dtolnay/rust-toolchain@stable
176:      - uses: Swatinem/rust-cache@v2
454:        uses: gitleaks/gitleaks-action@v2
```
plus `actions/checkout@v4` (×7), `actions/setup-python@v5` and `actions/setup-node@v4` (×4). No `uses:` line in the workflow carries a commit SHA.
```

## B5-17 [HIGH] Account deletion never contacts the bot for web-only accounts, so every bot-side store survives a "deleted" account

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `app/auth.js:1725`

**Observed**: A NULL telegram_id is read as "the bot holds nothing for this person". The bot in fact holds, keyed by "web:<uid>": the auto-provisioned UserStore record (bot/web/user_gateway.py:186-189), the agent profile and observed-memory stores, leverage/strategy preferences, the persisted conversation transcript (data/conversations.jsonl), the paper portfolio book (data/portfolio_web<uid>.json), the encrypted third-party LLM provider key and news provider key, and personal ingest notes. None of it is touched, and the user is told their account and its data have been erased.

**Root cause**: The presence of a Telegram id is used as a proxy for "the bot knows this person". That was true before lib/identity.js introduced the "web:<id>" fallback identity; the deletion route was never updated to match, and absent-telegram_id became a confident negative about a store nobody queried.

**Business impact**: A user who only ever used the website is told their account and data are erased while the bot retains their chat transcripts, their pasted personal notes, and their third-party LLM/news API keys indefinitely. Erasure requests are answered with a false confirmation.

**Remediation**: Call /account/purge unconditionally with the identity resolved by resolveBotIdentity(req) (telegram_id when linked, `web:<uid>` otherwise), keeping the existing abort-on-partial behaviour. Change app/test/account_delete_route.test.js:219-228 in the same commit - it currently pins the defect.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — One detail: the on-disk paper book is data/portfolio_web5.json, not portfolio_web:5.json — MultiUserPortfolio._sanitize strips ':' (documented at bot/web/user_gateway.py:83). Cosmetic; does not affect the finding.

- sev→UNCHANGED — None material. Worth adding to the fix: the same branch also skips the purge for a TELEGRAM-LINKED account whenever `gateway.isConfigured()` is false (app/lib/gateway.js:17-19 — `GATEWAY_SECRET.length >= 32`), which silently produces the exact failure the route's own header calls the worst version of the defect.

**Evidence**:

```
app/auth.js:1724-1730
    let botStores = null;
    if (user.telegram_id && gateway.isConfigured()) {
      let purge;
      try {
        purge = await postGateway('/account/purge',
          { telegram_id: String(user.telegram_id) }, 15000);

app/lib/identity.js:23-27
  if (u && u.telegram_linked && u.telegram_id) {
    return { id: String(u.telegram_id), linked: true, email: u.email || '' };
  }
  return { id: `web:${uid}`, linked: false, email: (u && u.email) || '' };
```

## B5-18 [HIGH] handle_account_purge misses the bot's own SQLite database entirely - LLM and news API keys, Telegram chat id/username, paper portfolio and personal ingest notes all survive account deletion

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `bot/web/user_gateway.py:2830`

**Observed**: Six stores are reported and the rollup answers `purged: true`. The SQLite database holding the user's third-party API keys, their Telegram chat id and username, their paper portfolio (positions + trade_history JSON) and up to 50 x 20,000 characters of text they pasted is never consulted, so its absence from the report is indistinguishable from success.

**Root cause**: The per-store enumeration that keeps this list honest (tests/test_account_purge.py::TestNoPerUserStoreOutlivesTheDeletePath) sweeps only `bot/core/*.py` for a module-level `def clear(user_id)`. bot/db/models.py is a different package with a different shape (module-level functions taking an INTEGER uid mapped by settings_user_id), so it is structurally invisible to the guard that exists to catch exactly this.

**Business impact**: A user's own paid third-party API keys (LLM provider, news provider) and the personal text they pasted into their agent are retained indefinitely after they delete their account and are told their data is erased. The keys remain usable by the operator against the user's provider billing.

**Remediation**: Add a bot.db.models purge step to handle_account_purge that resolves uid = settings_user_id(tg_id) and deletes from user_settings, user_news_keys, user_ingest_notes, user_telegram and user_portfolio (and tombstones/deletes the users stub row), reporting each as its own key. Widen the enumeration in tests/test_account_purge.py past bot/core/ so the next store outside that directory cannot be missed.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Note the mitigation the finder omits: user_settings.llm_api_key and ingest note bodies are Fernet-encrypted at rest (bot/db/models.py:372 _encrypt_llm_key, used by add_user_ingest_note). Survival is still real; readability requires the host key.

- sev→UNCHANGED — One nuance to carry into the fix: the ingest-note body is Fernet-encrypted at rest (bot/db/models.py:519-524 via _encrypt_llm_key), so the retained note text is ciphertext under the operator's master key rather than plaintext. That reduces exposure, not retention — the rows still survive an erasure the user was told completed.

**Evidence**:

```
bot/web/user_gateway.py:2830-2836, 2892-2896 - the complete store list:
    result: dict = {}
    # Credentials first: this is the one that matters most if the rest fails.
    try:
        from bot.core.exchange_credentials import get_credential_store
...
        store = getattr(tg_handler, "users", None)
...
            result["user_record"] = "deleted" if store.forget(tg_id) else "none"

The handler imports bot.core.exchange_credentials, user_profile_store, user_memory_store, user_leverage_store, user_strategy_store and tg_handler.users - and nothing from bot.db.models. Meanwhile the SAME file writes personal data there:
bot/web/user_gateway.py:2758-2767
    from bot.db.models import (ensure_settings_parent, get_user_settings,
                               save_user_settings, settings_user_id)
    ...
    s.llm_api_key = api_key
    save_user_settings(s)
```

## B5-19 [HIGH] Chat transcripts persisted to data/conversations.jsonl are never deleted, and clear_user() only clears memory - a restart restores them

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `bot/nlp/conversation_store.py:200`

**Observed**: No caller anywhere in the product invokes clear_user (only live_e2e_test.py:131 and tests/test_core.py:4643). The verbatim message text - capped at 2000 chars per message - remains in data/conversations.jsonl indefinitely, keyed by telegram id or "web:<uid>", and is reloaded into memory on every restart.

**Root cause**: The store was written as "in-memory with optional JSONL persistence" and clear_user was written against the in-memory half only. It was then given a persist_path in production, which made the file the durable copy while the delete path kept operating on the volatile one - and no delete path ever reached it at all.

**Business impact**: Everything a user typed to the agent - including anything they volunteered about themselves, their holdings or their finances - is retained verbatim on disk forever, with no deletion path, after they delete their account.

**Remediation**: Make clear_user rewrite the JSONL (it already has an atomic_write_text-based compactor at lines 340-357 to reuse), and wire `tg_handler.conversations.clear_user(tg_id)` into handle_account_purge as its own reported store key.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Two qualifications. (a) _maybe_compact (l.339-357) rewrites the JSONL from in-memory state once the file passes 5000 lines, so a cleared user's lines would eventually disappear — incidental, unbounded in time, and not a delete path. (b) The policy does disclose 'stored chat context' in its retention paragraph and does not list chat in the deletion sentence, so the over-promise is weaker than for credentials; the un-deletable content is real, hence MEDIUM rather than HIGH.

- sev→MEDIUM — Severity trimmed from HIGH to MEDIUM: this is retention of local on-host content with no exposure vector and no money path, and the user-facing false-erasure claim it feeds is already counted once in finding 0/1. The technical claim is fully confirmed. Also note the privacy page DOES disclose that chat context is stored indefinitely ('Nothing currently expires or purges account records, trade history or stored chat context on a schedule'), so the undisclosed-collection half of this is weaker than the undeleted half.

**Evidence**:

```
bot/nlp/conversation_store.py:200-204
    def clear_user(self, user_id: str) -> None:
        """Clear all conversation history for a user."""
        with self._lock:
            self._conversations.pop(user_id, None)
            self._user_contexts.pop(user_id, None)

bot/nlp/conversation_store.py:289-290 (the copy that is not popped)
            with open(self._persist_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
```

## B5-20 [MEDIUM] Per-user paper trading books (data/portfolio_<user>.json plus a .bak copy) have no delete path anywhere, while the privacy policy says deletion removes trades and positions

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `bot/risk/multi_portfolio.py:209`

**Observed**: The privacy page states "It removes your trades, positions, snapshots, alerts, watchlist, strategies, profile, diary, notification subscriptions, wallet links, and any exchange credentials on either side." The MySQL `trades`/`arena_trades`/`equity_snapshots` rows are indeed erased, but the bot's own per-user book - open positions plus complete trade history, keyed by telegram id or web:<uid> in the filename - is not, and neither is its .bak sidecar. No code in bot/ deletes a portfolio file.

**Root cause**: MultiUserPortfolio is a class in bot/risk/, outside the bot/core/*.py module-level `clear(user_id)` shape that the purge-coverage enumeration searches for, so the store was never surfaced as needing a delete path.

**Business impact**: A deleted user's complete trading history stays on disk indefinitely under a filename containing their Telegram id, contradicting an explicit deletion promise on the published policy.

**Remediation**: Add a delete(user_id) to MultiUserPortfolio that drops the in-memory tracker and unlinks data/portfolio_<id>.json, its .json.bak and any .conflict-*.json sidecar (and the data/venue/<venue>/ split copies), wire it into handle_account_purge as its own reported store, and re-check the policy sentence against the result.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Additional supporting fact the finder missed: bot/utils/backup.py:_CRITICAL_GLOBS includes "data/portfolio_*", so these books are also copied into every rotating archive — the same tension finding 7 raises for credentials.

- sev→UNCHANGED — Add to scope: bot/utils/backup.py:61 `_CRITICAL_GLOBS = ['data/learning/*', 'data/portfolio_*', 'data/risk_state_*']` archives these same files, so any delete() added here also needs the backup-window caveat from finding 7.

**Evidence**:

```
bot/risk/multi_portfolio.py:209-213
                    self._portfolios[user_id] = PortfolioTracker(
                        initial_balance=self._default_balance,
                        on_trade_close=self._make_close_cb(user_id),
                        state_file=f"data/portfolio_{user_id}.json",
                        trailing_config=self._trailing_config,
                    )

bot/risk/portfolio.py:536-539 (what the file holds)
            "positions": {
                tid: t.model_dump(mode="json") for tid, t in self._positions.items()
            },
            "history": [t.model_dump(mode="json") for t in self._history],
```

## B5-21 [MEDIUM] The purge-coverage guard sweeps only bot/core/*.py for module-level clear(user_id), so its "no per-user store outlives the delete path" claim is narrower than it reads

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `tests/test_account_purge.py:196`

**Observed**: Its docstring reads "A module in bot/core with a `clear(user_id)` is, by that signature, holding something keyed to a person - if the purge does not name it, either wire it or say here why it does not belong", and the EXEMPT dict is empty. Nothing states that stores outside bot/core, stores exposed as methods, and stores with no clear at all are all outside the sweep. Three such stores exist today and all three are missed by the purge.

**Root cause**: The sweep derives its question from a single implementation shape (module + top-level clear(user_id)) rather than from the property (holds data keyed to a person). Every store that chose a different shape is silently acquitted.

**Business impact**: This is the root cause of the three erasure gaps above; while it stands, the next per-user store added outside bot/core will be missed the same way and the suite will stay green.

**Remediation**: Walk the whole tree rather than bot/core, and include class methods named clear/clear_user/forget/delete taking a user id. Where a store legitimately has no clear (MultiUserPortfolio), the sweep should fail until it is either given one or added to EXEMPT with a reason - which is what the docstring already promises.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — The finding overstates the docstring's dishonesty: the very sentence it quotes states the boundary — 'A module IN BOT/CORE with a clear(user_id)…'. Only the preceding 'the NEXT per-user store cannot be forgotten either' reads wider than the sweep. This is a scope-overclaim in one sentence of a test docstring, not a broken gate; LOW.

- sev→LOW — Downgrade to LOW and correct the premise: the docstring DOES state the bot/core scope; what overreaches is the single clause 'so the NEXT per-user store cannot be forgotten either'. This finding is the meta-form of 1/2/3 and adds no distinct exposure — its value is only that widening the sweep is the cheapest way to keep those three fixed.

**Evidence**:

```
tests/test_account_purge.py:196-208
        for f in sorted(pathlib.Path("bot/core").glob("*.py")):
            ...
            for node in tree.body:
                if (isinstance(node, ast.FunctionDef) and node.name == "clear"
                        and node.args.args
                        and node.args.args[0].arg in ("user_id", "uid")):
                    found.append(f.stem)
```

## B5-22 [MEDIUM] The privacy policy's collection list and deletion list do not match what the code stores: verbatim chat text, personal ingest notes and third-party provider keys are undisclosed and undeleted

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `website/privacy/index.html:28`

**Observed**: Four disclosure gaps and one over-promise, all against a page that is otherwise unusually careful (it correctly discloses cookies, the reversible key encryption, the LLM providers, the surviving users row and the absence of any retention limit).

**Root cause**: privacy_truth.test.js pins the five claims that had already gone false and the shape of the deletion path, but never asks the two questions that would catch drift: what stores exist, and what the purge actually reaches.

**Business impact**: The published policy under-states what is collected and over-states what deletion removes, on the one document a data subject relies on to decide what to share.

**Remediation**: Add bullets for stored chat content, personal ingest notes, and BYO LLM/news provider keys; and correct the deletion paragraph until the code covers what it claims (see the erasure findings above). Extend site/test/privacy_truth.test.js with a ratchet that fails when a store is added that the deletion paragraph does not account for.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — The headline claim 'verbatim chat text … undisclosed' is materially weakened: the retention paragraph says in terms 'Nothing currently expires or purges account records, trade history or stored chat context on a schedule. Chat context is bounded by volume rather than by age.' — chat storage IS disclosed. The 'not the words you typed' phrase scopes the observed-assets bullet, not the conversation store. What genuinely survives is: ingest notes undisclosed, BYO provider keys undisclosed, deletion paragraph over-broad. Downgrade to LOW and drop the chat-text bullet.

- sev→UNCHANGED — Drop the 'verbatim chat text is undisclosed' claim — the retention section discloses stored chat context explicitly, and the 'not the words you typed' sentence is scoped to the observed-assets bullet, not a global denial. What survives: the deletion paragraph over-promises, and ingest notes and BYO LLM/news keys are absent from 'What we collect'. Confidence lowered to HIGH because two of the four asserted gaps do not hold.

**Evidence**:

```
website/privacy/index.html:28 (published policy text, "What we collect"):
"Which assets you ask the agent about - not the words you typed, but the ticker the bot itself resolved when it ran a tool for you, kept as a count per symbol over a rolling window of your twelve most recent."

website/privacy/index.html:28 ("Keeping and deleting your data"):
"It removes your trades, positions, snapshots, alerts, watchlist, strategies, profile, diary, notification subscriptions, wallet links, and any exchange credentials on either side."

bot/nlp/conversation_store.py:281-290 (what is actually written to disk):
            entry = {
                "user_id": user_id,
                "role": msg.role,
                "content": msg.content[:2000],  # Cap stored content
```

## B5-23 [MEDIUM] The append-only audit hash chain records the user id as `actor` and is immutable by design, but the policy admits only one surviving record

- **Dimension**: privacy · **Fix class**: MANUAL_ONLY · **File**: `bot/core/engine.py:1360`

**Observed**: The policy's only admission is a highlighted box titled "One row survives, with nothing in it that names you." A second, larger, deliberately un-erasable record exists and is not mentioned; and the purge's own completion is itself appended to it with `data={"user": tg_id}`.

**Root cause**: Tamper-evidence and erasability are in direct tension, and the design resolved it in favour of tamper-evidence without recording the consequence on the policy page.

**Business impact**: An erasure request is answered with a specific, quantified admission ("one row") that is incomplete, on a system whose whole design principle is not making confident claims about unmeasured state.

**Remediation**: Decide and then state it: either write a per-account pseudonym into `actor` whose mapping is destroyed on erasure (breaking linkability without breaking the chain), or add the surviving-audit-chain admission alongside the existing "one row survives" box. Do not silently keep both claims.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — One piece of evidence is misattributed and inverts its own point: bot/web/user_gateway.py:2902-2904 calls `audit(system_log, …)`, which is bot/utils/logger.py:137 — a plain structured log line into logs/system.jsonl through a RotatingFileHandler (10MB, backupCount=5). It is NOT an audit-chain append, so 'the purge writes one too [into the immutable chain]' is false. The actors recorded are opaque numeric/web ids in an operator-local log, so this is a disclosure-completeness gap: LOW.

- sev→LOW — Correct the evidence: the purge's `audit(system_log, ...)` at user_gateway.py:2902 writes a rotating log line via bot/utils/logger.py:137, not an audit_chain entry — the chain is appended only from bot/core/engine.py. Downgraded to LOW: this is a disclosure/linkability gap in an operator-only, on-host, tamper-evident log, and the tension between tamper-evidence and erasure is a legitimate design position — the defect is that the page claims exactly one surviving record.

**Evidence**:

```
bot/core/engine.py:1360
            self.audit_chain.append("POLICY_DECISION", payload, actor=str(user_id or "operator"))

bot/utils/audit_chain.py:195-196
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), sort_keys=False) + "\n")

bot/web/user_gateway.py:2902-2904 (the purge writes one too)
    audit(system_log, f"Account purge requested: {tg_id}",
          action="account_purge", result="OK" if ok else "PARTIAL",
          data={"user": tg_id, "stores": result})
```

## B5-24 [MEDIUM] Rotating backups retain erased users' encrypted exchange credentials and the whole SQLite user database for up to BACKUP_KEEP archives, contradicting the policy's "rewrites the encrypted file" wording

- **Dimension**: privacy · **Fix class**: REVIEW_REQUIRED · **File**: `bot/utils/backup.py:36`

**Observed**: The policy sentence is true of the live file and false of the archives. The archives also carry the SQLite tables that the purge never clears at all (see the bot/db/models.py finding), so those persist both live and in backup.

**Root cause**: Erasure was designed against live state; the backup set was designed independently and includes the same files.

**Business impact**: Data a user was told is gone remains recoverable from operator backups for up to two weeks, and the SQLite portion remains indefinitely because it was never deleted live either.

**Remediation**: Qualify the policy sentence to the live store and state the backup window (BACKUP_KEEP archives, default 14 at BACKUP_INTERVAL_H=24), or document a backup-expiry commitment. Note the mitigating fact already recorded at bot/utils/backup.py:47-55: data/.exchange_secret.key is deliberately NOT archived, so an off-host archive alone yields unreadable ciphertext.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Severity is inflated. The policy sentence is about the removal mechanism (rewrite vs. append), not a claim that no copy exists anywhere; the Fernet master key data/.exchange_secret.key is deliberately excluded from the archive (documented in the comment at l.44-56), so an archive alone yields unreadable ciphertext; and backups are host-local. This is a wording/retention-window disclosure gap, LOW.

- sev→LOW — Downgrade MEDIUM -> LOW. This is a policy-wording fix (qualify the sentence to the live store and state the BACKUP_KEEP window), not a code defect; the encryption-key exclusion at backup.py:47-55 already bounds the exposure to on-host access, which the live file has anyway.

**Evidence**:

```
bot/utils/backup.py:36-56 (the archived set)
    "logs/audit_chain.jsonl",
    "data/attestation_key.bin",
    ...
    "data/runeclaw.db",
    "data/secrets_vault.enc",
    ...
    "data/exchange_creds.enc",

bot/utils/backup.py:90-92
def _keep() -> int:
    ...
        return max(1, int(os.environ.get("BACKUP_KEEP", "14")))

website/privacy/index.html:28 (the claim)
"You can remove your keys at any time from either surface. Removal rewrites the encrypted file rather than leaving the old ciphertext behind."
```

## B5-25 [LOW] The policy names only a referral code in localStorage; the app writes at least six more keys including the signed-in user id

- **Dimension**: privacy · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/index.html:1328`

**Observed**: Only rc_ref is named. rc_session stores the authenticated user id, and rc_watchlist stores the user's watchlist - the same watchlist the policy elsewhere lists as collected preference data.

**Root cause**: The Cookies section was written against the cookie module (app/lib/session_cookie.js) and picked up localStorage only where the referral flow was being described.

**Business impact**: Understated disclosure of client-side storage on the document a reader uses to verify the claim in their own browser.

**Remediation**: Name the keys the way the cookies are named, with a one-line purpose each, and extend site/test/privacy_truth.test.js's cookie-naming assertion to cover localStorage keys so the list cannot drift.

**Verifier corrections** (these override the finder where they conflict):

- sev→UNCHANGED — Minor nuance: rc_watchlist is written only on the signed-OUT branch of saveUserProfile (`if (!LOGGED_IN)`), so it is a pre-login preference cache rather than the signed-in user's stored watchlist.

- sev→UNCHANGED — Two accuracy fixes: rc_session IS named on the privacy page, listed under Cookies rather than under localStorage; and rc_watchlist is written only when the visitor is signed out (dashboard.js:470 `if (!LOGGED_IN)`), so it is not the signed-in user's saved watchlist. The unnamed set is really rc_watchlist, rc_tts, rc_lang, rc_welcomed and rc_chk_done — all functional, none identifying. LOW is right.

**Evidence**:

```
website/privacy/index.html:28 (the claim, under "Cookies")
"Your browser's own localStorage may hold a referral code from a link you followed, so it still applies if you sign up on a later visit."

app/public/index.html:1328
  localStorage.setItem('rc_session',JSON.stringify({user_id:data.user_id}));

app/public/js/dashboard.js:471
      if (patch.watchlist) localStorage.setItem('rc_watchlist', JSON.stringify(patch.watchlist));
```

## B5-26 [MEDIUM] No consent or lawful-basis gate exists in code before chat text, positions and profile are transmitted to third-party LLM providers, and the policy discloses no processing location or transfer safeguard

- **Dimension**: privacy · **Fix class**: MANUAL_ONLY · **File**: `bot/llm/provider.py:62`

**Observed**: Neither exists. The leaderboard feature demonstrates the team can build an opt-in, revocable, documented consent flow (bot/core/engine.py:3690-3701 - "OPT-IN ONLY", "REVOCABLE", "UNLINKABLE"); nothing equivalent gates the LLM transfer, which is the larger and less obvious one.

**Root cause**: The LLM path predates the multi-user product and was disclosed retroactively on the policy page (privacy_truth.test.js records that the old page's "No Telegram user data is included in LLM requests" had gone false) without a corresponding gate being added.

**Business impact**: Chat content plus open positions and stated risk appetite are transmitted to third-party model providers, possibly via a relay, with no recorded basis and no stated transfer safeguard.

**Remediation**: NEEDS_LEGAL_REVIEW to decide the basis. Technically: record a per-user acknowledgement (a timestamped row, revocable, checked before the first _llm_chat transmission) modelled on the leaderboard opt-in, and add a processing-location / transfer paragraph to the policy naming the vendors' regions.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Two problems. (a) Evidence mislabelled: line 175 is a `get_key_url` (where a USER obtains their own key), not an egress endpoint, and the ALIBABA entry's actual base_url is https://hackathon.bitgetops.com/v1 — so 'the egress endpoints' misdescribes one of the four quoted lines. (b) This is a legal-basis judgment, not a defect in code that misreports state: the transfer is disclosed on the policy page in unusually direct terms, and the finder itself marks it NEEDS_LEGAL_REVIEW. Report it as a compliance question at LOW, not a MEDIUM engineering defect.

- sev→LOW — Downgrade to LOW/NEEDS_LEGAL_REVIEW and fix the evidence: line 175 is a get_key_url, not an egress endpoint; the actual Alibaba base_url is the Bitget hackathon relay, which the policy already describes. The page does disclose the transfer and its contents — what is absent is a processing-location/safeguard statement and any recorded acknowledgement. This is a legal-posture item with no code defect behind it, and should not be filed at the same severity as the erasure findings.

**Evidence**:

```
bot/llm/provider.py:62, 76, 151, 175 (the egress endpoints)
        "base_url": "https://api.anthropic.com",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "base_url": "https://openrouter.ai/api/v1",
        "get_key_url": "https://dashscope.console.aliyun.com/apiKey",

website/privacy/index.html:28 (the full disclosure of who receives it)
"Which provider handles a given request is set by the operator's configuration and can change; possible providers include OpenAI, Anthropic, Google and Alibaba/DashScope, and one configuration routes through a third-party relay rather than the vendor directly."
```

## B5-27 [CRITICAL] SystemHealthMonitor is never fed: /health, /ready and /metrics can only ever report a fabricated all-clear

- **Dimension**: observability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/system_health.py:53, 101-112, 123`

**Observed**: status is pinned to HEALTHY, error_rate_pct to 0.0, api_latency to 0ms and exchange_connected to True forever. Three operator-facing surfaces publish those constants as measurements:

1. Telegram /health - bot/skills/telegram_handler.py:8347 `text = self.engine.health.format_telegram()`
2. /ready (503 gate) - bot/web/dashboard_server.py:327-330 `_is_ready` returns `exchange_connected and status != 'CRITICAL'`, i.e. it can never return False
3. /metrics (unauthenticated Prometheus scrape) - bot/web/dashboard_server.py:391-397 emits `runeclaw_exchange_connected 1`, `runeclaw_api_error_rate_pct 0`, `runeclaw_api_calls_total 0`, `runeclaw_ready 1` unconditionally

docs/LIVE_HARDENING_RUNBOOK.md makes this the primary triage instruction - line 13: '**Watch via Telegram:** `/health` (vitals)' and line 36: '**Watch:** run `/health` and `/livepositions`'. And two proactive alerts point the operator straight at it as the follow-up: bot/core/proactive_monitor.py:1811 (WS_DOWN) and :2488 both end '👉 /health — check system vitals'. So the alert fires correctly and then hands the operator a panel that manufactures an all-clear.

**Root cause**: The monitor was written as a push-based collector ('Call record_api_call() after each exchange/LLM API call' - its own docstring, line 38) and the instrumentation at the call sites was never added. A push collector with no producers degrades to its constructor defaults, and every default here points at 'safe': `_exchange_ok = True`, and the empty-sample branch synthesises `err_rate = 0.0` rather than leaving it unknown. This is the exact 'fail-open defaults all pointed at safe' shape CLAUDE.md records for guardian_status.

**Business impact**: The bot's primary vitals panel, its container readiness gate and its Prometheus scrape all report a green system regardless of actual state. An orchestrator or load balancer reading /ready will keep routing to, and never restart, an instance whose exchange connectivity is gone; a Grafana/alertmanager rule on runeclaw_api_error_rate_pct or runeclaw_exchange_connected can never fire. On a bot holding leveraged perpetual positions, the runbook's own Stage-0 instruction ('run /health') will confirm health during an outage.

**Remediation**: Make 'never measured' a first-class state rather than a synthesised zero. Minimum: (a) give HealthSnapshot an `observed: bool` (or make error_rate_pct/api_latency_ms Optional[float] = None) set from `len(recent) > 0`, and have `_exchange_ok` start as None; (b) in format_telegram, print 'Error Rate: not measured' / 'Exchange: ⚪ unknown' when nothing was observed instead of 0.0%/🟢 Connected; (c) in `_is_ready`, treat an unmeasured exchange flag as NOT ready (the docstring already claims it 'fails CLOSED'); (d) omit `runeclaw_api_error_rate_pct` / `runeclaw_exchange_connected` from /metrics when unobserved rather than exporting 0/1. Separately, wire `record_api_call` into the exchange/LLM call path and `set_exchange_status` into the connectivity check, and drop the corresponding lines from tests/unreachable_methods_baseline.txt in the same commit (the ratchet's own rule). Add a test that drives a real SystemHealthMonitor (no test currently does - only tests/test_ops_endpoints.py, which hand-builds HealthSnapshot objects and so cannot see this).

**Verifier corrections** (these override the finder where they conflict):

- sev→HIGH — Downgrade CRITICAL to HIGH. Nothing on the money path consumes this: no risk gate, executor or trade decision reads engine.health (the class docstring's claim that it 'provides a health snapshot for risk engine' is itself unbacked — the only reader outside the display surfaces is set_ws_status). The impact is a misleading operator card, a readiness probe that cannot fail, and three Prometheus series that are constants — severe for triage, not directly loss-causing. Also correct the metrics line range to dashboard_server.py:389-397, and note in the writeup that uptime, ws_connected and the (0/0) counters are genuine.

- sev→HIGH — Factually correct in every particular. Severity CRITICAL is one notch high for this system: the fabricated all-clear misleads triage and can keep an orchestrator routing to a half-up instance, but it does not itself move money or place orders, and the ws_connected field on the same card is real. HIGH.

**Evidence**:

```
bot/core/system_health.py:53-54
        self._exchange_ok = True
        self._ws_ok = False

bot/core/system_health.py:101-112
            else:
                avg_lat = 0.0
                p99_lat = 0.0
                err_rate = 0.0

            # Determine status
            if not self._exchange_ok or err_rate > 50:
                status = "CRITICAL"
            elif err_rate > 10 or avg_lat > 5000:
                status = "DEGRADED"
            else:
                status = "HEALTHY"

The three methods that would ever move those inputs have no caller anywhere in the tree, and the repo's own ratchet already records it:

tests/unreachable_methods_baseline.txt:160-162
bot/core/system_health.py:SystemHealthMonitor.record_api_call
bot/core/system_health.py:SystemHealthMonitor.record_scan
bot/core/system_health.py:SystemHealthMonitor.set_exchange_status

The only writer that IS wired is the websocket flag, bot/core/engine.py:4059:
        self.health.set_ws_status(self.ws_feed.is_connected())
```

## B5-28 [HIGH] verify_deploy.sh reports "DEPLOY VERIFIED" for the bot box after asking nothing about what code the bot box is running

- **Dimension**: observability · **Fix class**: REVIEW_REQUIRED · **File**: `scripts/verify_deploy.sh:175-182`

**Observed**: The bot-box half contributes an `ok` line to a verdict about a claim it never tested. The exact 2026-08-20 scenario the comment cites - HEAD sitting 255 commits stale - is not detected either: `git rev-parse HEAD` prints whatever the box is on, and the script has no notion of what it SHOULD be on. Worse, the far more common failure (the pull landed but the process was never restarted, so the running bot is on the old code) is structurally invisible, and that is the same class as the 2026-08-25 incident in this file's own header.

**Root cause**: The web half was built around a content hash the server reports (/api/version), which makes it a real comparison. The bot half has no equivalent - the gateway's /health deliberately returns a fixed body, and the dashboard's /health (bot/web/dashboard_server.py:349-350) DOES return `{"status":"ok","build":build_short(),...}` but this script never calls it. The git rev-parse was substituted as a stand-in and then labelled `ok`.

**Business impact**: The gate an operator runs to confirm a deploy landed will say VERIFIED for a bot box running stale code, which is the precise pair of incidents (2026-08-20 stale checkout, 2026-08-25 half-deploy) documented in this file's own header. On a live trading bot, that means risk-limit or stop-loss fixes reported as deployed while the process enforcing them contains none of them.

**Remediation**: Query the bot box's own build stamp instead of the local checkout: `curl -fsS "$GATEWAY_URL/health"` (the dashboard liveness route registered at bot/web/dashboard_server.py:513, which already returns `build`) and compare its `build` field to `git rev-parse --short HEAD`. Apply the same absent-field discipline the web half already has (lines 105-118): an unparseable or missing `build` is `unk`, not `fail` and not `ok`. If the field is missing, `unk "the bot box did not report which commit it is serving - not verified"` so the run ends INCOMPLETE rather than VERIFIED. Optionally also call scripts/verify_deploy_source.sh, which does the remote-URL comparison correctly.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Downgrade HIGH to MEDIUM and soften the title from 'asking nothing about what code the bot box is running' to 'never comparing the running process's build against this checkout'. Two compensating controls exist in the documented flow: scripts/verify_deploy_source.sh performs the real remote-URL comparison pre-launch (the 255-commit case), and scripts/verify_bot_alive.sh gates DEPLOY_DONE on the process. The residual, real gap is process-vs-checkout drift (pull landed, restart missed) plus the `ok` label overstating a line that measured nothing.

- sev→MEDIUM — Two corrections. (a) The title overstates: the box half does ask two real questions (gateway and bridge reachability) and fails on them; what it never asks is which code is running. (b) The finder names the helper `build_short()`; the actual symbol is `bot.utils.build_info.short`, imported into dashboard_server.py as `n` and rendered as `"build": n()`. Severity HIGH → MEDIUM: the documented deploy flow also runs scripts/verify_deploy_source.sh (which does compare against the remote URL) before starting, and verify_bot_alive.sh after, so this is a weak link in a chain rather than the only check.

**Evidence**:

```
scripts/verify_deploy.sh:175-182
  # Which code the box is actually on. Compared against the local checkout,
  # because a deploy that pulled the wrong commit passes every other check —
  # 2026-08-20, 255 commits stale, everything green.
  if head="$(cd "$REPO" && git rev-parse --short HEAD 2>/dev/null)"; then
    ok "checkout at $head"
  else
    unk "not a git checkout here, so the running commit could not be confirmed."
  fi

The comment promises a comparison. The code reads the LOCAL HEAD and prints it, then calls `ok`, which sets no failure. There is no second value to compare it against.
```

## B5-29 [HIGH] Forensic audit logs (trade/risk/system/scan .jsonl) are written to a cwd-relative directory, and the durable-path ratchet's regex cannot see it

- **Dimension**: observability · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/utils/logger.py:27-28, 115-116`

**Observed**: The four structured audit channels (trade.jsonl, risk.jsonl, system.jsonl, scan.jsonl, built at bot/utils/logger.py:131-134) land in `<cwd>/logs/`. `mkdir(exist_ok=True)` and RotatingFileHandler both CREATE, so a wrong cwd produces a fresh empty log tree in silence - the failure mode bot/utils/paths.py's docstring describes verbatim ('mkdir(exist_ok=True) and sqlite3.connect both create, so a wrong directory produced a brand-new empty database in silence'). deploy.sh:167-178 symlinks the repo-root `logs` to the persistent store precisely so these survive, with a comment stating persistence 'is NOT optional' and calling logs/trade.jsonl 'the forensic record'. A process launched from anywhere else writes outside that symlink and outside backup coverage.

**Root cause**: logger.py predates bot/utils/paths.py and was not migrated with the six modules that were. The ratchet meant to find the stragglers keys on a slash-bearing literal, so a module that builds its relative path from a bare directory name (`Path("logs") / filename`) is invisible to it - a blind spot in the checker, which is exactly what that test's own docstring warns produces 'the reassurance it exists to prevent'.

**Business impact**: On a wrong-cwd launch the entire structured forensic record - every trade idea, every risk decision - is written to a directory nobody backs up (bot/utils/backup.py's _CRITICAL set is joined to rootp_of(), the repo root) and nobody greps during an incident. The logs appear to be working; `logs/` at the repo root simply stays at whatever it held before. Post-mortem evidence for a live trading loss would be missing with no signal that it was ever redirected.

**Remediation**: Two changes, one commit. (1) In bot/utils/logger.py, `from bot.utils.paths import state_path` and `LOG_DIR = state_path("logs")`. (2) Widen the ratchet so a bare directory literal is a finding too - e.g. `_LITERAL = re.compile(r'["\'](?:data|logs)(?:/[^"\']*)?["\']')` - and add any resulting legitimate exemptions to tests/durable_path_baseline.txt with reasons, per that file's two-way ratchet rule. Add a driven assertion in the existing `test_every_durable_path_is_absolute_and_under_the_repo` parametrize list for `bot.utils.logger.LOG_DIR`, since a scan alone is what missed it.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Downgrade HIGH to MEDIUM. Unlike the 2026-08-19 DB incident this cannot make a surface print a false measurement — nothing in bot/ or app/ reads logs/*.jsonl back (the only readers are ollama/generate_training_data*.py, which use hardcoded /workspace paths). The loss is forensic-record continuity under a launcher that does not cd, and the tamper-evident chain that actually matters (logs/audit_chain.jsonl) is already anchored via state_path. The checker blind spot is the more valuable half of this finding and should lead it.

- sev→MEDIUM — Correct, including the blind-spot analysis of the guard. Severity HIGH → MEDIUM: nothing in the codebase READS logs/{trade,risk,system,scan}.jsonl (grep finds only writers plus docs), so the loss is forensic/human, not a wrong decision or a wrong number on a surface — unlike the runeclaw.db incident this rule was written for. It is also latent, not active: today's launchers cd correctly.

**Evidence**:

```
bot/utils/logger.py:27-28
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

bot/utils/logger.py:115-116
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / filename, maxBytes=10 * 1024 * 1024, backupCount=5,
        )

The sibling audit chain does it correctly - bot/utils/audit_chain.py:165: `self._path = state_path(path)`.

The guard that should have caught this cannot match a bare directory name:
tests/test_durable_paths_are_not_cwd_dependent.py:277
_LITERAL = re.compile(r'["\'](?:data|logs)/[^"\']*["\']')

The literal in logger.py is `"logs"` with no slash, so `_LITERAL` never fires, and bot/utils/logger.py appears in neither the findings nor tests/durable_path_baseline.txt.
```

## B5-30 [MEDIUM] The F-08 "tamper-evident and verifiable" log hash chain has no verifier, restarts at GENESIS per process, and is silently truncated by log rotation

- **Dimension**: observability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/utils/logger.py:85, 103, 115-116`

**Observed**: Three independent gaps: (1) no code path anywhere verifies these chains, so tampering with logs/trade.jsonl would be detected by nobody; (2) `_prev_hash` is instance state reset to 'GENESIS' in __init__, and each process gets a fresh formatter while the file is opened in append mode - so every restart writes a 'GENESIS' link mid-file, which any future verifier would have to special-case; (3) RotatingFileHandler with backupCount=5 deletes the oldest segment, so the chain is not merely split across files, it is permanently truncated at ~60MB per channel.

**Root cause**: The chain was bolted onto a logging Formatter, which is the wrong seam: a Formatter has no knowledge of file boundaries, cannot persist state across process lifetimes, and produces no artifact anyone was tasked with checking. The parallel implementation in audit_chain.py got all three right (state re-read from the file's tail on every append, no rotation, a reachable verify()), which shows the difference is oversight rather than intent.

**Business impact**: The repository treats a tamper-evident trade log as a real control (deploy.sh goes out of its way to SIGTERM before SIGKILL so an append is not interrupted, watchdog.sh:71-74). For logs/trade.jsonl that control is nominal: nothing would ever notice an edited line, and the oldest evidence is deleted on a size trigger with no record. In a dispute over a live trade the chain would not support the claim made for it.

**Remediation**: Decide which of the two it is and say so. Either (a) add a verifier for logs/*.jsonl (a `verify_log_chain(path)` alongside audit_chain.verify, tolerant of a 'GENESIS' restart marker, plus a preflight/ops command that runs it) and stop deleting segments - set backupCount high enough that a full retention window survives, or archive rotated files rather than dropping them; or (b) if the chain is not actually relied on, delete the prev_hash field and the F-08 docstring claim, so nobody mistakes an unchecked field for an integrity control. Do not leave it as-is: an unverifiable integrity claim in a docstring is what an auditor and an operator will both trust.

**Verifier corrections** (these override the finder where they conflict):

- sev→LOW — Downgrade MEDIUM to LOW. Nothing depends on this chain and no operator surface reports on it, so the concrete harm is a docstring making an integrity claim the code does not support — worth resolving in the direction of deleting the claim rather than building a verifier, given the real tamper-evident chain (audit_chain.jsonl, anchored, unrotated, verified from two call sites) already exists beside it.

- sev→MEDIUM — Stands as written. One addition that makes it worse than reported — see 'missed': the bot box runs two processes that both import this module and both append to the same files, so the chains interleave, which no restart-marker tolerance can repair.

**Evidence**:

```
bot/utils/logger.py:83-85
    def __init__(self) -> None:
        super().__init__()
        self._prev_hash: str = "GENESIS"

bot/utils/logger.py:102-103
        line = json.dumps(entry, default=str)
        self._prev_hash = hashlib.sha256(line.encode()).hexdigest()

bot/utils/logger.py:114-116
        # File handler with rotation (10MB per file, keep 5 backups)
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / filename, maxBytes=10 * 1024 * 1024, backupCount=5,
        )

The module docstring claims (bot/utils/logger.py:9-10): 'F-08 FIX: Hash chain -- each JSON line includes prev_hash = sha256(previous line), making the audit trail tamper-evident and verifiable.'
```

## B5-31 [MEDIUM] Every trade blocks the event loop on four full scans of an audit chain that nothing rotates

- **Dimension**: observability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/engine.py:1555, 1559, 1646, 1650, 1655`

**Observed**: _sync_flight_records is a synchronous def called from three places on the trading path: bot/core/engine.py:1105 (inside _on_live_position_closed, right after appending the OUTCOME event), :5963 (after a re-check rejection), and :6460 (inside the async `_confirm_trade_inner`, immediately after sealing an EXECUTED_LIVE decision). Only the network send is backgrounded - `sync_flight_records_in_background` at line 1657 - while the four full-file scans happen inline on the caller's thread, which for line 6460 is the event loop. The chain is never rotated: bot/utils/audit_chain.py:59-61 states outright that append was optimised because it iterated 'a file that nothing rotates', but verify() and get_chain_length() were left as forward full scans on the same hot path.

**Root cause**: The append path was correctly optimised to O(1) via `_tail_lines` (audit_chain.py:44-101) when its linear cost was measured, but the two other linear readers on the same call chain were not, and guardian_status duplicates both. The growth assumption ('nothing rotates') was recorded as a fact without a corresponding bound on the readers.

**Business impact**: Cost grows monotonically with the number of decisions the bot has ever made, on the exact code path that runs at trade confirmation and at position close. At ~100k entries this is on the order of seconds of blocked event loop per trade, during which SL/TP monitoring, the WS heartbeat and the Telegram poller do not run - on a leveraged perpetuals book that is the window in which a stop is not being watched. It degrades silently and only on long-lived deployments, which is when it is hardest to attribute.

**Remediation**: Three cheap changes: (1) drop the duplicate work - _sync_flight_records already computes `ok` and `length`, so pass them into guardian_status (or have guardian_status accept a precomputed chain dict) rather than recomputing; (2) replace get_chain_length()'s full scan with the sequence number already on the tail entry (`_tail_state()` returns next_sequence, which IS the length); (3) either move verify() off the trade path (run it on a timer or on demand from /guardian) or run the whole of _sync_flight_records via `asyncio.to_thread` from the async call sites so it cannot block the loop. None of these change the chain format or touch a ratchet baseline.

**Verifier corrections** (these override the finder where they conflict):

- sev→MEDIUM — Severity stands at MEDIUM, but state the today-vs-later split plainly: logs/audit_chain.jsonl is 38 lines on this checkout, so present-day cost is negligible; the defect is that the two linear readers were left on the hot path after the append path was explicitly optimised for the same reason, on a file with no bound. Fix (1) alone — pass the already-computed ok/length into guardian_status — halves it with no behaviour change.

- sev→LOW — Severity MEDIUM → LOW. The finder itself notes the chain is 38 lines (45KB) today, and the chain only grows per decision/outcome — not per scan — so the measured cost is microseconds and the harm is entirely a growth projection. Nothing here is wrong output; it is a latency/altitude issue plus an over-strong docstring.

**Evidence**:

```
bot/core/engine.py:1643-1655 (_sync_flight_records, a plain `def` at line 1629)
            entries = self.audit_chain.get_entries(limit=400)
            records = assemble_flight_records(entries, limit=50)
            incidents = assemble_incident_records(entries, limit=40)
            ok, problems = self.audit_chain.verify(str(self.audit_chain._path))
            tip = entries[-1].entry_hash if entries else ""
            chain = {
                "ok": bool(ok),
                "length": self.audit_chain.get_chain_length(),
...
            try:
                gstatus = self.guardian_status()

and guardian_status repeats both scans, bot/core/engine.py:1555-1560
            status["chain"]["length"] = self.audit_chain.get_chain_length()
            entries = self.audit_chain.get_entries(limit=1)
            status["chain"]["tip"] = entries[-1].entry_hash if entries else ""
            try:
                ok, _problems = self.audit_chain.verify(str(self.audit_chain._path))

Both underlying methods read the whole file - audit_chain.py:238-239 `with file_path.open(...) as fh: for line_no, raw_line in enumerate(fh, ...)` and audit_chain.py:225-231 `for line in fh: if line.strip(): count += 1`.
```

## Suspected in batch 5

- **[INFORMATIONAL]** npm lifecycle install scripts execute unrestricted in every CI workspace install — `.github/workflows/ci.yml:316, 348, 516, 636, 668`
- **[LOW]** The bot dashboard's /ready leaks raw exception text on an unauthenticated, 0.0.0.0-bound endpoint, contradicting the F-15 coarse-reason rule the web app implements — `bot/web/dashboard_server.py:369-371`


========================================================================

# Batch 6 — a11y, reachability, docs-consistency, tests

**39 raw · 38 CONFIRMED · 1 SUSPECTED · 0 REFUTED**

> **Every a11y finding is STATIC INSPECTION.** No browser was driven in
> this container, so none of them is a runtime observation and no WCAG
> conformance is claimed. Treat each as NEEDS_RUNTIME_VALIDATION.


## B6-01 [HIGH] 3D Strength Map is pointer-only: no keyboard path to any coin's data, in either the WebGL view or the 2D fallback

- **Dimension**: a11y · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/strengthmap.js:439-446, 220, 227-230`
- **Standard**: WCAG 2.2 Level A: 2.1.1 Keyboard. Also WCAG 2.2 Level AA: 2.5.7 Dragging Movements (orbit has no single-pointer alternative). EN 301 549 §9.2.1.1 / §9.2.5.7.

**Observed**: Selecting a coin — and therefore reaching its factor breakdown and the venue links that take a user to a trade — is possible only with a mouse or touch tap on the canvas, or a mouse click on a fallback table row. Orbiting the scene is drag-only and zooming is wheel-only (OrbitControls at app/public/js/strengthmap.js:300-303), with no single-pointer non-drag alternative and no keyboard alternative.

**Root cause**: The page was built as a pure pointer-driven WebGL scene; the 2D fallback was written for the no-WebGL case (a rendering concern) rather than for the no-pointer case (an interaction concern), so it inherited the same click-only row handler instead of using anchors or buttons.

**Remediation**: Make the fallback rows real controls: emit `<tr>` cells wrapping a `<button type="button" data-sym=...>` (or give the row `role="button" tabindex="0"` plus an Enter/Space handler — the dashboard already has that pattern at app/public/js/dashboard.js:8516-8520). Render the fallback table always (visually hidden behind a "list view" toggle when WebGL is up) so keyboard users have a route to every coin regardless of WebGL. For 2.5.7, add zoom-in / zoom-out / reset-view buttons beside the bias segmented control, which OrbitControls can drive directly.

**Verifier corrections** (these override the finder):

- sev→MEDIUM — One evidence claim is wrong: the finder says grep for 'strengthmap' in app/test/ returns only unrelated files. app/test/strengthmap_page.test.js exists and reads both strengthmap.html and strengthmap.js — but it pins routing, the import map, the venue picker and §4 payload rules, and asserts nothing about keyboard operability, so the 'no test pins this' conclusion survives. Severity lowered HIGH->MEDIUM: this is a public read-only data-viz funnel (that same test file records 'the "trade" action is a venue picker of external deep links, not an order path'), and the same coins and the trade ticket are reachable from /dashboard, so no money path is keyboard-blocked — the loss is the visualisation and its factor breakdown.

- sev→MEDIUM — Two factual errors in the write-up, neither fatal. (1) The existing-test claim is wrong: app/test/strengthmap.test.js, app/test/strengthmap_page.test.js, app/test/strengthmap_polish.test.js and app/test/landing_strengthmap.test.js all exist. I grepped them for keyboard/tabindex/keydown/role/aria and found only one unrelated match, so no test pins this — the conclusion survives, the premise does not. (2) boot() is at strengthmap.js:275, not :373. Severity: HIGH is inflated. This is a public read-only data-viz page; the panel's only money-adjacent element is an outbound venue link, and every one of those venues is reachable from the app's own market surfaces. A Level A keyboard failure on a public tool page is MEDIUM here, not HIGH.

**Evidence**:

```
app/public/js/strengthmap.js:438-446 — the ONLY way to open a coin:
```js
  canvas.addEventListener('pointerdown', (e) => { downXY = [e.clientX, e.clientY]; lastInteract = now(); });
  canvas.addEventListener('pointerup', (e) => {
    if (!downXY) return;
    const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
    if (moved > 6) return; // a drag, not a tap
    setNDC(e.clientX, e.clientY);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    if (hit && hit.object.userData.node) openPanel(hit.object.userData.node.coin);
  });
```
The 2D fallback that is supposed to be the non-WebGL path is pointer-only too — app/public/js/strengthmap.js:220 and 227-230:
```js
    return `<tr style="cursor:pointer" data-sym="${esc(c.symbol)}"><td><b>${esc(c.base)}</b></td>
...
  $('smFbBody').addEventListener('click', (e) => {
    const tr = e.target.closest('[data-sym]'); if (!tr) return;
    const c = state.coins.find((x) => x.symbol === tr.dataset.sym); if (c) openPanel(c);
  });
```
The `<tr>` carries no `role`, no `tabindex` and no key handler. The canvas (app/public/strengthmap.html:125) is not focusable:
```html
<canvas class="sm-canvas" id="smCanvas" aria-label="3D strength map of the USDT-perp universe" data-i18n-attr="aria-label:a11y.strength_map"></canvas>
```
`grep -n "keydown\|keyup\|tabinde
```

## B6-02 [MEDIUM] Command palette is a role="dialog" with no focus trap, no aria-modal, no focus return, and no aria-activedescendant — shipped on 35 public pages

- **Dimension**: a11y · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/palette.js:111-147`
- **Standard**: WCAG 2.2 Level A: 2.4.3 Focus Order. Level A: 4.1.2 Name, Role, Value. EN 301 549 §9.2.4.3 / §9.4.1.2.

**Observed**: Tab escapes into the page behind the overlay; the background is never inerted or marked `aria-hidden`; focus is not restored on close; and the arrow-key selection is announced to nobody.

**Root cause**: palette.js was written as a self-contained IIFE ("Self-contained (no deps)", app/public/js/palette.js:11) and therefore did not reuse `RC.modalA11y` — which is loaded on most, but not all, of these pages (e.g. roots.html loads anchor_cell.js, i18n.js and palette.js but not app.js).

**Remediation**: Give the input `role="combobox" aria-expanded="true" aria-controls="<listbox id>" aria-autocomplete="list"`, assign each rendered option an id and set `input.setAttribute('aria-activedescendant', ...)` in the same block that toggles `aria-selected` (app/public/js/palette.js:104-108). Add `wrap.setAttribute('aria-modal','true')`, an inert/Tab-cycle pass over the other body children, and stash `document.activeElement` in `open()` to restore it in `close()`. Where `window.RC.modalA11y` is present, delegate to it rather than reimplementing; otherwise inline the same ~20 lines so pages without app.js are covered too.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The existing-test claim is materially false. app/test/palette.test.js EXISTS and is squarely about this file — it has a 'keyboard contract: toggle, slash guard, escape' test asserting /role', 'dialog'/, Escape handling, the INPUT|TEXTAREA|SELECT slash guard and the reduced-motion fade. What it does not assert is aria-modal, a focus trap, focus return or aria-activedescendant, so the defect is still unpinned — but 'nothing in app/test/ references palette.js' is wrong and would mislead whoever goes to add the test.

- sev→LOW — The existing-test claim is WRONG and should be corrected: app/test/palette.test.js exists and explicitly pins the palette's keyboard contract (Ctrl/Cmd+K, the slash guard, Escape, `role', 'dialog'`, reduced motion). It does NOT assert aria-modal, focus trap, focus return or activedescendant, so the defect itself is genuinely unpinned — but 'nothing in app/test/ references palette.js' is false. Severity: LOW rather than MEDIUM. The palette is navigation-only (app/test/palette.test.js:26 pins `location.href = it[3]` as the only act), so on Enter the page unloads and focus return is moot; the only lost case is Esc. Every ROOMS destination is an ordinary link in the site nav, so no function is keyboard-unreachable — the harm is a confusing overlay, not a lost capability.

**Evidence**:

```
app/public/js/palette.js:116-141 — the dialog is opened with nothing but a role and a label, and focus is dropped into the input with the whole page still reachable behind it:
```js
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-label', T('pal.aria', 'Command palette'));
...
      + '<div class="rcp-box"><input type="text" spellcheck="false" autocomplete="off" '
...
      + '<div class="rcp-ls" role="listbox"></div></div>';
    document.body.appendChild(wrap);
    input = wrap.querySelector('input');
    list = wrap.querySelector('.rcp-ls');
    render('');
    input.focus();
```
Closing throws the node away without restoring focus (app/public/js/palette.js:92-95):
```js
  function close() {
    if (wrap) { wrap.remove(); wrap = null; }
    document.removeEventListener('keydown', onNav, true);
  }
```
Arrow keys repaint the selection but focus never leaves the input and nothing points at the active option (app/public/js/palette.js:100-108):
```js
      sel = (sel + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      var els = list.querySelectorAll('.rcp-it');
      els.forEach(function (el, i) {
        el.classList.toggle('on', i === sel);
        el.setAttribute('aria-selected', String(i === sel));
      });
```
The repo already owns the correct helper — app/public/js/app.js:464-497 `modalA11y()` sets `aria-modal`, `inert`s every other 
```

## B6-03 [MEDIUM] Operator war-room dashboard: --text-dim (#64748b) fails AA at 3.83:1 and carries every table header, stat label and empty-state line

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard.html:20, 191-195, 227-235, 336, 346, 395, 436, 454, 465`
- **Standard**: WCAG 2.2 Level AA: 1.4.3 Contrast (Minimum). EN 301 549 §9.1.4.3.

**Observed**: Every column header in Active Positions / Recent Trades, every stat label (Balance, Equity, Open, Trades, Win Rate, Drawdown, Checks, Rejected, Trips, Loss Streak, LLM Calls, Cost, Tokens), the LLM tier provenance notes, the empty-state lines ("No open positions", "No trade history") and the connection status line render at 3.83-4.03:1, below AA.

**Root cause**: This file predates and does not import app/public/styles.css; it re-declares its own :root ramp (bot/web/dashboard.html:10-22) with a dimmer grey than the platform's, and it is outside the scope of app/test/palette_contrast.test.js, which reads only app/public/styles.css.

**Remediation**: Raise `--text-dim` to the platform's `--text-3` value #8f99ab (6.40:1 on #12141c, and 6.7:1 on this file's #111520) — a one-line change at bot/web/dashboard.html:20 that fixes all nine usages at once. Then extend app/test/palette_contrast.test.js (or add a sibling) to read this file's :root block, so a second ramp cannot drift again.

**Verifier corrections** (these override the finder):

- sev→LOW — Arithmetic: 3.78:1, not 3.83:1 (same conclusion). Severity lowered MEDIUM->LOW: this is the internal operator war room, not a user surface. bot/web/dashboard_server.py:435-452 documents that the whole /api/* surface is fail-closed token-gated and that bot/main.py:455 is meant to bind it to a private network, so the affected population is the one or two operators who hold DASHBOARD_TOKEN — a real AA failure, but not a product-wide one.

- sev→LOW — Facts all hold. Severity down to LOW: this is an internal single-operator war room whose data API is fail-closed behind DASHBOARD_TOKEN and documented as belonging on a private network (bot/web/dashboard_server.py:440-452). Sub-AA labels here inconvenience one operator; they do not reach a user or move money. The proposed one-line fix is sound — I verified #8f99ab scores 6.35:1 on #111520 and #94a3b8 scores 7.11:1, so either clears AA.

**Evidence**:

```
bot/web/dashboard.html:11-21 defines the pair:
```css
  --bg: #0a0c10;
  --panel: #111520;
...
  --text-dim: #64748b;
```
and it is the colour of every column header and every stat label — bot/web/dashboard.html:191-195 and 227-235:
```css
.stat-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-dim);
```
```css
.tbl th {
  text-align: left;
  padding: 6px 8px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-dim);
```
Arithmetic (WCAG relative-luminance formula):
  #64748b -> sRGB (100,116,139) -> linearised (0.1274, 0.1776, 0.2670) -> L1 = 0.2126*0.1274 + 0.7152*0.1776 + 0.0722*0.2670 = 0.1734
  #111520 -> sRGB (17,21,32)    -> linearised (0.00605, 0.00750, 0.01444) -> L2 = 0.00699
  ratio = (0.1734 + 0.05) / (0.00699 + 0.05) = 0.2234 / 0.05699 = 3.83:1
Against the header's gradient end #0d1018 (`.header` at bot/web/dashboard.html:64) it is 4.00:1. AA requires 4.5:1; none of these are large text (10px, 11px, 12px, 13px).
```

## B6-04 [MEDIUM] Operator war-room dashboard has no landmarks, one heading for six panels, and its live connection/state changes are announced to nobody

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard.html:413-505, 555-562, 820-835`
- **Standard**: WCAG 2.2 Level A: 1.3.1 Info and Relationships. WCAG 2.2 Level AA: 4.1.3 Status Messages. EN 301 549 §9.1.3.1 / §9.4.1.3.

**Observed**: Six panel titles are `<span class="panel-title">`, carrying their heading role in CSS only. The connect/disconnect transition — the single most important thing on the page, because it decides whether every other number is stale — changes silently.

**Root cause**: The page was styled first; `panel-title` is a visual treatment (Cinzel, uppercase, gold, letter-spaced) that was never given semantics, and the status span was written as a plain label rather than a status region.

**Remediation**: Change `<span class="panel-title">` to `<h2 class="panel-title">` at bot/web/dashboard.html:435, 453, 464, 475, 485, 504 (the class already carries all the styling, so no visual change). Wrap the grid in `<main>`. Add `role="status"` to `#connStatus` at :424 — the existing textContent writes then announce themselves with no JS change.

**Verifier corrections** (these override the finder):

- sev→LOW — Severity lowered MEDIUM->LOW for the same reason as finding 2: this is the token-gated, private-network operator dashboard, not a public surface. The proposed fix (span->h2, wrap in <main>, role="status" on #connStatus) is correct and costs nothing visually.

- sev→LOW — Every claim checks out; the quoted grep result is exact. Severity LOW rather than MEDIUM for the same reason as finding 2: internal operator page, one user, private bind. The proposed fix (span -> h2, wrap in <main>, role="status" on #connStatus) is genuinely zero-visual-risk since .panel-title carries all styling.

**Evidence**:

```
The whole page has exactly one heading and no landmark, role or live region. `grep -n "<h1\|<h2\|<h3\|aria-live\|role=\|<main\|<nav\|<footer\|alt=" bot/web/dashboard.html` returns a single line: `416:      <h1>⚔️ RUNECLAW</h1>`. Every panel is titled by a styled span — bot/web/dashboard.html:433-435, and repeated at :453, :464, :475, :485, :504:
```html
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Portfolio</span>
```
The connection status is rewritten in place with no live region — bot/web/dashboard.html:424 and :555-562:
```html
      <span id="connStatus">Connecting...</span>
```
```js
  const age = (Date.now() - lastUpdate) / 1000;
  if (lastUpdate && age > 10) {
    $('connStatus').textContent = 'Disconnected';
    $('connStatus').style.color = 'var(--red)';
    $('statusDot').className = 'status-dot dot-red';
  }
```
and again at :822-823 / :832-833 (`'Connected'` / `'Disconnected'`).
```

## B6-05 [MEDIUM] Platform header chips announce PAPER vs LIVE and engine reachability with no live region — the mode change is silent to screen readers

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/dashboard.html:21, 24`
- **Standard**: WCAG 2.2 Level AA: 4.1.3 Status Messages. Level A: 1.3.1 Info and Relationships (the `title`-only reason). EN 301 549 §9.4.1.3.

**Observed**: The transition into LIVE mode — the moment the account starts risking real money — and the transition to ENGINE OFFLINE are both silent. The page's own comment at app/public/js/dashboard.js:217-230 explains how carefully the four connection states are distinguished; none of that distinction reaches a screen reader.

**Root cause**: The chips were built as visual chrome; the live-region discipline applied elsewhere in this codebase (26 `aria-live` regions across app/public/*.html, including `#tgTokArea` at app/public/js/dashboard.js:4935) was not applied to the two top-bar chips.

**Remediation**: Add `role="status"` to both spans in app/public/dashboard.html:21 and :24 — the existing `textContent` writes then announce themselves with no JS change. Move the `title` string in `set()` (app/public/js/dashboard.js:237) into visible text or an `aria-describedby` target so the reason is not pointer-only.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. MEDIUM stands: PAPER->LIVE is the money-relevant transition on the main user surface, and role="status" on two spans is a two-attribute fix with no JS change.

- sev→MEDIUM — Stands as written, MEDIUM is right. This is the user-facing production dashboard, and the elaborate four-state honesty model the file's own comment at :217-230 describes reaches sighted users only. Two nuances worth carrying into the fix: role="status" on #modeChip must be added carefully because the element starts with class 'hidden' and is unhidden by classList.remove('hidden') at :251 — a live region that gains content while display:none may not announce in all AT, so the first announcement after unhide should be verified rather than assumed.

**Evidence**:

```
app/public/dashboard.html:21 and :24 — plain spans, no role, no aria-live:
```html
  <span id="connChip" class="chip chip--offline">● CONNECTING</span>
...
    <span id="modeChip" class="chip chip--paper hidden">PAPER</span>
```
Both are rewritten asynchronously — app/public/js/dashboard.js:231-247:
```js
  function updateConnChip() {
    const el = document.getElementById('connChip');
    if (!el) return;
    const set = (text, cls, title) => {
      el.textContent = text;
      el.className = `chip ${cls}`;
      el.title = title;
    };
```
and app/public/js/dashboard.js:248-267:
```js
  function updateModeChip(pf) {
...
    if (live && pf.live_unavailable) {
      el.textContent = 'LIVE — BALANCE UNAVAILABLE';
      el.className = 'chip chip--warn';
      return;
    }
    el.textContent = live ? 'LIVE' : 'PAPER';
```
The reason for the connection verdict is carried only in `el.title` (line 237), on a non-focusable `<span>` — unreachable by keyboard and by touch.
```

## B6-06 [MEDIUM] Copy-to-clipboard controls: two declare role="button" but ignore Enter/Space, two are bare divs with no role or tabindex

- **Dimension**: a11y · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/dashboard.js:4952, 5018, 4954-4972`
- **Standard**: WCAG 2.2 Level A: 2.1.1 Keyboard; 4.1.2 Name, Role, Value. EN 301 549 §9.2.1.1 / §9.4.1.2.

**Observed**: Two focusable, button-announced controls do nothing on any key press; two clickable controls are invisible to keyboard and to assistive technology.

**Root cause**: The `role="button" tabindex="0"` idiom was copied from the `[data-sym]` cards, but the keyboard bridge that makes that idiom work was written against `[data-sym][role="button"]` rather than `[role="button"]`, so it silently excluded every non-symbol adopter.

**Remediation**: Cheapest correct fix: make them real `<button type="button" class="token-display">` elements — they get keyboard activation, the button role and the focus ring for free, and `esc()`-ed text content still renders identically. If the markup must stay a div, widen the selector at app/public/js/dashboard.js:8518 from `[data-sym][role="button"]` to `[role="button"]` and dispatch a click, and add `role="button" tabindex="0"` plus a keydown branch to the `data-act` delegate at app/public/index.html:1782.

**Verifier corrections** (these override the finder):

- sev→LOW — Severity lowered MEDIUM->LOW. The failure is real (a role="button" that ignores Enter/Space is a 2.1.1 + 4.1.2 failure) but the content of all four controls is visible plain text a user can select and copy manually, and none of them is on an order path — the worst outcome is friction linking Telegram or copying an invite link.

- sev→LOW — Facts confirmed. Severity LOW rather than MEDIUM: in all four cases the value being copied is rendered as visible, selectable text right there (the token in #tgTok/#tok-box, the URL in #refLink, the TOTP secret in #tfa-secret), and #tok-box/#tfa-secret sit beside prose that repeats the value (`/link ${tok}`), so no capability is actually lost to a keyboard user — the failure is a false role promise (4.1.2) plus lost convenience. The #tgTok/#refLink pair is the real half; the two bare divs are the weaker half and should not be leaned on.

**Evidence**:

```
app/public/js/dashboard.js:4952 and :5018 promise button behaviour:
```js
          <div class="token-display" id="tgTok" role="button" tabindex="0" title="Copy">${esc(tok)}</div>
```
```js
        <div class="token-display" id="refLink" role="button" tabindex="0" title="Copy invite link">${esc(link)}</div>
```
but the only handler is a click delegate — app/public/js/dashboard.js:4941 and :4954-4956:
```js
    onView('click', async (e) => {
...
      const tokEl = e.target.id === 'tgTok' ? e.target : e.target.closest?.('#tgTok');
      if (tokEl) {
        try {
          await navigator.clipboard.writeText(tokEl.textContent.trim());
```
`onView` is click-only (app/public/js/dashboard.js:78-81: `container.addEventListener(type, handler, ...)`). The one keyboard bridge on the page is scoped to a different selector — app/public/js/dashboard.js:8516-8520:
```js
  document.body.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeSymModal(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-sym][role="button"]')) {
      e.preventDefault(); openSymbol(e.target.getAttribute('data-sym'), _geoOf(e.target));
    }
  });
```
Neither `#tgTok` nor `#refLink` carries `data-sym`, so they never match. The same page family has two copy controls that are not even announced as controls — app/public/index.html:821 and :1569:
```html
        <div
```

## B6-07 [MEDIUM] Silent demo video with no alternative for time-based media and no accessible name

- **Dimension**: a11y · **Fix class**: REVIEW_REQUIRED · **File**: `site/src/routes/index.tsx:86-102`
- **Standard**: WCAG 2.2 Level A: 1.2.1 Audio-only and Video-only (Prerecorded). Level A: 4.1.2 Name, Role, Value. EN 301 549 §9.1.2.1 — and this is a consumer-facing commercial site, so European Accessibility Act scope applies.

**Observed**: A 2.2MB silent screen recording of the product is the only demonstration on the landing page, with no textual equivalent of what it shows, and the player itself is unnamed.

**Root cause**: The video was added as a hero asset; the accompanying caption was written for the honesty concern ("not a live feed") rather than as a media alternative, and no transcript was authored.

**Remediation**: Add `aria-label="Recorded RUNECLAW session"` (or `aria-labelledby` pointing at the caption paragraph) to the `<video>`, and publish a short text alternative directly beneath it — a `<details><summary>What this recording shows</summary>` block listing the steps the session walks through is enough to satisfy 1.2.1 and costs no layout. Rebuild the site so website/index.html picks it up (the repo already gates on "the committed site is the built site").

**Verifier corrections** (these override the finder):

- sev→LOW — Severity lowered MEDIUM->LOW. It is a genuine WCAG 1.2.1 Level A gap, but the surface is a marketing landing page whose entire substance is restated in text elsewhere on the same route; nothing functional is gated behind the recording. Note also the surrounding comment at site/src/routes/index.tsx:76-81 still says `preload="none"` while the element ships preload="metadata" — unrelated to a11y, but the comment the finder did not read does not change the finding.

- sev→LOW — Technically correct (WCAG 1.2.1 Level A) and the atom analysis independently reproduces. Severity down to LOW: this is a marketing landing page, the video is a supplementary product demo rather than the sole carrier of any information (the rest of the route is text), and the element already degrades to a labelled download link. Also worth noting the finder's own quoted docblock at site/src/routes/index.tsx:78 says preload="none" while the code says preload="metadata" — an unrelated stale comment, not part of this finding.

**Evidence**:

```
site/src/routes/index.tsx:86-102:
```jsx
        <video
          controls
          preload="metadata"
          playsInline
          className="block aspect-video w-full bg-surface-2"
        >
...
          <source src="/demo-recording.mp4" type="video/mp4" />
          <source src="/demo-recording.webm" type="video/webm" />
          Your browser cannot play embedded video.{' '}
          <a href="/demo-recording.mp4">Download the demo (MP4)</a>.
        </video>
```
The only surrounding text is site/src/routes/index.tsx:104-106:
```jsx
      <p className="mt-3 text-center text-xs text-ink-3">
        A recorded session. Not a live feed.
      </p>
```
The asset is video-only. Parsing website/demo-recording.mp4 (2,179,259 bytes) for handler and codec atoms: `hdlr` appears at offsets [2132759, 2179186], `vide` at [162, 2132771], `avc1` at [24, 2132892]; there is no `soun` handler, no `mp4a` and no `esds` — a single video track with no audio track. `grep -rn "track kind\|captions\|<track" site/src/ website/index.html` returns nothing.
```

## B6-08 [MEDIUM] Keyboard focus is obscured by the sticky topbar and the fixed bottom tab bar — no scroll-padding anywhere in the app shell

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/styles.css:184-190, 219-226, 1060`
- **Standard**: WCAG 2.2 Level AA: 2.4.11 Focus Not Obscured (Minimum). EN 301 549 (2021 revision tracks WCAG 2.1; the EAA-referenced update tracks 2.2).

**Observed**: Only `.section[id]` and `nav.page-index[id]` carry a scroll offset. Every button, link, input and `role="button"` card in the app — including the trade forms and the position rows — can be scrolled entirely under the topbar or the tab bar when it receives focus.

**Root cause**: `scroll-margin-top` was added for in-page anchor navigation (the page-index jump links) rather than for focus, so it was scoped to the anchor targets instead of applied as `scroll-padding` on the scroll container, which covers both cases.

**Remediation**: One rule: `html { scroll-padding-top: var(--topbar-h); scroll-padding-bottom: var(--tabbar-h); }` in app/public/styles.css, dropping the bottom value inside the `@media (min-width: 1024px)` block where the tab bar is hidden. On the marketing site add `html { scroll-padding-top: 4.5rem; }` to site/src/styles.css beside the existing `scroll-behavior: smooth`.

**Verifier corrections** (these override the finder):

- sev→LOW — Two corrections. (a) The bottom half is partially mitigated already: app/public/styles.css:205 gives .content `padding-bottom: calc(var(--tabbar-h) + var(--s6))`, so the last focusable in the scroll flow is not tucked under the tab bar; the topbar half has no such mitigation. (b) The claim that 'every button, link, input and role=button card ... can be scrolled entirely under' is an inference about UA scroll-into-view behaviour, not something read from the code — WCAG 2.4.11 requires ENTIRELY hidden, and whether a given control ends up entirely under 56px of chrome depends on its height and the browser. Severity lowered MEDIUM->LOW to match that; the one-line scroll-padding fix is still correct and cheap.

- sev→LOW — Not refuted, but weaker than presented and one mitigation was missed. app/public/styles.css:205 gives .content `padding-bottom: calc(var(--tabbar-h) + var(--s6))`, so page content never rests under the tab bar — the bottom half of the claim only bites transiently during a focus scroll, not at rest. And WCAG 2.4.11 requires the focused component be ENTIRELY hidden; whether a given control lands fully behind 56px of sticky header is UA- and element-height-dependent and was not demonstrated. The finder correctly flagged this themselves. LOW, and it should be presented as a hardening gap ('add scroll-padding') rather than as a confirmed conformance failure.

**Evidence**:

```
app/public/styles.css:184-190 — a 56px sticky header over the scroll flow:
```css
.topbar {
  position: sticky; top: 0; z-index: 50; height: var(--topbar-h);
  display: flex; align-items: center; gap: var(--s4); padding: 0 var(--s4);
  background: rgba(10, 11, 16, .85); backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line);
}
```
app/public/styles.css:219-222 — a 58px fixed bar over the bottom of the viewport (`--tabbar-h: 58px`, line 122):
```css
.tabbar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 60; height: var(--tabbar-h);
  display: flex; background: rgba(14, 16, 23, .96); backdrop-filter: blur(12px);
```
The only scroll offset in the whole stylesheet is scoped to two selectors — app/public/styles.css:1060:
```css
.section[id], nav.page-index[id] { scroll-margin-top: calc(var(--topbar-h) + var(--s3)); }
```
`grep -rn "scroll-padding\|scroll-margin" app/public/ site/src/ bot/web/dashboard.html` returns that one line and nothing else. The marketing site has the same shape with no offset at all — site/src/routes/__root.tsx:62 `className="sticky top-0 z-50 border-b border-line bg-bg/85 backdrop-blur-md"` and site/src/styles.css:70 `html { color-scheme: dark; scroll-behavior: smooth; }`.
```

## B6-09 [LOW] Strength Map panel keeps focusable controls and links in the tab order while it is fully transparent and non-interactive

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/strengthmap.html:45-48, 133-136`
- **Standard**: WCAG 2.2 Level AA: 2.4.7 Focus Visible. Level A: 2.4.3 Focus Order. EN 301 549 §9.2.4.7 / §9.2.4.3.

**Observed**: `opacity: 0` and `pointer-events: none` hide the panel from sight and from the mouse but not from the keyboard or from assistive technology, so at least one and (after the first open) several controls are permanently focusable while invisible.

**Root cause**: The show/hide was written as a CSS opacity transition, which needs opacity to stay animatable — so `display`/`visibility` were deliberately avoided and nothing took their place for the a11y tree.

**Remediation**: Add `visibility: hidden` to `.sm-panel` and `visibility: visible` to `.sm-panel.open`, with `transition: opacity .18s ease, transform .18s ease, visibility 0s linear .18s` on the closed state so the fade still plays. Alternatively set the `inert` attribute in `closePanel()` and clear it in `openPanel()`.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — Line-number nit only: the smClose wiring is at app/public/js/strengthmap.js:276, not :374. LOW is the right severity — the stray focusables are a close button and a handful of external links, not a destructive control.

- sev→LOW — Correct; LOW is the right severity. One line-number correction: #smClose is wired at app/public/js/strengthmap.js:276 inside boot() (which starts at :275), not :374 — the same ~98-line drift that appears in finding 0's reachability note. The quoted code itself is accurate.

**Evidence**:

```
The panel is hidden with opacity and pointer-events only — app/public/strengthmap.html:45-48:
```css
  .sm-panel { position: fixed; z-index: 6; top: 64px; right: var(--s4); width: min(360px, 92vw); max-height: calc(100vh - 90px); overflow: auto;
    background: var(--surface); border: 1px solid var(--line-2); border-top: 3px solid var(--gold-bright); border-radius: var(--radius); padding: var(--s4);
    box-shadow: var(--shadow); transform: translateY(8px); opacity: 0; pointer-events: none; transition: opacity .18s ease, transform .18s ease; }
  .sm-panel.open { opacity: 1; transform: none; pointer-events: auto; }
```
and it contains a real button plus, after the first open, a list of external venue links — app/public/strengthmap.html:133-136:
```html
<aside class="sm-panel" id="smPanel" aria-live="polite">
  <button class="x" id="smClose" aria-label="Close" data-i18n-attr="aria-label:a11y.close">&times;</button>
  <div id="smPanelBody"></div>
</aside>
```
Closing only drops the class, leaving the populated body in the DOM — app/public/js/strengthmap.js:208:
```js
function closePanel() { state.sel = null; $('smPanel').classList.remove('open'); }
```
The venue links it leaves behind are anchors — app/public/js/strengthmap.js:200-204: `` `<a class="sm-v" href="${esc(v.url)}" target="_blank" rel="noopener">` ``.
```

## B6-10 [LOW] Sign-in / create-account tabs expose role="tab" with no tabpanel, no aria-controls and no arrow-key navigation

- **Dimension**: a11y · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/index.html:706-726, 1242-1251`
- **Standard**: WCAG 2.2 Level A: 1.3.1 Info and Relationships; 4.1.2 Name, Role, Value. EN 301 549 §9.1.3.1 / §9.4.1.2. (ARIA Authoring Practices: Tabs pattern.)

**Observed**: The tabs announce a relationship that the markup does not contain. A user who activates a tab is given no programmatic route to the panel it revealed.

**Root cause**: `role="tablist"`/`role="tab"` were added to two styled buttons for the announcement they produce, without the rest of the pattern; the panels were already being shown and hidden by a `.step.active` display rule that predates the roles.

**Remediation**: Add `aria-controls="step-register"` / `aria-controls="step-login"` to the two buttons, and `role="tabpanel" aria-labelledby="tab-register"` / `aria-labelledby="tab-login"` plus `tabindex="0"` to the two `.step` divs. Optionally add a roving `tabindex` and Left/Right handling in `switchTab`. The forgot-password step (`#step-forgot`) is not a tab and should stay outside the tablist, which it already is.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. LOW is right-sized: the panels are visible and their fields are individually labelled (every input at :711-729 has a matching <label for>), so the missing relationship costs orientation, not access.

- sev→LOW — Stands, LOW. Mitigation the finder got right by implication and worth stating explicitly for whoever fixes it: app/public/index.html:37-38 sets `.step { display: none }` / `.step.active { display: flex }`, so the inactive panel is genuinely removed from the a11y tree and the tab order — the defect is the missing tab/panel relationship and the missing arrow-key/roving-tabindex behaviour, not hidden focusable content. Both fields sets are still reachable by ordinary Tab, so no function is lost.

**Evidence**:

```
app/public/index.html:706-714 — a tablist whose tabs point at nothing, over panels that claim no role:
```html
    <div class="tab-row" role="tablist">
      <button class="tab-btn active" id="tab-register" role="tab" aria-selected="true" data-act="switchTab" data-arg="register" data-i18n="auth.tab_create">Create account</button>
      <button class="tab-btn" id="tab-login" role="tab" aria-selected="false" data-act="switchTab" data-arg="login" data-i18n="auth.tab_login">Log in</button>
    </div>
    <div id="step-register" class="step active">
```
and app/public/index.html:1242-1251 — the switch toggles `aria-selected` and a display class and nothing else:
```js
function switchTab(t){
  const isReg = t==='register';
  document.getElementById('tab-register').classList.toggle('active',isReg);
  document.getElementById('tab-register').setAttribute('aria-selected',isReg);
  document.getElementById('tab-login').classList.toggle('active',!isReg);
  document.getElementById('tab-login').setAttribute('aria-selected',!isReg);
  document.getElementById('step-register').classList.toggle('active',isReg);
  document.getElementById('step-login').classList.toggle('active',!isReg);
  document.getElementById('step-forgot').classList.remove('active');
}
```
`grep -rn "aria-controls\|role=\"tabpanel\"" app/public/` returns nothing across the whole directory.
```

## B6-11 [LOW] Equity curve is canvas-only: its 'Waiting for data…' state and the whole series exist only as pixels

- **Dimension**: a11y · **Fix class**: REVIEW_REQUIRED · **File**: `bot/web/dashboard.html:457, 566-583`
- **Standard**: WCAG 2.2 Level A: 1.1.1 Non-text Content. EN 301 549 §9.1.1.1.

**Observed**: Both the loading state and the rendered series are pixel-only. The 'Waiting for data...' text also renders at #64748b on the panel — the same 3.83:1 pair flagged separately — so it is low-contrast for sighted users too.

**Root cause**: The chart was written as a direct 2D-context renderer with no DOM mirror; the placeholder text was the quickest way to fill the empty canvas.

**Remediation**: Put the placeholder in the DOM instead of the canvas (a `<p class="empty-msg">` sibling toggled alongside the draw, matching the pattern already used at bot/web/dashboard.html:469 and :480), and give the canvas `role="img"` with an `aria-label` refreshed in drawChart() from the values it already has — e.g. "Equity curve, N points, from X to Y, latest Z". `#chartPoints` already tracks the count.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. LOW is correct — same private operator-dashboard population as findings 2 and 3, and the same series is available numerically from /api/state.

- sev→LOW — Accurate; LOW is right. Two notes for the fix: the '#64748b' here is a hard-coded literal, not var(--text-dim), so raising the token as finding 2 proposes will NOT fix this string — it needs its own edit. And the aria-label refresh must be honest about the empty case: 'Equity curve, no data yet' rather than an invented zero, per the repo's own rule.

**Evidence**:

```
bot/web/dashboard.html:457 — an empty canvas with no role, no label and no fallback content:
```html
        <div class="chart-wrap"><canvas id="eqChart"></canvas></div>
```
and the state message is painted into it — bot/web/dashboard.html:576-583:
```js
  ctx.clearRect(0, 0, W, H);
  if (equityHistory.length < 2) {
    ctx.fillStyle = '#64748b';
    ctx.font = '13px Rajdhani';
    ctx.textAlign = 'center';
    ctx.fillText('Waiting for data...', W/2, H/2);
    return;
  }
```
The panel's only other content is its span title (`<span class="panel-title">Equity Curve</span>`, bot/web/dashboard.html:453) and a point count (`<span ... id="chartPoints">0 pts</span>`, :454).
```

## B6-12 [HIGH] Drawdown early-warning tier alerts (50/75/85% of the circuit-breaker limit) can never fire — the probe names a field RiskEngine does not define

- **Dimension**: reachability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/proactive_monitor.py:1121`
- **Standard**: CLAUDE.md: 'A module nothing calls is indistinguishable from one that does not work' (here one granularity in: a method that IS called but whose probe can never resolve); and 'Ask which OTHER surface makes the same claim — before calling the fix done.'

**Observed**: `dd` is always None because RiskEngine has no `current_drawdown_pct`, so the method returns `[]` on the very first branch. The 50/75/85% early-warning tiers have never fired and cannot fire at any drawdown. Verified at runtime against a real PortfolioTracker + real RiskEngine (output above).

**Root cause**: An attribute probe pointing at the wrong object: the drawdown reading lives on the portfolio snapshot (`portfolio.snapshot().current_drawdown_pct`) or on the risk engine's own reporter `RiskEngine.drawdown_status()['drawdown_pct']` (bot/risk/risk_engine.py:3709-3739, which additionally distinguishes the ENFORCED live high-water mark from the paper number), not as a bare attribute on RiskEngine. The identical mistake was already found and fixed in the SIBLING method 75 lines above in the same file — bot/core/proactive_monitor.py:1044-1047 carries the comment: '# Gather live context for the alert. Read the REAL trip cause and the live accumulators — the old code read non-existent attrs (risk.current_drawdown_pct / risk.daily_pnl) and the empty PAPER portfolios'. The fix was applied to `_check_circuit_breaker` and not to `_check_drawdown_tiers`, which is exactly the 'ask which OTHER surface makes the same claim' rule in CLAUDE.md going unapplied.

**Remediation**: Read the enforced drawdown from the risk engine's own reporter rather than a non-existent attribute, and keep the three-valued shape: `st = self.engine.risk.drawdown_status(); dd = st.get('drawdown_pct') if st else None`. `drawdown_status()` is documented 'best-effort; returns empty on any error', so an empty dict must stay `dd = None` (silent) rather than becoming 0.0 (a confident 'no drawdown'). Then fix tests/test_proactive_alerts.py:19-27, whose `_engine()` fake constructs `types.SimpleNamespace(current_drawdown_pct=...)` — a shape the real RiskEngine never has, which is why four green tests said nothing about production.

**Verifier corrections** (these override the finder):

- sev→MEDIUM — Severity trimmed from HIGH to MEDIUM: this is a lost EARLY-WARNING tier, not a lost control. The circuit breaker itself still trips (its own check reads the real `circuit_trip_cause` / `last_known_daily_loss_pct`, both of which do exist on RiskEngine), so no money-moving gate is disabled — the operator merely never gets the 50/75/85% heads-up. Everything else in the finding is accurate as written.

- sev→MEDIUM — Severity lowered from HIGH: nothing false is printed and the enforcing path is unaffected — the drawdown circuit breaker itself gates on the live high-water mark inside _evaluate_locked and _check_circuit_breaker still fires on the trip (it reads circuit_breaker_active / circuit_trip_cause, both of which exist). What is lost is an advisory pre-warning, not a control. The proposed fix (drawdown_status()['drawdown_pct'], staying None on the empty dict) is correct and matches risk_engine.py:3709-3745, which also carries drawdown_source.

**Evidence**:

```
bot/core/proactive_monitor.py:1119-1125
        alerts: list[Alert] = []
        try:
            dd = getattr(self.engine.risk, "current_drawdown_pct", None)
            limit = float(getattr(CONFIG.risk, "max_drawdown_pct", 0) or 0)
            if dd is None or limit <= 0:
                return alerts
            frac = float(dd) / limit

The docstring directly above (bot/core/proactive_monitor.py:1114-1117) states the intent:
        """Early-warning alerts as drawdown approaches the circuit-breaker limit.

        Fires once at 50%, 75%, 85% of MAX_DRAWDOWN_PCT so the operator can act
        BEFORE the breaker halts trading. ..."""

`current_drawdown_pct` is a field of PortfolioState (bot/utils/models.py:283), NOT of RiskEngine. Every other reader in the tree reads it off the portfolio SNAPSHOT — bot/risk/risk_engine.py:1067, :1528, :3716, :3773 all use `getattr(state, "current_drawdown_pct", state.max_drawdown_pct)`. bot/core/proactive_monitor.py:1121 is the only place it is read off `risk`.
```

## B6-13 [MEDIUM] Every losing journal entry is stamped with a fabricated lesson ('Low confidence trade lost') derived from a confidence field nothing ever records

- **Dimension**: reachability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/engine.py:7170`
- **Standard**: CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement'; 'A heuristic is never a verdict'; and the shape table entry '`getattr(o, "pnl", 0)` — absent field is zero'.

**Observed**: `confidence` is 0.0 for every journal entry (the paper path passes `getattr(c, '_confidence', 0)`, which is always 0; the live path at bot/core/engine.py:1064-1077 omits the argument entirely and takes the dataclass default `confidence: float = 0.0` from bot/core/trade_journal.py:51). 0.0 < 0.60, so EVERY losing trade gets 'Low confidence trade lost — stick to high-conf setups'. Because get_weekly_review tallies lessons by frequency (bot/core/trade_journal.py:183-190) and /journal prints the top 3, this fabricated verdict becomes the #1 'Recurring Lesson' in any week that contains losses.

**Root cause**: An attribute probe naming a field that does not exist on the target class, with a falsy numeric default. `.get(k, 0)`-shaped defaulting on a field documented as a measurement is the exact shape CLAUDE.md tabulates ('`.get("pnl", 0)` · `getattr(o, "pnl", 0)` — absent field is zero'). The idea's confidence IS available at close time (TradeIdea.confidence is used all over the risk engine) but was never carried onto TradeExecution.

**Remediation**: Make JournalEntry.confidence three-valued: change `confidence: float = 0.0` to `Optional[float] = None` (bot/core/trade_journal.py:51) and `confidence: float = 0.0` in record_trade's signature (bot/core/trade_journal.py:88) to `Optional[float] = None`, then guard `_generate_lessons` with `if confidence is not None and pnl < 0 and confidence < 0.60:`. Separately, either carry the entry confidence through to TradeExecution so the field can be real, or drop the `getattr(c, '_confidence', 0)` argument so the absence is explicit at the call site rather than disguised as a reading.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — One reachability detail is wrong and should not be carried into a fix: /journal is NOT held by trader/paper/viewer. `_cmd_journal` opens with `if not self._is_admin(update): return` (bot/skills/telegram_handler.py:10638-10639), so the fabricated 'Recurring Lesson' is shown to admins only. The defect and its severity are unchanged; only the audience is narrower than claimed.

- sev→LOW — Two corrections. (a) The reachability claim about roles is wrong: _cmd_journal is admin-only — telegram_handler.py:10638-10640 opens with `if not self._is_admin(update): return`, so trader/paper/viewer never see this card regardless of the registration at :917. (b) Severity lowered to LOW: the output is an advisory prose line on an admin-only weekly review; it misattributes a real loss to a confidence level nobody recorded, but no number, gate or sizing decision is derived from it. The fix (Optional[float] = None plus `if confidence is not None and ...`) is right and cheap.

**Evidence**:

```
bot/core/engine.py:7168-7171 (inside _check_paper_positions, which starts at bot/core/engine.py:6975)
                        take_profit=c.take_profit,
                        pnl=c.pnl,
                        confidence=getattr(c, '_confidence', 0),
                        signals_used=getattr(c, '_signals_used', []),

`c` is a TradeExecution (bot/utils/models.py:198, a pydantic BaseModel with no `_confidence` field). A whole-word grep over the whole tree finds exactly ONE occurrence of `_confidence`: this read. Nothing writes it.

The consumer, bot/core/trade_journal.py:232-236:
        # Confidence analysis
        if pnl < 0 and confidence < 0.60:
            lessons.append("Low confidence trade lost — stick to high-conf setups")
        if pnl > 0 and confidence >= 0.80:
            lessons.append("High confidence = high win rate confirmed")
```

## B6-14 [MEDIUM] /attribution can never render data — `_signals_used` is read in two places and written in none, so signal attribution is structurally empty forever

- **Dimension**: reachability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/metrics.py:170`
- **Standard**: CLAUDE.md: 'A module nothing calls is indistinguishable from one that does not work' — and the corollary that a passing/green surface says nothing about whether the producer exists; plus 'absent is never a measurement' for the message that implies data is merely pending.

**Observed**: `compute_attribution` returns `{}` for any input, because the `continue` on line 172 fires for every trade. `/attribution` therefore always takes the empty branch at bot/skills/telegram_handler.py:5917-5922 and prints '⚠️ No signal attribution data yet. Need closed trades with signal tracking.' — wording that reads as a not-yet state that more trading will resolve, when in fact no amount of trading can ever populate it. The same root cause also makes `signals_used=[]` on every journal entry (bot/core/engine.py:7171) and `signals_attribution={}` on every MetricsEngine snapshot (bot/core/metrics.py:152).

**Root cause**: A feature whose producer was never wired: the consumer reads a private attribute `_signals_used` off TradeExecution that no producer ever writes. This is the CLAUDE.md 'module nothing calls' failure one granularity in — the code IS reached, so no reachability ratchet can see it, and its own error message disguises the permanent emptiness as a temporary one.

**Remediation**: Decide the product question first: either (a) carry the analyzer's contributing signals onto TradeExecution as a real declared field (e.g. `signals_used: list[str] = Field(default_factory=list)` in bot/utils/models.py) and populate it at open/close time, then read `trade.signals_used`; or (b) if the signal is not wanted, stop advertising it — remove the /attribution registration (bot/skills/telegram_handler.py:1011) and its catalogue entry (bot/skills/command_catalog.py:172). Do not leave the current shape, where an empty result is reported as 'not yet'. Until a producer exists, the message should say the feature is not recording rather than that data is pending.

**Verifier corrections** (these override the finder):

- sev→LOW — Severity trimmed MEDIUM -> LOW. Nothing false is rendered as a measurement: the surface prints an explicit empty-state, admin-only, and no number anywhere is fabricated from the absence (`signals_attribution={}` is an empty dict, not a zero). The real cost is an advertised-but-never-implemented analytic whose empty-state wording implies 'pending' rather than 'not recording' — worth fixing, but below the money-moving bar. Also correct the catalogue line number to bot/skills/command_catalog.py:169.

- sev→LOW — Severity lowered to LOW: the command is admin-gated inline (telegram_handler.py:5908 `if not self._is_admin(update): return`), it displays nothing rather than displaying something false, and no risk or sizing path consumes signals_attribution (grep shows the field is read nowhere outside the model). The honest complaint that survives is the wording — 'not yet' implies more trading will populate it — which is a message fix, not a subsystem rewrite. Do not remove the registration without a product decision; that would churn tests/unreachable_skills_baseline.txt territory.

**Evidence**:

```
bot/core/metrics.py:168-173
        for trade in closed:
            # Get signals_used from the trade's metadata
            signals = getattr(trade, '_signals_used', None)
            if signals is None:
                # Try to get from trade idea linkage
                continue

A whole-word grep over the tree for `_signals_used` returns exactly two lines, both READS:
  ./bot/core/metrics.py:170:            signals = getattr(trade, '_signals_used', None)
  ./bot/core/engine.py:7171:                        signals_used=getattr(c, '_signals_used', []),

TradeExecution (bot/utils/models.py:198-234) declares no such field.
```

## B6-15 [LOW] The bot dashboard's /api/signals always returns an empty signals list — SignalTracker lives on TelegramHandler, not on the engine

- **Dimension**: reachability · **Fix class**: REVIEW_REQUIRED · **File**: `bot/web/dashboard_server.py:174`
- **Standard**: CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement' — the guard/omit table requires that a composite view which omits a dead source still not present the omission as a measured negative.

**Observed**: `"signals": []` on every request, always, regardless of how many signals the bot has actually tracked.

**Root cause**: An attribute probe against the wrong object — the same shape as the macro_skills probes CLAUDE.md records ('all seven of that module's attribute probes named fields that never existed'). The failure is silent because `getattr(..., None)` plus `if tracker:` degrades to the omit path with no marker distinguishing 'no signals' from 'nobody looked'.

**Remediation**: Decide where the tracker belongs. If the engine should own it, construct it there and have TelegramHandler read `self.engine.signal_tracker` (one construction, one owner). If it genuinely belongs to the Telegram transport, this endpoint cannot serve it: return a distinguishable shape (e.g. `"signals": null` with a reason key) rather than `[]`, so a consumer cannot read 'nobody looked' as 'nothing tracked'.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The reachability paragraph is imprecise: there IS an in-repo consumer of the endpoint — bot/web/dashboard.html:813 fetches `API + '/api/signals'` on a 3-second poll. It happens to use only `sig.trades` (`updateTrades(sig.trades)` at dashboard.html:827) and never reads `sig.signals`, so no in-repo surface renders the always-empty list. Conclusion and LOW severity unaffected, but 'no in-repo consumer of this endpoint' should read 'no in-repo consumer of this FIELD'.

- sev→LOW — One refinement that further caps impact: the repo's own consumer does exist but ignores the field — bot/web/dashboard.html:813 fetches `/api/signals` and line 827 uses only `sig.trades` (`updateTrades(sig.trades)`); nothing renders `sig.signals`. So no in-repo surface makes a false claim from it; the defect is a dead probe on a DASHBOARD_TOKEN-gated JSON payload. LOW is right, arguably INFORMATIONAL.

**Evidence**:

```
bot/web/dashboard_server.py:170-180
async def handle_signals(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    signals = []
    try:
        tracker = getattr(engine, "signal_tracker", None)
        if tracker:
            all_stats = tracker.get_all_pair_stats()
            for symbol, stats in all_stats.items():
                signals.append({"symbol": symbol, **stats})
    except Exception:
        pass

The only construction of a SignalTracker in the tree is on the Telegram handler, not the engine:
  bot/skills/telegram_handler.py:845:        self.signal_tracker = SignalTracker()
A whole-tree grep for `signal_tracker` outside bot/core/signal_tracker.py returns only telegram_handler.py:603 (import), :845 (construction), :12865 (use) and dashboard_server.py:174 (this probe).
```

## B6-16 [LOW] The bot dashboard's /api/state reports a hardcoded scan_interval of 60 — the engine's field is _current_scan_interval

- **Dimension**: reachability · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard_server.py:94`
- **Standard**: CLAUDE.md: 'absent is never a measurement' — a default that looks like a plausible reading is indistinguishable from one, which is why the RC-AUD-016 comment three lines above exists.

**Observed**: `"scan_interval": 60` on every response, forever, whatever the engine is doing. This is the same defect class the block's own neighbouring comment records for a different field — bot/web/dashboard_server.py:85-87: '# RC-AUD-016: report the REAL trading mode, not a hardcoded True. # A hardcoded "simulation_mode": True made the dashboard show paper mode # even while trading live with real capital.'

**Root cause**: A field-name mismatch in an attribute probe, with a plausible-looking numeric default that hides the miss. The neighbouring `pending_ideas` probe on the same dict resolves correctly (RuneClawEngine defines a `pending_ideas` property at bot/core/engine.py:7244), which is what makes the one bad entry easy to miss.

**Remediation**: Read the real field: `"scan_interval": getattr(engine, "_current_scan_interval", None)`. Keep the fallback as None rather than 60 so an absent reading is distinguishable from a 60-second one, matching how the Telegram surface at telegram_handler.py:11405 treats it.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — Confirmed additionally that no in-repo surface renders the field: `updateEngine` (bot/web/dashboard.html:719) uses only `eng.state`, and grep for scan_interval in bot/web/dashboard.html returns nothing. LOW is right.

- sev→LOW — Confirmed no in-repo consumer: bot/web/dashboard.html's updateEngine (line 719 onward) renders state, tiers and cost, and never reads eng.scan_interval. So this is a stale field on a token-gated operator API, not something an operator currently reads — LOW stands, and the fix (read `_current_scan_interval`, default None not 60) is a one-liner.

**Evidence**:

```
bot/web/dashboard_server.py:92-96
        data["engine"] = {
            "state": state_name,
            "scan_interval": getattr(engine, "_scan_interval", 60),
            "pending_ideas": len(getattr(engine, "pending_ideas", [])),
            "simulation_mode": _sim_mode,

The engine's field is named differently — bot/core/engine.py:682:
        self._current_scan_interval: float = CONFIG.scan_interval_seconds

and the Telegram surface reads the correct one — bot/skills/telegram_handler.py:11405:
            _interval = float(getattr(self.engine, "_current_scan_interval", 0.0)
```

## B6-17 [MEDIUM] scripts/e2e_pipeline.py PHASE 5 crashes on AttributeError whenever a trade actually executes — PortfolioTracker has no get_state()

- **Dimension**: reachability · **Fix class**: SAFE_AUTO_FIX · **File**: `scripts/e2e_pipeline.py:276`
- **Standard**: CLAUDE.md 'Writing tests that scan source' / reachability: a code path that runs only on the success case is exactly the one a green run never exercises. Also the repo's own preflight ethos — a validation script that cannot complete its own success path is reporting a subset as the whole.

**Observed**: `engine.portfolio.get_state()` raises AttributeError. The block is guarded by no try/except — the nearest handlers in this file are at lines 107, 157, 175, 218 and 245, all in earlier phases — so the exception propagates out of `main()` (scripts/e2e_pipeline.py:43) and aborts the pipeline before PHASE 6. Had get_state existed, the very next two lines would still have failed on `state.unrealized_pnl` and `state.exposure_pct`.

**Root cause**: Three stale API names in a code path guarded by `if executed:` — it only runs when the pipeline succeeds in executing a trade, so the ordinary dry run (nothing executed) skips it and the breakage is invisible. Method-renaming drift on a caller no test covers; a dead branch that is dead only on the success path.

**Remediation**: `state = engine.portfolio.snapshot()`, then `state.portfolio_exposure_pct` for exposure. `unrealized_pnl` has no equivalent on PortfolioState (the note at bot/utils/models.py:284 marks portfolio_exposure_pct itself as 'Reserved — not currently populated by _snapshot_locked()'), so either compute it locally from the marked positions or drop the line rather than print a field that does not exist.

**Verifier corrections** (these override the finder):

- sev→LOW — Severity trimmed MEDIUM -> LOW. This is a standalone operator/validation script, not in CI, not on any trading path, and it fails LOUDLY with an AttributeError rather than printing a wrong number — the opposite of the silent-falsehood class this repo grades as severe. The fix note is right except that `state.open_positions` is fine as-is; only `get_state`, `unrealized_pnl` and `exposure_pct` need changing.

- sev→LOW — Severity lowered from MEDIUM: this is a standalone diagnostic script, not imported by bot/ or scripts/preflight.py and not run by CI, and the failure is a loud AttributeError traceback on an operator's terminal — it cannot mislead and cannot touch live money (the script is paper-only, 'Mode: PAPER SIMULATION', e2e_pipeline.py:45). Minor factual correction to the finding: execution happens in PHASE 3 (scripts/e2e_pipeline.py:227-247), not PHASE 4 — PHASE 4 is the portfolio card.

**Evidence**:

```
scripts/e2e_pipeline.py:275-280
        engine.portfolio.mark_to_market(prices)
        state = engine.portfolio.get_state()
        print(f"  Equity:       {usd(state.equity_usd)}")
        print(f"  Unrealized:   {usd(state.unrealized_pnl)}")
        print(f"  Positions:    {state.open_positions}")
        print(f"  Exposure:     {state.exposure_pct:.1f}%")

PortfolioTracker (bot/risk/portfolio.py) exposes `snapshot()` at line 309 and `_snapshot_locked()` at line 401; it has no `get_state`. `grep -rn "def get_state" bot/ --include=*.py` returns nothing at all.
```

## B6-18 [INFORMATIONAL] CLAUDE.md states 34 ambiguous method names; the baseline and the sweep both say 31, and no test pins the CLAUDE.md number

- **Dimension**: reachability · **Fix class**: SAFE_AUTO_FIX · **File**: `CLAUDE.md:465`
- **Standard**: CLAUDE.md: 'a gate whose coverage is overstated is the failure this file exists to prevent' — the sentence containing the stale number.

**Observed**: CLAUDE.md says 34. The other reachability counts in CLAUDE.md are accurate today and are pinned: the modules baseline holds 2 entries (bot/core/swarm.py, bot/core/presale_claims.py) matching the '**2** modules today' claim, and the skills baseline holds 7 entries against 30 registered skills — I verified the registry returns exactly 30 skill names — matching the '**7** of 30 registered skills' claim. All 24 reachability-ratchet tests plus the 42 dead-public-API / macro-card / roadmap tests pass on this commit, so every baseline file is accurate; only this one prose number has drifted.

**Root cause**: The paragraph was written when the number was 34 and not updated when three modules (alert_manager, performance_tracker, feedback_loop) were deleted — the baseline header itself records the resulting drop and was updated, CLAUDE.md was not. It is the exact failure mode CLAUDE.md names in the adjacent test docstring: 'A number in prose is the part that rots first.'

**Remediation**: Change '34' to '31' at CLAUDE.md:465, and extend tests/test_claude_md_accuracy.py with a test that re-derives the count from `ambiguous_method_names()` and asserts CLAUDE.md quotes it — the same shape as test_claude_md_states_the_real_count in tests/test_no_new_unreachable_modules.py, so this cannot rot again.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. INFORMATIONAL is the right grade and the proposed fix (change 34 to 31 and pin it from `ambiguous_method_names()`) is exactly the shape the repo already uses in tests/test_no_new_unreachable_modules.py.

- sev→INFORMATIONAL — Stands as written. One nuance for whoever fixes it: the baseline header already narrates 34 -> 33 -> 31, so the CLAUDE.md sentence should be edited to 31 rather than rewritten, and the proposed test should derive the number from ambiguous_method_names() the same way test_no_new_unreachable_functions.py:680 derives it from the header, so the two cannot diverge again.

**Evidence**:

```
CLAUDE.md:463-467
> A second pass now attributes `<recv>.<name>()` by resolving the receiver
> through `self.x = Foo()` and `x = Foo()`. **Sound, not complete**: one
> unresolvable receiver makes the whole name ambiguous, and the 34 names that
> stay ambiguous are stated in the baseline and pinned by a test, because a
> gate whose coverage is overstated is the failure this file exists to prevent.

tests/unreachable_methods_baseline.txt:33
# **31** method names remain ambiguous and are not checked by anything — the
```

## B6-19 [HIGH] SECURITY.md, both READMEs, the published GitBook root and agent_card.json all promise unconditional human confirmation, which auto_confirm_live_enabled=True contradicts by default

- **Dimension**: docs-consistency · **Fix class**: REVIEW_REQUIRED · **File**: `SECURITY.md:29`
- **Standard**: CLAUDE.md: 'Ask which OTHER surface makes the same claim — before calling the fix done.' / 'A heuristic is never a verdict.' Also the repo's own stated rule in tests/test_mcp_doc_matches_the_code.py: overstating a safety property 'is the direction this repo is organised around catching.'

**Observed**: Six further surfaces still carry the removed claim: SECURITY.md:29; README.md:62 ('receive explicit human confirmation before execution'), :66 ('The bot suggests. The human decides.'), :655 ('No trade executes without explicit confirmation.'); README.zh-TW.md (mirror); docs/gitbook/README.md:44 ('**Human-in-the-loop.** No trade executes without explicit confirmation via Telegram inline keyboard.') — which is the root page of the site the README's 'Full Documentation' badge links to; and agent_card.json:36 ("requires_confirmation": true) plus :44 ("human_in_the_loop": true).

**Root cause**: The fix for this exact claim was applied surface-by-surface without the CLAUDE.md corollary 'Ask which OTHER surface makes the same claim'. site/src/facts.ts:61-63 literally records the unfinished work: 'README.md says "explicit human confirmation before execution" in three places and is wrong in all three.' The only automated guard, tests/test_mcp_doc_matches_the_code.py::test_the_doc_does_not_promise_human_confirmation, is hardcoded to one file (docs/gitbook/mcp-integration.md), so nothing catches the rest.

**Remediation**: Either (a) restate the claim accurately on all six surfaces — e.g. 'trades require human confirmation unless the operator enables auto-confirm (AUTO_CONFIRM_LIVE_ENABLED, default ON in code)' — and set agent_card.json's requires_confirmation/human_in_the_loop to reflect the shipped default, or (b) flip bot/config.py:2313/2317 to the fail-closed values the docs already claim (1.0 / False), which is the smaller change and would make every surface true at once. Then generalise the existing guard: parametrise test_the_doc_does_not_promise_human_confirmation over SECURITY.md, README.md, README.zh-TW.md, docs/gitbook/README.md and agent_card.json instead of a single path.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — One line-number nit: the GitBook root sentence is docs/gitbook/README.md:45, not :44 (:44 is the '20 pre-trade checks' line). Everything else is verbatim at the cited lines.

- sev→UNCHANGED — None material. One nit: the finder cites README.md:62/:66/:655 — line 655 is the '- **Human-in-the-loop.** No trade executes without explicit confirmation.' bullet (the finder wrote :655 in one place and :653 in another for a neighbouring line); README.md:711 is a fourth English instance the finder did not list ('All trades are executed via the Telegram bot interface with human confirmation'). Both strengthen rather than weaken the finding.

**Evidence**:

```
SECURITY.md:29
  - **Human-in-the-Loop** — all trade executions require explicit human confirmation; the AI agent cannot autonomously place orders.

bot/config.py:2313-2317
    auto_confirm_threshold: float = _env_float("AUTO_CONFIRM_THRESHOLD", 0.85)
    # Allow auto-confirm to place LIVE (real-money) orders with no human press.
    # OPERATOR-ACTIVATED default ON. Set AUTO_CONFIRM_LIVE_ENABLED=0 to require a
    # human tap for every live trade (the fail-closed posture).
    auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", True)

bot/core/engine.py:6058-6065
            if human or CONFIG.auto_confirm_live_enabled:
                approval_token = self.compliance.issue_approval_token(
                    trade_id, self.compliance_profile.subject_id,
                )
                if not human:
                    # RC-AUD-018: unattended live execution explicitly opted in.
                    system_log.warning(
                        "AUTO-MINT APPROVAL TOKEN (RC-AUD-018): engine minted the "
```

## B6-20 [HIGH] The public /risk page claims categorically that no unevaluable check is treated as passed; risk_engine.py appends three 'skipped' outcomes to the passed list

- **Dimension**: docs-consistency · **Fix class**: REVIEW_REQUIRED · **File**: `site/src/routes/risk.tsx:82`
- **Standard**: CLAUDE.md: 'Unreadable is never zero, and absent is never a measurement' and 'A heuristic is never a verdict.' A skip reported to a caller as a pass is the tabulated shape 'unreadable rendered as a confident positive'.

**Observed**: The page makes an unqualified categorical claim. config/risk_manifest.yaml — the file SECURITY.md and README.md both call authoritative — contradicts it directly: check 17 LIQUIDITY has fail_behavior 'open' with the description 'This is the ONLY fail-open check: no data = pass', and checks 19 MTF_ALIGNMENT, 20 CONCENTRATION_PCA and 21 PORTFOLIO_VAR have fail_behavior 'skip', defined in the manifest header as 'check is gracefully skipped when data is unavailable (returns pass)'. README.md:653 states the accurate version ('a fail-open liquidity guard, advisory rules that skip without data'), so the site is inconsistent with the repo's own README.

**Root cause**: The page quotes the risk_engine.py module docstring ('if ANY check cannot be evaluated, the trade is REJECTED', risk_engine.py:203) as if it described every branch. It describes the except handlers only — every `except Exception` does append to `failed`. The explicit insufficient-data branches, which are the common case rather than the exceptional one, take the other path and were not covered by the sentence.

**Remediation**: Narrow the sentence on site/src/routes/risk.tsx to what is true — 'an exception inside a check rejects the trade' — and state separately that four checks are documented in config/risk_manifest.yaml as fail-open or graceful-skip. Rebuild website/ (the 'committed site is the built site' CI gate requires it). Then update site/test/site_honesty.test.js:316-319, which currently pins the over-broad wording in place by asserting /cannot be evaluated/i must remain on the page.

**Verifier corrections** (these override the finder):

- sev→MEDIUM — Anchor is site/src/routes/risk.tsx:72-78 (the prose), not :82 (the adjacent verbatim-contract block). Severity lowered from HIGH: this is marketing copy that overstates a property the repo's own README.md:653 and SECURITY.md already state accurately; no money moves differently because of it. Note the defect is broader than reported — risk_engine.py:2058 ('TAKER_3BAR: skipped (no order flow analyzer)') and :2096 ('BID_DOMINANCE: skipped (no fresh order flow data)') are two further skipped-counts-as-passed outcomes the finding did not list.

- sev→MEDIUM — Severity lowered from HIGH to MEDIUM: this is a false claim on a public marketing page, not a code path that moves money. It is a genuine violation of the repo's own honesty rule and worth fixing, but no order is placed or mis-sized because of it. Also note the fix must touch site/test/site_honesty.test.js:316-319 in the same commit or the rebuilt page fails that assertion — the finder correctly flagged this.

**Evidence**:

```
website/risk/index.html (built from site/src/routes/risk.tsx:82), visible copy:
  "Each check runs inside its own error boundary, and an exception does not skip that check: it records a failure, and any failure rejects the trade. There is no path where a check that could not be evaluated is treated as a check that passed."

bot/risk/risk_engine.py:1982
                passed.append("MACRO_EVENT: no calendar configured (skipped)")
bot/risk/risk_engine.py:1992
                passed.append("MTF_ALIGNMENT: aligned or skipped (no data)")
bot/risk/risk_engine.py:2019
                passed.append("PORTFOLIO_VAR: skipped (insufficient trade history)")
```

## B6-21 [MEDIUM] SECURITY.md's 21-check fail-behaviour breakdown does not match config/risk_manifest.yaml, the file it cites as authoritative

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `SECURITY.md:24`
- **Standard**: config/risk_manifest.yaml's own header: 'Single source of truth for all risk parameters. The risk engine loads from this file; docs render from it. "The documentation cannot drift from the code because they read the same file."'

**Observed**: The manifest is 17 closed / 1 open / 3 skip. #18 MACRO_EVENT is fail_behavior 'closed', not a skip. Three other surfaces state a fourth and fifth number for the same quantity: docs/gitbook/README.md:44 says 'all 20 pre-trade checks (fail-closed + 1 fail-open for liquidity only)'; docs/gitbook/risk-framework.md:164 says 'Of the 20 pre-trade checks, **19 are fail-closed** … and **1 is fail-open**'; and bot/risk/risk_engine.py:980 says 'Run all 23 pre-trade checks (16 in-engine + #17 liquidity + #18 macro + #19 MTF + #20 PCA + #21 VaR + #22 taker 3-bar + #23 bid dominance)'.

**Root cause**: A hand-maintained count and breakdown duplicated across five surfaces, none of which is derived from config/risk_manifest.yaml at build or test time — the exact drift mode tests/test_mcp_doc_matches_the_code.py documents ('a number typed into a document is a second, staler copy of something already knowable') and that the marketing site deliberately solved by removing the count entirely.

**Remediation**: Either derive the breakdown from the manifest at doc-build time, or extend tests/test_manifest_and_whynot.py (which already loads and validates the manifest) with an assertion that any fail_behavior counts stated in SECURITY.md and docs/gitbook/*.md equal the manifest's actual Counter. The 20/19-1 statements in docs/gitbook/README.md:44 and docs/gitbook/risk-framework.md:164 are the most misleading and should be corrected first: 'and 1 is fail-open' asserts there are no graceful skips at all.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None on the facts. Worth adding that the manifest itself is incomplete, not merely disagreed-with: it holds exactly 21 `- id:` entries while risk_engine.py implements #22 TAKER_3BAR (:2048-2060) and #23 BID_DOMINANCE (:2084-2098), so the 'authoritative list' omits two live checks.

- sev→UNCHANGED — One caveat the finder did not state: risk_engine.py:1982 shows MACRO_EVENT *does* have a skip branch ('no calendar configured (skipped)' → passed), so on this one point SECURITY.md is arguably closer to the code than the manifest is, and the fix may belong in the manifest rather than in SECURITY.md. The docs/gitbook '20 checks / 19 fail-closed / 1 fail-open' statements are unambiguously wrong against both and should be corrected first, as the finder says.

**Evidence**:

```
SECURITY.md:24
  - **21-Check Risk Engine** — every order must pass all pre-trade validations before execution. Of these, 16 are strict fail-closed (any failure = rejection), 1 is fail-open (#17 LIQUIDITY: no order-book data = pass), and 4 gracefully skip when data is insufficient (#18 MACRO, #19 MTF, #20 PCA, #21 VaR). See `config/risk_manifest.yaml` for the authoritative list.

config/risk_manifest.yaml (check 18):
    name: MACRO_EVENT
    fail_behavior: closed
    notes: "v2 provider: BLOCK_NEW_ENTRIES = reject, REDUCE = pass with size multiplier. v1 calendar: EVENT_LOCKDOWN = reject, BLACKOUT = reject (fail-closed)."
```

## B6-22 [MEDIUM] SECURITY.md says credentials are never persisted beyond process memory; the secrets vault (default ON) writes them to data/ and the master key in cleartext

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `SECURITY.md:35`
- **Standard**: SECURITY.md's own purpose — it is the document a security reviewer and an operator use to reason about where secret material lives.

**Observed**: SECURITY.md still describes a pre-vault design. bot/core/exchange_credentials.py:115-117 itself spells out what the key file decrypts: 'data/exchange_creds.enc (every user's exchange key+secret+passphrase and agent private keys), data/secrets_vault.enc, and the llm_api_key column'. .env.example:23-29 also documents the per-user BYOK store as 'encrypted at rest', so the repo's own operator-facing config contradicts its security policy.

**Root cause**: The secrets vault and the per-user BYOK credential store were added after SECURITY.md was written, and the API Key Handling section was never revisited. Nothing tests SECURITY.md against the code.

**Remediation**: Rewrite SECURITY.md:35-37 to state: keys are read from .env or the process environment; when SECRETS_VAULT_ENABLED is on (default) the managed set in bot/core/secrets_vault.py:49-72 is mirrored encrypted to data/secrets_vault.enc; per-user BYOK keys live encrypted in data/exchange_creds.enc; and the Fernet master key is stored at data/.exchange_secret.key (0600) unless pinned via RUNECLAW_SECRETS_KEY. Add a note that data/ therefore needs the same protection as .env. Consider a small test asserting SECURITY.md mentions secrets_vault.enc while bot/core/secrets_vault.py exists.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None.

- sev→UNCHANGED — Precision: the persisted material is Fernet-encrypted, so 'writes them to data/ … in cleartext' in the title reads worse than the facts — the *master key* is cleartext, the secrets are not. The operator-facing consequence the finder states (data/ needs the same protection, backup and wipe discipline as .env) is exactly right and is the part SECURITY.md omits.

**Evidence**:

```
SECURITY.md:35-37
  - API keys and secrets are loaded exclusively from a `.env` file, which is **gitignored** by default.
  …
  - Credentials are passed to the Bitget SDK at runtime only and are not persisted beyond process memory.

bot/core/secrets_vault.py:50-53
_DEFAULT_MANAGED = (
    "BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE",
    "BITGET_API_PASSPHRASE",  # legacy passphrase spelling — preserve either name
    "TELEGRAM_BOT_TOKEN",

bot/core/exchange_credentials.py:108-113
    key: bytes = Fernet.generate_key()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(key)
    try:
        os.chmod(str(p), 0o600)
```

## B6-23 [MEDIUM] README's Live Trading Records cites three evidence files in logs/; one has no producer anywhere in the repo, one is written to data/, and logs/ is gitignored wholesale

- **Dimension**: docs-consistency · **Fix class**: REVIEW_REQUIRED · **File**: `README.md:721`
- **Standard**: CLAUDE.md: 'A principle is not searchable. The shapes it takes are' — and the shape here is a published performance record whose cited substantiation is absent. README.md:53's own disclaimer commits to not presenting unverifiable performance.

**Observed**: logs/ is gitignored in its entirety (.gitignore:139), so none of the three files can ever be in the repository. logs/live_trading_log.csv has no writer anywhere in bot/, app/, scripts/ or the root scripts — the only occurrences are the two READMEs, a .gitignore comment, and a test fixture (tests/test_deploy_sh_preserves_state.py:58) that fabricates one to check deploy.sh symlink preservation. closed_trades.json is written to $RUNECLAW_STATE_DIR/closed_trades.json, default data/, not logs/ (bot/core/live_executor.py:318-320, corroborated by bot/backtest/parity.py:30 DEFAULT_TRADES_FILE = str(state_path("data/closed_trades.json"))). Only logs/audit_chain.jsonl matches a real runtime path.

**Root cause**: The section documents an operator's local machine state as if it were repository content, and was not revisited when .gitignore:132-139 began ignoring /data and /logs (added because deploy.sh replaces them with symlinks). The CSV filename appears to describe an artifact that was produced manually or by tooling that no longer exists.

**Remediation**: Either commit a redacted, verifiable extract of the trade record (a CSV under a tracked path such as evidence/, which already exists and is tracked) and point the table at it, or delete the 'Files in logs/' table and label the figures as operator-reported and unverifiable from the repository. Correct closed_trades.json's path to data/closed_trades.json in either case. Removing live_trading_log.csv from the table costs nothing since nothing produces it.

**Verifier corrections** (these override the finder):

- sev→LOW — closed_trades.json is at bot/core/live_executor.py:317-319, not :318-320 (one line high). Severity lowered from MEDIUM: this is a README table describing operator-local artifacts; the concrete errors are a wrong directory for one file and a non-existent file for another. It changes no behaviour and misleads only a reader trying to audit the published track record.

- sev→UNCHANGED — The finding is slightly over-stated in framing: the README does not literally claim these files are in the repository, and an operator's own logs/ dir is a plausible reading. What survives regardless is that (a) closed_trades.json is written to data/, not logs/, so the path is wrong on the shipped code, and (b) live_trading_log.csv is produced by nothing in the tree, so a published track record of 38 trades / 55.3% / +$46.30 names an evidence artifact that no version of the bot writes.

**Evidence**:

```
README.md:713-727
**Trading Period:** June 17-19, 2026
**Total Closed Trades:** 38
**Win Rate:** 55.3% (21W / 17L)
**Total Realized PnL:** +$46.30

### Files in `logs/`
| `live_trading_log.csv` | Complete trade log with timestamp, pair, side, entry/exit price, size, PnL |
| `closed_trades.json` | Raw closed trade records from the bot's state file |
| `audit_chain.jsonl` | Immutable audit chain -- every trade decision logged with context |

.gitignore:139
/logs

bot/core/live_executor.py:318-320
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "closed_trades.json"
```

## B6-24 [MEDIUM] .env.example ships deprecated model IDs as active settings, and two hardcoded LLM fallback chains use models provider.py itself records as retired

- **Dimension**: docs-consistency · **Fix class**: REVIEW_REQUIRED · **File**: `.env.example:328`
- **Standard**: tests/test_model_catalog_2026.py's own stated rationale, and CLAUDE.md: 'Ask which OTHER surface makes the same claim — before calling the fix done.'

**Observed**: Three classes of stale id survive outside the guard's scope: (a) .env.example's ACTIVE tier settings (gemini-2.5-flash ×3, claude-sonnet-4-6 ×2), which is the file every operator copies; (b) the hardcoded fallback chains at bot/core/analyzer.py:4455-4456 (gemini-2.0-flash, llama-3.3-70b-versatile) and bot/skills/telegram_handler.py:2178 (gemini-2.0-flash) — bot/llm/provider.py:96-97 says 'Groq retired the llama-3.3/3.1-instant models (June 2026)' and :83-85 says the Gemini '2.5 line was superseded by the 3.x generation'; (c) README.md:90-97, which advertises 'Google Gemini 2.5 Flash -- default provider' and 'Groq -- llama-3.3-70b-versatile' and tells the reader to set LLM_MODEL=gemini-2.5-flash. .env.example:290 also sets LLM_MODEL=claude-sonnet-4-6, which is not in PROVIDER_CATALOG's Anthropic recommended_models (bot/llm/provider.py:63-64: claude-fable-5, claude-opus-4-8, claude-sonnet-5, claude-haiku-4-5-20251001).

**Root cause**: The 2026-07 model refresh updated the four routing tables and wrote a guard scoped to those tables. Model ids also live in three other places the guard cannot see: the shipped .env.example, two hardcoded fallback chains that bypass resolve_tier_config by design, and the README.

**Remediation**: Update .env.example:290,328,330,332,334 to current ids (claude-sonnet-5, gemini-3.5-flash), update README.md:90-97 and README.zh-TW.md:92,97, and replace the hardcoded literals at bot/core/analyzer.py:4455-4456 and bot/skills/telegram_handler.py:2178 with PROVIDER_CATALOG[provider]['default_model'] so a catalog bump carries them. Then widen test_no_deprecated_model_ids_in_routing to scan .env.example, README.md and the two fallback-chain literals — the test's comment already claims the property it does not yet check there.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — One softening: the guard's dead-list literally bans 'llama-3.3-70b-versatile', 'llama-3.1-8b-instant' and 'gemini-2.5'; 'gemini-2.0-flash' in the two fallback chains is an inference from provider.py's 2.5-superseded note rather than a listed dead id. The .env.example/README half needs no inference.

- sev→UNCHANGED — One overreach: provider.py records llama-3.3-70b-versatile as retired, but nowhere records gemini-2.0-flash as retired — it only says the 2.5 line was superseded. So of the two hardcoded literals, only the Groq one is provably a dead id; gemini-2.0-flash is merely two generations stale. The .env.example leg (gemini-2.5-flash and claude-sonnet-4-6 shipped as active tier settings, both explicitly out-of-catalog) is the strongest part and is fully confirmed.

**Evidence**:

```
.env.example:327-334 (all uncommented, i.e. active in a copied .env)
LLM_TIER_SCAN_PROVIDER=gemini
LLM_TIER_SCAN_MODEL=gemini-2.5-flash
LLM_TIER_THESIS_PROVIDER=anthropic
LLM_TIER_THESIS_MODEL=claude-sonnet-4-6
LLM_TIER_LEARNING_PROVIDER=gemini
LLM_TIER_LEARNING_MODEL=gemini-2.5-flash
LLM_TIER_CHAT_PROVIDER=gemini
LLM_TIER_CHAT_MODEL=gemini-2.5-flash

bot/core/analyzer.py:4453-4457
        fallback_chain = [
            (LLMProvider.ALIBABA, "ALIBABA_API_KEY", "qwen3.6-flash"),
            (LLMProvider.GEMINI, "GEMINI_API_KEY", "gemini-2.0-flash"),
            (LLMProvider.GROQ, "GROQ_API_KEY", "llama-3.3-70b-versatile"),
            (LLMProvider.DEEPSEEK, "DEEPSEEK_API_KEY", "deepseek-chat"),
        ]

tests/test_model_catalog_2026.py:66-70
    # Groq retired llama-3.3/3.1-instant (June 2026) and Gemini 2.5 was superseded
    # by the 3.x line — a deprecated id breaks live calls the moment it's retired.
    models = " ".join(_all_routing_models())
    for dead in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemini-2.5"):
        assert dead not in models, f"deprecated model id still routed: {dead}"
```

## B6-25 [MEDIUM] config/risk_manifest.yaml documents MIN_BOOK_DEPTH_USD as check #17's threshold; bot/config.py records it as a dead knob and says the manifest is stale

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `config/risk_manifest.yaml:232`
- **Standard**: config/risk_manifest.yaml header: 'Single source of truth for all risk parameters. The risk engine loads from this file; docs render from it.' And bot/config.py:548-549, which already states the defect.

**Observed**: The manifest names MIN_BOOK_DEPTH_USD twice: at check 17 (env_var: MIN_BOOK_DEPTH_USD, min_book_depth_usd: 5000.0) and in the defaults section (value: 2000.0, env_var: MIN_BOOK_DEPTH_USD) with an operational note — 'Lowered from $50K default so small-cap and micro-test trades can pass' — that describes a tuning decision made through a knob that controls nothing. The two manifest entries also disagree with each other (5000.0 vs 2000.0). bot/config.py:548-549 explicitly flags the manifest as stale; the manifest was never corrected. OF_MIN_DEPTH_USD, the live knob, is not in .env.example at all.

**Root cause**: The liquidity guard moved from risk_engine.py into the order-flow analyzer (the manifest itself notes 'Runs in engine.py, not in risk_engine.py'), taking its threshold with it. config.py's field was kept for backward compatibility and annotated; the manifest and .env.example were not updated.

**Remediation**: In config/risk_manifest.yaml, change check 17's env_var to OF_MIN_DEPTH_USD and align the threshold with bot/core/order_flow.py:113 (default 2000.0); either drop the min_book_depth_usd entry from the defaults section or mark it deprecated-and-inert. Document OF_MIN_DEPTH_USD in .env.example. Extend tests/test_manifest_and_whynot.py::test_manifest_env_vars_match_config (which already maps manifest default keys to config.py env vars) to fail when a manifest env_var names a config field no module reads.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None.

- sev→UNCHANGED — None. This is the strongest finding in the set: the manifest is the file SECURITY.md and README both call authoritative, it names a knob the code itself annotates as inert, and the working knob is undocumented — so an operator tuning liquidity depth gets silent no-ops.

**Evidence**:

```
config/risk_manifest.yaml (check 17, LIQUIDITY):
    threshold:
      min_book_depth_usd: 5000.0
      large_cap_floor_usd: 50000.0
    env_var: MIN_BOOK_DEPTH_USD

bot/config.py:545-550
    # DEAD KNOB (tuning audit) — kept only for backward env compatibility:
    # the liquidity guard actually reads OrderFlowConfig.min_top_depth_usd
    # (env OF_MIN_DEPTH_USD, bot/core/order_flow.py). Nothing reads this
    # field; setting MIN_BOOK_DEPTH_USD changes NOTHING. risk_manifest.yaml
    # documenting it as check #17's threshold is likewise stale.
    min_book_depth_usd: float = _env_float("MIN_BOOK_DEPTH_USD", 2_000.0)
```

## B6-26 [MEDIUM] .env.example and an engine.py comment both state auto-confirm defaults that bot/config.py contradicts

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `.env.example:141`
- **Standard**: CLAUDE.md's central rule applied to configuration: a document read before the code is the worst place for a stale claim, and .env.example:6-7 explicitly tells operators 'All settings have safe defaults. Only TELEGRAM_BOT_TOKEN is required.'

**Observed**: Three statements are wrong. .env.example:139 claims the default threshold is 1.0 (it is 0.85). .env.example:145 claims AUTO_CONFIRM_USE_CALIBRATED defaults OFF (it is True) — and this is the one where the code default actually governs, because the line is commented out. bot/core/engine.py:4327-4329 repeats both errors inside the function that performs auto-confirmation. config.py's own comments say the opposite ('OPERATOR-ACTIVATED default 0.85', 'OPERATOR-ACTIVATED default ON'), so the disagreement is visible within the repo.

**Root cause**: The defaults were flipped from fail-closed to operator-activated in bot/config.py and the accompanying prose was not updated. .env.example's active lines happen to set the safe values, which masks the error for anyone who copies the file and hides it from anyone who reads it to learn the default.

**Remediation**: Correct .env.example:139 to state the code default is 0.85 and that the shipped line overrides it to 1.0; correct :145 to '(default ON)'; correct bot/core/engine.py:4327-4329. Add an assertion to tests/test_audit_v5_fixes.py (which already pins the two code defaults at :37-38) that .env.example's prose and the engine comment agree with them — the values are already asserted, so the doc check is a two-line addition.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — Line numbers drift by 2 in .env.example: the 'Default 1.0 = DISABLED' claim is :137 and '(default OFF)' is :143. Note also that the threshold sentence is arguably describing the shipped line rather than the code default; the AUTO_CONFIRM_USE_CALIBRATED sentence and the engine.py:4328 comment are the two that cannot be read charitably.

- sev→UNCHANGED — Line numbers drift by ~2 from the finder's citations (the .env.example prose is at :136-138 and :143, the values at :141-142, the commented line at :148). docs/FLAG_ACTIVATION.md exists and .env.example:21 points at docs/LIVE_TRADING_ENABLEMENT.md, so the activation decision is documented somewhere — but neither corrects the three false statements at the point of use.

**Evidence**:

```
.env.example:137-149
# Signals at/above this blended confidence auto-execute without a human button
# press. Default 1.0 = DISABLED (everyone manual-confirm). For the admin
# "85% or higher" auto-trade policy, set 0.85 AND enable live auto-confirm.
AUTO_CONFIRM_THRESHOLD=1.0
AUTO_CONFIRM_LIVE_ENABLED=false
# Gate auto-confirm on CALIBRATED confidence (default OFF).
# AUTO_CONFIRM_USE_CALIBRATED=false

bot/config.py:2313-2326
    auto_confirm_threshold: float = _env_float("AUTO_CONFIRM_THRESHOLD", 0.85)
    auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", True)
    auto_confirm_use_calibrated: bool = _env_bool("AUTO_CONFIRM_USE_CALIBRATED", True)
```

## B6-27 [LOW] SECURITY.md lists /portfolio and /risk/status as unauthenticated read-only endpoints; both require the DASHBOARD_TOKEN bearer

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `SECURITY.md:25`
- **Standard**: SECURITY.md is the integration contract for the API bridge; an endpoint list in it is a schema claim.

**Observed**: The four state-changing endpoints listed are correct (/analyze api_bridge.py:617, /confirm :704, /portfolio/close :760, /risk/halt :1036 all carry the dependency). Two of the four endpoints listed as unauthenticated do carry it. Only /health (:412) and /scan (:534) are genuinely open.

**Root cause**: Authentication was tightened on the two portfolio/risk read endpoints without updating the security policy that enumerates them.

**Remediation**: Update SECURITY.md:25 to list only /health and /scan as unauthenticated, and note that with DASHBOARD_TOKEN unset the token-gated endpoints return 503 rather than 401. Optionally add a test that enumerates api_bridge.py's routes and their dependencies and diffs against the SECURITY.md lists, so the two cannot drift again.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — Minor: /scan is POST at :533 (the finding wrote :534, the signature line) and /portfolio/close/{symbol} is at :759, not :760. Also worth stating plainly — the doc errs in the SAFE direction: the endpoints are more protected than SECURITY.md claims, so no exposure follows from it. LOW is the right ceiling.

- sev→UNCHANGED — Worth noting the direction: the doc understates the actual protection, so nobody is exposed by it — the risk is an operator or integrator building against a stated-public endpoint and getting 401/503. LOW is the right severity. Minor nit: /scan is a POST (api_bridge.py:533), not a GET, so SECURITY.md's 'read-only endpoints' grouping is loose there too.

**Evidence**:

```
SECURITY.md:25
  - **Bearer Token Authentication** — state-changing API endpoints (`/confirm`, `/portfolio/close`, `/risk/halt`, `/analyze`) require a `DASHBOARD_TOKEN` bearer token. Read-only endpoints (`/health`, `/scan`, `/portfolio`, `/risk/status`) do not require authentication.

api_bridge.py:691-692
@app.get("/portfolio")
async def portfolio(_token: str = Depends(require_dashboard_token)):

api_bridge.py:804-805
@app.get("/risk/status")
async def risk_status(_token: str = Depends(require_dashboard_token)):
```

## B6-28 [LOW] README and agent_card.json present the multi-agent swarm as a shipped capability; bot/core/swarm.py has zero non-test importers and the roadmap marks the feature Planned

- **Dimension**: docs-consistency · **Fix class**: REVIEW_REQUIRED · **File**: `README.md:236`
- **Standard**: CLAUDE.md: 'A module nothing calls is indistinguishable from one that does not work.' The repo already applies this to its own baselines; the public-facing surfaces were not brought along.

**Observed**: bot/core/swarm.py is recorded in tests/unreachable_baseline.txt — the repo's own ratchet for 'Modules imported by tests and by nothing else' — with the note that it is '428 lines of in-process pub/sub scaffolding for the roadmap's "Multi-agent ensemble" row, which is 🔵 Planned. Deleting it would throw away design work for a feature that is scheduled; wiring it is that feature, not a cleanup.' docs/ROADMAP.md:63 confirms: '**Multi-agent ensemble** — specialist sub-agents … | Later | 🔵 | the strategy engine'. README.md:240 nonetheless says 'Ready for production deployment as separate Agent Hub agents' under a heading tagged (NEW), and agent_card.json advertises multi_agent_swarm to any agent that reads the card.

**Root cause**: The README section and the agent card were written when the module was authored and never reconciled with the roadmap status or the unreachable-module ratchet that later catalogued it.

**Remediation**: Move README.md:236-240 out of the feature list into the Limitations section (which at :751 already hedges 'swarm uses experimental in-process pub/sub (not a production MCP deployment)'), or mark the heading Planned to match docs/ROADMAP.md:63. Remove 'multi_agent_swarm' from agent_card.json:22 until something constructs a SwarmBus. Optionally extend tests/test_no_new_unreachable_modules.py to fail when a module in unreachable_baseline.txt is also named as a capability in agent_card.json.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — One mitigating fact the finding already half-acknowledges: README's own sentence calls the architecture 'experimental, in-process pub/sub', and README's Limitations section repeats the hedge, so the README is not uniformly claiming shipped status. The load-bearing errors are the 'Ready for production deployment' clause and the unhedged agent_card.json capability entry.

- sev→UNCHANGED — None. README.md:751 does hedge in the Limitations section ('swarm uses experimental in-process pub/sub'), which softens the README half slightly, but agent_card.json advertises the capability unqualified to any agent reading the card, and the feature-list heading still says '(NEW)' and 'Ready for production deployment'.

**Evidence**:

```
README.md:236-240
### Multi-Agent Swarm Protocol (NEW)
Composable agent collaboration via experimental, in-process pub/sub architecture. Five specialized agents:
Scanner (perceives market), Analyst (generates theses), Risk (gates every trade), Executor (manages positions),
Sentinel (monitors for black swans). Communication via SwarmBus pub/sub, with Sentinel broadcasting HALT
to all agents when severity >= 0.8. Ready for production deployment as separate Agent Hub agents.

agent_card.json:22
    "multi_agent_swarm"

tests/unreachable_baseline.txt
bot/core/swarm.py
```

## B6-29 [LOW] README badges and prose state stale counts: 28 red-team scenarios (30 run), 2644 test functions across 227 files (8489 across 734)

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `README.md:21`
- **Standard**: CLAUDE.md's pinned-count discipline: the gate count in CLAUDE.md is enforced by tests/test_claude_md_accuracy.py::test_the_gate_count_it_quotes_is_the_real_one precisely because 'a number in prose is the part that rots first'. No equivalent guard covers README.md.

**Observed**: Red team: 28 claimed in three places (badge alt text, README.md:206, README.md:209) versus 30 actually run and 30 named in the CI step title ('Red team — 30 adversarial scenarios against the live risk engine'). Test suite: 2644 functions / 227 files claimed in two places versus 8489 / 734. Security tests: 29 is correct. README.zh-TW.md:22-23 carries the same two stale badges.

**Root cause**: Hand-maintained counts in a document with no guard, exactly the failure mode tests/test_mcp_doc_matches_the_code.py describes ('a number typed into a document is a second, staler copy of something already knowable') and that the marketing site solved by deleting its counts.

**Remediation**: Either drop the numbers from the badges (the red-team badge already says 'framework included', which is the durable claim) or derive them: scripts/red_team.py already reports report.total_scenarios, so a test can assert README's figure equals it, mirroring the existing CLAUDE.md gate-count test.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The prose line numbers are wrong by six: the '28 scenarios across 10 categories' sentence is README.md:200 and 'runs 28 adversarial scenarios' is :203, not :206 and :209. Content is otherwise exact. My own count of test functions came to 8490 rather than the finder's 8489 — immaterial to the claim.

- sev→UNCHANGED — None. The direction of the test-count drift is worth noting for whoever fixes it: the real numbers are ~3x the advertised ones, so the badge understates rather than oversells — but it is still a number in prose with no guard, the exact class CLAUDE.md and tests/test_mcp_doc_matches_the_code.py both call out.

**Evidence**:

```
README.md:21-23
  <img src="https://img.shields.io/badge/tests-2644%20test%20functions%20%7C%20227%20files-brightgreen" alt="2644 Test Functions | 227 Files">
  <img src="https://img.shields.io/badge/security%20tests-29%20passing-blueviolet" alt="29 Security Tests">
  <img src="https://img.shields.io/badge/red%20team-28%20scenarios%20%7C%20framework%20included-critical" alt="Red Team 28 Scenarios | Framework Included">

README.md:622
|   |-- (2644 total test functions across 227 files)

README.md:206-209
An adversarial engine that attacks the risk engine with 28 scenarios across 10 categories:
… Red-team testing framework included -- runs 28 adversarial scenarios to verify risk gate behavior.
```

## B6-30 [LOW] README advertises Gemini 2.5 Flash as the default LLM provider; the code default is OpenAI, .env.example sets Anthropic, and the tier tables use Gemini 3.5 Flash and Grok

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `README.md:90`
- **Standard**: CLAUDE.md's requirement that a document read first must be true, since everything after it is interpreted through it.

**Observed**: Four different answers: README says Gemini 2.5 Flash; bot/config.py:1101 says openai; .env.example's own comment says gemini is DEFAULT while its active line sets anthropic; and the tier routing tables — which are what actually resolve per task — use Groq / Gemini 3.5 Flash / Grok. bot/llm/provider.py:83-85 additionally records that the Gemini 2.5 line 'was superseded by the 3.x generation'.

**Root cause**: 'Default provider' means three different things in this codebase (the LLMConfig fallback, the shipped .env.example value, and the tier routing tables) and the README picked a fourth that no longer matches any of them after the 2026-07 model refresh.

**Remediation**: Rewrite README.md:88-97 to describe the tier routing that actually applies (bot/llm/provider.py:246-268) rather than a single 'default provider', and correct the model ids. Fix the internal contradiction in .env.example between :265 and :287.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None.

- sev→UNCHANGED — None. Overlaps finding 5 on the model-id half; the distinct defect here is that four surfaces give four different answers to 'what runs by default', including a self-contradiction between .env.example:265 and :287.

**Evidence**:

```
README.md:90 and :97
- **Google Gemini 2.5 Flash** -- default provider, zero-cost reasoning with free-tier API key
> **Zero-cost setup:** Set `LLM_PROVIDER=gemini` and `LLM_MODEL=gemini-2.5-flash` with a free API key from [Google AI Studio](https://aistudio.google.com/apikey). No credit card required.

bot/config.py:1101
    provider: str = _env("LLM_PROVIDER", "openai")

.env.example:265,287
#   gemini      → Gemini 2.5 Flash               (free tier, reasoning)  ← DEFAULT
LLM_PROVIDER=anthropic
```

## B6-31 [LOW] CLAUDE.md's '47 of 532 test files' is stale by ~200 files and is one of the few counts it states that no test pins

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `CLAUDE.md:245`
- **Standard**: tests/test_claude_md_accuracy.py's own docstring: 'A document that is read FIRST is the worst place for a stale claim… So the checkable claims are pinned.'

**Observed**: 532 versus 734 actual test files. tests/test_claude_md_accuracy.py, which exists specifically to pin CLAUDE.md's checkable claims and which does pin the gate count, does not cover this one.

**Root cause**: A ratio typed into prose with no guard, in the one document the repo declares 'is read FIRST … the worst place for a stale claim'.

**Remediation**: Drop the numerator/denominator ('A minority of test files scan source, and most of them should') or add a bounded assertion to tests/test_claude_md_accuracy.py in the shape of the existing gate-count test.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The finding only challenges the denominator; I did not verify the numerator (47 source-scanning files) and neither did the finder, so no claim about 47 should be carried forward.

- sev→UNCHANGED — None, though the impact is the mildest in the set: the sentence's point ('do not convert wholesale') is unaffected by the denominator, and CLAUDE.md itself says a number typed into prose is the part that rots first. Dropping the ratio is the cheaper fix than pinning it.

**Evidence**:

```
CLAUDE.md:245
**Do not convert wholesale.** 47 of 532 test files scan source and most of
them should — `tests/test_trade_live_mode.py` says so in its own docstring:
```

## B6-32 [LOW] tests/unreachable_skills_baseline.txt's triage prose claims no @guard handler exists for /macro or /compliance; both exist

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `tests/unreachable_skills_baseline.txt:16`
- **Standard**: tests/unreachable_skills_baseline.txt's own framing — the file exists 'so the decision is visible rather than silent' — and CLAUDE.md: 'A note explaining why something is unreachable is itself a claim, and it ages against code that keeps moving. Re-read it before trusting it.'

**Observed**: The paragraph still asserts all five commands are dark on every transport. CLAUDE.md records that '/eventrisk and /compliance are wired', and I confirmed @guard handlers for /macro and /compliance plus CommandHandler registrations for macro, eventrisk, compliance and approve. The later sentence 'The other three (macro_brief, check_event_risk, compliance_status) are read-only cards and would be reachable the moment somebody adds a permission' is also stale — two of those three left the list.

**Root cause**: The ratchet test enforces the entry LIST but not the explanatory prose around it, so the entries were maintained and the narrative was not.

**Remediation**: Rewrite the TRIAGED block to distinguish the SKILL (macro_brief, still undispatched) from the COMMAND (/macro, a separate handwritten @guard handler at bot/skills/telegram_handler.py:10560). Drop check_event_risk and compliance_status from the 'other three' sentence.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The finder writes 'CommandHandler registrations'; the registrations are (name, handler) tuples at telegram_handler.py:907-909 and :944, not literal CommandHandler(...) calls — same fact, different construction.

- sev→UNCHANGED — Sharpen the distinction the finder gestures at: /macro dispatches the skill `macro_calendar` (telegram_handler.py:10562), NOT `macro_brief`, so the baseline entry `macro_brief` is still correctly listed as dark — the entry list is right and only the narrative around it drifted. Any rewrite must keep macro_brief in the list.

**Evidence**:

```
tests/unreachable_skills_baseline.txt
# Five skills that each advertise a slash command — /macro, /eventrisk,
# /compliance, /approve, /kill — dispatched by NOTHING. Confirmed dark on
# every transport: no @guard command handler exists for any of them, they are
# absent from SKILL_PERMISSION …

bot/skills/telegram_handler.py:10560-10561
    @guard("macro")
    async def _cmd_macro(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:

bot/skills/telegram_handler.py:10591
    @guard("compliance")
```

## B6-33 [INFORMATIONAL] ONCHAIN_PROVIDER is documented as configuration in .env.example and docs/ONCHAIN.md and is read by nothing

- **Dimension**: docs-consistency · **Fix class**: SAFE_AUTO_FIX · **File**: `.env.example:368`
- **Standard**: The audit dimension's rule that env vars documented but never read are a defect; and this repo's general rule that an absent mechanism must not be presented as a present one.

**Observed**: ONCHAIN_PROVIDER appears in an uncommented .env.example line and in the Configuration fence of docs/ONCHAIN.md, positioned as a peer of three knobs that do work. Setting it has no effect. The same doc's closing 'Next' section ('Wire to_confluence_votes() into _score_confluence') is also stale — that wiring exists at bot/core/analyzer.py:3585.

**Root cause**: A knob sketched during design, documented, and never implemented; the surrounding block was implemented so the dead name reads as live.

**Remediation**: Remove ONCHAIN_PROVIDER from .env.example:368 and docs/ONCHAIN.md:39, or implement provider selection. Also update docs/ONCHAIN.md's 'Next' section, which asks for wiring that bot/core/analyzer.py:3585 already performs. A general guard is worth considering: a test that parses .env.example's uncommented names and fails on any not read by bot/, app/, scripts/, docker-compose.yml or nginx.conf would have caught this.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. INFORMATIONAL is the right level — an inert name in a config block.

- sev→UNCHANGED — None. INFORMATIONAL is the right level — the knob is inert and its absence changes nothing. The stale 'Next' section is the more useful half of the report, since it tells a reader work is outstanding that has already shipped.

**Evidence**:

```
.env.example:359-368
ONCHAIN_ENABLED=false
ONCHAIN_API_KEY=
ONCHAIN_BASE_URL=
…
ONCHAIN_FLOW_ENABLED=0
ONCHAIN_PROVIDER=

docs/ONCHAIN.md:36-39
ONCHAIN_ENABLED=false
ONCHAIN_API_KEY=            # your Glassnode / Arkham / Nansen-style key
ONCHAIN_BASE_URL=          # endpoint returning normalised metrics
ONCHAIN_PROVIDER=
```

## B6-34 [HIGH] CI coverage floor silently excludes bot/core/live_executor.py — the order-placing module has been measured at zero for the life of the gate

- **Dimension**: tests · **Fix class**: SAFE_AUTO_FIX · **File**: `scripts/ci_test_gate.py:50-52, 129-150`
- **Standard**: CLAUDE.md: "Running a subset and reporting it as the whole is the defect this repo spends most of its guard tests preventing" and "Unreadable is never zero, and absent is never a measurement."

**Observed**: Only bot/risk (2,659 stmts) and bot/compliance (143 stmts) are measured. bot/core/live_executor.py (4,163 stmts, 60% of the intended scope) contributes nothing to the floor. The gate prints `[gate] FAIL — coverage on ['bot/risk', 'bot/core/live_executor.py', 'bot/compliance'] is below 60%` — a message that names a target it has never measured.

**Root cause**: `--cov=` takes an importable module/package name or a directory, not a file path. The list at line 50 mixes two directory paths (which work) with one file path (which does not). coverage emits a CoverageWarning rather than an error, so pytest still exits 0 and the drop is invisible in a 15-minute log. A second, independent fail-open sits in the same function: `coverage report` exits 1 for "No data to report", and lines 148-150 return False (pass) for that case — an unreadable measurement read as a passing one, in the gate whose sibling comments (lines 190-215) are entirely about not doing that.

**Remediation**: Change line 50 to `COV_TARGETS = ["bot/risk", "bot.core.live_executor", "bot/compliance"]` (or `--cov=bot/core` plus an include filter), then re-measure and set COV_FAIL_UNDER to a floor just under the real number. Add a test asserting every entry in COV_TARGETS appears as a row in `coverage report` output after a run — the gate's coverage of its own targets is exactly the claim nothing checks. Separately, make the rc==1 "no data" branch FAIL rather than return False. Note also that `--fail-under` is a single TOTAL across the report, so once live_executor is included a well-covered bot/risk can still mask a poorly covered live_executor; a per-file floor is the honest shape.

**Verifier corrections** (these override the finder):

- sev→MEDIUM — Two line-number corrections, neither material: the call site is ci_test_gate.py:357 (not 337), and the rc-handling quote sits at ~143-150 (not 136-147). The finding's own title says 'measured at zero' while the body correctly says the module is absent from the report entirely — the body is right. Severity lowered HIGH->MEDIUM: this is a gate that measures less than it claims, with no current live-money impact; the code being unmeasured is not the code being wrong. It is nonetheless exactly the defect CLAUDE.md names as the one the repo spends most of its guard tests preventing ('Running a subset and reporting it as the whole'), which is why it is not LOW.

- sev→MEDIUM — Two corrections to the write-up, neither of which changes the verdict. (1) `_coverage_below_floor()` is called at scripts/ci_test_gate.py:357, not 337 — the finder's line number is wrong; the guard is `if cov_available and not (new_failures or internal_error)` at line 356. (2) The finder's statement counts for live_executor (4,163 stmts, "60% of the intended scope") cannot have come from a coverage report, since coverage never measures the file — they are an estimate presented as a measurement, which is the very thing this repo's rules forbid. The line count (9,728) is real (`wc -l`). Severity down HIGH->MEDIUM: this is a gate overstating its own scope, with zero direct effect on money movement today; no live defect follows from it. It is still a genuine instance of CLAUDE.md's central complaint ("running a subset and reporting it as the 

**Evidence**:

```
scripts/ci_test_gate.py:50-52
    COV_TARGETS = ["bot/risk", "bot/core/live_executor.py", "bot/compliance"]
    COV_FAIL_UNDER = 60
    COV_FLAGS = [f"--cov={t}" for t in COV_TARGETS] + ["--cov-report="]

scripts/ci_test_gate.py:136-147
            r = subprocess.run(
                [sys.executable, "-m", "coverage", "report", f"--fail-under={COV_FAIL_UNDER}"],
                cwd=ROOT, capture_output=True, text=True,
            )
        ...
        if r.returncode == 2:
            print(f"[gate] FAIL — coverage on {COV_TARGETS} is below {COV_FAIL_UNDER}%.")

Actual tool output when the gate's own flags are used:
    CoverageWarning: Module bot/core/live_executor.py was never imported. (module-not-imported)

coverage's `--cov=` argument is an importable module/package name or a directory. `bot/risk` and `bot/compliance` resolve as directories and are measured; `bot/core/live_executor.py` is neither, so coverage never registers it as a source and it is omitted from the report entirely — not reported at 0%, simply absent.
```

## B6-35 [HIGH] The two fail-closed guards between a malformed LLM response and a live trade have zero tests; the second is the only thing stopping an unreadable direction becoming a SHORT

- **Dimension**: tests · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/analyzer.py:1348-1358, 4295-4299, 4744, 4775`
- **Standard**: CLAUDE.md: "A heuristic is never a verdict" and "absent is never a measurement"; the C-07 comments in the source ("do not default to LONG on parse failure", "guards against any path that returns a thesis dict with direction=None") are load-bearing claims with nothing holding them.

**Observed**: Both guards are unpinned. The suite asserts only the happy direction of the parser (`_parsed is True` in five places across four files). No test in tests/ mentions INVALID_DIRECTION or LLM_PARSE_FAIL, and no test drives `Analyzer.analyze()` with an LLM stub returning unparseable or non-directional output.

**Root cause**: Coverage was written around the shapes the parser accepts (tests/test_thesis_prose_is_not_the_tag.py explicitly says its job is proving the empty-reasoning shape is REACHABLE). The refusal half — the reason the C-07 fix exists — was never given a test, so the guards are the code equivalent of CLAUDE.md's #999 card: present, and nothing distinguishes present from working.

**Remediation**: Add two behavioural tests. (1) `Analyzer._parse_llm_response` against the shapes that must be refused — 'garbage', 'DIRECTION: LONG' alone, a JSON body with a non-directional direction — asserting `_parsed is False` or (for the JSON case) that the downstream guard catches it. (2) A test calling the analyzer's thesis path with `{"direction": None}` / `{"direction": "SIDEWAYS"}` asserting `analyze()` returns None and `_record_no_trade` was called with "thesis". Optionally tighten analyzer.py:4744 to mirror 4775 so the JSON branch does not depend on a guard 3,000 lines away, but the tests matter more.

**Verifier corrections** (these override the finder):

- sev→MEDIUM — Severity lowered HIGH->MEDIUM. The finder is explicit that both guards are currently correct and that this is a coverage finding; there is no live defect today. The consequence of an unnoticed regression is real (an unreadable direction becoming a live SHORT), which keeps it above LOW, but HIGH overstates the present state of a system that moves real money.

- sev→MEDIUM — Severity HIGH->MEDIUM. The finder itself concedes "I confirmed both guards are currently CORRECT: this is a test-coverage finding, not a live bug," and I verified the same by reading both guards end to end — there is no path today by which a non-directional LLM response becomes a trade. Un-pinned correct code on the money path is a real gap but it is not HIGH on a severity scale defined by real-world money impact; HIGH would imply something is currently mis-executing. The title also overstates slightly: guard #2 is not "the only thing" stopping a SHORT for every malformed response — for the plain-text branch guard #1 already refuses (verified: 'garbage' and 'DIRECTION: LONG' alone both return _parsed=False). Guard #2 is the sole defence specifically for the JSON branch, which is the accurate narrower claim.

**Evidence**:

```
bot/core/analyzer.py:4295-4299 — guard #1, the parse gate:
            if not result.pop("_parsed", False):
                audit(trade_log, "LLM response could not be parsed, blocking trade",
                      action="analyze", result="LLM_PARSE_FAIL",
                      data={"raw_text": raw_text[:200]})
                return None  # C-07 FIX: do not default to LONG on parse failure

bot/core/analyzer.py:1348-1358 — guard #2, and what sits immediately after it:
        if thesis.get("direction") not in ("LONG", "SHORT"):
            audit(trade_log,
                  f"Thesis has invalid direction={thesis.get('direction')!r}, blocking trade",
                  action="analyze", result="INVALID_DIRECTION", ...)
            return None

        direction = Direction.LONG if thesis["direction"] == "LONG" else Direction.SHORT

bot/core/analyzer.py:4744 — why guard #2 is load-bearing: the JSON branch sets _parsed=True WITHOUT requiring a valid direction, unlike the text branch at 4775 (`result["_parsed"] = parsed_fields >= 2 and result["direction"] is not None`):
                result["_parsed"] = True
                return result

Driven, not read (./.venv-audit/bin/python, Analyzer._parse_llm_response):
  '{}'                                        -> {'direction': None, 'confidence': 0.0, '_parsed': True}
  '{"direction":"SIDEWAYS","confidence":0.9}'  -> {'direction':
```

## B6-36 [MEDIUM] The POST_ONLY double-fill guards are pinned by one substring-in-source assertion; no test ever executes the retry branch

- **Dimension**: tests · **Fix class**: REVIEW_REQUIRED · **File**: `tests/test_audit_v7_fixes.py:211-217`
- **Standard**: CLAUDE.md: "The narrow failure mode is a source scan STANDING IN FOR BEHAVIOUR NOTHING ELSE TESTS" and "Rank candidates by what a wrong claim would cost."

**Observed**: One assertion: `"ABORT_UNVERIFIED_RECHECK" in inspect.getsource(execute)`. It confirms a string literal exists inside a 900-line function. It cannot distinguish a reached guard from an unreached one, cannot see the `elif not _orig2_verified:` condition it depends on, and would stay green if that condition were negated or made unreachable while the audit call remained.

**Root cause**: tests/test_halt_holds_at_order_submission.py:169-176 states the belief that made this acceptable: "Reaching the second needs the venue to reject a post-only order and `_find_order_by_client_oid` to confirm it never landed — a path this suite cannot stage honestly." That premise appears not to hold: tests/test_order_idempotency.py:30-45 already contains the exact fixture shape needed (a `_FakeExchange` with a mode-switched `create_order` and `fetch_open_orders`/`fetch_closed_orders`), and the lookup's verified/unverified behaviour is driven directly in tests/test_audit_v5_fixes.py:98-125. Both halves exist; nothing composes them.

**Remediation**: Add a behavioural test driving `LiveExecutor.execute()` with `CONFIG.limit_orders.post_only` on and an exchange whose first `create_order` raises `Exception("post only order failed")`, parameterised over the three lookup outcomes, asserting `create_order` call counts (1 when the original is found or unverifiable, 2 only in the verified-absent case). Keep the source scan as a cheap position check; it stops being the only evidence.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — Line-number drift on the resubmit quote: `retry_coid = coid + "-r1"` is at live_executor.py:3957, not 3970-3979 (the `_create_order_idempotent` call it leads to is at 3977). The quoted test_halt_holds_at_order_submission.py premise is at ~178-185, not 169-176. Content is otherwise accurate and the severity is appropriate.

- sev→MEDIUM — No correction to substance; MEDIUM is the right level for an untested-but-correct double-fill guard. One caveat the finder does not state: the existing halt test's fixture (`_executor()` in tests/test_halt_holds_at_order_submission.py) uses a MagicMock exchange, so composing the proposed test still requires driving `use_limit` on and supplying price_to_precision/ATR plumbing — the suite's "cannot stage honestly" claim is overstated but not baseless, and the proposed test is a real piece of work rather than a five-line addition.

**Evidence**:

```
tests/test_audit_v7_fixes.py:211-217 — the entire test coverage of the retry path:
    def test_post_only_retry_reverifies_before_resubmit(self):
        # F-10: before resubmitting with a fresh clientOid, the code must
        # re-verify the original isn't resting (index-lag double-fill guard).
        import inspect
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.execute)
        assert "ABORT_UNVERIFIED_RECHECK" in src

What it stands in for — bot/core/live_executor.py:3888-3893 and 3910-3916, two guards whose failure mode is a second live fill:
                        elif not _orig_verified:
                            audit(trade_log, ... result="ABORT_UNVERIFIED", ...)
                            raise
...
                            elif not _orig2_verified:
                                audit(trade_log, ... result="ABORT_UNVERIFIED_RECHECK", ...)
                                raise

and the resubmit they gate, at bot/core/live_executor.py:3970-3979:
                                retry_coid = coid + "-r1"
                                create_kwargs["coid"] = retry_coid
                                ...
                                order = await self._create_order_idempotent(exchange, **create_kwargs)
```

## B6-37 [MEDIUM] api_bridge's bearer-token dependency has no behavioural test — its fail-closed property is asserted only as a substring on one route, while the sibling surface's equivalent IS driven

- **Dimension**: tests · **Fix class**: REVIEW_REQUIRED · **File**: `api_bridge.py:372-388`
- **Standard**: CLAUDE.md: "Ask which OTHER surface makes the same claim — before calling the fix done." The aiohttp dashboard's auth middleware is tested behaviourally and the FastAPI bridge's is not — the parity shape this repo has been bitten by repeatedly.

**Observed**: Zero. The presence of the dependency on each privileged route is enforced structurally by scripts/guard_lint.py's `fastapi-route-auth` rule; the dependency's own logic — including the fail-closed 503 that guard_lint's `why` text calls "the property to preserve" — is executed by nothing.

**Root cause**: api_bridge refuses to import without JWT_SECRET, so the suite historically source-scanned it instead of importing it. tests/test_http_gate_parity.py:38-39 solved the import problem (`os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))`) for the gate-parity helpers but did not extend it to the auth dependency. `_DASHBOARD_TOKEN` being read at module import time (line 372) also makes a naive monkeypatch of the env var a no-op — a trap for whoever writes the test.

**Remediation**: Add tests/test_api_bridge_auth.py: set JWT_SECRET the way tests/test_http_gate_parity.py:39 does, import api_bridge, and call `require_dashboard_token` directly with (a) `api_bridge._DASHBOARD_TOKEN` monkeypatched to "" -> HTTPException 503, (b) credentials=None -> 401, (c) a wrong token -> 401, (d) the right token -> returns it. Patch the module attribute, not the env var, because line 372 runs at import.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — The quoted test_http_gate_parity assertion is at lines 129-134 rather than 132-134; everything else, including all six route line numbers and the guard_lint rule text, checks out to the line. MEDIUM is the right level — the wiring is structurally pinned by guard_lint, so only the dependency's eight-line body is unexercised.

- sev→LOW — Severity MEDIUM->LOW, and one stated root cause is factually wrong. The finder writes "api_bridge refuses to import without JWT_SECRET, which is why the suite historically source-scanned it instead of importing it" — but tests/test_client_ip_cannot_be_forged.py:239 and tests/test_health_does_not_invent_an_engine.py:50 both already do `pytest.importorskip("api_bridge")` and drive real module functions, so the import barrier is not what is stopping this test from existing. On severity: the dependency body is six lines of straight-line logic with no branch that can silently mis-answer, its attachment to every non-exempt route is machine-enforced by scripts/guard_lint.py's fastapi-route-auth rule, and the identical logic on the sibling surface IS driven end to end. That is a coverage gap worth closing cheaply, not a MEDIUM risk to a money-mov

**Evidence**:

```
api_bridge.py:372-388 — the whole of the operator auth for the FastAPI bridge:
    _DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
    _security = HTTPBearer(auto_error=False)

    async def require_dashboard_token(
        credentials: HTTPAuthorizationCredentials = Security(_security),
    ) -> str:
        """Validate bearer token on state-changing endpoints."""
        if not _DASHBOARD_TOKEN:
            raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN not configured — ...")
        import hmac as _hmac
        if credentials is None or not _hmac.compare_digest(credentials.credentials, _DASHBOARD_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token")
        return credentials.credentials

The only test that mentions it — tests/test_http_gate_parity.py:132-134, a substring check on one route:
    def test_risk_status_is_still_token_gated(self):
        i = SRC.index("async def risk_status(")
        assert "Depends(require_dashboard_token)" in SRC[i:i + 200]

The sibling surface is driven end-to-end — tests/test_analytics_endpoints.py:191-196:
    def test_no_token_configured_fails_closed(self, monkeypatch):
        monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "")
        for path in ("/api/performance", "/api/equitycurve"):
            req = SimpleNamespace(path=path, headers={}, method="GET")
            resp = _
```

## B6-38 [LOW] A vacuous `assert ... or True` in tests/test_board_cards.py — the exact pattern the repo caught and annotated once, missed a second time

- **Dimension**: tests · **Fix class**: SAFE_AUTO_FIX · **File**: `tests/test_board_cards.py:71`
- **Standard**: CLAUDE.md, "Asserting a short string is ABSENT is the assertion that keeps misfiring", and the repo's own annotation of `X or True` as vacuous at tests/test_trade_gate_parity.py:274.

**Observed**: A line that reads as a third check and is unconditionally true. Per the neighbouring comment in test_trade_gate_parity.py this is a shape the author already knows misfires; the second instance simply was not re-read.

**Root cause**: The `or True` was very likely added to silence a failing assertion whose expression (`card.split("You:")[0].split("\n\n")[-1]`) is brittle string surgery, rather than the assertion being reworked or dropped. CLAUDE.md's own guidance applies: "when a fresh assertion fails, check whether the code or the assertion is wrong before touching the code" — here neither happened; the assertion was neutered in place.

**Remediation**: Delete line 71, or replace it with the property it was reaching for, anchored to a line rather than to a `split` chain — e.g. assert the rows block contains exactly `DISPLAY_ROWS` handle lines and that `h17` is not one of them. A grep for `or True` inside `assert` is worth adding to whatever already lints tests/, since this is now the second occurrence.

**Verifier corrections** (these override the finder):

- sev→UNCHANGED — None. Quote, line number, precedence reasoning and the cross-reference to test_trade_gate_parity.py:274 all check out; LOW is the right severity since the two live assertions around it do the test's stated job.

- sev→LOW — The finding stands but its root-cause paragraph is wrong and should not be acted on. The finder speculates the `or True` was "very likely added to silence a failing assertion." I executed the expression against the real renderer: with `render_leaderboard(many, "h17", ranked_total=20)`, `card.split("You:")[0].split("\n\n")[-1]` is the ten-row table block (`#  HANDLE ... 1 h1 ... 10 h10`) and `"h17" not in seg` evaluates to True. The assertion passes on its own today, so `or True` is redundant belt-and-braces, not a neutered failure. The fix is therefore the trivial one — delete ` or True` — and the CLAUDE.md quote about "check whether the code or the assertion is wrong" does not apply here. Severity LOW is right; arguably INFORMATIONAL.

**Evidence**:

```
tests/test_board_cards.py:63-72
    def test_a_viewer_below_the_display_cut_still_sees_their_rank(self):
        """The exact case the web version added my_rank for: dropping to 11th
        is when you most want to be told where you are."""
        many = [{"rank": i, "handle": f"h{i}", "profit_factor": "1.0",
                 "round_trips": 5, "trust_tier": "bronze"}
                for i in range(1, 21)]
        card = plain(render_leaderboard(many, "h17", ranked_total=20))
        assert "#17" in card
        assert "h17" not in card.split("You:")[0].split("\n\n")[-1] or True
        assert "You: #17" in card

The repo has already diagnosed this exact shape once, at tests/test_trade_gate_parity.py:272-274:
        assert "gate_label(_gate)" in src
        # `X or True` is vacuous — written once here and caught on re-read.
        # The real property: the icon is a LOOKUP with no green fallback, so
        # a label the map does not know cannot come out green.
```


========================================================================

# Batch 7 (final) — frontend-correctness, contracts

**18 raw · 15 CONFIRMED · 1 SUSPECTED · 2 REFUTED**

**This completes all 26 dimensions.**


## B7-01 [CRITICAL] Operator live dashboard hardcodes the badge "SIMULATION" and never reads engine.simulation_mode

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard.html:417-419 (markup); 718-771 (updateEngine)`

**Observed**: The badge is amber and reads "SIMULATION" permanently. `grep -n "simulation" bot/web/dashboard.html` returns nothing — the client never reads the field at all. `.badge-live` (line 97) is dead CSS reachable from no code path. The connection dot nested inside the badge *is* updated (green when polling succeeds), so a live-trading engine renders as a green-dot "SIMULATION" badge.

**Root cause**: The RC-AUD-016 fix was applied to the server half (dashboard_server.py:84-96) and the client half was never wired. `updateEngine(eng, ...)` receives the whole `engine` object and uses only `eng.state`.

**Remediation**: In `updateEngine`, set the badge from the payload with three states, not two: `const sim = eng && eng.simulation_mode;` → `sim === true` → SIMULATION/`badge-sim`; `sim === false` → LIVE — REAL MONEY/`badge-live`; anything else (absent/non-boolean, e.g. the `data["engine"] = {"state": "UNKNOWN"}` branch at dashboard_server.py:98-99) → "MODE UNKNOWN" with a neutral class. Add a test in tests/ that plants each of the three payloads and asserts the badge text, in the style of app/test/engine_status_scenarios.test.js.

**Verifier corrections**:

- sev→HIGH — Downgrade CRITICAL to HIGH. The defect is real and the direction is the dangerous one (live money labelled SIMULATION), but this is a display-only operator panel behind a Bearer token, and it is not the only place the operator learns the mode — bot/main.py:47 prints `Mode: SIMULATION|LIVE` at boot and bot/skills/telegram_handler.py:1275 derives LIVE/PAPER/IDLE from CONFIG.is_live() on the Telegram surface. No trading decision is gated on this badge. Fix as described; the three-state treatment (including the dashboard_server.py:98 `{"state": "UNKNOWN"}` branch, which carries no simulation_mode key at all) is correct.

- sev→HIGH — Finding stands as written. Downgrading CRITICAL->HIGH only because this is a display on an operator console, not an execution path: it cannot itself place or block an order, and the same operator has /risk, /portfolio and the Telegram surfaces. The lie points the dangerous way (says 'no real money at stake' while live), so HIGH rather than MEDIUM.

**Evidence**:

```
bot/web/dashboard.html:417-419
      <span class="header-badge badge-sim" id="modeBadge">
        <span class="status-dot dot-amber" id="statusDot"></span>
        SIMULATION
      </span>

bot/web/dashboard.html:718-721 (updateEngine — the only consumer of `eng`)
function updateEngine(eng, tiers, cost) {
  // State badge
  const s = (eng && eng.state) || 'IDLE';
  $('stateBadge').textContent = s.toUpperCase();

And the server that feeds it, bot/web/dashboard_server.py:84-97:
        # RC-AUD-016: report the REAL trading mode, not a hardcoded True.
        # A hardcoded "simulation_mode": True made the dashboard show paper mode
        # even while trading live with real capital.
            _sim_mode = not _engine_cfg.is_live()
        data["engine"] = { ... "simulation_mode": _sim_mode, ... }
```

## B7-02 [HIGH] Operator dashboard renders a swallowed positions-read exception as "No open positions" (HTTP 200 with an empty or partial list)

- **Dimension**: frontend-correctness · **Fix class**: REVIEW_REQUIRED · **File**: `bot/web/dashboard_server.py:151-167`

**Observed**: The exception is swallowed and the client says "No open positions" — and `posCount` is set to '0'. Worse, because the `except` wraps the whole accumulation loop, an exception part-way through returns the positions gathered so far as if they were the complete book: a partial position list printed as whole.

**Root cause**: `except Exception: pass` around the accumulation, plus a return that cannot distinguish "nothing open" from "could not look". The client's only failure guard is `res.ok`, which this path deliberately keeps at 200.

**Remediation**: Move the try/except to wrap the whole build and return 503 `{"error": "positions_unavailable"}` on failure — matching `handle_positions` in bot/web/user_gateway.py. Then `fetchAll`'s existing `!posRes.ok` check already routes it to the Disconnected state. Do the same for `handle_signals` (dashboard_server.py:170-190), which has two identical `except Exception: pass` blocks feeding `updateTrades` → "No trade history".

**Verifier corrections**:

- sev→HIGH — None. The partial-book half is the more serious of the two claims and is correctly identified — this is CLAUDE.md's 'sum(...) over a set that includes unreadable rows' shape at the transport layer. The proposed fix (move the try to wrap the whole build, return 503, let the existing `!posRes.ok` check route to Disconnected) is minimal and correct.

- sev→HIGH — Stands. One caveat on the 'partial list printed as whole' half: it requires the exception to be raised mid-iteration (e.g. one user's portfolio raising in get_trailing_status) rather than at all_portfolios(); that is plausible but not demonstrated, so treat the empty-list case as the confirmed one and the partial case as the secondary risk.

**Evidence**:

```
bot/web/dashboard_server.py:151-167
async def handle_positions(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    positions = []
    try:
        for uid, port in engine.user_portfolios.all_portfolios().items():
            ...
                positions.append(d)
    except Exception:
        pass
    return web.json_response({"positions": positions})

bot/web/dashboard.html:650-654
function updatePositions(positions) {
  const el = $('posBody');
  if (!positions || !positions.length) {
    el.innerHTML = '<div class="empty-msg">No open positions</div>';
```

## B7-03 [HIGH] Operator dashboard paints "CIRCUIT BREAKER: OK" in green from a failed risk read

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard.html:703-712`

**Observed**: A green "CIRCUIT BREAKER: OK" with a glowing green dot, plus "Checks 0 / Rejected 0 / Trips 0 / Loss Streak 0" (bot/web/dashboard.html:710-715 use `(r.total_checks||0)` etc.) — a complete, confident, all-clear risk report assembled from no data.

**Root cause**: The server's fail-safe emits `{}` rather than omitting the key or signalling failure, and every client guard on this page is `if (!x) return` — which `{}` passes. The same file already documents this exact trap two functions down for the cost panel ("`{}` is truthy, so the old `if (cost)` did not stop it either", lines 758-762) but the fix was never carried to `updateRisk` or `updatePortfolio`.

**Remediation**: Have handle_state omit the `risk` key on failure (or set `data["risk"] = None`), and change the client guard to test for the field rather than the object: `if (!r || typeof r.circuit_breaker_active !== 'boolean') { render an explicit "breaker state unreadable" row in the muted/neutral colour; return; }`. Apply the same field-presence test to the four counters.

**Verifier corrections**:

- sev→HIGH — None. This is the strongest of the twelve: a confident all-clear on the control that decides how much real money is lost before the engine halts, assembled from a failed read, in a file whose own comments name the bug two functions below. Fix both halves (omit/None on the server, field-presence test on the client) as proposed.

- sev→HIGH — Stands. Note the failure branch requires the risk read itself to raise (engine.risk missing an attribute / stats raising), so this is a failure-path defect, not the steady state — same class as the cost/tiers defects this same file already fixed.

**Evidence**:

```
bot/web/dashboard.html:703-708
function updateRisk(r) {
  if (!r) return;
  const cb = r.circuit_breaker_active;
  const breakerHtml = cb
    ? '...CIRCUIT BREAKER: TRIPPED...'
    : '<div class="breaker breaker-ok">...color:var(--green);">CIRCUIT BREAKER: OK</div></div></div>';

The payload it guards against, bot/web/dashboard_server.py:64-72:
    try:
        data["risk"] = {
            "circuit_breaker_active": engine.risk.circuit_breaker_active,
            ...
        }
    except Exception:
        data["risk"] = {}
```

## B7-04 [HIGH] Operator dashboard shows "Daily: +0.00%" in green, 0 open positions and 0 trades when the portfolio read failed

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `bot/web/dashboard.html:627-641`

**Observed**: "Open 0", "Trades 0", and a green "Daily: +0.00%" beside two honest '--' figures — a mixture that reads as a real, flat, break-even day rather than a failed read.

**Root cause**: `|| 0` coercion on four fields, plus the same `{}`-is-truthy guard gap as updateRisk. The file's own comment at lines 758-762 identifies this exact family for the cost panel and fixes it there only.

**Remediation**: Reuse the file's own `orDash()` helper (bot/web/dashboard.html:779-783), which already implements exactly this rule, for `sOpen` and `sTrades`. For the daily badge: `const dp = (p.daily_pnl === null || p.daily_pnl === undefined || p.daily_pnl === '') ? null : Number(p.daily_pnl);` then render '--' with the dim colour when `dp === null`, and only apply green/red when it is a finite number.

**Verifier corrections**:

- sev→MEDIUM — Downgrade HIGH to MEDIUM. Two mitigating facts the finder itself established: the two dollar figures beside these fields already render '--', so the panel is visibly half-broken rather than confidently whole, and the defect is confined to the `data["portfolio"] = {}` failure branch (PortfolioState always defines daily_pnl on the success path — verified at bot/utils/models.py). Real, worth fixing with the file's own orDash(), but a rung below the circuit-breaker all-clear in finding 2.

- sev→MEDIUM — Stands, but HIGH is inflated relative to findings 1 and 2: half the panel (balance, equity, win rate) already degrades honestly to '--', so the operator sees a visibly mixed panel rather than a coherent false all-clear. The green '+0.00% Daily' is the real defect here; 'Open 0'/'Trades 0' beside two dashes is milder.

**Evidence**:

```
bot/web/dashboard.html:627-640
function updatePortfolio(p) {
  if (!p) return;
  $('sBalance').textContent = fmtUsd(p.balance_usd);
  $('sEquity').textContent = fmtUsd(p.equity_usd);
  $('sOpen').textContent = p.open_positions || 0;
  $('sTrades').textContent = p.total_trades || 0;
  ...
  const dp = p.daily_pnl || 0;
  $('pnlBadge').textContent = 'Daily: ' + pnlSign(dp) + fmt(dp) + '%';
  $('pnlBadge').style.color = dp >= 0 ? 'var(--green)' : 'var(--red)';

The payload it guards against, bot/web/dashboard_server.py:54-61:
    try:
        ...
        data["portfolio"] = _safe_dict(snap)
    except Exception:
        data["portfolio"] = {}
```

## B7-05 [MEDIUM] Chat drawer header prints "Today +0.00" in green when daily PnL is null — a value routes/portfolio.js explicitly returns

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/js/chat.js:272-280`

**Observed**: A green "+0.00" labelled "Today", indistinguishable from a genuine measured break-even day. Note that the line directly above handles the same problem correctly for equity: `const eq = (d.equity == null) ? '—' : ...`.

**Root cause**: `Number(x || 0)` upstream of two helpers that were written to handle null correctly, plus a hand-rolled `dp >= 0 ? 'up' : 'down'` instead of the repo's `pnlClass`, which deliberately returns '' for unknown.

**Remediation**: `const dp = (d.daily_pnl == null) ? null : Number(d.daily_pnl);` then omit the Today cell entirely when `dp === null` (the omit strategy the rest of this function already uses — the whole strip hides on a failed read), or render `signed(dp)` with `class="${pnlClass(dp)}"` so unknown gets no colour.

**Verifier corrections**:

- sev→MEDIUM — None; if anything under-stated. The finder cites only the operator sync path (portfolio.js:144); dbFallback omits daily_pnl entirely, so every non-operator user on a gateway outage also gets a green '+0.00' labelled Today.

- sev→MEDIUM — Stands as written, and the reachability is broader than the finder stated: the dbFallback shape omits daily_pnl entirely, so every fallback response also renders the fabricated green +0.00.

**Evidence**:

```
app/public/js/chat.js:272-280
    const dp = Number(d.daily_pnl || 0);
    const dpTxt = signed ? signed(dp) : (dp >= 0 ? '+' + fmt(dp, 2) : fmt(dp, 2));
    const openN = (d.open_positions || []).length;
    const modeCls = d.mode === 'LIVE' ? 'mode-badge--live' : 'mode-badge--paper';
    metaEl.innerHTML =
      `<span class="mode-badge ${modeCls}">${esc(d.mode || 'PAPER')}</span>` +
      `<span class="chat-meta-item"><span class="k">Equity</span><b>${eq}</b></span>` +
      `<span class="chat-meta-item"><span class="k">Today</span><b class="${dp >= 0 ? 'up' : 'down'}">${dpTxt}</b></span>` +

The producer, app/routes/portfolio.js:144:
    daily_pnl: null, // not tracked on the sync path
```

## B7-06 [HIGH] Chat drawer stamps a confident PAPER badge on the /api/portfolio fallback that the dashboard's own mode chip refuses to trust

- **Dimension**: frontend-correctness · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/chat.js:275-277`

**Observed**: The chat drawer header shows a PAPER badge (amber, `.mode-badge--paper`, app/public/styles.css:529) while the dashboard's own top chip on the same page shows "MODE ?". `loadMeta` never reads `d.stale` or `d.source` at all. Two surfaces in one tab give two different answers about whether real money is at stake — the same 'one session, two answers' class of bug app/test/cache_buster_ratchet.test.js was written about.

**Root cause**: The mode-unknown distinction was added to `updateModeChip` in dashboard.js and never carried to the chat drawer, which consumes the identical `/api/portfolio` payload.

**Remediation**: Extract the mode verdict into a shared pure model (as the repo did for `engine-status-model.js` and `panel-error-model.js`) and call it from both `updateModeChip` and `loadMeta`, so the two cannot drift. Minimum fix: in loadMeta, `if (d.stale && d.source !== 'sync') render a neutral 'MODE ?' badge` before the LIVE/PAPER branch.

**Verifier corrections**:

- sev→MEDIUM — Downgrade HIGH to MEDIUM. The failure direction is the safer one: it asserts PAPER when the mode is unknown, so it under-claims risk rather than over-claiming it, and the authoritative chip on the same page already says MODE ?. Worth fixing (the two-surfaces-one-answer drift is exactly what the shared-model pattern exists for), but it does not tell a user their real money is safe when it is not.

- sev→MEDIUM — Stands, but HIGH overstates it. The server is the one asserting mode:'PAPER' on the fallback, and the sibling chip on the same page simultaneously shows 'MODE ?', so the user gets a visible contradiction rather than a silent false all-clear; and no action is taken from this badge (the trade-confirm card computes its own mode from _trade_mode, which the finder correctly excluded). MEDIUM.

**Evidence**:

```
app/public/js/chat.js:275-277
    const modeCls = d.mode === 'LIVE' ? 'mode-badge--live' : 'mode-badge--paper';
    metaEl.innerHTML =
      `<span class="mode-badge ${modeCls}">${esc(d.mode || 'PAPER')}</span>` + ...

The fallback it renders, app/routes/portfolio.js:234-259:
    if (!gateway.isConfigured()) {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER' });
    }
    ...
    if (r.status !== 200) {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER' });
    }

The sibling that gets it right, app/public/js/dashboard.js:248-258:
  function updateModeChip(pf) {
    ...
    if (pf.stale && pf.source !== 'sync') {
      // Bot unreachable and no live feed: mode is unknown — don't assert PAPER.
      el.textContent = 'MODE ?';
```

## B7-07 [MEDIUM] Public trader card tells a visitor the trader does not exist when the server returned 500/429/503

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/trader.html:167-170`

**Observed**: A shared public link to a real trader renders "No trader by that handle — the board awaits." The correct copy exists three lines further down and is unreachable for HTTP failures because `r.ok ? r.json() : null` collapses them into the absence branch before the catch can see them.

**Root cause**: `r.ok ? r.json() : null` conflates 'server refused' with 'server said no such row'.

**Remediation**: Replace line 168 with the pattern page_failure_honesty.test.js already pins for strategy.html: `.then(function (r) { if (r.status === 404) return null; if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })`, and keep `if (!d)` for the genuine-404 copy. The existing `.catch` at line 237 then paints the unavailable state. Add trader.html to app/test/page_failure_honesty.test.js.

**Verifier corrections**:

- sev→MEDIUM — None. This is the cheapest fix of the twelve and the repo has already written both the pattern and the test that would pin it; adding trader.html to page_failure_honesty.test.js is the right move.

- sev→LOW — Stands. Downgrading MEDIUM->LOW: this is a public, read-only marketing card. No money, no operator decision and no user action depends on it; the cost is a shared link reading 'no such trader' during an outage. Still a clean instance of the repo's own rule and cheap to fix by adding trader.html to page_failure_honesty.test.js.

**Evidence**:

```
app/public/trader.html:167-170
  fetch('/api/arena/trader/' + encodeURIComponent(handle), { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) { root.innerHTML = '<div class="empty">No trader by that handle — the board awaits. <a href="/arena">Enter the Arena →</a></div>'; return; }

The route's failure answers, app/routes/arena.js:958-961:
  } catch (err) {
    console.error('Arena trader error:', err.stack || err.message);
    res.status(500).json({ error: 'Trader card unavailable' });
  }
```

## B7-08 [MEDIUM] i18n.js ships 1.91 MB (765 KB gzipped) of 14 language dictionaries as a render-blocking script on 37 pages

- **Dimension**: frontend-correctness · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/i18n.js:1 (whole file; tag e.g. app/public/index.html:930, dashboard.html:94)`

**Observed**: Every visitor to any of 37 pages downloads all 14 dictionaries synchronously. For the English default the module then does literally nothing (`if (current !== 'en') apply(...)`). This is by a wide margin the largest asset the platform ships: 3.4× `dashboard.js` and ~13× the whole three.js bundle the perf pass went to the trouble of deferring.

**Root cause**: One monolithic dictionary bundled with the (correct, well-documented) requirement that the language be resolved at parse time. The parse-time requirement applies only to `resolveCurrent()` — a few dozen lines — not to the 1.9 MB of strings.

**Remediation**: Split the module: keep the tiny language-resolution + `<html lang/dir>` half inline or in a small synchronous file, and load only the resolved language's dictionary (`/js/i18n/<lang>.js?v=N`) — for `en` load nothing, since the English text is already the markup. Add a byte-budget test for app/public/js/*.js modelled on site/test/payload_budget.test.js so this cannot regress. Note app/package.json declares no compression middleware and app/server.js:288-294 does not add one, so whether the 1.91 MB or the 765 KB figure is what a visitor pays depends on the front proxy, which is not in this repo (the nginx.conf here proxies api_bridge and the bot, not the Express app).

**Verifier corrections**:

- sev→LOW — Downgrade MEDIUM to LOW, and drop the 'render-blocking' framing. The tags sit at the END of <body> (index.html:930 of ~940), so they block DOMContentLoaded and the scripts after them, not first paint. This is a payload/latency issue with no correctness consequence — nothing renders wrong, nothing lies to a user — so it sits outside the frontend-correctness dimension proper. Real and worth a byte budget, but it should not outrank any of findings 0-6.

- sev→MEDIUM — Stands. Two wording corrections: it is not 'render-blocking' in the head sense — both tags sit at the end of <body>, so it blocks the parser tail, the three scripts after it (including app.js) and DOMContentLoaded, not first paint. And the page count is 40 script references, not 37 pages. This is a performance finding rather than a correctness one; MEDIUM is defensible on mobile but do not let it outrank the honesty findings.

**Evidence**:

```
app/public/index.html:930
<script src="/js/i18n.js?v=135"></script>
<script src="/js/icons.js?v=5"></script>
<script src="/js/panel-error-model.js?v=1"></script>
<script src="/js/app.js?v=13"></script>

Measured: `ls -la app/public/js/i18n.js` → 1,958,667 bytes; `gzip -c i18n.js | wc -c` → 765,344 bytes. Loading the module under node: 14 languages (en,es,zh,pt,fr,de,nl,ja,ko,ru,tr,it,hi,ar) × 1,783 keys.

And the module's own last line, app/public/js/i18n.js (tail):
    if (current !== 'en') apply(document, current);
```

## B7-09 [LOW] "Today for you" buckets by the browser's local day while the PnL calendar buckets by UTC day — same data, two answers

- **Dimension**: frontend-correctness · **Fix class**: REVIEW_REQUIRED · **File**: `app/public/js/dashboard.js:886-887 (local) vs 3897 and 3902 (UTC)`

**Observed**: Two panels in the same app, reading the same `/api/trades/history` payload, disagree about which day a close belongs to for any user not on UTC. Neither surface labels its timezone: the calendar's footnote reads only "One cell per day, newest bottom-right. + profit · − loss."

**Root cause**: `toDateString()` (local calendar day) in one panel and `toISOString().slice(0,10)` (UTC day) in the other, written independently.

**Remediation**: Pick one boundary (UTC, matching the rest of the product) and use it in both: replace line 886-887 with `const todayStr = new Date().toISOString().slice(0,10); const today = rows.filter(t => t.closed_at && new Date(t.closed_at).toISOString().slice(0,10) === todayStr);`. Then label it — 'Today (UTC)' on the card and in the calendar footnote — so a user in UTC+13 is not left reconciling two numbers.

**Verifier corrections**:

- sev→LOW — None. LOW is the right severity: it produces two reconcilable-but-unlabelled numbers for off-UTC users, no false all-clear and no money at risk. Note for whoever fixes it that the Today card was deliberately routed through self.TradeStats.summaryCells (dashboard.js:889-899, with a comment about not duplicating pnlClass's contract), so the change should be confined to the filter predicate at 886-887 and not reach into the stats helper.

- sev→LOW — Stands at LOW. Real but narrow: it only diverges for closes near midnight for non-UTC users, and neither number is used for anything but display. Note the finder's proposed fix silently changes 'Today for you' from the user's own day to UTC — that is a product choice, so label whichever is chosen on the surface rather than swapping it quietly.

**Evidence**:

```
app/public/js/dashboard.js:885-887 (Home card, "Today for you")
        const rows = hist?.data?.trades || hist?.data?.rows || [];
        const todayStr = new Date().toDateString();
        const today = rows.filter(t => t.closed_at && new Date(t.closed_at).toDateString() === todayStr);

app/public/js/dashboard.js:3894-3903 (Journal, daily PnL calendar — same /api/trades/history source)
      trades.forEach(t => {
        const v = parseFloat(t.pnl);
        if (!Number.isFinite(v)) return;
        const d = new Date(t.closed_at).toISOString().slice(0, 10);
        byDay[d] = (byDay[d] || 0) + v;
      });
      for (let i = 27; i >= 0; i--) {
        const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
```

## B7-10 [LOW] Trade-confirm modal leaves the Confirm button enabled for the whole 35-second request, unlike the chat card that shares the same endpoint

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `app/public/js/dashboard.js:3070-3082`

**Observed**: Up to the rate limit (app/routes/webtrade.js:31, 10 requests/minute per user) concurrent confirms are sent. On success each returns 200; the handler closes the modal and toasts 'Trade confirmed.' per response, so a user who double-tapped on a slow connection sees the success toast more than once for one trade.

**Root cause**: No `disabled` state and no in-flight sentinel on the confirm handler; the guard was implemented in chat.js and not carried to the dashboard modal.

**Remediation**: Disable both modal buttons at the top of the handler and re-enable them on the failure branch, matching app/public/js/chat.js:201-211: `const okB = document.getElementById('tradeModalConfirm'), noB = document.getElementById('tradeModalCancel'); okB.disabled = noB.disabled = true;` … re-enable in the `if (!r.ok)` branch.

**Verifier corrections**:

- sev→LOW — None. The finder did the reachability work the brief asks for, verified the server-side serialisation before reporting, and set severity to match — this is a duplicated success toast, not a double execution. Fix is three lines mirroring chat.js:201/210.

- sev→LOW — Stands at LOW, and credit where due: the finder verified the server-side serialisation before reporting and set severity accordingly rather than claiming double execution. Minor: the duplicate success toast requires two responses to arrive after `close()`, which is possible but needs a genuine double-tap during the in-flight window.

**Evidence**:

```
app/public/js/dashboard.js:3070-3072
    document.getElementById('tradeModalConfirm').onclick = async () => {
      msg.textContent = 'Executing…';
      const r = await RC.postWithStepUp('/api/trade/confirm', { trade_id: pt.trade_id }, { timeoutMs: 35000 });

The sibling that does disable, app/public/js/chat.js:201-203:
    okBtn.onclick = async () => {
      okBtn.disabled = noBtn.disabled = true;
      const r = await postWithStepUp('/api/trade/confirm', { trade_id: pt.trade_id }, { timeoutMs: 35000 });
```

## B7-11 [INFORMATIONAL] Cache-busted assets are served with a one-day max-age, and the 985 KB 3D model falls through to one hour

- **Dimension**: frontend-correctness · **Fix class**: SAFE_AUTO_FIX · **File**: `app/server.js:288-294`

**Observed**: Versioned `.js`/`.css` get 86400 without `immutable`, so every returning visitor revalidates ~10 script URLs daily; the versioned 985 KB `.glb` revalidates hourly. Because express.static sets ETag/Last-Modified by default these are 304 round-trips rather than re-downloads — the cost is latency, not bytes — which is why this is filed as informational rather than a defect of substance.

**Root cause**: The extension allow-list in `setHeaders` predates the `.glb` asset and does not include it; and the long-cache opportunity the `?v=` discipline creates was never taken.

**Remediation**: Serve any request carrying a `?v=` query with `public, max-age=31536000, immutable`, and extend the extension list to cover `glb|png|svg|jpg`. Both are safe given the ratchet in app/test/cache_buster_ratchet.test.js, which already fails a build where a changed bundle keeps its version.

**Verifier corrections**:

- sev→INFORMATIONAL — None. Correctly filed as informational, with the 304-not-redownload nuance stated rather than hidden. One caution for the fix: `immutable` on a `?v=`-keyed URL is only safe while the ratchet holds, so the extension-list widening and the immutable switch should land together with a test on the static headers.

- sev→INFORMATIONAL — Stands at INFORMATIONAL, which is the right level — the cost is a handful of 304 round-trips, not bytes. Caution on the proposed fix: serving anything with a ?v= as `immutable` for a year is only safe while every referencing page bumps its ?v= on change, which CLAUDE.md's 'bump it in every page that references a changed bundle' says has been missed before; the ratchet checks values are distinct, not that every referencing page was updated.

**Evidence**:

```
app/server.js:288-294
app.use(express.static(path.join(__dirname, 'public'), {
  maxAge: '1h',
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache');
    else if (/\.(css|js|woff2)$/.test(filePath)) res.setHeader('Cache-Control', 'public, max-age=86400');
  },
}));

The asset it does not cover, app/public/js/mascot3d.js:61:
const MODEL_URL = '/mascot/RUNECLAW_Command_Core_Mascot_Premium.glb?v=1';
```

## B7-12 [HIGH] Solana delegate scan queries only the legacy SPL Token program, then reports itself "complete for token delegates" — every Token-2022 delegate (including $RCLAW's own program) is invisible and counted as clean

- **Dimension**: contracts · **Fix class**: REVIEW_REQUIRED · **File**: `app/lib/solana.js:22, 176-177, 220-225`

**Observed**: A wallet with an unlimited Token-2022 delegate gets a response asserting `unreadable_pairs: 0` and 'Every SPL token account of this owner was scanned ... this is complete for token delegates'. The completeness claim is false and the omission is not counted anywhere.

**Root cause**: `TOKEN_PROGRAM` is a single hardcoded constant for the legacy program, written when only legacy SPL mints were in the curated list, and `readDelegates` reuses the portfolio reader's filter verbatim (the same constant is used at line 107 for `readSolana`). The prose was written to describe the intent ('all token accounts') rather than the filter that was actually sent.

**Remediation**: Add `const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';` and issue a second `getTokenAccountsByOwner` for it, merging the results and incrementing `unreadable_pairs` if either call fails (today a failure of the single call already returns the honest 'unknown, not clean' body at lines 205-211 — keep that shape per-program). Update `spenders_checked` to name both programs, and extend `app/test/solana_delegates.test.js` (whose docstring currently asserts 'ALL token accounts are scanned') with a Token-2022 account fixture.

**Verifier corrections**:

- sev→MEDIUM — Holds on every factual point. Severity lowered from HIGH to MEDIUM: this is a read-only, unauthenticated advisory endpoint — it moves no funds and signs nothing (the module header and the note both say the revoke plan is a command the owner runs). The harm is a false all-clear on a security X-ray, which is a real instance of the repo's own rule, but it is one step removed from money. The minimal honest fix is the cheaper half of the proposal: change `spenders_checked` and the note to name the legacy SPL Token program specifically, rather than claiming completeness.

- sev→MEDIUM — Severity HIGH is inflated: this is a read-only, non-signing informational X-ray, the false 'complete' prose is not rendered on the page, and the page already tells the user a clean result is never a guarantee. What survives is the `spenders_checked` label 'SPL delegates (all token accounts)' plus the API `note`, both of which assert a completeness the query cannot deliver for the project's own token standard. MEDIUM.

**Evidence**:

```
app/lib/solana.js:22
  const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';

app/lib/solana.js:176-177 (readDelegates)
    const res = await rpcCall('getTokenAccountsByOwner',
      [address, { programId: TOKEN_PROGRAM }, { encoding: 'jsonParsed' }]);

app/lib/solana.js:212-225 (the answer it returns)
    spenders_checked: ['SPL delegates (all token accounts)'],
    findings,
    zero_pairs: scanned - findings.length,
    unreadable_pairs: 0,
    note: 'Every SPL token account of this owner was scanned for a set '
      + 'delegate — unlike the EVM check this is complete for token '
      + 'delegates, but Solana programs can hold OTHER powers (PDAs, '
      + 'program ownership) this scan does not see.'
```

## B7-13 [MEDIUM] Presale unsold-token rollover is computed, printed to the operator as applied, and then never sent when `raisedSolToLiquidityBps` is 0 — the exact condition the config says strands ~120,000,000 tokens permanently

- **Dimension**: contracts · **Fix class**: REVIEW_REQUIRED · **File**: `token/presale/genesis_presale.mjs:537-538, 558-575, 683-697, 1121-1124`

**Observed**: The operator is told the rollover was configured; on chain it does not exist; and the command that would execute it declines. Per the config's own `_unsoldRolloverNote`, the consequence is that the unsold presale remainder is 'stranded permanently' — 'roughly 120,000,000 tokens, 12% of supply, frozen in an account nobody can reach' — because `withdrawUnsoldPresaleV1` is V1-only and rejects a V2 genesis account (0x2f).

**Root cause**: Two independent on-chain behaviors (quote split and unsold rollover) share one `endBehaviors` array and one send, but the send and the trigger are both conditioned on the quote split's parameter alone. The rollover was added later (its `push` at line 564 is unconditional) without widening the two `bps > 0` conditions, and the operator-facing `console.log` sits next to the push rather than next to the send.

**Remediation**: Change line 683 to `if (endBehaviors.length)` and line 686's log to name every behavior being sent; change line 1121 to gate on the presence of ANY end behavior (re-read the bucket, as `assertEncodedSplitOnChain` already does, rather than trusting `a.quoteSplitBps`); and record the rollover in the artifact alongside `quoteSplitBps` so `presale:liquidity`/`presale:verify` can check it. Add a `rollover.test.mjs` case asserting the rollover behavior survives `raisedSolToLiquidityBps = 0`.

**Verifier corrections**:

- sev→LOW — Holds, but severity lowered from MEDIUM to LOW. It is doubly latent: the committed config carries raisedSolToLiquidityBps 6667 (metaplex-genesis.config.json) and derive_params.test.mjs:174-179 pins it >0, and a bps=0 config is a broken configuration that cmdPlan and cmdLiquidity both refuse (parityLpBaseUnitsForRaise throws), so an operator who set it would be stopped before the LP step — though only AFTER the presale bucket is immutable. The genuinely valuable half of the fix is cheap and worth doing: gate the send on `endBehaviors.length` and stop printing a behavior that was not sent.

- sev→LOW — MEDIUM is too high. Devnet-only tooling, a committed config pinned >0 by an existing test, and a downstream hard refusal (parityLpBaseUnitsForRaise throws on bps=0) mean no realistic path reaches the stranded-tokens outcome without first tripping another gate. The one-line hardening (`if (endBehaviors.length)` at :683, and gating cmdTrigger on the presence of any end behavior) is still worth taking, and so is removing the console.log that announces an unsent behavior.

**Evidence**:

```
token/presale/genesis_presale.mjs:537-538 — the array is created empty when bps is 0
  const bps = Number(cfg.liquidity.raisedSolToLiquidityBps ?? 0);
  const endBehaviors = bps > 0
    ? [ behavior('SendQuoteTokenPercentage', { ... }) ]
    : [];

:558-575 — the rollover is pushed onto it unconditionally and announced
  const rollover = deriveUnsoldRollover(cfg);
  if (rollover) {
    ...
    endBehaviors.push(
      behavior('BaseTokenRollover', { processed: false,
        percentageBps: rollover.percentageBps, padding: [0,0,0,0],
        destinationBucket: destination[0] }));
    console.log(
      `    unsold rollover: ${rollover.percentageBps / 100}% of unsold presale tokens -> ` +
      `"${rollover.name}" bucket [${rollover.bucketIndex}] ${destination[0]}`);

:683-697 — but the ONLY send of endBehaviors is gated on the quote split
  if (bps > 0) {
    await step('setPresaleBucketV2Behaviors',
      () => setPresaleBucketV2Behaviors(umi, { ..., endBehaviors }), ...);
  } else {
    console.log('[4/4] No quote split configured (raisedSolToLiquidityBps is 0).');
  }

:1121-1124 — and the executor refuses for the same reason
  if (!a.quoteSplitBps) {
    console.log('This presale encodes no end behaviors — nothing to trigger.');
    return;
  }
```

## B7-14 [MEDIUM] `meme_preflight.preflight` never supplies `radar_risk`, so the meme buy gate's `risk_tier` check fails on every production call — /memeplan can never report a ready plan and the entire Jupiter swap-build/signing slice is unreachable

- **Dimension**: contracts · **Fix class**: REVIEW_REQUIRED · **File**: `bot/core/meme_preflight.py:112-116`

**Observed**: `grep -rn radar_risk --include=*.py .` shows the parameter is supplied only in tests (tests/test_meme_gate.py, tests/test_meme_executor.py). The two production callers — bot/skills/telegram_handler.py:5395 (`/memeplan`) and bot/web/user_gateway.py:964 (`POST /meme/swap/build`) — both go through `preflight`, which omits it. `meme_swap.build_swap`, `intent_id`, `terms_expired`, `app/public/js/swap-sign-model.js` and the `signTransaction` path in `app/public/js/solana_wallet.js` are consequently unreachable in production.

**Root cause**: `plan_swap` was designed with `radar_risk` as an injected input (bot/core/meme_executor.py:53) and the shared preflight extracted later (per its own docstring, to stop /memeplan and the gateway keeping two copies) gathers only the DexScreener features and `assess_token` output. The radar read was never wired into the extracted path, and because the omission fails CLOSED it produces a plausible denial rather than an error.

**Remediation**: Either (a) compute a radar tier inside `preflight` from the features already gathered and pass it to `plan_swap`, or (b) if no radar source exists yet, remove the `risk_tier` check from `evaluate_meme_buy` and record the removal, rather than shipping a precondition no caller can satisfy. Then add a test that drives `preflight` with a good market and asserts `allowed is True` when `MEME_TRADING_ENABLED` is on — the absence of such a test is what let this stand.

**Verifier corrections**:

- sev→MEDIUM — Stands as reported. One nuance worth keeping in the writeup: the failure is honest and fail-closed — the gate renders 'no risk read (fail-closed)' and the gateway returns build:null with 'plan not allowed — nothing was built', so nothing is misrepresented to a user and no money is at risk. MEDIUM is right for a shipped feature with no reachable success path; it is not higher because the failure mode is refusal.

- sev→LOW — The defect is confirmed exactly as described — this is the 'precondition no caller can satisfy / tests were the only caller' pattern. Severity should be LOW rather than MEDIUM on a money scale: the failure is fail-CLOSED (it blocks trades, never permits one), MEME_TRADING_ENABLED is default-OFF, and the rendered reason ('no risk read (fail-closed)') is honest rather than a false all-clear. The cost is a permanently dead feature slice, not lost funds.

**Evidence**:

```
bot/core/meme_preflight.py:112-116 — every production plan is built without radar_risk
    plan = meme_executor.plan_swap(
        intent={"side": side, "token_mint": mint, "size_usd": size_usd},
        safety_report=assess_token(feats),
        market=market,
        envelope_authorized=auth)

bot/core/meme_gate.py:104-106 — which makes this check unconditionally false
    tier = (radar_risk or {}).get("tier")
    add("risk_tier", bool(tier is not None and tier != "extreme"),
        f"risk tier: {tier}" if tier else "no risk read (fail-closed)")

bot/web/user_gateway.py:990-995 — and therefore no transaction is ever built
    if plan.get("allowed") is not True:
        payload["build"] = None
        payload["reason"] = "plan not allowed — nothing was built"
        return web.json_response(payload)
```

## B7-15 [INFORMATIONAL] `close_stake_account` is the only instruction that does not check `StakeAccount.version` before acting on the record, breaking the layout contract the program states in its own header

- **Dimension**: contracts · **Fix class**: SAFE_AUTO_FIX · **File**: `programs/rclaw_staking/src/lib.rs:280-286, 492-501`

**Observed**: The header comment at lines 44-46 states the contract — 'The leading `version` byte exists so a future layout change is detectable rather than silently misparsed: any reader that does not recognise the value must refuse to interpret the rest of the account' — and one of the three instructions does not honour it. The instruction that does not honour it is the destructive one.

**Root cause**: The version byte and `UnsupportedAccountVersion` were added to `unstake` and `stake_inner` (both of which move tokens) and the close path, which moves only rent, was not revisited. It is nonetheless the instruction that permanently destroys a record.

**Remediation**: Add the version constraint to the `CloseStake` accounts struct. One line, no behaviour change today, and it closes the contract.

**Verifier corrections**:

- sev→INFORMATIONAL — Accurate and correctly labelled INFORMATIONAL/prospective by the finder. Worth noting alongside it that StakeAccount::RESERVED (:525-528) is 64 bytes of headroom explicitly provided so a future field can be added in place — which is exactly the scenario in which the missing guard would matter, and which strengthens the one-line fix. No severity change; it is a one-line consistency close, not a live bug.

- sev→INFORMATIONAL — No correction. Accurate, honestly scoped, correctly labelled INFORMATIONAL, and the one-line constraint is a free hardening on a program that is not deployed.

**Evidence**:

```
programs/rclaw_staking/src/lib.rs:280-286 — no version check
    pub fn close_stake_account(ctx: Context<CloseStake>) -> Result<()> {
        emit!(StakeAccountClosed {
            owner: ctx.accounts.stake_account.owner,
            mint: ctx.accounts.stake_account.mint,
        });
        Ok(())
    }

:492-501 — the accounts struct checks amount but not version
    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
        has_one = mint @ StakeError::WrongMint,
        constraint = stake_account.amount == 0 @ StakeError::StakeNotEmpty,
        close = owner,
    )]

Contrast :208-211 (unstake) which does check:
        require!(
            ctx.accounts.stake_account.version == StakeAccount::CURRENT_VERSION,
            StakeError::UnsupportedAccountVersion
        );
```

## Refuted in batch 7 (both verifiers)

- **Authority Envelope applies NO notional ceiling to `transfer`/`withdraw` — the only value-moving action in the live signing slice — while the signer's own precondition text tells the operator the envelope "caps ... every on-chain action"** — `bot/guardian/authority.py:281-294`
  - The code is quoted accurately (bot/guardian/authority.py:281-294) and the exfil branch does return before the ceiling block at :338-356. But the omission is the module's DOCUMENTED design, stated in the same file the finder read: lines 40-43 say 'Only ``trade`` is ever fund-*bounded* rather than fund-*moving-out*; withdraw and transfer move value OUT of the account and are denied unless doubly opted in', and the docstring at :22-23 defines the exfil control as 'Withdrawal is denied by default an
- **`reject_hazardous_extensions` is a DENY-list with a catch-all `_ => {}`, so any Token-2022 mint extension the pinned crate does not name — including future ones such as Pausable — is silently accepted into the stake vault** — `programs/rclaw_staking/src/lib.rs:154-180`
  - The code shape is real — programs/rclaw_staking/src/lib.rs:154-180 is verbatim, a six-arm deny match with `_ => {}` — but the DEFECT claim is unsubstantiated and its one concrete example is wrong. Cargo.toml pins anchor-lang/anchor-spl 0.30.1, whose spl-token-2022 (3.x) ExtensionType has no Pausable variant at all; Pausable arrives in a much later crate line, so the named hazard cannot be present. I looked for a currently-accepted hazard the list misses and could not name one: MintCloseAuthority
