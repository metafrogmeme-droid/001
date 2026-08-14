# RUNECLAW — Deep Security & Code-Quality Audit

**Date:** 2026-08-14 · **Base commit:** `5b6012d` (rebased onto `main` during the audit) · **Branch:** `claude/codebase-security-quality-audit-eeongs`  
**Scope:** full repository — 1,947 tracked files, ~330,000 LOC across Python, JavaScript, Rust and Solidity  
**Method:** static source review by 20 parallel domain auditors, every finding re-checked by an independent adversarial verifier instructed to refute it. Findings-only; no code was modified.

---

## 1. Executive Summary

### Overall risk posture: **3 / 5 — Moderate**

RUNECLAW is a genuinely well-defended codebase whose defects cluster at the *edges of its own discipline* rather than in its core. The parts that handle real money are hardened and multiply gated: the risk engine fails closed on an unreadable state file, live execution is guarded by an independent simulation veto plus a kill switch plus per-user notional caps, exchange credentials are envelope-encrypted, and the repository ships a bespoke linter (`scripts/guard_lint.py`) whose entire job is proving that guards are actually *reached*. **No CRITICAL finding was identified, and no unauthenticated path to funds, keys, or live order placement exists.** State-changing endpoints fail closed when their token is unconfigured (`api_bridge.py:381`), and the bot gateway refuses all traffic unless a ≥32-character shared secret is set (`bot/web/user_gateway.py:64`).

The four HIGH findings share one root cause, and it is the failure mode this repository's own `CLAUDE.md` predicts in writing: *"Ask which OTHER surface makes the same claim — before calling the fix done."* Three of the four are literally that. `app/routes/guardian.js:18-30` documents finding and fixing an unauthenticated dollar leak, adding a `publicSafe()` helper so "a new endpoint here cannot forget it" — but the fix was scoped to that router, and `app/routes/mcp.js:505`, a fourth equally-unauthenticated consumer of the identical data source, still returns records raw (**H2**). `bot/skills/telegram_handler.py:2138-2145` carries a comment explaining that gating commands but not their natural-language equivalents "made the paywall a spelling test", and fixes it for scan modes — twenty lines below, the generic dispatch calls `skill.execute()` with no permission check at all, so a deliberately restricted user can halt live trading by typing a sentence (**H3**). A per-IP rate limiter carefully engineered to resist header spoofing is inverted by one deploy flag (**H1**).

The systemic weakness is **assurance coverage, not code quality**. CI gates stop at the `bot/` boundary: `app/` — 93,580 LOC, 83 route modules, 248 endpoints, the single largest attack surface — has no SAST, no dependency audit, and no linter anywhere in either pipeline (**M3**), and `api_bridge.py`, the container's default command, is excluded from bandit (**M1**). `pip-audit` scans a 10-package lock file while the image installs a different, larger set (**M2**). The result is that the strongest engineering in the repository is concentrated exactly where the tooling looks, and the thinnest coverage sits exactly where the most user-facing HTTP code lives.

Nothing here suggests a compromised or careless codebase. The remediation path is short: **four HIGH findings are all small, local fixes**, and together they close the only issues that are both remotely reachable and materially harmful.

### Findings by severity

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 4 |
| MEDIUM | 21 |
| LOW | 41 |
| INFO | 17 |
| **Total** | **83** |

Of these, **82 are open** against current `main`; **1** was fixed upstream while the audit was running and is retained for the record (M7).

**By pillar:** 50 security · 33 code-quality/architecture  
**By confidence:** 66 Confirmed · 10 Likely · 7 Suspected  
**Verification:** 85 candidate findings were produced; each was independently re-examined by a verifier instructed to default to *refuted*. One was refuted outright and several were downgraded (for example, the `telegram_handler.py:12469` direct `create_order`, initially flagged as an ungated live-order bypass, was confirmed on inspection to be a live-mode close-only fallback scoped to the caller's own executor, and was downgraded to **L30**).

**A note on the moving base.** The audit began at `aa43913` and was rebased onto `main` before publication. Two commits landed in between (`fa137e2`, `6008286`) and one of them fixed a finding this audit had already recorded — M7, the silent degradation to an in-memory database. Every `app/db.js` finding was re-verified against the new base: M7 is fixed and is marked as such, M8's anchor moved to `app/db.js:1739`, and I3 is unchanged. All other findings were unaffected by those commits.

---

## 2. Architecture & Attack Surface Map

### 2.1 Components

| Component | Size | Runtime | Role |
|---|---|---|---|
| `bot/` | 117,205 LOC Python, 21 subpackages | `python -m bot.main --mode telegram` | Trading engine, risk, compliance, guardian custody, Telegram surface |
| `app/` | 93,580 LOC JS, 83 routes / 107 libs / 248 endpoints | Node 20 + Express 4.21 | Public website, dashboard, arena, MCP server, web3 reads |
| `api_bridge.py` | 1,067 LOC, 19 FastAPI routes | uvicorn (container CMD) | HTTP bridge to the engine |
| `bot/web/` | aiohttp `:8080`, ~55 gateway routes | in-process with the bot | `/gateway/*` — the web app's channel into the engine |
| `dashboard_api.py` | 213 LOC | stdlib `ThreadingHTTPServer :9090` | Snapshot/static server |
| `programs/rclaw_staking` | 633 LOC Rust | Anchor 0.30.1 / Solana | Staking program (devnet, unaudited) |
| `contracts/rune` | 209 LOC Solidity | Base | Soulbound ERC-5192 signup NFT |
| `token/` | 6,875 LOC `.mjs` | Node | SPL Token-2022 mint + presale tooling (devnet) |
| `tests/` + `app/test/` | 90,761 LOC / 593 + 345 files | pytest gate / `node:test` | Guard, honesty and scenario suites |

### 2.2 Network exposure

Only nginx is host-exposed (`docker-compose.yml`, ports 80/443). Everything else is reachable through it or on the internal Docker network.

```
Internet ──► nginx :443 (TLS)
              ├─ /            → static site
              ├─ /api/*       → api_bridge:8000   (FastAPI, 19 routes)
              └─ /gateway/*   → runeclaw-bot:8080 (aiohttp, ~55 routes, X-Gateway-Secret)

Telegram ────► bot process (long-poll, ~150 commands + free-text NLP + photo)
Express app ─► bot gateway  (WEB_GATEWAY_SECRET)
Bot ─────────► Express app  (BOT_SYNC_SECRET, /api/bot/sync, 26 endpoints)
```

### 2.3 Trust boundaries and untrusted input

| # | Boundary | Entry points | Authentication |
|---|---|---|---|
| 1 | Anonymous internet → Express | 31 fully public route files incl. `/mcp` JSON-RPC | none (rate limit only) |
| 2 | Authenticated user → Express | 44 route files behind `authMiddleware` | JWT bearer, 30-day, localStorage |
| 3 | Anonymous internet → FastAPI | `/health`, `/scan`, `/portfolio`, `/risk/status` | none |
| 4 | Operator → FastAPI | `/confirm`, `/risk/halt`, `/portfolio/close`, `/analyze` | `DASHBOARD_TOKEN` bearer, fail-closed |
| 5 | Express → bot gateway | `/gateway/*` | `WEB_GATEWAY_SECRET` (≥32 chars, fail-closed); **identity is a caller-supplied `telegram_id` field** (M11) |
| 6 | Bot → Express | `/api/bot/sync/*` | `BOT_SYNC_SECRET`, `timingSafeEqual` |
| 7 | Telegram user → bot | ~150 commands, inline callbacks, photos, **free text → LLM → skill dispatch** | env allowlists + role permissions (**bypassable via free text — H2**) |
| 8 | Exchange / LLM / RPC → bot | ccxt responses, Bitget WS frames, LLM completions, chain reads | none — responses parsed into trading state |

### 2.4 Data flow — the money path

```
Telegram /buy | POST /gateway/trade/confirm | POST /confirm
        │
        ▼
engine.confirm_trade()                       bot/core/engine.py:5074
        ├─ risk_for(user).evaluate()         bot/risk/risk_engine.py:842
        ├─ strategy / authority / policy gates
        ├─ kill-switch re-check under lock   engine.py:5767   ← TOCTOU window (M8)
        └─ venue-auth fail-closed            engine.py:5784
                │
                ▼
LiveExecutor.execute()                       bot/core/live_executor.py:2576
        ├─ _preflight_check()                :2694  (per-trade, total-exposure, per-user caps)
        ├─ if not CONFIG.is_live(): BLOCKED  :2703
        ├─ simulation hard veto              engine.py:4993
        └─ exchange.create_order()           :3402 / :3496
```

Three independent switches must all permit a live order: `LIVE_TRADING_ENABLED=true`, `SIMULATION_MODE=false`, and a non-empty `telegram.chat_id` (`bot/config.py:2343-2368`). Paper trading is the default in every one of them.

### 2.5 Secrets inventory

| Secret | Purpose | Storage |
|---|---|---|
| `BITGET_API_KEY/SECRET/PASSPHRASE` | Exchange trading | env → Fernet vault |
| `WEB_CREDS_KEY` | AES-256-GCM envelope for user exchange keys | env |
| `RUNECLAW_SECRETS_KEY` | Fernet master key | env, else auto-generated **beside the ciphertext** (M7) |
| `JWT_SECRET` | Web session signing | env, else derived from `BOT_SYNC_SECRET` (L29) |
| `BOT_SYNC_SECRET` / `WEB_GATEWAY_SECRET` | Service-to-service | env |
| `DASHBOARD_TOKEN` / `DIAG_TOKEN` / `MCP_AUTH_TOKEN` | Operator endpoints | env |
| `WEB3_SIGNER_PRIVATE_KEY` | EVM transaction signing | env |
| `TELEGRAM_BOT_TOKEN`, LLM provider keys | Bot / inference | env, sqlite for per-user BYOK |

---

## 3. Findings Register

| ID | Severity | Category | Location | Title | Confidence |
|---|---|---|---|---|---|
| [H1](#h1) | HIGH | spoofable-rate-limit | `api_bridge.py:134` | X-Forwarded-For spoofing defeats the API rate limiter and per-IP auth lockout | Confirmed |
| [H2](#h2) | HIGH | public-data-exposure | `app/routes/mcp.js:505` | Unauthenticated MCP get_flight_record serves raw dollar-carrying flight records | Confirmed |
| [H3](#h3) | HIGH | broken-access-control | `bot/skills/telegram_handler.py:2180` | Free-text NLP dispatch bypasses the role-permission gate, letting a restricted user trip the global kill-switch | Confirmed |
| [H4](#h4) | HIGH | authorization-scope | `bot/utils/user_store.py:50` | Default 'trader' role can globally clear the operator's live safety circuit breaker and kill switch | Confirmed |
| [M1](#m1) | MEDIUM | sast-coverage | `.github/workflows/ci.yml:56` | bandit is scoped to bot/ at high/high only; the container's default CMD (api_bridge.py) gets no SAST | Confirmed |
| [M2](#m2) | MEDIUM | dependency-audit-coverage | `.github/workflows/ci.yml:76` | pip-audit covers only requirements.lock; packages actually installed in the image and in CI are audited nowhere | Confirmed |
| [M3](#m3) | MEDIUM | sast-sca-coverage | `.github/workflows/ci.yml:462` | app/ — the largest HTTP surface — has no SCA, no SAST and no lint anywhere in CI | Confirmed |
| [M4](#m4) | MEDIUM | broken-ci-gate | `.gitlab-ci.yml:37` | GitLab fallback pipeline installs a requirements.txt that does not exist — all seven Python gates abort in before_script | Confirmed |
| [M5](#m5) | MEDIUM | broken-ci-gate | `.gitlab-ci.yml:92` | test:token-tooling never enters token/, globs root paths that do not exist, and omits the npm advisory ratchet | Confirmed |
| [M6](#m6) | MEDIUM | resource-exhaustion | `api_bridge.py:308` | Unauthenticated /scan accepts an unbounded symbols list and per-symbol limit, each hitting the exchange | Confirmed |
| [M7](#m7) | MEDIUM | silent-fallback | `app/db.js:52` | Production silently degrades to an ephemeral in-memory store ✅ **fixed upstream** | Confirmed |
| [M8](#m8) | MEDIUM | schema-drift | `app/db.js:1739` | ALTER TABLE errors swallowed indiscriminately, then masked by a tables-only fast path | Likely |
| [M9](#m9) | MEDIUM | correctness-honesty | `app/routes/mcp.js:403` | get_track_record counts unpriced trades as break-even, diverging from the /track page it mirrors | Confirmed |
| [M10](#m10) | MEDIUM | information-disclosure | `app/routes/mcp.js:879` | MCP tools/call returns raw internal error messages to unauthenticated callers | Confirmed |
| [M11](#m11) | MEDIUM | honesty-unreadable-as-measurement | `app/routes/portfolio.js:66` | operatorPortfolio uses the repo's banned unreadable-as-zero shapes over a nullable pnl column | Likely |
| [M12](#m12) | MEDIUM | error-handling-honesty | `app/routes/signals.js:104` | Public /api/signals/analytics renders a DB outage as measured zeros (and / as an empty stream) | Confirmed |
| [M13](#m13) | MEDIUM | error-handling-honesty | `app/routes/sync.js:242` | portfolio-summary DB failure is swallowed and served identically to 'no portfolio yet' | Confirmed |
| [M14](#m14) | MEDIUM | csp-xss-mitigation | `app/server.js:153` | CSP allows 'unsafe-inline' scripts while the JWT lives in localStorage — any single XSS becomes full session theft | Confirmed |
| [M15](#m15) | MEDIUM | token-lifetime-escalation | `bot/api/auth_routes.py:316` | GET /auth/me mints a fresh 7-day refresh token from a 1-hour access token | Confirmed |
| [M16](#m16) | MEDIUM | revocation-bypass | `bot/api/token_store.py:101` | JWT revocation is not durable — logout/reuse-guard silently lost across a Redis blip or restart | Confirmed |
| [M17](#m17) | MEDIUM | key-at-rest | `bot/core/exchange_credentials.py:95` | Fernet master key is always written into data/ beside the ciphertext it protects, even when supplied via env | Confirmed |
| [M18](#m18) | MEDIUM | toctou-fail-open | `bot/core/live_executor.py:3402` | Kill switch not re-checked at order submission — halt race can still place a live order | Likely |
| [M19](#m19) | MEDIUM | idempotency-gap | `bot/core/live_executor.py:6288` | Market-fallback OPEN order skips the idempotency key, risking an orphaned naked position | Likely |
| [M20](#m20) | MEDIUM | audit-integrity | `bot/utils/audit_chain.py:200` | Audit chain is a keyless hash chain whose Ed25519 anchor is computed then thrown away | Confirmed |
| [M21](#m21) | MEDIUM | authentication-authorization | `bot/web/user_gateway.py:3069` | Caller-asserted identity: gateway is a single-secret confused deputy with no per-user or admin identity binding | Likely |
| [L1](#l1) | LOW | documentation-accuracy | `.github/workflows/ci.yml:411` | npm advisory backlog described as '1 critical and 15 high' in two comments while the baseline recorded the same day says 0 critical / 9 high | Confirmed |
| [L2](#l2) | LOW | broken-ci-gate | `.gitlab-ci.yml:62` | lint:mypy invokes a nonexistent scripts/mypy_gate.py and falls through to a differently-scoped fallback | Confirmed |
| [L3](#l3) | LOW | build-reproducibility | `Dockerfile:17` | Unpinned pip installs undercut the Dockerfile's 'reproducible, tamper-evident' claim, and no image scan runs anywhere | Confirmed |
| [L4](#l4) | LOW | gate-bypass | `Makefile:56` | `make test` runs bare pytest, bypassing the ci_test_gate baseline CLAUDE.md forbids substituting | Confirmed |
| [L5](#l5) | LOW | secret-exposure | `Makefile:75` | `make health` puts DASHBOARD_TOKEN in a curl command line for an endpoint that requires no auth | Confirmed |
| [L6](#l6) | LOW | session-management | `app/auth.js:45` | 30-day JWT stored in localStorage under unsafe-inline CSP magnifies any XSS into month-long account takeover | Likely |
| [L7](#l7) | LOW | replay | `app/auth.js:51` | Telegram Login Widget payloads accepted for 24h with no single-use enforcement | Likely |
| [L8](#l8) | LOW | denial-of-service | `app/auth.js:141` | Per-account failed-login lockout allows targeted account lockout DoS | Confirmed |
| [L9](#l9) | LOW | user-enumeration | `app/auth.js:544` | Login timing side-channel enables account enumeration | Confirmed |
| [L10](#l10) | LOW | token-leakage | `app/auth.js:1467` | OAuth callback returns the full session including JWT as base64 in the URL fragment | Confirmed |
| [L11](#l11) | LOW | ssrf | `app/lib/ens.js:68` | ENS getAvatar may fetch an attacker-influenced URL server-side (self-linked-wallet SSRF primitive) | Suspected |
| [L12](#l12) | LOW | crypto-merkle | `app/lib/sealroot.js:36` | Merkle tree has no leaf/internal-node domain separation (second-preimage shape) | Confirmed |
| [L13](#l13) | LOW | inconsistent-escaper | `app/public/js/app.js:95` | Shared esc() in app.js omits single-quote escaping, diverging from the three other escaper copies | Confirmed |
| [L14](#l14) | LOW | race-condition | `app/routes/arena.js:691` | Non-atomic read-modify-write on arena balance allows lost updates and duplicate close records | Confirmed |
| [L15](#l15) | LOW | correctness | `app/routes/market.js:132` | Candle cache key omits the caller-supplied `limit`, so a request can be served the wrong candle count | Confirmed |
| [L16](#l16) | LOW | input-validation | `app/routes/mcp.js:53` | MCP 64KB body cap enforced via Content-Length only; absent on /api/tool/invoke | Confirmed |
| [L17](#l17) | LOW | correctness | `app/routes/public_duel.js:54` | Duel picks filtered with an ISO 'Z'-suffixed string literal against a DATETIME column | Suspected |
| [L18](#l18) | LOW | rate-limiting-dos | `app/routes/roots.js:61` | Public /api/roots/verify/:day triggers live outbound RPC per request with no rate limit | Confirmed |
| [L19](#l19) | LOW | public-data-exposure | `app/routes/signals.js:30` | Latent dollar-P&L channel on public signal surfaces (pnl / net_pnl emitted raw) | Likely |
| [L20](#l20) | LOW | error-handling-honesty | `app/routes/since.js:46` | 'While you were away' digest renders DB failures as zero counts | Confirmed |
| [L21](#l21) | LOW | rate-limiting-dos | `app/routes/stream.js:32` | SSE stream has a global 500-connection cap but no per-IP cap | Confirmed |
| [L22](#l22) | LOW | atomicity | `app/routes/sync.js:285` | Full portfolio sync deletes all trades before inserting, with no transaction | Confirmed |
| [L23](#l23) | LOW | correctness-honesty | `app/routes/track.js:215` | Public track-record recent_trades renders an unpriced close as a measured 'flat' | Confirmed |
| [L24](#l24) | LOW | correctness-honesty | `app/routes/track.js:270` | Public replay-trade classifies a measured break-even close as a 'win' | Confirmed |
| [L25](#l25) | LOW | key-management | `app/server.js:52` | JWT signing key derived from BOT_SYNC_SECRET reuses one secret across trust boundaries | Confirmed |
| [L26](#l26) | LOW | prompt-injection | `bot/config.py:569` | Guardian prompt-injection firewall detects but never blocks by default | Confirmed |
| [L27](#l27) | LOW | file-permissions | `bot/core/exchange_credentials.py:110` | Master key file created with default umask then chmod'd, leaving a world/group-readable window | Confirmed |
| [L28](#l28) | LOW | network-exposure | `bot/main.py:429` | Dashboard (and secret-gated /gateway) binds 0.0.0.0 by default; unauthenticated /metrics reachable on all interfaces | Confirmed |
| [L29](#l29) | LOW | authn-authz | `bot/skills/telegram_handler.py:2409` | Empty allowlist opens all privileged commands to any Telegram user (paper/demo deployments) | Confirmed |
| [L30](#l30) | LOW | fail-open-close | `bot/skills/telegram_handler.py:12469` | Untracked-position close is a plain (non-reduceOnly) market order that can flip into fresh exposure | Suspected |
| [L31](#l31) | LOW | failure-honesty | `bot/token/tier_gate.py:485` | Unparseable StakeAccount renders as a measured 0.0 stake, denying the staker | Confirmed |
| [L32](#l32) | LOW | authorization-bypass | `bot/web/user_gateway.py:3099` | web3/sign bypasses the Authority Envelope 24h spend cap (hardcoded spent_today_usd=0.0) | Confirmed |
| [L33](#l33) | LOW | cors | `dashboard_api.py:22` | Hardcoded third-party origin permanently allowed by dashboard CORS | Confirmed |
| [L34](#l34) | LOW | path-traversal | `dashboard_api.py:69` | Static-file prefix check lacks a trailing separator (sibling-directory prefix match) | Suspected |
| [L35](#l35) | LOW | ungated-execution-path | `micro_trade_test.py:87` | Committed script places real production orders bypassing every safety gate | Confirmed |
| [L36](#l36) | LOW | header-inheritance | `nginx.conf:46` | nginx location blocks with add_header discard the server-level security headers | Confirmed |
| [L37](#l37) | LOW | untested-security-control | `programs/rclaw_staking/src/lib.rs:129` | The Token-2022 leg, including reject_hazardous_extensions, has never been executed by any test | Confirmed |
| [L38](#l38) | LOW | weak-supply-chain-gate | `scripts/cargo_audit_gate.py:166` | RustSec ratchet compares advisory IDs only — a dev-only advisory moving into the shipped tree keeps the gate green | Confirmed |
| [L39](#l39) | LOW | cross-language-contract | `tests/test_token_tier_gate.py:762` | StakeAccount::RESERVED is unpinned in both languages; changing it alone keeps CI green and zeroes every staker | Confirmed |
| [L40](#l40) | LOW | process-supervision | `watchdog.sh:15` | watchdog.sh SIGKILLs the bot with no SIGTERM and reports a restart as successful without verifying it survived | Confirmed |
| [L41](#l41) | LOW | documentation-accuracy | `website/index.html:9` | Public site advertises '23 fail-closed risk checks'; the manifest has 21 and SECURITY.md says only 16 are fail-closed | Confirmed |
| [I1](#i1) | INFO | secrets-hygiene | `.gitignore:69` | .gitignore lists ollama/ and AUDIT_REPORT*.md while both are tracked, so the rules are inert for them | Confirmed |
| [I2](#i2) | INFO | vulnerable-dependency | `Cargo.lock:1419` | Both shipped-tree RustSec advisories are present in Cargo.lock but unreachable from this program's code | Confirmed |
| [I3](#i3) | INFO | transport-security | `app/db.js:39` | Connection pool built from raw DATABASE_URL with no explicit TLS or pool limits | Suspected |
| [I4](#i4) | INFO | canonicalization-divergence | `app/lib/canonical.js:21` | Key sort uses UTF-16 code units, Python twin sorts by code point | Suspected |
| [I5](#i5) | INFO | resource-exhaustion | `app/lib/http_cache.js:22` | Outbound response bodies are accumulated with no size cap | Confirmed |
| [I6](#i6) | INFO | input-validation | `app/routes/mcp.js:908` | validateArgs ignores minimum/maximum/minLength bounds the tool schemas advertise | Confirmed |
| [I7](#i7) | INFO | authorization-defense-in-depth | `app/routes/web3_execute.js:62` | On-chain signing/broadcast and contract-deploy endpoints carry no web-tier authz and no 2FA step-up | Suspected |
| [I8](#i8) | INFO | unsafe-default | `bot/config.py:326` | Daily-loss circuit breaker auto-resumes trading at UTC rollover without human acknowledgement (default ON) | Likely |
| [I9](#i9) | INFO | signature-malleability | `contracts/rune/RuneOfEntry.sol:79` | _recover accepts malleable (high-s) signatures and any v >= 27 without validation | Confirmed |
| [I10](#i10) | INFO | spec-compliance | `contracts/rune/RuneOfEntry.sol:112` | supportsInterface advertises ERC-721 while getApproved never reverts and the two Approval events are absent from the ABI | Confirmed |
| [I11](#i11) | INFO | test-coverage | `contracts/rune/test/rune.test.mjs:174` | The 'no admin surface' guarantee is enforced by a function-name regex, not by the property | Confirmed |
| [I12](#i12) | INFO | test-coverage | `contracts/rune/test/rune.test.mjs:196` | The 'no token is an empty staff' invariant is asserted against two seeds that cannot reach the branch that guarantees it | Confirmed |
| [I13](#i13) | INFO | deploy-config | `nginx.conf:25` | nginx.conf ships unsubstituted YOUR_DOMAIN placeholders with no substitution step | Confirmed |
| [I14](#i14) | INFO | csp | `nginx.conf:37` | nginx CSP allows 'unsafe-inline' scripts and an unused third-party CDN origin | Confirmed |
| [I15](#i15) | INFO | input-validation | `programs/rclaw_staking/src/lib.rs:163` | Mint-extension screening is a denylist with a silent catch-all; MintCloseAuthority is not enumerated | Likely |
| [I16](#i16) | INFO | correctness | `programs/rclaw_staking/src/lib.rs:185` | stake validates the account version only after reading a field whose offset the version determines | Confirmed |
| [I17](#i17) | INFO | version-pinning | `pyproject.toml:28` | Three different cryptography floors across four dependency files; requirements-ci.txt does not meet its own pinning claim | Confirmed |

---

## 4. Detailed Findings

### HIGH findings

<a id="h1"></a>

#### H1 — X-Forwarded-For spoofing defeats the API rate limiter and per-IP auth lockout

**Severity:** HIGH · **Confidence:** Confirmed · **Pillar:** security · **Category:** spoofable-rate-limit  
**Location:** `api_bridge.py:134` · also `docker-compose.yml:67` · `nginx.conf:66` · `bot/api/auth_routes.py:60`
  
> *Found independently by two separate audit domains (application code and deploy configuration).*

**Description**  
Production runs uvicorn with `--forwarded-allow-ips='*'` (docker-compose.yml:67, --workers 1, --proxy-headers) behind nginx, which sets `X-Forwarded-For $proxy_add_x_forwarded_for` (nginx.conf:66, and again at :104). `$proxy_add_x_forwarded_for` appends nginx's observed peer to whatever XFF the external client sent, so an attacker sending `X-Forwarded-For: 9.9.9.9` reaches uvicorn as `9.9.9.9, <real-ip>`. uvicorn>=0.30 (required per pyproject.toml:21 / requirements-ci.txt:18) uses `_TrustedHosts.get_trusted_client_host`, whose always_trust branch (triggered by `*`) returns the LEFTMOST XFF entry — the attacker value — and rewrites scope['client']. So `request.client.host` is attacker-controlled before the app runs. api_bridge.py `_client_ip` (line 134) returns `request.client.host` because TRUSTED_PROXY is unset (no TRUSTED_PROXY in .env.example or compose), feeding `_check_rate_limit` (30/min). auth_routes.py:60 keys the per-IP failed-login throttle/lockout on `request.client.host` DIRECTLY, bypassing even the RC-AUD-012 mitigation. The RC-AUD-012 XFF handling is moot because uvicorn already poisoned client.host.

Independently corroborated from the deploy-config side: `docker-compose.yml:67` launches uvicorn with `--proxy-headers --forwarded-allow-ips='*'`. uvicorn's ProxyHeadersMiddleware in always-trust mode returns the LEFTMOST X-Forwarded-For entry and overwrites `scope["client"]`, while nginx appends the real peer on the RIGHT (`$proxy_add_x_forwarded_for`, nginx.conf:66 and :104). The leftmost entry is therefore whatever the external client sent, so `request.client.host` is attacker-controlled before any application code runs. This inverts the RC-AUD-012 anti-spoof design in `_client_ip()`, whose documented safe default is to return `request.client.host` unchanged when `TRUSTED_PROXY` is unset — and `TRUSTED_PROXY` is set nowhere in the repository.

**Evidence**  
api_bridge.py:134 `direct = request.client.host if request.client else "unknown"`; :135-136 returns direct when _TRUSTED_PROXIES empty; docker-compose.yml:67 `--forwarded-allow-ips='*'`; nginx.conf:66/104 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`; auth_routes.py:60 `ip = request.client.host if request.client else "unknown"`; TRUSTED_PROXY absent from .env.example.

**Impact**  
An attacker rotating X-Forwarded-For gets a fresh rate-limit/lockout bucket per request, bypassing the 30/min API limiter and the per-IP auth throttle. Enables credential-stuffing on /auth/login and /auth/register (only the RC-AUD-026 per-email lockout at auth_routes.py:50-55 survives, and it does not cover registration), and unbounded abuse of unauthenticated exchange-hitting endpoints (/scan, /patterns, etc.).

**Remediation**  
Restrict `--forwarded-allow-ips` to the nginx container's real address / docker network CIDR so uvicorn only honors XFF from the trusted hop, and have nginx pass one trusted header (X-Real-IP with real_ip_recursive) rather than appending client-supplied XFF. Key both limiters on that trusted value. Concretely: replace `'*'` in docker-compose.yml:67 with the nginx container IP/subnet, or drop `--proxy-headers` and set `TRUSTED_PROXY` so `_client_ip()` performs its rightmost-untrusted walk. Add `TRUSTED_PROXY` to .env.example and a test asserting a forged leftmost XFF does not change the rate-limit bucket.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read api_bridge.py:100-170 (limiter + _client_ip: TRUSTED_PROXY unset returns raw request.client.host), auth_routes.py:44-80 (per-IP lockout keyed on request.client.host directly), docker-compose.yml:60-70 (--forwarded-allow-ips='*', --workers 1), nginx.conf:58-70/103-104 ($proxy_add_x_forwarded_for appends real IP to attacker XFF), pyproject.toml/requirements-ci.txt (uvicorn>=0.30.0), and confirmed TRUSTED_PROXY is not present in .env.example. The load-bearing external fact is uvicorn's documented always_trust behavior returning the leftmost XFF host; combined with nginx appending on the right, the leftmost is fully attacker-controlled. CONFIRMED at HIGH.

</details>

---

<a id="h2"></a>

#### H2 — Unauthenticated MCP get_flight_record serves raw dollar-carrying flight records

**Severity:** HIGH · **Confidence:** Confirmed · **Pillar:** security · **Category:** public-data-exposure  
**Location:** `app/routes/mcp.js:505`

**Description**  
get_flight_record (mcp.js:490-508) returns flight records verbatim: `records: all.slice(0, limit)` (505) and `{ record: rec || null, chain }` (496), with no sanitizeRecord/scrub. Records are the exact objects stored by POST /api/bot/sync/flight verbatim (sync.js:810,817) and returned raw by getLatestFlight (sync.js:1143-1152). They carry dollar fields: flight_recorder.py builds size_usd (:224) and pnl_usd (:246).

**Evidence**  
Read mcp.js:499-507 (raw records, no scrub) vs public_flight.js:42 `records.map(sanitizeRecord)` and lib/flight.js header (lines 8-11) stating the full dollar-carrying record is reserved for the authed view and the public view strips every dollar figure. sync.js:810 stores body.records unmodified; getLatestFlight (sync.js:1144) returns them raw. /mcp is mounted at server.js:349 with no auth (only the router's own rate limiter, mcp.js:46). Same registry is also reachable via POST /api/tool/invoke (tool8257.js:25,54).

**Impact**  
Any anonymous caller reads the operator's live per-decision position sizes (size_usd) and realized dollar P&L (pnl_usd), from which account scale is inferable. Direct violation of the repo's §4 public-surface rule; every sibling surface redacts for anonymous callers.

**Remediation**  
Pass every returned record (both the list path at 505 and the single-record path at 496) through sanitizeRecord from ../lib/flight, matching public_flight.js. Add a test planting size_usd/pnl_usd and asserting the MCP output carries neither.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Independently opened mcp.js:490-508, public_flight.js:1-73, lib/flight.js:1-93, sync.js:807-830/1143-1152, flight_recorder.py:212-246, and server.js:349. Confirmed the raw records contain dollar keys (public_flight sanitizes the identical getLatestFlight output, proving the fields are present), the MCP path applies no scrubber, and the endpoint is unauthenticated. Real, reachable, trivially exploitable. HIGH holds — it is unauthenticated financial-position disclosure the whole architecture exists to prevent, though it is disclosure/reconnaissance, not fund loss.

</details>

---

<a id="h3"></a>

#### H3 — Free-text NLP dispatch bypasses the role-permission gate, letting a restricted user trip the global kill-switch

**Severity:** HIGH · **Confidence:** Confirmed · **Pillar:** security · **Category:** broken-access-control  
**Location:** `bot/skills/telegram_handler.py:2180`

**Description**  
The `/halt` command is role-gated via `@guard("halt")` (telegram_handler.py:9338 -> `_guard` at 2625 -> `permission_denial(tg_id, "halt")` at 2700). The `viewer` role's permission set (user_store.py:59-64) omits "halt", so `/halt` from a viewer is correctly refused. The free-text path `_handle_message` classifies natural language via `IntentRouter.classify_rules` (2111); the rule at intent_router.py:397 maps 'halt the bot' / 'stop trading' / 'kill the bot' / 'emergency stop' to skill `halt` at confidence 1.0 (intent_router.py:490). With `intent.matched and confidence >= 0.8` (2118), execution falls through the special-cased branches (stance_ 2124, scan_modes 2136 which ARE token-gated, get_orders 2160) to the GENERIC dispatch at 2165-2180: `skill = self.registry.get(intent.skill)` then `await skill.execute(self.engine, user_id=tg_id, **intent.kwargs)` with NO `_guard` / `permission_denial` / role check anywhere between 2111 and 2180. HaltSkill is registered (skill_registry.py:3002, name="halt") and genuinely destructive (skill_registry.py:1062-1073): `engine.risk.emergency_halt(...)`, halts every per-user risk engine, clears `_pending_ideas`/`_pending_atr`, transitions to `AgentState.HALTED`. This is the exact 'gating the commands and not this made the paywall a spelling test' failure the file warns about at 2138-2140 (fixed there for scan_modes via `_token_gate_blocks`), left unfixed for the destructive `halt` intent.

**Evidence**  
telegram_handler.py:2180 `result = await skill.execute(self.engine, user_id=tg_id, **intent.kwargs)` reached for intent.skill=='halt' with no preceding permission check (verified lines 2118-2180: only stance_/scan_modes/get_orders are special-cased, halt hits the generic branch). Contrast @guard("halt") at 9338 and _guard's permission_denial at 2700. viewer role omits 'halt' at user_store.py:59-64. HaltSkill.execute destructive at skill_registry.py:1062 (emergency_halt) and 1073 (_transition to HALTED); registered at skill_registry.py:3002.

**Impact**  
An authorized, allowlisted-but-deliberately-restricted viewer (an admin `/approve <id> viewer` who was intentionally withheld halt/trade/reset — telegram_handler.py:3117) can trip the global circuit breaker and force AgentState.HALTED by typing a plain-English sentence, cancelling all pending trade ideas and halting every per-user risk engine. This is a role-authorization bypass yielding a denial-of-service on the operator's live trading by a principal the role model was configured to forbid; recovery needs an admin /reset. Precondition: the actor must already be authorized AND allowlisted (a semi-trusted insider), so it is not anonymous-remote, and the effect is recoverable — hence HIGH, not CRITICAL. The same un-gated generic dispatch also exposes other role-gated read-only skills the viewer lacks permission for (analyze_asset, run_backtest, playbook) — a lesser paywall/role bypass. NOTE: the finding's claim that the 'trade' path is also exposed is REFUTED — the manual-trade branch (2100-2107) delegates to `_cmd_trade`, which carries its own inline `await self._guard(update, "trade")` at 8935, so live/paper trade placement remains properly gated.

**Remediation**  
Before the generic dispatch at 2165-2180, resolve the intent's skill to the permission its equivalent command enforces and call `self.users.permission_denial(tg_id, perm)` (returning the standard role-denied notice on failure), mirroring the `_token_gate_blocks` already applied to scan_modes at 2142. At minimum map `halt`->'halt' (and reset->'reset', analyze->'analyze', backtest->'backtest', playbook->'playbook') so `/halt` and 'halt the bot' agree. Add a behavioural/source test asserting every NLP-dispatchable skill that maps to a guarded command carries an equivalent permission check (the guard_lint origin story the CLAUDE.md describes).

**Effort:** low-to-moderate: add a skill->permission map and a permission_denial gate at the generic dispatch site plus a regression test; no refactor of the skill registry required.

<details><summary>Verifier note</summary>

Independently read telegram_handler.py 1980-2200 (full generic dispatch path — confirmed no permission check between classify at 2111 and execute at 2180; only stance_/scan_modes/get_orders are special-cased and only scan_modes is token-gated), 2625-2709 (_guard -> permission_denial), 9338-9341 (@guard("halt")); user_store.py 30-73 (viewer perms omit halt/trade/reset/analyze/backtest/playbook); intent_router.py 397/460-498 (halt rule -> confidence 1.0); skill_registry.py 1056-1079 (HaltSkill destructive) and 3002 (registered). Also verified _cmd_trade:8935 has its own inline _guard, so the finding's 'trade' exposure claim is false and I corrected the impact accordingly. Core halt bypass CONFIRMED at stated severity/confidence.

</details>

---

<a id="h4"></a>

#### H4 — Default 'trader' role can globally clear the operator's live safety circuit breaker and kill switch

**Severity:** HIGH · **Confidence:** Confirmed · **Pillar:** security · **Category:** authorization-scope  
**Location:** `bot/utils/user_store.py:50`

**Description**  
The 'trader' role (DEFAULT_AUTO_ROLE, user_store.py:153) is granted both 'halt' and 'reset' permissions (user_store.py:47-58). The Telegram gate _guard (telegram_handler.py:2699-2705) authorizes a command purely on env-allowlist membership plus role-permission (permission_denial, user_store.py:788-807) and a 24h session-freshness check — it performs NO operator-identity check for halt/reset. _cmd_reset (@guard('reset'), telegram_handler.py:9343-9349) calls engine.reset_circuit_breaker_all() (engine.py:2146-2164), which resets the shared RiskEngine, EVERY per-user RiskEngine, AND clears the global kill switch (self._halted = False, engine.py:2152). _halted is read by the pre-execute gate at engine.py:5772, so clearing it re-arms live execution. A LIVE_TRADER_TELEGRAM_IDS principal — explicitly documented as a non-operator live user (telegram_handler.py:2368-2370) — is allowlisted (telegram_handler.py:2377-2381), auto-registered as trader (user_store.py:301-313), and therefore reaches /reset. The reset is not scoped to the caller's own account.

**Evidence**  
user_store.py:50 lists 'halt' and 'reset' in the trader permission set; permission_denial (user_store.py:793) only checks role membership; telegram_handler.py:9349 calls reset_circuit_breaker_all(); engine.py:2152 sets self._halted = False and 2158-2163 loops every per-user engine. The web gateway DELIBERATELY refuses the same action for the same default role: user_gateway.py:123-131 omits 'halt' because 'Web ids are auto-provisioned with DEFAULT_AUTO_ROLE, which holds the halt permission, so mapping it here would still let anyone who signed up ... stop trading for everybody' — the maintainers treat global halt/reset from a default-role user as a defect, but the Telegram path leaves it open.

**Impact**  
A semi-trusted, non-operator live trader (allowlisted via LIVE_TRADER_TELEGRAM_IDS) — or any admin-/approve'd user — can clear the operator's fail-closed circuit breaker and the global _halted kill switch after the risk engine deliberately halted live trading, re-arming live execution on the operator's shared account without operator action, and can force-reset every other user's per-user breaker. Directly undermines the safety guarantee the breaker exists to provide. Requires the attacker to hold a trader-role allowlist slot (a semi-trusted insider), so it is a privilege-scope defect rather than an anonymous-remote one — hence HIGH rather than CRITICAL.

**Remediation**  
Move 'halt'/'reset' out of the trader role into an operator/admin-only permission (mirroring the web gateway's deliberate omission), or gate reset_circuit_breaker_all()/emergency_halt_all() on _is_operator_user rather than the trader role. A self-scoped /reset for a trader should only touch that caller's own per-user engine, never the shared engine or _halted.

**Effort:** moderate

<details><summary>Verifier note</summary>

Read user_store.py:33-73 (trader set holds 'halt','reset'), 774-816 (permission_denial checks only role + session age, no operator check), 301-318 (register auto-approves role=trader/authorized=True). Read telegram_handler.py:539-560 (guard decorator), 2625-2718 (_guard: allowlist gate then permission_denial only), 2364-2414 (_allowlist_ids includes LIVE_TRADER_TELEGRAM_IDS; live traders documented as non-operators), 9338-9357 (_cmd_reset -> reset_circuit_breaker_all). Read engine.py:2146-2164 (resets shared + all per-user + _halted=False) and 5772 (_halted gates execute). Read user_gateway.py:110-131 confirming maintainers block this on the web path for exactly this role. Defect and reachability from an allowlisted trader confirmed; keeping HIGH.

</details>

---

### MEDIUM findings

<a id="m1"></a>

#### M1 — bandit is scoped to bot/ at high/high only; the container's default CMD (api_bridge.py) gets no SAST

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** sast-coverage  
**Location:** `.github/workflows/ci.yml:56`

**Description**  
The only SAST step is `bandit -r bot/ --severity-level high --confidence-level high -q`. api_bridge.py — the module the production image runs by default — is outside bot/, as are dashboard_api.py, scripts/ and the other root-level Python. The high/high threshold also suppresses the MEDIUM-severity checks (B608 SQL construction, B105/B106 hardcoded passwords, B113 request without timeout, B310 urlopen).

**Evidence**  
ci.yml:55-56 read directly. Dockerfile:47 `CMD ["uvicorn", "api_bridge:app", ...]`. `wc -l api_bridge.py dashboard_api.py` -> 1067 / 213; `find scripts -name '*.py' | xargs wc -l` -> 5498 total; 11 root-level .py files. .gitlab-ci.yml:106 repeats the identical invocation.

**Impact**  
The FastAPI bridge that holds the bearer-token auth, the rate limiter and /confirm, /portfolio/close, /risk/halt is never statically analysed, nor are the CI gate scripts themselves.

**Remediation**  
Extend the scan: `bandit -r bot/ api_bridge.py dashboard_api.py scripts/`, and lower to --severity-level medium behind a baseline file if the initial backlog is large.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Scope and threshold verified from ci.yml:55-56 and the entrypoint from Dockerfile:47; line counts reproduced exactly as claimed. One remediation nit: pyproject.toml declares bandit[toml] at :46 but has no [tool.bandit] section, so `-c pyproject.toml` would need one added first. The finding's aggregate '~35% of application lines' framing is arithmetic I did not independently reconstruct and it is not load-bearing — the entrypoint being unscanned is. MEDIUM stands: a coverage gap on the money-handling surface, no specific unscanned defect demonstrated.

</details>

---

<a id="m2"></a>

#### M2 — pip-audit covers only requirements.lock; packages actually installed in the image and in CI are audited nowhere

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** dependency-audit-coverage  
**Location:** `.github/workflows/ci.yml:76`

**Description**  
The only Python SCA gate is `pip-audit -r requirements.lock`. requirements.lock has 10 pins. CI installs requirements-ci.txt (30 lines, ~25 packages) and the image installs bot/requirements.txt plus fastapi/uvicorn inline. What ships is not what is audited.

**Evidence**  
ci.yml:74-76 `run: pip-audit -r requirements.lock` (read; `if: always()` on :75). ci.yml:29 `pip install -r requirements-ci.txt`. Dockerfile:16-18 `COPY bot/requirements.txt ./requirements.txt` then `RUN pip install --no-cache-dir -r requirements.txt fastapi>=0.110 "uvicorn[standard]>=0.29"`. requirements.lock:4-13 — python-dotenv, pydantic, ccxt, python-telegram-bot, openai, anthropic, numpy, aiohttp, websockets, cryptography only. bot/requirements.txt:11,13 Pillow>=10.3.0 / redis>=5.0.0. .gitlab-ci.yml:112 repeats the same narrow command.

**Impact**  
fastapi, uvicorn (+ h11/httptools/uvloop/watchfiles), Pillow and redis-py are in the deployed image and reported by no gate; the container's default CMD is a uvicorn/FastAPI server (Dockerfile:47). An advisory in the HTTP front door would never surface. anthropic==0.104.1 is in the lock and image but absent from requirements-ci.txt, so audited-set and tested-set differ in both directions.

**Remediation**  
Audit the artefact: `pip-audit -r bot/requirements.txt -r requirements-ci.txt`, or run pip-audit against the installed environment after the install step, plus a test asserting the lock is a superset of bot/requirements.txt and the Dockerfile's inline installs.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed by reading all four dependency files and both CI files line by line. One factual correction that does NOT change the verdict: PyJWT is listed only in requirements-ci.txt:19, not in bot/requirements.txt or the Dockerfile, and no Python module imports it — bot/api/auth_routes.py:222 hand-rolls create_jwt via a local _sign() (grep for `import jwt` across bot/ and api_bridge.py returns nothing). So the claim that PyJWT 'signs session tokens in the runtime image' is wrong; the shipped-but-unaudited set is fastapi, uvicorn[standard], Pillow and redis. The gap itself is real and severity MEDIUM stands.

</details>

---

<a id="m3"></a>

#### M3 — app/ — the largest HTTP surface — has no SCA, no SAST and no lint anywhere in CI

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** sast-sca-coverage  
**Location:** `.github/workflows/ci.yml:462`

**Description**  
The web-app job installs with `npm ci --no-audit --no-fund` and runs only the test suite. There is no npm audit, no advisory ratchet (token/ has one, app/ does not), no ESLint config anywhere in the repo, and no JS SAST.

**Evidence**  
ci.yml:460-462 `Install (locked)` / `working-directory: app` / `npm ci --no-audit --no-fund`; the only subsequent step in the job is `Tests` (:487 region). ci.yml comment at :464-466 calls app/ 'THE LARGEST HTTP SURFACE IN THIS REPO — 228 Express routes'. app/package.json:9-17 lists bcryptjs, ethers, express, jsonwebtoken, mysql2, qrcode, web-push with no devDependencies key. `find -maxdepth 3 -name .eslintrc* -o -name eslint.config.*` excluding node_modules -> empty. `grep -n audit ci.yml` -> pip-audit (:76), cargo-audit (:171-175), token ratchet (:424) only; :325/:440/:462 are all `--no-audit`.

**Impact**  
An advisory in jsonwebtoken, mysql2, express or ethers reaches the deployed web platform with no gate. Same gap for contracts/rune (ci.yml:440 `npm ci --no-audit`, no Solidity static analysis).

**Remediation**  
Add an app/ advisory ratchet modelled on token/scripts/audit_gate.mjs, plus an ESLint config; consider a Solidity analyser for contracts/rune before any value-bearing deployment.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Verified by reading ci.yml's app job, contracts/rune job, app/package.json in full, and by searching for any ESLint config outside node_modules (none). Not a documented tradeoff: the repo argues for the ratchet pattern at ci.yml:409-421 and applies it to token/ and cargo, so app/ is an omission rather than a considered exclusion. MEDIUM retained — a missing control, not a demonstrated vulnerability.

</details>

---

<a id="m4"></a>

#### M4 — GitLab fallback pipeline installs a requirements.txt that does not exist — all seven Python gates abort in before_script

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** broken-ci-gate  
**Location:** `.gitlab-ci.yml:37`

**Description**  
The `.python` anchor's before_script runs `pip install -q -r requirements.txt`. No requirements.txt exists at the repo root, so before_script fails and every job extending `.python` fails before its script runs.

**Evidence**  
.gitlab-ci.yml:35-38 read directly (`- pip install -q -r requirements.txt` on :37). `ls /home/user/001/requirements.txt` -> No such file or directory; root has requirements.lock and requirements-ci.txt only, plus bot/requirements.txt. Jobs extending .python: lint:ruff-hard (:43-44), lint:ruff-ratchet (:51-52), lint:mypy (:57-58), guards (:64-65), test:python (:73-74), sast:bandit (:102-103), sca:pip-audit (:108-109).

**Impact**  
The pipeline the file's own header (:3-8) describes as restoring enforcement after the GitHub account suspension provides zero Python enforcement — no ruff, mypy, guard_lint, baseline test gate, bandit or pip-audit. Fails loudly rather than silently, but a permanently-red environment failure is the 'trains people to ignore the check' dynamic the same file argues against at :11-14.

**Remediation**  
Point line 37 at requirements-ci.txt (which already carries the test tooling, making :38 redundant) and add a path-existence test over both CI files alongside tests/test_preflight_matches_ci.py.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Verified by reading .gitlab-ci.yml in full and by `ls` on the root. Could not refute: nothing generates a root requirements.txt (no Makefile target, no earlier job, no artifact), and GitLab treats a non-zero before_script as job failure. Header at :16-20 explicitly claims only cargo and solidity are unported, so this is not a documented gap. MEDIUM stands — it is CI enforcement, not a runtime vulnerability, and the alternative host's ci.yml is intact.

</details>

---

<a id="m5"></a>

#### M5 — test:token-tooling never enters token/, globs root paths that do not exist, and omits the npm advisory ratchet

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** quality · **Category:** broken-ci-gate  
**Location:** `.gitlab-ci.yml:92`

**Description**  
The job runs `npm ci` and `node --test presale/*.test.mjs scripts/*.test.mjs` from the repo root with no `cd token`. Root has no presale/ and no scripts/*.mjs; the real files are token/presale/*.test.mjs and token/scripts/*.mjs. The `rules: changes:` list is written against the same nonexistent root paths, so a change under token/ does not trigger the job.

**Evidence**  
.gitlab-ci.yml:92-99 read directly. `ls /home/user/001/presale` -> No such file or directory; `ls /home/user/001/scripts/*.mjs` -> No such file or directory; `ls token/presale/*.test.mjs` -> 12 files; `ls token/scripts/*.mjs` -> 7 files including audit_gate.mjs. Contrast ci.yml:308-424 where every token step carries `working-directory: token`, and ci.yml:422-424 runs `node scripts/audit_gate.mjs`, which .gitlab-ci.yml has no equivalent of.

**Impact**  
On the fallback host the presale/mint tooling — the code that signs privileged Solana transactions — has no test coverage, and token/'s npm advisory ratchet, cluster-identity guards and validator test are absent. The header at :16-20 lists only cargo and solidity as unported, so the file overstates its own coverage.

**Remediation**  
Add `cd token` before both commands, rewrite `rules: changes:` to `token/**/*`, and add `node scripts/audit_gate.mjs`.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Verified every path claim with ls. Two small corrections that do not change the verdict: the rules list also includes root package.json/package-lock.json, which DO exist, so the job can still fire on a root-workspace change and will then fail loudly on the unmatched globs — it is not purely dormant; and node exits non-zero on the literal unmatched glob, so this fails rather than silently passing. Kept MEDIUM because the GitLab file was created specifically as the enforcement host after the GitHub suspension (:3-8), so a gate missing there is not merely redundant.

</details>

---

<a id="m6"></a>

#### M6 — Unauthenticated /scan accepts an unbounded symbols list and per-symbol limit, each hitting the exchange

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** resource-exhaustion  
**Location:** `api_bridge.py:308`

**Description**  
POST /scan (api_bridge.py:505-506) depends only on `_require_rate_limit` — no dashboard-token auth (contrast /analyze:589, /confirm:676, /close:732 which add `require_dashboard_token`). `ScanRequest.symbols` (line 308) has no `max_length` and `limit` (line 307) is uncapped. Supplied symbols pass only `_SYMBOL_RE` (line 512-515) and each drives a live `exchange.fetch_ohlcv` via `_scan_single`→`_fetch_ohlcv` (line 543/291). Symbols are batched by 10 (line 521-523) and batches run sequentially, so N symbols = N total exchange fetches; the global Semaphore(5) bounds only concurrency, not total work. `req.limit` flows unclamped into `fetch_ohlcv(limit=...)`. lab.py:60 caps its list at `max_length=4`.

**Evidence**  
api_bridge.py:308 `symbols: list[str] | None = None`; :307 `limit: int = 100`; :506 `Depends(_require_rate_limit)` only; :521-523 sequential batches → `_scan_single` → :543 `_fetch_ohlcv(symbol, timeframe, limit)` → :291 `exchange.fetch_ohlcv(symbol, timeframe, limit=limit)`; contrast bot/api/lab.py:60 `symbols: list[str] = Field(default_factory=list, max_length=4)`.

**Impact**  
An unauthenticated client (behind the spoofable limiter of finding #1) can force thousands of sequential exchange fetches per request and arbitrarily large per-fetch candle counts, saturating the single worker's event loop and risking exchange rate-limiting/IP ban of the shared session used by the live engine.

**Remediation**  
Add `Field(max_length=...)` (e.g. 25) to ScanRequest.symbols and clamp `limit` to a sane max (e.g. 500), matching bot/api/lab.py.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read api_bridge.py:285-300 (_fetch_ohlcv), 303-308 (ScanRequest, no bounds), 505-545 (scan: rate-limit-only, sequential batches, unbounded len(symbols), limit passed through), and lab.py:55-65 (max_length=4 contrast). Confirmed unauthenticated and unbounded. CONFIRMED at MEDIUM.

</details>

---

<a id="m7"></a>

#### M7 — Production silently degrades to an ephemeral in-memory store

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** quality · **Category:** silent-fallback  
**Location:** `app/db.js:52`

> ✅ **Resolved upstream.** Fixed upstream while this audit was running, in commit fa137e2 ("M10: a production deployment that quietly became an amnesiac"). app/db.js now throws `database unavailable` instead of setting USE_MYSQL = false, and app/test/db_fails_closed.test.js pins the behaviour. Retained here because the finding was valid against the audited base commit and the fix is worth recording.

**Description**  
The DB backend is chosen solely by `USE_MYSQL = !!process.env.DATABASE_URL` (line 8). If DATABASE_URL is unset, MemoryDB is instantiated with only a console line (lines 1548-1551). When DATABASE_URL IS set but `require('mysql2/promise')` throws, the catch at lines 51-54 downgrades USE_MYSQL to false, silently switching to the in-memory store. There is no NODE_ENV/production or REQUIRE_DB assertion that forces a durable backend — I grepped db.js and found no process.exit/NODE_ENV/REQUIRE_DB guard anywhere.

**Evidence**  
Verified line 8 `let USE_MYSQL = !!process.env.DATABASE_URL;`; lines 51-54 catch on require failure set `USE_MYSQL = false`; lines 1548-1551 `if (!USE_MYSQL) { memDb = new MemoryDB(); pool = memDb; console.log('Using in-memory database (no DATABASE_URL found)'); }`. File header (lines 2-3) explicitly documents this as intended demo/dev fallback. No production assertion exists.

**Impact**  
A missing env var or broken mysql2 dependency causes the auth-bearing trading app to run against an ephemeral store: all users/trades/credentials are wiped on every restart and durability guarantees vanish while the process reports healthy — precisely the 'dead store looks like a live one' failure class the repo guards against. NOTE: the first-pass claim that 'auth succeeds against fabricated in-memory rows' is INACCURATE — MemoryDB constructs with `this.users = []` (line 61), so no login succeeds; the real harm is silent data-loss/durability, not auth bypass.

**Remediation**  
When NODE_ENV=production (or a REQUIRE_DB flag is set), treat an unset DATABASE_URL or a mysql2 require failure as a fatal boot error (process.exit non-zero) rather than falling back to MemoryDB. Reserve the in-memory shim for explicit demo/dev.

**Effort:** low

<details><summary>Verifier note</summary>

Read lines 1-70, 1540-1594, and grepped for guards. Code matches the finding exactly and no production assertion exists, so CONFIRMED. Kept MEDIUM: real operational risk aligned with the repo's core anti-silent-degradation principle, but requires a misconfiguration to trigger. Corrected the overstated 'auth against fabricated rows' impact — MemoryDB.users starts empty.

</details>

---

<a id="m8"></a>

#### M8 — ALTER TABLE errors swallowed indiscriminately, then masked by a tables-only fast path

**Severity:** MEDIUM · **Confidence:** Likely · **Pillar:** quality · **Category:** schema-drift  
**Location:** `app/db.js:1739`

**Description**  
Every back-fill ALTER in migrate() is wrapped in `try { ... } catch (e) { /* exists */ }` swallowing ALL errors, not just ER_DUP_FIELDNAME. Confirmed the base `CREATE TABLE IF NOT EXISTS users` (lines 1645-1655) contains only id/email/password_hash/plan/telegram_linked/telegram_id/link_token/link_token_expires/created_at — columns like token_epoch (line 1672), totp_secret (1676), google_id/discord_id/x_id, wallet_address, and the verify/reset tokens are added ONLY via these guarded ALTERs. Separately, schemaIsCurrent() (lines 1610-1619) short-circuits migrate() (lines 1640-1643) using `information_schema.TABLES` — it checks table existence only, never columns.

**Evidence**  
Verified line 1660-1661 and the repeated pattern through line 1773 all catch every error with `/* exists */`. Confirmed token_epoch exists only at line 1672 (ALTER) and is absent from the base CREATE at 1645-1655. schemaIsCurrent lines 1612-1615: `SELECT TABLE_NAME ... information_schema.TABLES ...; return EXPECTED_TABLES.every((t) => have.has(...))` — tables only, no column check. (Line re-confirmed at app/db.js:1739 after rebasing onto current main; the catch still swallows any error, not only duplicate-column.)

**Impact**  
A transient ALTER failure (lock-wait timeout, dropped connection mid-migration, common on serverless/TiDB) is swallowed identically to a benign duplicate-column. On the next boot all tables already exist, so schemaIsCurrent() returns true and the full DDL is skipped forever — the missing column is never created. Queries on it (e.g. token_epoch used for token revocation) then error or misbehave, and the drift is invisible. Requires a specific transient failure precisely during an ALTER, so real but not routine.

**Remediation**  
Inspect the caught error `.code` and re-raise anything that is not the expected ER_DUP_FIELDNAME / ER_DUP_KEYNAME; and/or extend schemaIsCurrent() to verify required COLUMNS (information_schema.COLUMNS) not just table names, so a partially-migrated table forces the DDL to re-run.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Read lines 1596-1773 and confirmed both halves independently: the blanket catch swallows all errors AND the fast path is table-existence only. The two combine into a real, permanent-drift reliability bug. Kept MEDIUM/Likely — mechanism is fully confirmed in code, but exploitation depends on a transient error landing exactly on an ALTER.

</details>

---

<a id="m9"></a>

#### M9 — get_track_record counts unpriced trades as break-even, diverging from the /track page it mirrors

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** quality · **Category:** correctness-honesty  
**Location:** `app/routes/mcp.js:403`

**Description**  
get_track_record uses `const pnls = trades.map(t => parseFloat(t.pnl) || 0)` (mcp.js:403) — the first banned shape. win_rate_pct uses trades.length as denominator (:410), so every unpriced close drags the rate down; recent_trades reports an unreadable pnl as 'flat' (:418-419). The public /track route it claims to mirror was already fixed: track.js classifyPnls excludes non-finite rows (:29-40), win_rate_pct is over priced.length (:201), and it publishes an unpriced count and distinguishes 'unknown' from 'flat' (:174).

**Evidence**  
Read mcp.js:398-424 and track.js:29-40/97-101/159-176/201. Query at mcp.js:398-402 filters status='CLOSED' AND closed_at IS NOT NULL but NOT pnl IS NOT NULL, so a NULL-pnl closed row reaches the map. track.js's own header (:25-27) asserts trades.pnl is DECIMAL(14,2) NULLABLE and such a row is reachable.

**Impact**  
The unauthenticated machine-readable track record understates the win rate and fabricates 'flat' outcomes whenever a close has no recorded P&L — the two public surfaces disagree the moment an unpriced close lands.

**Remediation**  
Reuse track.js classifyPnls: filter to Number.isFinite, use priced count as the win-rate denominator, publish an unpriced count, and emit 'unpriced'/'unknown' rather than 'flat'.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Verified the divergence directly: mcp.js uses the banned `|| 0` shape and trades.length denominator; track.js in the same repo was explicitly corrected to priced.length with an unpriced count. The code defect is unambiguous. Reachability of NULL pnl is asserted by the repo's own track.js header; MEDIUM (honesty defect on an agent-facing surface) holds.

</details>

---

<a id="m10"></a>

#### M10 — MCP tools/call returns raw internal error messages to unauthenticated callers

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** information-disclosure  
**Location:** `app/routes/mcp.js:879`

**Description**  
handleRpc tools/call catch (mcp.js:877-882) returns `Tool failed: ${String(e.message || e).slice(0, 200)}` raw. Handlers run pool.execute (mysql2 driver errors carry host/schema/table/SQL detail) and getGateway (errors carry the internal gateway URL). safe_error.js exists precisely for this and its docstring quotes this exact pattern, but safeErrorText was wired only into the sibling dispatcher tool8257.js:66; the /mcp path still ships the raw string.

**Evidence**  
Read mcp.js:877-882 (raw slice), tool8257.js:56-68 (routes same handlers' errors through safeErrorText with a comment naming the DB/gateway leak), and safe_error.js:1-83 (module built for this defect). /mcp unauthenticated (server.js:349). Truncation to 200 chars bounds size, not whether a leak occurs.

**Impact**  
Unauthenticated caller can drive tools into failure and harvest driver error strings, schema/table names, and internal gateway URLs — deployment reconnaissance.

**Remediation**  
Use safeErrorText(e) from ../lib/safe_error in the mcp.js:877-882 catch, matching tool8257.js:66.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Confirmed by reading all three files. The fix is demonstrably applied to the twin endpoint but not here. Reachability is real: pool.execute and getGateway both appear in handlers and their errors carry the cited internals. MEDIUM (reconnaissance, not credential/fund loss) is correct.

</details>

---

<a id="m11"></a>

#### M11 — operatorPortfolio uses the repo's banned unreadable-as-zero shapes over a nullable pnl column

**Severity:** MEDIUM · **Confidence:** Likely · **Pillar:** quality · **Category:** honesty-unreadable-as-measurement  
**Location:** `app/routes/portfolio.js:66`

**Description**  
operatorPortfolio() (the code path taken only for the operator account, userId === BOT_USER_ID, via router at portfolio.js:211-214) aggregates CLOSED trades with COALESCE(SUM(pnl),0) (line 66) and returns it as parseFloat(pnlRows[0]?.net_pnl || 0) (line 130); it computes win_rate = wins/total*100 where wins counts pnl>0 (lines 68-70,92) and total is COUNT(*) of all CLOSED rows (lines 66,91,132). trades.pnl is DECIMAL(14,2) with NO NOT NULL (db.js:1783), and a CLOSED row with NULL pnl is genuinely reachable for user 1: sync.js inserts closed rows with t.pnl uncoerced (line 300) and trade-event with trade.pnl uncoerced (line 463). These are exactly the two banned shapes in CLAUDE.md's table (SUM over unreadable rows printed as whole; wins/COUNT(*) putting unpriced rows in the denominator as non-wins). The sibling sync.js paths (lines 209-221 DB-fallback with a scored-denominator query; lines 336-342 winStats/realizedTotal) and routes/trades.js were explicitly rewritten to avoid this; operatorPortfolio never received that fix.

**Evidence**  
portfolio.js:66 'SELECT COALESCE(SUM(pnl),0) as net_pnl ... COUNT(*) as total_trades ... status = ?' ('CLOSED'); :68-70 'SELECT COUNT(*) as wins ... AND pnl > 0'; :130 total_pnl: parseFloat(pnlRows[0]?.net_pnl || 0); :132 win_rate: total > 0 ? (wins/total)*100 : null. Nullable column: db.js:1783 'pnl DECIMAL(14,2),' (no NOT NULL); contrast arena_trades 'pnl DOUBLE NOT NULL' at db.js:2164. Insertion of NULL-pnl closes for user 1: sync.js:300 (t.pnl) and sync.js:463 (trade.pnl) forward the gateway value with no || 0.

**Impact**  
The operator's own private /api/portfolio dashboard shows an unpriceable book as a confident measured net P&L and an understated win rate — unscored closes counted as break-even in the total and as non-wins in the denominator. A false-negative performance read, the exact failure class the repo's guard tests exist to prevent. Scope is the single operator account, not multi-user.

**Remediation**  
Score the raw rows the way sync.js/trades.js already do: read pnl per row, use a scored (pnl IS NOT NULL) count as the win-rate denominator, sum only priced rows, return null when all-unpriced, and surface an unpriced count. Do not COALESCE NULL pnl to 0 or use COUNT(*) of all CLOSED as the denominator.

**Effort:** moderate — replace the two aggregate queries with the scored-denominator pattern used in sync.js:215-225 and reuse trade-stats helpers

<details><summary>Verifier note</summary>

Independently read portfolio.js:58-139 (operatorPortfolio), confirmed lines 66/68-70/130/132 use the banned shapes and that this function serves only the operator (BOT_USER_ID) at line 211-214. Confirmed db.js:1783 pnl is nullable and db.js:2164 arena is NOT NULL. Traced reachability: sync.js:294-304 and :459-466 insert closed rows with the gateway's pnl uncoerced, so a NULL-pnl close reaches user 1's trades. This is not one of the repo's proven-safe patterns (the column is nullable and the write path does not filter). CONFIRMED; kept MEDIUM (single-operator private surface) and confidence Likely (defect definite; depends on the gateway actually emitting a null-pnl close, which the sibling null-handling code implies is possible).

</details>

---

<a id="m12"></a>

#### M12 — Public /api/signals/analytics renders a DB outage as measured zeros (and / as an empty stream)

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** quality · **Category:** error-handling-honesty  
**Location:** `app/routes/signals.js:104`

**Description**  
The /analytics catch returns EMPTY_ANALYTICS whose overall is {resolved:0,wins:0,losses:0,win_rate:0,net_pnl:0} (signals.js:90-91,104) — a DB failure served as HTTP 200 with a confident 0% win rate and $0 net. GET / catch returns {signals:[]} (:40). The SAME FILE's /stats handler was fixed to return 503 (:82) with a comment explaining a DB failure is not a record of zero. feed.js:51 shares the {events:[]} shape.

**Evidence**  
Read signals.js:37-106 in full. /stats at :74-83 returns res.status(503).json({error:'signal_stats_unavailable'}); /analytics at :102-105 and / at :37-41 fail soft to fabricated zeros/empty. Both endpoints are unauthenticated (server.js:290). Reachable on any pool.execute failure.

**Impact**  
During a DB incident the public analytics panel renders '0% win rate over 0 resolved' and the stream renders empty — confident negatives manufactured by an outage, indistinguishable from real data. Core honesty-rule violation, on two of three siblings after the third was fixed.

**Remediation**  
Return 503 with an error code from both catches (as /stats does); same for feed.js /recent.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Read all three handlers side by side. The inconsistency is real and self-evident within one file — /stats deliberately 503s while /analytics and / fail soft to zeros/empty. MEDIUM is appropriate; the /analytics win_rate:0/net_pnl:0 is the clearest documented bug class. (The GET / empty-stream carries a deliberate design comment, but still contradicts the file's own fixed sibling.)

</details>

---

<a id="m13"></a>

#### M13 — portfolio-summary DB failure is swallowed and served identically to 'no portfolio yet'

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** quality · **Category:** error-handling-honesty  
**Location:** `app/routes/sync.js:242`

**Description**  
The final catch of GET /api/bot/sync/portfolio-summary is `catch (err) { res.json({ portfolio: null }); }` (sync.js:241-243) — no logging, and byte-identical to the genuine empty state at :228-230. A DB outage on the fallback path renders as 'no portfolio yet' with zero diagnostic trace, unlike every other handler in the file which logs err.stack || err.message.

**Evidence**  
Read sync.js:204-244. The route is defined before router.use(botAuth) at :262, so it is public/unauthenticated. Line 228-230 returns {portfolio:null} for a real empty state; line 242 returns the identical body on exception with no console.error.

**Impact**  
Dashboard and external consumers show a real empty state during DB failures instead of an error state, and the operator gets no log signal. Honesty-rule violation on a public endpoint plus a swallowed exception.

**Remediation**  
Log the error and return 503 with an error body (mirroring signals.js /stats), or at minimum a distinguishing {portfolio:null, unavailable:true} plus console.error.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Confirmed the catch swallows without logging and returns a body identical to the honest-empty branch; confirmed the route sits above botAuth so it is public. Every sibling catch in this file logs err.stack. MEDIUM holds — the missing log plus manufactured empty-state on a public surface. Borderline LOW, but the no-diagnostic-trace aspect keeps it at MEDIUM.

</details>

---

<a id="m14"></a>

#### M14 — CSP allows 'unsafe-inline' scripts while the JWT lives in localStorage — any single XSS becomes full session theft

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** csp-xss-mitigation  
**Location:** `app/server.js:153`

**Description**  
The CSP script-src is "'self' 'unsafe-inline' https://telegram.org" (server.js:153), so CSP provides no backstop against injected inline script. The session bearer token is read from localStorage (app.js:11-18 resolveToken) and attached as Authorization: Bearer (app.js:37-39 authHeaders), making it JS-readable. No CSP directive restricts top-level navigation, so token exfil via location.href to an external origin is not blocked even though connect-src is 'self' blob:. This is a defense-in-depth weakness that compounds any XSS, not an active exploit on its own.

**Evidence**  
Read server.js:151-167: CSP array has "script-src 'self' 'unsafe-inline' https://telegram.org" (line 153), "connect-src 'self' blob:" (line 160), no navigate-to/no directive blocking outbound navigation. Read app.js:10-18: resolveToken() reads localStorage 'token' then rc_session.token. app.js:37-39: authHeaders returns { Authorization: 'Bearer ' + TOKEN }. The comment at server.js:146-150 explicitly acknowledges the pages use inline script/style, confirming this is an intentional trade-off, not an accident.

**Impact**  
If any of the app's innerHTML sinks is ever reached with unescaped attacker data, unsafe-inline lets it execute and read the bearer token from localStorage, exfiltrating via navigation to fully hijack an authenticated crypto-trading session. Requires a separate XSS to exist first; this finding is the missing second layer, so real-world impact is conditional.

**Remediation**  
Remove 'unsafe-inline' from script-src via nonces/hashes on the app's inline scripts, and/or move the session token to an HttpOnly, Secure, SameSite cookie so JS cannot read it. Either measure independently breaks the XSS-to-session-theft chain.

**Effort:** high

<details><summary>Verifier note</summary>

Verified all cited lines directly. server.js:153 contains 'unsafe-inline'; app.js:11 and :37-38 confirm token in localStorage and Bearer attachment. Confirmed no CSP directive restricts top-level navigation. Facts are exactly as reported. This is a legitimate, well-recognized defense-in-depth gap. Kept MEDIUM: it is a mitigation weakness contingent on a separate XSS, not directly exploitable, so not HIGH. Verdict CONFIRMED.

</details>

---

<a id="m15"></a>

#### M15 — GET /auth/me mints a fresh 7-day refresh token from a 1-hour access token

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** token-lifetime-escalation  
**Location:** `bot/api/auth_routes.py:316`

**Description**  
`me()` (auth_routes.py:316-323) is gated only by get_current_user_id (a valid access token), yet it mints BOTH a new access token AND a new refresh token via `create_jwt(user_id, token_type="refresh")` (JWT_REFRESH_TTL = 7 days, line 140) and returns them. Unlike /refresh (line 348-350) which enforces rotation + single-use replay detection via `_check_and_record_refresh`, /me applies no such control and can be called repeatedly to mint unlimited fresh 7-day refresh tokens. No epoch bump revokes the old tokens, so this is pure additional long-lived-token minting, over a GET (more likely to be cached/logged).

**Evidence**  
auth_routes.py:316-323 `token = create_jwt(user_id, token_type="access"); refresh = create_jwt(user_id, token_type="refresh"); return _user_response(user_id, token, refresh)`; :139-140 JWT_ACCESS_TTL=1h, JWT_REFRESH_TTL=7d; :340-350 /refresh applies reuse detection that /me skips.

**Impact**  
A stolen/leaked 1-hour access token can be upgraded into a 7-day refresh token via a single GET /auth/me, converting a short compromise window into week-long persistence and side-stepping the rotation/reuse-detection design.

**Remediation**  
Make /auth/me read-only — return user info with no create_jwt calls (or echo the presented access token only). Mint refresh tokens exclusively in /login, /register, and /refresh.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read auth_routes.py:139-140 (TTLs), 300-323 (/me mints access+refresh under get_current_user_id only), 332-350 (/refresh's reuse-detection which /me lacks). Confirmed /me issues a full 7-day refresh token on every call with no rotation control. CONFIRMED at MEDIUM.

</details>

---

<a id="m16"></a>

#### M16 — JWT revocation is not durable — logout/reuse-guard silently lost across a Redis blip or restart

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** revocation-bypass  
**Location:** `bot/api/token_store.py:101`

**Description**  
TokenStore 'fails toward availability': when Redis is configured but a call raises, `bump_epoch` (line 101-109) and `try_consume_jti` (111-125) fall back to the per-process dict/set. If a logout/epoch-bump or a refresh-consume is recorded in-process during a Redis outage, once Redis recovers `get_epoch` (91-99) reads Redis first and returns the stale pre-bump value, so tokens the operator believed revoked verify again; a consumed refresh jti recorded only in-process is likewise forgotten, defeating single-use/replay detection. A process restart discards any in-process-only revocation the same way. The module docstring (18-23) documents this as a deliberate trade-off, but the revocation-bypass window is real.

**Evidence**  
token_store.py:103-109 bump_epoch falls back to `self._epoch[user_id] += 1` on Redis error; :91-99 get_epoch reads Redis first (returns 0 / stale for a missed increment); :115-125 try_consume_jti falls back to in-process set; :18-23 docstring acknowledging the trade-off.

**Impact**  
During (and after) a Redis disruption a logged-out or force-revoked token can remain valid, and refresh single-use/replay protection is lost in the same window. Requires a Redis outage to coincide with a revoke, so impact is conditional.

**Remediation**  
On the revocation write path, fail closed: if bump_epoch cannot durably persist to Redis, surface an error rather than recording an epoch the verify path will never read; at minimum reconcile in-process epochs into Redis on reconnect. Keep the read/verify path's availability posture separate from the revoke-write path.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Read token_store.py:1-135 in full. The fallback-on-error paths, the Redis-first read on recovery, and the explicit docstring trade-off all match the finding. It is a genuine, self-documented revocation-durability gap gated on a Redis outage; MEDIUM is defensible given the security relevance despite the conditionality. CONFIRMED at MEDIUM.

</details>

---

<a id="m17"></a>

#### M17 — Fernet master key is always written into data/ beside the ciphertext it protects, even when supplied via env

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** key-at-rest  
**Location:** `bot/core/exchange_credentials.py:95`

**Description**  
_load_or_create_master_key persists the master key to data/.exchange_secret.key on the env-var path too: when RUNECLAW_SECRETS_KEY is set, lines 91-102 write the env key to the key file whenever it is absent or differs, then return it. This single Fernet key encrypts everything sensitive at rest (exchange_creds.enc holding every BYOK user's api_key/secret/passphrase and agent private keys; secrets_vault.enc; the per-user llm_api_key column via the shared loader). The env-persist branch is a deliberate tradeoff (comment 87-90: survive a wiped .env so ciphertext is not orphaned), but the consequence is that key and ciphertext always live in the same directory in every configuration. The auto-generate warning (128-135) claims the env option keeps the key 'managed outside the data dir' — a claim the mirroring write contradicts. At-rest encryption exists to protect ciphertext when a volume/backup/mount leaks but the key does not; here one read of data/ yields both halves.

**Evidence**  
Lines 91-102, env-key branch: p.write_bytes(env_key.encode()) writes to key_file (data/.exchange_secret.key). _CREDS_FILE (40) and _KEY_FILE (41) are both under _STATE_DIR='data' (39). The docstring at 128-135 asserts env mode keeps the key 'managed outside the data dir', which this write defeats.

**Impact**  
There is no supported configuration in which the master key is stored apart from the ciphertext it protects, so at-rest encryption gives no protection against the specific threat it exists for (data-dir / backup / volume-snapshot disclosure). One filesystem read of data/ compromises all users' exchange trading keys and the WEB3 signing key. Requires read access to the data dir; not remotely exploitable on its own.

**Remediation**  
When RUNECLAW_SECRETS_KEY is provided, do not mirror it into the data dir (or gate mirroring behind an explicit opt-in and fix the warning text). For real separation, source the master key from an external KMS/secret manager or a path outside the persisted volume, and document that env-only mode trades the wiped-.env self-heal for key/ciphertext separation.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read exchange_credentials.py 72-136. Confirmed lines 91-102 write the env key to the data-dir key file on the env branch, that _KEY_FILE and _CREDS_FILE share _STATE_DIR='data', and that the docstring at 128-135 promises out-of-data-dir management the code contradicts. The mirroring is an intentional tradeoff (comment 87-90) but the finding's core claim — key and ciphertext are never separable — holds. Kept MEDIUM: defeats the stated purpose of at-rest encryption for high-value secrets, but only realizable given data-dir/backup disclosure, so not elevated.

</details>

---

<a id="m18"></a>

#### M18 — Kill switch not re-checked at order submission — halt race can still place a live order

**Severity:** MEDIUM · **Confidence:** Likely · **Pillar:** quality · **Category:** toctou-fail-open  
**Location:** `bot/core/live_executor.py:3402`

**Description**  
The engine's kill-switch re-check (engine.py:5772, checks self._halted / circuit_breaker_active) runs BEFORE executor.execute() is invoked at 5792. Inside execute(), the only in-flight re-check is is_live() at 2703; between there and the actual entry order at 3402 (await self._create_order_idempotent) the code performs uncancellable network awaits (load_markets, _ensure_leverage, fetch_ticker) with no trading_halted() consultation. guard_lint.py:242-243 explicitly excludes _create_order_idempotent from the kill-switch rule precisely because 'the normal open path is gated upstream at engine.py's last-mile check' — confirming the design relies solely on the upstream check and execute() never re-reads the halt flag. emergency_halt_all (engine.py:2116) sets self._halted=True then awaits flatten_all_positions -> close_all_positions, which iterates ONLY self._positions (line 6522). A confirm already past 5772 and awaiting inside execute() places its order at 3402; if the order lands after flatten snapshots self._positions, the emergency flatten does not force-close it. The market-fallback path at 6279 does call trading_halted() immediately before its order, proving the pattern is understood and simply absent on the main entry path.

**Evidence**  
Read live_executor.py 2589-2718 (only re-gate is is_live() at 2703; _preflight_check at 1303-1380 has no halt check), 3388-3417 (order at 3402), 506-544 (halt plumbing), 6279 (fallback halt check present). Read engine.py 5748-5807 (last-mile check at 5772 precedes execute() at 5792), 2078-2137 (halt sets flag then flattens), and live_executor 6515-6540 (close_all_positions iterates only self._positions). guard_lint.py 218-252 documents the exclusion rationale.

**Impact**  
A /halt or breaker trip landing while a confirm is mid-execute() still opens a live position that the concurrent emergency flatten can miss — a position opened AFTER the operator engaged the kill switch, contrary to the halt intent. Note the position is still recorded and receives SL/TP via the normal fill flow, so it is monitored, not naked; the harm is that it opens/stays open despite the halt, not that it is unmanaged. The window is the sub-second internal await span of an already-confirmed trade, so it is real but narrow.

**Remediation**  
Add an `if trading_halted(): return 'BLOCKED: halted'` immediately before the create_order at 3402 (and/or right after the load_markets/leverage/ticker awaits), mirroring line 6279, so the last-mile check is co-located with the actual submission rather than only upstream in the engine.

**Effort:** low

<details><summary>Verifier note</summary>

Confirmed the race window by code: engine last-mile halt check is strictly before execute() (5772 vs 5792), execute()/_create_order_idempotent contain no internal trading_halted() call (corroborated by guard_lint's deliberate exclusion of _create_order_idempotent), and close_all_positions only walks self._positions so a just-placed order can be missed. Kept MEDIUM but refined the impact: the raced position gets SL/TP and is tracked, so 'unmonitored' is overstated — the actual defect is a fail-open opening a position after the kill switch. Confidence Likely: window confirmed, exploit is timing-narrow and non-attacker-controlled.

</details>

---

<a id="m19"></a>

#### M19 — Market-fallback OPEN order skips the idempotency key, risking an orphaned naked position

**Severity:** MEDIUM · **Confidence:** Likely · **Pillar:** quality · **Category:** idempotency-gap  
**Location:** `bot/core/live_executor.py:6288`

**Description**  
When a resting limit order is cancelled on drift, _execute_drift_market_fallback opens NEW exposure for the unfilled remainder via a raw `exchange.create_order(pos.symbol, 'market', side, qty, params=self._venue.futures_params())` at line 6288 — no clientOid/coid, and not wrapped in _create_order_idempotent. The main entry path uses _create_order_idempotent (3402), which (1560-1610) injects the clientOid and, on ANY exception, queries the exchange by clientOid to reconcile a timed-out-but-filled order instead of losing it. The fallback forgoes this entirely. If the create_order at 6288 times out but the order actually landed, the exception propagates to the outer handler at 6388, which audits ERROR and returns None. pos.status is only set to 'open' at 6307 and SL/TP placed at 6347 — both AFTER the order — so neither runs. The result is a real exchange position with no SL/TP, while the bot still tracks pos as a pending limit whose limit_order_id points to the just-cancelled order.

**Evidence**  
Read 6228-6393 (whole function): cancel at 6240, raw create_order at 6288 with venue params only and no id key, status/SL/TP set only after at 6307/6347, outer except at 6388 returns None. Contrast _create_order_idempotent 1560-1610 which reconciles by clientOid via _find_order_by_client_oid and only re-raises when confirmed absent. This opening path performs no such reconciliation.

**Impact**  
A timeout-but-filled market fallback leaves a naked, SL/TP-less live position that the bot does not know it holds (tracked state says pending limit). This is the exact naked-position failure mode _create_order_idempotent exists to prevent, reachable on a real (if occasional) network-timeout condition. Not attacker-controlled; probability compounds a rare path (limit drift fallback) with a rare event (submit timeout that landed), so occurrence is low but the consequence is the worst case.

**Remediation**  
Route this opening order through _create_order_idempotent with the trade's coid (e.g. self._client_oid(...) / pos-derived coid) so a timed-out-but-landed fill is recovered and tracked, and SL/TP still get attached. At minimum add a by-clientOid reconcile in the 6388 handler before giving up.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Confirmed line 6288 is a raw create_order with no clientOid and no idempotent wrapper, and that the outer except (6388) returns None without any by-clientOid reconciliation, while status='open' and _place_sl_tp only occur after the order. _create_order_idempotent (1560-1610) demonstrably does the reconciliation this path lacks. Genuine idempotency/fail-open gap distinct from the halt-race finding. MEDIUM/Likely retained.

</details>

---

<a id="m20"></a>

#### M20 — Audit chain is a keyless hash chain whose Ed25519 anchor is computed then thrown away

**Severity:** MEDIUM · **Confidence:** Confirmed · **Pillar:** security · **Category:** audit-integrity  
**Location:** `bot/utils/audit_chain.py:200`

**Description**  
AuditChain.append() calls sign_latest_batch() every 50 entries (line 200) but discards the returned AttestationResult. sign_latest_batch (309-322) builds an AttestationEngine, computes a Merkle root, signs it, and returns the AttestationResult to no persistent sink. verify() (237-305) is a pure recomputation: it re-derives each entry_hash from the record's own fields via _compute_hash and checks prev_hash linkage and sequence continuity, never consulting any signature. Because _compute_hash (103-116) uses no secret key, the entire chain is recomputable by anyone. A party with write access to logs/audit_chain.jsonl can edit any entry, recompute its entry_hash and every downstream prev_hash/entry_hash, and verify() returns (True, []). Tail truncation is likewise undetectable: verify() starts at GENESIS_HASH/seq 0 with no length or signature anchor, so lopping off the newest entries yields a valid shorter chain. The Ed25519 signature was the one mechanism that could have made either detectable to a verifier holding the public key, and it is never stored. Note the threat requires local filesystem write access to the log — it is not remotely exploitable.

**Evidence**  
audit_chain.py:198-203 increments _entries_since_sign and calls self.sign_latest_batch(...) inside a try that only logs on failure; the return value is dropped. sign_latest_batch (309-322) returns engine.sign_batch(hashes). verify() (287-300) recomputes _compute_hash from record fields only. Grep confirms sign_latest_batch's only caller is the self-call at line 200 and no audit-chain signature sidecar is written.

**Impact**  
The module docstrings present the chain as 'tamper-evident' and the attestation as 'non-repudiation ... haven't been forged or replayed' — a forensic/compliance control for a real-money bot. Against a knowledgeable adversary with data-dir write access it provides neither: decision records, outcomes and actor fields can be rewritten, or the tail truncated, and still pass verify(). It does still catch accidental corruption/reorder. A post-incident audit relying on it could certify a forged history as intact.

**Remediation**  
Persist each batch signature (signature_hex + pubkey + Merkle root + covered sequence range) to an append-only sidecar and make verify() require a valid Ed25519 signature over the Merkle root of the covered range, rejecting an unsigned tail. For genuine non-repudiation the verifying pubkey must be anchored where the bot host cannot silently rewrite it, since the private key currently lives beside the log.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Read audit_chain.py in full. Confirmed line 200 discards the AttestationResult, sign_latest_batch (309-322) returns to no sink, verify() (237-305) is pure recomputation with no signature check, and _compute_hash (103-116) uses no key. Grepped the tree: sign_latest_batch has one caller (the self-call), and no audit-chain signature file is written. Defect is real. Kept MEDIUM: legitimate weakness in a control marketed as cryptographic non-repudiation, but exploitation requires local write access to the log rather than remote input, so not higher.

</details>

---

<a id="m21"></a>

#### M21 — Caller-asserted identity: gateway is a single-secret confused deputy with no per-user or admin identity binding

**Severity:** MEDIUM · **Confidence:** Likely · **Pillar:** security · **Category:** authentication-authorization  
**Location:** `bot/web/user_gateway.py:3069`

**Description**  
Verified: the gateway authenticates only the CHANNEL via a shared secret (secret_middleware :62-72 — fail-closed on unset/<32 chars and constant-time hmac.compare_digest). User identity is taken verbatim from the caller-controlled field `tg_id = str(body.get("telegram_id") or "").strip()` (:3066) and the ROLE/allowlist of that CLAIMED id is then re-checked (_guard_user :189-248, _is_admin_id :89-97) with nothing binding the authenticated caller to the claimed id. _is_admin_id matches tg_id against CONFIG.telegram.admin_ids (:94-96), a non-secret numeric Telegram id. HOWEVER: this is a documented, intentional trust delegation — the module comments (:78-81, :198-200) state the Express server's JWT layer authenticates the caller and the gateway trusts the identity it forwards, the standard 'internal service behind an authenticating proxy' pattern. Exploitation therefore requires an ADDITIONAL precondition: obtaining WEB_GATEWAY_SECRET, or a bug in the trusted Express layer that fails to overwrite a client-supplied telegram_id. With the secret intact this is NOT attacker-reachable.

**Evidence**  
user_gateway.py:3066-3070 handle_web3_sign takes tg_id from the request body then gates on `if not _is_admin_id(tg_handler, tg_id): return 403`. _is_admin_id (:89-97) compares the request-supplied tg_id to CONFIG.telegram.admin_ids. secret_middleware (:62-72) authenticates only the X-Gateway-Secret channel, not the identity. No signed identity token / per-request HMAC over telegram_id exists.

**Impact**  
The genuine residual is a defense-in-depth gap, not an active bypass: because admin authorization reduces to (channel secret) + (a PUBLIC numeric Telegram id), a compromise of the single shared secret collapses per-user isolation AND grants admin with no second factor — an attacker who learned the secret could read/act as any user and, by supplying a known admin id, reach the admin routes (testnet-only web3 sign, policy authoring, staking execute). Impact is bounded by (a) the precondition of secret compromise or an Express bug, (b) admin actions being testnet-only / config-level, and (c) the 32-char fail-closed constant-time channel gate. The 0.0.0.0 default (separate finding) widens where the secret gate can be reached but does not remove it.

**Remediation**  
Add a distinct control for admin-only routes so admin authorization does not reduce to a public Telegram id plus the channel secret (a separate operator admin token, or a short-lived signed identity token issued by Express and verified server-side). Keep the fail-closed shared-secret channel gate and rotate WEB_GATEWAY_SECRET on any suspected exposure. Not an active remote vuln absent secret compromise.

**Effort:** Large (>3 days)

<details><summary>Verifier note</summary>

Read user_gateway.py:40-259 and 3055-3106. Confirmed all factual claims: channel-only auth, caller-asserted telegram_id, admin id is a non-secret numeric compared to CONFIG.telegram.admin_ids. Downgraded HIGH->MEDIUM: the design is an intentional, documented delegation to the Express JWT layer (comments :78-81, :198-200), and exploitation is fully gated behind WEB_GATEWAY_SECRET compromise or an Express bug — it is not attacker-reachable on its own. The real, keepable point is the missing second factor for admin routes.

</details>

---

### LOW findings

<a id="l1"></a>

#### L1 — npm advisory backlog described as '1 critical and 15 high' in two comments while the baseline recorded the same day says 0 critical / 9 high

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** documentation-accuracy  
**Location:** `.github/workflows/ci.yml:411`

**Description**  
Two comments state the token/ tree carries 1 critical and 15 high advisories; the machine-readable baseline dated the same day records 0 critical, 9 high, 15 moderate, 11 low. The '15' in the prose is the moderate count.

**Evidence**  
ci.yml:411-412 `# This tree carries 1 critical and 15 high advisories today, nearly all` / `# transitive through the Wormhole SDK.` token/scripts/audit_gate.mjs:9-10 `// specific, instructive way: this tree already carries 1 critical and 15 high` / `// advisories (2026-07-26)`. token/.audit-baseline.json:3-9 `"recorded": "2026-07-26"` with counts critical 0, high 9, moderate 15, low 11. audit_gate.mjs:135 `const grew = SEVERITIES.filter((s) => nowCounts[s] > (baseline.counts?.[s] ?? 0));` — the enforced ceilings come from the JSON, so the enforced critical ceiling is 0.

**Impact**  
The stated backlog for the code that signs privileged Solana transactions is wrong in both directions; anyone triaging the 'must be cleared before any deployment that holds value' item (ci.yml:419-421) works from a number the enforcement data contradicts. No enforcement gap — the gate reads the JSON.

**Remediation**  
Correct both comments to quote the baseline, or have them reference the file instead of restating its contents.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Every cited line read and matched exactly, including the enforcement line at audit_gate.mjs:135 confirming the JSON is authoritative. Note the same drift is already recorded in docs/AUDIT_2026-08-12.md:112 ('ci.yml says 1 crit/15 high; baseline records 0/9'), so it is a known-but-uncorrected doc defect rather than a new discovery. LOW stands: comment-only, no gate weakened.

</details>

---

<a id="l2"></a>

#### L2 — lint:mypy invokes a nonexistent scripts/mypy_gate.py and falls through to a differently-scoped fallback

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** broken-ci-gate  
**Location:** `.gitlab-ci.yml:62`

**Description**  
`python3 scripts/mypy_gate.py || mypy bot/risk bot/core --ignore-missing-imports` — the gate script does not exist, so the `||` fallback always runs, and its scope does not match the GitHub job the comment on :60 claims parity with.

**Evidence**  
.gitlab-ci.yml:60-62 read directly. `ls /home/user/001/scripts/` lists 27 entries and contains no mypy_gate.py. .github/workflows/ci.yml:48-53 gates `mypy bot/risk bot/compliance bot/utils/trailing.py bot/core/bitget_v3_client.py bot/core/position_telemetry.py bot/core/live_executor.py`, and :49 states 'The rest of bot/core is not gated yet' — the fallback checks all of bot/core and drops bot/compliance and bot/utils/trailing.py.

**Impact**  
The ratchet is not enforced on this host and the `||` hides the missing script; the parity comment is wrong in both directions.

**Remediation**  
Inline the exact GitHub module list and delete the `||` fallback, or add scripts/mypy_gate.py parsing ci.yml the way scripts/preflight.py does.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Both halves verified by reading .gitlab-ci.yml:60-62, `ls scripts/`, and ci.yml:48-53. DOWNGRADED MEDIUM -> LOW for reachability: this job extends `.python`, whose before_script already fails on the missing requirements.txt (finding above), so the script line never executes today — the defect is latent behind another defect in the same file, and both are fixed in one edit. Substance is real, so not refuted.

</details>

---

<a id="l3"></a>

#### L3 — Unpinned pip installs undercut the Dockerfile's 'reproducible, tamper-evident' claim, and no image scan runs anywhere

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** build-reproducibility  
**Location:** `Dockerfile:17`

**Description**  
The base image is digest-pinned, but the layer immediately after installs bot/requirements.txt (which carries three >= floors) plus fastapi>=0.110 and uvicorn[standard]>=0.29 written inline with no upper bound and no hash checking, so two builds of the same commit differ. No CI job builds or scans the image, and there is no dependabot config or CODEOWNERS.

**Evidence**  
Dockerfile:6-8 (`# Pin to digest for reproducible, tamper-evident builds.` then the sha256-pinned FROM) and :16-18 (`RUN pip install --no-cache-dir -r requirements.txt \` / `fastapi>=0.110 "uvicorn[standard]>=0.29"`). bot/requirements.txt:10,11,13 cryptography>=48.0.1 / Pillow>=10.3.0 / redis>=5.0.0. `ls -a .github .github/workflows` -> workflows/ci.yml only. `grep -n 'docker\|trivy\|grype' .github/workflows/ci.yml` -> no matches.

**Impact**  
A published image digest cannot be reproduced from the commit, and OS-level CVEs in the base layer and the gcc/libssl-dev/curl packages at Dockerfile:12-14 are reported by nothing.

**Remediation**  
Move fastapi/uvicorn into bot/requirements.txt with exact pins, generate with --generate-hashes and install with --require-hashes, and add a trivy/grype step plus dependabot and CODEOWNERS.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Every claim checked directly, including the negative ones (no docker/trivy/grype step in ci.yml; .github contains only workflows/ci.yml). The finding's own mitigating note about .dockerignore is accurate and I verified it: .dockerignore excludes .env, **/.env, **/*keypair*.json, **/id.json, **/.keys/, .git, data/ and logs/, so `COPY . .` at Dockerfile:31 carries no secret material. LOW is right — the digest pin does deliver base-layer integrity, and the gap is reproducibility plus a missing image scan, not an exploitable weakness.

</details>

---

<a id="l4"></a>

#### L4 — `make test` runs bare pytest, bypassing the ci_test_gate baseline CLAUDE.md forbids substituting

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** gate-bypass  
**Location:** `Makefile:56`

**Description**  
The `test` target runs `pytest tests/ -v --tb=short` directly, performing none of the gate's checks: no baseline diff, no failure when a baselined test starts passing, no 60% coverage floor on bot/risk, bot/core/live_executor.py and bot/compliance, and no rejection of a pytest exit code that means the suite never ran. No Makefile target invokes scripts/preflight.py or scripts/ci_test_gate.py.

**Evidence**  
Makefile:55-56 `test: ## Run full test suite` / `$(PYTHON) -m pytest tests/ -v --tb=short`; :64-66 test-cov also bare pytest with --cov but no --cov-fail-under; the .PHONY list at :5-7 contains no preflight or gate target. scripts/ci_test_gate.py:47-48 `COV_TARGETS`/`COV_FAIL_UNDER = 60`; :134-149 the exit-code sanity checks; :195-197 the now-passing baseline failure. CLAUDE.md: 'Do not substitute a bare pytest.'

**Impact**  
The most discoverable local entry point produces a verdict the project's own docs say is incomplete; the coverage floor and exit-code sanity checks are skipped locally.

**Remediation**  
Point `test:` at scripts/ci_test_gate.py, add a `preflight:` target, and keep a clearly-labelled `test-raw:` escape hatch.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Makefile and ci_test_gate.py both read; every cited line matches (COV_TARGETS is :47-48, one off from the cited :48-49). DOWNGRADED MEDIUM -> LOW: CI enforcement is unaffected — ci.yml:58 runs the gate — so this is dev-loop hygiene, CLAUDE.md already directs developers to scripts/preflight.py rather than make, and tests/known_failures.txt is currently empty (9 lines, all comments) so the baseline-diff arm is inert today; only the coverage floor and exit-code checks are actually skipped. It is also already a tracked backlog item (docs/AUDIT_2026-08-12.md:137, task 2.9 'Makefile truth').

</details>

---

<a id="l5"></a>

#### L5 — `make health` puts DASHBOARD_TOKEN in a curl command line for an endpoint that requires no auth

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** secret-exposure  
**Location:** `Makefile:75`

**Description**  
The health target greps the bearer token out of .env and interpolates it into curl's argv, where it is world-readable via /proc for the duration of the call — for an endpoint that takes no credential at all.

**Evidence**  
Makefile:73-78 read verbatim; :75 is `-H "Authorization: Bearer $$(grep DASHBOARD_TOKEN .env | cut -d= -f2)" \`. api_bridge.py:414-415 `@app.get("/health")` / `async def health():` — bare decorator, no dependency, no credential parameter (handler body read through :425). SECURITY.md:25 lists /health among the read-only endpoints that 'do not require authentication' and names /confirm, /portfolio/close, /risk/halt as what the token does authorise.

**Impact**  
A live bearer token that authorises state-changing trading endpoints is exposed in process arguments on the deployment host for no functional benefit. Requires local access to a host that already holds .env, hence LOW.

**Remediation**  
Delete the Authorization header from the health target; where a token is genuinely needed, pass it via `curl --config -` on stdin rather than argv.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Both halves verified independently: the argv interpolation from the Makefile and the absence of any auth dependency by reading the /health handler and its decorator in api_bridge.py. The secondary nits are also real — `cut -d= -f2` truncates at the first '=' (mangling base64 padding) and the bare grep would match a commented line. LOW is the correct rank: no remote exposure, and the leak requires an attacker already on the box.

</details>

---

<a id="l6"></a>

#### L6 — 30-day JWT stored in localStorage under unsafe-inline CSP magnifies any XSS into month-long account takeover

**Severity:** LOW · **Confidence:** Likely · **Pillar:** security · **Category:** session-management  
**Location:** `app/auth.js:45`

**Description**  
All three cited facts hold: JWT_EXPIRY defaults to '30d' (auth.js:45), the SPA reads the bearer token from localStorage rc_session (app/public/js/app.js:14), and the app CSP allows script-src 'unsafe-inline' (server.js:153). Together this is a defense-in-depth weakness: a long-lived bearer credential sits in a script-readable store behind a CSP that would not block inline injection. It is entirely conditional on an XSS existing — none is proven here — and the impact is bounded by the token_epoch revocation mechanism.

**Evidence**  
Read auth.js:45 `const JWT_EXPIRY = process.env.JWT_EXPIRY || '30d';`; app.js:10-16 resolveToken() reads localStorage 'token'/'rc_session'; server.js:153 `"script-src 'self' 'unsafe-inline' https://telegram.org"`. Also confirmed sessionResponse (line 332) signs with token_epoch and the file carries a revocation path (tokenIsCurrent/revokeUserTokens), so a stolen token is revocable on password change/logout — mitigating the 'month unrevocable' framing.

**Impact**  
If an XSS is ever reached, an injected script can read the token and act as the user for the token lifetime, until an epoch bump (password change / explicit logout) revokes it. No standalone exploit without an XSS.

**Remediation**  
Prefer httpOnly+Secure+SameSite cookie transport, or shorten default lifetime with a refresh flow, and replace 'unsafe-inline' with nonces/hashes.

**Effort:** high — cookie/refresh transport plus CSP nonce migration

<details><summary>Verifier note</summary>

Verified all three underlying facts by reading the cited lines; they are correct, so the design weakness is CONFIRMED rather than Suspected. But it is conditional on an unproven XSS and softened by token_epoch revocation, so LOW/Likely is the honest rating.

</details>

---

<a id="l7"></a>

#### L7 — Telegram Login Widget payloads accepted for 24h with no single-use enforcement

**Severity:** LOW · **Confidence:** Likely · **Pillar:** security · **Category:** replay  
**Location:** `app/auth.js:51`

**Description**  
verifyTelegramAuth accepts any correctly-HMAC-signed widget payload with auth_date within TELEGRAM_AUTH_MAX_AGE_S=86400 (24h); freshness is the only replay control and the /telegram route (1130-1141) neither consumes nor records the payload, so a captured signed payload replays within 24h. The HMAC is constant-time compared (line 72). Exploit requires capturing the signed payload, which travels in a TLS POST body, so practical reach is limited.

**Evidence**  
Read auth.js:51 (TELEGRAM_AUTH_MAX_AGE_S=86400), 60-61 (only freshness gate), 62-72 (HMAC verify, timingSafeEqual), and 1130-1141 (/telegram calls verifyTelegramAuth then findOrCreateOAuthUser with no nonce store or payload-hash record). No replay cache anywhere in the route.

**Impact**  
A captured valid payload (logs, history, or pre-TLS-termination MITM) can be replayed to authenticate as that Telegram user for up to 24h. Capture requirement bounds the risk.

**Remediation**  
Reduce max age to minutes and record the payload hash/auth_date to reject reuse within the window.

**Effort:** low — shorten window and add a seen-payload cache

<details><summary>Verifier note</summary>

Confirmed the 24h window, single freshness gate, and absence of any replay/nonce tracking in the /telegram handler. Replay window is real but needs payload capture over TLS; LOW/Likely retained.

</details>

---

<a id="l8"></a>

#### L8 — Per-account failed-login lockout allows targeted account lockout DoS

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** denial-of-service  
**Location:** `app/auth.js:141`

**Description**  
recordAccountFailure (137-142) locks an account for LOCKOUT_DURATION (5 min) after 8 failures, keyed only by normalized email, and checkAccountLockout is evaluated (532-534) before the password is verified — so a request presenting the CORRECT password during a lockout is still refused with 429. Anyone who knows a victim's email can hold them locked by sending 8 bad passwords every 5 minutes. This is the deliberate RC-AUD-026 anti-credential-stuffing tradeoff, so it is a known-tension weakness, not an oversight.

**Evidence**  
Read auth.js 137-142 (lockedUntil = now + LOCKOUT_DURATION at count>=8, ACCOUNT_RATE_LIMIT_MAX=8 line 87, LOCKOUT_DURATION=5min line 79) and 530-534 where checkAccountLockout(normalizedEmail) gates before the SELECT/bcrypt, returning 429. Keyed by email, not IP, so the legitimate user is blocked regardless of source.

**Impact**  
Availability: a known-email account can be denied login on demand; the 5-min window must be re-triggered, and password reset clears it. Not indefinite without sustained attacker effort.

**Remediation**  
Add delay/CAPTCHA rather than a hard email-keyed lockout, or skip the lockout for a request that presents the correct password; notify the owner instead of blocking.

**Effort:** medium — reshape throttle to avoid blocking valid credentials

<details><summary>Verifier note</summary>

Confirmed the lockout mechanics and that the check precedes password verification, so a correct password during lockout is blocked. Real targeted-DoS, but 5-min re-triggered and reset-clearable; LOW is correct.

</details>

---

<a id="l9"></a>

#### L9 — Login timing side-channel enables account enumeration

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** user-enumeration  
**Location:** `app/auth.js:544`

**Description**  
POST /api/auth/login (line 536-544) returns 401 immediately when no user row matches, without any bcrypt work, but runs bcrypt.compare on the hit path (line 544). The register handler deliberately hides ER_DUP_ENTRY (line 514) and forgot-password returns a uniform body, so enumeration is protected elsewhere while the login path leaves a measurable bcrypt-shaped timing gap between existent and non-existent emails. Real, but network jitter and the enumeration-only payoff cap the impact.

**Evidence**  
Read app/auth.js 536-544: on rows.length===0 the handler calls recordAttempt/recordAccountFailure and returns 401 with no hash comparison; the hit path at line 544 runs `await bcrypt.compare(password, user.password_hash)`. No dummy-hash compare exists on the miss branch. Register enumeration protection at 514 confirmed, so the inconsistency is genuine.

**Impact**  
An attacker measuring latency can distinguish existing password accounts (slow, bcrypt runs) from non-existent emails (fast). OAuth-only rows (null password_hash) also compare fast. Yields email existence only, not credentials.

**Remediation**  
On the no-row / null-hash path, run bcrypt.compare against a fixed dummy hash so response time is constant regardless of account existence.

**Effort:** low — one dummy-hash compare on the miss branch

<details><summary>Verifier note</summary>

Confirmed the code shape at 536-544 and the register protection at 514. Timing gap is real and measurable given bcrypt cost, but the outcome is account enumeration only, defended imperfectly by network noise; MEDIUM overstates it, downgraded to LOW.

</details>

---

<a id="l10"></a>

#### L10 — OAuth callback returns the full session including JWT as base64 in the URL fragment

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** token-leakage  
**Location:** `app/auth.js:1467`

**Description**  
The OAuth redirect callback base64-encodes the entire sessionResponse (a freshly signed 30-day JWT plus user fields) and places it in the URL fragment (auth.js:1465-1468). The fragment stays out of the query string/Referer, but base64 is encoding not encryption, and the value lands in browser history, synced-profile history, and is readable by any extension or script reading location.hash before the client scrubs it.

**Evidence**  
Read auth.js 1465-1468: `Buffer.from(JSON.stringify(await sessionResponse(user, {...}))).toString('base64')` then `res.redirect('/#oauth=' + payload)`. sessionResponse (321-340) embeds signToken(...) with the 30-day expiry.

**Impact**  
A recoverable long-lived token is exposed in client-side artifacts, broadening theft avenues beyond the localStorage copy.

**Remediation**  
Use a short-lived one-time handoff code redeemed over POST (as oauthLinkKeys already does), or set an httpOnly cookie on the callback, and scrub location.hash immediately.

**Effort:** medium — one-time-code handoff or cookie set on callback

<details><summary>Verifier note</summary>

Confirmed the base64-in-fragment redirect and that the payload contains a 30-day token via sessionResponse. Fragment avoids server/Referer leakage but not history/extensions; LOW/Confirmed is right.

</details>

---

<a id="l11"></a>

#### L11 — ENS getAvatar may fetch an attacker-influenced URL server-side (self-linked-wallet SSRF primitive)

**Severity:** LOW · **Confidence:** Suspected · **Pillar:** security · **Category:** ssrf  
**Location:** `app/lib/ens.js:68`

**Description**  
resolveIdentity() (ens.js:66-68) calls `p.lookupAddress(address)` then `p.getAvatar(name)` on a plain ethers JsonRpcProvider with no egress restriction. The address is always the caller's OWN SIWE-linked wallet (web3.js:27 /identity and :64 /profile → walletAddressOf). An authenticated user controls which wallet they link (SIWE proves control) and therefore the ENS reverse record and avatar text record that wallet carries, so they can point the server's avatar dereference at a host of their choosing. ethers' getAvatar dereferences NFT-backed avatar records (eip155/erc721/erc1155) by fetching a contract-controlled tokenURI/metadata, and handles URL-scheme records — a classic blind-SSRF primitive.

**Evidence**  
Read ens.js:68 (`if (name) avatar = await p.getAvatar(name).catch(() => null)`) and the provider factory at :26-33 (bare JsonRpcProvider, no fetch/host restriction). Reachability confirmed in web3.js: router.use(authMiddleware) at :21, /identity at :25-30 and /profile at :62-84 both call resolveIdentity(address) where address = await walletAddressOf(req.user.user_id). GAP: ethers is NOT vendored in the repo (no node_modules/ethers present), so I could not read the installed getAvatar to confirm it actually issues an outbound HTTP fetch to an attacker-controlled URL for the record types in question — that step rests on library behavior, not on code I read. The primitive is also blind (only the avatar URL string is returned in JSON; the fetched body is not reflected).

**Impact**  
An authenticated user who links a crafted wallet (with an ENS reverse record and an NFT/URL avatar record) could induce a server-side outbound request to a host of their choosing (blind SSRF), potentially reaching internal or metadata endpoints via the configured egress. Heavily bounded: requires SIWE-linking a wallet the attacker controls plus ENS setup; only the caller's own wallet is ever resolved; response body is not returned. LOW.

**Remediation**  
Confine avatar resolution: resolve only the raw avatar text record without dereferencing NFT metadata, or route getAvatar's fetch through an SSRF-guarded fetcher that blocks private/link-local/metadata IP ranges and non-allowlisted schemes. Do not fetch remote avatar metadata server-side for a user-controlled ENS record.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Reachable code path confirmed by reading ens.js:66-68 and web3.js:21,27,29,64,66. The finding is a legitimate hardening concern and correctly rated LOW/Suspected; I did NOT upgrade confidence because the SSRF turns entirely on ethers getAvatar dereference behavior which is not vendored and could not be verified locally, and the primitive is blind. Not refuted — the pattern is real and reachable by an authenticated user.

</details>

---

<a id="l12"></a>

#### L12 — Merkle tree has no leaf/internal-node domain separation (second-preimage shape)

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** crypto-merkle  
**Location:** `app/lib/sealroot.js:36`

**Description**  
merkleRoot/merkleProof hash internal nodes as sha256(leftHex + rightHex) with no domain tag separating a leaf from an interior node. Leaves are constrained to /^[0-9a-f]{64}$/ (canonicalLeaves, line 26) and every internal node produced by sha256hex (line 22) is also a 64-hex digest, so a leaf and an interior node are structurally indistinguishable — the classic Merkle second-preimage precondition. The odd-node promotion at line 36 (unpaired node promoted unchanged, no duplication) additionally lets distinct leaf multisets collapse to the same root.

**Evidence**  
Read line 36: `next.push(i + 1 < level.length ? sha256hex(level[i] + level[i + 1]) : level[i]);` — odd node promoted unchanged, confirmed. Line 26: canonicalLeaves filters with `/^[0-9a-f]{64}$/`; line 22: `sha256hex` returns a 64-hex digest — identical shape, no 0x00/0x01 leaf/node prefix. (Finding cited line 27 for the regex; it is actually line 26 — immaterial.)

**Impact**  
The construction in isolation permits presenting an interior node as a 'seal' and deriving a valid membership proof for something never sealed. Exploitability is nil in this deployment: forging a member requires a SHA-256 preimage of a structured seal payload (the verify page re-derives a displayed seal as sha256(payload)), and seal_roots.js commits the exact leaf set with the root — verified: seal_roots.js:58 stores `leaves: JSON.stringify(leaves)` and anchorFor (seal_roots.js:96) derives proofs ONLY from that committed set. Those mitigations, not the tree design, hold. Defense-in-depth hygiene issue on a crypto primitive; LOW is correct.

**Remediation**  
Domain-separate the hash: leaf = sha256(0x00||hex), parent = sha256(0x01||left||right), and either reject or duplicate unpaired nodes rather than promoting them unchanged. Mirror the change in the browser verifier (verifyProof, line 70) and the Python twin so the wire contract stays byte-identical.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Independently read sealroot.js:22,26,36 and seal_roots.js:58,96. Confirmed: no domain separation, odd node promoted unchanged, and the cited mitigations exist exactly as described. The defect (construction property) is real; exploitability is fully gated by the seal preimage requirement and the committed leaf set. Regex is at line 26 not 27. LOW/Confirmed stands.

</details>

---

<a id="l13"></a>

#### L13 — Shared esc() in app.js omits single-quote escaping, diverging from the three other escaper copies

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** inconsistent-escaper  
**Location:** `app/public/js/app.js:95`

**Description**  
app.js esc() (lines 95-98) escapes &, <, >, and " but not the single quote, whereas duel.js, guardian-console.js, and strengthmap.js all include the ' -> &#39; mapping. The one app.js sink that builds an href attribute (line 217) uses double quotes, and no app.js sink was found building a single-quoted attribute from esc() output, so the omission is a latent divergence with no reachable exploit today.

**Evidence**  
Read app.js:95-98: esc() has .replace(/&/), (/</), (/>/), (/"/) and stops — no single-quote replace. Read the three siblings via sed: duel.js esc() replaces /[&<>"']/ including "'": '&#39;'; guardian-console.js esc() same char class including single quote; strengthmap.js esc() same. app.js:217 builds href="${esc(cta.href||'#')}" — double-quoted, so a raw ' cannot break out. No single-quoted attribute sink found in app.js.

**Impact**  
Currently nil — every audited app.js sink is a text node or double-quoted attribute. Latent: any future single-quoted attribute built with this esc() would be breakable, yielding attribute-injection/DOM-XSS.

**Remediation**  
Add .replace(/'/g, '&#39;') to app.js esc() to match the other three, or factor the four escapers into one shared module to eliminate drift.

**Effort:** low

<details><summary>Verifier note</summary>

Verified app.js:95-98 lacks the single-quote replace, and confirmed all three sibling escapers include it. Confirmed the only href sink (app.js:217) is double-quoted, so no active exploit. The finding correctly self-scopes to latent/hygiene. Verdict CONFIRMED at LOW; the divergence is real and the no-current-exploit reasoning is accurate.

</details>

---

<a id="l14"></a>

#### L14 — Non-atomic read-modify-write on arena balance allows lost updates and duplicate close records

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** race-condition  
**Location:** `app/routes/arena.js:691`

**Description**  
Every arena balance mutation is a read-then-absolute-write with no transaction, row lock, or version check. In /close, loadAccount(userId) reads balance (line 683), pnl is computed in JS, then UPDATE arena_accounts SET balance = ? overwrites it with an absolute value (lines 691-692); /open does the same (419-420). Two concurrent /close for different positions each read the same starting balance and the last absolute write wins, dropping one credit (classic lost update). Two concurrent /close for the SAME position_id both pass the WHERE id=? AND user_id=? SELECT (670-671) before either DELETE runs (690), so both INSERT an arena_trades row (684-689) for one close; the DELETE is idempotent and the absolute SET means balance is not double-credited, but the duplicate trade row persists. tradeLimit (20/min per user) is a rate cap, not a serializer, so concurrent in-flight requests are not ordered.

**Evidence**  
arena.js:683 acct = await loadAccount(userId); :684-689 INSERT INTO arena_trades (...); :690 DELETE FROM arena_positions WHERE id=? AND user_id=?; :691-692 UPDATE arena_accounts SET balance = ? WHERE user_id = ? [round2(acct.balance + p.margin + pnl), userId] — no lock/CAS/txn. Same absolute-set at open :419-420. arena_trades has no unique constraint preventing a second row for the same position (trade_key/seal are nullable, db.js:2166-2167).

**Impact**  
Bounded to virtual funds (arena is §4 paper, no real money). Competitive-surface effect only: duplicate arena_trades rows inflate the public per-trader trade/sealed counts (computeLeaderboard COUNT(*) at line 712-714) and can double pnl in season rankings/streaks; a lost update can leave balance inconsistent with recorded trades. No fund loss.

**Remediation**  
Make balance mutations atomic: conditional increment UPDATE (SET balance = balance + ?) inside a transaction where supported, and gate the close on DELETE ... WHERE id=? AND user_id=? affectedRows==1 before inserting the trade and crediting, so a second concurrent close for the same position records and credits nothing.

**Effort:** moderate — reorder to delete-first-then-check-affectedRows and switch to relative balance updates; note the in-memory pool fallback lacks transactions (see sync.js:450-453), so guard on affectedRows rather than relying on a txn

<details><summary>Verifier note</summary>

Read arena.js:662-698 (/close) and :405-426 (/open). Confirmed the read (683) / absolute-write (691-692) pattern with no lock, txn, or CAS, and that two concurrent same-position closes both pass the SELECT at 670-671 before the idempotent DELETE at 690, yielding two arena_trades INSERTs. Balance is genuinely not double-credited because the SET is absolute, as the finding states. Verified tradeLimit is only a rate limit. This is a real race, correctly scoped to virtual funds. CONFIRMED at LOW/Confirmed as reported.

</details>

---

<a id="l15"></a>

#### L15 — Candle cache key omits the caller-supplied `limit`, so a request can be served the wrong candle count

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** correctness  
**Location:** `app/routes/market.js:132`

**Description**  
The /candles/:symbol handler computes `limit` from the query string (line 127, `Math.min(parseInt(req.query.limit) || 24, 200)`) and interpolates it into the Bitget URL (line 133, `&limit=${limit}`), but the cached() key on line 132 is `candles_${sym}_${bg}_${startTime}_${endTime}` — it includes symbol, Bitget granularity token and the optional time-window fragments, but NOT `limit`. Two requests differing only in `limit` collide on the same 15s-TTL entry; whichever misses first pins the payload for that window.

**Evidence**  
Read app/routes/market.js:127 (limit derived, clamped 1..200), :132 (cache key without limit), :133 (limit interpolated into fetched URL). The key demonstrably contains `${bg}` and the two window fragments but no limit component, so the cache cannot distinguish a 24-candle fetch from a 200-candle fetch for the same symbol/granularity/window.

**Impact**  
A public read-only data-correctness defect. For up to 15s, a chart asking for 200 candles can receive a 24-candle payload cached moments earlier (or vice-versa) for the same symbol/granularity/window, silently rendering fewer bars than requested. No security impact.

**Remediation**  
Include `limit` in the cache key, e.g. `candles_${sym}_${bg}_${limit}_${startTime}_${endTime}`.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Confirmed by reading lines 127, 132, 133. limit is a real per-request variable that changes the upstream URL yet is absent from the cache key; bg IS already in the key (as the first auditor noted). Genuine correctness bug, correctly rated LOW.

</details>

---

<a id="l16"></a>

#### L16 — MCP 64KB body cap enforced via Content-Length only; absent on /api/tool/invoke

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** input-validation  
**Location:** `app/routes/mcp.js:53`

**Description**  
mcp.js enforces its 64KB bound by parsing Content-Length (mcp.js:52-58), added because the global express.json (1MB) has already parsed the body before the per-route 64kb parser runs. A chunked-transfer-encoding request carries no Content-Length, so parseInt(...||'0') yields 0 and the check passes — the effective bound reverts to the global 1MB. tool8257.js dispatches the same registry at POST /api/tool/invoke with only the no-op per-route express.json({limit:'64kb'}) (tool8257.js:40) and no header check.

**Evidence**  
Read mcp.js:48-58, tool8257.js:40, and confirmed the global parser is express.json({limit:'1mb'}) at server.js:243, mounted before both routers (server.js:349,352). The header-only check is bypassable via Transfer-Encoding: chunked.

**Impact**  
Minor: global 1MB still bounds payloads, so this is a 16x weakening of a defense-in-depth limit on two unauthenticated JSON endpoints, not unbounded DoS.

**Remediation**  
Check the actual parsed body size (Buffer.byteLength(JSON.stringify(req.body)) or a verify callback on the global parser) instead of the header, and apply the same middleware to tool8257's invoke route.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed the Content-Length-only check, the chunked bypass, the 1MB global cap at server.js:243, and that tool8257 has no header check at all. Bounded by the 1MB global, so LOW is correct.

</details>

---

<a id="l17"></a>

#### L17 — Duel picks filtered with an ISO 'Z'-suffixed string literal against a DATETIME column

**Severity:** LOW · **Confidence:** Suspected · **Pillar:** quality · **Category:** correctness  
**Location:** `app/routes/public_duel.js:54`

**Description**  
loadSeason binds `start + 'T00:00:00.000Z'` (public_duel.js:54-55) against duel_picks.created_at (DATETIME). The neighboring rounds query (:52) binds plain 'YYYY-MM-DD'. The concern is that MySQL/TiDB string-to-datetime conversion rejects or mishandles the trailing 'Z' zone designator, either erroring the public board or dropping all picks.

**Evidence**  
Read public_duel.js:49-76. Confirmed the Z-suffixed literal is bound only here while the sibling rounds filter binds the plain date. I could NOT execute the real MySQL/TiDB backend to confirm the conversion actually fails — MySQL's implicit datetime coercion in a WHERE comparison is lenient in some sql_modes (it may emit a truncation warning and still compare), and TiDB behavior may differ, so the asserted breakage (503 or empty board) is unproven.

**Impact**  
Potentially an empty or erroring public duel board on the real backend; invisible under the in-memory test pool which ignores WHERE clauses. Impact contingent on backend datetime-parsing behavior.

**Remediation**  
Bind new Date(start + 'T00:00:00.000Z') or the plain string start + ' 00:00:00', matching the sibling query.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Code inconsistency confirmed (this is the only date filter in the domain binding a Z-suffixed string). Downgraded to DOWNGRADED/Suspected because the asserted runtime failure is unverified: I cannot run the DB, and MySQL comparison-context datetime coercion frequently tolerates such strings (warning, not error). Worth fixing defensively, but not a confirmed defect.

</details>

---

<a id="l18"></a>

#### L18 — Public /api/roots/verify/:day triggers live outbound RPC per request with no rate limit

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** rate-limiting-dos  
**Location:** `app/routes/roots.js:61`

**Description**  
The roots router applies a limiter only on POST /anchor (roots.js:92); GET /, /anchor-plan/:day and /verify/:day have none. /verify/:day calls verifyAnchor (:74), which makes outbound JSON-RPC POSTs to Base. Only status==='verified' results are cached (:79), so a day whose anchor read returns unknown/mismatch re-fires the outbound calls on every hit.

**Evidence**  
Read roots.js in full: router-level limiter is absent; only line 92 attaches rateLimit to POST /anchor. Line 74 performs verifyAnchor per uncached request; line 79 caches only verified. Endpoint is unauthenticated (server.js:340). Outbound RPC only fires when row.anchor_tx exists (else early 'unanchored' return at :66-68), so amplification is scoped to anchored-but-unverifiable days plus an attacker rotating the :day param.

**Impact**  
Anonymous client can use the server as an amplifier against public Base RPCs and burn event-loop/connection resources; a degraded RPC turns each request into slow outbound calls.

**Remediation**  
Add the shared per-IP rateLimit to the roots router (as public_flight.js does) and negatively-cache unknown/mismatch results for a short TTL.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Confirmed no GET-side limiter and the verified-only cache. Amplification is real but bounded (verifyAnchor does at most ~2 fetches, and only anchored days trigger it), so LOW is correct.

</details>

---

<a id="l19"></a>

#### L19 — Latent dollar-P&L channel on public signal surfaces (pnl / net_pnl emitted raw)

**Severity:** LOW · **Confidence:** Likely · **Pillar:** security · **Category:** public-data-exposure  
**Location:** `app/routes/signals.js:30`

**Description**  
GET /api/signals SELECTs and emits the raw pnl column per signal (signals.js:29-36), /stats emits net_pnl = SUM(pnl) (:55,72), and the unauthenticated MCP get_signals selects pnl too (mcp.js:468-472). No redaction sits in the path. Currently signal.pnl is unpopulated in production (per call.js's documented rationale), so nothing leaks yet, but the field is emitted and the moment the bot fills the outcome channel dollar P&L lands on three public payloads §4 forbids.

**Evidence**  
Read signals.js:28-36/51-73 and mcp.js:468-472. net_pnl is literally a dollar field emitted on the public /api/signals/stats payload (server.js:290, no auth). Confirmed no isFinite/redaction filter converts it to percent. Present-day values are NULL (SUM over WHERE pnl IS NOT NULL returns null), so no current runtime exposure.

**Impact**  
No current exposure (values NULL). A single bot-side change to populate signal pnl publishes dollar P&L on /api/signals, /api/signals/stats and MCP get_signals with nothing in the way.

**Remediation**  
Drop pnl/net_pnl from the anonymous payloads or convert to a percent on a recorded basis before emitting; add a test asserting no dollar-key fields even when pnl is populated.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed the raw pnl/net_pnl fields are emitted on unauthenticated payloads and no redaction is present. Kept as CONFIRMED-but-latent with confidence Likely: the exposure channel is definitely present, but there is no current runtime leak because the column is unpopulated. Defense-in-depth / future-risk finding; LOW is correct.

</details>

---

<a id="l20"></a>

#### L20 — 'While you were away' digest renders DB failures as zero counts

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** error-handling-honesty  
**Location:** `app/routes/since.js:46`

**Description**  
Each count block swallows its exception and leaves the initialized zero (since.js:38-40 init; :46,51,58 catches labeled 'stream quiet → 0' etc.). A query error thus reports '0 new signals/events/closes'. Because last_seen_at was advanced at :31 before the counts ran, the missed window is unrecoverable on retry.

**Evidence**  
Read since.js:23-64. Confirmed UPDATE last_seen_at at :31 precedes the three count blocks at :42-58, whose catch comments mislabel error paths as quiet-data paths. Authed route (authMiddleware at :23).

**Impact**  
Authed, low-stakes surface, but violates the absent-is-not-a-measurement rule and permanently consumes the absence window on a failed read.

**Remediation**  
Distinguish per-section failure (null + unreadable flag, or omit the section) and advance last_seen_at only after the counts succeed.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed the swallow-to-zero catches and the pre-count last_seen_at advance. Authed and low-stakes, so LOW is correct.

</details>

---

<a id="l21"></a>

#### L21 — SSE stream has a global 500-connection cap but no per-IP cap

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** rate-limiting-dos  
**Location:** `app/routes/stream.js:32`

**Description**  
GET /api/stream admits connections until clients.size >= MAX_CLIENTS (500) then 503s everyone (stream.js:19,32-34). No per-IP cap and no auth, so one client holding 500 idle EventSource connections locks out every legitimate dashboard; the heartbeat keeps attacker connections alive.

**Evidence**  
Read stream.js:1-53. Only admission control is the global Set size at :32; clients keyed by response object, never by IP. Mounted unauthenticated at server.js:380.

**Impact**  
DoS on real-time dashboard updates; clients fall back to polling, so impact is degraded freshness rather than data loss. Trivially executable, low blast radius.

**Remediation**  
Track connections per req.ip and cap at a small number before the global cap.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed the single global cap and absence of any per-IP limit or auth. Impact is degraded freshness (dashboards poll as fallback), so LOW is correct.

</details>

---

<a id="l22"></a>

#### L22 — Full portfolio sync deletes all trades before inserting, with no transaction

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** atomicity  
**Location:** `app/routes/sync.js:285`

**Description**  
POST /api/bot/sync runs DELETE FROM trades WHERE user_id=? (sync.js:285) then inserts closed trades and open positions row-by-row (:293-319) with no transaction. Any row that fails after the delete leaves the table partially populated or empty, and all public read surfaces serve the truncated record until the next successful full sync. The sibling /trade-event handler documents insert-before-delete ordering (:441-458) precisely because the in-memory fallback has no transactions, but the replace-all path got no equivalent protection.

**Evidence**  
Read sync.js:274-363. Confirmed DELETE at :285 then unguarded per-row INSERT loops at :294-319; contrast the carefully-ordered close path at :459-470. This route is bot-authenticated (below botAuth at :262).

**Impact**  
A transient failure window (until the bot's next sync) in which the public track record and portfolio understate or blank history. Self-healing, bot-authenticated input.

**Remediation**  
Insert before deleting rows not in the new set (keyed by a sync generation), or wrap in a transaction on the MySQL backend with the ordering fallback on the in-memory one.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

Confirmed the delete-then-loop-insert with no transaction, and confirmed the file's own /trade-event path explicitly solves the analogous problem with ordering. Bot-authed and self-healing, so LOW is correct.

</details>

---

<a id="l23"></a>

#### L23 — Public track-record recent_trades renders an unpriced close as a measured 'flat'

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** correctness-honesty  
**Location:** `app/routes/track.js:215`

**Description**  
recent_trades maps `const p = parseFloat(t.pnl) || 0; result: p > 0 ? 'win' : p < 0 ? 'loss' : 'flat'` (track.js:214-217), the banned shape, so an unpriced close publishes as 'flat'. The same file distinguishes 'unknown' from 'flat' for the monthly block (:174) and the headline stats use classifyPnls with an unpriced count.

**Evidence**  
Read track.js:214-218 and :159-176/201. Confirmed the recent-trades strip is the one place in the file still using `|| 0` while the headline stats and monthly block correctly separate unpriced rows.

**Impact**  
The recent-trades strip asserts break-even outcomes for trades nobody priced; a visitor reconciling it against the (correct) unpriced count finds the strip contradicting the page. The headline win-rate/counts are correct, so impact is confined to a 20-item label list.

**Remediation**  
Use Number.isFinite(parseFloat(t.pnl)) and emit 'unknown'/'unpriced' when it fails, matching the monthly block at :174.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Code defect confirmed. Downgraded MEDIUM->LOW: unlike the MCP get_track_record case (which mislabels the headline win rate), here the headline stats/monthly block are already correct and only the recent-trades display strip mislabels an unpriced row as 'flat'. Real but low-impact cosmetic honesty defect.

</details>

---

<a id="l24"></a>

#### L24 — Public replay-trade classifies a measured break-even close as a 'win'

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** correctness-honesty  
**Location:** `app/routes/track.js:270`

**Description**  
The landing-page showcase uses `result: (parseFloat(pick.pnl) || 0) >= 0 ? 'win' : 'loss'` (track.js:270). The `usable` filter (:248-250) requires isFinite(pnl), so the `|| 0` is dead, but `>= 0` classifies a genuine 0.00 break-even as a 'win'. The MCP twin returns three-valued win/loss/flat (mcp.js:588).

**Evidence**  
Read track.js:242-274 and mcp.js:558-588. Confirmed the upstream isFinite filter blocks the unreadable case (so no false-positive on NULL), leaving only the genuine break-even mislabeled as a win. CLAUDE.md flags `(x || 0) >= 0` by name.

**Impact**  
A scratch trade animates on the landing page labeled a win — a small public overstatement and an inconsistency with the MCP get_showcase_trade surface sharing the pick logic.

**Remediation**  
Use the three-valued mapping from mcp.js:588.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Confirmed: the reachable case is a genuine 0.00 pnl (not an unreadable one, which the filter blocks), and >= 0 counts it as a win while the twin returns 'flat'. Real but rare and cosmetic, LOW is correct.

</details>

---

<a id="l25"></a>

#### L25 — JWT signing key derived from BOT_SYNC_SECRET reuses one secret across trust boundaries

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** key-management  
**Location:** `app/server.js:52`

**Description**  
When JWT_SECRET is unset or <32 chars, JWT_SECRET is deterministically derived via HMAC-SHA256 of BOT_SYNC_SECRET under a fixed domain-separation label that is hardcoded in plain sight (server.js:51-55). BOT_SYNC_SECRET also authorizes /api/bot/sync writes and is shared with the separate Python bot, so it crosses a trust boundary; the derivation label is public in source. Disclosure of BOT_SYNC_SECRET therefore yields JWT_SECRET and full token forgery. It is a fallback path only — an operator-set independent JWT_SECRET (>=32) skips this branch (line 44).

**Evidence**  
Read server.js 43-62: the derivation runs only when `_jwtProvided.length < SECRET_MIN_LEN` and BOT_SYNC_SECRET is >=32; the HMAC label is literal in source. auth.js separately enforces a fatal exit if no derivable secret in production.

**Impact**  
Expands a BOT_SYNC_SECRET compromise from sync-write access to JWT forgery / account (and admin-plan) impersonation. Key-separation weakness, not a standalone exploit.

**Remediation**  
Require an independent JWT_SECRET in production (fatal if absent); keep derivation only as an explicit dev/ephemeral convenience.

**Effort:** low — require distinct JWT_SECRET in prod, keep derivation dev-only

<details><summary>Verifier note</summary>

Verified the deterministic derivation and public label at server.js:51-55, and that it is a fallback gated on a missing/short JWT_SECRET. Reuse across trust boundaries is real; LOW/Confirmed stands.

</details>

---

<a id="l26"></a>

#### L26 — Guardian prompt-injection firewall detects but never blocks by default

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** prompt-injection  
**Location:** `bot/config.py:569`

**Description**  
guardian_firewall_enabled defaults True (config.py:565) so the inbound-text scan runs and records telemetry, but the caller only refuses a HIGH-risk message when guardian_firewall_block_high is ALSO set (telegram_handler.py:2015-2016; user_gateway.py has the parallel gate), and that flag defaults False (config.py:569). The scan is telemetry-first + fail-open by design. Out of the box, a detected instruction-override / action-injection / exfiltration attempt against the acting chat agent is logged but still acted upon.

**Evidence**  
config.py:565 guardian_firewall_enabled default True; config.py:566-569 guardian_firewall_block_high default False with comment 'Default OFF so enabling the firewall observes before it ever blocks'; telegram_handler.py:2013-2025 only returns/blocks when getattr(CONFIG.risk,'guardian_firewall_block_high',False) is truthy, otherwise falls through and acts on the message.

**Impact**  
The input-provenance protection for the natural-language layer that can place trades / dispatch skills is advisory-only unless an operator opts in. A prompt-injection payload forwarded into a conversation is recorded but not refused by default. Impact is bounded because downstream money-path actions still pass through the trade gate and per-user authority checks; this weakens a defense-in-depth layer rather than being the sole control.

**Remediation**  
Default guardian_firewall_block_high to True on live-capable deployments, or gate action-executing intents (trade/skill dispatch) on a clean firewall verdict regardless of the block flag. At minimum document prominently that the firewall is telemetry-only until the operator opts in.

**Effort:** low

<details><summary>Verifier note</summary>

Read config.py:560-569 (both defaults, block_high False confirmed) and telegram_handler.py:2005-2025 (block branch gated on guardian_firewall_block_high, else message is acted upon). Confirmed as an intentional-but-weak default. LOW is appropriate given it is a defense-in-depth layer, not the primary trade-authorization control.

</details>

---

<a id="l27"></a>

#### L27 — Master key file created with default umask then chmod'd, leaving a world/group-readable window

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** file-permissions  
**Location:** `bot/core/exchange_credentials.py:110`

**Description**  
Both the generate path (108-114) and the env-persist path (93-99) create the master key with Path.write_bytes(), which opens the file at mode 0o666 masked by the process umask (commonly 0o644 — group- and world-readable), and only afterwards call os.chmod(..., 0o600). Between file creation/first-write and the chmod, the Fernet master key is briefly readable by other local users/processes. The chmod's OSError is also swallowed, so a failed chmod leaves the file at the umask mode. This is inconsistent with the ciphertext path: atomic_write_bytes (79-88) chmods the temp file to the requested mode BEFORE os.replace, so the ciphertext is never briefly world-readable under its final name — but the key that decrypts it is. attestation.py:86-87 has the identical write_bytes-then-chmod pattern for the Ed25519 seed.

**Evidence**  
exchange_credentials.py:108-114 — p.write_bytes(key) then os.chmod(str(p), 0o600) inside a try that passes on OSError. Same shape at 93-99. Contrast atomic_write.py:87-88, which os.chmod(tmp, final_mode) before os.replace(tmp, p), and _save (228) which passes mode=0o600 through it. attestation.py:86-87 repeats write_bytes then os.chmod.

**Impact**  
On a shared/multi-tenant host or one with a permissive umask, a co-located user or process could read the master key during the brief creation window (first boot / first key write), defeating the 0600 protection for the key that unlocks all stored secrets. Narrow, single-shot window; requires local co-tenancy and timing.

**Remediation**  
Create the key file atomically with a restrictive mode from the outset — os.open(path, O_WRONLY|O_CREAT|O_EXCL, 0o600) then write, or route it through the same atomic_write helper (mode set before rename) already used for the ciphertext. Apply the same fix to attestation_key.bin.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Read exchange_credentials.py 93-114 and attestation.py 64-88 and atomic_write.py 65-94. Confirmed both key-write sites use write_bytes then chmod (with chmod OSError swallowed), that atomic_write_bytes sets mode before os.replace (so the ciphertext contrast is accurate), and that attestation.py repeats the pattern. Real but brief local-only window; LOW is correct.

</details>

---

<a id="l28"></a>

#### L28 — Dashboard (and secret-gated /gateway) binds 0.0.0.0 by default; unauthenticated /metrics reachable on all interfaces

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** network-exposure  
**Location:** `bot/main.py:429`

**Description**  
Verified: bot/main.py:429 binds the dashboard TCPSite to `os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")`. Verified: /metrics is registered outside /api/ (dashboard_server.py:476) and auth_middleware only intercepts paths under /api/ (:413), so /metrics is reachable with no token. The auditor's 'contradicts the documented control' framing is REFUTED by the fuller comment at main.py:420-428, which shows the 0.0.0.0 default is DELIBERATE for the production Docker+nginx topology (separate container reaches the dashboard over the docker network; a 127.0.0.1 default would break it), with the stated real protection being the mandatory fail-closed DASHBOARD_TOKEN gate on /api/* (verified :414-424 returns 403 when unset, 401 on mismatch, constant-time). The /metrics content is deliberately non-financial: _render_prometheus (:335-382) emits runeclaw_open_positions (a count), circuit_breaker_active/consecutive_losses (booleans/ints), api latency and error-rate gauges — no equity, PnL, positions, or secrets — and its docstring pins 'Only non-financial operational signals — safe for an unauthenticated scrape.'

**Evidence**  
bot/main.py:429 `_dash_host = os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")`. dashboard_server.py:413 `if request.path.startswith("/api/"):` — /metrics (:476) is not under /api/, so no auth. _render_prometheus (:347-378) emits only operational counts/booleans/rates.

**Impact**  
By default an on-network party (or a remote one if the host is directly internet-facing rather than behind the intended nginx) can scrape low-sensitivity operational metadata — open-position count, circuit-breaker/loss-streak flags, API latency and error rates — with no credentials. No dollar amounts, PnL, per-user data, or secrets are exposed. The sensitive /api/* aggregate surface remains fail-closed token-gated regardless of bind host. Net exposure is minor operational-metadata leakage.

**Remediation**  
Optionally require DASHBOARD_TOKEN (or a private-network bind) for /metrics as well, or document that /metrics is intentionally public and non-sensitive. If the intended nginx front-end is not guaranteed, prefer defaulting DASHBOARD_BIND_HOST to 127.0.0.1 with an explicit opt-in to bind wider. The /api/* gate already fully protects the sensitive surface.

**Effort:** Trivial (<1h)

<details><summary>Verifier note</summary>

Read main.py:418-434 (incl. the :420-428 comment explaining the intentional Docker default) and dashboard_server.py:400-484 and 335-388. Confirmed 0.0.0.0 default and unauthenticated /metrics. Downgraded MEDIUM->LOW: the 'contradiction' claim is refuted by the full comment (default is deliberate, /api/* is fail-closed token-gated), and /metrics data is deliberately non-financial per code and docstring. Real but minor exposure.

</details>

---

<a id="l29"></a>

#### L29 — Empty allowlist opens all privileged commands to any Telegram user (paper/demo deployments)

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** authn-authz  
**Location:** `bot/skills/telegram_handler.py:2409`

**Description**  
_is_allowlisted() returns True for everyone when no allowlist is configured (telegram_handler.py:2408-2410, 'if not allow: return True'). Combined with register() auto-approving every first-contact user as role=trader (user_store.py:301-313), any stranger who messages a bot with empty TELEGRAM_CHAT_ID/ADMIN_TELEGRAM_IDS/LIVE_TRADER_TELEGRAM_IDS becomes an authorized trader and can invoke /halt, /reset, /pause and /mode. Bounded for live money: is_live() refuses to arm live mode without chat_id (config.py:2355-2368), so the open door is confined to paper/demo, where it still permits any stranger to trip/clear the paper breaker and toggle strategy mode — a DoS on the trading control surface.

**Evidence**  
telegram_handler.py:2409-2410 returns True with no allowlist; user_store.py:307 marks auto-registered users authorized=True role=trader (which holds halt/reset/mode per user_store.py:50-52); config.py:2362-2368 blocks live mode when chat_id is empty, confirming the live-fund bound.

**Impact**  
On a paper/demo bot with no allowlist configured, any unauthenticated Telegram user can halt/reset the (paper) circuit breaker and change strategy mode — control-plane DoS. No live-fund impact because is_live() requires a non-empty TELEGRAM_CHAT_ID, which itself populates the allowlist.

**Remediation**  
Fail closed on an unconfigured allowlist for privileged (write/control) commands: keep the open door only for read-only commands, or require an explicit ALLOW_OPEN_ACCESS flag rather than inferring open access from an empty allowlist.

**Effort:** moderate

<details><summary>Verifier note</summary>

Read telegram_handler.py:2364-2414 (_allowlist_ids sources from three env vars; empty set -> _is_allowlisted True). Read config.py:2350-2369 confirming is_live() returns False without chat_id, so a live deployment always has a non-empty allowlist. Bound is accurate. Confirmed at LOW; impact correctly scoped to paper/demo control-plane DoS.

</details>

---

<a id="l30"></a>

#### L30 — Untracked-position close is a plain (non-reduceOnly) market order that can flip into fresh exposure

**Severity:** LOW · **Confidence:** Suspected · **Pillar:** quality · **Category:** fail-open-close  
**Location:** `bot/skills/telegram_handler.py:12469`

**Description**  
The LIVE-mode fallback that closes an untracked exchange position (12444-12473) sizes a market order to `contracts` read from an earlier fetch_positions snapshot (12447/12451) and submits it at 12469 with close_params = {'productType': 'USDT-FUTURES'} (12464). reduceOnly is never set; tradeSide='close' is added only in hedge mode (12466-12467). On Bitget one-way (USDT-FUTURES) an opposite-side market order that exceeds the live position first reduces then opens the opposite side, so if the position shrinks between the snapshot and the order (e.g. its exchange SL fires in that window) the order over-closes and OPENS fresh opposite exposure. That new position is ungated by the risk engine and is then written as a CLOSED trade (LivePosition status='closed', 12509-12527), so it is invisible to tracking. Confirmed the inconsistency: grep shows telegram_handler.py has zero reduceOnly usages, while live_executor close paths always carry it (4816-4818: 'reduceOnly means the venue clamps to the live position size').

**Evidence**  
Read 12441-12537: contracts from snapshot at 12451, close_params without reduceOnly at 12464, hedge-only tradeSide at 12466-12467, market create_order at 12469, closed-trade record at 12509-12527. grep: 0 reduceOnly in telegram_handler.py; live_executor.py uses reduceOnly in every close path (4816-4818 docstring states it clamps to live size).

**Impact**  
Under a narrow race (untracked position independently shrinks between snapshot and submit), a de-risking manual close could leave a fresh untracked live position with no SL/TP and no risk approval — a fail-open on a purely reducing action. Requires the untracked-position path, a manual close, a same-window size change, and Bitget one-way netting to open the opposite side; low likelihood.

**Remediation**  
Add reduceOnly to close_params on this path so the venue clamps to the actual live size and can never flip, matching the reduceOnly close used throughout live_executor (e.g. 4816).

**Effort:** low

<details><summary>Verifier note</summary>

Confirmed the missing reduceOnly at 12464/12469 and that hedge mode is the only branch adding a close directive; verified by grep that live_executor always uses reduceOnly on closes while telegram_handler never does — the inconsistency and defensive gap are real. Kept LOW/Suspected: the flip outcome depends on a sub-second race plus Bitget one-way over-close netting semantics I could not directly confirm, so the exploit remains suspected while the missing-guard defect is confirmed.

</details>

---

<a id="l31"></a>

#### L31 — Unparseable StakeAccount renders as a measured 0.0 stake, denying the staker

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** failure-honesty  
**Location:** `bot/token/tier_gate.py:485`

**Description**  
`staked_of` skips a StakeAccount whose `version` byte is unrecognised (line 485-491) and a buffer shorter than 98 bytes (line 483-484), then falls through to `return total_base / (10 ** _decimals())` at line 503. Both branches therefore emit an unreadable record as the float 0.0, which `check_user` cannot distinguish from a real zero stake. The code defect is real and it is a genuine violation of the repo's own stated doctrine. Severity is downgraded because neither branch is reachable against the program as it exists today.

**Evidence**  
bot/token/tier_gate.py:483-503 read in full. Line 483 `if len(raw) < STAKE_ACCOUNT_MIN_LEN: continue`; line 485 `if raw[STAKE_VERSION_OFFSET] != STAKE_ACCOUNT_VERSION:` ... line 491 `continue`; line 503 `return total_base / (10 ** _decimals())`. Contrast line 500-502, where a parse exception correctly returns None. Downstream confirmed: bot/token/tier_gate.py:700-704 `if bal is not None: _remember_balance(wallet, bal) ... return ok, ("ok" if ok else "insufficient")`, and the `insufficient` reason maps to `upgrade_message()` at bot/skills/telegram_handler.py:1174-1175 and bot/web/user_gateway.py:180-181, while `unavailable` maps to `unavailable_message()`. Grace window confirmed at bot/token/tier_gate.py:588 (`RCLAW_TIER_GRACE_SECONDS`, default 900.0).

**Impact**  
Latent. No wallet can currently hold a StakeAccount with version != 1: programs/rclaw_staking/src/lib.rs:231 writes `sa.version = StakeAccount::CURRENT_VERSION` on every stake, and `CURRENT_VERSION` is 1 (lib.rs:462). The defect fires only at the first layout bump, at which point stakers are told "stake more" rather than "we could not verify your stake".

**Remediation**  
Track unreadability separately from measurement in `staked_of`: set a local flag in the branches at lines 483 and 485 and `return None` when nothing was successfully summed. `None` already routes to `_cached_balance` and then to `(False, "unavailable")`, which is the honest answer. Then change tests/test_token_tier_gate.py:370 to assert `staked_of(...) is None` and add a `check_user` case asserting reason == "unavailable".

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

CONFIRMED as a code defect, DOWNGRADED from MEDIUM to LOW on reachability. Two corrections to the original write-up. (a) The short-buffer branch at line 483 is effectively unreachable: `staked_of` sends `{"dataSize": STAKE_ACCOUNT_TOTAL_LEN}` (162) as a getProgramAccounts filter at line 464, so every returned account is exactly 162 bytes unless the RPC ignores its own filter. It is defence-in-depth, not a live path. (b) The unknown-version branch requires a v2 StakeAccount to exist on chain; no build can produce one today (lib.rs:231/462). It is genuinely the designed upgrade path — RESERVED headroom keeps the size at 162 so a v2 record would pass the dataSize filter and hit this branch — so the finding is correct that it fires exactly when the version byte is first used for its purpose. (c) The cache-poisoning framing is overstated: `_remember_balance(wallet, 0.0)` adds no harm beyond the immediate denial, because any subsequent successful read would return the same 0.0. The residual harm is the denial itself and its wrong user-facing message, not the cache. The partial-sum case (one v2 record alongside v1 records, summed and printed as a whole) is real and is the sharper form of the defect.

</details>

---

<a id="l32"></a>

#### L32 — web3/sign bypasses the Authority Envelope 24h spend cap (hardcoded spent_today_usd=0.0)

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** authorization-bypass  
**Location:** `bot/web/user_gateway.py:3099`

**Description**  
Verified: handle_web3_sign calls `authorize(env, {"kind": "transfer", ...}, now_ts=_time.time(), spent_today_usd=0.0)` (:3097-3099) with a hardcoded zero and never reads or records to any spend ledger. authority.authorize enforces the per-day notional cap by comparing `spent + n > daily + 1e-9` (authority.py:347-355) — though note a transfer is an _EXFIL_KIND that returns at :293 BEFORE the daily-cap block, so the daily cap in :347-355 is not even evaluated for kind='transfer'; transfers are instead gated by withdraw_allowed + the withdraw allowlist (:281-294). Either way, no cumulative daily-spend accounting is applied to signs, and 0.0 makes any future cap evaluation treat each sign as the day's first. The value-trade path correctly reads and records real 24h spend via `_web_live_ledger().spent(tg_id, now)` / `.record(...)` (:1063-1069). This is a genuine fail-open of a control the envelope is meant to track.

**Evidence**  
user_gateway.py:3097-3099 hardcodes spent_today_usd=0.0 and there is no ledger read/record on the sign path; contrast :1063-1069 which reads ledger.spent and records notional on success. authority.py:347-355 shows the daily cap consumes spent_today_usd (for trade kinds).

**Impact**  
Bounded: web3 signing is hard TESTNET-ONLY — build_and_sign refuses any non-testnet chain before the library/key checks (web3_signer.py:200-205, fail-closed) and evaluate_sign gates on network/admin/enforcing-envelope. No mainnet value is at risk, so this is a testnet-only cap-tracking gap today. It becomes material only if a mainnet signing slice ever reuses this path without wiring the ledger. Reachable only by an admin (or someone past the admin gate via the identity weakness).

**Remediation**  
Source spent_today_usd from the per-user spend ledger (as the trade path does) and record signed notional on success, so daily accounting is consistent across sign and trade paths BEFORE any mainnet signing is enabled. Add a test pinning that the sign path consults/records the ledger.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read user_gateway.py:3055-3159 and 1040-1073, authority.py:238-357, web3_signer.py:190-218. Confirmed the hardcoded 0.0, the divergence from the ledger-backed trade path, and the hard testnet-only enforcement bounding impact. Noted that a 'transfer' kind returns at authority.py:293 before the daily-cap block, so the practical effect is that no daily-spend accounting is applied to signs at all — consistent with a LOW, testnet-only fail-open. Confirmed at LOW.

</details>

---

<a id="l33"></a>

#### L33 — Hardcoded third-party origin permanently allowed by dashboard CORS

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** cors  
**Location:** `dashboard_api.py:22`

**Description**  
`_EXTRA_ORIGINS = {"https://pmvc58g2.mule.page"}` (line 22) is a hardcoded opaque external origin that `_cors_headers` (line 45-46) reflects into `Access-Control-Allow-Origin` regardless of DASHBOARD_CORS_ORIGIN, with no config path to remove it. It applies to the unauthenticated GET /api/snapshot, /api/feed, /api/health (do_GET:96-105). No `Access-Control-Allow-Credentials` header is emitted, so the browser-scriptable exposure is confined to data these endpoints already return unauthenticated; per the repo's public-surface rules the snapshot should carry no dollar amounts.

**Evidence**  
dashboard_api.py:22 `_EXTRA_ORIGINS = {"https://pmvc58g2.mule.page"}`; :45-46 reflects req_origin when `req_origin in _EXTRA_ORIGINS`; :42-50 _cors_headers sends no Allow-Credentials; :96-105 snapshot/feed/health served without auth.

**Impact**  
A fixed external site can script cross-origin reads of the public dashboard snapshot/feed; low impact today since the data is already unauthenticated and no credentials are shared, but the allow-list is baked into source and cannot be tightened without a code change — and becomes a leak channel if the snapshot ever gains non-public fields.

**Remediation**  
Remove the hardcoded origin and drive all allowed origins from configuration (DASHBOARD_CORS_ORIGIN, comma-separated) as the surrounding code already does; put any real partner origin in env, not source.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read dashboard_api.py:18-50 and 92-105. Confirmed the hardcoded origin, its reflection with no credentials header, and that the guarded endpoints are unauthenticated public GETs. A real but genuinely low-severity hygiene/hardcoding finding. CONFIRMED at LOW.

</details>

---

<a id="l34"></a>

#### L34 — Static-file prefix check lacks a trailing separator (sibling-directory prefix match)

**Severity:** LOW · **Confidence:** Suspected · **Pillar:** security · **Category:** path-traversal  
**Location:** `dashboard_api.py:69`

**Description**  
`_serve_static` (line 69) and do_HEAD (line 186) validate the resolved path with `filepath.startswith(os.path.realpath(base_dir))` without a trailing `os.sep`. Contrary to the original write-up, this is NOT limited to symlink abuse: BaseHTTPRequestHandler does not normalize `..`, so a raw request like `/../website_backup/secret` yields `realpath(os.path.join('/app/website','../website_backup/secret')) = /app/website_backup/secret`, and `'/app/website_backup/secret'.startswith('/app/website')` is True — the check passes for a sibling directory whose path shares the base as a string prefix. The precondition is that such a sibling directory (e.g. `website_backup`, `dashboard_static_old`) exists on disk. It does not exist in the current repo (only `website/` and `dashboard_static/` are present, with no prefix-sharing siblings), so the flaw is latent, not presently exploitable.

**Evidence**  
dashboard_api.py:67-69 `filepath = os.path.realpath(os.path.join(base_dir, rel_path.lstrip('/')))` then `if not filepath.startswith(os.path.realpath(base_dir))`; :184-186 same pattern in do_HEAD; `ls` of repo root shows website/ and dashboard_static/ with no prefix-sharing sibling directory.

**Impact**  
If a directory whose path shares the served base as a string prefix is ever created adjacent to website/ or dashboard_static/, a raw `..` request (or an in-tree symlink to it) could read/HEAD-probe files outside the intended root. No such directory exists today, so this is a hardening gap.

**Remediation**  
Compare against `os.path.realpath(base_dir) + os.sep` (allowing the directory itself explicitly), or use `os.path.commonpath([filepath, base]) == base`.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Read dashboard_api.py:65-90 and 160-199, and traced the join/realpath/startswith logic. The missing os.sep is a real defect, and I corrected the finding's mechanism: it is reachable via a plain `..` to a prefix-sharing sibling (BaseHTTPRequestHandler doesn't collapse `..`), not only via a symlink. However, `ls` of the repo root confirms no prefix-sharing sibling directory currently exists, so exploitability is unproven today. CONFIRMED as a latent defect at LOW / Suspected.

</details>

---

<a id="l35"></a>

#### L35 — Committed script places real production orders bypassing every safety gate

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** ungated-execution-path  
**Location:** `micro_trade_test.py:87`

**Description**  
micro_trade_test.py is a git-tracked standalone script that connects to Bitget with sandbox hardcoded False (line 27), loads BITGET_API_KEY/SECRET/PASSPHRASE via load_dotenv(override=True) (line 16), and places a real market BUY (line 87) then market SELL (line 121) of $5 via raw ccxt.create_order. It imports no bot.config / bot.core.* modules and touches none of the safety machinery (confirm_trade, _preflight_check, is_live(), SIMULATION_MODE veto, trading_halted kill switch, MICRO_/PER_USER caps). It is genuinely the only real-order OPEN path in the tree outside LiveExecutor.execute(). However, grep across *.py/*.yml/*.sh/*.toml found ZERO references to it anywhere — no CI job, no cron, no launcher invokes it. It only fires on a deliberate manual `python micro_trade_test.py` in an environment that already holds live Bitget credentials, so the 'stray cron/CI step' impact is not supported by the tree.

**Evidence**  
Confirmed by reading: line 16 load_dotenv(override=True); line 27 "sandbox": False; line 87 buy market create_order; line 121 sell market create_order; no bot.* imports. `git ls-files` confirms it is tracked. `grep -rn micro_trade_test` over py/yml/sh/toml returned no external reference — nothing auto-runs it.

**Impact**  
An operator who deliberately runs this file with production credentials in env opens ~$5 of real exposure with no cap/halt/veto enforcement, and override=True would clobber a SIMULATION_MODE env set elsewhere. Not remotely triggerable and not wired into any automated path, so realistic blast radius is a manual foot-gun, not an exposed execution route.

**Remediation**  
Delete the script, or move it out of the repo, or hard-gate it behind an explicit non-default env flag and drop load_dotenv(override=True); default sandbox to CONFIG.exchange.sandbox instead of a hardcoded False.

**Effort:** low

<details><summary>Verifier note</summary>

Read the entire file (180 lines): all factual claims hold — tracked, sandbox False, override=True, two raw live market orders, zero safety imports. Downgraded MEDIUM->LOW because the stated impact (stray cron/CI) is refuted: grep found no invocation anywhere in the tree, so it requires deliberate manual execution with prod creds already present. Real hygiene/foot-gun issue, not a reachable ungated route.

</details>

---

<a id="l36"></a>

#### L36 — nginx location blocks with add_header discard the server-level security headers

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** header-inheritance  
**Location:** `nginx.conf:46`

**Description**  
nginx inherits add_header from an outer level only when the current level defines none. Five security headers are set at server level (nginx.conf:33-37) and then `location /` (:46), the static-asset location (:52) and `location /api/` (:77-79) each define their own add_header, dropping HSTS, X-Frame-Options, nosniff, Referrer-Policy and CSP for those paths. Only `location = /health` and `location /gateway/` inherit them.

**Evidence**  
nginx.conf:33-37 (the five `always` headers), :44-47 `location / { try_files ...; add_header Cache-Control "no-cache"; }`, :50-53 `location ~* \.(js|css|png|ico|woff2?)$ { expires 7d; add_header Cache-Control "public, immutable"; }`, :77-79 three Access-Control-Allow-* add_headers inside `location /api/`.

**Impact**  
The static pages nginx serves, their assets, and /api/* JSON responses carry none of the five headers despite the config appearing to set them all.

**Remediation**  
Extract the five headers into an include and repeat the include in every location that defines add_header (or avoid add_header in locations entirely), plus a curl smoke test asserting all five on /, /api/health and a .js asset.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Mechanism CONFIRMED by reading nginx.conf:33-79 — the three locations do define add_header and nginx's inheritance rule is as described; `always` affects error responses, not inheritance. Impact DOWNGRADED from MEDIUM to LOW: the finding says 'the pages that most need CSP' and invokes the app/ Express tier, but docker-compose.yml:127 mounts ./website as the nginx root and `ls website` is a static marketing set (index.html, privacy.html, images, demo video) with exactly one inline <script> and no user-rendered content; app/ is not a compose service at all and is reachable only as its own deployment where server.js:150-176 sets its own headers. So what actually loses CSP/X-Frame-Options is a static brochure page and a JSON API. Real defect, materially smaller blast radius than claimed.

</details>

---

<a id="l37"></a>

#### L37 — The Token-2022 leg, including reject_hazardous_extensions, has never been executed by any test

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** untested-security-control  
**Location:** `programs/rclaw_staking/src/lib.rs:129`

**Description**  
`reject_hazardous_extensions` early-returns for any mint not owned by the Token-2022 program (lib.rs:129-131). Every mint constructed anywhere in the program's test tree is a legacy SPL Token mint, so the early return is taken on every execution and the function's real body — the unpack at :134, `get_extension_types()` at :136, and all six rejection arms at :143-162 — has never run. Verified by exhaustive grep: `token_2022|Token2022|TOKEN_2022|token-2022` returns zero matches across programs/rclaw_staking/tests/. The real $RCLAW mint is created under `TOKEN_2022_PROGRAM_ID` (token/scripts/create_token.mjs:96, :116).

**Evidence**  
programs/rclaw_staking/src/lib.rs:128-131 read: `// Only Token-2022 accounts have a TLV extension region.` / `if *mint_ai.owner != anchor_spl::token_2022::ID { return Ok(()); }`. Mint constructors confirmed legacy: programs/rclaw_staking/tests/attack.rs:109-112 and :223 (`token_program: spl_token::id()`), tests/solvency.rs:131,:150,:185-186, tests/bpf_smoke.mjs:80,:82,:106. `grep -rn 'token_2022|Token2022|TOKEN_2022' programs/rclaw_staking/tests/` => no matches. Against token/scripts/create_token.mjs:85 `getMintLen([ExtensionType.MetadataPointer])` and :96/:104/:112/:116 `TOKEN_2022_PROGRAM_ID`. Additionally: programs/rclaw_staking/tests/bpf_smoke.mjs:18-19 and docs/TOKEN_SECURITY_AUDIT.md:109-110 both state the measured worst case "includes ... a TLV extension walk in reject_hazardous_extensions()" — with a legacy mint that walk is short-circuited at lib.rs:129, so both statements are false.

**Impact**  
No demonstrated defect. The cost is absence of evidence on the configuration that will hold value, plus two documentation statements that assert coverage which does not exist. Materially bounded by the program's own deployment gate.

**Remediation**  
Add a Token-2022 case to programs/rclaw_staking/tests/attack.rs: allocate with `spl_token_2022::extension::ExtensionType::try_calculate_account_len` and initialise under `spl_token_2022::id()`, then (a) a positive stake/unstake round-trip on a `MetadataPointer`+`TokenMetadata` mint (the real $RCLAW shape) asserting real balances, and (b) one negative case per rejected arm asserting the specific `UnsupportedMintExtension` code, `TransferFeeConfig` first. Separately, correct programs/rclaw_staking/tests/bpf_smoke.mjs:18-19 and docs/TOKEN_SECURITY_AUDIT.md:109-110, which currently claim the TLV walk was measured.

**Effort:** Medium (1–3 days)

<details><summary>Verifier note</summary>

CONFIRMED as a coverage gap, DOWNGRADED from MEDIUM/security to LOW/quality. Reasons for the downgrade: (1) I re-read the code the finding says is untested and found no defect in it either — the finding is honest that it reports absence of evidence, and absence of a test is a quality finding, not a security one; (2) programs/rclaw_staking/src/lib.rs:1-11 labels the program `DRAFT / DEVNET-ONLY`, `DO NOT DEPLOY TO ANY CLUSTER HOLDING REAL VALUE`, unaudited, with real deployment explicitly gated behind Phase 0 Guardrails including a third-party smart-contract audit — a gate that would cover exactly this. What keeps it above INFO, and what I did independently confirm, is that the gap is not merely undocumented but actively mis-documented: bpf_smoke.mjs:18-19 and docs/TOKEN_SECURITY_AUDIT.md:109-110 both present the 57,124/28,172 CU figures as including the TLV extension walk, and with `TOKEN_PROGRAM_ID` at bpf_smoke.mjs:80/:106 the walk provably never executed. That is a wrong mitigation claim, which is the one condition under which a documented gap is still reportable.

</details>

---

<a id="l38"></a>

#### L38 — RustSec ratchet compares advisory IDs only — a dev-only advisory moving into the shipped tree keeps the gate green

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** security · **Category:** weak-supply-chain-gate  
**Location:** `scripts/cargo_audit_gate.py:166`

**Description**  
The gate fails only on advisory ids absent from the baseline. The per-advisory `shipped` flag, computed each run via `cargo tree -e normal`, is recorded and printed but never compared against the baselined value, so a baselined advisory transitioning from dev-only to the program's normal dependency tree does not fail the gate.

**Evidence**  
scripts/cargo_audit_gate.py:166 `new_ids = sorted(set(found) - set(known))`; :168 `shipped_now = sum(...)`; :183-185 `if not new_ids: print("\nNo new advisories..."); return 0`. The shipped flag is computed at :113 (in_shipped_tree, :88-101) and used only for display at :124 and :191. .cargo-audit-baseline.json records six entries with "shipped": false (ring :21, quinn-proto :27 and :51, rustls-webpki :33/:39/:45).

**Impact**  
The shipped-vs-dev-only distinction the baseline advertises as its value-add is recorded but not enforced.

**Remediation**  
Add a failure branch for `found[id]["shipped"] and not known[id].get("shipped")`, naming the transition. Three lines beside the existing new_ids check.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Code reading confirms the flag is never compared — CONFIRMED as a gate weakness. DOWNGRADED MEDIUM -> LOW because the finding calls the transition 'silent' and it is not: lines 170-173 print 'RustSec advisories in Cargo.lock: N (M in the shipped tree, K dev-only)' on every run, so a crate crossing into the normal tree changes that printed count even though it does not fail the build. The scenario also requires a dependency-graph change that keeps the same advisory id, and the whole Solana 1.18 stack is deliberately pinned (docstring :4-16), which makes it unlikely without a deliberate bump. Real, worth the three lines, not a MEDIUM.

</details>

---

<a id="l39"></a>

#### L39 — StakeAccount::RESERVED is unpinned in both languages; changing it alone keeps CI green and zeroes every staker

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** cross-language-contract  
**Location:** `tests/test_token_tier_gate.py:762`

**Description**  
The `dataSize` filter sends 162 (tier_gate.py:464, `STAKE_ACCOUNT_TOTAL_LEN`), derived from the Python-only constants `STAKE_ACCOUNT_SPACE = 90` and `STAKE_ACCOUNT_RESERVED = 64` (tier_gate.py:172-179). `test_the_account_size_agrees_across_languages` compares Python to Python for the two terms that matter: only `DISCRIMINATOR` comes from Rust. `_rust_layout_constants()` parses only the body of `pub mod layout`, which contains DISCRIMINATOR plus seven offsets and nothing else; `StakeAccount::SPACE` and `StakeAccount::RESERVED` live in `impl StakeAccount` and are never read. The Rust-side test asserts only `buf.len() == DISCRIMINATOR + SPACE` and never mentions RESERVED. So RESERVED — the constant whose documented purpose is to be spent later — is pinned nowhere in CI.

**Evidence**  
tests/test_token_tier_gate.py:755-763 read: `assert tg.STAKE_ACCOUNT_TOTAL_LEN == (rust["DISCRIMINATOR"] + tg.STAKE_ACCOUNT_SPACE + tg.STAKE_ACCOUNT_RESERVED)` — 162 == 8 + 90 + 64 with 90 and 64 both Python. Parser scope confirmed at tests/test_token_tier_gate.py:728-731 (`start = src.index("pub mod layout {")` / `body = src[start:src.index("\n}", start)]`). programs/rclaw_staking/src/lib.rs:527-537 — `pub mod layout` defines DISCRIMINATOR + 7 offsets only. lib.rs:465-468 — `pub const SPACE: usize = 1 + 32 + 32 + 8 + 8 + 8 + 1;` and `pub const RESERVED: usize = 64;`, consumed at lib.rs:355 as `space = 8 + StakeAccount::SPACE + StakeAccount::RESERVED`. Note SPACE is written as a sum, so the parser's `= (\d+);` regex could not match it even if the scope were widened. lib.rs:565-570 — the Rust assertion is `buf.len() == layout::DISCRIMINATOR + StakeAccount::SPACE`; RESERVED never appears. The only 162-byte assertion anywhere is programs/rclaw_staking/tests/bpf_smoke.mjs:216 (`d.length === 162`), and .github/workflows/ci.yml does not run bpf_smoke.mjs — CI runs `cargo test -p rclaw_staking --all-targets` (:95, :106) and `cargo-build-sbf` (:136) only.

**Impact**  
Spending reserved headroom is safe (SPACE grows, RESERVED shrinks, total stays 162). Editing RESERVED itself is a one-token change that passes every CI gate and makes the on-chain size diverge from the dataSize filter, so getProgramAccounts returns [] for every wallet and `staked_of` returns a confident 0.0 — the same false zero as the first finding, with no failed read anywhere to signal it.

**Remediation**  
Move `SPACE` and `RESERVED` into `pub mod layout` as plain integer `pub const`s (they are already part of the public contract that module exists to publish), or widen `_rust_layout_constants()` to parse `impl StakeAccount` and handle the `1 + 32 + ...` form. Then assert `tg.STAKE_ACCOUNT_TOTAL_LEN == rust["DISCRIMINATOR"] + rust["SPACE"] + rust["RESERVED"]` so every term has a Rust source, and raise the `len(rust) >= 8` floor at line 770 accordingly.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

CONFIRMED at the stated LOW severity; I reproduced the whole chain by reading. One correction to the write-up's framing: programs/rclaw_staking/README.md:44-52 says the *offsets* are machine-checked in both directions, and that claim is true — the Rust test pins all seven offsets against the Borsh encoding and tests/test_token_tier_gate.py:740-753 pins five of them from Python. The README does not claim the account *size* is cross-checked, so it is incomplete rather than wrong, and the finding's 'asserts the opposite' overstates it; the finding's own next sentence concedes this. I also found one check the finding missed and which cuts against it slightly: programs/rclaw_staking/tests/bpf_smoke.mjs:216 does assert `d.length === 162` against a real deployed account. That does not rescue the gap, because bpf_smoke.mjs is a manual local-validator script that .github/workflows/ci.yml never invokes, so the finding's core claim — a lone RESERVED edit keeps CI green — holds. I also note tests/test_token_tier_gate.py:740-753 omits STAKED_AT_OFFSET and BUMP_OFFSET from its `pairs` dict, so the Python side cannot even infer SPACE from a pinned BUMP_OFFSET + 1.

</details>

---

<a id="l40"></a>

#### L40 — watchdog.sh SIGKILLs the bot with no SIGTERM and reports a restart as successful without verifying it survived

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** process-supervision  
**Location:** `watchdog.sh:15`

**Description**  
is_running() uses `kill -0` plus a `pgrep -f` pattern fallback rather than scripts/verify_bot_alive.sh; the restart path sends `pkill -9` with no SIGTERM and no grace period; and the success line is printed unconditionally after `nohup ... &` with no check that the new process is still alive.

**Evidence**  
watchdog.sh:12-22 (is_running: `kill -0 "$pid"` at :15, `pgrep -f "python.*bot\.main.*telegram"` at :20); :31-32 `# Clean up any zombie processes` / `pkill -9 -f "python.*bot\.main"`; :36-37 `nohup python3 -m bot.main --mode telegram >> "$LOGFILE" 2>&1 &` then an unconditional `echo "... started PID $!"`. scripts/verify_bot_alive.sh exists (its header at :18-48 prescribes exactly the --pid form) and watchdog.sh never references it.

**Impact**  
SIGKILL with no grace period can truncate in-flight writes to data/ and logs/audit_chain.jsonl; a restart that dies immediately (bad .env, port in use) is logged as a successful start and retried every minute with no signal.

**Remediation**  
Send SIGTERM and wait before escalating to SIGKILL, and gate the final log line on `scripts/verify_bot_alive.sh --pid $!` succeeding.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

File read in full; the SIGKILL-without-SIGTERM and unverified-start defects are CONFIRMED verbatim. DOWNGRADED MEDIUM -> LOW on two grounds. (1) The headline zombie claim is weak in THIS script's topology: watchdog.sh launches with nohup and then exits, so the bot is orphaned and reparented to PID 1, which reaps it — a persistent zombie requires a long-lived non-reaping parent, which the cron-driven watchdog is not. CLAUDE.md's zombie warning is about deploy.sh, which stays parent. (2) The issue is already an open, tracked backlog item, not an undiscovered defect: docs/AUDIT_2026-08-12.md:105 (M26) and :179 (task 2.10) name watchdog.sh:15 and the missing verify_bot_alive.sh call explicitly, and :206 records that it is unknown whether the cron is even installed on the deploy host. Tracked-but-open, so not refused outright.

</details>

---

<a id="l41"></a>

#### L41 — Public site advertises '23 fail-closed risk checks'; the manifest has 21 and SECURITY.md says only 16 are fail-closed

**Severity:** LOW · **Confidence:** Confirmed · **Pillar:** quality · **Category:** documentation-accuracy  
**Location:** `website/index.html:9`

**Description**  
The og:description claims 23 fail-closed risk checks. config/risk_manifest.yaml — which SECURITY.md:24 names as authoritative — has 21 entries, and SECURITY.md itself states 16 are strict fail-closed, 1 is fail-open (#17 LIQUIDITY) and 4 gracefully skip.

**Evidence**  
website/index.html:9 read verbatim: `<meta property="og:description" content="An AI trading engine you can watch, chat with, and trade alongside. 23 fail-closed risk checks, human-in-the-loop confirmation, simulation-first.">`. SECURITY.md:24 read verbatim with the 16/1/4 breakdown. `grep -c '^  - id:' config/risk_manifest.yaml` -> 21, last id at :280.

**Impact**  
A public safety claim overstating both the count and the failure semantics. The fail-open one is the material part: #17 passes when order-book data is unreadable — the 'unreadable rendered as a pass' shape CLAUDE.md is built around — while the marketing copy asserts the opposite.

**Remediation**  
Change the og:description to match the manifest and add a test deriving the number from config/risk_manifest.yaml, asserting every public surface quoting a check count agrees.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

All three sources read and cross-checked; the numbers are exactly as reported. SECURITY.md is honest (it also self-discloses at :43 that the security badge is not CI-backed), so the inaccuracy really is confined to the marketing page. I did not verify the ollama/ notebook parenthetical and it is not load-bearing. LOW: no code affected, but it is a public claim about a safety property on a repo whose product is verifiable claims.

</details>

---

### INFO findings

<a id="i1"></a>

#### I1 — .gitignore lists ollama/ and AUDIT_REPORT*.md while both are tracked, so the rules are inert for them

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** secrets-hygiene  
**Location:** `.gitignore:69`

**Description**  
.gitignore:69 lists `ollama/` and :71 lists `AUDIT_REPORT*.md`, but both sets of paths are tracked. .gitignore has no effect on tracked files, so the entries express an intent the repository does not implement and create a false expectation of exclusion.

**Evidence**  
`grep -n 'ollama/\|AUDIT_REPORT' .gitignore` -> 69:ollama/ and 71:AUDIT_REPORT*.md, both confirmed at those lines. `git ls-files ollama/` -> Modelfile, Modelfile.finetuned and the training notebooks; `git ls-files | grep AUDIT_REPORT` -> docs/AUDIT_REPORT_V4/V5/V6/V6.1/V7/V8.md (6 files, matched by the unanchored pattern at any depth).

**Impact**  
No secret exposure. Residual risk is expectation drift: a contributor reading .gitignore may assume ollama/ is excluded and leave a training credential there. The gitleaks history scan (ci.yml:290-306) covers those paths regardless.

**Remediation**  
Make it true one way or the other: `git rm -r --cached` the paths, or delete the two .gitignore lines.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Line numbers and tracked-file lists verified exactly. I did not re-run the finding's secret-shaped greps over ollama/ and docs/, so the 'no secret material found' half is inherited rather than independently confirmed — but it is the reassuring half, and the verdict does not rest on it. I did verify the gitleaks step exists at ci.yml:290-306 with a checksum-pinned binary and a redacted fingerprint baseline, which is the real backstop. INFO is correct.

</details>

---

<a id="i2"></a>

#### I2 — Both shipped-tree RustSec advisories are present in Cargo.lock but unreachable from this program's code

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** security · **Category:** vulnerable-dependency  
**Location:** `Cargo.lock:1419`

**Description**  
The two baselined advisories flagged `shipped: true` are genuinely in the lockfile and genuinely in the program's normal dependency tree via anchor-lang/anchor-spl, so the gate's tree-reachability labelling is accurate. Exploitability for this program is nil: it never signs and holds no secret scalar.

**Evidence**  
Cargo.lock:1419-1420 `name = "ed25519-dalek"` / `version = "1.0.1"`; Cargo.lock:1202-1203 `name = "curve25519-dalek"` / `version = "3.2.1"`. programs/rclaw_staking/Cargo.toml:34-37 — `[dependencies]` is exactly anchor-lang 0.30.1 and anchor-spl 0.30.1; solana-program-test/solana-sdk are under [dev-dependencies] at :39-41. `grep -rn 'ed25519|Signature|verify' programs/rclaw_staking/src/` -> no matches. .cargo-audit-baseline.json:5-16 records both with "shipped": true.

**Impact**  
No exploitable path in this program. RUSTSEC-2022-0093 needs a caller that signs with a secret key; RUSTSEC-2024-0344 needs timing measurement against a secret scalar. An on-chain program does neither.

**Remediation**  
No code change now. Clear both as part of the planned Solana bump before a value-bearing deployment; re-assess if the program ever adds its own signature verification.

**Effort:** —

<details><summary>Verifier note</summary>

I reproduced every check independently: both lockfile entries at the exact cited lines, the dependency/dev-dependency split in programs/rclaw_staking/Cargo.toml, and the empty grep over the program source. The baseline's shipped/dev-only framing is accurate and the docstring rationale at scripts/cargo_audit_gate.py:4-27 for baselining rather than bumping Solana is sound. Correctly filed as INFO — this is the reachability check rule 3 asks for, and it comes out in the repo's favour.

</details>

---

<a id="i3"></a>

#### I3 — Connection pool built from raw DATABASE_URL with no explicit TLS or pool limits

**Severity:** INFO · **Confidence:** Suspected · **Pillar:** security · **Category:** transport-security  
**Location:** `app/db.js:39`

**Description**  
The pool is created with `mysql.createPool(process.env.DATABASE_URL)` and no options object — TLS/timeouts/limits are left to whatever the connection string embeds. mysql2 accepts ssl and pool params directly in the URL, so the deployment can (and per the runbook, against TiDB must) specify TLS there. The plaintext-credential concern is largely refuted by the environment: the finding itself notes TiDB enforces TLS with ER_SECURE_TRANSPORT_REQUIRED, meaning a TLS-less URL is REJECTED (fails closed), not silently transmitted in plaintext.

**Evidence**  
Verified line 39 `pool = mysql.createPool(process.env.DATABASE_URL);` — no options object. No ssl/connectTimeout/connectionLimit passed programmatically. mysql2 defaults connectionLimit to 10 (bounded by default, not unbounded). The deployed DATABASE_URL is not in the repo, so its ssl params cannot be confirmed.

**Impact**  
Against a TLS-enforcing server (TiDB, per the runbook), a URL lacking ssl fails to connect rather than leaking credentials — the transport risk is fail-closed. Residual concern is minor hygiene: transport security and connect timeouts depend on the URL string rather than being pinned in code, and there is no explicit connectTimeout so a stalled DB could hang handlers longer than desired.

**Remediation**  
Optionally pass an explicit options object merging the parsed URL with `ssl: { minVersion: 'TLSv1.2', rejectUnauthorized: true }` and an explicit connectTimeout, so transport security and fail-fast behavior are pinned in code rather than left to the URL. Low priority given TiDB's server-side enforcement.

**Effort:** low

<details><summary>Verifier note</summary>

Read line 39 — the code matches. Downgraded LOW->INFO: the headline plaintext-credential impact is undercut by TiDB failing closed on missing TLS (fail-closed, not fail-open), and TLS/pool params are legitimately configurable in the URL. Kept as an INFO hygiene note only; confidence Suspected because the deployed URL cannot be inspected from the repo.

</details>

---

<a id="i4"></a>

#### I4 — Key sort uses UTF-16 code units, Python twin sorts by code point

**Severity:** INFO · **Confidence:** Suspected · **Pillar:** quality · **Category:** canonicalization-divergence  
**Location:** `app/lib/canonical.js:21`

**Description**  
canonicalStringify sorts object keys with Object.keys(value).filter(...).sort(), the default JS comparator, which orders by UTF-16 code unit. The pinned Python twin (csf.py: json.dumps(obj, sort_keys=True, ...)) orders by Unicode code point. For any key containing a supplementary-plane character (>U+FFFF, a surrogate pair 0xD800-0xDFFF in UTF-16) the two orderings diverge, producing different canonical bytes and a different SHA-256 — a proof that verifies on one side and fails on the other.

**Evidence**  
Read canonical.js:21 `const keys = Object.keys(value).filter(k => value[k] !== undefined).sort();` (default UTF-16 sort) vs csf.py `return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")` (code-point sort). Orders agree only within the BMP.

**Impact**  
A canonicalization mismatch between the JS verifier and the Python sealer would break the byte-equality the proof-of-PnL scheme rests on. Reachability confirmed low: verified _FILL_FIELDS keys in csf.py are all fixed ASCII field names (market, side, price, qty, fee, fee_ccy, ts, source_ref, trust_tier), so no astral-plane key reaches this sort in current call paths — no live bug, hygiene/latent-risk only.

**Remediation**  
If keys could ever become non-ASCII, sort by code point explicitly (compare via [...k] spread or normalize) to match Python; otherwise add a test asserting sealed-bundle keys are ASCII so a future non-ASCII key fails loudly rather than silently diverging.

**Effort:** low

<details><summary>Verifier note</summary>

Independently read canonical.js:21 and csf.py canonical()/fill core. Confirmed the sort-order divergence is a real code fact, and confirmed the reachability gap: all _FILL_FIELDS keys are fixed ASCII, so no supplementary-plane key reaches the sort today. Verdict CONFIRMED for the divergence property, held at INFO/Suspected because no live mismatch is reachable in current call paths.

</details>

---

<a id="i5"></a>

#### I5 — Outbound response bodies are accumulated with no size cap

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** security · **Category:** resource-exhaustion  
**Location:** `app/lib/http_cache.js:22`

**Description**  
fetchJSON buffers the full response into a growing string (`res.on('data', d => body += d)`) with an 8s connect/inactivity timeout but no maximum-size bound and no res.destroy() past a threshold. The fetched hosts are hardcoded, trusted APIs (api.bitget.com in market.js; the finding also cites gateway.js and opensea.js), and the URL/host is never attacker-controlled, so this is a hardening gap rather than a client-reachable DoS.

**Evidence**  
Read http_cache.js:18-31: `let body = ''; res.on('data', d => body += d); res.on('end', ...)` with only a timeout, no length accumulation guard. Callers in market.js pass hardcoded `https://api.bitget.com/...` URLs (only symbol/granularity path params, validated, are interpolated — not the host). I verified the primary cited site (http_cache.js); the gateway.js/opensea.js mirrors were asserted by the first auditor and not independently re-read here.

**Impact**  
Memory-pressure DoS only if a hardcoded trusted upstream is compromised or malfunctions and returns an unbounded body; not reachable from untrusted client input since hosts/paths are not attacker-controlled. Correctly rated INFO.

**Remediation**  
Cap accumulated bytes in the shared fetch helper (track body length, destroy the response and reject past a few MB).

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed the unbounded-accumulation shape at http_cache.js:20-23 by direct read. The finding's own reachability caveat is accurate — trusted hardcoded upstreams, not attacker input — so INFO/hygiene is the right severity. Not refuted; not upgraded.

</details>

---

<a id="i6"></a>

#### I6 — validateArgs ignores minimum/maximum/minLength bounds the tool schemas advertise

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** input-validation  
**Location:** `app/routes/mcp.js:908`

**Description**  
validateArgs (mcp.js:893-919) checks object shape, known keys, primitive types and a 200-char string cap, but never enforces schema minimum/maximum/minLength/maxLength/enum. Handlers clamp the top via Math.min but not the bottom, so a negative integer limit passes validation and reaches `LIMIT ${limit}` (e.g. get_signals :467-471, get_agent_feed :520-523) as `LIMIT -5`.

**Evidence**  
Read mcp.js:893-919 and the handlers at :466-472/519-524. Confirmed: validateArgs accepts any integer (Number.isInteger passes -5), and `Math.min(parseInt(args?.limit) || 20, 50)` gives -5 for input -5. That reaches an inlined LIMIT and produces a SQL error, not injection (the value is a validated JS number).

**Impact**  
Hygiene: advertised contract and enforcement differ; malformed-but-typed input yields a tool failure instead of the -32602 the schema promises. No injection.

**Remediation**  
Enforce minimum/maximum/minLength/maxLength/enum in validateArgs, or clamp lower bounds in the handlers.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Confirmed validateArgs enforces none of the numeric/length bounds and traced that a negative limit reaches an inlined LIMIT as an integer (SQL error, not injection). INFO is correct.

</details>

---

<a id="i7"></a>

#### I7 — On-chain signing/broadcast and contract-deploy endpoints carry no web-tier authz and no 2FA step-up

**Severity:** INFO · **Confidence:** Suspected · **Pillar:** security · **Category:** authorization-defense-in-depth  
**Location:** `app/routes/web3_execute.js:62`

**Description**  
The web3_execute router mounts only authMiddleware + a rate limit (lines 21-22). The /sign handler (62-87, signs+broadcasts a native-value transfer) and /deploy handler (131-152, deploys contract bytecode), plus /cross-plan and /sign/prepare, call resolveBotIdentity(req) and forward to the gateway with no local plan=='admin' re-read and no stepUpBlock TOTP challenge. All authorization on these on-chain money paths rests entirely on the bot gateway's server-side admin re-check + default-OFF signing gate + enforcing Authority Envelope, which cannot be inspected from these files — so no exploitable bypass is asserted. It is flagged only as a defense-in-depth gap: the equivalent money-moves in this domain each add a web-tier gate — staking.js:58-64 requires a fresh TOTP (stepUpBlock), controls.js:75-79 requires TOTP to enable live and controls.js:120-124 re-reads plan=='admin' from the DB — while the on-chain signing path has neither, so a stolen-but-valid session of any authenticated user reaches the gateway with no local gate before a broadcast is attempted.

**Evidence**  
web3_execute.js:21-22 router.use(authMiddleware); router.use(rateLimit(...)) — no admin gate. /sign (62-87) and /deploy (131-152) forward via gateway.postGateway with only resolveBotIdentity, no stepUpBlock, no plan check. Compare staking.js:19,58-64 (stepUpBlock over totp_enabled/totp_secret) and controls.js:76-78 (stepUpBlock) and controls.js:120-122 (re-read plan, 403 if != 'admin'). File header (web3_execute.js:52-61,121-130) states the gateway is authoritative, testnet-only, envelope-enforced, mainnet hard-blocked.

**Impact**  
Authorization reduced to a single enforcement point (the gateway) with no local defense-in-depth and, on the signing path, no 2FA step-up despite controls/staking having one. Constrained by the endpoints being testnet-only behind a default-OFF signing gate and an enforcing envelope, so real-fund exposure is low even if the gateway check regressed. Not a confirmed exploit.

**Remediation**  
Add the same belt-and-suspenders the other money-moves use: re-read plan=='admin' from the DB and require stepUpBlock TOTP before forwarding /sign and /deploy, so authorization does not depend solely on the gateway and the signing path matches the 2FA posture of controls-enable and staking.

**Effort:** low — mirror the plan re-read + stepUpBlock guard already present in controls.js/staking.js at the top of the /sign and /deploy handlers

<details><summary>Verifier note</summary>

Read web3_execute.js in full: /sign (62-87) and /deploy (131-152) indeed have only authMiddleware+rateLimit at the web tier, no plan check, no stepUpBlock. Verified the comparison claims: staking.js:58-64 does gate with stepUpBlock, controls.js:76-78 gates enable-live with stepUpBlock, controls.js:120-122 re-reads plan=='admin'. The observation is factually accurate. Kept INFO/Suspected: the gateway is authoritative and cannot be read from here, endpoints are testnet-only, so no exploitable bypass is provable — an honest defense-in-depth note, not a live vulnerability. CONFIRMED as an accurate observation.

</details>

---

<a id="i8"></a>

#### I8 — Daily-loss circuit breaker auto-resumes trading at UTC rollover without human acknowledgement (default ON)

**Severity:** INFO · **Confidence:** Likely · **Pillar:** quality · **Category:** unsafe-default  
**Location:** `bot/config.py:326`

**Description**  
DAILY_LOSS_BREAKER_AUTORESET defaults True (config.py:326). _should_autoreset_daily_breaker (risk_engine.py:3309-3323) clears a tripped breaker at UTC-day rollover ONLY when the trip cause was 'daily_loss', the day has genuinely rolled, and the loss-streak block is not active. drawdown/streak/manual trips stay manual. Because _live_daily_pnl only rolls to 0 on a trade close (risk_engine.py:571-573) and no closes occur while halted, after the autoreset the daily-loss check at 1237 (which reads _live_daily_pnl via 1226) immediately re-trips on the stale accumulator — so the feature is fail-safe: it re-evaluates and re-latches rather than silently resuming into a still-breached condition. This is documented intended behavior, not a defect.

**Evidence**  
config.py:320-326 documents the design explicitly ('If the new day is also bad, the daily-loss check re-trips immediately') and defaults True; risk_engine.py:3319-3323 scopes autoreset to cause=='daily_loss' with streak<max guard; risk_engine.py:1226 sets _daily_pnl=self._live_daily_pnl and 1237 re-runs the daily-loss check same evaluate() pass; risk_engine.py:571-573 shows _live_daily_pnl resets only on close.

**Impact**  
A daily-loss halt is auto-cleared at day rollover but immediately re-evaluated in the same evaluate() pass, re-tripping if the condition still holds; drawdown/streak/manual trips remain manual. The only path to a silent resume is a genuine new-day PnL roll, which is the intended semantic. This is fail-safe, documented behavior — an observability nuance at most, not a fail-open defect.

**Remediation**  
Optional hardening only: emit an explicit operator notification on every auto-reset for auditability, and/or reset _live_daily_pnl on UTC-day change inside evaluate() so the re-evaluation reflects the fresh day rather than the stale accumulator. No change is required for safety.

**Effort:** low

<details><summary>Verifier note</summary>

Read config.py:320-326 (default True, behavior documented as intended and fail-safe), risk_engine.py:3309-3323 (scoping to daily_loss + streak guard), 1125-1142 (autoreset clears then evaluate continues), 1217-1245 (daily-loss check re-runs on _live_daily_pnl), 566-577 (accumulator rolls only on close). Behavior is confirmed but it is intended, documented, and fail-safe (re-trips on stale accumulator; drawdown/streak stay manual). This is not a defect — downgrading from LOW to INFO; the original 'warrants operator awareness' framing overstates it.

</details>

---

<a id="i9"></a>

#### I9 — _recover accepts malleable (high-s) signatures and any v >= 27 without validation

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** security · **Category:** signature-malleability  
**Location:** `contracts/rune/RuneOfEntry.sol:79`

**Description**  
_recover (RuneOfEntry.sol:73-80) passes (v, r, s) to ecrecover after only `if (v < 27) v += 27;` (:78). There is no EIP-2 upper-half-order rejection of `s` and no `v in {27,28}` constraint, so (r, n-s, v^1) is a second byte-distinct encoding that recovers the same signer over the same digest. I could not construct any exploit path, on-chain or off-chain, and the finding's own analysis of why it does not bite is correct. It is defence-in-depth hardening on an immutable contract, not a weakness.

**Evidence**  
contracts/rune/RuneOfEntry.sol:73-80 read verbatim — `bytes32 s = bytes32(sig[32:64]); uint8 v = uint8(sig[64]); if (v < 27) v += 27; return ecrecover(digest, v, r, s);` with no s-bound and no v equality check. Anti-replay is caller-keyed at :58 (`require(tokenOf[msg.sender] == 0, "already minted")`) and the digest binds msg.sender at :63, so a second valid encoding is redeemable only by the same wallet that is already gated. address(0) from a failed ecrecover can never match voucherSigner because the constructor rejects a zero signer (:48), and contracts/rune/test/rune.test.mjs:206-215 pins that. I additionally checked the off-chain half, which the original finding did not: app/lib/nft.js:99-132 signs a fresh voucher per request with no nonce, no used-signature ledger and no dedup keyed on signature bytes (`buildMintPlan` just returns `IFACE.encodeFunctionData('mint', [sig])` at :122), and app/routes/nft.js consumes only that. There is nowhere in the system that a distinct-but-valid encoding could slip past.

**Impact**  
None realizable today. No unauthorized mint, no forgery, no replay, no double-mint. The only cost is the absence of a layer OpenZeppelin's ECDSA.tryRecover provides by default, on a contract that can never be patched.

**Remediation**  
Optional hardening, worth doing only before deployment since the contract is immutable: inside _recover, before ecrecover, add `if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) return address(0);` and `if (v != 27 && v != 28) return address(0);` (after the existing `v < 27` normalisation so raw 0/1 recovery ids still work). Add a test that flips a valid voucher to (r, n-s, v^1) and asserts the mint still reverts. If the contract is already deployed, the correct action is a note in contracts/rune/README.md stating the invariant that keeps it safe — one-per-wallet is keyed on the caller, never on signature bytes — so a future off-chain voucher ledger is not added without re-examining this.

**Effort:** ~15 minutes pre-deploy; not applicable post-deploy

<details><summary>Verifier note</summary>

Read RuneOfEntry.sol:73-80, :58, :63, :48 and app/lib/nft.js:99-132 in full. The technical claim is factually CONFIRMED — there genuinely is no s-bound and no v constraint. Severity DOWNGRADED LOW -> INFO because I verified there is no exploit path on either side of the trust boundary: the anti-replay gate is `tokenOf[msg.sender]`, not a signature registry, and the server issues a fresh voucher per call with no sig-bytes bookkeeping that malleability could bypass. Per the audit rule that a pattern match is not a bug, this is hygiene/defence-in-depth, which is INFO, not LOW. The finding's own reachability analysis (a)-(d) is accurate and I found no error in it.

</details>

---

<a id="i10"></a>

#### I10 — supportsInterface advertises ERC-721 while getApproved never reverts and the two Approval events are absent from the ABI

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** spec-compliance  
**Location:** `contracts/rune/RuneOfEntry.sol:112`

**Description**  
Both factual claims hold. The file declares exactly two events — `Transfer` (:39) and `Locked` (:41) — so the ERC-721-mandated `Approval` and `ApprovalForAll` events are missing from the emitted ABI, and `getApproved` (:101) is `external pure` returning address(0) unconditionally, where the spec says it throws for an invalid NFT (contrast ownerOf :84-87, locked :105-108 and tokenURI :170-171, which all revert 'no such token'). What the finding overstates is the framing: this contract already, by explicit and documented design, does not satisfy ERC-721's central behavioural clause — every transfer path reverts (:96-98). That is exactly what ERC-5192 sanctions, and advertising 0x80ac58cd alongside 0xb45a3c0e (:112, :114) is the ERC-5192-prescribed way to do it. Against that backdrop, two dead-code clauses about approvals that can never succeed are cosmetic, not a compliance gap of a different kind.

**Evidence**  
contracts/rune/RuneOfEntry.sol:101 — `function getApproved(uint256) external pure returns (address) { return address(0); }` (no existence check, unlike locked() at :106 which calls ownerOf(id) first). Events: I read the entire 209-line file; the only `event` declarations are :39 `event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);` and :41 `event Locked(uint256 tokenId);`. Approvals can never occur — approve (:99) and setApprovalForAll (:100) both `revert("soulbound")`, pinned behaviourally by contracts/rune/test/rune.test.mjs:148-158 — so neither missing event could ever have a value to report. The test at :162-165 checks only that the four interface IDs answer true.

**Impact**  
Nil for security and nil for function. A strict ERC-721 conformance checker or an indexer codegen expecting a complete ERC-721 event ABI will flag the contract, but no handler it could generate would ever fire, and a getApproved of address(0) on an unknown id discloses nothing and enables nothing. No fund, access-control or availability consequence.

**Remediation**  
Pre-deploy only, and low priority: declare `Approval` and `ApprovalForAll` so the ABI is a true superset of ERC-721 (they add negligible bytecode since nothing emits them), and change getApproved to `view` calling `ownerOf(id)` first so unknown ids revert 'no such token', matching locked() at :106. Extend contracts/rune/test/rune.test.mjs:162 to assert getApproved(999) reverts. If already deployed, record the deviation in contracts/rune/README.md next to the existing 'Hard lines' section rather than treating it as a defect to fix.

**Effort:** ~20 minutes pre-deploy

<details><summary>Verifier note</summary>

Read RuneOfEntry.sol lines 39, 41, 84-115, 170-171 and the whole file to enumerate events; read rune.test.mjs:148-166. Both factual assertions are CONFIRMED by reading — getApproved really is unconditional-pure, and the two Approval events really are absent. Severity DOWNGRADED LOW -> INFO: the impact section of the original overstates the consequence for an immutable contract that already and deliberately diverges from ERC-721's transfer semantics under ERC-5192 cover (documented at RuneOfEntry.sol:16-18 and README.md:4-5). Two unreachable-approval clauses cannot be a meaningfully larger conformance problem than the divergence the design is built on. I also confirmed the finding correctly excluded the non-payable signatures as a documented, test-pinned choice (rune.test.mjs:143-145) — that exclusion is right.

</details>

---

<a id="i11"></a>

#### I11 — The 'no admin surface' guarantee is enforced by a function-name regex, not by the property

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** test-coverage  
**Location:** `contracts/rune/test/rune.test.mjs:174`

**Description**  
The test at rune.test.mjs:168-177 reads ABI function names and asserts they do not match /owner|admin|pause|withdraw|set[A-Z]|upgrade/. It pins the vocabulary a control surface happens to use, not the property that none exists. `rotateVoucherSigner`, `sweep`, `emergencyMint`, `grantRune`, `retire` and `configure` all pass the regex unchanged. This is precisely the failure mode CLAUDE.md documents — a source/name scan standing in for behaviour nothing else tests — and it guards the contract's loudest and least revocable promise (RuneOfEntry.sol:19-21, README.md:14-15). The suite's other ABI-wide loop (:143-145) only checks payability, so it does not cover this either.

**Evidence**  
contracts/rune/test/rune.test.mjs:168-177, read verbatim; the two `continue` exemptions at :173 for ownerOf and setApprovalForAll are themselves the tell that the assertion is matching words rather than behaviour. I independently verified the finding's premise that no admin surface exists today by enumerating every externally callable function in RuneOfEntry.sol: name/symbol (:28-29 constants), voucherSigner (:31 immutable), totalMinted (:32), tokenOf (:37), mint (:57), ownerOf (:84), balanceOf (:89), transferFrom/safeTransferFrom x2/approve/setApprovalForAll/getApproved/isApprovedForAll (:96-102, all pure), locked (:105), supportsInterface (:110), seedOf (:141), tokenURI (:170). `mint` is the only non-view, non-pure entry point, so the proposed property assertion would pass today and fails closed for anything new.

**Impact**  
A privileged function added in a future change ships green if its name avoids six English words. Because the contract is non-upgradeable and has no owner, such a function would be permanent from the moment of deployment. No live defect — this is the gate being weaker than the promise it guards.

**Remediation**  
Replace the name regex with the property: iterate artifact.abi and assert every entry of type 'function' has stateMutability 'view' or 'pure', or has name 'mint' — i.e. mint is the sole state-mutating entry point in the whole ABI. That derives from the compiled artifact rather than a word list, needs no exemptions, and catches a neutrally-named mutator. Keep the behavioural soulbound-revert test at :148-166 unchanged.

**Effort:** ~10 minutes

<details><summary>Verifier note</summary>

Tried to refute this and could not. Read rune.test.mjs:168-177 verbatim and confirmed the assertion is `assert.doesNotMatch(n, /owner|admin|pause|withdraw|set[A-Z]|upgrade/)` over ABI names only. I also verified the finding's own reachability claim by listing every function in RuneOfEntry.sol myself: mint (:57) is indeed the only state-mutating one, so this is a coverage weakness and not a live defect, which the finding states correctly. Severity INFO is right and the proposed remediation is sound and would pass against the current ABI. Kept as filed.

</details>

---

<a id="i12"></a>

#### I12 — The 'no token is an empty staff' invariant is asserted against two seeds that cannot reach the branch that guarantees it

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** test-coverage  
**Location:** `contracts/rune/test/rune.test.mjs:196`

**Description**  
RuneOfEntry.sol:156-157 forces a minimum of two branch strokes, and the comment at :120-121 states it as a guarantee. The test asserts `>= 3` <line> elements for token ids 1 and 2 only. I reproduced the seed derivation (keccak256(abi.encodePacked(uint256 id, address owner)), :141-142) and the twelve-iteration selection loop, and both test seeds are far too dense to reach the forcing lines: the guard requires drawn < 2 at i == 10, which needs fewer than two of the low twelve seed bits set. So deleting lines 156-157 outright would leave this assertion green. The implementation itself is correct — I exhaustively simulated all 4096 low-bit patterns and the minimum branch count is exactly 2, including the all-zero seed. The invariant is protected by inspection, not by the suite.

**Evidence**  
contracts/rune/test/rune.test.mjs:196-197 (`assert.ok((svg.match(/<line /g) || []).length >= 3, ...)`) inside `for (const id of [1, 2])` at :186, with test wallets minterA = 0xaa..aa and minterB = 0xbb..bb (:44-45). I implemented keccak-256 from scratch and validated it against the known keccak256("") = c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470, then computed the two real seeds: token 1 -> d94cde99...4620bf75, 9 of the low 12 bits set, 9 branches, 10 <line> elements; token 2 -> 8f5f1fa3...4cf98b5a, 7 bits set, 7 branches, 8 <line> elements. Neither comes near the `drawn == 0 && i == 10` / `drawn == 1 && i == 11` guards at RuneOfEntry.sol:156-157. Exhaustive simulation of the same loop over seeds 0..4095 gives min(drawn) == 2, so the forcing logic is sound; only 13 of 4096 bit patterns (the all-zero seed plus the twelve single-bit ones) even reach it.

**Impact**  
A future edit to the branch-selection loop that breaks the two-branch floor — including the degenerate all-zero-bit seed rendering a bare staff — ships green. The consequence is cosmetic: a token whose art contradicts the contract's own stated guarantee at RuneOfEntry.sol:120-121. No financial or access-control effect.

**Remediation**  
Make the invariant reachable rather than incidental: port the twelve-iteration selection loop into the test file and assert drawn >= 2 for all 4096 low-bit patterns, plus keep one on-chain tokenURI assertion confirming the port matches deployed bytecode (the existing id 1/2 checks already serve as that anchor). Cheaper alternative if a seam is preferred: expose the branch-selection count as a pure helper taking a bytes32 seed so the test can drive a chosen sparse seed directly.

**Effort:** ~30 minutes

<details><summary>Verifier note</summary>

Tried to refute by hoping one of the two test seeds was sparse enough to exercise the forcing branch; it is not, and I now have the numbers. Read rune.test.mjs:186-200 and RuneOfEntry.sol:141-166. Recomputed both seeds with a from-scratch keccak-256 validated against the known empty-string digest: token 1 has 9 of 12 bits set (10 lines), token 2 has 7 (8 lines) — both an order of magnitude above the `>= 3` threshold and neither anywhere near the drawn<2 precondition, so the finding's claim that removing :156-157 leaves the test passing is CONFIRMED, not merely 'almost certainly'. Also independently confirmed the finding's exhaustive-simulation claim: min drawn over all 4096 patterns is exactly 2, so the contract logic is correct and this is purely a coverage gap. Severity INFO is right. Kept as filed with the evidence strengthened from probabilistic to computed.

</details>

---

<a id="i13"></a>

#### I13 — nginx.conf ships unsubstituted YOUR_DOMAIN placeholders with no substitution step

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** security · **Category:** deploy-config  
**Location:** `nginx.conf:25`

**Description**  
YOUR_DOMAIN appears as server_name (:15, :22), in both certificate paths (:25, :26), in the CORS origin (:77) and in a gateway example (:95). docker-compose.yml:126 mounts the file read-only with no envsubst or template stage.

**Evidence**  
nginx.conf:15, :22, :25, :26, :77 read directly. docker-compose.yml:126 `- ./nginx.conf:/etc/nginx/conf.d/default.conf:ro`. Repo-wide grep for YOUR_DOMAIN outside nginx.conf returns exactly one hit, the comment at docker-compose.yml:116; grep for envsubst returns nothing.

**Impact**  
On an unedited host nginx refuses to start (missing cert files), which is fail-closed for TLS. Partial substitution would leave a literal https://YOUR_DOMAIN CORS origin that fails closed in browsers.

**Remediation**  
If it stays a template, rename to nginx.conf.template and use nginx:alpine's /etc/nginx/templates envsubst with DOMAIN in .env.example.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Facts all confirmed. DOWNGRADED LOW -> INFO because this is an explicitly documented manual step, not an oversight: nginx.conf:5 says 'Replace YOUR_DOMAIN with your actual domain', :8-9 gives the certbot command, docker-compose.yml:116-117 repeats the cert requirement and offers the dev alternative, and Makefile:103-104 provides a `cert` target. The failure mode is loud and fail-closed. Per the rule about documented, genuinely-mitigated tradeoffs, this is packaging ergonomics rather than a security weakness; the only real residual (a half-substituted CORS origin) is speculative.

</details>

---

<a id="i14"></a>

#### I14 — nginx CSP allows 'unsafe-inline' scripts and an unused third-party CDN origin

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** security · **Category:** csp  
**Location:** `nginx.conf:37`

**Description**  
The server-level CSP permits `script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com`, and the Express tier's CSP has the same 'unsafe-inline' allowance.

**Evidence**  
nginx.conf:37 read verbatim — `script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com`. app/server.js:153 `"script-src 'self' 'unsafe-inline' https://telegram.org",`. website/index.html contains exactly one <script> tag.

**Impact**  
'unsafe-inline' removes CSP's main protection against injected script on the pages this header reaches.

**Remediation**  
Drop the cdnjs origin (nothing loads from it), move the single inline script in website/index.html to an external file or a nonce, and drop 'unsafe-inline'.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

Directive text confirmed at nginx.conf:37 and app/server.js:153 (the cited :151-167 range is right; script-src is :153 exactly). DOWNGRADED LOW -> INFO. Three reasons the impact is smaller than stated: (a) grep shows website/index.html loads nothing from cdnjs.cloudflare.com, so the CDN allowance is dead permissiveness rather than an active third-party trust; (b) as the finding itself notes, `location /` at :44-47 drops this header entirely, so it governs almost nothing today; (c) the Express-side allowance is a documented, reasoned tradeoff at app/server.js:146-149 ('the pages use both'), with object-src 'none', base-uri 'self' and frame-ancestors 'none' compensating. Hardening opportunity, not a weakness with a demonstrated path.

</details>

---

<a id="i15"></a>

#### I15 — Mint-extension screening is a denylist with a silent catch-all; MintCloseAuthority is not enumerated

**Severity:** INFO · **Confidence:** Likely · **Pillar:** security · **Category:** input-validation  
**Location:** `programs/rclaw_staking/src/lib.rs:163`

**Description**  
`reject_hazardous_extensions` enumerates six hazardous mint extensions and admits everything else through `_ => {}` at line 163. `MintCloseAuthority` is genuinely absent from the enumeration. The structural observation — a security control shaped as a denylist over a type set the program does not own, defaulting to allow — is accurate as written.

**Evidence**  
programs/rclaw_staking/src/lib.rs:140-165 read in full; the six arms are PermanentDelegate, TransferHook, DefaultAccountState, NonTransferable, ConfidentialTransferMint, TransferFeeConfig, followed by `_ => {}` at :163. The entry-only freeze-authority constraint is at lib.rs:346-348 (`constraint = mint.freeze_authority.is_none() @ StakeError::MintHasFreezeAuthority`), documented as entry-only at :113-119. Cargo.lock:5227-5229 confirms spl-token-2022 3.0.5 (a second, older 1.0.0 entry also exists at :5203-5206).

**Impact**  
None established. MintCloseAuthority cannot be exercised while the vault holds any balance, because Token-2022 requires `supply == 0` to close a mint — so there is no state in which the close authority can act and anything is at stake. The remaining unenumerated mint extensions in this crate version are either benign against raw-base-unit accounting (InterestBearingConfig, the metadata/group pointer family) or cannot be initialised without an already-rejected extension (ConfidentialTransferFeeConfig requires ConfidentialTransferMint).

**Remediation**  
If the shape is changed at all, invert to an allowlist matching the extensions actually expected on the $RCLAW mint (`MetadataPointer`, `TokenMetadata`), returning `UnsupportedMintExtension` with the existing `msg!` for everything else. That fails in the safe direction and covers future additions in one change. Lower priority than adding the Token-2022 test coverage that would let either shape be verified rather than reasoned about.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

DOWNGRADED from LOW to INFO, and confidence from Confirmed to Likely. The code facts are confirmed by reading lib.rs:140-165 — the six arms, the `_ => {}`, and the absence of MintCloseAuthority are all exactly as described. But the finding itself concludes there is no exploitable path, and I could not construct one either: the supply==0 precondition on mint closure is dispositive, and the re-initialised-mint-at-a-recycled-address scenario the finding raises as residual also requires an empty vault first, at which point there is nothing to protect. That is an impact of zero, which is INFO by the stated severity scale, not LOW. Confidence drops to Likely because the finding's central forward-looking premise — that unknown TLV discriminants are silently skipped rather than erroring — is explicitly unverified, and I could not verify it either: spl-token-2022 3.0.5 is not vendored in this container and the registry is unreachable. If `get_extension_types()` errors on an unrecognised discriminant, the `map_err` at lib.rs:136-138 converts it to `UnsupportedMintExtension` and the guard already fails closed against every future extension, which would refute the finding's remaining rationale entirely. That question decides the finding and is unanswered. Context also matters: lib.rs:1-11 labels the program DRAFT/DEVNET-ONLY and unaudited, with deployment gated behind a third-party audit.

</details>

---

<a id="i16"></a>

#### I16 — stake validates the account version only after reading a field whose offset the version determines

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** correctness  
**Location:** `programs/rclaw_staking/src/lib.rs:185`

**Description**  
The re-stake guard reads `sa.amount` at line 182 to decide whether to check `sa.version` at line 185-188. `amount` sits at a version-dependent offset (layout::AMOUNT_OFFSET = 73), so a record written under an unrecognised layout is interpreted before it is validated — the inverse of the contract stated at lib.rs:42-44. `unstake` gets the ordering right, checking version unconditionally and first. The asymmetry is real and the one-line fix is correct.

**Evidence**  
programs/rclaw_staking/src/lib.rs:180-190 read: `let sa = &ctx.accounts.stake_account;` / `if sa.amount > 0 { require_keys_eq!(sa.owner, ...); require_keys_eq!(sa.mint, ...); require!(sa.version == StakeAccount::CURRENT_VERSION, StakeError::UnsupportedAccountVersion); }`. Contrast lib.rs:269-273, where `unstake` opens with the version require! before touching `unlock_at` or `amount`. The unguarded fall-through writes are at lib.rs:230-253 (`sa.version`, `sa.owner`, `sa.mint`, the amount-weighted `sa.staked_at` at :239-246, `sa.unlock_at` at :252, `sa.bump` at :253). Contract statement confirmed at lib.rs:42-44.

**Impact**  
None today, and the finding says so plainly. A BPF upgrade replaces bytecode atomically, so two layout versions are never simultaneously live, and programs/rclaw_staking/README.md:229-231 records that no migration or rescue instruction exists — a layout change must land before value is at stake. This is a latent inconsistency in a defensive guard.

**Remediation**  
Hoist the version check out of the `if sa.amount > 0` block so it runs unconditionally, matching `unstake`. `init_if_needed` leaves a fresh record fully zeroed, so guard on that: `require!(sa.version == 0 || sa.version == StakeAccount::CURRENT_VERSION, StakeError::UnsupportedAccountVersion);` before any other field read, leaving the existing block to carry only the owner/mint assertions. No behaviour change for any record this build can produce.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

CONFIRMED as an ordering asymmetry, DOWNGRADED from LOW to INFO. I read both sites and the write-back block and the finding's description is accurate in every particular, including that `sa.staked_at` and `sa.unlock_at` are consumed at :239-252 with no version check on the `sa.amount == 0` path. I attempted to find current reachability and could not: lib.rs:231 sets `sa.version = StakeAccount::CURRENT_VERSION` on every successful stake, the PDA is program-owned so no third party can plant bytes in it, and an account written by a pre-version-byte build would fail Anchor's Borsh deserialisation outright rather than reaching this guard. With impact of exactly zero under any state this build can reach, INFO is the correct grade on the stated scale; LOW would imply a real present weakness. The finding is worth keeping on the record because the fix is one moved line and the moment it matters is the moment nobody will re-read this ordering — but it is hygiene, not a weakness.

</details>

---

<a id="i17"></a>

#### I17 — Three different cryptography floors across four dependency files; requirements-ci.txt does not meet its own pinning claim

**Severity:** INFO · **Confidence:** Confirmed · **Pillar:** quality · **Category:** version-pinning  
**Location:** `pyproject.toml:28`

**Description**  
The crypto extra allows cryptography>=43.0.1, bot/requirements.txt requires >=48.0.1, requirements-ci.txt requires >=50.0.0 and requirements.lock pins ==50.0.0. Separately, requirements-ci.txt's header claims it mirrors a reproducible environment while 10 of its entries use >=.

**Evidence**  
pyproject.toml:26-29 `[project.optional-dependencies]` / `crypto = [` / `"cryptography>=43.0.1",`. bot/requirements.txt:10 `cryptography>=48.0.1`. requirements-ci.txt:14 `cryptography>=50.0.0`; requirements.lock:13 `cryptography==50.0.0`. requirements-ci.txt:1-5 claims it 'mirrors the environment the known-failures baseline ... was generated in, so the baseline-diff gate is reproducible', while :14-19 and :23,28,29,30 are all `>=` (10 entries).

**Impact**  
The reproducibility guarantee behind the known-failures baseline degrades as upstream publishes new versions; the declared floors disagree with the audited pin.

**Remediation**  
Raise the pyproject crypto floor to match the lock, pin the >= entries in requirements-ci.txt or soften its header, and add a test asserting every declared floor is >= the corresponding lock pin.

**Effort:** Small (<½ day)

<details><summary>Verifier note</summary>

All four version strings read at the cited lines and confirmed, as is the >= count in requirements-ci.txt. DOWNGRADED LOW -> INFO because the security mechanism is largely hypothetical: pip's default resolver installs the NEWEST compatible release, so `pip install .[crypto]` yields current cryptography, not 43.0.1 — the finding's own scenario needs an external constraints file or a legacy resolver, and nothing in this repo installs the crypto extra at all (Dockerfile, Makefile:26-29 and both CI files install bot/requirements.txt or requirements-ci.txt). What remains is genuine version-declaration drift and a header whose reproducibility claim is not met: hygiene.

</details>

---
## 5. Quick Wins

High impact, low effort. The first four close every HIGH finding in the report.

| # | Finding | Change | Effort |
|---|---|---|---|
| 1 | **H2** | In `app/routes/mcp.js`, pass both return paths (`:496`, `:505`) through `sanitizeRecord` from `../lib/flight` — the helper already exists and is already used by `public_flight.js:42`. One import, two `.map()` calls. | Trivial |
| 2 | **H1** | Replace `--forwarded-allow-ips='*'` in `docker-compose.yml:67` with the nginx container subnet, **or** drop `--proxy-headers` and set `TRUSTED_PROXY` so `_client_ip()` performs its intended rightmost-untrusted walk. Add `TRUSTED_PROXY` to `.env.example`. | Small |
| 3 | **H4** | Move `halt` and `reset` out of the default `trader` permission set (`bot/utils/user_store.py:50`) into an operator-only permission — mirroring the deliberate omission the web gateway already makes at `user_gateway.py:123-131`. | Small |
| 4 | **H3** | Add a skill→permission lookup before the generic NLP dispatch at `bot/skills/telegram_handler.py:2165`, calling `self.users.permission_denial(tg_id, perm)` exactly as `_token_gate_blocks` already does for scan modes twenty lines above. | Small |
| 5 | **M6** | Add `Field(max_length=25)` to `ScanRequest.symbols` and clamp `limit`, copying the bound `bot/api/lab.py:60` already applies. | Trivial |
| 6 | **M10** | Return a generic error body from the unauthenticated MCP `tools/call` handler instead of the raw exception string. | Trivial |
| 7 | **M15** | Make `GET /auth/me` read-only — delete the two `create_jwt` calls at `bot/api/auth_routes.py:316-323`. Refresh tokens should be minted only by `/login`, `/register` and `/refresh`. | Small |
| 8 | **L41** | Correct the public claim: `website/index.html:9` advertises "23 fail-closed risk checks"; `config/risk_manifest.yaml` and `SECURITY.md` say 21 (16 fail-closed). | Trivial |
| 9 | **M2** | Point `pip-audit` at the dependency set that actually ships, or converge `requirements.lock`, `requirements-ci.txt` and `bot/requirements.txt` onto one file. | Small |
| 10 | **M4 / M5 / L2** | Repair or delete `.gitlab-ci.yml`. It installs a `requirements.txt` that does not exist, calls a `scripts/mypy_gate.py` that does not exist, and runs the token-tooling job from the wrong directory. As written it cannot be the failover it is documented to be. | Small |

---

## 6. Strategic Recommendations

### Now (this week)

1. **Ship the four HIGH fixes.** All are small and local; together they remove the only remotely-reachable, materially-harmful issues found.
2. **Write the regression test with each fix, not after.** This repository's own guidance is explicit that the grep tells you where you looked and the test tells you where you did not. For **H2** specifically, the test should plant `size_usd`/`pnl_usd` into a flight record and assert *every* unauthenticated consumer of `getLatestFlight()` emits neither — not just the one being fixed.
3. **Run the cross-surface sweep the fixes imply.** Three of four HIGHs are one surface missing a fix a sibling already received. Before closing them, enumerate the siblings: every consumer of `getLatestFlight()` (four exist), every branch of the NLP dispatch, every permission the default role holds.

### Next (this quarter)

4. **Close the CI coverage gap on `app/` (M3).** 93,580 LOC and 248 endpoints currently pass through no linter, no SAST and no dependency audit in either pipeline. Adding ESLint with the security plugin, `npm audit` with a ratchet baseline mirroring `token/.audit-baseline.json`, and Semgrep's JS ruleset would bring the largest attack surface up to the standard `bot/` already meets.
5. **Extend bandit past `bot/` (M14)** to `api_bridge.py`, `dashboard_api.py` and `scripts/`, and lower the threshold from high/high — the container's default command is currently unscanned.
6. **Resolve the gateway's confused-deputy design (M21).** Identity is a caller-supplied `telegram_id` authenticated only by a shared secret. Bind identity cryptographically — a short-lived per-user token minted by the Express layer and verified by the gateway — so that a leaked `WEB_GATEWAY_SECRET` does not imply the ability to act as every user.
7. **Make revocation durable (M16)** by treating Redis as required rather than best-effort for `token_store`, or by persisting epochs to the database.
8. **Fix the two live-execution correctness risks (M18, M19):** re-check the kill switch at order submission rather than only at gate time, and give the market-fallback open path the same idempotency key the primary path uses.

### Later (next two quarters)

9. **Retire `'unsafe-inline'` (M14).** This is the single largest structural weakness in the web tier and the reason the 30-day localStorage JWT (L6) is worth worrying about. It requires migrating inline handlers in 35 HTML pages to nonces or external bundles — genuinely large, but it converts a whole class of findings from "exploitable if XSS" to "contained".
10. **Consolidate the four client-side escapers (L13)** into one shared module; today `app/public/js/app.js:95` omits single-quote escaping while three sibling copies include it.
11. **Unify the dependency story (I17).** Four files declare Python dependencies with three different `cryptography` floors. One source of truth, generated locks.
12. **Do not deploy the staking program without the third-party audit its own README demands.** The prior vault-drain was real and the fix looks correct, but `reject_hazardous_extensions` has never executed in any test (L37) and the deployment configuration (Token-2022) is precisely the untested one.

---

## 7. Positive Observations

This codebase does a number of things better than most production systems, and the audit is more useful if those are named precisely.

- **The risk engine genuinely fails closed.** `bot/risk/risk_engine.py:2923` `_fail_closed_restore` distinguishes `CORRUPT_FAIL_CLOSED` from `IO_FAIL_CLOSED` and blocks trading rather than resetting to a permissive state — the behaviour most systems get wrong.
- **Live execution is defended in depth.** Three independent switches (`LIVE_TRADING_ENABLED`, `SIMULATION_MODE`, non-empty `chat_id`), an independent simulation veto (`engine.py:4993`), a kill switch, per-trade, total-exposure and per-user notional caps, all defaulting to paper. The audit found no unauthenticated path to a live order.
- **`scripts/guard_lint.py` is exceptional.** A 1,060-line bespoke linter with 11 rules whose entire purpose is proving guards are *reached* — written after discovering that `assertDevnet` covered 5 of 9 commands and a token gate 8 of 12 sites. Very few codebases invest in reachability of their own controls.
- **The test gate refuses to let baselines rot.** `scripts/ci_test_gate.py` makes a `known_failures.txt` entry that starts *passing* a hard failure, and the baseline is currently empty. `scripts/preflight.py` parses `ci.yml` rather than restating it, so local and CI checks cannot drift.
- **Deny-by-default authorization on the web skill path.** `bot/web/user_gateway.py:117` maps skills to permissions with unmapped skills denied, and the comment explains the exact incident that motivated it. This is the right pattern — H3 exists precisely because the Telegram path did *not* adopt it.
- **Honest failure rendering is enforced structurally.** `app/test/panel_failure_honesty.test.js` brace-matches every `renderPanel` loader to prove a failed read cannot render as `0.00%`. The doctrine is unusual; mechanically enforcing it is rarer still.
- **Secrets tooling is careful.** gitleaks runs pinned and SHA256-verified with a one-entry history baseline for a real past leak that was rotated on both sides; exchange credentials are AES-256-GCM envelope-encrypted with a random 12-byte IV per encryption and a strict 32-byte key check.
- **SQL is uniformly parameterized.** Across 248 endpoints, only four sites interpolate an identifier, and all four were verified to derive from frozen constant maps — no user-controlled identifier reaches SQL.
- **Write integrity is taken seriously:** `atomic_write.py`, `durable_io.py` (parent-dir fsync), `state_lock.py`, and `state_guard.py`, which refuses to boot when state would land somewhere a redeploy deletes.
- **The Anchor program's PDA redesign looks correct**, is compiled with `overflow-checks = true`, and carries 1,444 lines of attack and solvency tests.
- **`SECURITY.md` is honest about its own limits**, stating plainly that no third-party audit has been performed and that the "Security Scan Passed" badge is self-asserted. That candour is worth more than a badge.

---

## 8. Appendix

### 8.1 Audit coverage — what was and was not examined

**Examined in depth:** `app/` routes, libs, auth, db and SSR surfaces; `bot/` HTTP entrypoints, gateway, risk/compliance core, live-execution path, secrets handling, Telegram/NLP surface; `programs/rclaw_staking`; `contracts/rune`; CI/CD pipelines; dependency manifests and lockfiles; Docker/nginx/deploy configuration.

**Not examined, and therefore carrying no assurance from this report:**

| Area | Size | Why |
|---|---|---|
| `ollama/` | 9,017 LOC | LLM fine-tuning pipeline, out of the request/trading path |
| `playbooks/` | 1,939 LOC | Standalone strategy packages, not imported by `bot/` |
| `tests/`, `app/test/` | 90,761 LOC | Reviewed as evidence about the code, not audited as code |
| `benchmark/`, `agentbench/` | data + 799 LOC TS | Frozen datasets and an offline harness |
| `token/` `.mjs` | 6,875 LOC | Reviewed at supply-chain level only, not line-by-line; devnet-only |
| `app/public/js/dashboard.js` | 8,165 LOC | Sampled (162 `innerHTML` sites reviewed in aggregate), not exhaustively |
| `docs/` | 128 files | Read selectively for claims verification |

**Method limits.** This was a **static review**. No instance was run, no dynamic or authenticated scanning was performed, no exploit was executed against a live system, and no dependency was executed in a sandbox. Findings marked *Suspected* are precisely those where static reading could not settle reachability.

### 8.2 Items needing manual follow-up (UNVERIFIED / Suspected)

| ID | Item | What would settle it |
|---|---|---|
| **L11** | `app/lib/ens.js:68` — ENS `getAvatar` may fetch an attacker-influenced URL server-side | Confirm whether ethers' avatar resolution performs an outbound fetch on the server for a self-linked wallet; if so, this is an SSRF primitive |
| **L17** | `app/routes/public_duel.js:54` — ISO `Z`-suffixed string compared against a `DATETIME` column | Run the query against MySQL/TiDB and check whether the comparison silently excludes rows |
| **L34** | `dashboard_api.py:69` — static-path prefix check lacks a trailing separator | Attempt a sibling-directory traversal against a running instance |
| **I3** | `app/db.js:39` — pool built from raw `DATABASE_URL` with no explicit TLS | Confirm the production connection string carries TLS parameters; the runbook records `ER_SECURE_TRANSPORT_REQUIRED`, implying TiDB enforces it |
| **I4** | `app/lib/canonical.js:21` — key sort uses UTF-16 code units; the Python twin sorts by code point | Differential-test both implementations with non-BMP keys; a divergence would break Proof-of-PnL verification |
| **I7** | `app/routes/web3_execute.js:62` — signing/deploy endpoints carry no web-tier authz | Confirm the bot gateway re-checks admin identity for these paths; the web tier delegates entirely |
| **L30** | `bot/skills/telegram_handler.py:12469` — untracked-position close is a non-`reduceOnly` market order | Confirm against Bitget semantics whether a close order exceeding remaining size can open opposing exposure |

### 8.3 Tooling recommendations

| Layer | Gap | Suggested tool |
|---|---|---|
| JS SAST | none today | Semgrep (`p/javascript`, `p/express`), ESLint + `eslint-plugin-security` |
| JS SCA | `app/` unaudited, no devDependencies | `npm audit` with a ratchet baseline like `token/.audit-baseline.json`; Dependabot or Renovate |
| Python SAST | bandit limited to `bot/` at high/high | Extend to `api_bridge.py`, `dashboard_api.py`, `scripts/`; add Semgrep `p/python` |
| Python SCA | audits a 10-package lock, not the shipped set | `pip-audit` against the image's actual requirements; consider `uv`/`pip-tools` for one generated lock |
| Solidity | no SAST | Slither; Foundry invariant tests for the soulbound and voucher-replay properties |
| Rust | `cargo-audit` with an ID-only ratchet | Keep, but compare advisory *reachability* not just IDs (see L38); add `cargo-deny` |
| Containers | no image scan | Trivy or Grype in CI |
| IaC / config | none | Checkov or `hadolint` for the Dockerfile; a lint that fails on `YOUR_DOMAIN` placeholders reaching a release |
| Secrets | gitleaks (good) | Keep; add pre-commit hook parity |
| Runtime | none | DAST (OWASP ZAP) against a staging instance, focused on the 31 public route files |

### 8.4 Method

Twenty domain auditors ran in parallel across three workflows (7 web, 6 Python, 3 contracts/supply-chain, plus verifiers). Each auditor read source directly and reported structured findings with mandatory `file:line` anchors. Every finding was then handed to an **independent adversarial verifier** instructed to open the cited code and default to *refuted* unless it could confirm the defect and trace reachability from untrusted input. 85 candidate findings entered verification; 1 was refuted outright and several were downgraded on reachability grounds. Four findings were additionally spot-checked by hand against the source by the orchestrating auditor, including both leading HIGH findings.

The single most useful control was instructing verifiers to treat pattern matches as insufficient. This repository deliberately contains code that *looks* like the bugs it has previously fixed — `arena_trades.pnl` is `NOT NULL`, `track.js` filters on `isFinite` upstream, and the paper `Trade` sets `pnl` and `closed_at` in one atomic `model_copy`. A less skeptical pass would have reported all three.
