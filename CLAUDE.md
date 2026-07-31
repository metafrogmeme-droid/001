# Working on RUNECLAW

## Before you push, run one command

```bash
python3 scripts/preflight.py
```

It runs what CI runs, by **parsing `.github/workflows/ci.yml`** rather than
restating it — so it cannot drift, and a new CI step becomes a new preflight
step for free. Eight gates: two ruff passes, mypy, bandit, pip-audit, the
baseline test gate, the web app's suite, and guard reachability. ~6 minutes.

```bash
python3 scripts/preflight.py --fast   # tight loop; drops only the network gates
python3 scripts/preflight.py --list   # show the plan, run nothing
```

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

Rank candidates by what a wrong claim would cost. The surfaces that still
build cards inline and make halt/breaker/stop-loss claims — `_status_lines`,
`_cmd_escape`, `_cmd_open_positions` — are where to look next.

**Plant the state, assert what the card says.** `tests/test_surface_scenarios.py`
and `app/test/engine_status_scenarios.test.js` hold the pattern: MUST_SAY,
MUST_NOT_SAY, and a planted **red herring** — a true-but-misleading signal.
The red herring is the point. A green LLM health check rules *one* cause out
and names none, and reading it as "the exchange is slow" cost 37 timed-out
ticks pointed at the wrong subsystem.

## Operational docs

- `docs/LIVE_HARDENING_RUNBOOK.md` — boot probes, engine triage, the caps
  table, dashboard vocabulary, deploy verification
- `scripts/cloudflared/` — named-tunnel procedure for the bot gateway
