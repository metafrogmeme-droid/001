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
