# Working on RUNECLAW

## Before you push, run one command

```bash
python3 scripts/preflight.py
```

It runs what CI runs, by **parsing `.github/workflows/ci.yml`** rather than
restating it — so it cannot drift, and a new CI step becomes a new preflight
step for free. Twenty gates: two strict ruff passes, the whole-tree ruff
ratchet, mypy on the money modules, the whole-tree mypy ratchet, bandit,
pip-audit, the baseline test gate, the red team, the custody red team, the web
app's parse check, its npm advisory ratchet, its suite, the marketing site's
build, its npm advisory ratchet, its published-output honesty tests, the check
that the committed site is the built site, the Anchor workspace's typecheck, its
npm advisory ratchet, and guard reachability. ~14 minutes.

That "for free" is literal and has now been collected six times: the app parse
gate and the npm ratchet were added to `ci.yml` for M3 and appeared in the local
plan with no change to `preflight.py`, the two red-team gates each did the
same, the marketing site's advisory ratchet did it again, and the two lint/type
ratchets did it a sixth time. This paragraph's own gate count is pinned by
`tests/test_claude_md_accuracy.py`, which failed the moment each of them landed
— including on the sentence you are reading, which said "Ten" until the risk
red team made it eleven, the custody one made it fifteen, the audit's
npm-coverage fix made it eighteen, and its lint/type ratchets made it twenty.

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

Two practices found these; the rule alone found none of them.

**Ask which OTHER surface makes the same claim — before calling the fix
done.** Five of those ten PRs came from auditing the previous one. `/portfolio`
still had the defect `/open_positions` had just been cured of. A `theater.js`
value flowed through three renderings and fixing two left the third.

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
`tests/unreachable_baseline.txt` (**6** modules today). It is a ratchet in
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
the pending decision, so the backlog reached **9** of 30 registered skills
(`tests/unreachable_skills_baseline.txt`, same two-way ratchet). Five of them
are in `bot/skills/macro_skills.py` and advertise slash commands — `/macro`,
`/eventrisk`, `/compliance`, `/approve`, `/kill` — that no transport reaches.

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
