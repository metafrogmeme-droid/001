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

---

## RC-2026-001 — corroborating evidence added after the dimension agent's run

Two further facts, both read directly and both making the CRITICAL worse:

**The repo's own structural auth guard was told to look away from this route.**
`scripts/guard_lint.py:536` exempts it from `express-route-auth`:

```python
"auth.js:POST /validate-token",   # answers "is this token valid" — the
                                  # token IS the credential being checked
```

That rationale is true of the SELECT at `app/auth.js:874-876` and false of the
UPDATE at `:886-889`. The route does not answer a question; it performs an
identity binding from an unauthenticated field. `guard_lint` reports
`express-route-auth (55 site(s) of 349 candidate(s), all guarded)` — green,
because the one route that needed it is on the exemption list.

**Nothing stops two rows sharing a telegram id.** `app/db.js:2230` declares
`telegram_id VARCHAR(32) DEFAULT NULL` with no unique index, while
`wallet_address` (`:2309`), `referral_code` (`:2340`) and `leaderboard_handle`
(`:2347`) each get an explicit `CREATE UNIQUE INDEX`. So the write succeeds and
the victim's row is left intact — the attacker gains the identity without the
victim losing anything that would make it noticeable.

Remediation therefore has three parts, not one: gate the route on
`X-Bot-Secret` (bot side first — see above), reject a `chat_id` already bound
to another `id` (mirroring the check `auth.js` already performs for
`wallet_address`), and add the missing unique index. The exemption at
`guard_lint.py:536` should be removed in the same change, or the guard will
keep reporting the route as covered.

---

## RC-2026-007 — `setlimit:` callback ownership guard is fail-OPEN on a missing owner tag

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
- **Category**: Broken access control / IDOR (OWASP A01; CWE-639, CWE-863)
- **Component**: Telegram bot — trade callback handling
- **File**: `bot/skills/telegram_handler.py:14007` (before fix)
- **Fix class**: SAFE_AUTO_FIX — applied
- **Credit**: surfaced by the `telegram-authz` dimension agent (W-20); independently re-derived and verified here.

### Evidence (before)

`_uid_matches` is documented allow-all on an empty expectation
(`telegram_handler.py:2989-2992`):

```python
if not expected_uid:
    return True
```

Three trade-callback branches consume it. Two are fail-closed:

```python
# :14066 (confirm), :14199 (reject)
if not expected_uid or not self._uid_matches(caller_uid, expected_uid):
```

under a comment at `:14060-14063` stating the rule — *"Every legitimate confirm
button is built as `confirm:<id>:<uid>` … so a missing owner tag means a
crafted/replayed callback — deny rather than allow."*

The third is not:

```python
# :14007 (setlimit)
if expected_uid and not self._uid_matches(caller_uid, expected_uid):
```

The `and` short-circuits on precisely the payload the guard exists to catch.

### Why there is no second layer

`pos_close_` (`:13677`) has the same fail-open shape **deliberately** — its
comment calls the tag "defense-in-depth" and the real isolation is the
caller-keyed `user_portfolios.get(user_id)` (`:13686`) and
`_caller_executor(update)` (`:13692`).

`setlimit:` has nothing equivalent. It reads
`self.engine._pending_ideas.get(trade_id)` (`:14013`), and that store is
declared at `bot/core/engine.py:508` as:

```python
self._pending_ideas: dict[str, TradeIdea] = {}
```

— one global dict keyed by trade id, no owner field, no caller filter on the
read. `engine.confirm_trade` (`engine.py:5631-5633`) performs no ownership
check either.

### Reachable consequence

An authorized bot user sending a crafted `setlimit:<other-user-trade-id>` with
no third field: (1) passes the guard, (2) is shown that trade's asset,
direction, entry, stop-loss and take-profit (`:14035-14038`), and (3) has
`_pending_limit_input[attacker_uid]` armed against the victim's trade id, so the
next price they type retargets it.

Requires an authorized Telegram user and a known or guessed trade id, which is
why this is HIGH rather than CRITICAL.

### Verified not to break anything

All four `setlimit:` construction sites emit the uid —
`telegram_handler.py:2797`, `:8440`, `:9578`, `:11483` — so the untagged form is
reachable only as a crafted payload. A repo-wide sweep for the fail-open shape
(`if expected_uid and not self._uid_matches`) returns exactly one site, the one
fixed.

### Fix

Added `_callback_owner_ok(caller_uid, expected_uid)` — `bool(expected_uid) and
_uid_matches(...)` — and routed all three branches through it. For confirm and
reject this is provably identical (`not a or not b` ≡ `not (a and b)`); for
setlimit it is the fix. One predicate rather than three hand-written copies,
because the defect was drift between copies of the same rule. `pos_close_` is
left alone on purpose.

### Validation

New `tests/test_callback_owner_guard_is_fail_closed.py`: 9 passed. It tests the
predicate by behaviour, and adds two source scans for the property no unit test
can see — that no branch re-hand-rolls the fail-open condition, and that all
three still consult the shared predicate. Both scans strip comments and
docstrings first, because the branches are explained in prose that quotes the
forbidden expression.

**Mutation-checked**: dropping the `bool(expected_uid) and` clause fails 2 tests;
restoring it passes 9. `__pycache__` cleared between mutations per CLAUDE.md.

Related suites (`-k "callback or telegram or isolation or idor or confirm or
reject or uid"`): **581 passed**. ruff and mypy ratchets unchanged. `guard_lint`
exit 0.

### Residual risk

The deeper issue W-19 raises stands and is **not** fixed here: ownership is
carried in the callback round-trip rather than recorded beside the idea, so a
caller who supplies `setlimit:<victim_trade>:<own_uid>` still satisfies a tag
they authored. Closing that needs an `owner_uid` on `TradeIdea` and is a
schema-touching change — REVIEW_REQUIRED, raised for the maintainers.

---

## RC-2026-008 — Backups omit the per-user credential store, and the master key that opens what they do archive

- **Status**: PARTIALLY FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
- **Category**: Credential durability / disaster recovery (CWE-522)
- **File**: `bot/utils/backup.py:35-47`
- **Credit**: surfaced by the `secrets` dimension agent (W-13); re-derived and verified here.

Two separable defects behind one omission.

### (a) `data/exchange_creds.enc` was not archived at all — **FIXED**

`_CRITICAL` listed `data/secrets_vault.enc` (the operator's encrypted secrets)
but not `data/exchange_creds.enc` (`bot/core/exchange_credentials.py:40`),
which holds every linked user's `api_key`/`api_secret`/`passphrase` and their
Hyperliquid/Paradex agent private keys. `_CRITICAL_GLOBS` is
`data/learning/*`, `data/portfolio_*`, `data/risk_state_*` — none matches it.

The file is written on every `/connect` and every website credential pull
(`bot/utils/credential_pull.py`), so it is populated in normal operation. Any
restore returned the operator's keys and none of the users'.

Two ciphertext files of identical shape, one archived and one not: an
oversight, not a policy. Added, with a test asserting the critical set's
**contents** — `tests/test_backup_durability.py` covers round-trip, tamper
detection, rotation and throttling, all of which pass over whatever `_CRITICAL`
happens to list, so none of them could see a missing entry.

### (b) The Fernet master key is still not archived — **OPEN, REVIEW_REQUIRED**

`data/.exchange_secret.key` opens **both** stores —
`exchange_credentials.py:41` `_KEY_FILE` and `secrets_vault.py:44`
`_MASTER_KEY_BASENAME` are the same file. It is in neither `_CRITICAL` nor the
globs. An off-host restore — which `docs/DURABILITY.md` states is the point
("a backup on the same disk protects against bad deploys, not dead disks") —
therefore yields a vault whose every entry fails to decrypt, and the bot boots
with none of its exchange credentials.

Archiving a key alongside the data it opens is a security trade-off, so it is
**not** applied here. Note for whoever decides: `data/attestation_key.bin` is
already in `_CRITICAL`, so key material in the archive is established practice
in this repo rather than a new precedent. The alternative is to keep them
separate and have `create_backup()` record in the manifest that the key is
externally managed — which at least makes the dependency visible instead of
silent.

`docs/DURABILITY.md`'s "what is irreplaceable" table repeats the same omission,
and its restore verification (check `/anchor` still VERIFIED) probes the
attestation key — which IS archived — and nothing needing the Fernet key. So
the runbook cannot catch it either. Both want updating with whatever is decided.

### (c) Noted, not fixed — `RUNECLAW_STATE_DIR` silently drops the vault

`_ENV_OVERRIDES` (`backup.py:49-52`) maps only `ANCHOR_STATE_PATH` and
`PROOFOFPNL_PUBLICATION_PATH`. Every other entry is a literal `data/...`, while
`secrets_vault.py` and `exchange_credentials.py` both resolve their paths
through `RUNECLAW_STATE_DIR`. On a deployment that sets it, `critical_paths`
looks for `data/secrets_vault.enc`, finds nothing, and — because it filters on
`is_file()` — skips it without complaint. Same class as (a): a backup that
reports success while missing the thing it exists to protect.

### Validation

`tests/test_backup_covers_the_credential_stores.py` 4 passed (2 failed before
the change). Existing `-k "backup or durability"` suites: **43 passed**. ruff
and mypy ratchets unchanged; `guard_lint` exit 0.

---

## RC-2026-009 — `/performance` paper branch publishes a hardcoded "Week PnL" of $0.00 in green

- **Status**: OPEN · **Severity**: MEDIUM · **Confidence**: CONFIRMED
- **Category**: Honesty of displayed measurement (CLAUDE.md's own top rule)
- **File**: `bot/skills/telegram_handler.py:12555` (value), `:12571-12572` (render)
- **Fix class**: REVIEW_REQUIRED (what an unmeasured tile should say is a product call)

The live branch of this handler is careful and visibly so — `win_rate` is
`None` rather than `0` when nothing could be scored (`:12461`), `realized_totals`
separates net-unknown from net-zero, `best_and_worst` refuses to rank unpriced
closes under a comment explaining that a sort key becomes a claim once the
order is published as "Best 🏆".

The paper branch beneath it (`:12539-12560`) has none of that:

```python
data = {
    "today_pnl": round(state.daily_pnl, 2) if hasattr(state, "daily_pnl") else 0.0,
    "week_pnl": 0.0,
    ...
```

`week_pnl` is a literal. Nothing computes it. It is then rendered as a
first-class tile beside real numbers:

```python
{"label": "Week PnL", "value": f"${data.get('week_pnl', 0.0):+,.2f}",
 "color": "green" if data.get("week_pnl", 0.0) >= 0 else "red"},
```

so the card always shows **Week PnL $+0.00 in green**. Not "unreadable rendered
as zero" — never measured, rendered as a measurement, in the colour that means
profit. CLAUDE.md: *"Colour is a claim. A green accent says 'in profit' as
loudly as the number does."* And `(x or 0) >= 0` is listed there as the shape
that silently asserts **unreadable won**, because `0 >= 0` is true.

`today_pnl` has the same shape one step weaker: an engine state without
`daily_pnl` renders `$+0.00` green rather than saying it could not be read.

Remediation: give the tile the three-valued treatment the live branch already
uses — a muted colour and an em dash when the figure was not computed — or
drop the tile in paper mode. Either is a display-policy choice, hence
REVIEW_REQUIRED. The renderer is built inline in a 14k-line handler, so the
repo's own guidance applies: extract the tile builder before fixing it, or the
fix cannot be tested.

---

## RC-2026-010 — the honest "unscored" win rate makes the whole stats card disappear

- **Status**: OPEN · **Severity**: MEDIUM · **Confidence**: CONFIRMED
- **Category**: `is None` vs falsiness, one layer out
- **File**: `bot/skills/telegram_handler.py:12567` and `:12574`
- **Fix class**: SAFE_AUTO_FIX (proposed, not applied — same seam problem as above)

`:12461` deliberately makes the rate `None` when nothing could be scored:

```python
win_rate = (_ws["rate"] * 100) if _ws["rate"] is not None else None
```

with a comment saying `... if rate is not None else 0` had converted "nothing
to measure" into a measured zero. Correct. The value then reaches:

```python
_wr = data.get("win_rate", 0.0)          # :12567
...
"value": f"{_wr:.0f}%",                   # :12574
```

`.get(key, default)` returns the **stored** `None`, because the key is present.
The default never fires. Verified directly:

```
>>> d = {'win_rate': None}; wr = d.get('win_rate', 0.0); f"{wr:.0f}%"
TypeError: unsupported format string passed to NoneType.__format__
```

The block sits inside `try:` at `:12563` ("guarded — falls back to the text
readout"), so it does not crash. It silently drops the entire PNG stats card —
every tile, PnL and trade count included — in exactly the case the upstream fix
exists to communicate.

Two defects in one line: the `.get` default is dead code that reads as
protection, and the guard converts a specific, fixable formatting bug into a
whole missing card. Fix: `_wr = data.get("win_rate")` then
`"—" if _wr is None else f"{_wr:.0f}%"`, with the tile's colour muted in that
case. Not applied because the card is assembled inline; per CLAUDE.md the
builder wants extracting first so the fix can be tested at all.

---

## Method note — my own sweep produced a false positive too

The regex `len\(\s*\w+\s*\)\s*-\s*\w*wins?\w*`, written to catch the
`losses = len(all) - wins` shape, matched
`bot/formatters/rich_cards.py:75`:

```python
for i in range(window, len(closes) - window):
```

because **win**dow contains "win". A reminder that the scan tells you where you
looked, not what is there — the same trap CLAUDE.md records for short-string
assertions, arriving in the search rather than the assertion. 75 raw shape hits
were found across the rendering and decision modules; the two above are the
ones that survived reading the surrounding code.
