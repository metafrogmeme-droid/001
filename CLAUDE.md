# Working on RUNECLAW

## Before you push, run one command

```bash
python3 scripts/preflight.py
```

It runs what CI runs, by **parsing `.github/workflows/ci.yml`** rather than
restating it — so it cannot drift, and a new CI step becomes a new preflight
step for free. Twenty-one gates: two strict ruff passes, the whole-tree ruff
ratchet, mypy on the money modules, the whole-tree mypy ratchet, the honesty
ratchet, bandit, pip-audit, the baseline test gate, the red team, the custody
red team, the web app's parse check, its npm advisory ratchet, its suite, the
marketing site's build, its npm advisory ratchet, its published-output honesty
tests, the check that the committed site is the built site, the Anchor
workspace's typecheck, its npm advisory ratchet, and guard reachability.
~14 minutes.

That "for free" is literal and has now been collected seven times: the app parse
gate and the npm ratchet were added to `ci.yml` for M3 and appeared in the local
plan with no change to `preflight.py`, the two red-team gates each did the
same, the marketing site's advisory ratchet did it again, the two lint/type
ratchets did it a sixth time, and the honesty ratchet a seventh. This
paragraph's own gate count is pinned by
`tests/test_claude_md_accuracy.py`, which failed the moment each of them landed
— including on the sentence you are reading, which said "Ten" until the risk
red team made it eleven, the custody one made it fifteen, the audit's
npm-coverage fix made it eighteen, its lint/type ratchets made it twenty, and
the honesty ratchet made it twenty-one.

**Two gates per tool, and the pairing is the point.** The strict steps are
FLOORS over a narrow scope — those rules, those directories, zero tolerance —
and they say nothing about anything outside it. `pyproject.toml` declared
`select = ["E","F","W","I"]` while CI ran a subset, so both strict steps passed
green against a tree the declared config scored at 1,361; `mypy` gated six
modules while the other 272 carried 390 unmeasured errors. `scripts/ruff_gate.py`
and `scripts/mypy_gate.py` cover the remainder as ratchets against
`tests/ruff_baseline.json` and `tests/mypy_baseline.json`: a rule may only go
DOWN, and a class that improves must be re-recorded in the same commit, same
rule as `known_failures.txt`. Neither backlog is swept, and both refusals are
deliberate — `I001` is an UNSAFE fix in a repo whose imports run `load_dotenv`
and the vault restore, and the `operator`/`union-attr` errors were sampled and
found to be mypy NARROWING false positives, where rewriting correct code to
satisfy the analyser buries real defects in cosmetic diff.

**"For free" covers a new STEP, not a new JOB.** `LOCAL_JOBS` is a deliberate
allow-list — token tooling is excluded because one of its steps curl-pipes a
Solana validator installer, and a preflight that installs a toolchain behind
your back is not a preflight. So `Marketing site (vite)` needed one line added
there, and a job that is added to `ci.yml` and not to that tuple runs in CI
while `--list` reports it under "NOT covered locally". That line is the honest
half of the design and worth reading before trusting a green preflight.

`Anchor workspace (node)` is the second job to need that line, and it is worth
saying why it qualifies where token tooling does not: it runs `tsc` and the
advisory ratchet, and neither installs anything beyond the lockfile. It
deliberately does **not** run `anchor test` — that needs a local validator,
which is the exact thing keeping token tooling out. The root `package.json` had
been installed by no job at all: five workspaces, four `npm ci`s, and a
2,277-line lockfile carrying six high advisories that nothing had ever printed.
`contracts/rune` and `site` were installed but never advisory-checked for the
same reason — only `token/` and `app/` ran the ratchet. All five do now.

```bash
python3 scripts/preflight.py --fast   # tight loop; drops only the network gates
python3 scripts/preflight.py --list   # show the plan, run nothing
```

A real run **clears every `__pycache__` first**, because CI checks out a fresh
tree and never has one. A `.pyc` is reused whenever the source's *(mtime,
size)* match what the cache recorded, and both are coarse — mtime is stored in
whole seconds, size says nothing about content. A mutation experiment that
swapped `r.get("max_margin")` for `r.get("margin_cap")` and put it back 260 ms
later changed neither, so three tests failed against source byte-identical to
the commit CI had just passed green: `git diff` clean, `inspect.getsource`
right, and `margin` still `None` after `margin = r.get("max_margin")` ran on a
dict that had that key. **Mutation testing in Python can poison the tree in a
way `git status` cannot see** — clear the cache between mutations, not just the
source. The failing direction is the cheap one; a stale cache can as easily
hold bytecode that *passes*, which is a preflight answering "will CI pass" from
code CI will never load.

It ends by naming the jobs it could **not** run (cargo, solidity, gitleaks,
token tooling). Those still need CI.

> This exists because "pytest passes" was standing in for "CI will pass" while
> covering a fraction of it — a push failed on a ruff rule that had never been
> run locally. Running a subset and reporting it as the whole is the defect
> this repo spends most of its guard tests preventing; don't reintroduce it in
> the dev loop.

**Do not** substitute a bare `pytest`. The suite runs through
`scripts/ci_test_gate.py`, which enforces `tests/known_failures.txt` — a
baseline entry that starts *passing* is a hard failure, so stale entries
cannot hide real bugs.

**The flake filter is the one part of that gate that can hide one, and it did
— for 40 tests at once.** `ci_test_gate` re-runs each new failure alone and
files the ones that pass as order-dependent. That is the right call for a
genuinely time-sensitive test and it is indistinguishable, from the gate's
side, from a suite-wide state leak. `tests/test_vault_keeps_what_it_cannot_read.py`
called `store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})` to prove the recovery
path does not erase the vault — which it does prove — and `store_secrets`
writes into `os.environ` deliberately ("recovery of the running process"),
outside monkeypatch's bookkeeping, so the value outlived its test. Every later
test that plants a gateway secret does it by monkeypatching the module
attribute, and `_secret()` reads the environment FIRST. **40 of
`test_web_gateway.py`'s 48 failed 403 in a full run and all 48 passed alone**:
confirm, the live-mode gate, the portfolio snapshot, authority
apply/revoke/enforce — the money-facing HTTP surface, in CI, gating nothing.
The gate reported success, each test passed individually, and the only
artefact was a count of "flaky" nobody reads.

Three things worth keeping from it. **A leak is invisible from any single
run's verdict** — it was found by watching `os.environ` after every test in
one full session and printing the first change, which named the writer in 12
minutes where bisecting would have taken hours. **The containment belongs in
the harness, not in a rule each test remembers**: `tests/conftest.py` hands
the vault-managed keys back after every test, sibling to the fixture that
does the same for the analyzer's lookahead flags, and for the same stated
reason — the leaking test tested exactly what it meant to. And **a mutation
that renames an `autouse` fixture is a no-op**: autouse binds on the decorator,
not the name, so the first attempt to prove the guard worked passed against a
fixture that was still running. `autouse=False` is the mutation that bites.

## The rule behind most of the tests here

**Unreadable is never zero, and absent is never a measurement.**

A failed read must not render as an empty result, a `0.00%`, or a confident
negative. It has bitten in most surfaces at least once — a 503 shown as
"No venues found", a dead SSE stream shown as "Engine live", an unfetchable
price shown as `+0.00%` beside a green stripe. Two honest strategies:

| | shape | right for |
|---|---|---|
| **guard** | throw / `mustRead()` so the caller paints an error state | a single-source panel |
| **omit** | catch each source individually and leave missing ones out | a composite view where one dead source must not blank the rest |

Never neither. `app/test/panel_failure_honesty.test.js` enforces this
structurally across every `renderPanel` loader.

Corollaries that come up constantly:
- **Colour is a claim.** A green accent says "in profit" as loudly as the
  number does. Unknown gets a muted one.
- **A heuristic is never a verdict.** A green health check rules *one* cause
  out; it does not name the cause.
- **Test `is None`, not falsiness.** `0.0` is falsy and `0.0` is a real,
  measured, break-even position.

### Knowing the rule has not been enough

Everything above was already written here on 2026-07-31, and that day the
same rule was broken in twenty-plus places across ten PRs — win rates,
an edge-metrics panel whose own comment promised "nothing is invented", a
public track record that published `12 (7W/4L)`. A principle is not
searchable. The **shapes** it takes are, so here they are:

| shape | what it silently asserts |
|---|---|
| `parseFloat(x) \|\| 0` · `float(x or 0)` | unreadable is break-even |
| `(x \|\| 0) >= 0` | unreadable **won** (`0 >= 0` is true) |
| `(x or 0) > 0` | unreadable **lost** |
| `losses = len(all) - wins` | unscorable rows are losses |
| `.get("pnl", 0)` · `getattr(o, "pnl", 0)` | absent field is zero |
| `sum(...)` over a set that includes unreadable rows | a partial total, printed as whole |
| `if total != 0:` guarding a display | all-missing and genuinely-flat hidden alike |
| `isConfigured()` rendered as "encrypted at rest" | a config flag is the state of every stored row |

Two practices found these; the rule alone found none of them.

**The third practice is a ratchet, and the table above is its rule set.**
Reading every diff and auditing the previous PR both work and neither scales.
`scripts/honesty_gate.py` parses `bot/` and `scripts/` and counts five of those
eight shapes per file, against `tests/honesty_baseline.json` — a two-way
ratchet on 775 hits, same rule as `known_failures.txt`. It claims exactly one
thing: **these shapes did not increase.** A hit is a place to LOOK, and most of
them are not defects, which is the whole reason they are recorded rather than
swept: `patterns.py` computes a rate `if completed else 0` two lines under
`if not completed: continue`; `sum(t.pnl or 0 ...)` over rows already filtered
`is not None` changes `0.0` into `0` and nothing else. *Check reachability
before fixing* applies to a gate's output like anything else.

Its first run bought three that were real, all one field:

- **The cooldown that stops the bot after a live loss** filtered on
  `(t.pnl_usd or 0) < 0`. `0 < 0` is False, so a close nobody could price was
  read as *not a loss* and the engine went straight back to sizing the next
  entry — on the one trade it understood least. `loss_cooldown_reason()` is
  the seam (there was none: a comprehension inside a `try` inside a message
  loop), it has three outcomes, and the unpriced reason quotes no dollar figure.
- **Both website wires** then turned the same close into `$0.00`. The producer
  said `d["pnl"] = pos.pnl_usd or 0` six lines above its own comment about
  sending `None` for unavailable equity; the wire said
  `float(_attr(t, "pnl", 0))`, twice. Every other party was already honest —
  `live_executor` writes `None` deliberately, `trades.pnl` is nullable,
  `sync.js` inserts it raw, and `winStats` counts unpriced closes as a fourth
  outcome with `rate: null`. Three lines in the middle made sure it never saw
  one.

The wire is also why the gate knows more than two spellings. Its first version
looked for `.get(k, 0)` and `getattr(o, k, 0)` and found neither, because
`_attr` is a project-local accessor — so the single most expensive instance in
the tree was invisible to the gate written to find it. Any call ending
`(..., "<measurement>", 0)` counts now.

Two things it deliberately does not do, stated because a gate whose coverage is
overstated is the failure this file exists to prevent. It is **Python only** —
the JS half of every shape above is not checked here — and it skips `tests/`,
because a test PLANTS these shapes to prove the code rejects them. The three
remaining shapes (a partial `sum`, an `if total != 0:` guarding a display, a
config flag read as the state of stored rows) are semantic and stay a reading
job. Its rule set is fingerprinted into the baseline: widen the vocabulary and
the counts stop being comparable, so it reports CANNOT CHECK rather than
manufacturing growth — the same trap `ruff_gate.check_version` documents, where
a baseline recorded under mypy 1.19.1 and checked under 1.15.0 named eleven
grown classes and not one was a code change.

**"Python only" was a stated hole, and something walked through it.** That
gate's own coverage section says the JS half of every shape above is not
checked, and PR #314's third surface was `dashboard.js` — found by a human
deciding to sweep, which is the practice `honesty_gate.py` exists because it
does not scale. `app/test/js_honesty_ratchet.test.js` is the other half (161
hits, four shapes, same two-way rule), and it rides `npm test`, so it reached
CI and preflight with no new job and no change to the gate count.

**The shape is JS-specific and Python could not host the rule.** `None > 0`
RAISES in Python — loud. In JS the two ways of being absent disagree with each
other: `undefined >= 0` is false and `null >= 0` is **true**, so
`pnl >= 0 ? 'up' : 'down'` renders one absent value as a loss and the other as
a win, from one expression. It is not an `|| 0`, so no vocabulary of or-zero
spellings would ever have found it.

**Building it found the deeper cause, and it was in the Python gate too.** The
new gate did not catch `g.net_r > 0 ? …` on its first run, and the reason was
not the pattern: `net_r` splits to `['net','r']` and **neither word was in the
measurement vocabulary**, on a repo whose entire shadow book is denominated in
R. `honesty_gate.py` had the same blind spot and always had. The vocabulary is
`tests/honesty_vocabulary.json` now — one file, both gates, both hashing it
into their own baseline — because a second copy of a threshold is a second
answer, which is the rule `winrate-bar.js` states about `MIN_RATED`. Widening
it by one word (`net`) and one substring (`rmultiple`) tripped both
fingerprints to CANNOT CHECK, exactly as designed.

**What the widening bought was an R-multiple computed from the wrong
denominator.** `trade_journal.record_trade` did
`initial_risk = abs(entry_price - stop_loss)` and both live callers hand it
`float(getattr(pos, "stop_loss", 0) or 0)` — absent-is-zero, on the one field
the calculation is a ratio *against*. A close with no recorded stop (an
ORPHAN, so precisely the kind whose stop cannot be read) gave
`abs(entry - 0) == entry` and therefore `r_multiple = pnl / entry_price`:
**not an R at all**, a different quantity in the right units, printed on the
weekly review as `Avg R-Multiple: +0.02R`. The `else 0` branch was the quieter
half — both prices unreadable gives `abs(0-0) == 0`, and 0R is a real outcome,
so an unmeasurable close was indistinguishable from a measured break-even.
`r_multiple_for` answers `None` for both, `average_r` carries `scored`/`total`
beside the mean, and the card says *"R unknown — no stop on record"* rather
than formatting one.

**The same shape on the return itself, and what it omitted was a CONSTANT.**
`close_pct` builds `pnl_pct_margin` from two prices and a leverage, so it is
the GROSS return on margin, and four surfaces rendered it as *the* return — the
public channel, the share sheet, the share button's win/lose label, and the PNG
close card. Fees are a fraction of NOTIONAL and notional is margin × leverage,
so `fees / margin = 2 × fee_pct × leverage`: **1.2% of margin at 10× and 2.4%
at 20×**, before the position has done anything, and always in the flattering
direction — a win prints larger than it was and a loss smaller. **Two fixtures
already in the suite were self-consistent proofs and nobody read them that
way**: TRIA published `+26.62%` where `$1.89 / $7.44` is `+25.40%`, TAO
published `+9.47%` where `$0.15 / $1.88` is `+7.98%`, each gap being exactly
its own fees. The costliest reader was `_is_win`, which fell back from the
gross figure to the raw price move — *both* price-derived — so at 20× a
`+0.10%` move (+2.0% gross, −0.4% net) put **"📣 Share this win"** on a losing
trade. `realized_margin_return_pct` is the reading, and every publishing
surface abstains when the margin was never recorded rather than falling back to
the gross one.

**And the margin it divides by had two meanings.** `size_usd` was
`cost_usd if cost_usd > 0 else entry_price * quantity` — margin, or the
NOTIONAL, twenty times larger at 20×, under a name that says neither, selected
by the falsy shape where `0.0` means the venue never told us. `portfolio_return`
documents the key as margin; `tax.js`, `intel.js` and `replay.js` each document
it as notional. `position_size_basis` answers both separately and refuses to
derive one from `pos.leverage` — the field these very records exist to document
as unreliable. Its sharpest consumer was three lines of a single round trip on
two bases, under a comment naming the right one: `entry_fee` and `funding` on
the margin, `exit_fee` on the notional, at a twentieth of what was paid, all
feeding the `net_pnl` a card prints. The guard over it was a **grep for a
literal that was present and correct the whole time** — a scan cannot see which
quantity a name holds, which was the entire defect.

**A gate that scolds the cure teaches the wrong thing.** The first draft of
`bare-compare-verdict` flagged 104 lines and most were
`size > 0 ? pnl / size : null` — the honest guard, the shape the whole gate
exists to encourage. It counts a comparison only when BOTH branches assert a
verdict: one branch abstaining (`null`, `undefined`, `''`) is the fix, and two
bare numbers are a precision choice, not a claim.

> **And I grepped a name I had invented, again.** A sweep for `log_trade`
> returned zero callers and very nearly went into a PR body as "the journal is
> never written". The method is `record_trade`, and it has two live call
> sites. That is the `_infer_close_price` lesson, repeated in the same session
> that wrote it into this file. Grep the definitions (`^\s*def `) and count
> callers of THOSE; a name you remember is not a measurement of anything.

**A config flag is not a measurement of what is already stored — and the flip
that makes it true is the one that hides what it did not fix.** With
`WEB_CREDS_KEY` unset, `config_audit` warned "new 2FA secrets are stored
unencrypted". Setting it stopped that warning, flipped `secretsAreSealed()` to
true, and re-sealed **zero** rows: `/2fa/enable` migrates the row it enables and
refuses to run for an account that is already enabled, so every seed enrolled
before that deploy had no path to encryption and no surface that said so. The
deployment answer is right at exactly one moment — the instant a row is written
— which is why `/2fa/setup` may use it and `/2fa/status` may not.
`totp.secretIsSealed()` is the row reading (shape, not readability: a row sealed
to a rotated-away key is still encrypted at rest). `app/lib/totp_seed_audit.js`
counts four outcomes, because three of them look alike from a distance — sealed,
plaintext, **unreadable** (an envelope the current key will not open, so those
accounts fail their next second-factor check while the row looks healthy), and
absent — and a query that fails reports `could not be checked`, never zero.
`app/scripts/reseal_totp_secrets.js` is the backfill: dry run by default,
compare-and-swap on the value it read, and the seal verified to open back to the
seed before anything is written. Its guards each needed a `db` seam to be driven
at all; the first draft had one that a mutation could flip with the suite still
green, because `classify` had already ruled its case out.

Two gates were narrower than the claims read off them, both one directory
short: CI's app parse step compiled `lib/` and `routes/` but not `scripts/`,
and `totp_secret_at_rest.test.js` — the guard that every reader of the column
opens it — swept the same two and skipped the one caller that *writes* the
column for every enrolled account at once.

**The same shape, on the exchange keys, was already written down and not
followed up.** `test_unreadable_credentials_are_not_reported_present.py` fixed
the LLM key with exactly that design — `llm_key_state()` three-valued, the
decrypt refusing to hand back ciphertext — and its own docstring names the
trigger as "the same event this repo already logs for **the exchange vault**".
The exchange vault was not then checked, and it holds the keys that move real
money. `ExchangeCredentialStore.get()` answered `None` for "never connected"
and for "stored but undecryptable" alike, saying so in its docstring ("the
caller treats that as 'not connected'"), while `has()` and `list_venues()` read
the record map without decrypting and answered CONNECTED for that same user —
so `/exchange` printed `Status: connected` above an **empty** `Key:` line while
the engine's live gate told the same user "no linked Bitget account". Routing
asked `list_venues()`, a presence test, so a venue whose keys stopped
decrypting could never reach `dropped` — defeating a property
`venue_selection`'s own docstring promises in as many words, with the whole
reporting path already built. And a FAIL-CLOSED web live gate was satisfied by
`has()`, from two callers. `credential_state()` (readable/unreadable/absent)
and `readable_venues()` are the readings now, both derived from one
`_decrypt_fields` so no two answers can drift. It is not exotic:
`_load_or_create_master_key` GENERATES a new key when `RUNECLAW_SECRETS_KEY` is
unset and the data dir was wiped, and its own warning says so.

**And the third store, sharing that same key, was DESTROYING what it could not
read.** `secrets_vault._load_vault` dropped every entry `decrypt` refused and
both write paths then saved the map wholesale — `store_secrets` (reached by
`/setexchange`, `/setgateway`, `/setllm`, which is what an operator runs
*because* a secret went missing) and `seed_and_restore` (runs at boot, saves
whenever any managed env value differs, which after a key change is all of
them). One boot erased the lot, permanently, from a file with no `.bak`.
`exchange_credentials._load` was hardened against that exact thing and says so
in capitals; the vault never got the equivalent. **The fix is not the same
shape, and that matters**: there the whole FILE failed to parse, so a
`_load_failed` flag blocking every save was right; here the file parses and
individual ENTRIES fail, so blocking the save would take the vault offline over
one stale key. `_load_vault` returns `(readable, opaque)` and `_save_vault`
writes the un-openable ciphertext back verbatim — a re-entered value taking
precedence, or the fix would be undoable — so readable entries keep working and
unreadable ones stay recoverable. `/vault` has a fourth bucket saying which,
because "env-only, mirrored on next boot" is a promise about a copy already
sitting there. The Node half (`app/lib/secrets_vault.js`) reads the same file
with the same key and was left alone on purpose: it has no write path, so it
cannot erase, and it claims nothing it has not restored.

**And the KEY those three stores share was named by four surfaces and read by
none.** `/vault` printed "Fernet under the master key (`RUNECLAW_SECRETS_KEY` /
`data/.exchange_secret.key`)" as though those were one thing, on the command
whose own docstring says it "is how you verify nothing is left unprotected".
They are not one thing, and the difference is the entire durability story: with
the variable set the key lives in two places and either rebuilds the other;
without it that file is the only copy and a wiped `data/` loses every linked
account, the vault and the `llm_api_key` column, permanently. The boot
preflight's undecryptable-accounts alert had the same shape — "a wiped data dir
with `RUNECLAW_SECRETS_KEY` unset does it" is a *hypothesis*, offered at the
moment the operator most needs the fact, on a box where the fact is one file
read away. `master_key_state()` is that read (`pinned` / `file_only` /
`diverged` / `absent` / `unreadable`, with a fingerprint and never the key), and
all three surfaces derive from it so no two can drift.

**A secret can be encrypted at rest and still have only a plaintext door.**
`WEB3_SIGNER_PRIVATE_KEY` — the key that signs on-chain transactions — has
been in `secrets_vault._DEFAULT_MANAGED` since the WEB3-LIVE-EXEC slice, so
the vault protects it *once it holds it*. The intake was the hole: the only
route in was to write the key in the clear into `.env` and wait for a boot to
mirror it, and `/vault`'s own footnote says what that costs — "a key that only
ever came from `.env` stays in the clear there". Every other managed secret
had `/setexchange`, `/setgateway` or `/setllm`; the most sensitive one printed
`→ .env` on the card that exists to say what is unprotected. Coverage of a
STORE is not coverage of the PATH INTO it.

`/setsigner` closes it, and the interesting half is the confirmation. **A
private key cannot be echoed back**, so a typo in a 64-character paste is
invisible until something is signed by an account the operator did not mean —
`check_signing_key` derives the ADDRESS and the card shows that, the one thing
safe to print and the only thing that answers "did I paste the right key?".
Three outcomes, not two, because `eth-account` is optional and is NOT
installed in CI: confirmed, **well-formed but unconfirmed** (stored, and the
card says plainly that nothing checked which account it controls), and
rejected. The middle one dressed as the first is the failure this whole file
is about. Validation is arithmetic rather than a library call — 64 hex chars
and a scalar in `[1, n-1]` — so it works with no crypto installed, and it
catches the two pastes a length check does not: all-zeros, and a value at or
above the curve order.

Every rejection quotes **no part of the input**, including the one that comes
from the signing library's own exception. A parser's error message is one of
the few places key material genuinely escapes, and the mutation that made it
`f"rejected: {raw}"` is in round 18.

**A second copy of a map is a second answer, and this one was written three
times.** `bot/llm/provider.py` holds `_PROVIDER_KEY_ENV` — which env var
carries which provider's key, eleven rows. `set_provider` had a local copy of
seven of them, and its own comment records the cost: a client built for Grok,
Mistral, OpenRouter or Together came up keyless with its key sitting in the
environment. That copy was consolidated. Two more were not. `/setllm` carried a
hand-written **ten** of the eleven, missing `grok`, so `/setllm grok <key>`
switched the provider, answered "LLM provider updated" and stored **nothing** —
under a help text promising the key is "stored ENCRYPTED in the operator vault
… survive restarts and redeploys". Free-user chat routes to Grok and falls back
"if `XAI_API_KEY` is unset", so it fell back on every restart, and `/vault`
reported the key missing however many times the operator set it.

**The third copy was a DERIVATION, which is the hardest kind to see.**
`shadow_eval` built the name as `f"{PROVIDER}_API_KEY"`. That is correct for
ten of the eleven providers — and wrong for `grok`, whose key lives in
`XAI_API_KEY`, so shadow eval read a variable that does not exist and logged
"could not build client" with the key in the process. A convention that holds
ten times in eleven is *why* nobody checks the eleventh. `provider_key_env()`
and `settable_key_envs()` are the door now, and
`tests/test_vault_hints_name_a_command_that_works.py` pins that the derivation
misses exactly one provider, so a rename cannot quietly restore the trap.

**A card that names a command is claiming the command does something.**
`/vault` prints, per managed secret, what to run to protect it — its docstring
says it "is how you verify nothing is left unprotected" — and it picked that by
asking whether the NAME ends `_API_KEY`. That is a guess about what a key is
FOR, not a reading of what any command DOES, and four keys took the route:
`ONCHAIN_API_KEY` (a Glassnode/Arkham/Nansen key), `HYPERLIQUID_API_KEY` (an
exchange key), `LLM_API_KEY` (the generic default no invocation writes), and
`XAI_API_KEY` (above). Membership in `settable_key_envs()` is the reading. The
loudest tell was there all along: `HYPERLIQUID_API_KEY` said `/setllm` while
`HYPERLIQUID_API_SECRET` said `.env` — two halves of one credential, two
instructions, one card. `vault_fix_hint` is module-level because there was no
seam; the map was nested in a 150-line method, so nothing could read the
instruction an operator is given, and `/setsigner`'s own test grepped that
method for the map rather than asking it.

**And the vault protected two names read nowhere while missing the one that
signs.** `venues.py` and `config.py` both say operator Hyperliquid needs
`HYPERLIQUID_WALLET_ADDRESS` + `HYPERLIQUID_PRIVATE_KEY`, and
`has_operator_credentials` reads exactly those. `_DEFAULT_MANAGED` held
`HYPERLIQUID_API_KEY` and `_API_SECRET` — grep the tree, nothing reads either —
plus the address, and **not** the agent-wallet private key that signs live
perps orders. Same shape as `WEB3_SIGNER_PRIVATE_KEY` one venue over and
quieter: a wiped `.env` restored the address, the gate went False, and the
venue simply stopped trading with the card showing nothing missing. Coverage of
a STORE is not coverage of the KEYS IN IT — check the list against the readers,
not against itself.

**Suppression needs the same three values as everything else.** All those keys
were hidden from `Missing` by prefix, correctly: a venue nobody uses is not a
gap. But half a venue is not an unused venue — it is the state that breaks — so
`optional_venue_absences` reports a key only when a sibling that makes the
venue usable is already stored. Absent alone is not a measurement; absent
beside a configured sibling is.

**A warning that fires once is not a surface.** `_load_or_create_master_key`
logs "set RUNECLAW_SECRETS_KEY for production" on the boot that GENERATES the
key, and every boot after that takes `if p.exists(): return p.read_bytes()` in
silence. The condition persists; the only thing that reported it does not — so a
box one `rm -rf data/` from losing everything says so once, in a container log,
months ago. `_master_key_preflight` runs on every boot and is quiet only when
the state is genuinely healthy, because a warning that fires when nothing is
wrong is how operators learn to skip the next one (`boot_health.py` records that
lesson about `WEB_CREDS_KEY` and it is why the master key did NOT go in
`IMPORTANT_ENV`: unset is a durability condition, not a broken surface).

**The vault's rule applies to the key that reads the vault.** With
`RUNECLAW_SECRETS_KEY` set to something the file does not match, the loader
overwrote the file — destroying the only copy of the key that still opened the
data, with no `.bak`, from a typo or a stale compose file. That is the sentence
already in this document one store up. It keeps a `0600` backup now and refuses
to overwrite when the backup fails, which is the case that matters.

**Driving the states is what found the defect in the fix.** `diverged` is
transient: the loader replaces the file on the first boot that sees it, after
which the reading says `pinned` — the healthiest word available — at the exact
moment every existing ciphertext stopped opening. The `.bak` is the durable
trace, so `prior_backup` rides on the reading in *every* state and prints on the
card, because the operator who needs it is the one who does not know it exists.

**Ask which OTHER surface makes the same claim — before calling the fix
done.** Five of those ten PRs came from auditing the previous one. `/portfolio`
still had the defect `/open_positions` had just been cured of. A `theater.js`
value flowed through three renderings and fixing two left the third.

**A fix that lands in the assessor and not the renderer has not landed.**
`assess_readiness` added `decisions_on_record` precisely so three disagreeing
denominators would stop reading as one, with a comment naming the live
`6 / 17 / 61` card that caused it — and `render_report` went on printing
`resolved_samples`, the calibrator's own subset. A later live card headed
itself "Resolved outcomes: 23" above a component claiming 46 unseen trades and
another counting 168. The guard that shipped with the fix asserts the KEY IS IN
THE DICT, which is one step short of the surface anyone reads.

**Enumerate the combinations, not the happy ones.** The same card printed
`⏳ calibration: ACCUMULATING (23/30)` with `AUTO_CONFIRM_USE_CALIBRATED — ON`
directly beneath and recommended nothing, because both branches keyed on
`READY` (ready-and-unapplied → "consider enabling"; ready-and-applied →
"validated ✓"). Applied-and-NOT-validated — the one combination of the four
that means something is already wrong — had no branch at all, on the report
whose header says it answers *the question the operator has to answer before
flipping*. `recommendations_for()` is the seam now, because the rule needed a
store, a fitted calibrator and a config to reach, so a test of it either did
not exist or reimplemented it. Both had happened. And a bare `— ON` beside a
state the reader has skimmed past is itself a claim: it reads as approval of
the exact thing that has not been approved.

**A bar cleared by two points is cleared by noise, and the count beside it has
to be the rate's own.** The next live card off that report read
`✅ voter_weights: READY — 62% of 34 voter(s) held direction on 46 unseen
trade(s) (bar 60%)` and recommended turning the flag on. 62% of 34 is 21 of 34,
which a fair coin reaches about one time in ten. The two sample floors added
previously bound the SAMPLE and say nothing about the MARGIN, which is why that
card passed both: 46 trades and 34 voters is a real sample, and 21 of 34 is
still a coin flip. `wilson_lower_bound` is twenty lines of arithmetic and no
new dependency, and READY now needs the whole 95% interval above chance, not
its top end. Underneath it the usual defect: `hold_rate` is holds/JUDGED and
the card printed `len(voters)` — a fraction over one population beside a count
of another, so no reader could recover the 21 the significance turns on.
`n_judged` and `n_holds` travel with the rate now. **`setup_expectancy` makes
the same claim and is NOT the same defect** — `nudge = (wr - 0.5) * 2 *
max_nudge * shrink`, and `shrink = n / (n + shrinkage)` already pulls a thin
sample toward zero, so a weak reading produces a weak nudge instead of a
verdict. Check reachability before fixing, in the corollary sweep too.

**The same bar, on a TOTAL, in a second module — and the sweep for it found a
third surface.** The nightly self-audit's card read `MTF_ALIGNMENT is the
costliest gate (net +4.1R over 97 blocked trades)` off `net_r > 0.5`, and +4.1R
over 97 is **+0.042R per trade**: the 95% interval is (-0.25, +0.34). Same
defect as the voter card, on the scoreboard whose next step is loosening a risk
gate on a live account. The instrument is NOT Wilson and that is the point — R
is a continuous signed magnitude, not a proportion, so `mean_r_interval` is the
normal interval on the per-trade mean and only the DISCIPLINE is shared (the
whole interval clear of the null). `MIN_GATE_TRADES` sits beside it because
three blocked trades that all took profit at exactly +1.8R have a sample sd of
**zero**, hence a lower bound of +1.8R, from three trades — the interval alone
would call that certainty.

Two more fell out of it. `gate_report`'s verdict rides on the row, so the
Telegram scoreboard, the card and the LLM's evidence blob cannot disagree; the
`/shadow` icon was making the same claim in colour off `net_r > 0.5`, and
**`app/public/js/dashboard.js` was making it with no threshold at all** —
`g.net_r > 0 ? 'neg' : 'pos'`, whose else-branch catches a measured zero AND a
missing field (`undefined > 0` is false), painting both green on a panel whose
caption reads green as *saved you money*. Two rows of the shapes table in one
ternary. `RCShadowGates` reads the server's verdict rather than recomputing a
bar, for the reason `RCWinRate` gives about `MIN_RATED`: a second copy of a
threshold is a second answer.

**And the sentence under it was the else-branch of `if not results:`.** The
same card printed *"No changes proposed — the evidence supports the current
configuration"* directly beneath `40 closes · win 28% · PF 0.4 · net $-26.86` —
a verdict on evidence the function never consulted, twenty lines under the
summary it would have read. `window_reading()` is that reading and
`no_change_verdict()` the sentence, with four outcomes because *proposed
nothing*, *could not read the reply*, *proposed things I then rejected* and
*the record is too thin to say* are different events and only one is a pass.
The second of those was `parse_llm_json` returning `[]` **"when unparseable" by
its own docstring** — so a truncated reply, a refusal or a page of prose became
an endorsement of a live trading config. It answers `None` now.

**The honesty gate caught two defects in the commit that fixed them, in the
renderer written to stop that exact reading.** `_gate_stat`'s first draft was
`float(g.get('net_r') or 0)` and a raw `{g.get('n')}` — an absent total
printing `net +0.0R`, a measured break-even, and an absent count printing `over
None blocked trades`. Knowing the rule is not enough *while writing the fix for
it* either; the ratchet is, and both were fixed rather than re-recorded.

**A component that can never become ready is a slot on the card, not a
learner.** `setup_expectancy` keyed on `(symbol, regime, direction)` with a
10-trade floor, and after 168 trades the card had said `0 setup(s) at/above
10-trade threshold` for months: 105 setups over 168 trades is 1.6 trades each,
and no amount of trading fixes a key space that sparse. It backs off now —
symbol, then regime, then direction — with the tier on the `Nudge` and a weight
per tier, because a regime-level record applied to a symbol with no record of
its own is a wider claim than the module's name suggests. That claim is what
`SETUP_EXPECTANCY_BACKOFF_ENABLED` gates (default off, shadow first), and
`may_apply()` is the seam so the analyzer and the readiness card cannot
disagree about what is switched on. `lookup()` deliberately does NOT back off:
something still has to be able to ask the narrow question.

**Write the assertion, then re-run the search.** Three separate times the
source test written for the known sites failed on sites the original grep
could not reach — they used `t.pnl`, `getattr(t, 'net_pnl', 0)`, a streak
helper. No search for `pnl_usd` was ever going to find them. The grep tells
you where you looked; the test tells you where you didn't.

**Then check reachability before fixing.** Not every match is a defect, and a
refactor bought with no safety is a real cost: `track.js` filters on
`isFinite` upstream, `arena_trades.pnl` is `NOT NULL`, and the paper `Trade`
sets `pnl` and `closed_at` in one atomic `model_copy`. All three look exactly
like the bug. None of them are. `tests/test_paper_pnl_default_is_safe.py`
pins the third rather than refactoring twenty call sites to fix nothing.

**And check the NAME before calling something dead.** A sweep for
`_infer_close_price` returned zero callers and the function was written up
twice, and in a merged PR body, as ~100 lines of dead code to delete. The
function is `_get_actual_close_price`; `exchange_sync.py:342` calls it. Zero
hits on a name that does not exist is not a measurement of anything — the same
"a checker with a blind spot manufactures exactly the accusation it exists to
prevent" failure the reachability sweep documents, done by hand. Grep for the
definition, not for the name you remember.

**It was not dead, it was wrong, and the wrongness was the expensive kind.**
Its step 4 answered `trade.entry_price` when no fill matched AND no ticker
could be read, so `_calc_pnl` came out at exactly `0.00` and a position that
vanished from the venue — liquidated, stopped out, or closed in profit —
entered the permanent record as a MEASURED break-even. `0.00` is the one
answer that can be ruled out: a close at the entry is precisely what "no data"
was standing in for. The damage ran past the record, because
`close_position` hands the P&L to `_on_trade_close` →
`_realized_pnl_window`, and **two tighten-only size controls read that window
and were pushed in opposite wrong directions**: the live-performance governor
counts `p > 0` over `len(recent)`, so a fabricated `0.0` is not a win but is in
the denominator and drags the win rate down; the equity throttle's
`rolling_profit_factor` sees a value that adds to neither gross profit nor
gross loss, yet it still counts toward `equity_throttle_min_samples` — an
evidence floor satisfied by a non-measurement. The consecutive-loss streak was
the one consumer already written for it (`# C2-09 FIX: pnl == 0.0 (breakeven)
— no change to streak`).

**The fix is to defer, not to guess, and the asymmetry decides it.** Step 4
answers `None` and the sweep leaves the position tracked. "Open" is not true
either — it did close — but it is the RECOVERABLE falsehood: the sweep runs
again, a readable ticker books it at a real price, and local SL/TP monitoring
keeps running meanwhile. A break-even written into the trade record and the
risk windows is permanent. That is `live_executor`'s flatten argument
("keeping a position that DID close is recoverable; booking a close that did
NOT happen is not") pointed the other way. It did **not** need `Trade.pnl` to
become Optional — `test_paper_pnl_default_is_safe.py` argues correctly against
that, and its grounds cover the *default* rather than an invented price, so
the two never conflicted. The whole chain had no test of any kind before this.

## Public-surface rules

No dollar amounts on public, community, leaderboard or marketplace payloads —
percent, ratio and count only. Private per-user surfaces may show dollars.
Market prices, volume, OI and gas are public market facts and are fine.
Several suites pin this (`app/test/mcp_public_records.test.js`,
`app/test/dashboard_social.test.js`, and others).

Never put secrets, API keys, private keys or internal config into user-facing
text, logs, or the repo. `/readyz` returns a coarse reason code from a fixed
vocabulary for exactly this reason — driver messages never reach it.

## Verifying a deploy

`/api/version` carries two content hashes, computed by `app/lib/version.js`.
The pair is the diagnosis:

| `build` | `assets` | means |
|---|---|---|
| moved | moved | full deploy landed |
| moved | unchanged | server-only change |
| unchanged | moved | client-only change |
| unchanged | unchanged | **nothing deployed**, whatever the log says |

```bash
node -e "const v=require('./app/lib/version').buildInfo(); console.log(v.build, v.assets)"
```

That prints what *should* be live. `scripts/verify_deploy.sh` compares it to
what *is* live, on **both** deploy targets:

```bash
scripts/verify_deploy.sh              # web container + bot box
scripts/verify_deploy.sh --web-only   # after a web republish
WEB_URL=https://host scripts/verify_deploy.sh --web-only
```

The two-target part is the point. On 2026-08-25 a deploy pulled the right
commit onto the bot box, passed `verify_deploy_source.sh`, restarted cleanly,
and reported success — while sign-in stayed broken all day, because the fix was
in `app/lib/siwf.js` and **the bot box never serves `app/`**. A checker that
asks about one half cannot report the other.

Three outcomes, not two: `0` verified, `1` a real mismatch, **`3` could not be
checked**. The header says why — "reporting an unreachable endpoint as a failed
deploy sends an operator to roll back a deploy that landed perfectly."

`3` also covers a hash the server did not *send*. `version.js` **omits**
`build`/`assets` rather than nulling them, so the sed that parses them yields
`""`, and `""` is never equal to the expected hash — which for a while printed
`FAIL: serving DIFFERENT code` with `live=` in the detail line, a verdict
manufactured from an absence. A proxy error page landed on the same false FAIL.
Unreadable is not a measurement, here as everywhere else.

A moved `assets` still is not a *fetched* file — browsers cache on the `?v=`
in the script tag. **Bump it in every page that references a changed bundle.**

## Writing tests that scan source

Strip comments first. A comment that quotes the string it forbids is
indistinguishable from the code doing it, and this has produced four false
failures. `tests/test_preflight_matches_ci.py` has a `tokenize`-based
`code_only()` worth copying.

Prefer exercising a property over matching text: run the function, drive the
failure, assert the outcome. Source matching is for shapes a unit test cannot
reach (a guard being *reached* at every call site, a cap being configurable).

**When there is no seam, make one.** That advice is easy to skip because the
seam is usually the reason the scan was written. Three cases from 2026-07-30:

- The Telegram adoption card was built inline in the handler. #999 added a
  per-position SL/TP outcome, source-scanned it, shipped it — and it rendered
  **zero times** in production, because the callback received prose where the
  lookup expected symbols. The code was *present*. It was never reached, and
  no scan can tell those apart.
- The dashboard's engine-status chip was inline in 6k lines of browser script.
  Its test sliced the function body out with `indexOf` and ran it in a VM.
- A `/portfolio` label was pinned by grepping the file that builds it. That
  test passes with the label present *and* the "Recent:" list moved on top of
  it — which was the entire defect.

Extracting each into a pure renderer took minutes and immediately caught
things the scans could not: `/risk` scoring `HEALTHY 100%` on a halted
engine, and a `0 trades at 0% win rate` line that reads as a measured record
of failure rather than the absence of one.

**A mutation is what tells you a scan is standing in for behaviour.** Two of
these were written knowingly and both SURVIVED the round that should have
killed them, for the same reason: the mutation kept the literal and inverted
the branch. A scan asserting `credential_state` appears in the engine's live
gate passed against a swap of its two sentences, so a linked user was still
told they had never linked; a scan asserting `result="UNDECRYPTABLE"` appears
in the boot preflight passed against `if False:` around the block containing
it. Neither could see reachability, which is the one thing they were being
asked about. Both are driven now — plant the state, read what the operator is
told — and the drives are shorter than the scans were.

**Do not convert wholesale.** 47 of 532 test files scan source and most of
them should — `tests/test_trade_live_mode.py` says so in its own docstring:
the behaviour is covered elsewhere and the file locks *wiring*. The narrow
failure mode is a source scan **standing in for behaviour nothing else
tests**.

Rank candidates by what a wrong claim would cost. That list is empty now —
`_status_lines` was the last, and it had the same shape as the other two:

```python
st = {}
try:
    st = self.engine.risk.drawdown_status()
except Exception:
    st = {}
lines = ["📉 <b>Live drawdown backstop</b>"]
if st:
    ...
```

`drawdown_status()` is *itself* documented "best-effort; returns empty on any
error", so two layers swallowed the same fault and produced **a heading with
nothing under it**. Neither guard nor omit — the section still announces
itself and then says nothing, which reads as the third thing the table warns
about: *nothing to report*. On the control that decides how much real money is
lost before the bot halts, printed directly above "a looser cap means the bot
tolerates **more loss** before halting".

The engine also computes `drawdown_source` — live high-water mark vs paper
snapshot — with a comment recording that reporting one as the other let an
operator "read ~0% from a gate that was refusing trades at 9%". The card
dropped the label, so the number was unattributable. It prints it now.

`/risk` was left open here on purpose — it substituted the paper number for
the enforced one on failure, and the note said "fix it with that renderer, not
before". **Done**, and the renderer half was the larger one:
`render_risk` did `data.get("current_drawdown", 0.0)`, so an absent reading
scored `healthy = 0.0 < ddl` and printed **HEALTHY · Health 100%**. The two
comments already inside that function describe that exact contradiction —
they were about a high-water mark erased by a restart; this was the reading
never arriving. Same output, different door. There are three outcomes now, not
two, because *could not read it* is not one of the other two.

A test wrote itself into the same trap on the way: the PNG tile's colour was
checked by asserting the old expression was **absent from the handler**, and
that passed against a mutation reintroducing it under a different variable
name two lines up. `drawdown_tile()` is the seam; it is now simply called.

### Asserting a short string is ABSENT is the assertion that keeps misfiring

Three times in one sweep, each a test failing on prose rather than code:
`"to liq" not in out` matched "sits **to liq**uidation" in the card's own
caveat; `"0.0%" not in out` matched inside "(default 1**0.0%**)"; and a colour
test asserting no green anywhere matched `d_icon`, which encodes DIRECTION and
was telling the truth. Anchor to the field's own line, or assert the positive
rendering instead — and when a fresh assertion fails, check whether the code
or the assertion is wrong before touching the code.

`_cmd_open_positions` came off it, and the most expensive claim in the product
turned out to be sitting behind a comment that said the opposite:

```python
except Exception:
    pass  # Orders fetch not critical
```

True of the listing, false of everything built on it. One failed
`fetch_open_orders` left every symbol at `0`, and `sl_str = ... if sl and sl >
0 else _none` rendered that as **SL None** — *this position is unprotected* —
for every orphan at once. Orphans are the positions the bot did not open, shown
to an operator reading the list *because they do not know what is out there*.
`sl_order` is three-valued now: a price, `none` (the venue answered and there
is no stop), `unknown` (nobody looked).

The extraction is what found the rest. `orphan_position_row` is pure, and the
mutation that reverted the mark price to `0` had passed the **entire suite**
before it existed — the renderer was thoroughly covered and the row builder was
covered only by grep. Behind it: an age of `0.0` rendering as "0m" (just
opened) for a position of unknown age, an `rr_live` of `0` for an orphan that
has no thesis to measure a reward against, and `_fmt_price` happily formatting
whatever it was handed.

Two things worth copying from it. `_fmt_price(None)` now returns an em dash
rather than each of a dozen call sites remembering to check — guard at the
boundary and new callers inherit the honest behaviour. And the first draft of
the colour test asserted no green anywhere on the row and **failed**, on
`d_icon` — which encodes direction (🟢 long / 🔴 short), and the direction of a
position whose mark we could not read is still perfectly well known. Not every
match is a defect, including in your own new tests; that assertion would have
removed a true statement to satisfy a rule about false ones.

`_cmd_escape` was on that list and came off it, and the extraction paid for
itself immediately. Inline, nothing could plant a crashed planner and read what
the operator would see — and what they would have seen was
**"🪂 no open positions to unwind"**, because `escape_agent.plan()` returned
the same document for "the book is flat" and for "an exception happened". An
all-clear on the emergency-exit screen, assembled from a failure, shown to
someone reading it precisely because something is wrong.

Three more came out of the same seam, all leaning the same way: an urgency
nobody could measure rendered 🟢 (the `⚪` fallback was real but unreachable,
because `report.get("risk", "none")` resolved the absent case to a word the
icon map knows); a twelve-step cap on an *ordered exit plan* with nothing
saying so, on the card **and** on the tamper-evident chain record; and
`_book_risk(None)` — reached when no position had a readable leverage —
answering `"none"`, the calmest verdict there is, on the exact evidence that it
could not be assessed.

The corollary found the rest: `guardian_status` makes the same claim, and every
one of its fail-open defaults pointed at safe — `twin`, `sentinel`, `escape`
and `posture` all `"none"`, inside a `try/except` that swallows the read. Its
rollup then ranked an unknown as the *safest* input (`order.get(r, 0)`), so
`max()` discarded it in favour of whatever happened to work.

One fix was made and then removed on purpose: a probe of the executor to tell a
flat book from an unreadable one. It coupled the console to executor internals
to buy a case nothing demonstrated, and broke four tests doing it. The
`try/except` already covers every fault that raises. *Check reachability before
fixing* applies to your own fixes too.

### A module nothing calls is indistinguishable from one that does not work

`token_dossier`, `presale_claims` and `deployer_history` were pure, correct,
heavily tested, and imported by **zero** non-test modules. Four scorers, a
composer, seventy-seven tests, and no human could reach any of them. Every
test passed the entire time, because tests were the only caller.

That is the same failure as #999 one level up: there, a card was built and
never reached; here, a whole subsystem was. Neither is visible from a green
suite, and no source scan distinguishes them — reachability is a property of
the *callers*, so it can only be checked from outside the file.

`tests/test_no_new_unreachable_modules.py` checks it every run, against
`tests/unreachable_baseline.txt` (**2** modules today). It is a ratchet in
both directions: a new entry means
somebody just built another scorer nobody calls, and an entry that leaves must
be deleted in the same commit — the `known_failures.txt` rule, for the same
reason.

**Fix before you wire, and the fixing is most of the work.** `basis.py` and
`market_cap.py` were the last two names on the roadmap's signal-fusion line,
and each was defective in exactly the way a module nothing reads becomes
defective. `market_cap` built every number with `.get(k, 0)`, so an unreadable
FDV produced `fdv_mcap_ratio = 0.0` on a field documented ">2.0 = high
inflation risk" — the *safest* value it can carry, arrived at from no data, and
CoinGecko returns a null FDV for every token with no max supply, so that was
the ordinary response for a whole class of asset rendered as an all-clear.
`basis` read `ticker.get("last", 0)`, and a null `last` answers `None`, so
`None <= 0` raised and a successful fetch of an unpriced ticker was logged as a
network failure.

The trap was in the wiring rather than the code: the engine's exchange factory
is a **coroutine function**, and `basis.py` called it synchronously. That fails
into the broad handler as an `AttributeError`, so the provider would have
returned `None` forever — wired, called, and dead, which no reachability
checker can see because it *has* a caller. `exchange_flow.py` already carries
the `inspect.isawaitable` guard, and its docstring records the same bug
shipping once before.

Both halves fired on their first real use, one commit later. `integrity_veto`
— veto-only, `off/shadow/enforce`, described in `docs/token_safety.md` as the
thing `token_safety` "unblocks" — got wired into `token_research` in shadow,
and the stale-entry test refused to pass until the baseline was updated. The
count above is pinned against that file for the same reason: a number in prose
is the part that rots first.

Wiring it also surfaced the trap waiting in it. `assess({})` returns the word
**`clear`** — correct on its own terms, nothing flagged because nothing could
be — and `clear` is what a reader takes as a clean bill of health. Printing it
over `checked == 0` is a confident all-clear manufactured from no data.
Fail-open-per-feature is the right rule for SCORING and the wrong one for
DISPLAY, and the two were the same function until something finally called it;
`integrity_veto.is_reading()` is now the seam between them.

> Its first version scanned only `bot/` and `scripts/` for importers and
> declared `bot/api/auth_routes.py` dead — it is mounted by `api_bridge.py` at
> the repo root, which was not being read at all. **A reachability checker with
> a blind spot manufactures exactly the accusation it exists to prevent**, so
> the sweep now reads every `.py` in the tree and entry points are excluded by
> their `__main__` guard.

**Registration is not reachability, and it is a fourth granularity.** Module,
module-level def, method — and then the thing that dispatches. `permission_for()`
is fail-closed and says so: "a skill added later is unreachable from chat until
somebody decides what it needs". Correct, and *silent* — nothing ever reported
the pending decision, so the backlog reached 9 of 30 registered skills
(`tests/unreachable_skills_baseline.txt`, same two-way ratchet). Five of them
were in `bot/skills/macro_skills.py`, each advertising a slash command —
`/macro`, `/eventrisk`, `/compliance`, `/approve`, `/kill` — that no transport
reached. The backlog is **5** of 30 registered skills now: `/eventrisk` and
`/compliance` are wired, and the deciding question was never "can it run" but
*who should be able to run it*. `/eventrisk` reuses `macro`, a permission
trader and paper already hold;
`/compliance` summarises the GLOBAL consent ledger, so it took a permission no
role but admin holds. `macro_brief` is the third, and the question for it was
*which surface*: it advertised `/macro`, which the calendar already answers
to, so it is a sub-mode of that command — `/macro brief` — plus a chat tool
and a free-text skill under `macro`, rather than a second name for one
subject. The five left are each triaged in the baseline with a reason,
and the two macro ones are arguments against themselves — `kill_switch` would
be a SECOND emergency halt beside `/halt`, which is a hazard in an emergency,
and `request_live_approval` needs an `approval_manager` that does not exist.

**A triage note names the blocker it noticed, and the one it did not is the
reason the skill was still dark.** `quant_analyze`'s entry said the obstacle
was a TIER decision — "a permission alone would give away more than deepscan
for free" — which was true and was the smaller half. Driven against a 503 the
skill returned a complete report: `Hurst 0.851 → trending memory`, `ADX 22.9`,
a GARCH forecast, `Composite Score 0.381` and `QUANT GATE: REJECTED`, every
number modelled from `_generate_synthetic_ohlcv(150, seed=hash(symbol))`, with
`bars_analyzed: 150` for bars nobody fetched and the word "synthetic" reaching
the audit log only. The seed made it worse rather than better: `hash(symbol)`
is stable, so the same fabricated report came back every time and a user who
ran it twice read the consistency as corroboration. Unwired is *why* that
survived — the `market_cap` / `basis` / `seasonality` shape a fourth time, and
fixing was most of the work. `read_ohlcv` is the seam, an unreadable fetch
GUARDS (a card that quotes no statistic at all), and generated candles are now
opt-in and banner-labelled on the card, because `scripts/e2e_pipeline.py`
legitimately walks the whole system with no venue. The tier question then
answered itself on the ladder's own stated basis — seconds of in-process
modelling is what `pro` means — and `/quant` is `@guard("analyze")`, the same
read-only question one layer deeper.

Neither older ratchet could see them: the module *is* imported and its
`build_v2_skills()` *is* called, and every skill body is an `execute` override
on a subclass, which the method sweep declines by design. And unrunnable is
precisely *why* all seven of that module's attribute probes named fields that
never existed — `upcoming_events` for `get_upcoming_events`, `consent_ledger`
for `get_consent_ledger`, a `circuit_breaker` for a halt that lives on
`engine.risk`. Every miss rendered as a confident negative: **"No upcoming
events loaded"** over a calendar holding 40 events with NFP a week out, on a
fail-closed macro system where that exact sentence means *the calendar is
gone*. Tests were never the only caller here — there was no caller at all.

> The methods ratchet had a blind spot in the *other* direction, and the other
> direction is the dangerous one because it is quiet. It counts identifiers, so
> it cannot tell whose method a name means and drops any name two classes both
> define — 60 names covering **274** methods nothing checked.
> `ComplianceEngine.format_for_telegram` has no caller anywhere and never
> appeared in the baseline, because seven classes define that name. A false
> accusation is loud and gets fixed; a false acquittal just sits there.
>
> A second pass now attributes `<recv>.<name>()` by resolving the receiver
> through `self.x = Foo()` and `x = Foo()`. **Sound, not complete**: one
> unresolvable receiver makes the whole name ambiguous, and the 34 names that
> stay ambiguous are stated in the baseline and pinned by a test, because a
> gate whose coverage is overstated is the failure this file exists to prevent.
>
> **Two drafts of it accused live code, which is the argument for that rule.**
> The first collected only `self.x.run()` receivers, concluded every receiver
> had resolved, and reported `RuneClawEngine.run` dead — `bot/main.py:434`
> calls it as `engine.run()` on a local. The second treated `x = make_thing()`
> as typing `x`, so a factory bound the name to a function matching no class,
> the receiver *looked* resolved, and `CatalogWatch.recent` was accused while
> `scan_skill.py` calls it. Names assigned from anything that is not a known
> class are poisoned now.
>
> And the guard against those two was itself worthless at first: asserting
> `CatalogWatch.recent` is not accused PASSED under both mutations, because a
> different receiver of `recent` poisons the name anyway. A real-tree assertion
> can pass for a reason unrelated to the rule. The guards are planted trees
> where the rule is the only thing in play.

**Plant the state, assert what the card says.** `tests/test_surface_scenarios.py`
and `app/test/engine_status_scenarios.test.js` hold the pattern: MUST_SAY,
MUST_NOT_SAY, and a planted **red herring** — a true-but-misleading signal.
The red herring is the point. A green LLM health check rules *one* cause out
and names none, and reading it as "the exchange is slow" cost 37 timed-out
ticks pointed at the wrong subsystem.

## Deploying so a dead bot cannot look like a live one

`python -m bot.main` defaults to `--mode telegram`. It used to default to
`cli`, which finds no TTY and **exits zero** — so a launcher that forgot the
flag printed `DEPLOY_DONE` and left nothing running. That happened on ~15
consecutive redeploys on 2026-08-01, because `git reset --hard` restored the
flagless launcher every time.

Two habits stop it recurring:

- **Keep the launcher outside the repo.** Anything inside it is one
  `git reset --hard` away from reverting. `deploy.sh` in here only symlinks
  persistent `.env`/`data` back in; it is not the entry point.
- **Gate `DEPLOY_DONE` on the process still being alive**, not on it having
  started:

  ```bash
  nohup python -m bot.main >> bot.log 2>&1 &
  scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
  echo "DEPLOY_DONE"
  ```

  Prefer `--pid`: the launcher knows what it started, and `pgrep -f` matching
  a *pattern* also matches the checking script's own command line. The first
  draft of that script reported OK for a process that had never existed.

  It also treats a **zombie as dead** — `kill -0` succeeds on a defunct
  process, and since the deploy script is the parent that has not reaped it,
  the naive check passes on exactly the failure it exists to catch.

- **Gate it on the code being the code you think it is**, before starting
  anything:

  ```bash
  scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
  ```

  On 2026-08-20 a deploy ran `git fetch origin && git reset --hard
  origin/main` and reported success while landing on a commit **255 commits
  stale**: `origin` on that box is a GitLab mirror and the real repository is
  a remote named `backup`. Every other check passed, because each was true of
  the stale tree — the pull worked, the symlinks resolved, the user store
  loaded, 18 users were present. The only thing wrong was *which code*, and
  nothing asked. A restart would have applied new configuration to a binary
  containing none of the fixes it was meant to deploy.

  **Never reset to a remote-tracking ref. Reset to the URL:**

  ```bash
  git fetch https://github.com/metafrogmeme-droid/001 main
  git reset --hard FETCH_HEAD
  ```

  A remote *name* is a per-machine nickname that can point anywhere, so
  "use the right remote" is advice, and advice is what failed. Fetching a URL
  writes `FETCH_HEAD` and no `refs/remotes/*`, so there is no stale ref left
  to reset to by mistake — which also sidesteps the trap that `git fetch
  origin main` updates `FETCH_HEAD` while leaving `refs/remotes/origin/main`
  untouched.

  The guard reads the URL with `git ls-remote` and consults nothing local, and
  it separates **could not check** (exit 3) from both verdicts — a gate that
  reads an unreachable network as "up to date" ships stale code on the one day
  the network is down.

## Operational docs

- `docs/LIVE_HARDENING_RUNBOOK.md` — boot probes, engine triage, the caps
  table, dashboard vocabulary, deploy verification
- `scripts/cloudflared/` — named-tunnel procedure for the bot gateway
