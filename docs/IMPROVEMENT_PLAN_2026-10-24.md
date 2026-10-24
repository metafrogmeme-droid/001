# RUNECLAW improvement plan, 2026-10-24: finish the evidence spine, drain the queue, decide the centre

*Prepared 2026-10-24 on branch `humanoid-traders/awesome-hopper-cy4ry9` from `main` at `64e91f2`. A read-only survey of the 25 days since `docs/IMPROVEMENT_PLAN_2026-09-29.md`; no code was changed. Code is cited by symbol (`path::Qualname`), never by line. Every figure carries its date and basis; a figure this survey could not re-drive is written as unknown.*

## Bottom line

The September plan asked for two things: contain capital in code, and build the measurement that could one day release it. The first landed. The eligibility gate refuses every autonomous live order until a reviewed record exists, live orders stay inside the measured universe, live off now means paper, and the one rule that passed a pre-registered bar is recorded daily without being traded. The second did not: there is no idea ledger, no hypothesis registry, no shared inference utility, the Day-0 production readout was never run, and all ten architecture decision records are still "Proposed".

What the capacity went to instead is a product fan-out. In 25 days, 151 commits and 51 merged pull requests added a Daily Duel family (its own two Telegram bots, a Mini App, chips, confidence ratings, receipts), a Pi Network app in seventeen languages with a coach, a memecoin launchpad on a testnet, seal-root anchors on eight chains, and seven read-only safety tools (Claw Guard, Panic Button, RWA pre-check, Scam or Safe, a custodial-bot check, an approval digest, a SaR oracle). Almost all of it is behind flags that default off. Sixteen more pull requests, opened in the last 24 hours, add Kink Cap, Time-to-Cash, a fair-value bridge, Recursion Breaker, sandwich restitution, a conservation brake and ADL cover, each 24 to 83 files.

None of this produces evidence about the money core, and each piece pays the same tax: a re-recorded count, a merge with `main`, a 41-minute test gate, a lessons chapter. The September diagnosis ("about 95% of engineering time goes to repair and bookkeeping") has flipped to "most engineering time goes to new capability", but the capability is on the periphery, and the loop tax is unchanged.

The plan, in order:

1. **Decide, in one operator session.** Run the Day-0 readout, accept or reject ADRs 0001 to 0010, and choose the product's centre: the trading copilot with a validated core, or the proof-and-duel family. Set a work-in-progress limit. Without this the agents keep producing whatever is easiest to produce.
2. **Drain the queue.** Land, split or close the sixteen open pull requests in ten days. No new product-surface pull request opens while more than four are open.
3. **Finish the evidence spine.** Identity stamps on every evaluation, Idea Ledger v1, a hypothesis registry with the daily trend sleeve as its first entry, one time-clustered inference utility, and the first committed week of the public record.
4. **Cut the loop tax.** A parallel test gate (measured first), counts derived in CI rather than quoted in prose, a serialized merge path so every `main` commit gets a verdict, and no empty commits to retrigger workflows.
5. **Consolidate the product.** One evidence page, a flag audit with kill dates, one story for four Telegram processes, and "lab" labels on what is not yet measured.

## What git, CI and the code show today

All figures measured 2026-10-24 on `main` at `64e91f2` unless stated.

| Measure | Reading | Basis |
|---|---|---|
| Commits since the September plan | 151 non-merge, 51 merged pull requests (#627 to #677) | `git log --since=2026-09-29` |
| Authors | 143 Claude, 46 Cursor Agent, 51 operator (mostly merges) | `git log --format=%an` |
| Where the lines went | `app/test` 2,850 files touched, `app/public` 2,457, `app/lib` 781, `bot/core` 643, `app/routes` 553, `bot/skills` 253 | per-commit file lists, grouped by top two path segments |
| Open pull requests | 16, of which 10 draft; all opened 2026-10-23 or 2026-10-24 from `cursor/*` branches; 24 to 83 files and 1,581 to 6,118 insertions each | GitHub API |
| Open issues | 0 | GitHub API |
| CI on `main`, last 60 push runs (2026-10-15 to 2026-10-24) | 52 success, 6 cancelled by `cancel-in-progress`, 1 failure (npm advisory ratchet, fixed in the next commit), 1 running | Actions API |
| Test gate duration on CI | 41 minutes 4 seconds serial (run on `afeabef`) | step timings |
| Whole CI wall time on `main` | about 45 minutes | run timings |
| Ratchet state | ruff 1,127 hits over 10 rules; mypy 483 over 18 classes; honesty: `bot/core/live_executor.py` 34 `get-default-zero` hits, `bot/formatters/signal_card.py` 19 | `tests/*_baseline.json` |
| Coverage floor | 60% | `scripts/ci_test_gate.py::COV_FAIL_UNDER` |
| God files | `bot/core/live_executor.py` 18,071 lines; `bot/core/engine.py` 11,877; `bot/skills/telegram_handler.py` 6,049 | `wc -l` |
| Surfaces | 50 public HTML pages, 123 route files, 36 skill modules, 84 uncommented keys in `.env.example` | `ls`, `grep` |
| Agent guide | `CLAUDE.md` 15,374 bytes; `docs/lessons/ENGINEERING_LOG.md` 1,940,466 bytes with 36 chapters added since the cut; `docs/lessons/INDEX.md` 95,648 bytes | `wc -c` |
| Re-record and re-measure commits | 13 of 151 name a count, baseline or re-measurement in their subject | `git log` subject grep |
| Dependency-advisory commits | 9 of 151 | `git log` subject grep |
| Chat eval | 54 cassette cases, scored in CI | `evals/chat/cases.jsonl` |
| Weekly public record | script and route exist; 0 weeks committed | `benchmark/record/` holds only `README.md` |
| Eligibility records | 0 (none ships, by design) | `benchmark/eligibility/` |

### What the September plan asked for, and what landed

| Item | Asked | State on 2026-10-24 |
|---|---|---|
| D2 default-deny eligibility at the Lock-5 mint | Phase 0 | **Landed.** `bot/core/live_eligibility.py`, read at the compliance mint; `benchmark/eligibility/README.md` describes the record. No record ships. |
| D0 Day-0 production readout | Phase 0, step 1 | **Not run.** ADR 0001 still says "readout not yet run". |
| G0 operator decisions as ADRs | Phase 0, step 2 | **Half.** ADRs 0001 to 0010 written; all ten remain "Proposed, decision pending operator". |
| D1 research mode; auto-confirm default off | Phase 0 | **The code default is off.** `bot/config.py::Config.auto_confirm_live_enabled` defaults to false, and `.env.example` sets the same. Research-mode notional (ADR 0002's other half) is still an operator decision. |
| G1 lean CLAUDE.md, lessons archive, backlog | Phase 0 | **Landed, minus the backlog.** Guide at 15 KB under a 25 KB test; lessons log and generated index. `docs/BACKLOG.yaml` does not exist. |
| G11, G5 derived ratchet totals, symbol citations | Phase 0 | **Landed.** No stored totals; `scripts/rerecord.py --all`; `scripts/cite.py` anchors in the income map. |
| G14 merge queue | Phase 0 | **Not landed.** `ci.yml` cancels in-progress runs per ref; six `main` commits in ten days got no verdict of their own. |
| A1 identity stamps, B3 Idea Ledger | Phase 0 and 1 | **Not started.** No `analysis_id` or `cfg_hash` anywhere under `bot/`; `strategy_hash` exists only for the eligibility gate. |
| C1, C11, C12 hypothesis registry, clustered inference, sealing | Phase 0 | **Not started in code.** No `benchmark/hypotheses/`, no `n_eff` or MDE anywhere. The sleeve pre-registration in `docs/research/daily_trend_sleeve_prereg.md` does the sealing by prose and a sha256. |
| B2, B5 adjusters in shadow, learner readiness | Phase 0 and 1 | **One piece landed.** Voter weights reach READY only when a trade-level AUC improvement's lower bound is above zero (`bot/learning` readiness, 2026-10-22). |
| A6, A3 as-live measurement | Phase 1 | **Landed as a reading.** Six-fold walk-forward on the v2 and v3 snapshots: honest PF 0.69, as-live 0.85, smart exits off 0.75. 226 of live's 249 strategy exits were on markets no snapshot holds. |
| New: LIVE_UNIVERSE gate | not in the plan | **Landed.** `bot/risk` refuses a live order on a market outside `benchmark/majors_1h/manifest.json`; the TradFi legs ran at PF 0.03 to 0.22 over 77 exits. |
| New: live off means paper | not in the plan | **Landed, owner's call 2026-10-18.** `RUNTIME.live_suspended`; a non-operator's confirm is a paper fill under `LIVE_ADMIN_ONLY`. |
| C-family: the daily trend sleeve | Phase 1 | **Landed as a pre-registered record.** The rule is a preset the live bot does not run; the book is appended daily; first reading 2027-10-17. The funding study (2026-10-17) passed on its pre-registered window (Sharpe 0.88, funding 14.5% of gross, drawdown 12% against a 38% bar) and reads 0.54 and 0.49 on the stricter holdout window. Adverse selection: the long book pays 2.24 times the unconditional funding rate. |
| E3 chat eval | Phase 0 | **Landed small.** 54 cases, CI-scored; injection cases unknown. |
| E7 model refresh | Phase 1 | **Done by decision.** Chat on Claude Haiku 5.5 for every caller (2026-10-21), scan and thesis unchanged. |
| Public record | Phase 1 | **Half.** `scripts/weekly_record.py` and `/agents#record` exist; no week has been committed. |
| G3 parallel test gate | Phase 1 | **Not started.** 41 minutes serial. |
| G9 strangler decomposition | Phase 2 | **Not started.** `live_executor.py` is 18,071 lines against the 5,685 the June roadmap recorded. |

## Diagnosis

1. **The safety spine is real and the evidence spine is prose.** Capital is contained by code that tests drive. The thing that could release it, a ledger of every evaluation with its outcome, a registered hypothesis with a date, an interval computed one way everywhere, exists as a pre-registration document and a sha256. That is honest, and it is not a system. The sleeve's 2027 reading will be computed by hand by whoever is around in a year.

2. **The operator's decisions are the critical path and they have not been taken.** Ten ADRs wait on one person. Every lane that needed a decision either waited (ledger, hypotheses) or routed around it (new surfaces that need no decision because they are off). The cheapest intervention in this plan is one afternoon of the operator's time.

3. **Flag-off is being read as free.** A surface that defaults off still ships code, tests, a lessons chapter, a count to re-record, routes the guard lint must classify, and a claim on a page. The admin-preview module (`app/lib/admin_preview.js`) now exists so that flagged features can be seen at all. Nine off-by-default product flags, four Telegram bot processes and eight anchor chains is a surface nobody has measured in production.

4. **The queue is the symptom.** Sixteen pull requests in 24 hours, each merging `main` into itself, each re-recording the source-scan count, each needing the same 45-minute CI. The plan's "one stage, one check or one intent family per PR" is being followed in name, and the result is sixteen half-done families at once.

5. **The loop tax is unchanged and partly self-inflicted.** The 41-minute gate is what the September plan measured. On top of it, `tests/test_claude_md_accuracy.py::test_the_source_scan_counts_it_quotes_are_the_ones_a_walk_returns` requires a prose edit to the lessons log whenever a test file is added, which is why "Record the source-scan count" appears as a commit in nearly every branch. A count quoted in prose that a test re-derives is a count that should be derived, not quoted.

6. **Dependency advisories are a steady drain and the one CI failure on `main`.** Nine advisory commits in 25 days; the only red `main` run was three npm ratchets tripping at once. The ratchet works. It should run on a schedule against `main`, not be discovered on a merge.

## Principles

The September principles stand. Three are added:

- **A flag that is off is a liability until it is measured.** Every off-by-default flag has an owner, a measurement that would turn it on, and a date by which it is turned on or deleted.
- **A count a test re-derives is derived, not quoted.** Prose carries the reading's date and basis; the number comes from the walk.
- **The operator's queue is a budget.** A decision that waits more than two weeks is taken by the recorded default, and the ADR says so.

## Workstreams

### H. Decide: the operator session

**Goal.** The decisions the September plan filed, taken and recorded, in one sitting, informed by a readout of production.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| H1 | Run ADR 0001's Day-0 readout: flags, caps, budgets, ledger row counts and date ranges, LLM and rule shares, venue-priced close share, adopted positions with unread margin, the four processes' uptime. No secrets. Append it to ADR 0001 and set it Accepted. | S (half a day, read-only) | - |
| H2 | Decide ADRs 0002 to 0010. Each gets a chosen option, who chose it and the date, or an explicit "defer to the recorded default on <date>". | S (2 to 3 hours) | H1 |
| H3 | Flip `Config.auto_confirm_live_enabled` to default off in code (ADR 0002's recommendation), so the code and `.env.example` read the same. Drive both readings in one test. **Landed:** the code default is false, `.env.example` has one `AUTO_CONFIRM_LIVE_ENABLED=false`, and the tests pin both. | S | H2 |
| H4 | Name the product's centre in ADR 0011: (a) copilot plus validated core, with the Duel and Pi family as acquisition; (b) the proof-and-duel family as the product, with the engine as its evidence source; (c) both at equal priority. Every open and future surface PR cites which centre it serves. | S | H2 |
| H5 | Work-in-progress limit: at most four open product-surface pull requests, written into the PR template and checked by a workflow that fails a fifth. Money-path, CI, security and dependency PRs are exempt. | S | H4 |

**Done when.** ADR 0001 carries the readout. No ADR says "pending operator". ADR 0011 exists. The WIP check runs.

### I. Drain the queue

**Goal.** Sixteen open pull requests become merged, split or closed, and the pattern that produced them does not recur.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| I1 | Triage table for the sixteen: centre served (H4), flag name, files, test count, CI state, blocking review items. Published as one comment per PR and one summary in `docs/adr/0011`. | S | H4 |
| I2 | Land the ones that are complete and green within the WIP limit, oldest first. Each merge waits for the previous one's `main` verdict (see J3). | M (ten days of merges) | I1, J3 |
| I3 | Split any PR over 40 files into one PR per stage, or close it with its branch kept and a backlog line. | M | I1 |
| I4 | Close the Kink Cap, Time-to-Cash, fair-value bridge and Recursion Breaker stacks into one PR each, or one backlog entry each, depending on H4. | M | H4, I1 |
| I5 | `docs/BACKLOG.yaml`, the file the September plan asked for: every closed-not-merged branch, every deferred ADR default, every "later" in a lessons chapter, each with an owner, a measurement and a date. A test refuses an entry with no date. | S | - |

**Done when.** Open product-surface PRs are at or under four. Every closed branch has a backlog line. No PR over 40 files is open.

### J. Cut the loop tax

**Goal.** A green `main` verdict for every commit, a test gate under 15 minutes, and no prose edit forced by a count.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| J1 | Measure the test gate first: per-file durations from one serial run, the ten slowest files named, and the share of wall time that is the network-reach ledger, the harness's `data/` deletes and import time. | S | - |
| J2 | Parallel gate behind a test-only state root (the September G2 and G3): `pytest-xdist` with `loadfile`, flake re-runs serial, the reach ledger aggregated, and a nightly `pytest-randomly` job. Target under 15 minutes, then 12. | M | J1 |
| J3 | Serialized merges: GitHub's merge queue on `main` with the baseline gate as a required check, or, if the plan does not allow it, drop `cancel-in-progress` for pushes to `main` so every commit keeps its run. Ban the empty "retrigger" commit by a workflow check. | S | - |
| J4 | Derive the source-scan and ambiguous-method counts. The lessons log keeps the date and basis of the reading; `tests/test_claude_md_accuracy.py` asserts the walk's shape (which files are scanned and why), not a quoted integer. The same for any other prose-quoted count the test file compares with the code. | S | - |
| J5 | Scheduled advisory job: the three npm ratchets, pip-audit and cargo-audit run nightly against `main` and open one PR with the pins, so a merge never discovers an advisory. | S | - |
| J6 | Impacted-test selection for the local loop (G7): a map from touched module to the suites that import it, used by `preflight.py --fast`; CI still runs everything. | M | J1 |
| J7 | One re-record step in CI that fails with the diff it would have written, so a branch that lowered a count is told what to commit rather than discovering it in a review. | S | - |

**Done when.** Every `main` commit in a rolling week has its own CI verdict. Test gate under 15 minutes on CI. Zero commits in a rolling month whose subject is a re-record.

### K. Finish the evidence spine

**Goal.** The sleeve's reading in 2027, the parity card, the Duel's track record and any future eligibility record are computed by one system from one store.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| K1 | Identity stamps (September A1): every analysis and every refusal carries `analysis_id`, `code_sha`, `cfg_hash` and `strategy_hash`. Below-floor evaluations carry their would-be levels. | M | - |
| K2 | Idea Ledger v1 (B3): one append-only store of evaluations with outcomes in net R, labelled from recorded candles within 8 days, never pruned. Writes are batched off the scan path and fail open with an audited drop count. | M | K1 |
| K3 | Hypothesis registry (C1): `benchmark/hypotheses/<id>.yaml` with question, data that may be read, data that may not, endpoint, MDE, required `n_eff`, evaluation date and verdict. Entry 0001 is the daily trend sleeve with its 2027-10-17 date; entry 0002 is the funding study, already read, marked exploratory. | S | - |
| K4 | One inference utility (C11): time-clustered intervals and a sequential test, used by the parity card, the readiness card, the Claw Record and the Duel track record. A ratchet test lists every other interval computation and refuses a new one. | M | K2 |
| K5 | Commit week 1 of the public record and schedule the rest: `scripts/weekly_record.py` runs on the bot's server each Monday and the operator commits the file; `verify_evidence.py` is a CI step. | S | - |
| K6 | The sleeve's evaluator, written now and sealed: the script that will compute the 2027 reading from the recorded book and Bitget's daily closes, with a fixture that proves it on synthetic rows, committed before any real row is read by it. | M | K3 |
| K7 | Cost model v1 (A7): per-symbol spread, fee and funding from recorded fills and book snapshots, labelled extrapolated above the sizes observed. Every C-family reading and every future eligibility record is priced by it. | M | K2 |

**Done when.** The parity, readiness, Claw Record and Duel surfaces read one interval. Week 1 is committed. The sleeve has a registered entry and a sealed evaluator. Every new evaluation row carries four stamps.

### L. Consolidate the product

**Goal.** Fewer, measured surfaces, each saying what it is.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| L1 | Flag audit: every off-by-default product flag (`BOTCHECK_ENABLED`, `SCAM_OR_SAFE_V0`, `PANIC_BUTTON`, `RWA_PRECHECK_ENABLED`, `CLAW_GUARD_V01`, the SaR, chips, confidence, Avalanche and season-prize flags, and any this list missed) gets an owner, the measurement that turns it on, and a date. `scripts/safety_flag_inventory.py` learns a second section for product flags so the list is derived. | S | H4 |
| L2 | Kill rule: a product flag off for 60 days past its date is deleted with its code in one PR, and its backlog line records why. | S | L1 |
| L3 | One evidence page: `/agents#record` carries the weekly record, the parity verdict, the sleeve's days-kept and the Duel track record, each read, absent or unreadable, with the K4 interval. Every other page that states a measured claim links here instead of restating it. | M | K4, K5 |
| L4 | One story for four processes: `docs/LIVE_HARDENING_RUNBOOK.md` and `scripts/launch_all.sh.template` describe the bot, the bridge, the duel bot and the anonymous duel bot with their ports, health probes and what each may not touch; `verify_deploy.sh` checks all four. | S | - |
| L5 | "Lab" labels: a surface whose flag is on but whose measurement is not yet in the ledger says "lab, unmeasured" in its header and its share preview, by the same reading the admin-preview banner uses. | S | L1 |
| L6 | Trust claims re-read: the anchor quorum, the proof bundle verifier, Claw Guard and the receipts each make a claim about what a chain or a signature proves. One review per claim, recorded in the lessons log, confirming the page says exactly what the code checked and no more. | M | - |

**Done when.** Every product flag has a date. No page restates a measurement another page owns. Four processes are documented and verified together.

## Phases and gates

### Phase 0: Decide and drain (days 0 to 10)

Deliverables: H1, H2, H3, H4, H5, I1, I5, J3, J4, J5, K3, K5, L4.

**Gate.** (1) ADR 0001 carries the readout and no ADR says "pending operator". (2) ADR 0011 names the centre. (3) The WIP check runs and open product-surface PRs are at or under eight (halfway). (4) Every `main` commit since the gate opened has its own CI verdict. (5) The source-scan count is derived. (6) Week 1 of the public record is committed. (7) Hypothesis 0001 is registered with its date.

### Phase 1: Spine and speed (weeks 2 to 6)

Deliverables: I2, I3, I4, J1, J2, J6, J7, K1, K2, K4, K6, L1, L2, L3, L5.

**Gate.** (1) Open product-surface PRs at or under four. (2) Test gate under 15 minutes on CI. (3) New evaluation rows carry four stamps and land in the ledger. (4) One interval utility, with a ratchet listing the holdouts. (5) The sleeve's evaluator is sealed. (6) Every product flag has an owner and a date.

### Phase 2: Price and attribute (weeks 6 to 12)

Deliverables: K7, L6, and the September items this plan did not restate (A4 replay and attribution, B6 loop health, D3 monitor gap, E4 typed read tools, G9's first strangler slice), in the order the September plan gives them.

**Gate.** The September Phase 1 gate, re-read against the ledger rather than against prose.

## First two weeks, in order

1. **H1 and H2, one operator session.** Half a day to read production, two hours to decide. Everything below either needs a decision or is cheaper once one exists.
2. **J3 and J4, one platform PR.** Serialized merges and a derived source-scan count stop the two taxes every other PR pays. Both are small and neither waits on a decision.
3. **I1, the triage table.** One agent, one afternoon, one comment per open PR. Then I5, the backlog file, so a closed branch is a line rather than a loss.
4. **K3 and K5.** Register the sleeve and commit week 1. Both are files; both start the clock the September plan said to start.
5. **H4 and H5.** Name the centre, set the WIP limit, and begin landing the queue under it.
6. **J1.** Measure the test gate before parallelising it.

## KPIs

| Metric | 2026-09-29 | 2026-10-24 | Target | How measured |
|---|---|---|---|---|
| Autonomous live orders without an eligibility record | unknown in production; flag-only barrier | 0 by code; D2 at the Lock-5 mint, no record ships | 0, and the readout confirms it | compliance audit rows; H1 |
| ADRs decided | 0 of 10 | 0 of 10 | 11 of 11 | `docs/adr/README.md` |
| Open product-surface pull requests | not measured | 16 (10 draft), 24 to 83 files each | at most 4, none over 40 files | GitHub API, weekly |
| `main` commits with their own CI verdict | not measured | 54 of 60 runs (6 cancelled) | 100% | Actions API, rolling week |
| Test gate on CI | 31m39s | 41m04s | under 15 min, then 12 | step timings |
| Re-record commits | 5 manual re-records in 6 days | 13 of 151 commits | 0 per month | `git log` subject grep |
| Evaluation rows with four identity stamps | 0% | 0% | 100% of new rows | ledger query |
| Registered hypotheses | 0 | 0 (one pre-registration in prose) | every open hypothesis, each with MDE, `n_eff` and a date | `benchmark/hypotheses/` |
| Interval computations outside the one utility | all | all | 0 new, listed holdouts shrinking | K4 ratchet test |
| Weekly public record weeks committed | 0 | 0 | every ISO week since the first | `benchmark/record/` |
| Off-by-default product flags without an owner and date | not measured | all of them | 0 | L1 inventory section |
| Dependency advisories found on a merge rather than on the schedule | not measured | 1 red `main` run in 60 | 0 | Actions API |
| God-file size | 5,685 lines (June) | `live_executor.py` 18,071 | not growing; first strangler slice in Phase 2 | `wc -l` ratchet |
| Capability commits on the money core or its evidence | about 4.5% | not classified | at least 30% | monthly PR-label classification against H4's centre |

## Stop doing

- Stop opening a product-surface pull request while four are open. Stop opening a stack of four PRs for one feature in one evening.
- Stop treating "flag off by default" as a reason to merge. A flag is a date, an owner and a measurement, or it is a deletion.
- Stop quoting a count in prose that a test re-derives. Stop committing "Record the source-scan count" as a step of every PR.
- Stop pushing empty commits to retrigger a workflow. Fix the trigger or wait for the run.
- Stop merging onto a `main` whose previous commit has no verdict.
- Stop adding a chain to the anchor set, a category to the Duel, or a language to the Pi app before the one evidence page reads one interval.
- Stop writing the operator's decision into a commit message ("the owner's call") without an ADR line. Two such calls (live is admin only; chat on Haiku 5.5) are in the log and in no ADR.
- Everything the September plan's "Stop doing" list says still holds.

## Decisions the owner needs to make

1. **The product's centre (H4).** (a) Copilot plus validated core, Duel and Pi as acquisition. (b) Proof-and-duel family as the product. (c) Both at equal priority. *Recommendation:* (a) unless the Duel family has a measured retention figure by the Phase 0 gate, in which case (b) is defensible and the engine becomes its evidence source. (c) is what the last 25 days were, and this plan exists because of it.
2. **ADRs 0002 to 0010.** Take the recorded defaults unless the readout contradicts one. The September plan's recommendations stand; the only new information is the as-live reading (PF 0.85), the LIVE_UNIVERSE finding (226 of 249 exits on unmeasured markets) and the funding study, all of which support research mode at the venue minimum.
3. **The work-in-progress limit (H5).** Four, six or none. *Recommendation:* four. The queue today is sixteen; six would ratify it.
4. **Merge queue or no cancellation (J3).** GitHub's merge queue needs a plan that allows it; dropping `cancel-in-progress` for `main` pushes costs runner minutes and nothing else. *Recommendation:* merge queue if available, otherwise the drop.
5. **The sixteen open PRs (I4).** Which stacks survive H4. *Recommendation:* land SaR adoption and the Recursion Breaker look-through if green and under 40 files; fold the Kink Cap, Time-to-Cash and fair-value bridge stacks into one backlog line each until the centre is named.
6. **The 60-day kill rule (L2).** Keep, lengthen, or none. *Recommendation:* keep. A flag that cannot earn a measurement in 60 days is a deletion the repo is paying to carry.
7. **Who computes the 2027 sleeve reading (K6).** The pre-registration says "someone who has not looked at the rows". Name the role now and seal the evaluator, or the reading will be whoever is present.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The operator session does not happen, and the plan stalls on the same critical path as in September. | Every ADR gets a default and a date (the "operator's queue is a budget" principle). Phase 0 items that need no decision (J3, J4, J5, K3, K5, I5) start on day 0 regardless. |
| Draining the queue loses work people care about. | Nothing is deleted: a closed branch keeps its ref and gets a backlog line with a reason and a date. A split PR keeps its tests. |
| Parallelising the test gate surfaces order dependence. | J1 measures first; J2 runs as a non-blocking shadow until it has matched the serial verdict for a week; flake re-runs stay serial. |
| Deriving the source-scan count weakens the guard it carried. | The test keeps asserting the shape of the walk (which files, which exclusions, why), which is the claim that mattered; the integer was a proxy for it. |
| The WIP limit slows the Duel and Pi family that is the only thing with users. | Money-path, CI, security and dependency PRs are exempt. Four concurrent product PRs is more than one person can review anyway. |
| The ledger adds latency or REST load on the scan path. | Batched off-thread writes that fail open with an audited drop count, as the September plan specified; the drop count is on the loop-health card. |
| A trust claim on a page (anchor quorum, proof bundle, guard) overstates what the code checked. | L6 reviews each claim once, and the admin-preview banner's reading is reused for "lab" labels so a page cannot claim more than its flag state allows. |

## How this plan was produced, and what it cannot see

Read-only survey from a shallow clone (240 commits) of `main` at `64e91f2`, the GitHub API for pull requests, issues and the last 60 CI runs on `main`, the September plan and its ADRs, the lessons log's chapter list, the research pre-registrations, the ratchet baselines and the eligibility README. Commit bodies were read for the money-path, evidence and owner's-call commits named above.

It cannot see production: which flags are on, how many users each surface has, whether the four processes are up, what the live book holds, or whether any live order has been placed since the LIVE_UNIVERSE gate landed. That is ADR 0001's readout, and it is the first item in this plan for the same reason it was the first item in the last one.
