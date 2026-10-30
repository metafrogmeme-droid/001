# Bitget AI Hackathon readiness: Weekend Event Agent (2026-10-30)

Input: the operator's kit `RUNECLAW_Bitget_Hackathon_Kit.md` (Agentic Trading track,
Event-driven sub-theme, Bitget Demo paper trading only) and the Step 0 result
`docs/research/prereg/weekend_stock_perps_2026-10/REPORT.md`. Every "has" below was
read in the code on 2026-10-30 at the commit this file was added in. Citations are
`path::symbol`. Nothing here was run against Bitget.

Effort: **S** about a day, **M** two to four days, **L** a week or more. One engineer.

## Summary

- Step 0 **failed**. No weekend price rule survives costs. The news layer could not
  be tested offline. Under kit rule #4 the entry cannot claim an edge. It can claim a
  risk cage, replayable decisions and an honest forward test (section 3).
- RUNECLAW has most of the plumbing: Bitget v3 signing, clientOid idempotency,
  hash-chained JSONL, a fail-closed risk engine, a kill switch that flattens, an LLM
  layer with schema-constrained output, a nightly self-audit with a human merge gate,
  and a public decision-ledger page.
- The main gaps:
  1. **The Bitget v3 transport is not Demo-safe.** `bot/core/bitget_v3_client.py::BitgetV3Client`
     sends no `paptrading` header and has no demo setting. Audit finding M-07
     (`audit/workflow_raw_findings.md`) is still open, and no test covers it.
  2. **The LLM does not decide by default.** `LLM_DECIDES` is off
     (`bot/config.py::AnalyzerConfig`), so the rule engine decides. Decisions are
     triggered by scans, not by events. No kit-schema decision exists (HOLD/REDUCE/hedge).
  3. **The engine's live path cannot run autonomously in Demo** without weakening a
     guard. An autonomous confirm needs `AUTO_CONFIRM_LIVE_ENABLED` plus an eligibility
     record (`bot/core/live_eligibility.py`), and Demo through the engine counts as live.
     The agent must therefore be a separate process that never imports the live executor.
  4. **Missing pieces:** no private WebSocket (order channel or Demo hosts), no
     event-source layer for US equities, no 5-minute equity log, and no per-type
     decision logs in the kit's shape.

## 1. Requirement by requirement

| # | Kit requirement | What RUNECLAW has (verified) | Partial / missing | Effort |
|---|---|---|---|---|
| 1 | LLM makes every trade decision; invalid JSON or timeout gives HOLD | `bot/core/analyzer.py::Analyzer._llm_thesis` asks the LLM. A parse failure returns no trade (the "C-07 FIX" branch audits `LLM_PARSE_FAIL`). `bot/config.py::AnalyzerConfig.llm_decides` (env `LLM_DECIDES`) is **off by default**: the rule engine decides and the LLM runs as a shadow. Calls are bounded by `CONFIG.llm.timeout_seconds`. | The LLM is asked only after a scanner signal, never on a news event. It has no HOLD, REDUCE or CLOSE vocabulary. Flipping `LLM_DECIDES` inside the engine does not meet the kit, because the confluence blend and the scanner still choose what is asked. **Missing: a standalone REASON module** (event in, kit JSON out, HOLD on anything invalid). | M |
| 2 | Demo only: Demo API key, header `paptrading: 1`, Demo WS hosts | `bot/config.py::ExchangeConfig.sandbox` (`BITGET_SANDBOX`, code default false; `.env.example` sets true). `bot/core/venues.py::BitgetVenue.create_exchange` calls ccxt `set_sandbox_mode(True)`, which sends the header. `bot/core/exchange_credentials.py::_bitget_balance_probe` validates a key in the demo environment. `bot/main.py::_banner` prints the environment. | **Defect:** `bot/core/bitget_v3_client.py::BitgetV3Client._headers` never sends `paptrading`, and `from_config` drops `cfg.sandbox`. The executor uses this client for stops, positions and flash close (`bot/core/live_executor.py::LiveExecutor._place_sl_tp_v3`, `::_fetch_v3_positions_raw`, `::_flash_close_position`), so in Demo those calls go to production (audit M-07). `bot/core/ws_feed.py::BITGET_WS_URL` is the production public host. Missing: Demo WS hosts, separate Demo key names, and a refusal to send any private request without the header. | S (client) + S (guard) |
| 3 | Decision schema (action, symbol, conviction, horizon, thesis, invalidation, stop, TP, hedge, evidence, regime) | `bot/core/analyzer.py::THESIS_JSON_SCHEMA` (direction LONG/SHORT/null, confidence, reasoning). `bot/llm/provider.py::llm_complete` takes `json_schema` and uses structured output where the model supports it (`model_supports_structured_output`), with a tolerant parser as fallback (`tests/test_ai2_structured_outputs.py`). | Missing: the kit schema, its validator, and the fallback "anything not exactly valid is HOLD". | S |
| 4 | Deterministic risk gate the LLM cannot override, with the kit limits | `bot/risk/risk_engine.py::RiskEngine._evaluate_locked`: fail-closed checks, documented in `config/risk_manifest.yaml` (17 closed, 1 open, 3 skip). It covers max open positions 5 (matches the kit), symbol exposure 20% (matches, but **margin**-based), portfolio exposure 80% (**margin**), daily loss 5%, drawdown 10% paper / 7% live, and STALE_DATA (idea age 300 s). The kill switch `bot/core/engine.py::RuneClawEngine.emergency_halt_all` trips every breaker and flattens. `bot/core/order_rules.py::reference_session_state` is a DST-aware US session clock. | **Differs from the kit:** daily −2%, drawdown −8% from peak, leverage ≤ 2x (repo default venue 10x, sizing 5x), gross ≤ 100% **notional**, market-data age > 60 s (the repo measures idea age), no entries within 15 min of 09:30 ET (no such rule), and sizing = equity × 0.5% ÷ stop × conviction with zero below 0.55 (the repo sizes by `MAX_POSITION_PCT`, vol target and Kelly). A breach trips the breaker but **does not flatten**: `emergency_halt_all` is reached only from Telegram (`bot/skills/callback_handler.py`). Recommendation: a small new pure gate with the kit's figures, written in RiskEngine's fail-closed style, rather than re-parameterising RiskEngine. | M |
| 5 | Every position has a server-side stop | Live entries place SL/TP on the venue (ccxt trigger path, or `LiveExecutor._place_sl_tp_v3` on UTA). The v3 place-order body accepts a preset `stopLoss` (`docs/bitget-uta/trade.md`, place-order request table). | Not Demo-safe through the v3 path (row 2). The new agent should attach `stopLoss` on place-order, confirm the stop exists, and flatten if it does not. | S |
| 6 | UTA v3 REST orders; confirm state from the private WS order channel | Signed v3 REST: `BitgetV3Client.sign` / `.build_request` (tested in `tests/test_bitget_v3_client.py`). Order state is read over REST: `bot/core/order_state.py::order_status`, `::is_filled_status`. Public WS: `bot/core/ws_feed.py::BitgetWSFeed` (ticker and trade, with staleness filtering in `get_snapshot`). | Entries go through ccxt, not `POST /api/v3/trade/place-order`. **Missing: private WS** (login handshake, order and position channels; the format is documented in `docs/bitget-uta/README.md` and `docs/bitget-uta/websocket.md`, but nothing implements it), Demo WS hosts, and a REST fallback when the WS is silent. | M–L |
| 7 | clientOid idempotency | `bot/core/live_executor.py::LiveExecutor._client_oid` (deterministic per trade id), `::_find_order_by_client_oid` (recovery after a timeout), `::_reconcile_unverified_submissions`. Tested in `tests/test_order_idempotency.py`. | Reuse the pattern in the agent's v3 place-order. | S |
| 8 | Round every order to the instrument rules | `LiveExecutor._round_price_to_market` (ccxt `price_to_precision`), `amount_to_precision`, `LiveExecutor._exchange_minimum_gate`. `scripts/weekend_perps_study.py::universe` reads `/api/v3/market/instruments` (`symbolType`, `status`, `launchTime`). `bot/core/equity_bucket.py` detects tokenized equities from the instrument row. | Missing: rounding from the v3 instrument fields (price/size multipliers, minimum order amount) for a direct-REST path, plus its unit tests. | S |
| 9 | Token-bucket rate limiter, exponential backoff, reconcile every 5 min | `bot/utils/rate_limiter.py::AsyncRateLimiter`. It is an even-spacing limiter (60/max_rpm seconds between calls, no burst), although its docstring says token bucket. ccxt `enableRateLimit`. `LiveExecutor.reconcile_positions` runs on every engine monitor pass and at startup. | Missing: per-endpoint budgets (place-order 10/s/UID; rTokens lower), an exponential backoff helper (none found in `bot/`), cancel-and-replace for rTokens (no modify), and a 5-minute reconcile loop in the agent. | S–M |
| 10 | Append-only JSONL, one file per type (events, llm_calls, decisions, risk_verdicts, orders, fills, equity every 5 min, review_reports) | `bot/utils/logger.py` (`trade.jsonl`, `risk.jsonl`, `system.jsonl`, `scan.jsonl`, each line carrying `prev_hash`). `bot/utils/audit_chain.py::AuditChain` (SHA-256 chain, `verify`, `sign_latest_batch` attestation). `bot/guardian/flight_recorder.py` (decision provenance including `prompt_hash`). `bot/core/equity_record.py::record_daily_equity` (one row per **day**). | Missing: the kit's eight per-type files with ids that join (event_id → llm_call_id → decision_id → clientOid → fill), and 5-minute equity rows. Reuse the chain for tamper evidence. | S–M |
| 11 | Daily review: P&L by event type, hit rate by conviction bucket, live-vs-backtest drift; versioned prompt diffs applied only after human approval | `bot/core/self_audit.py::SelfAudit`: nightly, advisory, allowlisted knobs, each proposal measured on a frozen benchmark, human applies. `bot/learning/prompt_opt.py::PromptOptimizer.register_version` (version id, prompt hash, safety validation). `bot/core/trade_postmortem.py`, `bot/core/trade_journal.py`. | Missing: the kit's attribution tables, prompt files under version control with a hash, an approval record (who, when, which diff) written to `review_reports`, and a refusal to load an unapproved prompt version. No backtest exists for an LLM agent, so "backtest drift" can only be measured against the shadow rule (section 3). | M |
| 12 | Metrics: return, Sharpe, Sortino, max DD, Calmar, win rate, profit factor, avg R, turnover, fees, beta to hedge, % HOLD, % blocked, LLM cost | `bot/core/metrics.py::MetricsEngine.compute` (Sharpe, Sortino, Calmar, profit factor, attribution). `bot/backtest/metrics.py`. `bot/core/cost.py::CostTracker.record_llm` and `bot/llm/usage.py` (token cost). | `MetricsEngine` returns `0.0` for Sharpe and Sortino when it cannot compute them, which the repo's "unreadable is never zero" rule forbids on a new surface. Missing: one script that recomputes every metric from the raw JSONL (kit scorecard item), plus beta, % HOLD, % blocked, turnover and avg R. | M |
| 13 | Read-only web dashboard: equity curve, open positions, decision feed with reasoning | `app/routes/public_flight.js` + `app/public/flight.html` (public decision ledger, dollar-stripped by `sanitizeRecord`). `app/public/dashboard.html`, `dashboard_api.py`. `app/lib/admin_preview.js::flag_on_for` gates a page to admins. | Missing: a page that reads the agent's JSONL. A public version must show percent and ratio only (CLAUDE.md public-surface rule). A new `/api` route needs `authMiddleware` or `optionalAuth`. | M |
| 14 | Tests: signing, rounding, risk gate, JSON-schema fallback, kill switch | Signing: `tests/test_bitget_v3_client.py`. Idempotency: `tests/test_order_idempotency.py`. Schema fallback: `tests/test_ai2_structured_outputs.py`. Flight recorder: `tests/test_flight_recorder.py`. | Missing: tests for each new module, **a test that every Demo request carries the header** (none exists anywhere), a kill-switch drill test, and a static test that the agent never imports `bot.core.live_executor` or `bot.core.engine` (as `bot/sleeve/paper.py` promises for itself). All of it must pass `scripts/preflight.py` and keep the ratchets (ruff, mypy, honesty, unreachable-methods 53). | M |
| 15 | One-command start; clean clone in under 10 min | `Makefile` (`run-telegram`, `run-scan`, ...), `scripts/launch_all.sh.template`. | Missing: `python3 -m <agent>` with its own `.env.example` section. The full RUNECLAW install and preflight (about 45 min) do not fit "clean clone in under 10 min". The agent needs a minimal requirements set (operator question 5). | S |
| 16 | Runbook | `docs/LIVE_HARDENING_RUNBOOK.md`, `docs/TESTNET_SETTINGS.md`. | Missing: an agent runbook covering kit Part 2 steps 1–7, the kill-switch drill and log export. | S |
| 17 | 3-minute demo script and submission answers | `docs/SUBMISSION.md` (S1 entry), `docs/gitbook/agent-hub-alignment.md`. | Missing for this entry. Write it last, from real logs. | S |
| 18 | SENSE: market WS plus news, macro and earnings feeds normalised to Event objects | `bot/core/news.py::NewsRadar` (public **crypto** RSS, heuristic impact), `bot/core/news_byon.py` (CryptoPanic, bring your own key), `bot/macro/calendar.py::MacroCalendar` and `bot/core/macro_events.py::MacroEventProvider` (FOMC, CPI, NFP, ... with blackout windows). | Missing: US-equity company news and earnings sources, the Event model `{id, ts, source, tickers, category, headline, novelty, raw}`, dedupe/novelty, and weekend replay capture. | M–L |
| 19 | Startup instrument scan; Demo availability; `universe.json` | `scripts/weekend_perps_study.py::universe` (stock-type perps by turnover, production public endpoints). `bot/config.py::US_STOCK_SYMBOLS` (a hand list of `R*` and `*ON` names). | Missing: the scan against the Demo environment, and the fallback to USDT-M stock perps where rTokens are absent. | S |
| 20 | Beta hedge (index or BTC proxy) | The Step 0 study hedged with `SPYUSDT`. | Missing in code: sizing the hedge leg and treating a pair as one position in the gate. | S–M |
| 21 | Secrets only from `.env`; none in repo history | `bot/config.py::_env_secret`, `bot/utils/secret_shapes.py`, `bot/utils/outbound.py::reply_safe`. | Missing: Demo key names kept separate from `BITGET_API_KEY` (row 2). | S |
| 22 | Step 0 edge-validation notebook | Done as a sealed pre-registration plus a report (`docs/research/prereg/weekend_stock_perps_2026-10/`). Verdict FAIL. A forward follow-up is sealed (`docs/research/prereg/weekend_reversal_forward_2026-10/`). | Not a notebook. The report is the deliverable; link it as is. | done |

## 2. What can be reused directly

| Piece | Reuse | Note |
|---|---|---|
| `bot/core/bitget_v3_client.py::BitgetV3Client` | Directly, **after** a demo setting is added (header on every request, fail closed without it). | Fixing it also closes M-07 for any `BITGET_SANDBOX=true` deployment of the main bot. It is on the order path, so read the INDEX chapters on Bitget v3 and sandbox first. |
| `bot/utils/audit_chain.py::AuditChain`, `bot/guardian/flight_recorder.py` | Directly, to seal each decision with its event, LLM call, risk verdict and order ids. | Tamper evidence is a strong judge-facing proof. |
| `bot/llm/provider.py::llm_complete` (+ `json_schema`), `bot/core/cost.py::CostTracker`, `bot/llm/usage.py` | Directly for the REASON call and LLM cost. | Budget: `LLM_DAILY_BUDGET_USD`, default 1.0 (`bot/config.py`). |
| `bot/core/order_rules.py::reference_session_state` / `is_reference_session_open` | Directly for the "flat before 09:30 ET" and "no entries in the last 15 min" clock. | US holidays are not modelled (`bot/core/stock_trading.py` says so). Add a holiday list or treat a holiday Monday as a weekend day. |
| `bot/core/equity_bucket.py` detection; `scripts/weekend_perps_study.py::universe` | Pattern for the instrument scan. | Point it at Demo with the header. |
| `LiveExecutor._client_oid` / `_find_order_by_client_oid` | Copy the pattern, not the class. | Importing `LiveExecutor` would pull in the operator's order path. |
| `bot/utils/rate_limiter.py::AsyncRateLimiter` | Directly as the per-endpoint spacer. | Add burst and backoff on the agent side. |
| `bot/core/self_audit.py`, `bot/learning/prompt_opt.py` | Pattern for the review loop: propose, measure, human approves, version bump. | — |
| `app/routes/public_flight.js`, `app/lib/admin_preview.js::flag_on_for` | Pattern for the dashboard page: admin preview first, public version percent-only. | — |
| Telegram (`bot/skills/`) | Optional: an operator-only `/agent` status and kill command that reads the agent's state file. The bot process must stay a reader, not a writer, of the agent's state. | — |
| `bot/risk/risk_engine.py::RiskEngine` | **Not directly.** Its limits are margin-based, its stale check is idea age, and it is wired to `TradeIdea`, `PortfolioTracker` and the operator's book. Reuse its fail-closed conventions and the manifest idea (a YAML stating each check, held to the code by a test). | — |
| `RuneClawEngine` FSM / live path | **Not for the Demo agent.** Running there means `LLM_DECIDES`, `AUTO_CONFIRM_LIVE_ENABLED` and an eligibility record. Adding a record to make a hackathon run autonomous would weaken the guard for the real bot. Copy the FSM vocabulary (`bot/utils/models.py::AgentState`) only. | — |
| xsmom + BTC regime sleeve (`bot/sleeve/paper.py`, `bot/sleeve/xsmom_regime.py`) | Can keep running as its own paper book (`SLEEVE_PAPER_ENABLED`). Not the entry (section 3). | — |

**Demo or paper mode with the paptrading header today:** only through ccxt
(`BitgetVenue.create_exchange` and `_bitget_balance_probe`). There is no Demo
WebSocket, and the v3 REST client has no demo mode. `bot/core/practice_fill.py` and
`bot/sleeve/paper.py` are internal simulated books, not Bitget Demo.

## 3. Strategy: what the agent should trade, and how to say it

**What is known.**
- No price-only weekend rule survives taker costs on 35 weekends.
- Early-weekend moves against SPY partly reverse before Monday 09:00 ET
  (correlation −0.31 discovery, −0.25 holdout). The size of the reversal is about
  the size of taker costs.
- Whether an LLM reading weekend news adds anything is **untested**.
- A forward test of the reversal at maker cost is sealed for the 16 weekends from
  2026-10-30 to 2027-02-15, and is evaluated once after that. It assumes maker fills;
  it does not measure them.

**Recommendation: an LLM event classifier with a conservative, HOLD-heavy policy, and two baselines that place no orders.**

1. **Primary (the LLM decides).** On each normalised event, plus a scheduled
   Sunday 20:00 ET review of each symbol's Friday-to-Sunday move against SPY, the
   LLM returns the kit schema.
   - HOLD is the default. The prompt says "no trade" is the expected answer, and the
     gate zeroes any conviction below 0.55.
   - The LLM sees the measured reversal statistic as context, labelled "observed, not
     validated, about equal to cost".
   - Entries are post-only (maker) limit orders, SPY-hedged, flat by 09:15 ET.
2. **Shadow baselines in the logs, no orders:**
   - (a) always HOLD;
   - (b) the deterministic maker reversal rule R2 from the sealed forward test.
   This lets judges see whether the LLM did better than doing nothing and better than
   a dumb rule. It is also the only honest stand-in for the kit's "live vs backtest
   drift".
3. **Not the entry:** the xsmom + BTC regime sleeve. It rebalances every 14 days on
   daily bars, so a 14-day run gives one or two decisions. The LLM does not decide
   there. It was studied on spot, not perps. It is not event-driven.

**Framing for judges.** "Step 0 failed and we say so. The agent is a forward test of
one hypothesis, inside a risk cage the LLM cannot override. Every fill traces to an
event and an LLM call. Most decisions are HOLD, by design." Do not show a return
figure as evidence of skill.

**What a 14-day Demo run (2 weekends) can show:**

| Evidence | Can it be shown in 14 days? |
|---|---|
| Uninterrupted loop; valid-JSON rate; LLM latency and cost; % HOLD; % blocked by rule | Yes, as counts with dates. |
| Risk gate blocking a real LLM proposal; kill-switch drill (flatten, OPEN disabled, alert) | Yes. The drill is scheduled and logged. |
| Flat before the cash open on each Monday; stops present on every position; reconciliation clean | Yes. |
| Maker fill rate, time to fill, fees, slippage against mid | Yes, and the forward test only assumes these. Caveat: these are fills on Bitget's Demo matching engine, not the live book. |
| Net P&L, max drawdown, profit factor against the HOLD and R2 baselines | Reported, but about 2 × 30 symbol-weekends at most cannot separate skill from luck. Say so in the README. |
| One review → approved prompt change → before/after | As a demonstration of the process only. The before/after has no statistical weight. |

The Demo run must not change the sealed forward test (its rules, cost or weekends).
Its results are reported separately from it.

## 4. Phased build plan (smallest runnable Demo agent first)

Safety rules for every phase, from CLAUDE.md:
- **Demo/testnet only.** The agent reads `BITGET_DEMO_API_KEY`, `BITGET_DEMO_API_SECRET`
  and `BITGET_DEMO_PASSPHRASE`, never `BITGET_API_KEY`. It refuses to start if a Demo
  key equals the operator's live key.
- **Fail closed on the environment.** It refuses to send a private request without
  the `paptrading` header. At startup, the same read-only call must succeed with the
  header and be refused without it (wrong environment). Anything else exits.
- **Flags off by default.** `EVENT_AGENT_ENABLED` and `EVENT_AGENT_SEND_ORDERS` both
  default off. With orders off the agent is a dry run.
- **Admin only.** Telegram control is operator-only. The web page starts behind
  `flag_on_for`. A public page shows percent only.
- **No mainnet signing.** The server never holds mainnet keys and never signs. The
  agent runs on the operator's box, not the web container. It never imports
  `bot.core.live_executor` or `bot.core.engine`, and a static test enforces that.
- **Repo process.** The preflight passes after the last edit. Ratchets only go down.
  No force-push.

| Phase | Scope | Exit check | Effort |
|---|---|---|---|
| 0 | Demo setting in `BitgetV3Client` (header, fail closed) with tests, closing M-07. Decide the package location (`bot/event_agent/` with `sense/ reason/ risk/ execute/ review/` is suggested). Operator obtains a Demo key. | Header test fails when the fix is mutated back. | S |
| 1 | Dry-run agent: Demo instrument scan → `universe.json`; Demo public WS tickers; macro calendar plus one news source → Event; REASON with the kit schema (HOLD on invalid or timeout); pure risk gate with kit defaults and kill switch; intended orders logged, not sent; per-type JSONL plus audit chain; the tests in row 14. | 48 h dry run, no crash, valid JSON ≥ 99% (kit Part 2 step 4). | L |
| 2 | Demo orders: v3 place-order, post-only, `clientOid`, attached `stopLoss`, hedge leg; private WS order and position channels with REST fallback; reconcile every 5 min; equity every 5 min; auto-flatten on −2% daily and −8% drawdown. | 3 days at 50% limits, reconciliation clean, kill-switch drill logged (step 5). | M–L |
| 3 | Review loop and metrics: daily report from JSONL, conviction buckets, event-type attribution, shadow baselines, versioned prompts with an approval record; one-script metrics recompute. | A report reproduces from raw JSONL alone. | M |
| 4 | Read-only dashboard (equity curve, open positions, decision feed), runbook, `.env.example` section, one-command start. | Clean clone to running dry run on a fresh box. | M |
| 5 | Full-limit Demo run, at least 14 days covering at least 2 weekends; freeze the version; export logs; write the demo script and submission answers from the real record. | Kit scorecard checked item by item. | (calendar time) |

Kit Part 2 needs about 3 weeks of calendar time after the build (48 h dry run,
3 days at half limits, 14 days at full limits). Phases 0–4 are about 3–4 weeks of work.

## 5. Open questions only the operator can answer

1. **Next season:** submission deadline, whether the Event-driven sub-theme and the
   Demo-only rule carry over, and whether entries must be a public repo (this repo is
   BUSL-1.1).
2. **Demo API key:** does the operator have Demo mode with KYC, and a Demo key with
   UTA trade permission? Which US-stock perps and rTokens does Demo list? What is
   the Demo starting balance?
3. **LLM budget and model:** the daily cap for this agent (the bot's default
   `LLM_DAILY_BUDGET_USD` is 1.0) and which provider (structured output is model-gated).
4. **News sources:** which feeds for US equities, earnings and company events, and
   whether paid or licensed feeds are acceptable.
5. **Packaging:** a sub-package of this repo (reuses code, inherits the 45-minute
   preflight) or a small separate public repo (meets "clean clone in under 10 min").
6. **Where it runs:** the operator's box (recommended) or elsewhere. Which Telegram bot
   carries alerts.
7. **Who approves prompt and parameter changes,** and through which channel, so the
   approval record names a person.
8. **Dashboard:** public (percent only) or private.
9. **Agent Hub (`bgc`, Node 20+):** use it for interactive demos only, or not at all.
   The core loop calls REST and WS directly either way.
