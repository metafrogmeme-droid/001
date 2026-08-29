# Independently verified findings (auditor's own reading, not agent-reported)

Every item below was read in the source by the lead auditor, not accepted from
a subagent. Refuted candidates are kept, because a register that shows only
what stuck cannot be checked.

---

## RC-2026-001 — Unauthenticated `/api/auth/validate-token` allows binding any
## Telegram identity to an attacker's own web account

- **Status**: OPEN · **Severity**: CRITICAL · **Confidence**: CONFIRMED
- **Category**: Broken authentication / authorization (OWASP A01, API2; CWE-287, CWE-639)
- **Component**: web app — auth + bot linking
- **File**: `app/auth.js:867-889`
- **Fix class**: REVIEW_REQUIRED (two-sided change, deployment ordering matters)

### Evidence

`app/auth.js:867-889` — the route carries no auth middleware, and `app/auth.js`
declares no `router.use(...)` at all, so nothing gates it. `app/server.js:318`
mounts it as `app.use('/api/auth', authRouter)` with no middleware:

```js
router.post('/validate-token', async (req, res) => {
    const { token, chat_id } = req.body;
    ...
    await pool.execute(
      'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
      [String(chat_id).slice(0, 32), user.id]
    );
```

`chat_id` is taken verbatim from the request body and written as the row's
`telegram_id`. The only thing proven is possession of a valid, unexpired
`link_token` — and `/link-token` (`app/auth.js:855-858`) mints one for the
*caller's own* row, so any registered user can mint one for themselves.

### Why it matters — the identity that comes back out

`app/lib/identity.js:18-27` resolves the bot identity from that column:

```js
async function resolveBotIdentity(req) {
  const uid = req.user.user_id;
  const [rows] = await pool.execute(
    'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
  const u = rows[0];
  if (u && u.telegram_linked && u.telegram_id) {
    return { id: String(u.telegram_id), linked: true, email: u.email || '' };
```

Its own module docstring states the invariant this breaks:

> "The identity is always resolved server-side from the DB — the browser can
>  never choose who it acts as."

It is resolved server-side, from a row the browser chose the contents of.

And the credential path gates on exactly the two fields the attack sets
(`app/routes/credentials.js:79-81`), then writes the victim's telegram id:

```js
if (!u || !u.telegram_linked || !u.telegram_id) {
  return res.status(409).json({ error: 'telegram_required', ... });
}
...
`INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload) ...`
[uid, String(u.telegram_id), venue, payload]
```

`bot/utils/credential_pull.py` imports those rows into the bot keyed on
telegram id, so the reachable consequence is exchange-credential and
live-control actions attributed to another person's bot account.

### Reproduction

1. Register any web account; authenticate.
2. `POST /api/auth/link-token` → returns `token` bound to the attacker's row.
3. `POST /api/auth/validate-token` (no auth header) with
   `{"token": "<that token>", "chat_id": "<victim telegram id>"}`.
4. The attacker's row now has `telegram_linked=TRUE, telegram_id=<victim>`.
5. Any route calling `resolveBotIdentity` now acts as the victim.

### Root cause

The endpoint is designed as a bot-to-server call — its own comment says
"called by the Telegram bot" (`app/auth.js:865`) — but nothing proves the
caller is the bot. The repo already has the right mechanism and uses it
everywhere else: `BOT_SYNC_SECRET` via an `X-Bot-Secret` header
(`app/routes/sync.js:69`, and the "X-Bot-Secret authed" notes at
`sync.js:534,578,699,786`). `/validate-token` is the one bot-channel endpoint
that does not use it.

### Remediation (proposed — NOT applied)

Two-sided, and the ORDER matters or every `/link` breaks:

1. **Bot first.** `bot/skills/user_middleware.py:200-208` currently sends only
   `Content-Type`, `Accept`, `User-Agent`. Add
   `"X-Bot-Secret": os.getenv("BOT_SYNC_SECRET", "")`.
2. **Then the server.** Gate `/validate-token` on the same constant-time
   comparison `sync.js` uses, refusing when `BOT_SYNC_SECRET` is unset — the
   fail-closed shape `/diagz` already models (`app/server.js:252-262`).

Deploying the server half first rejects every real link attempt, so this must
not be applied as a single atomic change. That is why it is REVIEW_REQUIRED
rather than an auto-fix.

### Residual risk

Any `telegram_id` already mis-bound by this route stays mis-bound; the fix
does not clean existing rows. A one-off audit of `users` for duplicate or
unexpected `telegram_id` values is a separate operator action.

---

## RC-2026-002 — `guard_lint` accuses third-party code in any virtualenv not named `.venv`

- **Status**: FIXED · **Severity**: MEDIUM · **Confidence**: CONFIRMED
- **Category**: Gate integrity / tooling correctness
- **File**: `scripts/guard_lint.py:1049-1050` (before fix)
- **Fix class**: SAFE_AUTO_FIX — applied

Before: `_COVERAGE_SKIP` matched two hardcoded names (`/.venv/`, `/venv/`), and
`_route_module_coverage()` `rglob`bed the whole tree. A venv built as
`.venv-audit/` produced 9 accusations against `matplotlib`, `pandas` and
`mplfinance` — because `ax.add_patch(` matches the aiohttp signature
`\.add_(get|post|put|delete|patch)\(`.

Reproduced both directions: venv in tree → exit 1, 9 false accusations; the
same venv moved outside the repo → exit 0, all 12 rules pass.

Fixed by detecting a virtualenv structurally (`pyvenv.cfg` in the directory)
and pruning during the walk, plus `site-packages`/`dist-packages` for
`pip install --target` trees. `.gitignore` had the identical two-name gap and
now ignores `.venv*/`/`venv*/`. Runtime of the rule also fell from ~14s to 1.7s
because the venv is no longer read.

---

## RC-2026-003 — `guard_lint` scans Python comments and docstrings as if they were code

- **Status**: FIXED · **Severity**: MEDIUM · **Confidence**: CONFIRMED
- **Category**: Gate integrity / tooling correctness
- **File**: `scripts/guard_lint.py` `_route_module_coverage()`
- **Fix class**: SAFE_AUTO_FIX — applied

`_strip_js_comments` exists precisely because a commented-out
`router.use(authMiddleware)` once satisfied a rule; its docstring says so. The
Python branch had no equivalent, so docstrings and `#` lines were regex-scanned
as code. Demonstrated live: the comment written to explain RC-2026-002 had to
name `ax.add_patch(`, and `guard_lint.py` immediately reported *itself* as an
unchecked HTTP surface registering two routes.

This is independent of virtualenvs — any first-party file whose prose mentions
`.add_get(` would be accused, in CI. CLAUDE.md names this exact trap ("a
comment that quotes the string it forbids is indistinguishable from the code
doing it, and this has produced four false failures").

Fixed with a tokenize-based `_strip_py_comments` that blanks COMMENT tokens and
docstrings while PRESERVING offsets — the fastapi pattern is `^`-anchored under
`re.M`, so a decorator must keep its line and column. Argument strings are
untouched, so route paths still match.

---

## RC-2026-004 — `tests/test_no_read_only_fields.py` has the same vendored-tree blind spot

- **Status**: FIXED · **Severity**: LOW · **Confidence**: CONFIRMED
- **File**: `tests/test_no_read_only_fields.py:66-70` (before fix)
- **Fix class**: SAFE_AUTO_FIX — applied

`_py_files` skipped only `__pycache__` and `/.git/`. The audit venv put 107
extra `site-packages` symbols into the comparison and failed the test on
`_pytest.ReprTracebackNative.extraline` — a field in pytest itself. Fixed with
the same structural `pyvenv.cfg` / `site-packages` test. Runtime fell from 130s
to 7.9s.

A sweep of all nine tree-walking test/script files found only this one and
`guard_lint` walk from the repo root; the rest scope to `bot/` and are
unaffected. `tests/test_dashboard_pusher_is_not_wired_by_default.py:190` walks
from ROOT but filters `.git`/`node_modules`/`.mypy_cache` and matches only
config globs — latent, not currently failing, recorded as such.

---

## RC-2026-005 — 90 default-ON safety toggles are absent from `.env.example`

- **Status**: OPEN · **Severity**: MEDIUM · **Confidence**: CONFIRMED
- **Category**: Configuration governance / operational safety
- **Fix class**: REVIEW_REQUIRED (documentation content is a product decision)

713 environment variables are read by code; 215 are declared in the 47KB
`.env.example`. Of the 110 boolean flags that DEFAULT TO TRUE — i.e. each one
is a protection that setting `false` removes — **90 appear nowhere in
`.env.example`**. 19 gate money-path controls, among them:

`UNPROTECTED_GUARD_ENABLED` (`bot/config.py:1854`) and
`UNPROTECTED_ESCALATION_ENABLED` (`:1870`) — the machinery that detects and
rescues a filled position whose stop never landed; `SLIPPAGE_GUARD_ENABLED`
(`:1740`); `PER_STRATEGY_NOTIONAL_CAP_ENABLED` (`:326`);
`LLM_DIRECTION_GUARD_ENABLED` (`:1187`); `API_DEGRADE_REDUCE_ONLY` (`:1845`);
`GUARDIAN_FIREWALL_ENABLED` / `_ESCAPE_` / `_RISK_SENTINEL_` / `_DIGITAL_TWIN_`
(`:620,651,642,633`); `TRAILING_STOP_ENABLED` (`:1925`); `TIME_STOP_ENABLED`
(`:2010`); `MTF_ALIGNMENT_GATE_ENABLED` (`:505`).

The defaults are correct and fail-safe; the gap is that an operator can
silently disable any of these protections through a variable the file that
documents configuration never mentions, and no inventory of them exists.

Full lists: `audit/env_diff.md`, `audit/safety_flags.md`.

---

# Refuted during this audit (recorded, per the brief)

## RC-2026-F01 — `.env.example` testnet RPC vars are dead config — **FALSE POSITIVE**

Initially reported: `.env.example:743-753` declares 11 `WEB3_RPC_<CHAIN>` vars
while `app/lib/wallet.js:35-121` reads `WEB3_RPC_URL_<CHAIN>`, so setting them
would be silently ignored.

**Refuted by reading `bot/web/web3_signer.py:245-250`:**

```python
def rpc_url_for(network: str, env: Optional[dict] = None) -> str:
    key = "WEB3_RPC_" + str(network or "").strip().upper().replace("-", "_")
    return str((env or os.environ).get(key, "") or "").strip()
```

The key is built dynamically, so all 11 ARE read. The two naming schemes serve
two subsystems: `WEB3_RPC_*` for testnet signing, `WEB3_RPC_URL_*` for mainnet
wallet reads. Not a defect.

## RC-2026-F02 — `LLM_TIER_LEARNING_MODEL` is declared but never read — **FALSE POSITIVE**

Refuted at `bot/llm/provider.py:701`:
`tier_model = os.getenv(f"LLM_TIER_{tier_upper}_MODEL", "")`, and again at
`bot/core/proactive_monitor.py:1317`. Read via f-string construction, which a
literal-string grep cannot see.

## RC-2026-F03 — `VALIDATION_GATE_ALLOW_UNTESTED` defaults True (permissive) — **FALSE POSITIVE**

The default is deliberate and documented at `bot/config.py:600-606`:
NEVER_TESTED is an absent measurement, not a failed one, and the parent gate
`VALIDATION_GATE_ENABLED` defaults to False anyway. Read at
`bot/risk/risk_engine.py:2172` and pinned by
`tests/test_validation_gate_is_consulted.py:306`. Correct as written, and a
correct application of the repo's own unreadable-is-never-zero rule.

# Areas inspected with no defect found

- **`bot/risk/confidence_floor.py`** — `clears_confidence_floor` tests
  `conf is None` rather than falsiness, so a measured 0.0 is compared and an
  absent reading fails closed. `min_confidence_for` falls back to the stricter
  flat global on any exception. Correct in both directions.
- **Unprotected-position machinery** — `bot/core/live_executor.py:4420` (retry),
  `:5540-5619` (bounded grace sub-loop), `:5648-5657` (marker + escalation),
  `:4498` (explicit "SL/TP FAILED — position unprotected!" surface). A filled
  entry whose stop fails is detected, retried, escalated and surfaced rather
  than silently left naked.
- **`app/server.js:170-208`** — `script-src` is SHA-256 hashed per inline block
  rather than `'unsafe-inline'`; `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`, `frame-ancestors 'none'`, HSTS, nosniff and
  `Referrer-Policy` all set before the static handler.
- **`/diagz` (`app/server.js:252-262`)** — fail-closed (404 when `DIAG_TOKEN`
  unset), length-checked `crypto.timingSafeEqual`, `Cache-Control: no-store`.

---

## RC-2026-006 — GDPR account purge probes an attribute `TelegramHandler` does not have, so the bot's user record is never deleted

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
- **Category**: Privacy / right to erasure (GDPR Art. 17) · data integrity · CWE-670
- **Component**: bot web gateway — `POST /gateway/account/purge`
- **File**: `bot/web/user_gateway.py:2885` (before fix)
- **Fix class**: SAFE_AUTO_FIX — applied, but **compliance-relevant: flagged for human review**

### Evidence (before)

```python
store = getattr(tg_handler, "user_store", None)
if store is None:
    result["user_record"] = "error"
else:
    result["user_record"] = "deleted" if store.forget(tg_id) else "none"
```

`TelegramHandler` binds the store as `self.users`
(`bot/skills/telegram_handler.py:846`), and *every other* call site in this same
file reads it that way — `_is_admin_id` (`:94`), `_web_skill_denied` (`:148`,
`:156`, `:162`), `_guard_user` (`:193`). No `user_store` attribute exists
anywhere on the handler. The probe therefore resolved to `None` on every real
request.

### Reproduction — run, not reasoned

Driving the real handler with a stand-in shaped like production (store on
`.users`, seeded with user `111`):

```
stores = {'exchange_credentials': 'none', 'agent_profile': 'none',
          'agent_memory': 'none', 'leverage_preference': 'none',
          'strategy_preference': 'none', 'user_record': 'error'}
result = PARTIAL          # audit log line, verbatim
```

and `store.get("111")` still returns the record afterwards.

### Observed vs expected

`ok = all(v in ("deleted", "none") ...)` is therefore always False, so the
endpoint answers **409 `{"purged": false}`** — for every user, on every
request, forever. Expected: the record is deleted and the endpoint answers 200.

The failure mode is worse than a plain bug: the operator is told the erasure
*partly* failed, with nothing distinguishing "one store refused" from "this
line never ran at all". A user who exercised their right to erasure was told it
did not fully succeed, while the bot kept their record.

### Why the existing tests did not catch it

`tests/test_account_purge.py` exercises `UserStore.forget` directly and proves
it deletes from memory and disk, and `test_every_per_user_store_is_named_by_the_purge`
scans the handler's source to confirm every store is *named*. Both pass. Naming
a store is not reaching it — this is the failure CLAUDE.md records twice: an
attribute probe naming a field that does not exist and rendering as a confident
negative, and a code path whose only caller was a test.

### Fix

One word — `getattr(tg_handler, "users", None)` — with the reasoning recorded
at the call site.

New test `tests/test_account_purge_reaches_the_user_record.py` drives the real
`handle_account_purge` and asserts the record is **gone from the store
afterwards**, not that the handler mentions one. Its fixture deliberately does
NOT define `user_store`; a fake carrying both names would pass whichever the
code reached for, which is how this stayed invisible. A second test keeps the
genuine `error` branch reachable, so renaming the probe to whatever the object
happens to expose cannot make the check vacuous.

### Validation

`tests/test_account_purge_reaches_the_user_record.py` + `tests/test_account_purge.py`
→ 13 passed. Broader selection `-k "gateway or purge or erasure or user_store"`
→ **169 passed**.

### Rollback

Revert the one-line `getattr` change; the new test then fails, which is the
point.

### Note for the reviewer

This changes behaviour on a **compliance** path — from "never deletes" to
"deletes". It restores the endpoint's documented intent rather than setting new
policy, but it is called out here explicitly because the brief forbids silently
altering compliance behaviour. Operators should also consider whether records
that survived past erasure requests need a one-off sweep; the fix is not
retroactive.
