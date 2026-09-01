# Independently verified findings (auditor's own reading, not agent-reported)

Every item below was read in the source by the lead auditor, not accepted from
a subagent. Refuted candidates are kept, because a register that shows only
what stuck cannot be checked.

---

## RC-2026-001 — Unauthenticated `/api/auth/validate-token` allows binding any
## Telegram identity to an attacker's own web account

- **Status**: FIXED · **Severity**: CRITICAL · **Confidence**: CONFIRMED
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

### Remediation — APPLIED, in three layers

**1. Bot half.** `bot/skills/user_middleware.py` sends
`"X-Bot-Secret": os.getenv("BOT_SYNC_SECRET", "")` on the `/validate-token`
call, read per request rather than at import — the reason
`bot/utils/website_sync.py:104` gives for the same header: a vault restore or
an admin repair must not need a bot restart to take effect.

**2. Server half.** `linkBotAuth` gates the route on the constant-time compare
`sync.js:273` uses, with the length pre-check that keeps a wrong-length secret
a clean 403 instead of a crash to 500.

`linkBotSecretVerdict` is three-valued, and the third value is the point:
`unconfigured` is neither `bad` nor `ok`. A server with no `BOT_SYNC_SECRET`
has not *checked* anything — passing would be "absent is a measurement", and a
403 would send an operator hunting a mismatch that does not exist. It answers
503 `link_not_configured`, and a bad secret answers 403 `invalid_bot_secret`:
coarse codes from a fixed vocabulary, the `/readyz` rule.

The deploy-ordering constraint that made this REVIEW_REQUIRED is handled by an
observe-first ladder, `LINK_BOT_SECRET_GATE` ∈ `off|warn|block`, **defaulting
to `block`**. A ladder defaulting to `warn` would leave the CRITICAL open on
every deployment that never sets the variable — which is every deployment that
exists. `warn` remains available for an operator who must go web-first and
wants the transition window visible; it is a choice they make, not a state they
land in. An unrecognised value falls to `block`, so a typo in an `.env` cannot
disable an auth gate.

**DEPLOY THE BOT BOX FIRST.** With `block` as the default, a web-first deploy
refuses every `/link` until the bot half follows.

**3. The layer that does not depend on deploy order.** A `chat_id` already held
by a *different* row is refused 409 `telegram_already_linked`, before any
write. This needs no secret and no bot-side change, so it holds at every rung
including `off` — a deployment part-way through the two-sided rollout is still
protected from the takeover, which is the one outcome a victim cannot undo.
`id != ?` and not a bare match, so a user re-linking their own id still works.

`app/db.js` adds a unique index on `users.telegram_id` for the race the
application check cannot close (two concurrent calls both reading "unclaimed").
Its catch **distinguishes** where the surrounding ones say `/* present */`:
reporting "there are duplicates, so the index was not created" as "already
installed" would report a security control as present on precisely the
deployment where it could not be.

**4.** `scripts/guard_lint.py`'s exemption note — which recorded the reasoning
that let this through, "the token IS the credential being checked" — is
corrected. The route stays exempt from `express-route-auth`, because it
genuinely cannot carry a session, and the note now says what does gate it.

### Verification

The route had **zero** tests. It has 22 now:
`app/test/link_token_identity_binding.test.js` (15) drives the nine
rung×verdict cases directly and then runs the finding's own reproduction
through the real router, reading the **victim's row** afterwards — the 403 is
not the claim, the claim is that the identity did not move.
`tests/test_link_sends_bot_secret.py` (7) drives `cmd_link` and reads the
headers off the request object it actually constructs, because
`os.getenv(...)` appearing in a dict literal proves the line exists and not
that the dict is the one that gets sent (#999).

Ten mutations, all killed: default rung `block`→`warn`; dropping the
`timingSafeEqual` length pre-check; scoring `unconfigured` as `ok`; dropping
`AND id != ?`; unmounting the gate from the route; letting an unknown rung fall
open; reverting the shim fix below; removing the header; capturing the secret
at import time; sending a placeholder.

One defect was found *by* this work, in the test double rather than the code:
`MemoryDB`'s `FROM USERS WHERE TELEGRAM_ID` branch answered `telegram_id = ?`
alone, so `AND id != ?` matched the row it names to exclude. The shim was
**less** correct than the statement it was handed — the worse direction, since
MySQL honours the clause and only the test lied. Fixed, and it is the same
lesson the `siwf_nonces` branch four hundred lines below already records.

### Residual risk

Any `telegram_id` already mis-bound by this route stays mis-bound; the fix does
not clean existing rows. If duplicates exist, the unique index will not install
and `app/db.js` prints the query that finds them. A one-off audit of `users` is
still a separate operator action.

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

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **(c) FIXED.** Reproduced first: with `RUNECLAW_STATE_DIR` set, `critical_status` found **only `runeclaw.db`** — both credential stores dropped out of the archive and the run reported success. Both locations are searched now (searched, not redirected: not every `data/` writer honours the variable, and a redirect trades one silent miss for another), and results are de-duplicated.
  → **The honest half, which is the larger one.** Only the ALL-absent case was ever reported, so an archive missing exactly the two files it exists to protect came back as an unqualified success — a partial total printed as whole, on the disaster-recovery path. `create_backup()` now records `missing` and `complete` in the manifest and logs `BACKUP IS PARTIAL` naming what was skipped.
  → **(b) The key is still NOT archived, deliberately, and the decision is still yours.** Putting a Fernet master key beside the ciphertext it opens is a security trade-off, not an audit fix. What is fixed is that the dependency was *silent*: the manifest's `externally_managed` now states that `data/.exchange_secret.key` opens both stores and that a restore without it cannot decrypt either. A restore operator learns this before the decrypt fails rather than after. `tests/test_backup_reports_what_it_missed.py` pins the key as absent in **both directions**, so if someone archives it they do so deliberately and update the note in the same commit.
  → **`docs/DURABILITY.md` updated on all three counts the finding named**: the "irreplaceable" table gains `exchange_creds.enc` and the master key, the restore procedure gains a manifest check and a step to restore the key from wherever it is kept, and the verification list now includes `/livebalance` — the only probe that exercises the Fernet key. The old list checked `/anchor`, which proves the *attestation* key survived, and that key **is** archived, so the runbook could not have caught this.
  → 13 tests, 6 mutations killed — including "archive the key", which fails now rather than passing quietly.
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

- **Status**: FIXED · **Severity**: MEDIUM · **Confidence**: CONFIRMED
  → **FIXED**: the week is `None` and the tile is gray, via `performance_card_payload`.
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

- **Status**: FIXED · **Severity**: MEDIUM · **Confidence**: CONFIRMED
  → **FIXED**: an unscored rate renders an em dash instead of taking the card down.
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

---

# CORRECTION — RC-2026-007 severity was inflated (HIGH → LOW)

I rated the `setlimit:` fail-open guard **HIGH** on the reasoning that it let a
caller "rewrite the entry price of another user's pending trade". An
adversarial verifier challenged the impact, and it is right.

`engine._pending_ideas` is populated by the engine's own scan
(`bot/core/engine.py:4258`, `:6576`) and read as one book — `/latest_signal`
reads `self.engine.pending_ideas` wholesale. It is **shared**, not per-user.
Every user holding the `scan` permission is already handed a legitimate
`setlimit:<id>:<own_uid>` button for ideas in that book. So the untagged
payload did not let anyone reach a trade they could not otherwise reach; it let
them skip a tag check on a resource they already had access to.

There is no "another user's pending trade" in a shared book. The phrase was
mine and it was wrong.

**What does not change**: the fix is still correct. An untagged payload cannot
come from any of the four construction sites, so honouring it is honouring a
crafted callback; and the branch now matches its two siblings, which were
deliberately made fail-closed with a comment saying why. It is defence-in-depth
hardening of a guard that was not guarding — worth doing, not worth paging
anyone.

**Corrected**: severity LOW, category defence-in-depth rather than broken
access control. The register, the JSON artifact and PR #227 are updated.
Recorded rather than quietly edited, because a severity that moves is exactly
what a later audit diffing these ids needs to see.

The same reasoning downgrades the related SUSPECTED finding (ownership carried
in the callback round-trip): on a shared idea book, a caller authoring their own
tag is not crossing a boundary either. Recording an `owner_uid` on `TradeIdea`
remains the right design if the book ever becomes per-user — which is the
condition that would make it matter.

---

# Adversarial verification results — 4 dimensions, 22 raw findings

Each finding was judged by two independent verifiers with distinct lenses
(evidence/correctness, and reachability/prior-art), both instructed to default
to refuting. Refuted by both → REFUTED; by one → SUSPECTED; by neither →
CONFIRMED.

**15 CONFIRMED · 6 SUSPECTED · 1 REFUTED · 0 unverified**

## CONFIRMED (15)

| severity | dimension | finding |
|---|---|---|
| CRITICAL | web-authz | `/api/auth/validate-token` identity binding (RC-2026-001) |
| HIGH | web-authz | `/api/auth/2fa/disable` has no throttle, lockout or attempt counter |
| HIGH | py-api-authz | `/risk/halt` swallows the halt failure and returns hardcoded success |
| HIGH | py-api-authz | account purge never deletes the user record (RC-2026-006, fixed) |
| HIGH | py-api-authz | Redis unreachable at boot silently downgrades JWT revocation to in-process |
| HIGH | telegram-authz | `/risk` "Safe Mode" button changes no state but replies "Safe mode is on" |
| HIGH | secrets | backups omit the master key and the credential store (RC-2026-008) |
| MEDIUM | py-api-authz | `dashboard_api.py` authenticates the snapshot WRITE but not the READ |
| MEDIUM | py-api-authz | unauthenticated `/api/lab/run` allows unbounded subprocess/job growth |
| MEDIUM | telegram-authz | confirm/reject double-tap guard consumes the trade before the ownership check |
| MEDIUM | secrets | gitleaks path allowlist disables the Solana keypair rules under `tests/` and `app/` |
| MEDIUM | secrets | an undecryptable LLM key is returned as ciphertext and reported as present |
| LOW | py-api-authz | `/lab/status` returns subprocess stderr to unauthenticated callers |
| LOW | py-api-authz | `handle_policy_clear` swallows the failure and answers `ok: true` |
| LOW | secrets | `/connect` and `/setexchange` echo a raw ccxt exception to the user |

Three of these are already fixed on this branch (RC-2026-006, RC-2026-007,
RC-2026-008a). **The remaining twelve are open and none has been remediated** —
they are reported, not resolved.

Two deserve immediate attention alongside the CRITICAL, both because they are
the honesty defect this repo's own CLAUDE.md exists to prevent, on controls
that stop losses:

- **`/risk/halt` returns a hardcoded success** while never performing the halt
  its docstring promises. An operator hitting the emergency stop is told it
  worked.
- **The `/risk` "Safe Mode" button changes no state** and replies "Safe mode is
  on". Same shape, same panel.

## SUSPECTED (6) — one verifier refuted, so treat as unproven

`/api/push/subscribe` row re-assignment · `/api/lab/status/:id` has no owner
check · `/ready` echoes a raw exception · `/news`, `/funding`, `/duel`,
`/approvals` registered with no authorization gate · callback ownership carried
in the round-trip · the master-key warning's claim about pinning
`RUNECLAW_SECRETS_KEY`.

## REFUTED (1)

The `setlimit:` finding — refuted by both verifiers as **already fixed**, since
I had applied the fix before they ran. Both independently confirmed the finding
was accurate at `fcbb632` and that the current code is correct. A remediation
race, not a substantive refutation; the severity correction above came out of
the same reading.

---

# Money-path batch — independently verified by the lead auditor

The `ai-to-money`, `order-exec`, `risk-engine` and `market-data` dimensions
returned 27 findings; two adversarial verifiers confirmed **25** and refuted 2.
Two are CRITICAL. I read both myself rather than accept them, and both hold —
with one reachability qualification the finder did not state.

---

## RC-2026-011 — Stop-loss orders for a per-user account are signed with the OPERATOR's credentials

- **Status**: FIXED · **Severity**: CRITICAL *(conditional — see reachability)*
- **Fixed by**: `BitgetV3Client.for_account(credentials)` — one place that
  answers "whose keys is this?", replacing `from_config()` at all four v3 call
  sites (both writes and both reads). Two of the four are `@staticmethod` and
  cannot see `self`, so credentials are threaded as a parameter from their
  instance callers; a half-filled credential dict falls back to the operator
  rather than signing with a key and no secret.
- **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **Category**: Cross-account money path (CWE-522, CWE-863)
- **File**: `bot/core/live_executor.py:5109-5116` and `:5228`, via `bot/core/bitget_v3_client.py:46-55`

`LiveExecutor.__init__` is explicitly built for per-user trading
(`live_executor.py:618-630`): it takes `user_id` and a `credentials` dict, and
its own comment says "credentials, when set, {api_key, api_secret,
passphrase}".

The v3 channel ignores them. `_v3_post`, nested inside
`async def _place_sl_tp_v3(self, …)`, builds its client like this:

```python
return cast(dict, BitgetV3Client.from_config().request("POST", path, body_dict))
```

and `from_config()` (`bitget_v3_client.py:46-55`) reads the global
`CONFIG.exchange`:

```python
cfg = CONFIG.exchange
return cls(cfg.api_key, cfg.api_secret, cfg.passphrase)
```

Line `:5228` is the write that matters:

```python
result = await _asyncio.to_thread(_v3_post, "/api/v3/trade/place-strategy-order", payload)
```

`place-strategy-order` is the **stop-loss and take-profit**. So a per-user
executor holding that user's keys places their protective stop, signed with the
operator's keys, on the **operator's** account. Two failures at once: the user's
live position carries no stop on their own account, and the operator's account
acquires a strategy order for a position it does not hold.

`/api/v3/trade/close-positions` is on the same channel.

### Reachability — the qualification the finder did not state

`PER_USER_LIVE_ENABLED` defaults to **False** (`bot/config.py:2261`, and
`.env.example:22` ships it `false`). With per-user live off, every executor IS
the operator executor, `from_config()` is the correct source, and no harm
occurs. **This is latent in a default deployment.**

It becomes real the moment an operator turns on a documented, supported feature
the repo has built substantial machinery for — per-user credential storage, the
`web_live_gate` preconditions, per-user eligibility. Nothing warns that stops
will land on the wrong account when they do. That is why it stays CRITICAL
rather than being downgraded: the trigger is a normal product operation, not an
exotic misconfiguration.

### Remediation (proposed, not applied)

`_v3_post` is inside an **instance** method, so `self._credentials` is already
in scope. Build the client from it when set and fall back to `from_config()`
only for the operator executor. `BitgetV3Client.__init__` already takes explicit
credentials, so no new plumbing is needed.

Not applied here because it changes which account a live order reaches — the
brief forbids altering financial behaviour silently, and this wants an operator
to confirm the intended semantics and to check whether any per-user account
currently carries a stop the operator's book is holding.

---

## RC-2026-012 — Unreadable live equity silently reroutes the DAILY-LOSS and DRAWDOWN breakers to the paper book

- **Status**: FIXED · **Severity**: CRITICAL · **Confidence**: CONFIRMED
- **Fixed by**: `d0dd61c` — `risk_engine.py:1038` refuses a live evaluation
  with no readable equity, via an explicit `live_mode` parameter wired at both
  engine call sites. One early return before any gate measures anything, so all
  three fail-open branches (sizing, daily-loss, drawdown) are covered by
  construction rather than three separate patches.
- **Fix class**: REVIEW_REQUIRED
- **Category**: The repo's own top rule, on the control that decides how much real money is lost before it halts
- **File**: `bot/risk/risk_engine.py:1413-1418` (daily loss), `:1475-1486` (drawdown)

```python
if live_equity is not None and live_equity > 0:
    _daily_pnl = self._live_daily_pnl
    loss_base  = live_equity
else:
    _daily_pnl = state.daily_pnl
    loss_base  = min(sizing_equity, state.equity_usd) if ... else ...
```

and, for drawdown:

```python
if live_equity is not None and live_equity > 0:
    ...
    _cur_dd = (100.0 * (self._live_equity_peak - live_equity) / self._live_equity_peak) ...
else:
    _cur_dd = getattr(state, "current_drawdown_pct", state.max_drawdown_pct)
```

The `else` branch is correct for genuine paper mode. It is also what runs when
the bot **is live and the equity read failed** — `None` or `0` from a timeout,
a venue error, a rate limit. There is no third case.

What it then measures is the paper book. The comment four lines above states
exactly why that is worthless here:

> the paper snapshot's `daily_pnl` is ~0 because live fills never touch the
> paper portfolio

So on a live account with an unreadable equity read, `daily_loss_pct` is
computed from a PnL that is ~0 by construction, and `_cur_dd` from the paper
drawdown. Both breakers see ~0% and **do not trip**. The bot keeps trading
through the loss the breaker exists to stop.

The authors were demonstrably alert to this axis — the same block carries
"Without this the daily-loss breaker could never trip on real losses (audit
CRITICAL, 2026-07-14)". They fixed the direction where live equity IS
available. The unreadable case still falls through to paper.

This is CLAUDE.md's own rule — *unreadable is never zero, and absent is never a
measurement* — reaching the one control the file itself describes as deciding
"how much real money is lost before the bot halts".

### Why it is worse than the 2026-07-14 fix it sits beside

Unlike RC-2026-011 this needs **no feature flag**. It applies to the default
operator live path, and it fails in the direction that spends money.

### Remediation (proposed, not applied)

The call site knows whether the bot is live. Make the three cases distinct
rather than two: live-with-equity gates normally; live-without-equity must
**refuse to trade** (or halt), because an unmeasurable drawdown is not a
passing one; paper mode uses the paper book. Refusing is the tighter direction,
which is the right default for a control of this kind — and it is the shape
`clears_confidence_floor` already uses elsewhere in this codebase.

Not applied: this changes when the bot stops trading. That is a financial
control decision for the operator, not an audit fix.

---

## The other 23 confirmed findings

Recorded in full, with evidence, reachability and proposed remediation, in
`audit/workflow_raw_findings.md` as **M-01 … M-25**. Highlights by class:

**Money-path isolation** — a paper-only user's proposed trade can be
auto-confirmed LIVE on the operator's account as `user_id="auto"`, because
`_pending_ideas` is one engine-wide book and the auto-confirm loop has no
notion of who wrote an entry (`engine.py:4330-4334`; HIGH, race-bounded and
the finder says so).

**Sandbox separation** — `BitgetV3Client` ignores `CONFIG.exchange.sandbox`, so
a demo-configured bot sends live-account stop orders over the v3 channel (HIGH).

**Risk gates measuring the wrong book** — correlation, portfolio exposure,
symbol exposure, PCA concentration and VaR all measure the paper book
(`risk_engine.py:1853,1865,3115-3118`); the LIVE daily-loss accumulator is
dropped by the combined-state restore (`:3478-3491`); a halt never cancels
resting limit ENTRY orders, so new exposure can still arrive after the breaker
trips (`live_executor.py:576-579`).

**Time and market data** — `is_market_open('Stock')` is **11 hours wrong**,
reporting OPEN outside US cash hours (`order_rules.py:56-65`; HIGH); the market
scanner renders an unreported 24h move as a measured `0.00%`
(`market_scanner.py:645`); open-interest reads answer `oi_change_pct: 0.0` —
"OI unchanged" — on a failed fetch (`exchange_flow.py:206-236`).

**Two were refuted** by both verifiers and are recorded as such: the WS-ticker
local-clock stamping and the repaint-guard bar-timestamp comparison.

---

# Re-anchoring after PR #229, and what it turned up

Another session merged PR #229 into main ("The only pre-trade slippage estimate
had a reason for being unwired, and it had expired"), adding ~95 lines to
`bot/core/live_executor.py`. I said I would re-anchor RC-2026-011's line
references rather than leave a reviewer chasing stale numbers. Doing so found
two things the original findings missed, both extending a CRITICAL.

## Line drift — RC-2026-011 anchors, corrected

| what | was | now |
|---|---|---|
| `_place_sl_tp_v3` | ~5102 | **5158** |
| `_v3_post` | 5109 | **5202** |
| `from_config()` inside it | 5116 | **5208** |
| `place-strategy-order` POST | 5228 | **5321** |

RC-2026-012's anchors are unchanged: `risk_engine.py:1413` and `:1475`.

## RC-2026-011 is broader — there are TWO operator-signed writes, not one

A sweep of every `BitgetV3Client.from_config()` call site in the executor:

| line | verb | endpoint | kind |
|---|---|---|---|
| 1390 | GET | `/api/v3/account/settings` | read |
| 4996 | GET | `/api/v3/position/current-position` | read |
| **5208** | **POST** | `/api/v3/trade/place-strategy-order` | **write — the SL/TP** |
| **8765** | **POST** | `/api/v3/trade/close-positions` | **write — the flash close** |

The second write was not in the original finding. `_flash_close_position`
(`live_executor.py:8734`, an **instance** method, so `self._credentials` is in
scope) closes a position:

```python
path = "/api/v3/trade/close-positions"
body_dict = {"category": "USDT-FUTURES", "symbol": bitget_symbol, "posSide": pos_side}
result = await asyncio.to_thread(
    BitgetV3Client.from_config().request, "POST", path, body_dict)
```

On a per-user executor that is a close request signed with the operator's keys.
Two failure modes at once, and this one is worse than the stop case: **the
user's position is not closed**, so they stay exposed; and if the operator holds
a position on the same symbol and side, **theirs is closed instead**. A flash
close is what runs when something has already gone wrong.

Both reads are also on the operator's account, which is a correctness problem of
its own — a per-user executor reconciling against the operator's positions — but
the writes are the money.

## RC-2026-012 is broader — there are THREE fail-open branches, not two

Every `if live_equity is not None and live_equity > 0` in `risk_engine.py`:

| line | gate | else-branch falls back to |
|---|---|---|
| **1033** | **position sizing** | `sizing_equity` = `state.equity_usd` (paper) |
| 1413 | daily-loss breaker | `state.daily_pnl` (paper) |
| 1475 | drawdown breaker | `state.current_drawdown_pct` (paper) |

The sizing one at `:1033` was not in the original finding, and its own comment
states the exact harm:

```python
# LIVE FIX: In LIVE mode, use actual exchange equity for sizing
# instead of paper portfolio equity.  This prevents sizing $2K
# positions against $10K paper when the real account has $50.
sizing_equity = state.equity_usd
if live_equity is not None and live_equity > 0:
    sizing_equity = live_equity
```

When the equity read fails, `sizing_equity` stays at the paper equity — which
is precisely the scenario the comment says the fix exists to prevent. So an
unreadable live equity does not merely stop the breakers tripping; it also
**sizes real orders against a fictional balance**.

All three share one cause: a two-way branch serving three situations — live and
readable, live and unreadable, genuinely paper — with the third and second
collapsed together. Any fix should separate them once, in one place, rather than
three times.

## Method note

I found these only because PR #229 forced me to re-open the file. The original
finding named one write and two branches and was correct about both; it was
incomplete because neither the finder nor I asked *"what else calls this?"*
before writing it up. CLAUDE.md says to ask which OTHER surface makes the same
claim before calling a fix done — that applies to a *finding* as much as to a
fix, and I did not do it the first time.

---

# Batch 3 — ai-injection, injection, browser-sec, honesty-py

**22 raw · 22 CONFIRMED · 0 SUSPECTED · 0 REFUTED.** The only batch so far in
which nothing was refuted. Recovered from the workflow journal after a worker
restart swallowed the completion notification — the findings were produced and
verified normally; only the delivery was lost.

Full detail in `audit/workflow_raw_findings.md` as **B3-01 … B3-22**. The four
at HIGH:

## RC-2026-013 — the operator's `DASHBOARD_TOKEN` is read from the URL fragment

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
- **Fix class**: REVIEW_REQUIRED · **Dimension**: browser-sec · **Raw**: `B3-01`

That token carries trade-confirm, close and halt authority. A URL fragment
survives in browser history, is readable by any script on the page, and leaks
through anything that reflects `location`. `browser-sec`, HIGH.

### Remediation — APPLIED

`_takeTokenFromHash()` consumes and erases in the same breath: read the
`token` param, delete it, and `history.replaceState` the URL without it.

**`replaceState`, not `location.hash = rest`, and that is the whole fix.**
Assigning to `location.hash` pushes a NEW history entry and leaves the
token-bearing one behind it, still reachable with the Back button — a strip
that strips nothing. No pattern match distinguishes the two: both mention the
hash. So the test drives the real function under node against a stubbed
browser and asks what the history entry actually became.

Other fragment params are preserved, and a hash carrying no token is left
completely alone — a security fix that silently ate a future view-router's
state, or rewrote the URL of every page load, would be found the hard way.

**BOTH PAGES.** `bot/web/dashboard.html` and `bot/web/performance_chart.html`
are separate `FileResponse` handlers with no shared asset pipeline, so the
block is duplicated. `tests/test_dashboard_token_leaves_no_trace_in_the_url.py`
is parametrised over both: a fix applied to one and not the other fails.

**RESIDUAL, stated rather than implied.** The token still lands in
`localStorage`, so it survives a browser restart and is readable by any script
that reaches the page. Narrowing that to `sessionStorage` costs the operator a
re-prompt every session — a UX decision, not one to make silently inside a
security fix. The fragment was the defect this finding names, and it is closed.

13 tests, 4 mutations killed (never stripping; assigning `location.hash`
instead of replacing; wiping the whole fragment; rewriting the URL when no
token is present). Three of the test's own fixtures were wrong before the code
was: the stub omitted the browser's leading `#`, and its `replaceState` did not
mirror into `location.hash`.

## RC-2026-014 — `SystemHealthMonitor` is fed by nothing, so `/health`, `/ready` and `/metrics` publish a permanent HEALTHY

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED** in two passes. The first made the snapshot honest — None rather than `0.0` for unmeasured latency and error rate, None rather than `True` for `exchange_connected`, and UNKNOWN as a fourth outcome carried through the Telegram card and /metrics (series omitted, not zeroed).
  → The second is the half that pass could not reach: **nothing FED it.** `record_api_call`, `set_exchange_status` and `record_scan` had no caller in the tree, so DEGRADED, CRITICAL and both of `_is_ready`'s 503 branches were unreachable — the honest UNKNOWN was permanent. `RuneClawEngine._record_exchange_read` now reports every fetch through `_cached_ohlcv` (instrumented *after* the cache hit returns, so a cached series is not counted as a fast success), and `_record_sweep_complete` stamps `record_scan` on the path that is not reached when the scan failed. `set_exchange_status(False)` fires only on TRANSPORT-class failures, matched across the whole exception MRO — a `BadSymbol` is the exchange *answering*, and 503-ing over a delisted ticker is a heuristic promoted to a verdict. The recorded error is the exception's class name plus the symbol, never `str(exc)`: `last_error` renders into the Telegram card and a ccxt error string can carry the request URL.
  → `handle_ready`'s docstring promised a fail-closed contract `_is_ready` deliberately does not implement; the **docstring** is corrected, not the predicate. UNKNOWN is now a bounded boot window rather than a permanent state, so failing closed on it became possible — but that changes how an orchestrator treats a restarting instance, which is a deployment decision, not an honesty fix. Left as a named option rather than taken silently.
  → `tests/test_health_monitor_is_actually_fed.py` (32) drives the real engine functions against a stand-in `self` rather than the monitor directly, because reachability is a property of the CALLERS; all 10 mutations killed.
- **Fix class**: REVIEW_REQUIRED · **Dimension**: honesty-py · **Raw**: `B5-27`
 A monitor with no input reporting
the good state is the exact failure CLAUDE.md's rule describes, on the endpoints
an operator and any uptime checker consult first. `honesty-py`, HIGH.

## RC-2026-015 — `/livebalance` renders a FAILED exchange balance read as a complete $0.00 account statement

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED** by OMIT, per the table in CLAUDE.md, and deliberately **not** by the remedy the second pass rated HARMFUL. `fetch_balance` still returns its zeros-plus-`error` dict, because `bot/main.py` classifies its startup credential preflight on exactly that shape — `bal.get("error")` selects "STARTUP: exchange auth FAILED" and calls `set_live_auth_status(False)`, which halts new live entries. Returning `None` would have traded an honest card for a safety regression.
  → The CARD learned to read it instead. `bot/formatters/live_balance.py` is a new pure seam: `read_balance()` gives a three-valued `BalanceReading` (the venue answered / it failed and said why / that was not a balance), `money()` renders `None` as the word rather than `$0.00`, and `render_balance_block()` prints Cash, Used and Equity as `unknown` with the scrubbed venue reason above them. `holdings` is `None` rather than `[]` — an empty list is a measurement ("you hold no spot") and the failed read must not make that claim either. The NET line and the `pct` divisor were the two other places a `None` equity would have raised or lied.
  → **Composite, so one dead source must not blank the rest**: realized PnL, fees, trade count and exposure come from the local store and the executor's own book and stay on the card. Exposure is relabelled *bot-tracked* in the failed-read block so a figure that did not come from the venue is not read as the venue's, and `max(used, exposure)` no longer prints our number as theirs.
  → **A second defect, not in the original finding.** RC-2026-017 made `free` three-valued upstream (`_free_or_none`), but this call site still did `f"${free:,.2f}"` — `TypeError` on `None`, swallowed by the outer `except`, taking down the *whole* card including everything that was readable. An honest fix upstream had become a crash at an unfixed consumer.
  → `tests/test_livebalance_failed_read_is_not_a_statement.py` (31), all 8 mutations killed. Two of its own tests were wrong first: the handler diverted into new-user onboarding so an absence assertion passed against a welcome message, and a blanket `"$0.00" not in out` failed on a *correct* card whose realized PnL and exposure genuinely were zero. Both are now anchored to the venue's own lines, with a rendered-card guard so neither can pass vacuously.
- **Fix class**: REVIEW_REQUIRED · **Dimension**: honesty-py
 — cash, equity and the rest, presented as a
measurement. `honesty-py`, HIGH.

## RC-2026-016 — the web gateway reports `unprotected: false` for a live position

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED**: `unprotected` is three-valued and the UI chips on the unknown case first.
- **Fix class**: REVIEW_REQUIRED · **Dimension**: honesty-py


that has no stop at all.** This is the inverse of the finding CLAUDE.md already
records about `sl_order` being three-valued: there, an unreadable stop rendered
as "SL None" and alarmed the operator wrongly; here a genuinely absent stop
renders as *protected*, which is the direction that loses money quietly.
`honesty-py`, HIGH.

The remaining 18 are MEDIUM/LOW across browser security (raw LLM research HTML
into `innerHTML` with no sanitiser; CSP `script-src` omitting the Google
Identity script the login page loads; no `Permissions-Policy` anywhere), the
LLM/agent surface (the web chat computes a Guardian prompt-injection verdict and
then discards it; a blanket 200-character cap on every MCP string argument; the
public MCP server's header claiming "every tool is READ-ONLY"), one
authenticated SSRF on the web-push subscription endpoint, and further
unreadable-as-zero renderings in `/performance`, `/classpf`, `/livepositions`
and the Daily Alpha card.

---

# What the verifiers found that the finders missed

Each dimension's verifiers were also asked to name defects the finder had not
reported. Across 12 dimensions they returned **59 items**, now recorded verbatim
in `audit/verifier_surfaced_gaps.md`.

**They are UNTRIAGED and are not findings.** A verifier asserting a defect is
exactly the kind of claim this audit refuses to take on trust; the same standard
that applies to finders applies to them. Per your decision, they are triaged
after the remaining dimensions complete, each getting the treatment the
CRITICALs got — read the code, establish reachability, check for an existing
test — and then confirmed, refuted, or dropped.

Recorded now rather than later because they existed only in this container's
workflow journal.

That said, the list is worth reading before then, because several read more
severe than the findings they were attached to:

- **There is no HTTP route anywhere for the real global kill switch.**
  `RuneClawEngine.emergency_halt_all` (`bot/core/engine.py:2437`) — the only
  thing that halts everything — is reachable from nothing.
- **Trade-executing callbacks sit outside the destructive-callback permission
  map**, so the `viewer` role can execute trades it is explicitly denied at the
  command layer.
- **`/broadcast`** lets a Telegram group admin who is not on the bot allowlist
  post to every registered marketing group.
- **`/autoconfirm off` does not disable auto-confirm** for manual ideas, despite
  `config.py:2306-2311` documenting exactly that.
- **`/news` is dead at runtime** — a zero-argument method behind a command guard
  — and so is the web news endpoint.
- **Any user holding `trade` can suspend the operator's autonomous scanning**
  for up to `PENDING_IDEA_TTL`, via the early return at `engine.py:4109`.
- `bot/web/performance_chart.html:92` loads Chart.js from a CDN with **no
  subresource integrity**, on the same origin that stores the money-capable
  `DASHBOARD_TOKEN`.

The methodological point is the one worth keeping: the finder-plus-verifier
shape catches a great deal, but 59 items in 12 dimensions says it does not bound
what a single finder pass will miss.

---

## RC-2026-017 — a balance payload without `free` clamps every live order to $0 and reports it as a measurement

- **Status**: PARTIALLY_FIXED · **Severity**: LOW · **Confidence**: CONFIRMED
  → **PARTIALLY_FIXED**: an unreadable free margin now REFUSES the order instead of sizing it at $0 and sealing a fabricated figure into the audit chain. Still open: `free` can still arrive unreadable for a USDC-margined venue.
- **Category**: absent-is-never-a-measurement, on the pre-execution size clamp
- **File**: `bot/core/engine.py:6271-6278`
- **Fix class**: SAFE_AUTO_FIX (proposed, not applied — out of scope for the
  RC-2026-012 change and I will not widen a risk-engine PR)

Found by tripping over it: while repairing test scaffolding for RC-2026-012 I
supplied a balance cache of `{"total": …}` and every order silently sized to
zero.

```python
live_bal = await self.get_user_live_equity(user_id)
if live_bal:
    available = live_bal.get("free", 0.0)
    if size_usd > available:
        audit(trade_log,
              f"Live size clamped: ${size_usd:.2f} -> ${available:.2f} (exchange available)",
              ...)
        size_usd = available
```

`if live_bal:` establishes only that *a* payload came back. `.get("free", 0.0)`
then treats a **missing** `free` exactly like a measured zero, so the clamp sets
`size_usd = 0.0`.

The behaviour fails **safe** — no order is placed — which is why this is LOW and
not higher. The dishonesty is in the reporting: the audit line reads

> Live size clamped: $250.00 -> $0.00 (exchange available)

which states as fact that the exchange had $0 available, when in truth nothing
was read. An operator diagnosing "why did my $250 trade not go on" is pointed at
their balance instead of at a payload shape. The block's own docstring is careful
about the neighbouring case — "Fail-safe: returns None on fetch failure, so the
clamp is simply skipped" — so `None` is handled; a payload *present but missing
the key* is not.

Remediation: distinguish the two. `free = live_bal.get("free")`, then skip the
clamp when it is `None` (matching the documented fetch-failure behaviour) and
apply it when a real number came back. Bitget's ccxt payload does carry `free`,
so this is latent rather than active on the current venue — which is exactly why
it wants a test rather than a comment.

---

# Batch 4 — backtest, honesty-js, data-db, concurrency

**33 raw · 31 CONFIRMED · 2 SUSPECTED · 0 REFUTED.** Severity mix of the
confirmed: 1 BLOCKER (downgraded — see below), 4 CRITICAL, 10 HIGH, 12 MEDIUM,
4 LOW. Full detail in `audit/workflow_raw_findings.md` as **B4-01 … B4-31**,
including each finding's verifier notes.

## RC-2026-018 — the default backtest fills entries at prices the market never traded

- **Status**: FIXED · **Severity**: **HIGH** (finder BLOCKER -> verifiers CRITICAL -> second pass HIGH; the fixing session recorded CRITICAL from the pre-correction register. See "the CRITICAL was incoherent" below.)
- **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `bot/backtest/engine.py:593`

`CONFIG.limit_orders` defaults to `enabled=True` with
`default_order_type="limit"` (`config.py:1982-1984`), so the analyzer sets
`idea.entry_price` to a pullback level up to 1 ATR below the close. The
backtest has **no order-type model at all** — `bot/backtest/` contains zero
references to `order_type` — so `_execute_fill` books the position at that limit
price unconditionally on the signal bar, whether or not any bar ever traded
there. The engine captures exactly the entries a real limit order would have
missed: the ones that ran away favourably.

`bot/backtest/models.py:52-57` states the discipline this violates: *"Run both
and compare: a large edge gap between them means the strategy's backtested edge
lives in the fill assumption, not the signal."* Nothing runs that comparison.

### The verifiers corrected this, and the corrections are the important part

I am recording the corrected version, not the finder's.

**1. Severity: BLOCKER → CRITICAL.** No live order path is affected and no money
moves on this code. It corrupts measurement, which is severe, but it is not
ship-stopping in the way a live-trading defect is.

**2. The blast radius is much smaller than claimed — I verified this myself.**
The finder implied every published figure is tainted. It is not:

```python
# bot/backtest/runner.py:546-548
if getattr(args, "honest", False):
    args.strict_data = True
    args.fill_mode = "next_open"
```

and `--honest` is what the published paths actually use — `bot/api/lab.py:164`
passes `"--honest", "--strict-data"`, and `docs/FROZEN_BENCHMARK.md:11,36` runs
`--honest --walk-forward 6`. So the **frozen benchmark and the marketplace
scorecards are NOT affected.**

What *is* affected is everything run in the default `fill_mode="close"`
(`models.py:50`) — including the committed **`backtest_deep_results.json`**, and
the `/backtest` and `/walk_forward` Telegram cards.

**3. I am not quoting the fill percentage.** The finder reported "73% of fills
(35 of 48)"; one verifier re-ran it and measured "21 of 36 (58%)"; the other did
not re-run at all and said so. Two measurements that disagree, neither
reproduced by me. The *mechanism* is proven from source — a limit up to 1 ATR
below the close, filled without being touched — and that is what I am asserting.
A number I have not reproduced does not go in a register that exists to be
trusted.

### Remediation — APPLIED

`_place_entry` now stands between the signal path and `_execute_fill`:

- a **market** order fills at `bar.close` — which is what `fill_mode="close"`
  has always been named for and never did; the old call site passed
  `idea.entry_price` while `_execute_fill`'s own docstring said "bar close in
  legacy mode";
- a **limit** fills at its price only when a bar's range reaches it (LONG when
  `bar.low <= px`, SHORT when `bar.high >= px`). On the signal bar that is
  optimistic about ORDER WITHIN the bar, which is the ordinary bar-data
  limitation and a different thing from inventing the price; the count is
  reported separately as `total_limits_filled_same_bar` so a run leaning on it
  is visible;
- otherwise it **rests**, and `_drain_pending_limits` runs each bar before the
  stop check: fill on touch, expire at `CONFIG.limit_orders.expire_seconds`,
  cancel past `price_drift_cancel_pct`. Both non-fill branches call
  `clear_pending_intent`, which `_execute_fill` does on the fill branch — an
  intent left behind would hold size against every later idea in the run.

Drift uses the LIVE formula, `abs(price - limit) / limit * 100`
(`live_executor.py:6721`), and a test pins the boundary in both directions.
Modelling it differently would trade one fill-assumption defect for another.

**Stated limitation.** Live's `drift_market_fallback` (default ON) converts a
drifted limit to a MARKET order when ADX clears `drift_market_min_adx`. That is
NOT modelled: the ADX at cancel time is not on this path, and a fallback driven
by a guessed momentum reading would put back exactly the class of invented fill
this change removes. So `total_limits_cancelled_drift` is an UPPER BOUND on true
cancellations and the backtest now under-fills against live by that margin —
recorded here rather than left for someone to discover.

**The result carries the lifecycle**: `total_limits_filled`,
`..._filled_same_bar`, `..._expired`, `..._cancelled_drift`, and resting orders
join `total_entries_pending_at_end`. Before this they were structurally
0/0/0/0, which is how a fill assumption hides inside a performance figure: a
run that cannot say how many entries never filled is indistinguishable from one
where they all did.

15 tests in `tests/test_backtest_limit_fills_need_a_touch.py`, including the
register's own acceptance property — every recorded entry lies within some
bar's `[low, high]` at or after the signal bar — plus a conservation check that
every order placed ends in exactly one state, so an engine that quietly dropped
what it could not fill would still fail. Nine mutations, all killed. All 187
existing backtest tests pass unchanged.

**Still true, and unchanged by this fix**: `backtest_deep_results.json` was
produced by the OLD engine and remains a default-mode artifact from before the
fill model existed. It should be regenerated before any figure in it is quoted.

## The other confirmed CRITICALs

- **`buildDefiPositions` returns an all-clear on Aave liquidation risk when
  every check failed** (`honesty-js`). The repo's own rule, on a liquidation
  warning.
- **The migration fast path checks TABLE existence only**, so all 64
  column/index migrations are skipped on an existing database (`data-db`).
- **`/walk_forward`'s out-of-sample window is shorter than the indicator
  warmup** (`backtest`).
- **Under `--honest`, positions open at the next bar's open while [exit logic
  still uses the signal bar]** (`backtest`) — the honest mode has its own
  asymmetry.

## HIGHs worth naming (reported, not fixed, per your scope decision)

`data-db`: arena position close has no transaction and no affected-rows check;
bot sync acks delete the pending row by `user_id` unconditionally, discarding
concurrent writes; portfolio sync deletes all of a user's trades and equity
snapshots before reinserting.

`concurrency`: **no SIGTERM handler at all** — the entire graceful-shutdown path
is unreachable on every deployment.

`honesty-js`: `buildExposure` renders a failed `trades` query as a flat book
("No directional exposure"); the escape planner reads a 502 wallet response as
"No linked wallet found".

`backtest`: `--honest` win rate and trade count are per scale-out **leg**, not
per position; regenerating the published scorecards writes to a directory
nothing reads; the live↔backtest parity report scores an unpriced live close as
break-even.

---

# Batch 5 — infra-cicd, deps, privacy, observability

**33 raw · 31 CONFIRMED · 2 SUSPECTED · 0 REFUTED.** Confirmed mix: 1 CRITICAL,
9 HIGH, 17 MEDIUM, 4 LOW. Detail in `audit/workflow_raw_findings.md` as
**B5-01 … B5-31**.

**The verifiers downgraded 8 of the 10 CRITICAL/HIGH findings.** That is the
strongest signal yet that the finders systematically inflate severity, and the
register records the verifiers' number, not the finder's, in every case:

| finder | verifiers | finding |
|---|---|---|
| CRITICAL | HIGH | `SystemHealthMonitor` is never fed — `/health`, `/ready`, `/metrics` |
| HIGH | MEDIUM | CI pipes an unverified installer into `sh` |
| HIGH | MEDIUM | production image installs a manifest omitting pins |
| HIGH | LOW/MEDIUM | pip-audit audits a different manifest than the deployed one |
| HIGH | MEDIUM | the GitLab CI fallback cannot run |
| HIGH | MEDIUM | chat transcripts never deleted |
| HIGH | MEDIUM | `verify_deploy.sh` reports VERIFIED after a failure |
| HIGH | MEDIUM | forensic audit logs written where they are lost |

On the CI installer one, both verifiers independently made the same point and
they are right: there is **no `actions/upload-artifact` and no release step** in
the whole workflow — the `.so` is `ls -l`'d and discarded. So a compromised
toolchain buys code execution on the runner, not bytecode on a chain. The
installer URL is also already version-pinned (`v1.18.26`, not a floating tag).
Worth fixing; not HIGH.

## RC-2026-019 — the GDPR purge misses the bot's SQLite database entirely

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED**: `purge_user_data` reaches all seven tables, the parent delete is CONDITIONAL on the row being a stub so a collision cannot cascade away somebody's account, and the verdicts survive the audit redactor. NEEDS_LEGAL_REVIEW still applies to whether this satisfies erasure.
- **File**: `bot/web/user_gateway.py:2830-2900`
- **Fix class**: REVIEW_REQUIRED

**This qualifies a fix I already shipped, and I want that stated plainly.**

RC-2026-006 found that `handle_account_purge` probed
`getattr(tg_handler, "user_store", ...)` — an attribute that does not exist — so
the bot's user record was never deleted. I fixed that (now `.users`, at
`:2892`), with a regression test, and it merged in PR #227.

The fix was correct. **The finding was narrower than the real defect.**

The handler imports `exchange_credentials`, `user_profile_store`,
`user_memory_store`, `user_leverage_store`, `user_strategy_store` and
`tg_handler.users` — and **nothing from `bot.db.models`**. Verified by reading
every import and every `result[...]` assignment in the handler. Meanwhile
`bot/db/models.py` holds, for that same user:

- `username TEXT` (`:84`) and the Telegram chat id via
  `link_telegram(user_id, chat_id, username)` (`:314-322`)
- `llm_api_key TEXT` (`:95`) — the user's third-party LLM provider key
- the paper portfolio and personal ingest notes

None of it is touched, before my fix or after. Six stores are reported and the
rollup answers `purged: true`, so the database's absence from the report is
indistinguishable from success — the same shape as the original defect, one
level up.

**The lesson is the one this audit keeps relearning**: I fixed the store the
finding named and did not ask which *other* stores the purge should reach.
CLAUDE.md says to ask which other surface makes the same claim before calling a
fix done. I applied that to the code and not to the completeness of the store
list.

## RC-2026-020 — web-only accounts never reach the purge at all

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED**: the bot purge is no longer gated on `user.telegram_id`; a web-only account is purged under the `web:<id>` identity the gateway actually stores, and a telegram_id spelled `web:*` is refused rather than sent. NEEDS_LEGAL_REVIEW still applies.
- **File**: `app/auth.js:1724-1730`

```js
let botStores = null;
if (user.telegram_id && gateway.isConfigured()) {
```

A NULL `telegram_id` is read as "the bot holds nothing for this person". But
`app/lib/identity.js:23-27` provisions web-only users as `web:<uid>`, and the
bot holds under that key: the auto-provisioned UserStore record, agent profile
and memory, leverage and strategy preferences, the conversation transcript, the
paper portfolio book, and the encrypted LLM and news provider keys.

None of it is contacted, and the user is told their account and its data have
been erased.

Together with RC-2026-019: a Telegram-linked user's erasure misses the SQLite
database; a web-only user's erasure misses **everything the bot holds**.
Both surfaces report success.

---

# Batch 6 — a11y, reachability, docs-consistency, tests

**39 raw · 38 CONFIRMED · 1 SUSPECTED · 0 REFUTED.** Mix: 6 HIGH, 18 MEDIUM,
12 LOW, 2 INFORMATIONAL. By dimension: a11y 11, reachability 7,
docs-consistency 15, tests 5. Detail as **B6-01 … B6-38**.

**Every a11y finding is static inspection.** No browser was driven in this
container, so none is a runtime observation and **no WCAG conformance is
claimed**. Each is `NEEDS_RUNTIME_VALIDATION`.

**The verifiers changed severity on 16 of the 38**, and downgraded 5 of the 6
HIGHs. Only one HIGH survived untouched.

## The finder made a false evidence claim, and both verifiers caught it

The a11y finder wrote that grepping `app/test/` for `strengthmap` "returns only
strengthmap-unrelated files". It does not: `strengthmap.test.js`,
`strengthmap_page.test.js`, `strengthmap_polish.test.js` and
`landing_strengthmap.test.js` all exist. Both verifiers independently checked,
found them, and confirmed that **none pins keyboard operability** — so the
finder's *conclusion* survived while its stated *evidence* was false. One also
corrected a line number (`boot()` is at :275, not :373).

That is the single clearest argument for the two-verifier design in this whole
audit: a finding can be right for reasons its author did not actually verify.

## RC-2026-021 — the security documentation promises human confirmation that the default configuration does not provide

- **Status**: FIXED · **Severity**: HIGH · **Confidence**: CONFIRMED
  → **FIXED**, and the second pass was right that BOTH proposed remedies were unsound — so neither was taken. The product decision it was reserved for turned out not to be the blocker: underneath it was a **code defect**.
  → **The disable switch did not hold.** `config.py` documents "set to 1.0 to DISABLE", `.env.example` ships `AUTO_CONFIRM_THRESHOLD=1.0`, and `/autoconfirm off` writes 1.0. The adaptive block undid all three on a timer, and the branch that did it fastest is the one whose comment says it makes the bot *more* careful: `min(cap 0.90, 1.00 + 0.05)` → **0.90 in a single tick** on a losing streak. The winning-streak branch walked it to 0.60 more slowly. So the operator most likely to discover their disable had been undone is the one who had just lost five trades. `ADAPTIVE_THRESHOLD_ENABLED` defaults ON and appears nowhere in `.env.example`, so nothing in a normal install stopped it. Executed, not read.
  → `bot/core/adaptive_threshold.py` is the new pure seam. Three rules, the last two being the **general** form of the bug rather than a patch over 1.0: a threshold at or above `DISABLED` is not a number to tune; a winning streak may only lower the bar; a losing streak may only raise it. A cap below the current value inverts "be more selective" into a loosening at 0.95 exactly as it does at 1.00. An unreadable threshold counts as disabled — the one direction that must not fail open.
  → Both engine gates now check the sentinel rather than comparing against it. `value >= threshold` made 1.0 mean "needs a perfect score", so a blend scoring exactly 1.0 would have auto-executed through a switch the operator had turned off.
  → **The docs are qualified, not deleted, and the boolean is NOT inverted.** The second pass rated `"requires_confirmation": false` "a safety declaration inverted toward danger on every standard install", and it was right: `cp .env.example .env` is the documented install and it *does* require a human press. Every surface now states the guarantee **and names the flag that suspends it** — `SECURITY.md`, `README.md`, `README.zh-TW.md`, `docs/gitbook/README.md`, and `agent_card.json` (booleans kept `true`, each carrying a note naming `AUTO_CONFIRM_LIVE_ENABLED`).
  → `tests/test_human_confirmation_claim_is_qualified.py` generalises the guard the raw finding asked for — the repo had it on exactly **one** of the surfaces, which was an admission rather than a fix. It asserts a **presence**, not an absence: deleting the sentence would remove a statement that is true as shipped, and this repo has watched an absence assertion misfire four times. Writing it immediately found **12 more claim sites** than the grep had — a comparison table whose "**Required**" column is exactly what auto-confirm turns into the column beside it, two ASCII pipeline diagrams, and the Chinese mirror throughout.
  → `tests/test_autoconfirm_disable_actually_holds.py` (27). 8 mutations killed; a 9th "survivor" was a bad mutation, not a gap.
- **Fix class**: REVIEW_REQUIRED (documentation vs default is a product decision)
- The one HIGH in this batch neither verifier downgraded. I verified it directly.
- **The product decision is still yours and was NOT made here.** `auto_confirm_live_enabled` still defaults `True` in code. What changed is that the docs no longer hide it and the off switch now actually holds.

`SECURITY.md:29`:

> **Human-in-the-Loop** — all trade executions require explicit human
> confirmation; the AI agent cannot autonomously place orders.

`README.md:655` and `docs/gitbook/README.md:45` (the root of the site the
README's documentation badge points at) repeat it. `agent_card.json` declares it
in machine-readable form — `"requires_confirmation": true` (`:36`) and
`"human_in_the_loop": true` (`:48`), which other systems may consume as fact.

`bot/config.py:2317`:

```python
auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", True)
```

**Defaults True**, and the code comment says exactly what that means: "Allow
auto-confirm to place LIVE (real-money) orders with no human press." The engine
then auto-mints the approval token (`engine.py:6058-6065`) and logs
"AUTO-MINT APPROVAL TOKEN … unattended live execution explicitly opted in".

The code is self-consistent and honestly commented. Five public surfaces,
including a machine-readable capability declaration, state the opposite of its
default.

## RC-2026-022 — the public /risk page's categorical claim, and it is worse than reported

- **Status**: FIXED · **Severity**: MEDIUM (verifier-corrected from HIGH) · **Confidence**: CONFIRMED
  → **FIXED**, and worse again than the nine recorded below. An AST walk finds **sixteen** skip-to-passed paths, and **seven of them are `except` handlers** — which contradicts the sentence directly beneath the flagged one on the same page: *"an exception does not skip that check: it records a failure"*. That second false claim was not in the finding. The seven are `USER_RISK_PREF`, `FEE_AWARE`, `REENTRY_COOLDOWN`, `FUNDING_CLOCK`, `INTENT_POLICY`, `VALIDATION` and `AUTHORITY`.
  → **Two of the nine are NOT defects**, checked rather than counted: `RISK_REWARD` and `CONFIDENCE` skip only `if is_manual` — a human supplied the levels, so the check is *not applicable* rather than *unevaluable*. Their `except` branches append to `failed`, correctly. Not every match is a defect, including in a count I inherited.
  → **The root was the engine's own docstring.** `risk_engine.py` stated the categorical form in three places (module, class, and the inline "the contract, verbatim" comment the page quoted). All three now state the scope: fail-closed on the 17 the manifest marks `closed`, with the fail-open and skip paths named. Fixing the page without fixing what it quoted would have left the next writer to re-derive the same false sentence.
  → **A contradiction inside the GitBook, found by the sweep**: `risk-framework.md:166` said *"Of the 20 pre-trade checks, 19 are fail-closed and 1 is fail-open"* — counting the three `fail_behavior: skip` checks among the fail-closed, which is the same defect one page down and disagrees with the manifest and with line 3 of its own file.
  → **The page states no tally**, deliberately. `site/test/site_honesty.test.js` forbids a published risk-check count — the number that matters is per-trade and is already on the decision record — and a manifest tally reads as that count. The page names `LIQUIDITY` and quotes the manifest ("the ONLY fail-open check: no data = pass") instead; the numbers live in the manifest, the GitBook and the guard.
  → `tests/test_risk_page_does_not_overclaim_fail_closed.py` (13). It is **positive wherever possible**: both the corrected page and the corrected docstring *quote* the false sentence to show what was wrong, and a scan cannot tell a quotation from a restatement — CLAUDE.md records four false failures from exactly that shape. The single negative check is anchored to the correction framing. 5 mutations killed, two of them only after being re-run at full strength; the first pass's "survivors" were weak mutations, not gaps.

`site/src/routes/risk.tsx:82`, published to `website/risk/index.html`:

> "There is no path where a check that could not be evaluated is treated as a
> check that passed."

That is CLAUDE.md's own rule asserted as a product guarantee. The finder cited
**three** counter-examples. I counted them myself:

```
$ grep -c 'passed\.append(".*skipped' bot/risk/risk_engine.py
9
```

`RISK_REWARD` (:1603), `MARGIN_RISK` (:1690), `CONFIDENCE` (:1695),
`MACRO_EVENT` (:1982), `MTF_ALIGNMENT` (:1992), `PORTFOLIO_VAR` (:2020),
`TAKER_3BAR` (:2058), `BID_DOMINANCE` (:2096), `VALIDATION` (:2204) — nine
checks that append a *skipped* outcome to the **passed** list.

`config/risk_manifest.yaml` — which `SECURITY.md` and `README.md` both call
authoritative — agrees with the code and contradicts the website: check 17
LIQUIDITY is `fail_behavior: open` ("the ONLY fail-open check: no data = pass"),
and 19/20/21 are `fail_behavior: skip`. `README.md:653` states the accurate
version. So the website is inconsistent with the repo's own README, its own
manifest, and its own engine.

I am recording nine rather than three because I ran the count. The finding was
right and understated.

---

# Batch 7 (final) — frontend-correctness, contracts

**18 raw · 15 CONFIRMED · 1 SUSPECTED · 2 REFUTED.** Mix: 1 CRITICAL (verifiers
say HIGH), 5 HIGH, 5 MEDIUM, 2 LOW, 2 INFORMATIONAL. **This completes all 26
dimensions.**

Two findings were refuted by both verifiers and are recorded as such: the claim
that the Authority Envelope applies no notional ceiling to `transfer`/`withdraw`,
and that `reject_hazardous_extensions` is a deny-list with a catch-all.

## RC-2026-023 — the operator's live dashboard says SIMULATION while trading live

- **Status**: FIXED · **Severity**: HIGH (finder said CRITICAL; both verifiers
  → **FIXED**: the client now reads `trading_mode` and opens as MODE UNKNOWN.
  downgraded, and I agree with them)
- **Fix class**: SAFE_AUTO_FIX
- **File**: `bot/web/dashboard.html:417-419` (markup), `:718-771` (`updateEngine`)

The badge is hardcoded:

```html
<span class="header-badge badge-sim" id="modeBadge">
  <span class="status-dot dot-amber" id="statusDot"></span>
  SIMULATION
</span>
```

`grep -n "simulation" bot/web/dashboard.html` returns nothing — the client never
reads the field. `.badge-live` exists in the CSS at `:97-101` and is reachable
from no code path.

**The server half was already fixed.** `dashboard_server.py:84-97` carries an
`RC-AUD-016` comment saying exactly this: *"report the REAL trading mode, not a
hardcoded True. A hardcoded `simulation_mode: True` made the dashboard show
paper mode even while trading live with real capital."* It now sends
`simulation_mode: false` correctly. The client was never wired to it.

So the fix is present in the payload and unreachable from the UI — which, by
this repo's own reachability rule, is indistinguishable from not being there.
And the connection dot nested *inside* the badge does update, so a live engine
renders as a **green-dot "SIMULATION"** badge: the colour says healthy, the word
says no money at risk, and both are wrong together.

**Why HIGH and not CRITICAL** — both verifiers made the same point independently
and it is correct: this is a display-only operator console behind a Bearer
token, no trading decision is gated on it, and the operator learns the mode
elsewhere (`bot/main.py:47` prints it at boot; `telegram_handler.py:1275`
derives LIVE/PAPER/IDLE from `CONFIG.is_live()`). The lie points the dangerous
way, so HIGH rather than MEDIUM.

The remediation needs **three** states, not two, because
`dashboard_server.py:98-99` has a branch emitting `{"state": "UNKNOWN"}` with no
`simulation_mode` key at all: `true` → SIMULATION, `false` → LIVE, anything else
→ MODE UNKNOWN with a neutral colour.

## The other confirmed HIGHs — all the same defect class

Every one is the repo's own rule on the operator's own console:

- **A swallowed positions-read exception renders as "No open positions"**
  (`dashboard_server.py:151-167` — `except Exception: pass`, then HTTP 200 with
  `[]`). `user_gateway.py:1667-1670` returns `503 positions_unavailable` for the
  same data, so the correct pattern exists in the codebase already.
- **"CIRCUIT BREAKER: OK" painted green from an absent reading.**
- **"Daily: +0.00%" in green with 0 open positions** from unreadable data.
- **The chat drawer stamps a confident PAPER badge** on a portfolio payload it
  could not classify.
- `contracts`: the Solana delegate scan queries only the legacy SPL Token
  program, missing Token-2022 delegates (verifiers: MEDIUM).

Five of the six HIGHs in the final batch are the same shape — *unreadable
rendered as a confident value* — on the screen an operator watches while real
money moves.

---

## RC-2026-024 — the secret scanner reports another branch's leak as this PR's

- **Status**: OPEN · **Severity**: MEDIUM · **Confidence**: CONFIRMED (mechanism)
  / NEEDS_RUNTIME_VALIDATION (the specific leak)
- **Fix class**: REVIEW_REQUIRED — no fix pushed; this changes a security gate
- **Dimension**: infra-cicd · **File**: `.github/workflows/ci.yml:482-500`
- **Standard**: NIST SSDF PS.1 / PW.7; CWE-1120 (excessive code complexity in a
  control) is a poor fit — the closer statement is that a control whose alarms
  are unattributable is a control people learn to ignore.

**Found by tripping over it.** `Secret scan (gitleaks)` failed on this PR's head
`97476bd`, and the two steps in that one job disagreed with each other:

| step | scope | gitleaks | result |
|---|---|---|---|
| pull-request scope | `c40c4d6^..97476bd`, 2 commits, 60,170 B | 8.24.3 | **no leaks found** |
| full history | (see below) | 8.28.0 | **leaks found: 1** → exit 1 |

**Reproduction** — CI's own pinned binary, downloaded and checksum-verified
against the workflow's own pin (`a65b5253…40ae`, `sha256sum -c` → `OK`), with
the repo's `.gitleaks.toml` and `--baseline-path .gitleaks-history-baseline.json`:

```
$ gitleaks git --log-opts="c40c4d6^..97476bd" .   2 commits,     60,170 B  -> no leaks found
$ gitleaks git --log-opts="97476bd" .          1037 commits, 86,063,153 B  -> no leaks found
$ gitleaks git --log-opts="990ef73" .          1037 commits, 86,063,153 B  -> no leaks found
$ gitleaks git .            (all refs)         1039 commits, 86,080,867 B  -> no leaks found
$ git fetch <url> refs/pull/237/merge
$ gitleaks git --log-opts="$(git rev-parse FETCH_HEAD)" .
                                               1037 commits               -> no leaks found
```

The 2-commit scan is **byte-identical** to CI's own PR-scope scan (60,170 B), and
the all-refs scan is byte-identical to CI's **main** run 567 (1039 commits,
86,080,867 B, no leaks). So the local environment reproduces CI exactly; it is
not a version or configuration difference.

CI's **PR** run 566 on the same content reported **1079 commits, 86,367,776 B** —
40 commits and 287 KB that neither this branch's history nor main's contains.

**Root cause (confirmed):** `gitleaks git .` with no `--log-opts` scans **every
ref in the checkout**, not the history of the commit under test. The 1037 → 1039
delta above is exactly the two then-unmerged commits on an unrelated branch
(`claude/new-session-fk85gd`), and nothing else. `actions/checkout` with
`fetch-depth: 0` fetches every branch, so the scan's subject is "the repository
as the runner happened to see it", not "this pull request".

**Consequence:** one leak on any branch anybody pushes turns this check red on
every open PR and names a commit the PR never touched — while the PR-scope step
sitting directly above it says *"✅ No leaks detected"*. That is the failure mode
`ci.yml`'s own comment says the `pull_request` gating exists to prevent: *"A step
that is guaranteed red on a whole class of events carries no signal and trains
people to click past the scanner."* Same hazard, reached by the other door.

**What is NOT established.** The specific leaking commit is not identified, and
this finding does not claim there was no real secret. Output is `--redact`ed, the
report names a fingerprint the baseline does not hold, and the refs carrying
those 40 commits are no longer reachable — so the evidence is gone. Two facts are
consistent with the mechanism above and neither proves it: run 565, on the
unrelated branch `claude/runeclaw-llm-rtx-setup-hwvebb`, failed the same check in
the same minute; main passed it 23 minutes later. **If a real credential was
briefly pushed on a branch, it is still in that branch's objects until GitHub
garbage-collects them, and this finding must not be read as an all-clear.**

**Remediation.** Scope the full-history step to the commit under test:

```yaml
./gitleaks git \
  --config .gitleaks.toml \
  --baseline-path .gitleaks-history-baseline.json \
  --log-opts="HEAD" \
  --redact --no-banner .
```

A PR is then judged on its own history, which is the question the step's comment
says it is asking (*"is anything still reachable in this repository's history"* —
of **this** ref). The all-refs sweep keeps real value, but as a scheduled job or
on `push` to main, where a red result is actionable by whoever can act on it.
Pair it with a fingerprint the operator can act on: the current step prints a
redacted count and no location, so the reader cannot tell a real incident from
this one.

**Test.** `.github/workflows/` has no test harness, so the assertion belongs with
the other CI-parity checks in `tests/test_preflight_matches_ci.py`: parse the
`secrets` job and assert the full-history step passes an explicit `--log-opts`,
with the reason in the failure message.

**Residual risk.** Scoping to `HEAD` means a secret pushed on a branch that is
never merged is not caught by PR CI. That is the correct trade — it was never
that step's job, the branch-tip sweep belongs on a schedule, and GitHub's own
push protection covers the push itself.

**Rollback.** Delete the `--log-opts` line. One line, no state.

## Confirmed by the experiment I could not run at the time

The finding above was written from a local reproduction plus an inference: that
the 40 extra commits came from refs the runner held at 08:17 and no longer
holds. CI has now run the identical check on the same branch, and the inference
is confirmed.

| run | head | commits | bytes | verdict |
|---|---|---|---|---|
| 08:17 | `97476bd` | 1,079 | 86,367,776 | **leaks found: 1** → exit 1 |
| 11:37 | `ec6b977` | **1,081** | **86,427,022** | **no leaks found** |

Nothing about the scanner changed between them: same pinned 8.28.0, same
`.gitleaks.toml`, same `--baseline-path`, same runner image, same `git 2.55.0`.
Nothing about the branch's own history was removed — it **grew** by two commits,
and the second run scanned 2 more commits and 59 KB more content than the one
that failed.

So the scan that saw MORE was clean and the scan that saw LESS was not. That is
only possible if the offending content was never in the set under test, and the
whole delta is what else happened to be in the runner's checkout at the time.
The mechanism is no longer an inference: **this check's verdict depends on which
branches exist when it runs.**

It also settles the direction. A green result here is not evidence that the
history is clean — it is evidence that whatever tripped the scanner at 08:17 is
no longer reachable from any ref the runner fetched. Those are different claims,
and the check reports them identically. **The caveat above therefore stands
unchanged**: the leak was never identified, the fingerprint is not in the
baseline, and a credential briefly pushed on a branch survives in that branch's
objects until GitHub garbage-collects them. A gate that goes green because
evidence became unreachable is the same shape as every other finding in this
audit — absent read as clean.

Confidence on the mechanism: **CONFIRMED**. Confidence on the specific leak:
unchanged at `NEEDS_RUNTIME_VALIDATION`, and no longer obtainable from CI.

---

# RC-2026-001 — `/api/auth/validate-token` is unauthenticated and it WRITES

- **Status**: FIXED · **Severity**: CRITICAL · **Confidence**: CONFIRMED
- **Fix class**: REVIEW_REQUIRED — it changes an auth boundary and it has a
  deploy-ordering constraint (below)
- **Dimension**: web-authz · **File**: `app/auth.js:903-935` (before the fix)
- **Standard**: OWASP API Top 10 API2:2023 (Broken Authentication) and API5:2023
  (Broken Function Level Authorization); ASVS V4.1.1; CWE-306 (Missing
  Authentication for a Critical Function), CWE-639 (Authorization Bypass
  Through User-Controlled Key)

**What it did.** Anyone on the internet could POST `{token, chat_id}` and the
route would, with no credential of any kind:

1. look the account up by `link_token`;
2. **consume** the token;
3. **set `telegram_id` to the caller-supplied `chat_id`** and
   `telegram_linked = TRUE`; and
4. return the account's `user_id`, `email` and `plan`.

**The exemption argued the read, and the route is a write.** `guard_lint`
carried it in `express-mixed-module-routes` with the reason *"answers 'is this
token valid' — the token IS the credential being checked"*. That is a sound
argument about a lookup. It is not an argument about binding a Telegram account
to somebody's row and handing back their email. The exemption is deleted, not
reworded, in the same commit as the gate.

**The codebase already knew the rule.** Two entries below it in the same list
sits `auth.js:POST /wallet/link-by-code`, whose comment reads: *"Refuses if the
wallet is already on another account."* And `app/auth.js:1110-1115` implements
it:

```js
// A wallet identifies at most one account (it is also a login key).
const [rows] = await pool.execute(
  'SELECT id FROM users WHERE wallet_address = ? LIMIT 1', [lower]);
if (rows.length && rows[0].id !== req.user.user_id) {
  return res.status(409).json({ error: 'That wallet is already linked to another account.' });
}
```

`telegram_id` had no equivalent. It also had no unique index, while
`wallet_address`, `referral_code` and `leaderboard_handle` all do
(`app/db.js:2309`, `:2339`, `:2347`). So **two rows could hold the same
chat_id**, and every resolver takes the first match — `app/db.js:1492`
(`WHERE telegram_id = ?`) and the tier sync at `:1749`
(`this.users.find(x => String(x.telegram_id) === String(params[1]))`). Which
account a user's tier, trades and exchange credentials attached to would depend
on row order.

**Not a brute-force finding.** `link-token` mints
`crypto.randomBytes(16).toString('hex')` with a 10-minute TTL
(`app/auth.js:889-890`) — 128 bits is not guessable, and saying otherwise would
overstate it. The exposure is every path by which a 10-minute token reaches a
second pair of eyes: pasted into the wrong chat, screenshotted, in a proxy log,
or simply raced. Against an anonymous endpoint, one such token was a complete
account bind plus an email disclosure.

## The fix

Four parts, and the fourth is the one that keeps the other three honest.

1. **`app/lib/bot_auth.js`** — `botAuth` extracted from `app/routes/sync.js`,
   which has used it since the sync endpoints existed. Extracted rather than
   rewritten: a second constant-time comparison is one that can drift into not
   being constant-time with nothing noticing. It now reads
   `process.env.BOT_SYNC_SECRET` **per request** instead of at module scope, so
   whether the channel works no longer depends on import order versus the vault
   restore. All 31 tests that set the variable set it before requiring the
   router, so none change.
2. **`app/auth.js`** — the route takes `botAuth` as middleware, and refuses a
   `chat_id` already bound to a different row with a **409, before the write**,
   so a refusal does not burn the token and the legitimate owner can retry.
3. **`app/db.js`** — `CREATE UNIQUE INDEX idx_users_telegram_id`. Deliberately
   **not** wrapped in the bare `catch (e) { /* exists */ }` its neighbours use:
   on a live table that already holds duplicates this fails `ER_DUP_ENTRY`, and
   swallowing that leaves no index while the code reads as though there is one.
   It distinguishes "already created" from "could not create" and says which,
   naming the manual reconciliation. An absent constraint reported as a present
   one is the defect this repository exists to prevent.
4. **`scripts/guard_lint.py`** — the exemption removed. Without this the rule
   keeps reporting the route as covered and the fix is unverifiable from
   outside.

Plus `bot/skills/user_middleware.py:198` sends `X-Bot-Secret`, reusing
`BOT_SYNC_SECRET` rather than minting a second credential to rotate. An unset
secret is **logged by name and the header omitted** rather than sent blank — a
blank fails the comparison exactly like a wrong one, so the operator would read
"invalid bot secret" for "this bot has none".

**`botAuth` is named as middleware rather than open-coded on purpose.**
`guard_lint`'s rule matches `authMiddleware|optionalAuth|botAuth`, so naming it
is what makes the exemption *deletable*. Open-coding the same check would have
left the route flagged and invited a reworded exemption — the defect wearing a
different hat.

## ⚠ Deploy order: **bot first, then app**

`app/` and `bot/` deploy to separate targets. A new bot against an old server
sends a header the old server ignores — harmless. **Reversed, every `/link`
returns 403.** Merging this is not the same as being safe to deploy in either
order.

## Validation

| gate | result |
|---|---|
| `app/test/link_binding_is_bot_authenticated.test.js` | 6/6 |
| full app suite | **3,607 passed, 0 failed** |
| `tests/test_link_sends_the_bot_secret.py` | 4/4 |
| `guard_lint.py` | 12/12 rules, `express-mixed-module-routes` 196/225 |
| `ruff_gate.py` | 1257 == baseline |

**Four mutations, all killed** — a test that passes against the reverted code
proves nothing:

| mutation | caught by |
|---|---|
| drop `botAuth` from the route | 3 test failures |
| drop the already-bound check | 1 failure |
| move the check *after* the write | 1 failure — the token is burned |
| exemption removed, route left unguarded | `guard_lint` ✗, naming the route |

**A defect in the test found first.** Its seeding used one combined
`UPDATE ... SET link_token = ?, link_token_expires = ?, telegram_id = ? WHERE id = ?`.
`app/db.js`'s in-memory shim pattern-matches SQL and reads parameters
positionally, so that statement matched its `UPDATE USERS SET LINK_TOKEN`
branch, misread `params[2]` as the user id, found nobody, and **silently seeded
nothing**. Four tests failed with a 404 that had nothing to do with the route.
The shim's supported statements are used now, with the reason recorded in the
file.

## Residual risk

The 503 branch in `botAuth` is not reachable through a normal boot —
`app/server.js` refuses to start without `BOT_SYNC_SECRET`. It is reachable
when the router is mounted by something that is not `server.js`, which is what
every test suite does, and it costs one branch. Stated rather than presented as
production protection.

The unique index does not repair pre-existing duplicates; it refuses new ones
and reports loudly if it could not be created. Any account already sharing a
chat_id needs a human decision about which one owns it.

## Rollback

Revert the four files. The index survives a revert and is harmless on its own —
it enforces a property the application would then no longer depend on. Dropping
it (`DROP INDEX idx_users_telegram_id ON users`) is separate and optional.

---

## Correction — the confirmed-finding count was 177 and it is 162

I reported **177 confirmed findings** in the batch-7 commit message
(`97476bd`), in the PR description, and in every status update after batch 7.
The number is wrong. Counted from the batch summaries in
`audit/workflow_raw_findings.md`:

| batch | raw | CONFIRMED | SUSPECTED | REFUTED |
|---|---|---|---|---|
| money-path (`M-*`) | 27 | 25 | 0 | 2 |
| 3 (`B3-*`) | 22 | 22 | 0 | 0 |
| 4 (`B4-*`) | 33 | 31 | 2 | 0 |
| 5 (`B5-*`) | 33 | 31 | 2 | 0 |
| 6 (`B6-*`) | 39 | 38 | 1 | 0 |
| 7 (`B7-*`) | 18 | 15 | 1 | 2 |
| **total** | **172** | **162** | **6** | **4** |

162 is also exactly the number of finding blocks written in that file, which is
the independent check: only CONFIRMED findings get a block, so the two counts
have to agree, and they do.

Separately there are **22 `W-*` items** from the first, rate-limited run that
never went through a refutation pass at all. They are not confirmed and were
never included in the total.

**Where 177 came from.** A running total I carried forward by hand across seven
batch reports instead of recounting. Every individual batch figure I published
was right; the sum was not, and nothing recomputed it because it lived in prose.

That is this repository's own stated lesson — *"a number in prose is the part
that rots first"*, the sentence justifying why `tests/test_claude_md_accuracy.py`
pins the gate count in `CLAUDE.md` — committed by the person auditing for it,
in the headline figure of a security audit, five times in a row.

**The structural fix, not just the number.** `audit/generate_artifact.py` now
derives the release decision from the findings list rather than restating it,
derives the dimension total from the two coverage lists with an overlap
assertion, and counts the untriaged verifier gaps by parsing
`verifier_surfaced_gaps.md` instead of carrying an integer. The remaining hand-
carried number is this one, and the batch table above is now the thing to
recount from.

**Not corrected:** commit `97476bd`'s message. It is pushed and merged into this
branch's history, and rewriting published history to fix a figure would cost
more than the figure is worth. This entry is the correction of record.

---

## RC-2026-001, corrected — the attack needs no leaked token at all

I wrote RC-2026-001 up as: someone who obtains a live link token can bind their
Telegram to that account, and stated the exposure as *"every path by which a
10-minute token reaches a second pair of eyes"*. That is true and it is the
smaller half. The raw finding for the same route (`W-09`, from the first
rate-limited batch, which I had not read when I wrote mine) has the direction
right and I had it backwards.

**The attacker uses their OWN token.** `POST /link-token` is authenticated and
mints a token for the caller's own account — entirely legitimately. The attacker
then posts `{their_own_token, THE OPERATOR'S chat_id}` to the anonymous
`/validate-token`. The route looks the row up by *token* and writes `telegram_id`
from the *body*, so it writes the operator's Telegram id onto the attacker's row
and sets `telegram_linked = TRUE`.

No token has to leak. No race is needed. The attacker needs a free account.

**What that buys, verified in the code rather than taken from the claim:**

`app/lib/identity.js` resolves the bot identity for gateway-backed routes:

```js
const [rows] = await pool.execute(
  'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
if (u && u.telegram_linked && u.telegram_id) return { id: String(u.telegram_id), ... };
```

Its docstring says:

> The identity is always resolved server-side from the DB — the browser can
> never choose who it acts as.

**That sentence is true of the read and false of the write.** The browser could
not choose an identity in the request; it could write one into the column the
read trusts, one route away. A server-side resolution is only as trustworthy as
every path that writes the column it reads, and nothing connected the two.

`resolveBotIdentity` is imported by a dozen routers. The `telegram_required`
gates (`credentials.js:81-83`, `controls.js:78-80`) are satisfied because
`/validate-token` also sets `telegram_linked = TRUE` — the same write.

**And the 2FA step-up does not stop it**, `app/routes/staking.js:55-66`:

```js
const [rows] = await pool.execute(
  'SELECT totp_enabled, totp_secret FROM users WHERE id = ?', [req.user.user_id]);
const blk = stepUpBlock(u.totp_enabled, u.totp_secret, b.totp_code, ...);
if (blk) return res.status(blk.status).json(blk.body);
const ident = await resolveBotIdentity(req);
const r = await gateway.postGateway('/staking/fixed', { telegram_id: ident.id, ... });
```

The step-up is evaluated against the **caller's own row** — an attacker who has
enrolled no 2FA passes it trivially — and the action is then performed as the
**resolved identity**. Guard and action address different subjects, two lines
apart, on a money move.

**Severity is unchanged at CRITICAL and the fix is unchanged**; what was wrong
was my account of how it is reached, which made it sound like it needed bad luck.
It needed a signup. Both halves of the fix independently block it: `botAuth`
means the attacker cannot call the route at all, and the 409 means that even
holding the bot secret, a chat_id already on another row is refused.

## RC-2026-025 — the step-up and the action address different subjects

- **Status**: OPEN · **Severity**: MEDIUM (latent — see reachability)
- **Confidence**: CONFIRMED · **Fix class**: REVIEW_REQUIRED
- **File**: `app/routes/staking.js:55-66`, and the same shape wherever
  `stepUpBlock` precedes `resolveBotIdentity`
- **Standard**: CWE-863 (Incorrect Authorization); ASVS V4.2.1

Found while verifying the correction above, and reported separately because it
**survives the RC-2026-001 fix**. The 2FA check reads the caller's row; the
money move is executed as whatever identity `resolveBotIdentity` returns. Those
are the same subject only while nothing can put another account's `telegram_id`
on your row.

**Reachability: latent, not live.** With RC-2026-001 fixed, `telegram_id` is
written only by an authenticated bot-secret request that refuses an id already
on another row, and `idx_users_telegram_id` makes the collision impossible at
the storage layer too. So there is no path today. It is recorded because the
property the code depends on is stated nowhere near the code that depends on
it — the next route that writes `telegram_id`, or a migration that repairs rows
by hand, re-opens a 2FA bypass on a money path with nothing to catch it.

**Remediation**: read the step-up factors for the identity the action will be
performed as, not for `req.user.user_id` — or assert the two agree and refuse if
they do not. The second is cheaper and states the invariant out loud.

**Test**: plant a row whose `telegram_id` belongs to another account, with 2FA
disabled on the caller and enabled on the identity, and assert `/staking/fixed`
refuses. That test fails today and passes under either remediation.

---

# The release decision changed: NO-GO → CONDITIONAL GO

Not because anything was fixed. Because the adversarial second pass found that
the two findings holding the NO-GO were **rated CRITICAL on reasoning the
register itself had already refuted**, and three independent prosecutors per
finding, one per lens, said so without conferring.

## RC-2026-018 — the CRITICAL was incoherent, and this register carried the proof

Verifier 1 justified CRITICAL in one sentence:

> *"it corrupts every published backtest/scorecard number, which is CRITICAL
> rather than ship-stopping."*

Verifier 2 then proved that premise false: `--honest` forces
`fill_mode="next_open"` at `runner.py:546-548`, and every published path passes
`--honest` — `bot/api/lab.py:164`, `scripts/gen_agent_scorecards.py:70`,
`docs/FROZEN_BENCHMARK.md`, and all four `benchmark/scorecards/*.json` carry
`"honest": true`.

**I adopted verifier 2's fact and kept verifier 1's severity.** This file says,
two paragraphs below the severity line:

> *"So the **frozen benchmark and the marketplace scorecards are NOT
> affected.**"*

while `audit/runeclaw-audit.json` and §5 of the report both said:

> *"Every published backtest number rests on this."*

Two of my own artifacts, in direct contradiction, on the finding driving the
audit's verdict — and I repeated the false half in status updates more than
once. The severity was inherited from an argument nobody re-derived after its
premise was withdrawn.

**Now HIGH.** What is genuinely affected: real-data default-mode developer runs
(`run_realdata_backtest.py`, `backtest_realdata.py`, ad-hoc `runner`
invocations without `--honest`). What is not: the frozen benchmark, the
marketplace scorecards, the web Strategy Lab. `backtest_deep_results.json` is
100% synthetic GBM — `run_deep_backtest.py` builds all 500 runs from
`DataLoader.generate_synthetic`.

The defect is untouched and still real. Only the blast radius was overstated.

## The remediation was ALSO wrong, and that is the more useful finding

This register proposed:

> *"Add a test asserting every recorded entry price lies within some bar's
> `[low, high]` **at or after** the signal bar."*

A limit resting up to 1 ATR below the close is very often touched by *some*
later bar. **That test passes on the unfixed engine.** It asserts the price was
eventually plausible, not that it was tradeable when the fill was booked. The
assertion has to be against the range of the bar the fill was **booked on**.

Follow that remediation and you ship a non-fix with a green test vouching for
it — which is the defect class this entire audit is about, written by the
auditor, into the register, on the highest-severity finding.

## B4-03 — three prosecutors, none left it at CRITICAL

Two said HIGH, one MEDIUM. I took **HIGH**, the more conservative. The remedy
lens additionally rated its proposed remediation **HARMFUL**.

## What this verdict rests on, stated so it cannot be over-read

**CONDITIONAL GO is not "ready to ship".** It means: no unresolved BLOCKER or
CRITICAL, and **26 open HIGH findings**, each reported with a proposed patch
rather than fixed, by the agreed scope. The conditions are those patches.

**The second pass that produced this change is INCOMPLETE.** 13 of 56 targets
were lost to a session limit. The two blockers are complete — three prosecutors
each, all three reporting — so the specific adjudication behind this change is
whole. The wider pass is not, and a later target could raise something. This
verdict is therefore current, not final.

**Every severity the second pass moved, it moved DOWN** — eight of them. That is
a finding about the audit rather than about RUNECLAW: agents asked to find
defects rate them generously, and two adversarial verifiers correcting 84 of 162
severities still left a systematic upward bias. A reader should discount the
remaining severities accordingly, in that direction.

**The findings themselves held.** Across 24 prosecuted findings: zero refuted,
zero stale. The defects are real and none had been quietly fixed. What did not
hold was the severities (8 corrected, all down) and the remedies — **20 of 24
incomplete, three of them actively harmful**.

## RC-2026-026 — two different people share one bot-database row, so one reads the other's API keys

- **Status**: FIXED · **Severity**: **CRITICAL** · **Confidence**: CONFIRMED
  → **FIXED**: both doors refuse now -- `_ensure_local_user` and `ensure_settings_parent` -- keyed on `password_hash`, and all five call sites handle the refusal.
- **Category**: Improper access control (CWE-863, CWE-1270)
- **File**: `bot/skills/user_middleware.py:77-91`
- **Found**: while prosecuting the RC-2026-019 remedy. It is not a purge bug and
  does not need a purge to fire.

`_ensure_local_user(user_id, email, plan)` runs `SELECT id FROM users WHERE id = ?`
and **returns early if a row exists**, never checking that the row belongs to this
person. That `user_id` is the **website's MySQL id**. The same SQLite table also
holds rows created by `create_user`, which `AUTOINCREMENT`s from 1 behind
`POST /auth/register` — mounted at `api_bridge.py:366`. Both id spaces start at 1.

**Driven end to end, not reasoned:**

```
alice bot-native id: 1
row id 1 email     : alice@real.com
Bob reads llm_api_key: 'sk-ALICE-PRIVATE'
Bob reads news key   : ('cryptopanic', 'ALICE-NEWS-KEY')
```

Also reachable on that row: `user_portfolio` (equity, `trade_history`) and
`user_ingest_notes` (text the user pasted to their own agent).

**The condition, stated honestly.** A bot-native signup must land on an id a
website account also holds. `ensure_settings_parent` inserts telegram-id-keyed
rows (~10 digits), which drags `AUTOINCREMENT` up — so the window is bot-native
signups occurring *before* any large stub exists: the early life of a deployment,
when ids on both sides are small.

**The fix is not free.** Refusing to bind is the fail-closed direction and is
correct, but it denies bot features to any website user already comingled, so an
operator has to decide what happens to existing pairs. The discriminator is
measured and stable: a bot-native row carries a PBKDF2 hash, a stub carries `''`,
and a website-linked row always carries the literal
`website-linked:no-local-password` — nothing in the tree ever updates
`password_hash`.

## RC-2026-027 — `settings_user_id` is not injective: Unicode digits reach another user's row

- **Status**: FIXED · **Severity**: **HIGH** · **Confidence**: CONFIRMED
  → **FIXED**: `settings_user_id` is ASCII-only, rejects 0 and returns None instead of raising; `_WEB_ID_RE` carries `re.ASCII`.
- **Category**: Improper access control (CWE-289, CWE-178)
- **File**: `bot/db/models.py:397-409`

Executed against the real functions:

| input | maps to | same row as |
|---|---|---|
| `'12345'` | `12345` | — |
| `'١٢٣٤٥'` (Arabic-Indic) | `12345` | **`'12345'`** |
| `'１２３４５'` (fullwidth) | `12345` | **`'12345'`** |
| `'web:١٢'` | `-12` | **`'web:12'`** |
| `'0'` / `'web:0'` | `0` | **each other** |
| `'²'`, `'⁵'` | **raises `ValueError`** | — |

`str.isdigit()` is True for these and `int()` accepts them. The gateway's own gate
does not stop it: `_WEB_ID_RE = re.compile(r"^web:\d{1,20}$")` is a `str` pattern,
so its flags are `re.UNICODE` (32) and `\d` matches them —
`_is_web_id('web:١٢')` is `True`. That row holds `llm_api_key`.

Two further defects in the same function: `'0'` and `'web:0'` collide the two id
spaces at their boundary, and `'²'` / `'⁵'` are `isdigit()` but not `int()`-able,
so the function **raises** where its docstring promises `None` — 500ing the routes
that call it rather than rejecting cleanly.

Remediation: normalise and validate with an **ASCII-only** pattern, reject `0`, and
return `None` rather than raising. Not yet fixed; filed with the measurement.


---

# The adversarial second pass — 61 findings, 66 prosecutor reports

Brief Phase 15. Targets were chosen by what a wrong claim would cost, computed
from the artifact rather than picked: the findings driving the release decision,
the open HIGHs, and the 33 whose severity the two first-pass verifiers
**disagreed** about — where the finder's number stood by default rather than by
argument. The two blockers got three prosecutors each, one per lens.

The first pass asked *is this defect real*, three times over. This pass asked
three questions nobody had:

1. **Staleness** — is it still true of the tree today? Five PRs landed during
   the audit and at least three findings were fixed by them. One triage claim
   asserted a missing UNIQUE index the audit's own fix had already added.
2. **Remediation soundness** — would the proposed fix work, and is it worse than
   the defect?
3. **Severity honesty** — adjudicate, rather than inherit, a disputed number.

## Results

| verdict | count |
|---|---|
| STANDS | 30 |
| REMEDIATION_UNSOUND | 23 |
| SEVERITY_WRONG | 13 |

| remediation | count |
|---|---|
| INCOMPLETE | 49 |
| HARMFUL | 8 |
| SOUND | 8 |
| NOT_ASSESSED | 1 |

**61 of 66 reports carry executed evidence** — the prosecutors ran the code
rather than reading it.

### The findings held

**0 refuted. 0 stale.** Not one of the 61 had been quietly fixed by the
audit's own PRs, and not one fell over under a third adversarial read. The
finder-plus-two-verifiers pipeline produced claims that survive.

### The severities did not, and they failed in one direction

**18 severities moved. Every one moved DOWN**: B4-03, B4-20, B5-02, B5-05, B5-06, B5-11, B5-22, B6-05, B6-13, B6-38, M-07, RC-2026-005, RC-2026-008, RC-2026-010, RC-2026-015, RC-2026-018, RC-2026-021, RC-2026-025.

That is a finding about the audit, not about RUNECLAW. Agents asked to find
defects rate them generously; two adversarial verifiers corrected 84 of 162
severities and still left a systematic upward bias. **A reader should discount
the remaining severities in that direction.**

### The remedies did not, and that is the discovery

**52 of 61 proposed fixes are incomplete or harmful** (85%). Three are
actively **HARMFUL**: B4-03, B6-05, B6-13, M-07, RC-2026-009, RC-2026-013, RC-2026-015, RC-2026-025.

Every gate this audit ran — finder, two verifiers, the lead-auditor register
pass — asked whether the defect was real. **None asked whether the fix would
work.** So the register accumulated well-evidenced defects paired with cures
nobody had tested: one emits invalid Prometheus, one does not compile as
written, and RC-2026-018's acceptance test passes on the unfixed engine.

An audit that names real problems and prescribes broken cures is worth less than
it looks. That gap was invisible from inside the first pass, because the first
pass was not asking.
