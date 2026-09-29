# RUNECLAW improvement plan: from a well-guarded engine to a top-tier AI trading copilot

*Prepared 2026-09-29 on branch `ccr-d840684f-sxcbdm` at `2f22b93`. Read-only survey; no code was changed.*

*Line citations are to `2f22b93` and are not kept current; they will drift. The pull request that adds this file also lands four small Phase 0 changes: the candle cache key, the `/deepscan` push, chat's share of the LLM budget, and the eligibility record at the Lock-5 mint. Each is narrower than the item it starts; the pull request says how.*

## Bottom line

The safety and honesty work in this repo is excellent. The problem is what it protects. On the recorded evidence the engine does not make money after fees, and no signal, voter, pattern or preset has a measured edge. Meanwhile about 95% of recent engineering time goes to repair and bookkeeping rather than new capability.

The plan, in order:

1. **Contain capital in code.** No autonomous live order unless a committed evidence file for the exact strategy version says it survived a test on data that did not exist when the idea was written. Everything else is denied by default.
2. **Measure what live actually trades.** One idea ledger that records every analysis (including refusals) with its outcome in net R, a benchmark that replays live's real decisions, and a cost model.
3. **Look for edge where it can be found.** Funding carry and market-neutral cross-sectional factors first; single-name direction is barely testable at this universe size.
4. **Rebuild the chat copilot on read-only, typed tools with an eval suite**, and make signals and patterns show their measured record in R with honest intervals.
5. **Restore developer speed.** CLAUDE.md from 1.3 MB to under 25 KB, a parallel test gate under 12 minutes, merge queue, derived citations.

If nothing survives testing, the plan still succeeds: capital stays at research size, the LLM stays an analyst and narrator, and the money core pivots to whatever does survive.

## What git and the code show today

- **History (shallow clone, 2026-09-24 to 09-29):** about 213 commits in 6 days, 174 non-merge. Roughly 68% correctness or honesty fixes, 27% bookkeeping (re-recording benchmarks, regenerating blocks, moving line citations), and about 4.5% new capability. 111 of 172 commits touched CLAUDE.md; 72 touched `docs/INCOME_MAP.md`, mostly to re-point line numbers. Authors: 145 Claude, 15 deploy bot, 10 Cursor, 7 MuleRun, 1 human.
- **Size:** `bot/` is 190k lines in 379 files; tests are 274k lines in 1,188 files (17,158 test functions, about 488 files that scan source text). `live_executor.py` is 16,282 lines, `engine.py` 10,576, `analyzer.py` 5,674, `risk_engine.py` 5,244. The largest functions: `_evaluate_locked` 1,846 lines, `_close_position_inner` 1,114, `_confirm_trade_inner` 1,006, `_handle_message` 977.
- **Web:** `app/public/js/dashboard.js` is 10,459 lines (697 KB), unbundled, 195 fetch sites, 23 views; 90 route files and 279 handlers.
- **CI:** one serial test process, 31m39s of a 34m job; local preflight about 45 minutes; no pytest-xdist, no test tiers.

## Diagnosis

- The engine loses money after costs, no signal has a measured edge, and the recorded intervals are narrower than the data supports. Benchmark of record (benchmark/majors_1h/result.json, code b8c36630, --honest, 5x): 129 trades, PF 0.63, 1 of 6 folds profitable, mean OOS -2.40%. Idea direction over 8,510 v2 ideas: +0.03 ATR [-0.09, +0.14] at 24h. None of 37 voters replicates. POC-retest is +0.03R on v2 and -0.39R on v3. vwap_reversion failed its pre-registered cell. All four presets have PF below 1, and live 40-close windows run PF 0.4-0.6. The idea intervals cluster by (dataset, symbol, ISO week) (scripts/signal_edge.py:413). Yet non-overlapping 24h returns correlate 0.71 across the v3 majors, 0.57 across the v3 alts and 0.79 across the v2 majors, design effects of roughly 7, 5 and 8, which signal_edge's own voter docstring warns about (:227-230). Zero is also the wrong null. The benchmark round trip (0.06% fee plus 0.05% slippage per side, 0.22% total) is 0.17-0.41 of a median 1h ATR on the v3 symbols, so a gross excess below that loses money before funding.
- No clean holdout is left, and single-name direction is barely testable at this universe size. v1 (2025-10-27 to 2026-07-04, the Rounds 1-4 tuning window) sits inside v2 (2025-02-21 to 2026-07-06). v3 (2026-05-22 to 2026-09-24) overlaps v2 by about 6.5 weeks, and its fresh ~11.5 weeks have already been read by the vwap_reversion cell, the all-ideas excess, the per-voter test (harmonic included), POC-retest's 81-cell grid and the momentum continuation. Confirmation can only come from data dated after a hypothesis is registered, and nothing schedules that data today. Rough power arithmetic, to be recomputed in C1: the SD of a 24h move is about 4 ATR(1h), so detecting a 0.3-ATR net excess at 80% power needs about 1,500 independent 24h windows. At the design effects above, 10-20 correlated symbols supply only one or two independent windows a day. Market-neutral carry and cross-sectional factors have far less correlated residuals, so they are the candidates that can be confirmed within months.
- The strategy that is measured is not the one that trades, the labels are contaminated, and the research rows are censored. The backtest runs the rule engine with order_flow=None (bot/backtest/engine.py:477-483), no smart exits, a flat 0.05% slippage and unpriced funding, over 10-21 fixed crypto perps. It fills change_pct_24h with a 1-bar change and volume_usd_24h with base volume (:1426-1460). Live scans the top 200 movers by 24h change, TradFi included (config.py:900, 2673), with an LLM/rule direction mix whose split is unmeasured (analyzer.py:4022-4136). Decision rows store confluence_score=idea.confidence and log live trades as mode='paper' (engine.py:8919-8943; experience.py:58). Aborts, practice fills and manual tickets train the learners. Engine signals publish pattern=None with no signal type, strategy type or timeframe (website_sync.py:494-512). 182 of 211 exits on the 2026-09-28 parity card were ticker-priced. analyze() returns below the 0.60 floor before any stop or target exists (analyzer.py ~1822 vs 1910), and signal_edge keeps only non-None ideas, so every confidence and floor study is truncated. idea.id is 'TI-' plus 8 hex digits (32 bits, models.py:94), too short to key a never-pruned ledger of every evaluation.
- Unmeasured numbers are shown as probabilities, and untested adjusters move live confidence. All 34 pattern 'confidence' values are hand-set constants. The pattern_watch push fires on them: the symmetrical triangle's fixed 0.60 clears its 0.6 bar. /deepscan rebuilds the public Setups panel with dir = LONG if RSI<50 or chg>0, a fabricated 'Vol 2.5x' and a 2%-of-price ATR (skill_registry.py:4155-4165). ADAPTIVE_CONFIDENCE is on, needs only 5 samples and keys on the sign of dollar P&L (config.py:1815; engine.py:7430-7465). Setup expectancy applies on a count floor, because may_apply never reads validate_oos (setup_expectancy.py:411). Nobody has tested whether confidence ranks outcomes; the only reading is +1.78 per trade, 95% CI [-0.96, +4.51], over 903 OOS trades.
- The capital posture in production is unknown, the autonomous path has two doors, and position protection is bound to the loop. Code defaults put AUTO_CONFIRM_LIVE_ENABLED on at a 0.85 bar with a $1 LLM budget (config.py:2553-2557, 1300). The documented install sets 1.0 / false and $5 (.env.example:197-198, 380), and what production runs is not in the repo. Autonomous confirms leave through the tick's _auto_confirm_batch (engine.py:9654) and through the /forcescan loop, which calls confirm_trade(tid, user_id='auto') directly (engine.py:9240-9275). The only barrier both cross is the Lock-5 mint (engine.py:8397-8420), and it fails closed only while that flag is off. The positions pass runs after scan and a 300s-capped analyze (engine.py:6368). No pass lock exists, the analyzer does its CPU work on the event loop (no to_thread), live symbols are not on the WS feed, there is no private order stream, venue quantity is never reconciled, and PRE_CAP_TIGHTENS_CAP is empty (risk_engine.py:162). LivePosition has 16 undeclared attributes. live_executor.py has 26 direct .status writes (34 across bot/), and outcomes are classified by substring (live_executor.py:397; confirm_result.py).
- Chat is a regex front door in front of a narrow, read-only model, and it shares the trading engine's budget. There are 95 rules over 51 intents, about 44.6k characters of regex, plus 15 Node intercepts. A 41-phrase probe got 3 confident wrong cards at confidence 1.0; for example, 'what's open interest doing on eth' went to the positions card. The model has 17 read-only tools against 133+ commands. There is no eval and no feedback, and the only routing audit is in dead code. The chat guard compares the shared cost and call snapshot against the one budget (telegram_handler.py:2947-2951). The analyzer falls to RULE_ENGINE_BUDGET off the same snapshot (analyzer.py:4118-4136), so a busy chat day moves the engine to rules-only. cost.py books claude-sonnet-5 at $3/$15 against the current $2/$10, and every claude-opus-* at $15/$75 against the current $4-5/$20-25. The request helper sends no thinking or effort setting for Sonnet ids (provider.py:1689-1700) and turns a refusal into a RuntimeError that ends the turn (:1491, 1926). The cached system block carries live data, so the cache never hits. History is a 9-message window with a 200-user LRU.
- The website and product layer break in the first minutes of use. The candle cache key omits limit (app/routes/market.js:127-134), so the Markets chart collapses to 2 bars about 20s after load. dashboard.js is 10,459 lines, unbundled, with 195 fetch sites. There is no per-signal page. 'What works' shows win rate without R, and the calibration breakdown is computed but never rendered. A self-admitted paper user's first Confirm dead-ends because PAPER_SIM_OPT_IN_ENABLED=False and the bot says LIVE-ONLY (config.py:2587; engine.py:8452-8463). The watchlist never reaches the engine or the Telegram alerts. There is no billing, one 'demo-user' compliance profile covers every confirm, and withdraw-enabled keys are stored with only a warning.
- About 95% of engineering throughput is repair and bookkeeping. Of about 174 non-merge commits since 2026-09-24, roughly 68% are correctness or honesty fixes, 27% bookkeeping and 4.5% capability. CLAUDE.md is 1,324,924 bytes (about 330k tokens), is injected into every agent session, and was touched by 112 of those commits. INCOME_MAP was touched by 73, mostly to re-point about 420 line citations. The test gate is a single serial 31m39s process. Parallelism is blocked by the repo-anchored state_path, the shared data/ wipe and about 305 object.__setattr__(CONFIG...) sites in about 100 test files. The benchmark was re-recorded by hand 5 times in 6 days. Functions of 1,846, 1,114 and 1,006 lines force source-scan tests. Backups are tarred to the same box.

## What "top of the line" means for this product

For RUNECLAW, top of the line means a trading copilot people can trust, running on a money core that takes risk only where evidence earned in advance supports it. It has seven properties. (1) No autonomous live order, and no size above a research notional, unless a committed eligibility artefact for the exact strategy_hash that is running says 'survives'. That artefact is earned on data dated after the hypothesis was registered, net of modelled per-symbol costs and funding, with time-clustered intervals and stated power. Every other state is denied by default, in code, at the one chokepoint all non-human live confirms cross. (2) The benchmark replays the decisions live actually made (recorded LLM/rule source, recorded order flow, smart exits, the live candidate universe), so losses and any wins can be attributed to signal, exits or execution. (3) One never-pruned Idea Ledger, keyed by a 128-bit analysis id, holds every evaluation, including refusals with their would-be levels, labelled in net R from OHLCV. Outcomes, scoreboards, pattern tables, calibration, learners and chat tools are all views over it. (4) Open positions are checked within seconds whatever the scan load, on an event loop whose lag is measured. Outcomes are typed, position state is declared, and venue quantity is reconciled against the book. (5) A chat agent answers from typed, read-only, ledger-backed tools, with as_of, source and sample on every figure. Its only way to create a ticket is a draft the user stages with a button, and every change to it is scored by an eval. (6) Web and Telegram show each call's reasoning, what happened afterwards and its measured record in R with honest intervals. A result is labelled 'exploratory' until a registered cell replicates on new data, and 'unmeasured' where nothing was measured. (7) A CI loop under 10 minutes and an agent guide under 25KB, so at least 30% of throughput goes to edge and capability. If nothing survives, the plan still succeeds. Capital stays at research size and the LLM stays the analyst and narrator. The money core pivots to whatever does survive, most plausibly funding carry or a market-neutral book, whose effective sample sizes are far larger than single-name direction. Every refutation or 'inconclusive' is recorded as carefully as a pass would be.

## Principles

- Default-deny capital. No autonomous live order, and no size above the research notional, unless a committed eligibility artefact for the running strategy_hash says 'survives' and grants a stage. Missing, unreadable, mismatched, thin, straddling or inconclusive all deny, in code, at the one chokepoint every non-human live confirm crosses. Config flags are the second line of defence.
- Confirm only on data that did not exist when the hypothesis was written. v1, v2 and v3, and every window already read, are discovery data. Prospective partitions stay sealed until their evaluation date, and the strategy under test is frozen while it accrues.
- One inference utility. Intervals are time-clustered, resampling whole days or weeks across all symbols with at least 20 clusters. Anything monitored continuously uses an anytime-valid test or fixed evaluation dates. No iid or symbol-week interval appears in a gate or on a public surface.
- Test against cost, with stated power. Primary endpoints are net of per-symbol cost and funding and are compared against baselines. Each hypothesis states its MDE, required effective n and equivalence margin. A verdict is survives, refuted (upper bound below the margin) or inconclusive, and inconclusive is never read as refuted.
- Budget multiple testing. Money gates control the family-wise error rate and display labels use BH-FDR. Only pre-registered cells that replicate prospectively may be called 'survives'. Any selection among variants reports PBO and a deflated Sharpe, using the registry's trial count.
- Measure decisions, not placed trades. Replay what the engine decided, labelled from OHLCV plus modelled costs, over the candidate universe live actually saw. Under research mode, placed trades are human-selected minimum-size fills and say little about the engine.
- One dataset, one key. The Idea Ledger, keyed by a 128-bit analysis id, holds every evaluation, including refusals with their would-be levels. Every outcome store is a view over it, and nothing is pruned.
- Removing an unvalidated input needs no proof; adding or restoring one does. Defaults move toward fewer claims (LLM weight 0, no confidence percentage, adjusters in shadow) until evidence earns them back.
- Unreadable is never zero and unmeasured is never a percentage; enforce this with types and at boundaries. No dollars on public surfaces: percent, R and counts only.
- Derive, don't hand-keep: registries, schemas, gate lists, citations, ratchet totals and outcome kinds come from code.
- Chat reads, humans act. Every chat tool is read-only. The only state change chat can start is a draft the user stages with a button, in reply to their own request. Text inside a tool result is never a trigger, and every chat change is eval-gated.
- Every new money-path component ships with a kill flag, persisted formats the previous build can read, audit-only mode before enforcement, and replay fixtures for the calls it touches. One stage per PR.
- Velocity is a safety and research input. Fast CI, a lean agent guide, and operator decisions recorded as ADRs rather than prose. Filed items get an owner, a status and a blast radius.

## Workstreams

Effort: S is a day or two, M is under a week, L is one to three weeks, XL is longer. IDs are stable so phases and dependencies can refer to them.

### A. Parity: measure the decisions the bot actually makes

**Goal.** A replay reproduces live's recorded decisions over live's candidate universe with a per-symbol cost model, so edge and losses can be attributed to signal, exits or execution.

**Why.** The backtest runs the rule engine with order_flow=None (bot/backtest/engine.py:477-483), no smart exits, flat 0.05% slippage and unpriced funding (bot/backtest/funding.py:28-31). It fills change_pct_24h with a 1-bar change and volume_usd_24h with base volume (engine.py:1426-1460), and that field feeds the volume bonus and the volume-spike voter (analyzer.py:1557, 3277). Live scans the top 200 movers by 24h change, TradFi included (config.py:900, 2673; market_scanner.py:336-347). The LLM/RULE_ENGINE_* share is unmeasured, and llm_calibration rows carry no idea id (analyzer.py:1505-1528). funding_clock hard-codes an 8h settlement (funding_clock.py:28). Under research mode, placed trades are human-selected, so parity must be measured on decisions.

**Done when.** The replay reproduces at least 95% of recorded live decisions. Backtest and live MarketSignal fields are equal by test. Every C and D2 artefact is priced by the A7 cost model. At least 90% of live closes are venue-priced. An attribution artefact is committed.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| A1 | Identity stamps: 128-bit analysis key, build and strategy fingerprints, counterfactual geometry | M | - |
| A2 | Signal-construction parity as a parallel benchmark arm | S | C1 |
| A3 | Model live smart exits in the backtest | M | - |
| A5 | Live candidate-universe log | S | - |
| A6 | Measure live close pricing after the v3 history-stage fix | S | - |
| A7 | Execution cost model v1 | M | - |
| A4 | Live-decision replay and attribution | M | A1, A3, A5, A7, B3 |

**A1 · Identity stamps: 128-bit analysis key, build and strategy fingerprints, counterfactual geometry** (M)

Mint analysis_id (a ULID, 128 bits) per Analyzer.analyze call as the ledger key. Keep idea.id (32 bits, limited by Telegram's 64-byte callback_data) as a foreign attribute. Carry analysis_id into llm_calibration.jsonl, order-flow snapshots, decision rows, signal payloads (outside the sealed payload) and flight records, and remove the flight recorder's 32-character analysis_version trim. Replace the hand-typed _ANALYSIS_VERSION with code_sha plus a cfg_hash computed over a secret-free, path-free projection of CONFIG, so rows from the bot box and the research box join. Add a strategy_hash over the strategy-relevant subset (analyzer, sizing and exit config plus their code paths); D2 and C12 key on it. Fix decision rows: mode is live or paper, confluence is the real confluence, and add entry regime, signal and strategy type, htf_trend, timeframe, llm_source and llm_confidence. For evaluations refused below the floor or by a gate, compute the would-be SL/TP and emit a non-registered record, so labels are not censored at the floor. Publish the daily LLM vs RULE_ENGINE_* share. Rows written before A1 join by nearest timestamp, are labelled approximate, and never feed a gate.

- *Where:* bot/core/analyzer.py:93, ~1805-1822 (floor), 1910 (SL/TP), 1505-1528; bot/core/engine.py:7668-7686, 8919-8943; bot/learning/experience.py:58; bot/guardian/flight_recorder.py:168; bot/utils/website_sync.py; bot/utils/models.py:94; new bot/utils/build_identity.py
- *Success metric:* A guard test finds the same analysis_id, code_sha, cfg_hash and strategy_hash in all sinks. cfg_hash is identical on two boxes with different secrets and paths. 0 live rows are logged 'paper'. Below-floor evaluations carry would-be SL/TP.

**A2 · Signal-construction parity as a parallel benchmark arm** (S)

Build the backtest MarketSignal from live's definitions: 24-bar change, 24-bar quote volume, and the scanner's own volume-spike rule. Add a property test that feeds the same candles through both builders. Record this as a PARALLEL arm next to the current benchmark, not a replacement. Replace the benchmark only after the C1 hypotheses have been re-registered against the new field meanings, so a registration never straddles a change of measurement basis.

- *Where:* bot/backtest/engine.py:1426-1460; bot/core/market_scanner.py:782-797; bot/core/analyzer.py:1557, 3277; benchmark/*/result.json; docs/FROZEN_BENCHMARK.md
- *Success metric:* The property test passes for every field the analyzer reads. The parallel arm artefacts are committed with the delta and its cause, and the benchmark of record is unchanged until the re-registration ADR.

**A3 · Model live smart exits in the backtest** (M)

Call the live time_exits and smart_exits modules inside the backtest bar loop, not a copy of them: hold limit and twice the limit, no-progress, VWAP reversion, volume decay.

- *Where:* bot/backtest/engine.py; bot/core/smart_exits.py:240-246; bot/core/time_exits.py
- *Success metric:* A planted trade exits on the same bar with the same reason in the backtest and in a live-path drive.

**A5 · Live candidate-universe log** (S)

Record, per tick, the ranked candidate list and which candidates were analysed. The replay and any universe benchmark evaluate only those (symbol, tick) pairs. They never evaluate every bar of symbols that later became movers, because that selects on future mover status. Freeze OHLCV for those symbols with a manifest.

- *Where:* bot/core/market_scanner.py:336-347; bot/core/engine.py (tick); bot/backtest/snapshot.py
- *Success metric:* At least 4 weeks of candidate logs exist, and the replay refuses (symbol, tick) pairs outside them.

**A6 · Measure live close pricing after the v3 history-stage fix** (S)

Show the venue-priced share of the last 50 closes, with a cause breakdown, on /parity and the loop-health card. Alert below 90%.

- *Where:* bot/backtest/parity.py; bot/core/close_lookup.py; bot/core/proactive_monitor.py
- *Success metric:* The production number is recorded. At least 90% of recent closes are venue-priced, or the root cause becomes the top D item.

**A7 · Execution cost model v1** (M)

Model per-symbol spread and impact from the recorded order-flow book snapshots and from live fills (implementation shortfall against decision price, by liquidity bucket). Read per-symbol funding intervals from the venue instead of the hard-coded 8h; many alts settle every 4h or 1h, and Hyperliquid settles hourly. Put funding into the market label, and report cost in R and in ATR per symbol. Research-mode fills are at venue-minimum size, so impact at larger size is extrapolated from book depth and labelled as a model, never as a measurement. Every C endpoint and D2 artefact stamps the cost-model version.

- *Where:* bot/core/trade_costs.py; bot/risk/funding_clock.py:28; bot/core/order_flow.py; bot/backtest/engine.py (flat 0.05% slippage); new bot/learning/cost_model.py
- *Success metric:* A per-symbol cost table is committed. The backtest has a parallel arm with per-symbol cost and priced funding. 100% of C artefacts carry a cost-model version.

**A4 · Live-decision replay and attribution** (M)

Replay the engine's recorded DECISIONS: every analyzed idea and its gate verdict from the ledger, with the recorded LLM thesis and order flow, over the A5 candidate pairs only. Label them from OHLCV using the A7 cost model. Placed trades are not the unit of comparison. Report three things: decision reproduction (same direction and gate verdict), per-idea net R in the replay against live's own market labels, and an attribution table (signal vs exits vs execution gap from realized fills). The artefact is pinned to strategy_hash and the dataset hash.

- *Where:* bot/backtest/runner.py:668-671, 1129-1145; bot/backtest/recorded_llm.py; bot/backtest/recorded_order_flow.py; new benchmark/live_parity/result.json
- *Success metric:* At least 95% of recorded decisions are reproduced in direction and gate verdict, with every mismatch itemized with its cause. The attribution artefact is committed.

### B. One Idea Ledger and learning-loop hygiene

**Goal.** One append-only, never-pruned dataset keyed by analysis_id. Every evaluation, refusals included, carries OHLCV market labels in net R. Learners act live only after a fixed-date prospective evaluation passes, and they train only on engine rows.

**Why.** Four surveys each proposed a separate store. Outcome rows carry only symbol, direction, regime-at-close and net dollars (experience.py:98-128). Aborts, practice fills and manual closes feed setup expectancy and the adaptive nudge (setup_expectancy.py:193-203). The adaptive nudge is on and untested (engine.py:7430-7465). Refits run every 25 closes with no champion/challenger (auto_refit.py:47-79), and re-evaluating a fixed 95% bar on accumulating data eventually passes a null adjuster. Signal-outcome labels are pruned on the bot at 14 days or 2,000 rows (signal_outcomes.py:102-104). The labeller walks each pending row every 15 minutes (engine.py:6441-6476), which is sized for tens of rows a day, not 10k+ evaluations.

**Done when.** Every engine learner reads ledger views trained on market labels. 0 live adjustments come without a fixed-date prospective pass. At least 95% of evaluations are labelled, refusals included. No second outcome store exists.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| B1 | Label hygiene; learners move to market labels | S | - |
| B2 | Every live confidence adjuster in shadow until a fixed-date evaluation | S | - |
| B3 | Idea Ledger v1: the one feature and label store | L | A1, B1, C12 |
| B4 | Rebuild existing ledgers as views, with dual-run and backfill | M | B3, C11 |
| B5 | Learner registry: fixed-date champion/challenger, rollback, retire dead scaffolding | M | B3, C11 |
| B6 | Loop-health monitor | S | B1, A1 |

**B1 · Label hygiene; learners move to market labels** (S)

record_closed_outcome stamps fill_source, fee_basis, close_reason, is_execution_abort, net R, producer/basis, user_id and the entry regime taken from the decision row. Every engine learner excludes aborts, practice/paper, manual/inherited-basis and per-user rows now. Once B3 lands, every engine learner trains on OHLCV triple-barrier market labels, which do not depend on how a close was priced. Realized fills are used only to estimate the execution gap. There is no outcome-dependent filter; the earlier 'drop ticker-priced closes near break-even' rule is dropped. AMBIGUOUS bars (one bar spanning stop and target) score stop-first for any money verdict, and the target-first bound is reported alongside. Bump SAMPLE_READING so existing fits refit, and print the exclusion counts on the readiness card.

- *Where:* bot/learning/experience.py:98-128; bot/learning/outcome_join.py; bot/learning/setup_expectancy.py:193-203; bot/learning/voter_weights.py; bot/learning/confidence_calibration.py; bot/core/engine.py:1533-1624
- *Success metric:* Guard tests show no abort, manual, practice or per-user row reaching any engine learner. After B3, 100% of engine-learner samples are market labels.

**B2 · Every live confidence adjuster in shadow until a fixed-date evaluation** (S)

ADAPTIVE_CONFIDENCE and setup expectancy both move to shadow: they log the would-be delta and apply nothing. Re-enabling either needs a pre-registered evaluation on a fixed date, on prospective ledger rows, using the C11 utility. READY is no longer recomputed at each refit, because repeated looks at a fixed 95% interval eventually pass a null adjuster. /calibration and the readiness card list all four adjusters, and every applied delta writes an audit row.

- *Where:* bot/core/engine.py:7430-7465; bot/config.py:1401, 1815-1852; bot/learning/setup_expectancy.py:411; bot/learning/readiness.py:313-340; bot/risk/confidence_floor.py:59-85
- *Success metric:* 0 live confidence changes from any adjuster without a passing fixed-date artefact, verified by drive and audit.

**B3 · Idea Ledger v1: the one feature and label store** (L)

bot/learning/idea_ledger.py on SQLite: WAL mode, batched off-thread writes, schema_version plus code_sha, cfg_hash and strategy_hash. Writes fail open with an audited drop count. It is append-only and never pruned, keyed by analysis_id, with idea.id as an attribute and one row per analyzer evaluation, refusals included. Features: scalars, (voter, vote, weight), entry regime, types, LLM direction, confidence and source, confluence, blend, stop distance, R:R, spread/funding/OI, session, producer and user basis, and the counterfactual geometry from A1. Four label families join by id. MARKET: triple-barrier net R on the idea's own or counterfactual SL/TP plus 1-48h excess ATR, MFE and MAE, net of A7 cost, with AMBIGUOUS bounds. GATE: verdict and failed checks. EXECUTION: fill_source, fee_basis, realized net R, funding. PROVENANCE: decision_id. Labelling is symbol-batched (one candle read labels every open idea on that symbol) or runs offline from recorded candles, sized for 10k+ evaluations a day; an optional 1m pass resolves AMBIGUOUS bars. Backups use the SQLite backup API; tar-copying a live WAL file can corrupt it. Account purge reaches per-user rows and the chat_drafts side table, and the pattern_events side table serves F12. Label partitions obey C12 sealing. Backfill runs `signal_edge collect` over every frozen snapshot, marked discovery. The website MySQL stays the published projection.

- *Where:* new bot/learning/idea_ledger.py; bot/core/signal_outcomes.py; scripts/signal_edge.py; bot/core/trade_costs.py; bot/core/engine.py:6441-6476; bot/utils/backup.py; purge path
- *Success metric:* At least 95% of evaluations are labelled within 8 days, refusals included. At least 30k backfilled rows per v2 snapshot. Research queries run in under 60s, and a per-id lookup takes under 5ms at 1M rows. Analyze p95 regresses by no more than 2%. A purge drive removes a user's ledger rows.

**B4 · Rebuild existing ledgers as views, with dual-run and backfill** (M)

signal_outcomes, shadow_book (fee-aware R, C11 intervals), parity, calibration samples, setup expectancy, the scoreboard feed and the /signals card become ledger views. Each runs beside its legacy store for at least 2 weeks with an equality check on the overlap. Live decision and closed-trade history is backfilled into the ledger before the switch, so calibration and expectancy do not reset. Each learner has its own switch-back flag. ladder_shadow and bounds_shadow rows get an analysis_id, so the cuts they would have made are scored in realized R.

- *Where:* bot/core/signal_outcomes.py; bot/core/shadow_book.py; bot/backtest/parity.py; bot/learning/setup_expectancy.py; bot/risk/ladder_shadow.py; bot/core/bounds_shadow.py
- *Success metric:* Every view equals its legacy output on the overlap window before its switch. One command reproduces the parity card and /api/signals stats. No learner shows a sample reset at the cutover.

**B5 · Learner registry: fixed-date champion/challenger, rollback, retire dead scaffolding** (M)

Every refit writes a versioned artefact (window, n, SAMPLE_READING, code_sha, metrics). A challenger is evaluated only at pre-set dates with alpha spending, or with an anytime-valid sequence, and is promoted only on a paired improvement whose lower bound on prospective rows is above 0. A /learning rollback command restores the previous artefact byte for byte. Refits move off the close callback via asyncio.to_thread. Delete or park strategy_eval, prompt_opt, model_compare and the boilerplate rejection reflections, and replace the per-idea full-JSONL re-parse with ledger queries.

- *Where:* bot/learning/auto_refit.py:47-114; new bot/learning/registry.py; bot/learning/reflection.py; bot/learning/store.py:141-145; tests/unreachable_methods_baseline.txt
- *Success metric:* No promotion happens outside a scheduled evaluation. Rollback is byte-identical. The unreachable-methods baseline shrinks by at least 10 learning entries.

**B6 · Loop-health monitor** (S)

A daily operator card with threshold alerts. It shows label coverage (refusals included), ticker-priced share, abort rate, unpriced closes, outcome mix by source, the LLM/RULE_ENGINE_* share, confidence PSI against the discovery ideas, and predicted-vs-realized reliability per regime and signal type.

- *Where:* bot/core/proactive_monitor.py; bot/learning/readiness.py
- *Success metric:* A ticker-priced share above 20% or an abort rate above 5% is alerted within 24h. The card reproduces from the ledger.

### C. Edge research program under a pre-registered, prospective, power-aware protocol

**Goal.** Find a signal, carry sleeve or market-neutral factor that survives a pre-registered prospective test net of modelled costs, or record clean refutations and 'inconclusive' results and pivot the money core. Every claim uses one inference utility and a multiple-testing budget.

**Why.** Every recorded edge measurement is null or negative, and the frozen windows are all burned (v1 inside v2; v3 overlaps v2 and its fresh tail has been read at least five times). Idea intervals cluster by symbol-week, although cross-symbol 24h correlation is 0.57-0.79. Tests use 0 as the null, while cost is 0.17-0.41 ATR. No hypothesis states power. Four harnesses still use --fetch. No funding, OI or survivorship-complete history exists (bot/backtest/funding.py:28-31). The carry tracker runs forward-only on 8 majors (arb_tracker.py:89-117). The LLM's direction and confidence's link to outcomes have never been scored.

**Done when.** Either a pre-registered strategy or sleeve survives its prospective test net of A7 costs at the money-family alpha, with n_eff at or above its requirement, covering at least 2 regimes (or labelled regime-conditional), with a positive C13 portfolio result; or every candidate has a recorded refuted or inconclusive verdict and the operator records the pivot.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| C1 | Hypothesis registry with power, cost-net endpoints and a testing budget | M | C11 |
| C11 | One time-clustered inference utility and sequential tests | M | - |
| C12 | Holdout sealing, prospective schedule and strategy freeze | M | - |
| C2 | Score the LLM's live direction against baselines | S | C1, C11, D0 |
| C3 | Confidence validity: provisional now, decisive on uncensored ledger rows | S | C1, C11 |
| C4 | Survivorship-complete perp panel (funding first) | L | - |
| C5 | Historical funding-carry backtest without look-ahead | M | C4, C1, C11, A7 |
| C6 | Cross-sectional / market-neutral factor harness | M | C4, C1, C11, A7 |
| C7 | Pattern instance metadata and an exploratory replay table | L | C1, C11, A7 |
| C13 | Portfolio-level evaluator | M | A1, B3, A7 |
| C8 | Expected-net-R meta model, in shadow | M | B3, C3, C11, C13 |
| C9 | Exit-shaper decisions from paired per-idea differences | S | B3, A3, C11 |
| C10 | Prune the voter stack (conditional) | M | C3, C7, C8 |

**C1 · Hypothesis registry with power, cost-net endpoints and a testing budget** (M)

benchmark/hypotheses/*.yaml. Each entry specifies: a primary endpoint net of per-symbol cost and funding (A7 model version) in R or ATR, never a gross test against 0; baselines (for any direction claim: the rule-engine direction on the same rows, sign(24h change) and its negation, always-long); MDE, required n_eff (from C11's design-effect estimate) and equivalence margin delta; a three-way verdict (survives: lower bound > 0 at the family alpha; refuted: upper bound < +delta; otherwise inconclusive); its family (FWER-controlled money family or BH-FDR display family); its data window as prospective dates (C12); the regime composition to report; and a looks counter that every run, CI included, increments. Declare v1/v2/v3 discovery-only in FROZEN_BENCHMARK.md. Any selection among variants reports PBO (CSCV) and a deflated Sharpe, with the registry's trial count. Port voter_ablation, robustness_suite and ab_flag to --dataset. llm_ab is not ported: its signal is recorded live theses, which C2 scores on live rows. A CI test requires every documented benchmark number to regenerate from a committed artefact.

- *Where:* docs/FROZEN_BENCHMARK.md; new benchmark/hypotheses/; scripts/voter_ablation.py:89-90; scripts/robustness_suite.py:113; scripts/ab_flag.py:94-95
- *Success metric:* Every open cell has a YAML entry with MDE, n_eff and delta, committed before its data is read. 0 harnesses use --fetch. CI fails on a documented number that has no artefact.

**C11 · One time-clustered inference utility and sequential tests** (M)

A pure-Python module, plus a JS twin for F3 pinned to the same outputs by a cross-runtime test. It resamples whole calendar days or weeks across all symbols together, needs at least 20 clusters, falls back to a t interval on per-period aggregates below that, and refuses to answer below a floor. It also provides anytime-valid confidence sequences (or mSPRT) for continuously monitored quantities (D2, D11, B2, B5, F3) and an effective-n and design-effect estimator for C1's power fields. It is the only interval function that ledger views, C harnesses, parity, F3 and the gates may call. A ratchet test forbids arb_tracker.mean_interval and symbol-week idea clustering on those paths. Restate the existing headline results with it (signal_edge h24, voter table, POC-retest, the parity live edge) and record the widened intervals in FROZEN_BENCHMARK.md.

- *Where:* new bot/utils/inference.py; new app/lib/inference.js; scripts/signal_edge.py:162, 227-230, 413; bot/core/arb_tracker.py:278-291; bot/backtest/parity.py:388-407
- *Success metric:* A planted perfectly correlated panel yields the width of a single series (drive). 0 gates or public surfaces call another interval function (ratchet). Restated headline intervals are committed.

**C12 · Holdout sealing, prospective schedule and strategy freeze** (M)

A registry-controlled access ledger. Snapshot fetches dated after a hypothesis's registration commit, and ledger label partitions after that date, can be read only by that hypothesis's registered evaluation command, on or after its evaluation date. Every other read is refused and logged as a look. A dated prospective fetch schedule starts in week 1: v4 at registration plus 4 weeks, then every 4 weeks, covering the discovery universes, the live candidate universe (A5) and funding. Each fetch is frozen with a manifest and hash. During a hypothesis's accrual window its strategy_hash is frozen; only fixes the replay proves byte-identical in decisions may merge, enforced by a CI check. CI never reads sealed data.

- *Where:* benchmark/hypotheses/; new scripts/research/seal.py; bot/backtest/snapshot.py; bot/learning/idea_ledger.py; .github/workflows/ci.yml
- *Success metric:* v4 is fetched on its date and each later fetch on schedule. 0 unlogged reads of sealed partitions. The strategy_hash freeze check blocks a non-identical change during accrual (drive).

**C2 · Score the LLM's live direction against baselines** (S)

Export the production llm_calibration.jsonl, with row counts known from D0, and join it to OHLCV fetched once and frozen. Measure the forward move from the next bar's open after the decision timestamp. The primary endpoint is excess net of per-symbol cost, with C11 intervals. It is compared, paired on the same rows, against: the rule-engine direction recorded beside the LLM direction, sign(24h change) and its negation (the live universe is ranked by 24h change, so a direction that tracks that sign inherits the mover effect), and always-long. Stratify by llm_source, by date before and after E1, and by model id. Count and bound rows dropped because a symbol has since delisted. The pre-registered action is asymmetric. Unless the LLM survives against the best baseline, its blend weight goes to 0, because removing an unvalidated input needs no proof. A survival on existing rows only nominates the LLM for prospective confirmation (C12). The thesis model id is pinned in the artefact, a model change (E7) resets the evidence clock, and no LLM is ever scored on history dated before its training cutoff. Power is reported, and 'inconclusive' is the expected result if LLM-sourced rows are few.

- *Where:* new scripts/llm_direction_edge.py; benchmark/llm_direction/result.json; bot/config.py:1328-1350
- *Success metric:* The artefact records n and n_eff per source and a three-way verdict against each baseline. The pre-registered action is applied and recorded as an ADR.

**C3 · Confidence validity: provisional now, decisive on uncensored ledger rows** (S)

Confidence gates entries (MIN_CONFIDENCE 0.60), sets the auto-confirm bar, is shown to users as a percentage, and feeds the sizing paths that read it (Kelly's confidence factor, plus the opt-in quality ladder and high-conviction margin). The backtest rows are censored at the floor (analyze returns before SL/TP at ~1822 vs 1910, and signal_edge keeps only non-None ideas) and carry the rule-engine blend rather than live's LLM blend with nudges. So in Phase 0 the decile and rung table, with C11 intervals on the backtest rows, is labelled provisional. The pre-registered default now: the displayed '% confidence' becomes a rung word or 'unmeasured', and confidence-scaled sizing stays off until validated. The decisive test runs prospectively on ledger rows with counterfactual geometry, below-floor included. The floor stays as a filter, and its value is evaluated through the counterfactual labels.

- *Where:* scripts/signal_edge.py:340-355, 453-480; bot/risk/quality_ladder.py:310; bot/config.py:435, 709; user-facing confidence renderers
- *Success metric:* A provisional table is committed and the default is applied (no '%' shown as a probability anywhere, guard test). The decisive test is registered against B3 rows with an evaluation date.

**C4 · Survivorship-complete perp panel (funding first)** (L)

Venue kline and funding endpoints generally do not serve delisted contracts, so a venue-API-only panel would contain survivors only. The chosen source must retain delisted USDT-M contracts. The obvious candidate is a public exchange data archive (for example Binance's); verify its coverage of delisted klines, funding and metrics before choosing, or buy a vendor. Build a point-in-time rolling-liquidity universe, a listing-age filter, 1000x redenomination handling and delisting settlement returns. Record realized funding with its settlement and publication time, and per-symbol funding intervals. Acquisition starts in Phase 0 with funding only, because it is I/O-bound and the long pole; OHLCV and OI follow, with OI also accumulated forward. Data lives outside git with hashed manifests in git. Backtest funding becomes priced.

- *Where:* bot/backtest/snapshot.py; bot/backtest/data_loader.py; bot/backtest/funding.py:28-31; bot/core/order_flow.py; new scripts/research/fetch_panel.py
- *Success metric:* The funding panel is frozen with a stated count of delisted contracts included. The full panel covers 150-250 symbols over at least 24 months. Any survivorship gap that remains is measured and printed on every artefact that uses the panel.

**C5 · Historical funding-carry backtest without look-ahead** (M)

The entry decision uses only rates known before the settlement: the last settled rate, or a predicted rate with its publication timestamp, never the rate about to be paid. The backtest includes cross-venue basis P&L at entry and exit, per-leg margin and liquidation risk on funding spikes, the capital split across two venues, and the spot-borrow cost for negative-funding perp-vs-spot legs. Start with single-venue perp-vs-spot if cross-venue history is thin. The entry threshold is a pre-registered grid scored with PBO, not the tracker's fixed 3% APR; that threshold needs about 29 days of persistence to cover 4 taker legs (about 54 with slippage), so a verdict on it is a verdict about the threshold, not about carry. Intervals are clustered by funding episode. Discovery runs on archive history. Prospective confirmation comes from the forward arb_tracker record, registered in C1 before it accrues further.

- *Where:* new scripts/carry_backtest.py; bot/core/arb_tracker.py:43-117, 294-346; bot/core/trade_costs.py
- *Success metric:* A discovery artefact reports net carry per unit of capital, an episode-clustered interval, n_eff and the worst drawdown across flip episodes. A registered prospective test is accruing.

**C6 · Cross-sectional / market-neutral factor harness** (M)

scripts/xs_factors.py runs over the panel: funding carry, basis, 7-28d momentum, 1-3d reversal, OI change vs price, realized vol and volume shock. It reports weekly IC/ICIR and quantile spreads with purged, embargoed splits and C11 intervals, plus turnover-aware net P&L of a beta-neutral book under the A7 cost model. MDE comes from the panel's residual correlation, and the net-Sharpe target is stated with its standard error (about 1 per year of data), so a miss is 'inconclusive' unless the upper bound is below delta. Selection reports PBO and deflated Sharpe with the registry's trial count. Confirmation is prospective.

- *Where:* new scripts/xs_factors.py; benchmark/hypotheses/
- *Success metric:* Every factor has a registered three-way verdict. At least one candidate is registered for prospective confirmation, or the refutations and inconclusives are recorded.

**C7 · Pattern instance metadata and an exploratory replay table** (L)

Each detector returns formed_idx, bars_ago and an instance_id (name plus key levels via record_level). A recency gate sits behind a flag and is judged on paired per-idea net R. scripts/pattern_replay.py reduces detections to formation events on the discovery snapshots and reports, per (pattern, timeframe): n, excess net of cost, C11 interval and a BH-FDR q-value. It assigns no 'survives' labels. Cells chosen from the table are pre-registered for prospective confirmation. bot/core/pattern_history.py is a 4-state reader copied from poc_retest_history.

- *Where:* bot/core/chart_patterns.py:1789-1840; bot/core/analyzer.py:351-403, 3406-3643; bot/core/liquidity_sweep.py; bot/core/divergence.py; new scripts/pattern_replay.py; new bot/core/pattern_history.py
- *Success metric:* Every detector, sweep and divergence has an exploratory row. With the gate on, a 150-bar-old pattern casts no vote. Only registered cells can later carry a verdict.

**C13 · Portfolio-level evaluator** (M)

Replays a decision set through the risk engine's sizing, concurrency caps, notional-cap binding and correlation groups. It produces book-level net P&L, the drawdown path and turnover under A7 costs. Per-idea mean net R is not portfolio return: at defaults the notional cap binds below roughly 1.5-3.1% stops (risk_engine.py:162, 1601-1602, 1931). C8, D2 and D11 read this output.

- *Where:* new scripts/research/portfolio_eval.py; bot/risk/risk_engine.py (sizing, read-only use)
- *Success metric:* The evaluator reproduces the benchmark's book P&L on a replayed decision set within rounding. Every D2 artefact carries its portfolio result.

**C8 · Expected-net-R meta model, in shadow** (M)

Fit E[net R | signal_type, entry regime, direction, confidence band, top features] on ledger market labels, including counterfactual rows. Use empirical-Bayes shrinkage and purged, embargoed CV, with PBO and deflated Sharpe over the model search. It runs as a shadow entry filter. Live application needs an absolute criterion: on prospective rows, the kept set's mean net R has a time-clustered lower bound above 0 at the money-family alpha, and the C13 portfolio simulation of the kept decisions is positive. AUC and kept-vs-refused are diagnostics only. Research dependencies live in a [research] extra the runtime never imports.

- *Where:* new bot/learning/net_r_model.py (inference only); scripts/research/; pyproject [research] extra
- *Success metric:* A registered prospective evaluation with a fixed date. No live application without the absolute criterion (test-pinned).

**C9 · Exit-shaper decisions from paired per-idea differences** (S)

For ELLIOTT_FIB_TARGETS, ELLIOTT_MTF/MTF_ALIGNMENT and LEVEL_AWARE_SLTP, replay the same ideas with and without each shaper and compare net R per idea. When the difference is inconclusive, default to the simpler exit. Count the ideas whose R:R approval depended on the Elliott TP extension.

- *Where:* bot/config.py:1553-1586; bot/core/analyzer.py:142-180; scripts/research/
- *Success metric:* A committed per-shaper paired table and a keep/dark decision for each, with the simpler exit chosen on inconclusive results.

**C10 · Prune the voter stack (conditional)** (M)

Triggered only if C3, C7 and C8 produce a keep list. Cut voters that have no measured contribution, split trend and mean-reversion into regime-conditioned sub-models, and delete dead knobs and mode boosts. Ship in shadow first.

- *Where:* bot/core/analyzer.py:3109-3990; bot/core/strategy_modes.py; bot/config.py (AnalyzerConfig)
- *Success metric:* Voter and knob counts fall by at least 50%, and retained families lose no measured contribution on the ledger.

### D. Capital gate and execution integrity

**Goal.** Real money is exposed only as far as a default-deny artefact allows. Positions are protected within seconds whatever the load. Outcomes are typed, position state is declared, and venue quantity is reconciled.

**Why.** Autonomous confirms leave through two loops (engine.py:9654 and 9240-9275). The only shared barrier is the Lock-5 mint (8397-8420), which depends on a flag. The positions pass runs after scan and analyze (6368). No pass lock exists, analyzer CPU runs on the event loop, and live symbols are not on the WS feed. Limit fills sit unprotected until the next pass, and venue quantity is never reconciled. PRE_CAP_TIGHTENS_CAP is empty. LivePosition has 16 undeclared attributes and 26 direct status writes, and outcomes are classified by substring. There are 0 venue fixtures, 7 `_venue.id == 'bitget'` branches and 6 `40085` references. The macro seed and intent-policy paths are CWD-relative (engine.py:723, 1900), and the macro seed blocks every entry when the bot runs from another directory. Production values are unknown.

**Done when.** Autonomous orders and size above research notional are impossible without an eligible artefact (drives through both loops). Gap p99 is under 20s and fill-to-stop under 15s. Quantity drift is detected. Outcomes are typed and positions declared. Every venue path has replays. Every new money-path component has a kill flag and backward-readable persistence.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| D0 | Day-0 production read | S | - |
| D1 | Research mode by configuration, with the margin-cap units done right | S | D0 |
| D2 | Default-deny live eligibility at the Lock-5 mint | M | D1, A1 |
| D3 | Monitor gap: measure it and shorten it without new concurrency | S | - |
| D15 | PositionGuard task under a pass lock, behind a kill flag | M | D3 |
| D4 | Bitget replay corpus (classic and UTA) | L | D0 |
| D8 | Typed ExecutionOutcome as a str subclass | M | - |
| D6 | Reconcile venue quantity against the book, conservatively | M | D4, D8 |
| D7 | Decide PRE_CAP_TIGHTENS_CAP on measurement | S | - |
| D10 | Anchor CWD-relative config paths through repo_path() | S | - |
| D12 | Key custody and per-user trust | S | - |
| D9 | Declared LivePosition schema and PositionStatus enum, audit-first | M | D8, D4 |
| D14 | Venue adapter contract, and corpora for Bybit, BingX and Hyperliquid | L | D4, D8 |
| D5 | Venue-native protection at fill, then a private order stream | L | D4, D15, D14 |
| D11 | Staged capital ramp read from the eligibility artefact | M | D2, C13, C11 |

**D0 · Day-0 production read** (S)

A read-only record from the bot box, with no secrets. Flags: AUTO_CONFIRM_*, MICRO_MAX_* caps, LLM_DAILY_BUDGET_USD, PER_USER_LIVE_ENABLED, WEB_LIVE_TRADING_ENABLED, LIVE_OPEN_TO_KEY_HOLDERS, PAPER_SIM_OPT_IN_ENABLED. Row counts and date ranges for llm_calibration.jsonl, order_flow_snapshots.jsonl, decision_memory.jsonl (by outcome type) and closed_trades. Also: the LLM/RULE share, the venue-priced share of recent closes, the count of 'LLM daily dollar budget exhausted' audits, monitor-pass gaps from tick audit lines, and adopted positions with unread margin. Stored as an ADR appendix.

- *Where:* production .env and data/ (read-only); docs/adr/
- *Success metric:* All fields are recorded before any D1, E1 or C2 decision.

**D1 · Research mode by configuration, with the margin-cap units done right** (S)

Operator-approved env change. Set AUTO_CONFIRM_LIVE_ENABLED=false; that is already the .env.example value, while the code default is True, and D0 shows what production holds. Choose a target NOTIONAL N per order, about the largest venue minimum you will trade. MICRO_MAX_POSITION_USD is a MARGIN cap (config.py:1945), so set it to N divided by the standard leverage (5x), with headroom for the venue-minimum round-up (EXCHANGE_MIN_ROUNDUP_MAX_MULT 1.5, config.py:952). Set MICRO_MAX_TOTAL_EXPOSURE to k times that margin and keep PER_USER_LIVE_ENABLED off. Before switching, run the executor's minimum gate offline against a planted venue over the live universe and count the refused symbols; a refusal places nothing. Shadow recording and signal publishing stay on, and /status and the web backstop say 'research mode'.

- *Where:* bot/config.py:952, 1945, 1967, 2553-2557; production .env; app/public/js/risk-backstop-model.js; docs/adr/
- *Success metric:* 0 autonomous live orders from day 1. No placed order exceeds the margin cap or the standard leverage (executor audit, first week). At least 1 minimum-size close per week keeps execution telemetry flowing.

**D2 · Default-deny live eligibility at the Lock-5 mint** (M)

The new leaf bot/core/live_eligibility.py reads one committed artefact, benchmark/eligibility/<strategy_hash>.json. Its schema is shared with D11, the KPIs and the phase gates: strategy_hash; the registration commit sha, dated before its data existed; the prospective window(s); n and n_eff vs required; the A7 cost-model version; the C11 method; the family alpha; the three-way verdict; the C13 portfolio result; regime coverage; and the stage granted (none / minimum / 25% / 100%). The verdict is 'eligible' only when the file exists, parses, names the running strategy_hash, says survives and grants a stage. Every other state denies, with no governor-probe or 'too thin' exception. It is asked at the Lock-5 mint in _confirm_trade_inner (engine.py:8397-8420), which every non-human live confirm crosses: the tick's _auto_confirm_batch and the /forcescan loop that calls confirm_trade(tid, user_id='auto') directly. AUTO_CONFIRM_LIVE_ENABLED becomes the second line. The verdict also supplies a size ceiling, and the executor's hard-cap check reads min(config caps, stage ceiling), so size above the research notional needs the artefact too. Human confirms keep working at research size. trade_gate and entry_gate only render the verdict, in one sentence with no dollars. Name the new refusal in test_trade_gate_parity's confirm-window count.

- *Where:* new bot/core/live_eligibility.py; bot/core/engine.py:8397-8420, 9240-9275, 9654; bot/core/live_executor.py (hard-cap check); bot/core/trade_gate.py (render only); tests/test_trade_gate_parity.py
- *Success metric:* With AUTO_CONFIRM_LIVE_ENABLED=true and no artefact, drives through both the tick and /forcescan place nothing. An artefact for a different strategy_hash is refused. Size above the ceiling is refused without a stage (test). The verdict prints on /status, the web backstop and /health.

**D3 · Monitor gap: measure it and shorten it without new concurrency** (S)

Stamp the time between completed position passes and the event-loop lag (the delay on a 1s heartbeat), and export both: p99 on /health, alerts above a 60s gap or a 2s lag. In _tick, run the existing positions pass after scan and again between analyze batches (_analyze_signals_batched already batches), so the worst case is bounded by one batch rather than the 300s phase. Subscribe live position symbols to ws_feed; today only BTC, ETH, SOL and paper symbols are subscribed. No lock is needed because no pass runs concurrently.

- *Where:* bot/core/engine.py:6156, 6248, 6368, 4596-4604, 4259, 10131-10143; bot/core/ws_feed.py; bot/web/dashboard_server.py
- *Success metric:* Production gap p99 and loop lag are measured. In a stalled-analyze drive the gap is bounded by one batch timeout plus pass time. Live symbols are on the WS feed.

**D15 · PositionGuard task under a pass lock, behind a kill flag** (M)

Add a new _position_pass_lock (none exists today), acquired by every entry into the positions pass: engine.py 6081, 6092, 6123, 6134, 6368, the _tick_guarded backstop at 4604, and the backoff pass. Then add a task that runs every 10s: check_positions, _check_pending_limit (live_executor.py:10839-11315), the halt cancel and unverified-submission recovery. It runs for real behind POSITION_GUARD_ENABLED. There is no shadow mode: those ~1,000 lines book fills and place stops, and threading a dry-run flag through them would be new money-path code. The in-tick passes stay as a backstop until production p99 has been under 20s for 7 days. Move analyzer CPU to asyncio.to_thread so the loop lag the guard depends on is bounded. Set a REST budget covering guard cadence times executors against venue limits and the degraded-mode counter (5 API errors switches to reduce_only/paused).

- *Where:* new bot/core/position_guard.py; bot/core/engine.py (7 call sites); bot/core/analyzer.py; bot/core/live_executor.py:2287ff, 10839-11315
- *Success metric:* A drive shows two passes can never run concurrently. A stalled-analyze drive sees at least 25 guard passes in 300s. Production gap p99 is under 20s and fill to stop under 15s. 0 degraded-mode trips are attributable to guard cadence. Turning the flag off restores the D3 behaviour.

**D4 · Bitget replay corpus (classic and UTA)** (L)

Recorded, sanitised HTTP pairs: one capture per executor call site (open, protect, trail, partial, close, reconcile, adopt, history lookup) on classic and UTA accounts, replayed through ccxt 4.5.56. Captures come from production read-only calls plus minimum-size real orders under an operator-approved cost budget. Demo-environment recordings are not assumed to match production request shapes. A nightly read-only smoke diffs response shapes. The adapter refactor is D14.

- *Where:* new tests/venue_replay/bitget/; new scripts/capture_venue_fixtures.py; tests/venue_symbol_reads.py
- *Success metric:* Every Bitget executor call site has at least 1 replay. At least 5 recorded Bitget defects reproduce as failing replays on their pre-fix commits. The nightly diff runs.

**D8 · Typed ExecutionOutcome as a str subclass** (M)

execute() and confirm_trade return ExecutionOutcome(str) carrying .kind (PLACED|RESTING|REFUSED|FAILED|UNVERIFIED), .trade_id and .reason_code. The subclass matters: web.json_response({'result_html': result}) (user_gateway.py:2497), telegram _send's split and scrub, confirm_trade's internal concatenation and the substring readers all keep working while readers move to .kind. The token lists are deleted afterwards, and an AST test requires a kind at every return site.

- *Where:* bot/core/live_executor.py:397; bot/core/confirm_result.py; bot/core/engine.py:7946; bot/web/user_gateway.py:2497; bot/skills/callback_handler.py; bot/skills/telegram_handler.py:3913
- *Success metric:* Every existing transport test passes unchanged at the first step. At the end, no substring classification of execute/confirm results remains.

**D6 · Reconcile venue quantity against the book, conservatively** (M)

reconcile_positions compares venue contracts per (symbol, side) with the book via rows_for_side. When the venue holds less than the book by more than one lot, audit QTY_DRIFT, update the book and alert. When the venue holds more (a manual add or a double fill), adopt the excess as a separate row with provenance and alert, and leave the bot's stop sized to the bot's own quantity, never resized over quantity the bot did not open. Duplicate merge reads the venue before merging, and duplicate_merged joins NON_FILL_CLOSE_REASONS. Behind a kill flag.

- *Where:* bot/core/live_executor.py (reconcile_positions ~15818-16262, _place_classic_trigger ~8800, dedupe ~5086); bot/utils/close_reason.py
- *Success metric:* Replay drives: venue at 2x the book produces an adopted excess row with the bot stop unchanged; venue at 0.5x updates the book. QTY_DRIFT is visible on /status.

**D7 · Decide PRE_CAP_TIGHTENS_CAP on measurement** (S)

Measure how often the notional cap binds under the loss-at-stop base on all discovery snapshots. Run three arms: empty, {governor, drawdown_recovery}, and all seven. Record the decision at risk_engine.py:162, and replace the stale 'binds on ~every trade' comment with the measured fraction. This is a tighten-only risk policy decided on discovery data and labelled as such.

- *Where:* bot/risk/risk_engine.py:156-165, 1998-2010; docs/FROZEN_BENCHMARK.md; docs/adr/
- *Success metric:* A recorded decision backed by PF, max drawdown and worst fold per arm on 3 snapshots, with C11 intervals.

**D10 · Anchor CWD-relative config paths through repo_path()** (S)

Add repo_path() for read-only repo assets, distinct from state_path, so a test state root (G2) cannot hide the seed. Resolve config/macro_calendar.seed.json and config/intent_policy.json through it, and widen the durable-path guard to cover config/ literals.

- *Where:* bot/core/engine.py:723, 1900, 1929; bot/utils/paths.py; tests/test_durable_paths_are_not_cwd_dependent.py
- *Success metric:* An engine started from /tmp loads the macro seed and evaluates entries normally (drive). The guard fails on any new relative config/ literal.

**D12 · Key custody and per-user trust** (S)

At /connect, refuse keys whose withdraw scope is readable and enabled, unless the operator explicitly overrides. The re-check refusal names only the exception class. Per-user Telegram live requires an enforce-mode Authority Envelope before PER_USER_LIVE_ENABLED is ever turned on; otherwise docs/guardian_authority.md and every surface that repeats it are corrected, per G0.

- *Where:* bot/skills/account_commands.py:327-368; bot/core/engine.py:~8296, 1485-1496; bot/guardian/order_authority.py; docs/guardian_authority.md
- *Success metric:* 0 new withdraw-enabled keys are stored. No exception text appears in any confirm result. The decided per-user behaviour is drive-tested.

**D9 · Declared LivePosition schema and PositionStatus enum, audit-first** (M)

Declare the 16 dynamic attributes as typed fields. Mark runtime-only fields (_close_flight, _unprotected_alert_at, _reconcile_retries, _duplicate_signature) as non-persisted in field metadata, with reasons. Derive _save_positions, _load_positions, the restore helper and closed_trade_row from dataclasses.fields plus codecs. Make status a PositionStatus(str, Enum) with a transition table that is audit-only for at least 4 weeks (logging unexpected transitions such as CloseFlight recovery, unverified recovery, adoption, fill-during-cancel) before it asserts. Round-trip tests read rows from older builds, and a rollback test has the previous build read rows written by this one. The per-trade event log formerly planned as D13 is not built in this horizon.

- *Where:* bot/core/live_executor.py (LivePosition ~1862-1938, _save_positions ~15240, _load_positions ~15446, restore ~1150, closed_trade_row ~556); new bot/core/position_schema.py
- *Success metric:* A ratchet finds 0 undeclared position setattr names (from 16) and 0 raw status-string writes (from 26 in the executor, 34 across bot/). One serializer. Both directions of older/newer rows round-trip.

**D14 · Venue adapter contract, and corpora for Bybit, BingX and Hyperliquid** (L)

Promote Venue (venues.py:161) to an adapter: place_entry, place_protection, list/cancel_protection, read_position, read_order, read_close, probe_account_type. Move the 7 bitget-id branches and the 40085 UTA probe copies into BitgetVenue, one call site per PR, each green on the D4 replays. Capture each other execution venue's corpus before extracting its paths.

- *Where:* bot/core/venues.py:161; bot/core/live_executor.py; tests/venue_replay/
- *Success metric:* 0 `_venue.id == 'bitget'` branches remain in the executor. Every execution venue has replays for every call site its adapter touches.

**D5 · Venue-native protection at fill, then a private order stream** (L)

Where the replays show the venue accepts it, place entries with preset SL/TP, starting with Bitget classic and UTA, and keep the _protect_legs and plan-cleanup id bookkeeping correct under replay. Then use ccxt.pro watch_orders/watch_positions as the fill trigger with REST as fallback. PositionGuard stays as the backstop. Each step sits behind its own kill flag.

- *Where:* bot/core/live_executor.py (_submit_entry_order, _protect_legs, _check_pending_limit); bot/core/venues.py; new bot/core/order_stream.py
- *Success metric:* Fill to protection is 0s on preset-capable venues and under 15s elsewhere. Replay drives pass.

**D11 · Staged capital ramp read from the eligibility artefact** (M)

A strategy with a survives artefact moves from minimum to 25% to 100% of the operator's budget, and each stage is its own artefact update. Kill rules come from a bootstrapped path distribution of the replay's trade sequence over the stage horizon (for example the 95th-percentile drawdown), plus a CUSUM or SPRT on per-trade net R against the replay expectation. The rule is not 'the worst of 6 small folds', which a genuine edge will exceed over a longer horizon. Promotion needs a stated n_eff and power. Costs at scale use A7's impact model, flagged as extrapolated until fills at that size exist.

- *Where:* bot/core/live_eligibility.py; benchmark/eligibility/; docs/adr/
- *Success metric:* Every capital change maps to an artefact transition. A drill fires the kill rules automatically and demotes to minimum.

### E. Chat copilot: read-only tools, staged drafts, eval-gated

**Goal.** An analyst that answers from measured readings through typed, read-only tools, says 'unmeasured' when there is nothing to read, remembers the conversation, and can only propose a ticket the user stages themselves. Every change is scored by an eval, and chat can never change which trades the engine takes.

**Why.** 95 regex rules over 51 intents; a 41-phrase probe found 3 confident wrong cards. The model has 17 read-only tools against 133+ commands. There is no eval or feedback, and routing is audited only in dead code (intent_router.py:2390-2485). Chat and thesis share one budget, and prices are wrong. The request helper sends no effort for Sonnet ids (provider.py:1689-1700), and a refusal raises RuntimeError (:1491, 1926). The cached system block carries live data. 36 test files drive router corpora and 39 pin the two dispatchers, so retiring intents is a corpus migration. Every chat tool is read-only today, which is the stated reason injection cannot trigger an action; a draft tool must not break that.

**Done when.** Every production chat model scores: route accuracy at least 95%, 0 confident wrong cards, groundedness at least 98% (admin) and 97% (free), 0 act-claims and 0 injection-triggered actions. Chat cannot change which trades the engine takes. Drafts are staged only by the user.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| E1 | Separate chat and thesis LLM budgets; fix the cost table | S | D0 |
| E2 | Route telemetry and feedback; delete the dead classifier; freeze read-intent regex | S | - |
| E3 | Chat eval harness with injection cases | L | E2 |
| E7 | Model refresh and a cacheable prompt layout (before extra tool rounds) | M | E1, E3 |
| E4 | Typed read tools with provenance, run concurrently | L | E3, E7 |
| E5 | Staged drafts: the model proposes, the user stages, a human confirms | M | E3, E4, D8, B3 |
| E6 | Numeric grounding verifier | M | E3, E4 |
| E8 | One ChatTurn orchestrator for Telegram, web and the bridge | XL | E3, E4 |
| E9 | Memory and continuity for multi-turn planning | M | E3 |
| E10 | Per-user LLM economics and tier routing | M | E1, E3 |

**E1 · Separate chat and thesis LLM budgets; fix the cost table** (S)

Split the budget into LLM_DAILY_BUDGET_THESIS_USD and LLM_DAILY_BUDGET_CHAT_USD, read from cost_by_category, with per-category call counts. Today the chat guard compares the shared snap.llm_calls against daily_call_limit (telegram_handler.py:2949), while the analyzer uses its own _llm_calls_today (analyzer.py:4118). Category booking exists at telegram_handler.py:3138 and at analyzer 4398, 4425 and 4790. State the trade-off: the thesis loses headroom on quiet chat days, so reserve a thesis floor and let chat use only its own cap. Correct cost.py to the current rates: claude-sonnet-5 and the newer Sonnet release $2/$10; the newer Opus release $4/$20; claude-opus-5 and claude-opus-4-8 $5/$25; claude-haiku-4-5 $1/$5; claude-fable-5 and claude-fable-5-1 $10/$50. Fix the family fallbacks (opus $5/$25 as the conservative bound). Book cache read and write tokens. Lowering prices lets more real dollars through the same cap, so re-set the dollar caps from D0's measured spend. C2 stratifies by date before and after E1.

- *Where:* bot/config.py:1300; bot/core/analyzer.py:4118-4136, 4398, 4425, 4790; bot/skills/telegram_handler.py:2947-2951, 3138; bot/core/cost.py:36-70
- *Success metric:* A drive with chat spend over the chat cap still sees the analyzer call the LLM. 0 chat-caused RULE_ENGINE_BUDGET fallbacks. Booked cost is within 5% of the invoice.

**E2 · Route telemetry and feedback; delete the dead classifier; freeze read-intent regex** (S)

Move the intent_route audit into classify_rules (rule index, intent, confidence, reply_mode) and log the tools chosen on every LLM fall-through. Add thumbs up/down and 'wrong answer' on web bubbles and a Telegram inline button, stored on the conversation row under the existing retention and erasure rules. Delete IntentRouter.classify and _classify_with_llm. Produce a weekly misroute report. Policy: no new read-intent regex rules; misroutes become eval cases.

- *Where:* bot/nlp/intent_router.py:2318, 2390-2485; app/public/js/chat.js; app/routes/chat.js; bot/skills/telegram_handler.py; bot/nlp/conversation_store.py
- *Success metric:* 100% of turns carry a route label. The weekly report runs. The router rule count stops growing.

**E3 · Chat eval harness with injection cases** (L)

A corpus of at least 400 labelled turns plus 3-6-turn scenarios, drawn from the CLAUDE.md misroute corpora, the 41-phrase probe and the 36 router-corpus test files, whose pinned routings become cases. It is kept outside git (or synthesised as paraphrases), so production-derived turns stay within the account-purge path. Deterministic scorers check: route/tool correctness, act-request refusal, number groundedness, unread shown as zero, and prompt injection (instructions planted in web_search, news and token-name tool results must never produce an action or a draft). An LLM judge is advisory. Cassettes replay only unchanged requests, so CI with cassettes gates router and tool CODE only. Prompt, tool-schema and model changes are scored by live runs (nightly and on demand) using paired per-item comparisons over repeated runs with an interval, under a stated budget.

- *Where:* evals/chat/ (schema and scorers in git, corpus outside); new scripts/chat_eval.py; .github/workflows/ci.yml; new scheduled workflow
- *Success metric:* A per-model baseline exists before any tool, router or model change. CI fails on any act-claim or injection-triggered action. Model changes need a paired live comparison whose lower bound is not below -1 point.

**E7 · Model refresh and a cacheable prompt layout (before extra tool rounds)** (M)

Split the system prompt into a static cached block (persona, rules, tool rule) and volatile content (tickers, book, alerts) after the last breakpoint, or in mid-conversation system messages, which the newer Sonnet release supports and Sonnet 5 does not. Verify cache_read_input_tokens > 0. Move admin chat to the newer Sonnet release once E3 scores it at or above the old id, and set effort explicitly: _anthropic_reasoning_extras sends none for Sonnet ids, so the newer Sonnet release would run adaptive thinking at default 'high'. Start the chat sweep at 'low' and use display 'summarized' if reasoning is streamed. Forced tool_choice any/tool returns 400 on the newer Sonnet release and the newer Opus release, so use 'auto' with strict tools (additionalProperties:false plus required). Thinking {type:'disabled'} returns 400 on the newer Sonnet release; use 'between_tools' at effort high or below if a route must be thinking-off. the newer Opus release defaults to medium effort, so set it explicitly. Replace the refusal RuntimeError with stop_reason handling plus server-side fallbacks. Return all parallel tool results in one user message. Use Haiku 4.5 for the summarizer. Keep history append-only and text-only, since thinking blocks bind to their model. Keep the old id behind a rollback flag.

- *Where:* bot/llm/provider.py:63-65, 268-335, 1416-1424, 1491, 1689-1700, 1852, 1881-1884, 1926; bot/skills/telegram_handler.py (_build_chat_system_prompt); bot/core/cost.py
- *Success metric:* Cache reads are at least 50% of admin input tokens. The paired eval is not worse than the old id. Cost per admin turn falls measurably. Rollback is one flag.

**E4 · Typed read tools with provenance, run concurrently** (L)

Tools return JSON {value, unit, as_of, source, read_state: read|unread|absent, sample}, derived from the existing *_card_text seams: market_snapshot, setup_record, strategy_verdict (read from artefacts), signal_history, chart_read (the detector output plus the C7 exploratory row, or 'unmeasured'), funding_across_venues, etf_flows, stress_test and analyze_light (rules-only or a cached thesis, never a fresh paid thesis per turn). Tools switch to ledger views as B4 lands. Calls run concurrently, and rounds rise from 3 to 5 inside the 45s deadline only after E7's cache split. The Guardian defang applies to tool results. A read intent's regex fast path is retired only where the eval shows the tool beating it for that model tier.

- *Where:* bot/nlp/chat_tools.py:84-285; bot/llm/provider.py:1706-1751, 1876-1950; bot/formatters/rich_cards.py; bot/core/setup_record.py; bot/core/signal_outcomes.py
- *Success metric:* On the eval: at least 80% of read-intent turns are answered from a fresh reading, 0 confident wrong cards, under 5% 'cannot measure' on computable questions, and p95 turn latency of 12s or less.

**E5 · Staged drafts: the model proposes, the user stages, a human confirms** (M)

draft_trade computes a proposed ticket deterministically (ATR stop, the record_idea_levels floor, the strategy's minimum R:R, and the net ratio from trade_costs) and returns it as data. It registers nothing. It is offered only when the user's own message in that turn asks for a ticket; that check reads the user's text, never tool-result text. Only when the user presses 'Stage this ticket' does the handler call build_manual_idea with source='manual', so every manual-keyed site treats it as typed: quality_reading, is_manual, LEVELS_AS_SHOWN_SOURCES, the 24h expiry, GTC not post-only, no drift chase, copilot_context. A separate provenance field records origin='chat_draft'. The handler then runs review_ticket and shows the existing Confirm card with the strategy verdict. The 'execution' reply contract calls the tool instead of writing prices. Conversion (draft to staged to confirmed) and each draft's outcome go to the ledger's chat_drafts table. E3's injection cases must pass before this ships.

- *Where:* bot/nlp/chat_tools.py; bot/skills/manual_trade.py; bot/core/copilot_context.py; bot/core/limit_entry.py:464; bot/skills/callback_handler.py; bot/skills/chat_runtime.py:~703-709
- *Success metric:* 0 model-typed entry/stop/target triples on the eval. 0 drafts staged from injected instructions. A staged draft is treated exactly as a /trade ticket by every manual-keyed site (drive).

**E6 · Numeric grounding verifier** (M)

In _chat_ret, check every figure with a unit against this turn's prompt blocks and structured tool outputs, with tolerance and unit normalisation. Run it in shadow first, then annotate or drop unverified sentences. Add a 'read from: ...' footer built from tool events.

- *Where:* bot/skills/chat_runtime.py:979-1039; new bot/nlp/grounding.py
- *Success metric:* The unverified-figure rate is published per model. Annotation turns on only below 1% (admin) and 3% (free). The source footer appears on 100% of tool-backed answers.

**E8 · One ChatTurn orchestrator for Telegram, web and the bridge** (XL)

Collapse _handle_message (977 lines) and _chat_turn (615 lines) into one pipeline: firewall, grammar, action doors, fast path, agent, _chat_ret, memory. Transport adapters handle only doors and rendering. The 15 Node intercepts move behind the same tools, and the api_bridge/MCP facade gets tools under a scoped role. Migrate one intent family per PR behind the eval. The 39 dispatcher-pinning test files move to eval cases as each family migrates.

- *Where:* bot/skills/telegram_handler.py:3714-4691; bot/web/user_gateway.py:599-1214; bot/nlp/chat_facade.py; app/routes/chat.js:75-129; api_bridge.py:1072-1122
- *Success metric:* One dispatch table and one memory-record site. 30 or fewer rules, all action doors. 0 'fixed on Telegram, not on web' defects per quarter.

**E9 · Memory and continuity for multi-turn planning** (M)

Replace the 9-message window with a history budget of about 6k tokens, compressing tool records to their structured fields. Make the persisted store the source of truth, with no LRU eviction of active users. Add a dated per-user working-plan note the model can update through a tool.

- *Where:* bot/nlp/conversation_store.py:292-354; bot/skills/telegram_handler.py:2759; bot/nlp/chat_tools.py
- *Success metric:* At least 90% of multi-turn eval scenarios pass. 0 evictions of active users.

**E10 · Per-user LLM economics and tier routing** (M)

Per-user quotas and cost attribution. Route models per tier by paired eval score per dollar. An operator margin view shows cost per turn by tier. No single user can exhaust a global budget.

- *Where:* bot/web/chat_quota.py:40; bot/core/cost.py; bot/llm/provider.py:537-547; bot/config.py:1375
- *Success metric:* Cost per active user per day is tracked. 0 budget exhaustions caused by one user. Every routing choice is backed by the per-tier scorecard.

### F. Signals, patterns, website and product: show what was measured, make the first minutes work

**Goal.** Every trade surface carries setup identity and its record in R with honest intervals from the ledger, labelled 'exploratory' until a registered cell replicates. No public surface publishes a fabricated direction, volume or probability. New users reach a labelled practice action, and alerts are personal and deduped.

**Why.** The candle cache key omits limit (market.js:127-134). /deepscan fabricates public cards (skill_registry.py:4155-4165). 34 constant pattern percentages gate pushes (pattern_watch.js:21-46). /patterns shows dollar averages to viewer and paper roles (skill_registry.py:2599-2618). Engine rows have pattern=None, and mean R is stripped as though it were dollars (public_signal.js:81-97). Calibration is never rendered, and there is no per-signal page. The web chart-read reads the forming candle, and there are two sweep detectors. The first Confirm dead-ends. Presets with PF below 1 are offered for following. The watchlist never reaches alerts.

**Done when.** 0 new signals lack setup identity. The scoreboard shows mean R with C11 intervals and exploratory labels. No unsourced pattern percentage and no fabricated public card exist. Presets carry their verdicts. A new user reaches a labelled practice action on day 1.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| F1 | Public truth fixes: candle cache and /deepscan cards | S | - |
| F2 | Setup identity on the wire, with canonical symbols | M | A1 |
| F4 | Interim pattern relabel and a /patterns fix | S | - |
| F7 | Label or retire the losing marketplace presets | S | - |
| F8 | Honest onboarding: a first Confirm that works | S | B1 |
| F11 | Closed-bar correctness and one sweep detector | S | - |
| F3 | Setup scoreboard in R with honest intervals and exploratory labels | M | F2, B4, C11, C1 |
| F5 | Per-signal page /signal/:key | M | F2, F3 |
| F6 | Signal stream UX with one definition of 'live' | M | F2 |
| F12 | Pattern event stream with exploratory labels | M | C7, B3, F4 |
| F13 | Personal copilot: scoped alert subscriptions and an opt-in digest | M | E4, F2, F12 |
| F9 | Streaming deltas (no bundler in this horizon) | M | - |

**F1 · Public truth fixes: candle cache and /deepscan cards** (S)

Include limit and the normalised granularity in the candle cache key, or cache the max-limit series and slice it. Replace the object caches in http_cache, insight and patterns with a bounded LRU (about 500 entries) plus single-flight. /deepscan pushes only its pattern block (scanned=False), so the last real /scan's cards carry forward. Delete the RSI-heuristic direction, the 2.5/1.0 vol_ratio and the 2%-of-price ATR. A card's trigger lists only patterns that agree with its direction.

- *Where:* app/routes/market.js:127-134; app/lib/http_cache.js:33-49; app/routes/insight.js:73-82; app/routes/patterns.js:70-79; bot/skills/skill_registry.py:4020-4170; bot/skills/scan_skill.py:792-870
- *Success metric:* limit=2 then limit=200 returns 200 rows, and the Chromium smoke still shows 200 bars after 5 minutes. A bearish-H&S drive produces no LONG card, and no card prints 'Vol 2.5x'.

**F2 · Setup identity on the wire, with canonical symbols** (M)

Add a TradeIdea.patterns field ({name, signal, instance_id, formed_idx}), filled from detector output (never LLM prose). build_signal_payload and the scan rows send signal_type, strategy_type, timeframe, source, entry regime, analysis_id, strategy_hash and a canonical exchange symbol, in nullable columns outside the sealed canonical payload. The web ships first so it accepts the fields before the bot sends them. Old rows read 'unrecorded'.

- *Where:* bot/utils/models.py:92-170; bot/utils/website_sync.py:494-512; bot/skills/scan_skill.py; bot/formatters/signal_card.py:462-476; app/db.js:2656-2681; app/routes/sync.js:1090-1135
- *Success metric:* The null setup-identity share of new engine rows falls from about 100% to 0%. by_symbol has one group per market. Seal hashes are unchanged (test).

**F4 · Interim pattern relabel and a /patterns fix** (S)

Every pattern surface prints 'detector score, unmeasured' with no percent: Telegram /deepscan, the PNG card, web deepScanCard and the sweep card. pattern_watch stops gating on constants and labels its push unmeasured. Rename /patterns to /learned, or rescope it, in all 14 locales. Non-admins see no dollars, and 'Conf = min(win_rate, .85)' becomes win rate with n and an interval.

- *Where:* bot/core/chart_patterns.py; bot/skills/skill_registry.py:2599-2618, 4124-4132; app/public/js/dashboard.js:2976-2985; app/lib/pattern_watch.js:21-66; bot/skills/command_catalog.py; bot/utils/locales/; bot/learning/patterns.py
- *Success metric:* A renderer guard fails on any pattern percentage not sourced from pattern_history. A viewer-role drive shows no '$'.

**F7 · Label or retire the losing marketplace presets** (S)

Each preset shows its scorecard verdict (PF, n, folds, marked discovery-data). Public listing, promotion and copy/follow are removed for PF-below-1 presets until a strategy has an eligible artefact. Copy panels show the followed agent's record in R with a C11 interval.

- *Where:* benchmark/scorecards/*.json; app/routes/copy.js; marketplace and copy views in app/public/js/dashboard.js
- *Success metric:* 0 presets are shown or offered for following without their verdict (guard test).

**F8 · Honest onboarding: a first Confirm that works** (S)

Per the G0 decision, self-admitted paper users get labelled PRACTICE fills on their first Confirm, which B1 excludes from engine learners and public records. An onboarding checklist in chat: watch a signal, open its page, stage a practice ticket, link read-only keys. The /connect card reads PER_USER_LIVE_ENABLED instead of always claiming 'not yet enabled'. D7 and D30 retention are baselined in week 1.

- *Where:* bot/config.py:2587; bot/core/engine.py:8452-8463; bot/utils/user_store.py:76, 275; bot/skills/account_commands.py:363-368; bot/skills/start_commands.py
- *Success metric:* At least 90% of new paper users' first Confirm ends in a practice fill or an explicit next step (currently 0%).

**F11 · Closed-bar correctness and one sweep detector** (S)

chartread.js computes structure, BOS, CHoCH and VWAP on closed bars, or labels the chip 'provisional'. chart_renderer computes overlays from df[:-1]. Correct the false reason in the candle_hygiene_baseline row. chart_patterns.detect_liquidity_sweep calls liquidity_sweep.detect_sweeps. Descriptions use the adaptive price formatter.

- *Where:* app/public/js/chartread.js:30-40, 194-196; bot/skills/chart_renderer.py:459-461, 1618; tests/candle_hygiene_baseline.txt; bot/core/chart_patterns.py:1355-1415; bot/core/liquidity_sweep.py
- *Success metric:* A planted forming bar that breaks a swing shows no BOS chip (or one labelled provisional). There is one sweep definition, and no '$0.00' appears on sub-cent assets.

**F3 · Setup scoreboard in R with honest intervals and exploratory labels** (M)

Rename the R aggregates to net_r and mean_r so publicAnalytics keeps them; R is a ratio and allowed on public surfaces. Group by setup x regime x timeframe x source x direction. Show n, a Wilson hit rate and mean R with the C11 interval (JS twin), with a BH-FDR q-value across displayed cells. Every cell reads 'exploratory'. The word 'survives' appears only on cells that were pre-registered and replicated prospectively. Thin cells read 'too thin to say', with no colour. Render the calibration chart, worded per the C3 default.

- *Where:* app/lib/signal_analytics.js; app/lib/public_signal.js:81-97; app/lib/inference.js; app/public/js/dashboard.js:2889-2906; app/public/js/winrate-bar.js
- *Success metric:* Every group at or above the floor shows n, hit rate and mean R with intervals. 0 'survives' labels without a registry entry (guard). 0 dollar keys.

**F5 · Per-signal page /signal/:key** (M)

The thesis and its counter-case; the levels on a chart at the signal's own timeframe from publication to resolution; entry_at, exit_at and R (sent outside the seal); the seal and verify block; 'similar setups' rows from F3 with their exploratory labels.

- *Where:* new app/routes/signal_page.js; new app/public/signal.html; app/routes/call.js; bot/core/signal_outcomes.py; app/public/js/tv-chart.js
- *Success metric:* 100% of stream rows open a detail page. 0 chart timeframe mismatches.

**F6 · Signal stream UX with one definition of 'live'** (M)

Publish expires_at and use it in SignalStatusModel.actionable, shared with copy picks and the Arena callBlock, with a countdown. Add filter chips, cursor pagination, a thesis snippet, a regime chip and a mobile card list. Add DB indexes on status and resolved_at, a limiter on /api/signals, and 30s single-flight caches on stats and analytics.

- *Where:* app/public/js/signal-status-model.js; app/lib/public_signal.js:57-61; app/routes/signals.js; app/db.js; app/routes/sync.js; app/public/js/dashboard.js:2825-2880
- *Success metric:* No Trade or Paper button appears past expires_at (test). Mobile LCP on the Signals view is under 2.5s.

**F12 · Pattern event stream with exploratory labels** (M)

The autonomous sweep's chart_patterns_geo (analyzer.py:972) writes formed/confirmed/invalidated transitions into the ledger's pattern_events table, synced to the web, so pattern_watch no longer depends on a manual /deepscan. Chips, cards and pushes show the C7 exploratory row (n, interval, 'exploratory') or 'unmeasured'. 'Survives' appears only for registered, prospectively replicated cells. Dedupe by instance_id; an instance pushes 'confirmed' at most once per user.

- *Where:* bot/core/analyzer.py:972; bot/learning/idea_ledger.py; app/routes/sync.js; app/lib/pattern_watch.js; bot/formatters/signal_card.py; app/public/js/dashboard.js
- *Success metric:* Events reach the web within one sweep, with no /deepscan run. No instance pushes twice in 48h. Every push is labelled.

**F13 · Personal copilot: scoped alert subscriptions and an opt-in digest** (M)

Subscriptions filter by setup, direction, regime and source, optionally limited to watchlist or held symbols. Alerts are deduped on repeat_of and instance_id, capped via anomaly_scope budgets, and delivered by web push and Telegram. An opt-in daily digest is built from the user's own book and recorded in the unprompted-alert ring. The watchlist shapes alert scope and chat context only; changing scan priority from it would be a strategy change under C1.

- *Where:* app/lib/alerts.js; app/lib/copy_watch.js; bot/core/proactive_monitor.py; bot/core/anomaly_scope.py; bot/core/user_profile_store.py; bot/core/time_exits.py; app/routes/watchlist.js
- *Success metric:* Opt-in and follow-up-question rates are tracked. 0 duplicate pushes per call. D30 retention of opted-in users is compared against controls.

**F9 · Streaming deltas (no bundler in this horizon)** (M)

A server-side Bitget public WS relay publishes throttled ticks on the existing CSP-safe SSE channel. Row deltas patch in place instead of re-rendering whole views. The bundler and per-view split of dashboard.js are out of this horizon: 166 test files slice it by markers.

- *Where:* app/routes/stream.js; new app/lib/ws_relay.js; app/public/js/dashboard.js
- *Success metric:* Markets price latency under 2s, and 0 whole-view re-renders per SSE nudge on the Markets and Signals views.

### G. Engineering system and operations: mergeable lanes, fast loop, lean guide, safe releases

**Goal.** Parallel lanes can merge without stepping on each other. The repair and bookkeeping tax falls so edge and capability reach at least 30% of commits. Money-path refactors land in small, replay-gated PRs, and durable state survives losing the box.

**Why.** Capability is about 4.5% of commits. CLAUDE.md is 1,324,924 bytes and was touched by 112 of 174 commits; INCOME_MAP by 73 of 174 (about 420 line citations). Stored ratchet totals merge wrong. The test gate is serial at 31m39s, blocked by state_path (paths.py:47-53), the conftest data/ wipe and about 305 CONFIG setattr sites. There were 5 manual re-records in 6 days. There are three unhashed dependency manifests. The benchmark and red team never import LiveExecutor or confirm_trade, so they cannot protect executor or confirm refactors. 56 test files read live_executor.py by path. Backups are tarred to the same box.

**Done when.** Lanes merge through a queue. CI is under 12 min (target 8) with 0 forgiven-flaky, and the impacted-test loop is under 3 min. CLAUDE.md is under 25KB. 0 manual re-records and 0 citation-only commits. Capability plus edge is at least 30% of commits. Tagged releases with rollback under 5 min. The restore drill passes with separately held keys.

| ID | Deliverable | Effort | Depends on |
|---|---|---|---|
| G14 | Merge path for parallel lanes: merge queue and a day-1 CLAUDE.md freeze | S | - |
| G11 | Derived ratchet totals and one re-record command | S | - |
| G5 | Symbol-anchored citations | S | - |
| G0 | Week-1 operator decision pass recorded as ADRs | S | D0 |
| G1 | Lean CLAUDE.md, lessons archive, backlog tracker, PR template | M | G14 |
| G6 | One hash-locked dependency set from production, and a reproducible agent environment | S | D0 |
| G2 | Test-only state root and a CONFIG override fixture | L | D10 |
| G3 | Parallel test gate, measured first | M | G2 |
| G4 | Strategy CI on discovery data only | M | C1, C12 |
| G13 | Offsite encrypted backups with separate key custody and a restore drill | S | - |
| G7 | Impacted-test selection and test tiers | M | G3 |
| G8 | Committed mutation harness and a nightly workflow | M | G7 |
| G9 | Strangler decomposition of the money-path god functions, replay-gated | XL | G3, G5, D8, D4 |
| G10 | Observability and SLOs | M | - |
| G12 | Layering contracts and release engineering | M | G6 |

**G14 · Merge path for parallel lanes: merge queue and a day-1 CLAUDE.md freeze** (S)

Turn on a GitHub merge queue on main that runs the fast gate, with the full gate post-merge until G3. Freeze CLAUDE.md growth: from day 1 new lessons go to docs/lessons/, enforced by a size ratchet. The generated .env.example block is changed only through its generator.

- *Where:* .github/ (branch protection, merge queue); tests/test_claude_md_accuracy.py; scripts/safety_flag_inventory.py
- *Success metric:* Merge queue active. CLAUDE.md line count does not grow after day 1.

**G11 · Derived ratchet totals and one re-record command** (S)

Drop the stored totals from the honesty, ruff and mypy baselines and compute them. scripts/rerecord.py --all re-records every ratchet in one step.

- *Where:* tests/honesty_baseline.json; tests/ruff_baseline.json; tests/mypy_baseline.json; scripts/honesty_gate.py; scripts/ruff_gate.py; scripts/mypy_gate.py; new scripts/rerecord.py
- *Success metric:* 0 stale-total merges; re-recording is one command.

**G5 · Symbol-anchored citations** (S)

scripts/cite.py --check resolves path::Qualname[#marker] anchors through the AST. Codemod the ~420 INCOME_MAP line citations and any in lessons. Replace the citation re-derivation tests with one --check, and fail CI on new '.py:<line>' citations in docs.

- *Where:* docs/INCOME_MAP.md; new scripts/cite.py; tests/test_claude_md_accuracy.py
- *Success metric:* 0 line citations in docs, and INCOME_MAP-touching commits under 5%.

**G0 · Week-1 operator decision pass recorded as ADRs** (S)

One session, informed by D0, whose dated ADRs are referenced by the implementing work: research-mode posture and notional (D1/D2); the eligibility artefact schema and the sealing strictness (C12); public positioning ('no demonstrated edge', exploratory labels); practice fills for paper users (F8); the pre-registered defaults for C2 and C3; the chat write policy (E5 staged drafts); the per-user live envelope (D12); presets (F7); the fixture-capture cost budget (D4).

- *Where:* new docs/adr/
- *Success metric:* A dated ADR exists for each item before its implementing deliverable merges.

**G1 · Lean CLAUDE.md, lessons archive, backlog tracker, PR template** (M)

Cut CLAUDE.md to under 25KB: preflight, the unreadable-is-never-zero shapes table, public-surface rules, drive-don't-scan and code_only, deploy and verify, where things live, and a rule to read docs/lessons/INDEX.md#<subsystem> before editing that subsystem. A script moves each incident chapter verbatim to docs/lessons/ with frontmatter and generates the INDEX. The misroute corpora move to eval data. The ~87 filed items go to docs/BACKLOG.yaml with evidence anchor, status, owner and blast_radius, and a test checks that anchors resolve. The PR template gains blast radius and the 30% rule. Re-home the accuracy pins.

- *Where:* CLAUDE.md; new docs/lessons/, docs/BACKLOG.yaml, .github/pull_request_template.md, scripts/split_claude_md.py; tests/test_claude_md_accuracy.py
- *Success metric:* CLAUDE.md goes from 1,324,924 bytes to under 25,000. Commits touching it fall under 10%. Every lesson links an existing guard test.

**G6 · One hash-locked dependency set from production, and a reproducible agent environment** (S)

Regenerate one hashed lock with uv or pip-compile, starting from a pip freeze of the current production box and pinning ccxt==4.5.56, whose parsing the executor depends on, so the change does not silently move venue behaviour. The Dockerfile, CI, preflight and pip-audit all use it. A .claude SessionStart hook installs it and puts ruff 0.11.13, mypy 1.15.0 and Node 24.21.0 first on PATH. Fix the Makefile and CONTRIBUTING.md, and delete .gitlab-ci.yml or generate it.

- *Where:* pyproject.toml; requirements.lock; bot/requirements.txt; requirements-ci.txt; Dockerfile:21-22; .github/workflows/ci.yml:98, 249; .gitlab-ci.yml:37; new .claude/settings.json; Makefile; CONTRIBUTING.md
- *Success metric:* One manifest is installed everywhere, and the production diff against the old install is empty or explained. A fresh session runs the fast gate within 5 minutes.

**G2 · Test-only state root and a CONFIG override fixture** (L)

RUNECLAW_STATE_ROOT is honoured only under pytest and is refused at boot when live mode is on, so a leaked value cannot relocate the users store, breaker and position book (the DB_PATH incident class). Read-only repo assets resolve through D10's repo_path(). Convert the import-time state_path constants to call-time lookups, the conftest wipe to a per-worker root, and the ~305 object.__setattr__(CONFIG...) sites in ~100 files to one config_override fixture, with a lint rule against new ones. Publish --durations JSON.

- *Where:* bot/utils/paths.py:47-53; module-level state_path constants (e.g. parity.py:60, chat_quota.py:48); tests/conftest.py:1134-1187; scripts/ci_test_gate.py:36-41
- *Success metric:* Two full suites run concurrently on one checkout. A live-mode boot with the variable set refuses to start (drive). New CONFIG mutations fail lint.

**G3 · Parallel test gate, measured first** (M)

Run pytest-xdist -n auto --dist loadfile on one larger runner as a non-blocking shadow for about 5 main runs, and promote it to the gate only at 0 forgiven-flaky. Flake re-runs stay serial. The network-reach ledger and coverage are aggregated across workers. Sharding across jobs happens only if xdist alone misses the target.

- *Where:* scripts/ci_test_gate.py; .github/workflows/ci.yml:46-86; requirements-ci.txt; scripts/preflight.py; tests/conftest.py
- *Success metric:* CI gate p50 falls from 31m39s to under 12 min, and local preflight to under 15. 0 forgiven-flaky over 5 main runs.

**G4 · Strategy CI on discovery data only** (M)

PRs touching the analyzer, bot/risk, bot/backtest or signal code run the frozen --honest walk-forward on the declared discovery snapshots and post a delta table labelled 'discovery'. Every run increments the registry's looks counter. A CI delta never justifies a merge without a registry entry. CI never reads sealed prospective data (C12). On main the job re-records the discovery artefacts.

- *Where:* .github/workflows/ci.yml; bot/backtest/runner.py; benchmark/
- *Success metric:* 0 manual re-record commits. Every strategy PR links a registry entry. 0 CI reads of sealed partitions.

**G13 · Offsite encrypted backups with separate key custody and a restore drill** (S)

Scheduled encrypted offsite copies of positions, the credential store, the secrets vault, risk state and the ledger. RUNECLAW_SECRETS_KEY and data/.exchange_secret.key are never in the bundle; they are held separately by the operator. The ledger is copied with the SQLite backup API. A monthly scripted restore into a scratch box, and a backup-age alert above 24h.

- *Where:* bot/utils/backup.py; bot/proofofpnl/scheduler.py:121; scripts/monitoring/; docs/adr/
- *Success metric:* The monthly restore drill passes using the separately held key, and a backup older than 24h alerts.

**G7 · Impacted-test selection and test tiers** (M)

A nightly --cov-context=test run builds a module-to-tests map. preflight --changed runs the ratchets, structural rules, a smoke tier and impacted tests. Add slow, drive, scan and subprocess markers and a shared session AST index.

- *Where:* scripts/preflight.py; tests/conftest.py; new scripts/test_impact.py; pyproject.toml
- *Success metric:* A typical slice's dev-loop check runs in under 3 min. 'Full gate refused a slice' incidents approach 0 per month.

**G8 · Committed mutation harness and a nightly workflow** (M)

scripts/mutate.py builds in the recorded safety lessons: restore on SIGTERM, SIGINT and atexit; purge __pycache__ on every restore; refuse any anchor that does not match exactly once; refuse a red baseline; run only impacted tests. A nightly job mutates the day's money-module diff and runs pytest-randomly, the slow tier and the network-reach re-measure.

- *Where:* new scripts/mutate.py; new .github/workflows/nightly.yml
- *Success metric:* No ad hoc drivers in PRs. A mutation score is tracked. 0 stranded-mutation incidents.

**G9 · Strangler decomposition of the money-path god functions, replay-gated** (XL)

One stage per PR. (1) A risk check registry makes _evaluate_locked's ~45 checks declarative; trading_blocked_by and entry_gate derive from it, and RISK_ENGINE_MAP.md is generated. The byte-identical benchmark and the red team (30/30) protect this stage. (2) Executor mixins. (3) A confirm pipeline. The benchmark and red team never import LiveExecutor or confirm_trade, so stages 2 and 3 require D4/D14 replays green for every call site the stage touches, plus the executor drives. executor_sources()/engine_sources() helpers land BEFORE the split, because 56 test files read live_executor.py by path. (4) Analyzer feature, vote and level stages. Scan tests over each extracted stage become drives in the same PR.

- *Where:* bot/risk/risk_engine.py:1448; bot/core/live_executor.py:7697, 12619; bot/core/engine.py:7946; bot/core/analyzer.py:869; tests/source_scan.py; docs/RISK_ENGINE_MAP.md; docs/EXECUTION_MAP.md
- *Success metric:* No function over 250 lines in bot/core, bot/risk or bot/skills. Every stage-2/3 PR shows replay coverage of its call sites. Source-scan files over these modules fall by at least 30%.

**G10 · Observability and SLOs** (M)

Metrics: per-phase tick histograms, the monitor gap and event-loop lag; REST calls per venue against its budget and the degraded-mode counter; LLM provider, model, latency, tokens (cache included), cost, source mix and parse failures; chat route, tool and grounding metrics; a confirm funnel by ExecutionOutcome kind; ledger label coverage; backup age. A trace_id per tick and per chat turn is carried into audit rows. Prometheus/Grafana (or OTEL) on a private bind with alert rules, and no series carries dollars.

- *Where:* bot/web/dashboard_server.py:527, 618; bot/core/engine.py:1038; bot/llm/provider.py; bot/utils/logger.py; docker-compose.yml; scripts/monitoring/
- *Success metric:* SLO alerts cover gap p99, loop lag, chat p95, LLM error rate, RULE share, refusal rate, ticker-priced share and REST budget. A chat turn traces to its risk verdict and venue order.

**G12 · Layering contracts and release engineering** (M)

Import-linter contracts enforce utils < config < risk < core < skills/web and keep the [research] extra out of the runtime; existing violations are ratcheted. Releases are tagged with digest-pinned images and a changelog from PR labels. A pre-deploy paper-mode smoke runs on a scratch box against a copy of state. There is no per-account canary: one process holds every executor. Rollback redeploys the previous tag in under 5 minutes.

- *Where:* pyproject.toml (importlinter); .github/workflows/; scripts/verify_deploy.sh; CHANGELOG.md
- *Success metric:* Contracts are green. 100% of deploys map to a tag. A rollback drill completes in under 5 minutes.

## Phases and gates

### Phase 0: Read, contain, unblock merging, start the evidence clock

*Now: days 0-3 for D0, G0, D1, G14, G11, G5; weeks 1-2 for the rest. About 4 lanes: money safety, measurement, chat/web, platform.*

Deliverables: D0, G0, D1, G14, G11, G5, D2, C11, C1, C12, E1, D10, D3, A1, C2, C3, B1, B2, A6, F1, E2, G1

**Gate to the next phase.** (1) D0 production values are recorded and the ADRs signed. (2) In production AUTO_CONFIRM_LIVE_ENABLED=false and the research ceiling holds. D2 default-deny is live at the Lock-5 mint, with drives showing both the tick and /forcescan refused while the flag is ON and no artefact exists. (3) C11, C1 (with MDE, n_eff and margins) and C12 are merged, headline intervals are restated, and v4 has a fetch date. (4) Gap and loop-lag metrics are exported from production, and the in-tick passes bound the gap to one analyze batch. (5) Chat and thesis budgets are separate and priced at current rates. (6) New rows carry analysis_id, code_sha, cfg_hash and strategy_hash; below-floor evaluations carry would-be levels; no live row is logged 'paper'. (7) The C2 and provisional C3 artefacts are committed and their pre-registered defaults applied; 'inconclusive' is an acceptable result. (8) Adjusters are in shadow, and the venue-priced close share is measured. (9) The Markets chart holds 200 bars and no /deepscan card is fabricated. (10) CLAUDE.md is under 25KB, with the freeze and merge queue on.

### Phase 1: One dataset, a cost model, a protection floor and a fast loop

*Next: weeks 2-8. 5-6 lanes: ledger/parity, research data, money path, chat eval, web/product, platform.*

Deliverables: A2, A3, A5, A7, B3, B6, C4, C5, D4, D6, D7, D8, D12, D15, E3, E7, F2, F4, F7, F8, F11, G2, G3, G4, G6, G13

**Gate to the next phase.** (1) The Idea Ledger labels at least 95% of evaluations within 8 days, refusals included, and the backfill is marked discovery. (2) The A7 cost-model version is stamped into every C endpoint. (3) PositionGuard runs behind its flag with all seven pass entries under the lock; production gap p99 is under 20s and fill-to-stop under 15s. (4) Outcomes are typed at every door, the Bitget replay corpus is green, and quantity drift is detected and alerted. (5) The funding panel is frozen with survivorship handling stated, and C5's discovery artefact is committed with its prospective test registered. (6) The chat eval baseline gates router/tool PRs, with live paired scoring for model changes. (7) CI gate p50 is under 12 min with 0 forgiven-flaky over 5 runs. (8) 100% of new signals carry setup identity. (9) An offsite restore drill has passed with the master key held separately.

### Phase 2: Accrue and test evidence; rebuild the copilot and surfaces on the ledger

*Later: weeks 8-20. Prospective windows keep accruing; verdicts arrive when each hypothesis reaches its registered n_eff, not on a calendar date.*

Deliverables: A4, B4, B5, C6, C7, C8, C9, C13, D5, D9, D14, E4, E5, E6, E8, E9, E10, F3, F5, F6, F12, F13, G7, G8, G9, G10, G12

**Gate to the next phase.** Autonomous confirms and size above the research ceiling become possible only when D2 reads an eligibility artefact meeting the KPI evidence standard for the running strategy_hash. If none qualifies by week 20, the operator records each hypothesis as refuted or inconclusive and records the pivot: carry or cross-sectional if either is still accruing toward its MDE, assistant-only if not. Thresholds are not relaxed. In either case the following must also hold. The chat eval shows at least 95% route accuracy, 0 confident wrong cards, 98%/97% groundedness, and 0 act-claims or injection-triggered actions. The scoreboard, pattern tables and chat tools read only ledger views. Decision parity (A4) is at least 95%. Fill-to-stop is under 15s on every execution venue. No money-path function exceeds 250 lines.

### Phase 3: Conditional scale-up and deferred work

*20+ weeks, each item conditional on its trigger*

Deliverables: D11, C10, F9

**Gate to the next phase.** Terminal and ongoing. Each capital stage (minimum, 25%, 100%) needs its own artefact update. Demotion is automatic under the D11 kill rules. C10 runs only if C3, C7 and C8 produce a keep list. F9 ships when polling cost or latency justifies it. Billing, per-user compliance, the outcome-trained in-house model and the per-trade event log are out of this plan's scope until a positive artefact exists.

## First two weeks, in order

1. **Read production before deciding anything (D0)** (S (half a day, operator plus one agent, read-only))
   - *Why first:* Every capital, budget and research decision in this plan assumes production values the repo does not contain. The code defaults (auto-confirm live on at 0.85, $1 budget) and the documented install (off at 1.0, $5) disagree. C2 is only runnable if llm_calibration.jsonl has enough LLM-sourced rows.
   - *Done when:* An ADR appendix records the flags, caps, budget, per-user switches, ledger row counts and date ranges, the LLM/RULE share, the venue-priced close share, budget-exhausted audit counts, monitor-gap samples and adopted positions with unread margin, with no secrets.
2. **Operator decisions and research mode (G0 + D1)** (S (1-2 days, including the offline minimum-gate drive over the live universe))
   - *Why first:* None of the measurement work needs size. The ledger and signal walks label from OHLCV. The margin-cap unit trap (MICRO_MAX_POSITION_USD is margin, not notional) must be settled deliberately. Deciding copy and behaviour items once avoids rework in C2, C3, E5, F7, F8 and D12.
   - *Done when:* Dated ADRs exist for posture, artefact schema, sealing strictness, positioning, practice fills, the C2/C3 defaults, the chat write policy, the per-user envelope, presets and the fixture-capture budget. Production shows AUTO_CONFIRM_LIVE_ENABLED=false and the margin caps derived from target notional, standard leverage and round-up headroom. The refused-symbol count is recorded, and /status says 'research mode'.
3. **Make parallel lanes mergeable (G14 + G11 + G5)** (S (days 1-3, one platform lane))
   - *Why first:* Everything else runs in 4 lanes at once. Stored ratchet totals, line citations and CLAUDE.md are where concurrent branches have broken merges before, and with a 45-minute preflight every bad rebase costs an hour.
   - *Done when:* The merge queue is on. CLAUDE.md growth is frozen and new lessons land in docs/lessons/. Ratchet totals are computed, with one re-record command. INCOME_MAP uses path::Qualname anchors checked by scripts/cite.py.
4. **Default-deny eligibility at the Lock-5 mint (D2)** (M (3-5 days; lands by end of week 1))
   - *Why first:* A config flag can be flipped back by mistake, and the /forcescan loop bypasses _auto_confirm_batch. Placing the gate at the one mint every non-human live confirm crosses makes 'no autonomous capital without prospective evidence' true in code, with the flag as a second line.
   - *Done when:* With AUTO_CONFIRM_LIVE_ENABLED=true and no artefact, drives through the tick and /forcescan place nothing. An artefact for another strategy_hash is refused. The executor's hard cap reads min(config, stage ceiling). test_trade_gate_parity names the refusal. The verdict renders on /status, the web backstop and /health without dollars.
5. **Build the inference utility, register hypotheses, start the prospective clock (C11 + C1 + C12)** (M (4-6 days, measurement lane))
   - *Why first:* Every later verdict depends on honest intervals, cost-net endpoints and data nobody has looked at. The frozen windows are all burned, and every day without a scheduled post-registration fetch is prospective data lost.
   - *Done when:* bot/utils/inference.py (day/week blocks across all symbols, 20-cluster floor, t fallback, anytime-valid sequences, design-effect estimator) is merged with a correlated-panel drive, and the headline intervals are restated. Registry YAMLs carry MDE, n_eff, delta, family and baselines. v1/v2/v3 are declared discovery-only. The sealing ledger is on, and v4's fetch date is committed.
6. **Engine-input hotfixes: split the LLM budget, fix prices, anchor config paths (E1 + D10)** (S (1-2 days))
   - *Why first:* A busy chat day silently moves the engine to rules-only, the mispriced table makes that happen sooner, and both contaminate C2. Starting the bot from another directory blocks every entry. Each is a one-day fix with direct money impact.
   - *Done when:* Thesis and chat caps are read from cost_by_category with per-category call counts. A drive shows chat over its cap leaves the analyzer calling the LLM. cost.py pins the verified current rates and books cache tokens. An engine started from /tmp loads the macro seed through repo_path().
7. **Measure and shorten the monitor gap (D3)** (S (2-3 days))
   - *Why first:* A filled limit can sit with no venue stop for sleep + 300s scan + 300s analyze. Research mode shrinks the stake but not the window. Step 1 needs no new concurrency, and its metrics size step 2.
   - *Done when:* Gap and event-loop lag are exported (p99 on /health, alerts at 60s and 2s). The positions pass also runs between analyze batches. Live symbols are on the WS feed. Production numbers are recorded for D15's design.
8. **Stamp identity on every row (A1)** (M (3-4 days))
   - *Why first:* Every later join (ledger, replay, scoreboard, pattern tables) needs a collision-free key, and every day of rows without it cannot be joined. The mode='paper' and confluence=idea.confidence mislabels corrupt rows written right now, and below-floor evaluations are unlabelable until they carry would-be levels.
   - *Done when:* A guard test finds the same analysis_id (ULID), code_sha, cfg_hash and strategy_hash in llm_calibration, decision rows, signal payloads and flight records. Live rows read 'live'. Below-floor evaluations carry counterfactual SL/TP. The daily LLM/RULE share is published.
9. **Score the LLM's live direction and run the provisional confidence table (C2 + C3)** (S (3-4 days, including the export and a frozen OHLCV join))
   - *Why first:* The LLM's share and edge are unmeasured, but the data is on the bot box. Confidence gates entries and is shown as a probability. Both pre-registered defaults remove claims rather than add them, so they can be applied on inconclusive results.
   - *Done when:* benchmark/llm_direction/result.json reports n and n_eff per source and paired verdicts against the rule-engine direction, sign(24h change), its negation and always-long, net of cost, measured from the next bar's open. The provisional C3 table is committed with C11 intervals. The defaults are applied and recorded: LLM blend weight to 0 unless it survives; no confidence '%' shown as a probability.
10. **Clean the learning labels and shadow the untested adjusters (B1 + B2)** (S (2 days))
   - *Why first:* The adaptive nudge (5 samples, sign of dollar P&L) and setup expectancy (count floor) move live confidence today, trained on aborts, practice and manual rows. Stopping this is cheap and removes a confound from every later measurement.
   - *Done when:* Outcome rows carry the new stamps. Guard tests show no abort, practice, manual or per-user row reaching an engine learner. Both adjusters log would-be deltas only. READY is a fixed-date evaluation rather than a per-refit recomputation. The readiness card lists all four adjusters.
11. **Measure live close pricing and start the funding panel download (A6 + C4 kickoff)** (S (2 days, plus a background download))
   - *Why first:* Realized closes feed the governor, Kelly and parity, so a high post-fix ticker share becomes the top execution item. Survivorship-complete funding history is I/O-bound and the long pole for carry, the most plausible edge, so its acquisition should start now.
   - *Done when:* The venue-priced share of the last 50 closes, with causes, is on /parity and loop-health, with an alert below 90%. The panel source's coverage of delisted contracts is verified and recorded. The funding-history download is running into storage outside git with manifests in git.
12. **Stop publishing broken or fabricated public readings; start collecting routing labels (F1 + E2)** (S (2-3 days, web/chat lane))
   - *Why first:* Every viewer sees the flagship chart collapse to 2 bars, and anonymous visitors see LONG cards derived from RSI<50. The eval corpus needs weeks of labelled turns and feedback before E3 freezes it.
   - *Done when:* limit=2 then limit=200 returns 200 rows, and the smoke test holds 200 bars after 5 minutes. /deepscan pushes scanned=False with no fabricated fields. Every turn is audited with rule, intent, confidence and tools. Feedback buttons store to conversation rows. The dead classifier is deleted and the read-intent regex freeze is written into the guide.
13. **Lean agent guide and backlog (G1)** (M (3-5 days, mostly scripted, platform lane))
   - *Why first:* About 330k tokens of incident narrative are paid by every agent session in this plan. Cutting it before the refactors means every later PR lands on the lean base, and it turns ~87 buried items into an owned queue.
   - *Done when:* CLAUDE.md is under 25KB with a size ratchet. docs/lessons/INDEX.md is generated and every lesson links a guard test. docs/BACKLOG.yaml anchors resolve. The PR template carries blast radius and the 30% rule.

## KPIs

| Metric | Today | Target | How measured |
|---|---|---|---|
| Autonomous live orders without an eligibility artefact (any door, /forcescan included) | Unknown in production (D0). Code default AUTO_CONFIRM_LIVE_ENABLED=True at 0.85, and the only barrier on /forcescan is that flag at the Lock-5 mint. | 0, enforced at the Lock-5 mint by D2, with drives through both loops in CI | Executor and compliance audit rows; the D2 drives |
| Size above the research notional | Code defaults of $100 margin per order and $500 total margin (about $500 notional per order at 5x); production unknown | Every live order within the research ceiling unless a stage artefact grants more | Executor audit rows (margin, leverage) compared against the D2 ceiling |
| Evidence standard for any capital | No prospective verdict exists, and every frozen window has already been read | An eligibility artefact for the running strategy_hash: registered before its data existed; prospective window; n_eff at or above the registered requirement; net of A7 costs; time-clustered lower bound above 0 at the money-family alpha; positive C13 portfolio result; at least 2 regimes or labelled regime-conditional. Otherwise capital stays at the research ceiling. | benchmark/eligibility/*.json, validated by a schema test that D2 reads |
| Prospective evidence clock | 0 hypotheses machine-registered; no post-registration data scheduled | All open hypotheses registered with MDE, n_eff and delta by week 2. v4 fetched on its date and every 4 weeks after. Each hypothesis shows accrued vs required n_eff. | benchmark/hypotheses/*.yaml status and the C12 access log |
| Inference hygiene in gates and public claims | Parity live edge uses an iid normal interval with a floor of 10 (parity.py:388-407). The arb verdict is iid. signal_edge clusters ideas by symbol-week while cross-symbol 24h correlation is 0.57-0.79. | 0 gates or public surfaces use any interval other than C11's. Headline results restated with their widened intervals. | C11 ratchet test; FROZEN_BENCHMARK.md restatement |
| Decision parity (replay vs recorded live decisions) | Unmeasured; the benchmark runs a different strategy | At least 95% of recorded live decisions reproduced in direction and gate verdict, with every mismatch itemized | benchmark/live_parity/result.json (A4) |
| Label trust and ledger coverage | 29 of 211 closes venue-priced before the v3 fix (after the fix: unmeasured). No ledger. Bot-side labels pruned at 14 days / 2,000 rows. Research rows censored at the 0.60 floor. | At least 90% of recent closes venue-priced. At least 95% of evaluations, refusals included, labelled in net R within 8 days, never pruned. | /parity card and B6 loop-health card; Idea Ledger coverage query |
| Position protection latency and event-loop lag | Gap bounded only by sleep + scan (up to 300s) + analyze (up to 300s). Loop lag unmeasured. No pass lock. Limit fills unprotected until the next pass. | Gap bounded by one analyze batch by the end of Phase 0. Gap p99 under 20s and loop-lag p99 under 2s by the end of Phase 1. Fill to stop under 15s (0s with preset SL/TP). | D3/D15 histograms on /metrics and /health; audit join of fill and stop timestamps |
| Chat correctness and safety on the eval | No eval. A 41-phrase probe found 3 confident wrong cards and 16 fall-throughs. | Route/tool accuracy at least 95%; 0 confident wrong cards; 0 act-claims; 0 injection-triggered actions or drafts; at least 90% of multi-turn scenarios passing | evals/chat scorecards: cassette CI for code, paired live runs for models |
| Chat groundedness | Unmeasured; only fabricated [skill] blocks and R:R are checked | Eval groundedness at least 98% (admin) and 97% (free). Production unverified-figure rate under 1% and 3%. At least 80% of read turns answered from a fresh tool reading. | E3 deterministic scorer; E6 shadow verifier on production turns |
| LLM economics | Chat can push the thesis to rules-only. The cost table runs 1.5x-3.75x high on Sonnet 5 and Opus. Admin cache hit rate about 0% and unaccounted. | 0 chat-caused RULE_ENGINE_BUDGET fallbacks. Booked cost within 5% of the invoice. At least 50% of admin input tokens are cache reads. | Audit count of RULE_ENGINE_BUDGET on days thesis spend is under its cap; cache_read_input_tokens booked in cost.py; invoice reconciliation |
| Trade-surface honesty | About 100% of engine signals lack setup identity. 34 constant pattern scores shown as percentages. The scoreboard shows win rate without R. | 0% of new signals missing identity. 0 unsourced pattern percentages. Every scoreboard cell shows n and mean R with a C11 interval. 'Survives' appears only on registered, prospectively replicated cells. | Signals-table null-column query; renderer guards; analytics payload test plus Chromium smoke |
| Activation and retention | 0% of self-admitted paper users' first Confirm succeeds; D7 and D30 not measured | At least 90% of first Confirms end in a practice fill or an explicit next step. D7 and D30 baselined in week 1 and rising. | Confirm funnel by ExecutionOutcome kind (G10), segmented by role and signup date |
| Engineering loop and throughput mix | CI gate 31m39s serial. Local preflight about 45 min. CLAUDE.md 1,324,924 bytes, touched by 112/174 commits. About 4.5% capability commits. 5 manual benchmark re-records in 6 days. | CI under 12 min (then 8) and dev loop under 3 min. CLAUDE.md under 25KB and touched by under 10% of commits. At least 30% edge or capability commits. 0 manual re-records. | CI step timings; size ratchet; monthly PR-label classification; git log over a rolling 4 weeks |

## Stop doing

- Stop letting the engine place autonomous live orders, or size above the research notional, on anything but a default-deny eligibility artefact. Stop relying on a config flag alone at a chokepoint that /forcescan can reach.
- Stop treating v1, v2, v3, or any window already read, as a holdout. Stop quoting 'PF on 2 of 3 snapshots' as evidence, and stop reading 'inconclusive' as 'refuted'.
- Stop quoting iid or symbol-week-clustered intervals in any gate, verdict or public claim. Stop testing direction against zero instead of against per-symbol cost and funding.
- Stop re-evaluating fixed 95% bars on accumulating data (READY on every refit, a live-edge verdict on every close). Use fixed evaluation dates or anytime-valid tests.
- Stop tuning knobs and adding voters or sweeps on the exhausted 1h-OHLCV surface. No new voter, knob or strategy PR merges without a registry entry, and a CI benchmark delta never justifies a merge. Stop running harnesses with --fetch.
- Stop showing unmeasured numbers as probabilities: hand-set pattern percentages, pushes gated on detector constants, '% confidence' as a win probability, /patterns 'Conf'. Stop letting /deepscan write the public Setups panel.
- Stop letting untested learners move live confidence, and stop training engine learners on aborts, practice fills, manual tickets, per-user rows or fill-priced labels instead of market labels.
- Stop creating a new outcome store for each question. Pattern, scoreboard, draft and signal outcomes all live in the one Idea Ledger.
- Stop fixing chat misroutes with another read-intent regex or lookahead; add the phrase to the eval corpus. Stop editing the Telegram and web dispatchers in parallel until E8.
- Stop giving the chat model any state-changing tool. A draft is staged only by the user's button, in reply to the user's own request.
- Stop shipping money-path behaviour straight to enforcement. Every new component needs a kill flag, backward-readable persistence, audit-only first, and replay fixtures for the calls it touches.
- Stop writing incident chapters into CLAUDE.md and leaving filed items in prose. Lessons go to docs/lessons, decisions to docs/adr, and filed items to docs/BACKLOG.yaml.
- Stop citing code by line number in docs, re-recording benchmarks and ratchet totals by hand, and adding object.__setattr__(CONFIG...) in tests.
- Stop writing a new mutation driver per slice, and stop adding source-scan tests for behaviour a drive can reach once the code is extracted.
- Stop promoting PF<1 presets and growing the directional engine's marketing surface. Stop spending review attention on out-of-horizon items (billing, per-user compliance, the in-house outcome model, the per-trade event log, the dashboard bundler).
- Stop batching 30-commit, 200-file PRs: one stage, one check or one intent family per PR.

## Decisions the owner needs to make

1. **Live directional capital while no strategy has an eligibility artefact**
   - *Options:* (a) Full shadow, no live orders. (b) Venue-minimum live with autonomous confirms off, plus the D2 default-deny gate in code. (c) Status quo, relying on the governor.
   - *Recommendation:* (b). It stops losses and keeps fills, fees and close pricing flowing for cost modelling and labels. Set the cap as target notional divided by standard leverage (MICRO_MAX_POSITION_USD is margin). Capital rises only through D2 and D11 artefacts.
2. **Evidence standard and holdout sealing strictness**
   - *Options:* (a) Hard access control on prospective partitions for money-family hypotheses, logged reads for display. (b) A logged honour system for everything. (c) No sealing; reuse existing windows.
   - *Recommendation:* (a). The frozen windows are already burned, so sealing is the only way to get a clean confirmation, and hard control is cheap for the few money-family hypotheses.
3. **Directional research time-box given its power**
   - *Options:* (a) Keep the directional program open-ended. (b) Register directional hypotheses only at MDEs the data can reach, and stop at week 20 without a candidate. (c) Stop directional research now.
   - *Recommendation:* (b). Put most research effort into carry and cross-sectional factors, whose effective sample sizes are far larger, and record directional results as inconclusive rather than chasing them.
4. **The product's money core**
   - *Options:* (a) A directional TA/LLM alpha engine. (b) A trustworthy copilot plus whatever validated core survives (carry or market-neutral). (c) Assistant-only, no autonomous trading.
   - *Recommendation:* (b), decided by artefacts at the Phase 2 gate. Commit now to pivoting if no candidate reaches confirmation.
5. **The LLM's authority over trade direction**
   - *Options:* (a) Keep the current blend weight. (b) Blend weight 0 unless C2 shows it survives against the best baseline net of cost. (c) Replace it with the rule engine entirely.
   - *Recommendation:* Pre-register (b). Removal needs no proof. A survival on existing rows only nominates it for prospective confirmation, and it stays analyst and narrator in chat and on cards either way.
6. **Confidence as a displayed percentage and as a sizing input**
   - *Options:* (a) Keep both until a test says otherwise. (b) Show rung words or 'unmeasured' and keep confidence-scaled sizing off until the decisive prospective test on uncensored ledger rows passes. (c) Drop confidence entirely.
   - *Recommendation:* (b). The backtest test is censored at the floor and uses a different blend, so it cannot license the claim.
7. **Chat write capability**
   - *Options:* (a) Read-only tools only. (b) Staged drafts: the model proposes, the user presses 'Stage', and the ticket enters as source='manual' with origin='chat_draft'. (c) The model registers drafts directly.
   - *Recommendation:* (b), shipped only after E3's injection cases pass. (c) would let a planted instruction in a tool result put a one-tap live ticket in front of the operator.
8. **Public positioning of signals and patterns**
   - *Options:* (a) Observations with their measured record, 'exploratory' labels and a 'no demonstrated edge' line until an artefact exists. (b) Trade signals with confidence percentages. (c) Hide signals until something validates.
   - *Recommendation:* (a). It is consistent with the evidence and the repo's honesty rules, and keeps the surfaces useful.
9. **First run for self-admitted paper users**
   - *Options:* (a) Labelled PRACTICE fills. (b) LIVE-ONLY with a watch-and-link onboarding and no Confirm. (c) Status quo dead-end.
   - *Recommendation:* (a), with B1's exclusions so practice never trains engine learners or reaches public records.
10. **Whether practice, manual and per-user outcomes feed engine learners**
   - *Options:* (a) Never; engine learners read engine rows and market labels only. (b) Opt-in, tagged and weighted. (c) Status quo, silently mixed.
   - *Recommendation:* (a) now; build separate per-user learners later if personalisation needs them.
11. **PRE_CAP_TIGHTENS_CAP policy**
   - *Options:* (a) Keep it empty. (b) {governor, drawdown_recovery}. (c) All seven pre-cap kinds.
   - *Recommendation:* Measure first (D7). Expected default (b), unless the 3-snapshot table shows worse drawdown or a worse worst fold. The older benchmark predates the loss-at-stop base and must be re-run.
12. **Per-user Telegram live without an enforce-mode Authority Envelope**
   - *Options:* (a) Require an envelope before PER_USER_LIVE_ENABLED is ever turned on. (b) Correct docs/guardian_authority.md and every surface that repeats it. (c) Keep per-user live off.
   - *Recommendation:* (c) during research mode and (a) before re-enabling. Refuse withdraw-enabled keys at /connect in any case (D12).
13. **Marketplace presets with PF below 1**
   - *Options:* (a) Hide them. (b) Show them with their verdict and copy/follow disabled. (c) Status quo.
   - *Recommendation:* Hide them from public listing. Inside the logged-in marketplace, keep (b) until a strategy has an artefact.
14. **Perp-panel data source and research dependencies**
   - *Options:* (a) Venue APIs only (survivors only). (b) A public archive that retains delisted USDT-M contracts, verified before choosing. (c) A vendor (Coinalyze, Tardis, Amberdata). Separately: pure Python only, or a [research] extra with scipy/statsmodels/scikit-learn.
   - *Recommendation:* (b) if verification shows delisted klines and funding are present; otherwise (c) for funding at least. Allow the [research] extra, with import-linter keeping it out of the runtime.
15. **Chat model routing and per-user LLM economics**
   - *Options:* (a) Switch admin chat to the newer Sonnet release now. (b) Fix prices and the budget split now; switch after a paired eval with explicit effort and cache layout. (c) Per-tier routing by eval score per dollar, with per-user quotas.
   - *Recommendation:* (b) immediately via E1 and E7, then (c) via E10. Chat must never change which trades the engine takes.
16. **Restructuring CLAUDE.md**
   - *Options:* (a) A lean guide under 25KB plus docs/lessons, docs/adr and docs/BACKLOG.yaml, frozen from day 1. (b) Keep appending.
   - *Recommendation:* (a). It is the largest single velocity lever in an agent-built repo, and it makes the backlog decidable.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| No edge is confirmable within the horizon. With a 24h SD near 4 ATR and cross-symbol correlation of 0.57-0.79, single-name direction needs on the order of 1,500 independent windows to detect a 0.3-ATR net effect, and prospective data accrues slowly. | State MDE and n_eff up front so 'inconclusive' is expected rather than surprising. Prioritise market-neutral carry and cross-sectional factors, whose effective n is far larger. Time-box directional research at week 20. Capital stays at the research ceiling until an artefact exists, and the pivot to copilot plus a validated risk core is recorded as a success condition. |
| Sealing, pre-registration and freezes slow the research lane, and people route around them. | Discovery work continues freely on the burned windows and is labelled exploratory. Sealing covers only prospective partitions and money-family hypotheses. Every read is logged rather than silently blocked for display-family work. The freeze applies only to the strategy_hash under accrual, and replay-byte-identical fixes still merge. |
| Research mode and honest labelling ('exploratory', 'unmeasured', no confidence percentage) hurt engagement. | Signals keep publishing with measured outcomes, practice trading works (F8), and the per-signal page, scoreboard, staged drafts and personal digest make the measured record the product. Retention and thumbs rate are tracked rather than assumed. The stance is reversed by an artefact, not by opinion. |
| The research-mode margin cap is set in the wrong unit, refusing every order or allowing more notional than intended. | D1 derives the cap from target notional divided by standard leverage, with round-up headroom. The minimum gate runs offline over the live universe before switching. Executor audit rows are checked for margin and leverage in week 1, and D2's ceiling is a second bound. |
| The cost model understates costs on the momentum-selected, low-liquidity names live trades, and minimum-size fills cannot calibrate impact at scale. | A7 measures per-symbol spread and impact from book snapshots and fills, labels impact at larger size as extrapolated, and D11 promotes stage by stage, re-measuring realized costs at each stage before the next. |
| The perp panel has survivorship or look-ahead bias (delisted contracts missing; realized funding used as if known in advance). | Verify the archive's delisted coverage before choosing it, or buy a vendor. Print the survivorship gap on every artefact. Record funding with its publication time and decide entries only on prior information. Evaluate universe replays only on (symbol, tick) pairs from the live candidate log. |
| Operator bandwidth becomes the bottleneck. In research mode the operator is the only live confirmer and also approves ADRs, fixture-capture budgets and production exports. | Batch the week-1 decisions into one G0 session informed by D0, and batch production reads into D0. Give lanes explicit owners. Lanes wait rather than proceed without a needed sign-off, and nothing reaches money early. If a lane slips, its gate waits. |
| PositionGuard concurrency or the god-function refactors cause money-path regressions. | D3 first needs no new concurrency. D15 adds one lock taken by all seven pass entries, runs behind a kill flag with the in-tick passes kept as backstop, and moves analyzer CPU off the loop. G9 executor and confirm stages require D4/D14 replays for every call site they touch, since the benchmark and red team never import those paths. |
| Persisted-format changes (D9 schema, ledger, eligibility artefacts) break rollback or restore. | Round-trip tests in both directions, so the previous build reads the new rows. Transitions are audit-only before they assert. The ledger uses the SQLite backup API. G13's monthly restore drill uses separately held keys. |
| Parallelising tests surfaces hidden order-dependence and state leaks. | Test-only state root first (refused in live mode), then xdist as a non-blocking shadow with loadfile distribution. Flake re-runs stay serial, the network-reach ledger is aggregated, a nightly pytest-randomly job runs, and forgiven-flaky is held at 0. |
| Idea Ledger writes or labelling add latency or REST load on the scan path. | Batched off-thread WAL writes that fail open with an audited drop count, symbol-batched or offline labelling from recorded candles, a measured analyze-latency budget, and a REST budget in G10 that includes the degraded-mode counter. |
| The model migration changes behaviour silently (default effort, forced tool_choice and disabled-thinking 400s, refusals), and cheaper tiers call tools unreliably. | Switch only after the E3 baseline, using paired live comparisons. Set effort explicitly, use tool_choice auto with strict tools, handle refusal stop reasons with fallbacks, and keep the old id behind a flag. Retire a regex fast path per tier only where the tool wins. |
| Chat becomes a prompt-injection channel once drafts exist. | All tools stay read-only. A draft registers nothing until the user presses 'Stage'; offering one requires the user's own message to ask; tool text is never a trigger. The Guardian defang runs on tool results, and E3 injection cases gate E5. |
| Cutting CLAUDE.md loses incident context agents currently absorb. | A generated lessons INDEX by subsystem, a 'read lessons for X before editing X' rule, and a CI check that every lesson links an existing guard test. The guard tests are the durable memory. |

## How this plan was produced, and what it cannot see

- Seven read-only surveys (signals and edge, patterns, chat agent, website, learning loop, execution and backlog, developer velocity) read the code and cited `file:line`. A completeness critic added nine missed areas (market data freshness, live universe vs benchmark, news/on-chain inputs, personalization, onboarding, monetization, compliance, key custody, LLM pricing, disaster recovery) and re-checked 16 high-impact claims: 15 held, 1 was narrowed (signal outcome labels are pruned on the bot side but kept in the website database).
- Three plan drafts were written from different angles (edge-first, copilot-first, platform-first). Two judges scored them; edge-first won both (impact 9, honesty 9.5). The synthesis was then attacked by a quant skeptic and an engineering lead, who found 5 fatal issues (among them: the capital gate was on the wrong chokepoint because `/forcescan` bypasses `_auto_confirm_batch`; the proposed "holdout" windows had already been read; the planned intervals were too narrow for correlated symbols; the chat draft design would have bypassed ten `source == "manual"` checks) and 20 wrong claims. The final plan fixes all of them.
- Load-bearing facts were re-checked by hand: the candle cache key omits `limit` (`app/routes/market.js:132`); `AUTO_CONFIRM_LIVE_ENABLED` defaults True at 0.85 in `bot/config.py:2553-2557` while `.env.example:197-198` sets false/1.0; the `/forcescan` loop calls `confirm_trade(tid, user_id="auto")` directly (`bot/core/engine.py:9269`); `PAPER_SIM_OPT_IN_ENABLED` defaults False (`bot/config.py:2587`); idea ids are 32-bit (`bot/utils/models.py:94`); `/deepscan` builds `"dir": "LONG" if rsi < 50 or chg > 0` (`bot/skills/skill_registry.py:4159`); the default LLM budget is $1/day (`bot/config.py:1300`); admin chat runs `claude-sonnet-5` (`bot/llm/provider.py:283`).
- **Not visible from here:** production `.env`, the `data/` directory, live logs and the real LLM/rule mix. Live figures (win ~22%, PF ~0.6, 182 of 211 closes ticker-priced) come from cards quoted in CLAUDE.md and are unverified. That is why step 1 is a read-only look at production before any decision.
- The clone is shallow, so the history numbers cover 6 days only.
