# Raw findings from the completed audit dimensions

**Status: UNVERIFIED.** The two adversarial verifiers for each dimension
died on the session rate limit, so nothing below has been through the
refutation pass. Six items were independently verified by the lead
auditor and are written up in `verified_findings.md`; the rest are
SUSPECTED and must not be treated as confirmed.


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
