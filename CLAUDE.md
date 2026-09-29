# Working on RUNECLAW

RUNECLAW is a trading bot (`bot/`, Python: Telegram bot, engine, risk gate,
live executor) and a website (`app/`, Express + static pages). Real money moves
through `bot/core/engine.py` and `bot/core/live_executor.py`. This file is the
short guide. The full incident history it was distilled from, every chapter
this file used to hold, is `docs/lessons/ENGINEERING_LOG.md`, indexed by
`docs/lessons/INDEX.md`.

**Before you change a subsystem, search `docs/lessons/INDEX.md` for it and
read the chapters it points at.** Most defects in this repo were fixed once
already somewhere nearby. Where a code comment or test says "CLAUDE.md
records …", the chapter is in the log.

## Before you push, run the preflight

```bash
PATH=/usr/local/bin:$PATH python3 scripts/preflight.py          # everything CI runs locally
PATH=/usr/local/bin:$PATH python3 scripts/preflight.py --fast   # drops only the network gates
python3 scripts/preflight.py --list                              # the plan, runs nothing
```

- It parses `.github/workflows/ci.yml` and runs its steps, so a new CI step is
  a new preflight step. A new CI *job* must also be added to `LOCAL_JOBS`, or
  `--list` reports it as not covered locally.
- It takes about 45 minutes; the test gate is most of it.
- The `PATH` prefix puts the pinned ruff, mypy and Node first. `requirements-ci.txt`
  pins ruff and mypy; `ci.yml` pins Node. On a box where the pinned Node lives
  elsewhere, put that first too (for example `/opt/node24/bin`).
- **CANNOT CHECK is not a pass.** A gate that could not run (wrong tool
  version, a launcher that ignores SIGINT) is reported apart from a failure.
  Read the per-gate list, never only the summary line.
- Run it **after your last edit**. A gate run before a later edit measured a
  tree nobody committed.
- Never run two full suites at once in one checkout: the harness deletes
  `data/` around every test. A second checkout has its own `data/`.
- Keep extra git worktrees **outside** the repository directory. Several
  guards walk the whole tree and will read a worktree's copy of `bot/` as
  part of this one.
- Stop a preflight by killing its whole process tree, not one PID.
- It clears every `__pycache__` first, because CI starts cold. Clear it
  between mutations too: a `.pyc` whose source changed within the same second
  and kept its size is reused.

Do not substitute a bare `pytest`. The suite runs through
`scripts/ci_test_gate.py`, which enforces `tests/known_failures.txt`: a
baseline entry that starts passing is a failure. Its flake filter re-runs a
new failure alone and forgives it if it passes. That is right for a timing
test and wrong for a state leak; a test forgiven every run is a leak to find.

## Ratchets

Several gates are two-way ratchets against a recorded baseline: a count may
only go down, and a count that goes down is re-recorded **in the same
commit**.

| gate | baseline | re-record |
|---|---|---|
| `scripts/ruff_gate.py` | `tests/ruff_baseline.json` | `--update` |
| `scripts/mypy_gate.py` | `tests/mypy_baseline.json` | `--update` |
| `scripts/honesty_gate.py` | `tests/honesty_baseline.json` | `--update` |
| JS honesty shapes (`app/test/js_honesty_ratchet.test.js`) | `app/test/js_honesty_baseline.json` | `node app/test/update_js_honesty_baseline.js` |
| test gate | `tests/known_failures.txt` | edit the file |

`python3 scripts/rerecord.py --all` re-records the first four in one step, and
re-records nothing while any of them reports growth; `--check` reports without
writing. No baseline stores a total: each gate sums its counts, and refuses a
baseline that still stores one as CANNOT CHECK, because a clean git merge of
two branches that each lowered a different count leaves a stored total wrong.
Many structural rules are also baselines of named exceptions, each with a reason,
and are two-way: a new site fails, and a listed site that no longer exists
fails. Never widen a baseline to get green; fix the site or record it with a
reason a reviewer can check.

`tests/test_claude_md_accuracy.py` reads numbers and citations out of
`docs/lessons/ENGINEERING_LOG.md` and `docs/INCOME_MAP.md` and compares them
with the code. When you move code that the map cites by `path:line`, re-derive
the citation from what its sentence names; a mechanical line shift preserves
what a citation pointed at, right or wrong. The generated block in
`.env.example` is regenerated with `scripts/safety_flag_inventory.py
--section`, and `docs/lessons/INDEX.md` with `scripts/lessons_index.py`.

## The rule behind most of the tests: unreadable is never zero

**Unreadable is never zero, and absent is never a measurement.** A failed read
must not render as an empty result, a `0.00%`, or a confident negative. Two
honest strategies:

| | shape | right for |
|---|---|---|
| **guard** | throw / `mustRead()` so the caller paints an error state | a single-source panel |
| **omit** | catch each source and leave missing ones out, saying so | a composite view where one dead source must not blank the rest |

Never neither. `app/test/panel_failure_honesty.test.js` enforces this across
every `renderPanel` loader.

- **Colour is a claim.** Green says "in profit" as loudly as the number does.
  Unknown gets a muted colour.
- **A heuristic is never a verdict.** A green health check rules one cause
  out; it does not name the cause.
- **Test `is None`, not falsiness.** `0.0` is a real, measured break-even.
- **Three values, not two.** Read, absent, unreadable are different facts and
  get different sentences.

The shapes this rule takes, which a search can find:

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
| `undefined >= 0` false, `null >= 0` true (JS) | two absences, opposite verdicts |

The honesty gates count five of these shapes per file. A hit is a place to
look, not a defect: check reachability before fixing, and do not rewrite
correct code to satisfy an analyser.

## Habits this repo depends on

- **One reading per quantity.** A second copy of a map, a threshold or a
  judgement is a second answer, and the two drift. Derive lists from the code
  they describe (an AST walk, a registry) instead of writing them by hand.
- **A field name is not a quantity.** `size_usd` has meant margin in one place
  and notional in another; `stop_loss = 0.0` means "no stop on record";
  `confidence` may be a measurement, a stamp (`build_manual_idea` writes
  `1.0`) or a calibrated figure. Ask the existing reading
  (`position_size_basis`, `price_on_record`, `displayed_confidence`,
  `quality_reading`, …) rather than reading the field.
- **Ask which other surface makes the same claim** before calling a fix done.
  The same defect usually sits one card, one route or one runtime over.
- **A card that names a command or a door claims it works.** Check the door.
- **Identity decides whose book.** The operator's executor is
  `engine.live_executor`; a per-user executor comes from `_executor_for` /
  `viewer_executor`. A read for a caller must never fall back to the
  operator's book. `None` never means "the operator".
- **A process that is not the bot is a reader of the bot's state.** Any
  `RuneClawEngine` built outside the bot process must be detached
  (`detach_state_persistence`) so it cannot write the bot's files.
- **Autonomous live orders need an eligibility record.** A live confirm with
  no human tap is placed only when `AUTO_CONFIRM_LIVE_ENABLED` is on and
  `benchmark/eligibility/<strategy_hash>.json` says the running strategy
  survives (`bot/core/live_eligibility.py`, asked at the compliance mint).
  None ships. See `benchmark/eligibility/README.md`.
- **Grep the definition, not the name you remember.** Zero hits on a name that
  does not exist is not a measurement of anything.
- **A measurement you remember is not a measurement.** Re-drive a figure
  before you write it down, and state its date and basis.

## Writing tests

- **Drive behaviour; scan source only for shapes a test cannot reach** (a guard
  reached at every call site, a cap being configurable). A scan cannot see
  reachability: `if False:` around a line keeps the line.
- **Strip comments before scanning.** A comment that quotes the string it
  forbids is indistinguishable from the code doing it. Use
  `tests/source_scan.py::code_only` (Python) and
  `app/test/helpers/code_only.js` (JS). `code_only` blanks docstrings, so an
  AST walk should parse raw source; an AST cannot see comments anyway.
- **Assert both arms.** A refusal-only assertion passes against code that does
  nothing at all.
- **When a fresh assertion fails, check whether the assertion or the fixture is
  wrong before touching the code.**
- **A fixture must be able to produce the state it names.** A fixture where
  every row is readable cannot tell a filtered aggregate from an unfiltered
  one; a symmetric fixture cannot tell two books apart; a fixture on either
  side of a boundary measures nothing about the comparison at the boundary.
- **Asserting a short string is absent keeps misfiring.** It matches prose,
  captions and other fields. Anchor to the field's own line or assert the
  positive rendering.
- **A hand-written stand-in forgets the next attribute.** Prefer the real
  object, or bind the real methods, or record what the stand-in was handed.
- **Bound a slice by a node (`ast.FunctionDef`), never by "the next thing that
  appears" or a character count.**
- **Containment belongs in the harness.** `tests/conftest.py` restores state a
  test leaks (env keys, logging, the executor halt check, module singletons,
  outbound network). A leak is invisible from any single run's verdict.
- **Mutation-test a fix before calling it guarded.** Mutate the fix back and
  confirm a test fails for the reason it names. Refuse an anchor that matches
  twice or zero times; never run a round against a red baseline (every
  mutation then reports killed); record an equivalent mutant instead of
  counting it; restore the tree on SIGTERM/SIGINT.
- **When there is no seam, make one.** A renderer inside a 400-line handler is
  one no test can run; extract it and drive it.
- **Run the full gate before pushing.** It routinely fails a slice on a test
  none of the slice's own suites ran.

## Public-surface rules

- **No dollar amounts** on public, community, leaderboard or marketplace
  payloads: percent, ratio and count only. Private per-user surfaces may show
  dollars. Market prices, volume, OI and gas are public market facts and are
  fine. Check each route, not each file; a sub-object forwarded from the bot
  is checked where its keys are built.
- **Signed in is not the operator.** Registration is open. The operator's view
  is decided by `isOperator` in `app/lib/operator_view.js`, read from the database.
- **Never put secrets**, API keys, private keys or internal config into
  user-facing text, logs or the repo. Error text reaching a person names the
  exception class, never its message. `bot/utils/secret_shapes.py` is the one
  table of secret shapes; `reply_safe` (`bot/utils/outbound.py`) is the outbound
  scrubber.
- **A slash in a path segment does not survive a hop.** Pass a symbol such as
  `BTC/USDT` as a query parameter, not a percent-encoded path segment.

## Deploying and verifying

- There are **two processes**: `python3 -m bot.main --mode telegram` (bot,
  engine, gateway on :8080) and `api_bridge.py` (uvicorn on :8000). A deploy
  that starts one and reports success is not a deploy.
- Use `python3`, not `python`.
- `scripts/launch_all.sh.template` is the launcher (copy it out of the repo
  before use) and `scripts/systemd/` holds the units; do not run both.
- Reset to the repository URL, never to a remote-tracking ref:
  `git fetch https://github.com/metafrogmeme-droid/001 main && git reset --hard FETCH_HEAD`.
  `scripts/verify_deploy_source.sh` checks it.
- `scripts/verify_deploy.sh` compares the `/api/version` hashes on both deploy
  targets. Exit `0` verified, `1` a real mismatch, `3` could not be checked.
- Bump the `?v=` cache-buster in every page that references a changed bundle.
- `scripts/verify_bot_alive.sh --pid $!` gates on the process still running,
  not on it having started.

## Where things live

| path | what |
|---|---|
| `bot/core/engine.py` | the tick loop, confirm path, compliance mint |
| `bot/core/live_executor.py` | orders, stops, fills, closes, reconcile |
| `bot/risk/` | the fail-closed pre-trade gate and its inputs |
| `bot/skills/` | Telegram commands and skills (`telegram_handler.py` and mixins) |
| `bot/web/` | the bot's gateway for the website |
| `app/routes/`, `app/lib/`, `app/public/` | website server, shared libs, pages |
| `tests/`, `app/test/` | Python and Node suites |
| `scripts/` | preflight, ratchets, generators, deploy helpers |
| `docs/INCOME_MAP.md` | what the product does, by capability, with citations |
| `docs/IMPROVEMENT_PLAN_2026-09-29.md` | the current plan |
| `docs/lessons/` | the engineering log and its index |
| `benchmark/` | frozen datasets, recorded results, eligibility records |

New lessons go in `docs/lessons/ENGINEERING_LOG.md` as a chapter (then
regenerate the index), not here. This file stays under 25,000 bytes;
`tests/test_the_agent_guide_stays_lean.py` checks it.

## Operational docs

- `docs/LIVE_HARDENING_RUNBOOK.md` — boot probes, triage, caps, deploy checks
- `scripts/launch_all.sh.template` — both processes, both ports
- `scripts/systemd/README.md` — supervising the two processes
- `scripts/cloudflared/` — named-tunnel procedure for the bot gateway
