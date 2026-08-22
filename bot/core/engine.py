"""
RUNECLAW Trading Engine -- the central orchestrator.
FSM States: IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING
Fail-closed: any unhandled error aborts the trade pipeline.
Human confirmation is REQUIRED before execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from bot.compat import UTC
from typing import Callable, Optional, Tuple

from pathlib import Path

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.core.black_swan import BlackSwanDetector
from bot.core.cost import CostTracker
from bot.core.system_health import SystemHealthMonitor
from bot.core.exchange_flow import ExchangeFlowProvider
from bot.core.macro_events import MacroEventProvider
from bot.core.live_executor import LiveExecutor, display_symbol, normalize_symbol
from bot.core import live_executor as _live_executor_mod
from bot.core.exchange_sync import sync_portfolio_with_exchange, get_exchange_position_count, invalidate_position_count_cache
from bot.core.market_scanner import MarketScanner, _classify_symbol
from bot.core.order_flow import OrderFlowAnalyzer
from bot.core.ws_feed import BitgetWSFeed
from bot.compliance.compliance_engine import ComplianceEngine, Permission, default_demo_profile
from bot.learning.orchestrator import LearningOrchestrator
from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.risk.multi_portfolio import MultiUserPortfolio
from bot.core.dashboard_pusher import (
    DashboardPusher, is_configured as dashboard_pusher_configured,
)
from bot.utils.audit_chain import AuditChain, DecisionRecord
from bot.utils.durable_io import fsync_dir
from bot.utils.paths import env_state_path, state_path
from bot.utils.logger import audit, system_log, trade_log, scan_log

#: Stages of one analysis, in the order they run. Module-level, not a class
#: attribute reached through `self`: _analyze_signals_batched is exercised
#: with lightweight stubs standing in for the engine, and every `self.` lookup
#: for what is really a constant is one more thing those stubs must grow. That
#: has now broken two suites for reasons unrelated to what they test.
ANALYSIS_STAGES = ("fetch", "mtf", "analyze", "refine")
from bot.utils.models import (
    AgentState,
    MarketSignal,
    RiskVerdict,
    StateTransition,
    TradeIdea,
)
from bot.core.smart_exits import TimeOfDayEdge, AdaptiveLimitDistance

from bot.utils.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)


def filter_adopted_messages(sync_msgs: list[str]) -> list[str]:
    """The "Adopted"-labeled subset of sync_portfolio_with_exchange's messages.

    Extracted so the adopt-notify callback (which expects the FULL list, to
    render one consolidated "Found N position(s)..." notification) always
    gets a real list -- passing it a single string one call at a time made
    len()/iteration treat that string as a sequence of characters.
    """
    return [m for m in sync_msgs if "Adopted" in m]


#: The prose shapes exchange_sync emits for an adoption. The callback's
#: parameter is named `adopted_symbols` and for a long time received THESE
#: instead — so the per-position SL/TP detail (#999) looked up
#: "Adopted orphan: UNI" as a symbol, matched nothing, and silently rendered
#: nothing. Observed live on 2026-07-30: the card printed
#: "• Adopted orphan: UNI" with no levels beside it.
_ADOPTED_PREFIXES = ("Adopted orphan limit order:", "Adopted orphan:")


def adopted_symbols_from_messages(sync_msgs: list[str]) -> list[str]:
    """The SYMBOLS in the adoption messages, so the card can look them up.

    Longest prefix first: "Adopted orphan limit order: X" also starts with
    "Adopted orphan", and stripping the shorter one would leave
    "limit order: X" as the "symbol".

    A message in neither shape is passed through unchanged rather than
    dropped — the notification must still name it, even if the position
    lookup then finds nothing. Losing an adoption notice is worse than
    rendering it without levels.
    """
    out: list[str] = []
    for m in filter_adopted_messages(sync_msgs):
        for pre in _ADOPTED_PREFIXES:
            if m.startswith(pre):
                m = m[len(pre):].strip()
                break
        out.append(m)
    return out


def _build_signal_sync_payloads(ideas: list, regime_fn) -> list[dict]:
    """Shape a batch of newly-generated TradeIdeas into website signal-stream rows.

    Extracted from _tick() so the real engine-generated signal stream (as
    opposed to the manual Telegram /scan path's separate lightweight scanner)
    can be verified without driving a full scan cycle.
    """
    from bot.utils.website_sync import build_signal_payload

    return [
        build_signal_payload(
            idea.id, idea,
            score=idea.confidence,
            regime=regime_fn(idea.asset),
            status="NEW",
            created_at=idea.timestamp.isoformat(),
        )
        for idea in ideas
    ]


def _flight_idea(idea) -> dict:
    """Guardian Flight Recorder: provenance-complete idea dict for the seal.

    Wraps the pure builder so a recorder failure can never touch a trade — on
    any error it falls back to the historic thin shape.
    """
    try:
        from bot.guardian.flight_recorder import decision_idea_payload
        return decision_idea_payload(idea)
    except Exception:
        try:
            return {"direction": idea.direction.value, "confidence": idea.confidence}
        except Exception:
            return {}


def _flight_risk(risk, size_usd=None) -> dict:
    """Guardian Flight Recorder: provenance-complete risk dict for the seal."""
    try:
        from bot.guardian.flight_recorder import decision_risk_payload
        return decision_risk_payload(risk, size_usd=size_usd)
    except Exception:
        try:
            return {"verdict": getattr(risk.verdict, "value", str(risk.verdict))}
        except Exception:
            return {}



def _stage_enter_guarded(engine, symbol, stage) -> None:
    """Module-level, getattr-guarded stage mark.

    _analyze_signal runs under lightweight stubs in several suites; a mark
    that requires the caller to grow a method turns instrumentation into a
    hard dependency, which is how the same hazard broke five suites once
    already this session.
    """
    try:
        _fn = getattr(engine, "_stage_enter", None)
        if _fn is not None:
            _fn(symbol, stage)
    except Exception:
        pass


#: Where the learned timeframe availability survives a restart.
MTF_AVAILABILITY_PATH = str(state_path("data/mtf_availability.json"))


def _mtf_state(engine):
    """The engine's availability state, LOADED from disk on first use.

    In-memory only, every restart re-learned from zero: three consecutive
    failures per pair, each costing up to the per-call timeout, across ~25
    symbols x 4 timeframes. That was paid again on every one of fifteen-plus
    deploys in a single day.
    """
    from bot.core import mtf_availability as _ma
    st = getattr(engine, "_mtf_availability", None)
    if st is None:
        st = engine._mtf_availability = _ma.load(MTF_AVAILABILITY_PATH)
    return st


#: Floor for any multi-timeframe cache entry, matching the original fixed ttl.
MTF_TTL_FLOOR_S = 180


def _mtf_ttl(timeframe: str) -> int:
    """How long a CLOSED candle set for ``timeframe`` stays valid.

    Every mtf leg used ttl=180 regardless of timeframe, so a 1d candle set was
    re-fetched every three minutes — 480 times a day for data that changes
    once. Production, 2026-08-01: mtf dominant at ~60%, mean 20-23s per
    symbol, max 74.94s, and ticks breaching the 300s cap at 310-314s with
    every signal analysed. The calls were not failing; they were SUCCEEDING
    slowly, and most of them were asking again for a candle that had not
    moved.

    `_drop_forming_candle` removes the still-forming bar, so the set this
    caches contains CLOSED bars only. A closed 4h bar cannot change until the
    next one closes, four hours later. A quarter of the period is therefore
    conservative by a wide margin:

        15m -> 225s      1h -> 900s      4h -> 3600s      1d -> 21600s

    Three of the four legs go from "every tick" to "rarely", which is where
    the wall-clock is. Floored at the original 180s so no timeframe is cached
    for LESS than it was before, and an unparseable timeframe falls back to
    exactly the old behaviour rather than to something invented.
    """
    try:
        from bot.utils.candles import timeframe_to_ms
        period_s = int(timeframe_to_ms(timeframe)) // 1000
    except Exception:
        return MTF_TTL_FLOOR_S
    if period_s <= 0:
        return MTF_TTL_FLOOR_S
    return max(MTF_TTL_FLOOR_S, period_s // 4)


def _mtf_filter_specs(engine, symbol, specs):
    """Drop timeframes currently suppressed for ``symbol``. Guarded.

    Returns ``specs`` UNCHANGED on any error. The failure mode of this
    instrumentation must be "fetched something we could have skipped", never
    "silently stopped fetching" — the second one degrades analysis quality
    with nothing to show for it.
    """
    try:
        from bot.core import mtf_availability as _ma
        return _ma.filter_specs(_mtf_state(engine), symbol, specs)
    except Exception:
        return specs


def _mtf_note(engine, symbol, timeframe, *, ok: bool) -> None:
    """Record one timeframe outcome. Guarded like every mark on this path."""
    try:
        from bot.core import mtf_availability as _ma
        st = _mtf_state(engine)
        if ok:
            _ma.record_success(st, symbol, timeframe)
        else:
            # Persist only when a suppression NEWLY latches — record_failure
            # returns True exactly then. Saving on every failure would write
            # on every tick for every thin pair; saving never would lose the
            # learning this exists to keep.
            if _ma.record_failure(st, symbol, timeframe):
                _ma.save(st, MTF_AVAILABILITY_PATH)
    except Exception:
        pass


def _stage_profile_record(engine, stage, symbol, seconds) -> None:
    """Per-symbol duration for ONE stage, guarded like the stage mark above.

    `_stage_add(stage, …)` keeps a SUM. A sum cannot tell 85 symbols at 15s
    apart from 3 symbols at 300s, and those call for opposite fixes — the
    2026-08-01 tick stall was argued from that one number twice.

    Keyed by STAGE because the bottleneck moves: the same day's later data had
    fetch at ~36-41% and mtf at ~59-64%, so a profiler wired to `fetch` alone
    would already have been pointed at the wrong stage.

    Same getattr-guard as _stage_enter_guarded: _analyze_signal runs under
    lightweight stubs in several suites, and instrumentation that requires
    the caller to grow an attribute is how that hazard broke five suites once.
    """
    try:
        from bot.core import stage_profile as _sp
        profs = getattr(engine, "_stage_profiles", None)
        if profs is None:
            profs = engine._stage_profiles = {}
        key = str(stage or "?")
        prof = profs.get(key)
        if prof is None:
            prof = profs[key] = _sp.new_profile()
        _sp.record(prof, symbol, seconds)
    except Exception:
        pass

def _of_snapshot_path() -> str:
    """The order-flow snapshot file, repo-root anchored (bot/utils/paths.py).

    ONE DEFINITION FOR BOTH ENDS. The reader (warm_oi_history) and the writer
    (record_snapshot) each spelled out the same relative default, and a
    relative path resolves against the working directory — so the two agreed
    only for as long as every process was launched from the same place. A
    writer and a reader disagreeing about where the history lives looks
    exactly like a cold start, forever.
    """
    return str(env_state_path("OF_SNAPSHOT_PATH",
                              "data/learning/order_flow_snapshots.jsonl"))



class RuneClawEngine:
    """
    Main event loop that ties scanner, analyzer, risk, and execution together.
    Uses a formal FSM via AgentState for every lifecycle transition.
    The engine never executes a trade without explicit human confirmation.
    """

    def __init__(self) -> None:
        self.portfolio = PortfolioTracker()
        self.scanner = MarketScanner()
        self.cost = CostTracker()
        self.analyzer = Analyzer(cost_tracker=self.cost)
        # Learning auto-refit: keeps calibration/voter/expectancy learners fresh
        # as closed outcomes accrue (gated by CONFIG.analyzer.learning_auto_refit_*).
        from bot.learning.auto_refit import LearningAutoRefit
        self._auto_refit = LearningAutoRefit(CONFIG.analyzer.learning_auto_refit_interval)
        self.order_flow = OrderFlowAnalyzer()
        # Exchange flow provider: real-time funding rates + OI from Bitget
        self.exchange_flow = ExchangeFlowProvider(
            exchange_factory=self.scanner._get_exchange,
        )
        self.macro_calendar = MacroCalendar(
            events=build_2026_calendar(),
            fail_closed_when_stale=CONFIG.risk.macro_calendar_fail_closed_when_stale,
        )
        self.macro_provider = MacroEventProvider(
            seed_path=Path("config/macro_calendar.seed.json"),
            funding_provider=self.exchange_flow.funding_rate_provider,
        )
        self.risk = RiskEngine(
            self.portfolio,
            macro_calendar=self.macro_calendar,
            macro_provider=self.macro_provider,
        )
        # Wire Gate 2 (taker 3-bar) + Rule 20 (book dominance) — without this
        # both advertised order-flow gates permanently took their fail-open
        # skip branch (set_order_flow_analyzer had zero callers).
        self.risk.set_order_flow_analyzer(self.order_flow)
        self.compliance = ComplianceEngine()
        self.compliance_profile = default_demo_profile()
        # LIVE FIX: if env-level config enables live trading, auto-grant
        # LIVE_TRADE permission so the bot doesn't require /golive CONFIRM
        # after every restart.  The five-lock compliance engine still enforces
        # all other gates (risk, macro, notional cap, human approval).
        if CONFIG.is_live():
            from bot.compliance.compliance_engine import Permission as _Perm
            self.compliance_profile.permissions.add(_Perm.LIVE_TRADE)
            # RC-AUD-018: live mode armed from environment with NO per-session
            # human action. Emit a prominent one-time startup WARNING (not just
            # info) so the operator/audit trail records that Lock 1 (LIVE_TRADE)
            # was granted for the whole process lifetime by env config alone.
            system_log.warning(
                "LIVE ARMED FROM ENV (RC-AUD-018): LIVE_TRADE permission "
                "auto-granted from env config (SIMULATION_MODE=false, "
                "LIVE_TRADING_ENABLED=true) with no per-session human arming. "
                "Lock 1 is satisfied for the entire process lifetime."
            )
        self.audit_chain = AuditChain("logs/audit_chain.jsonl")
        # Guardian Intent Compiler: compile the operator's on-disk strategy-intent
        # policy against the live risk caps and bind it to the operator engine.
        # No-op (and no policy) unless INTENT_POLICY_ENABLED and a policy file
        # exist — fail-open, so a bad/absent file never blocks startup or trading.
        self._load_intent_policy_onto(self.risk, owner="operator")
        self.learning = LearningOrchestrator()
        # WebSocket feed for real-time price monitoring (supplements REST polling)
        self.ws_feed = BitgetWSFeed()
        # Tape CVD: order flow reads true aggressor-side delta from the WS
        # trade channel when fresh (WS_CVD_ENABLED), REST fallback otherwise.
        self.order_flow.set_ws_feed(self.ws_feed)
        # Warm the OI trend/divergence history from recorded snapshots so a
        # restart doesn't blind the OI classifiers for the first N scans.
        # Best-effort: returns 0 (cold start) on any problem.
        try:
            warmed = self.order_flow.warm_oi_history(
                _of_snapshot_path())
            if warmed:
                system_log.info("OI history warmed from snapshots for %d symbols", warmed)
        except Exception:
            pass
        # Live executor for real Bitget orders (micro-test mode). This is the
        # SHARED OPERATOR executor (CONFIG.exchange keys) — the only one used
        # unless per-user live trading is enabled.
        self.live_executor = LiveExecutor()
        # Wire balance cache invalidation + per-symbol SL cooldown
        self.live_executor.on_position_closed = lambda pos: self._on_live_position_closed(pos)
        # Wire risk engine for warning rate circuit breaker
        self.live_executor._risk_engine = self.risk

        # Wire the KILL SWITCH into the executor module (once, not per instance —
        # there are four LiveExecutor() sites and a fifth would miss a
        # per-instance hook). Without this the drift market-fallback in
        # live_executor opened NEW exposure on a halted account: /halt and every
        # automatic breaker leave resting limit orders in place, and
        # _check_open_positions keeps running while halted so that exits are
        # still managed. Mirrors the last-mile check on the normal open path.
        def _halted_now() -> bool:
            try:
                if self._halted or self.risk.circuit_breaker_active:
                    return True
            except Exception:
                return True          # unknown halt state -> treat as halted
            return False

        _live_executor_mod.set_halt_check(_halted_now)
        # Wire the shared WS feed so degradation reads true price-staleness, not
        # the coarse per-tick shadow clock (avoids false "feed disconnected"
        # pauses during calm-market cycles where the scan tick > pause threshold).
        self.live_executor._ws_feed = self.ws_feed
        # Per-user live executors (PER_USER_LIVE_ENABLED, default OFF): keyed by
        # telegram user_id, each bound to that user's OWN linked Bitget account.
        # Empty + unused while the flag is off, so the operator path is unchanged.
        self._user_executors: dict[str, LiveExecutor] = {}
        # Read-only per-user executors for the /livebalance VIEW only, keyed by
        # telegram user_id. Viewing your own linked account is read-only and must
        # work as soon as you /connect — independent of PER_USER_LIVE_ENABLED
        # (which gates order PLACEMENT). Cached SEPARATELY from _user_executors so
        # a view-only account is never enrolled in the trading monitoring /
        # reconciliation / close loops (all_executors()).
        self._balance_view_executors: dict[str, LiveExecutor] = {}
        # Per-user RiskEngines (PER_USER_LIVE_ENABLED, default OFF): keyed by
        # telegram user_id, each bound to that user's OWN portfolio + persisted
        # to data/risk_state_{user_id}.json.  Isolates the stateful safety
        # breakers (loss streak, circuit breaker, daily-loss, drawdown) so one
        # user's losses can't trip another user's halt.  Empty + unused while the
        # flag is off — risk_for() returns the shared operator engine — so the
        # operator path is byte-identical.  See risk_for().
        self._user_risk: dict[str, "RiskEngine"] = {}
        self.health = SystemHealthMonitor()
        # Multi-user portfolio manager: per-user isolated paper wallets
        self.user_portfolios = MultiUserPortfolio(
            default_balance=CONFIG.paper_balance_usd,
            on_trade_close=None,  # wired after risk engine init
        )
        # Dashboard pusher — the THIRD dashboard generation, and the one
        # nothing deploys: its consumer (`dashboard_api.py`, :9090) is named by
        # no compose service, nginx upstream, Dockerfile or deploy script. It
        # was constructed and started on every boot regardless, where `start()`
        # logged "disabled" and returned — wiring that looked live in the
        # source and did nothing in production, which is the harder direction
        # to notice.
        #
        # None unless an operator has actually configured it. `None` rather
        # than a disabled instance so that "is this running?" has one answer
        # instead of two, and so the snapshot builder — a fourth independent
        # rendering of win rate and P&L — cannot be reached by accident.
        self.dashboard_pusher = (
            DashboardPusher(self) if dashboard_pusher_configured() else None)
        # C1 fix: wire trade-close callback so portfolio closes feed risk streak tracking
        # Also sync trade events to the website dashboard
        def _on_trade_close_composite(net_pnl: float) -> None:
            self.risk.record_trade_result(net_pnl)
            # Auto-sync to website dashboard
            try:
                from bot.utils.website_sync import sync_in_background
                state = self.portfolio.snapshot()
                sync_in_background(
                    user_id=1,  # default user; multi-user resolves via telegram handler
                    equity=state.equity_usd,
                    positions=list(self.portfolio.open_positions),
                    closed_trades=list(self.portfolio._history[-50:]),
                )
            except Exception as exc:
                # C2-52 FIX: log website sync errors instead of silently swallowing
                logger.warning("Website sync failed: %s", exc)
        self.portfolio._on_trade_close = _on_trade_close_composite
        # Route each user's paper close into the RiskEngine that owns THAT user's
        # streak/breaker state.  In default (per-user OFF) mode risk_for() always
        # returns the shared engine, so this is equivalent to feeding every close
        # into self.risk — exactly as before — while also correctly handling
        # portfolios restored from disk.  When per-user live is on, each user's
        # losses accrue against their own breaker instead of one global counter.
        self.user_portfolios._on_trade_close = self.risk.record_trade_result
        self.user_portfolios._on_trade_close_user = self._route_user_trade_close
        # C2-34: Wire combined state saver for atomic portfolio+risk persistence.
        # Both components delegate their saves to this function, which writes
        # a single combined_state.json via fsync + os.replace.
        self._combined_state_file = os.path.join(
            os.path.dirname(self.portfolio._state_file) or "data",
            "combined_state.json"
        )
        self._wire_combined_state_saver()
        self.state: AgentState = AgentState.IDLE
        self._state_history: list[StateTransition] = []
        self._max_state_history = 1000  # F-13 FIX: cap state history
        self._running = False
        self._confirm_callback: Optional[Callable] = None
        self._close_notify_callback: Optional[Callable] = None
        self._fill_notify_callback: Optional[Callable] = None
        self._sync_notify_callback: Optional[Callable] = None
        self._adopt_notify_callback: Optional[Callable] = None
        self._auto_confirm_notify_callback: Optional[Callable] = None
        self._pending_ideas: dict[str, TradeIdea] = {}
        # Per-symbol entry lock: serializes confirm_trade for the same symbol so
        # two overlapping auto-confirm cycles can't each pass the (analysis-time)
        # duplicate guard and place two orders for one setup (TOCTOU).
        self._symbol_entry_locks: dict[str, "asyncio.Lock"] = {}
        self._last_confirmed_idea: Optional[TradeIdea] = None
        self._pending_atr: dict[str, Optional[float]] = {}  # H1: store ATR for re-check
        # Entry-timing (auto path): per-idea (allowed, reason) verdict from the
        # sub-degree confirmation gate, computed at analyze-time on the idea's own
        # candles (scoped to ENTRY_TIMING_REGIMES). The auto-confirm loops DEFER
        # an unconfirmed autonomous entry; capped so it can never grow unbounded.
        self._pending_timing: dict[str, tuple] = {}
        self._pending_pyramid: dict[str, bool] = {}  # Track pyramid add flags
        # Single-flight scan lock. With PTB concurrent_updates ON, two Telegram
        # updates (two 'Latest Signal' taps, or a tap + /forcescan) can enter
        # force_scan concurrently; both would clear+repopulate _pending_ideas. This
        # serializes force_scan against itself and against the periodic tick scan.
        self._scan_lock: asyncio.Lock = asyncio.Lock()
        # Hard kill-switch flag. Set by emergency_halt_all, cleared on resume, and
        # re-checked fail-closed just before executor.execute() so a confirm that
        # passed its risk gate BEFORE /halt tripped can never land an order after
        # the flatten (a race newly reachable once updates run concurrently).
        self._halted: bool = False
        self._user_store = None  # Set by TelegramHandler for role-based execution
        self._cooldown_until: float = 0.0
        # Per-symbol cooldown after SL hit — prevents immediate re-entry
        self._symbol_cooldowns: dict[str, float] = {}  # symbol_key -> monotonic expiry
        self._symbol_cooldown_seconds: float = float(os.environ.get("SYMBOL_SL_COOLDOWN_SEC", "1800"))  # 30 min default
        # A THIRD arming source for the dict above: an analysis that TIMED OUT.
        # Distinct from the two risk cooldowns because it is a fact about us,
        # not about the symbol — we could not read it in time, rather than
        # choosing not to trade it. Same dict and same pre-analysis guard, so
        # resting reuses machinery that already works.
        #
        # Both are in-memory and monotonic-based, so neither is persisted: a
        # monotonic expiry written to disk is meaningless after a restart, and
        # a restart is also the operator's chance to have fixed whatever was
        # hanging. Strikes reset on a clean analysis, not on a skip.
        self._analysis_rest_strikes: dict[str, int] = {}   # symbol_key -> consecutive timeouts
        self._analysis_rest_until: dict[str, float] = {}   # symbol_key -> monotonic expiry
        # Per-symbol loss-streak tracking — a much longer cooldown (see
        # CONFIG.risk.symbol_loss_streak_*) once a symbol has lost repeatedly,
        # reusing _symbol_cooldowns above so the existing pre-analysis check
        # in _analyze_signal blocks it the same way a post-SL cooldown does.
        self._symbol_loss_streaks: dict[str, int] = {}  # symbol_key -> consecutive losses
        self._last_rebalance_check: float = 0.0  # monotonic timestamp
        self._rebalance_interval: float = 4 * 3600  # 4 hours minimum between checks
        # /whynot: store last RiskCheck per symbol when risk rejects a trade
        self._last_rejections: dict[str, dict] = {}
        self._last_scan_signals: list = []
        self._ohlcv_cache: dict[str, tuple[float, list]] = {}
        # M-13 FIX: live balance cache as instance attributes (not class-level mutables)
        self._live_balance_cache: dict = {}
        self._live_balance_cache_ts: float = 0.0
        # Per-user live balance caches (PER_USER_LIVE_ENABLED, default OFF):
        # a regular user's confirmed live trade must size against THEIR OWN linked
        # account, not the operator's. Keyed by user_id, same TTL as the operator
        # cache. Empty + unused while the flag is off → operator path unchanged.
        self._user_live_balance_cache: dict[str, dict] = {}
        self._user_live_balance_cache_ts: dict[str, float] = {}
        # Live-auth health per account ("" = operator). When an account's venue
        # authentication is failing (missing passphrase, Bitget 40006/40012), new
        # live ENTRIES on that account are halted — existing positions keep being
        # monitored and closed. Set by the boot credential preflight / per-user
        # auth sweep and cleared when auth is confirmed OK. Unknown → healthy, so
        # nothing is blocked until a probe actually reports a failure.
        self._live_auth_ok: dict[str, bool] = {}
        self._live_auth_detail: dict[str, str] = {}
        # Consecutive engine-tick failures, mirrored from the run loop so the
        # proactive monitor can alert when the main loop is degraded/unmonitored.
        self._tick_consecutive_failures: int = 0
        # Tick-START stamp (monotonic; None = not started) — lets the monitor
        # detect a HUNG tick, which increments no failure counter.
        self._last_tick_started_ts: float | None = None
        # When the NEXT tick is due (monotonic; None = unknown): stamped
        # before every inter-tick sleep so the stall watchdog can tell a
        # PLANNED quiet-market sleep (smart-scan max is 600s — at or beyond
        # the stall threshold itself) from a genuine hang. Root-caused from a
        # production false alarm: 15s tick + 600s planned sleep = 615s since
        # tick start, which tripped the 600s threshold.
        self._next_tick_due_ts: float | None = None
        # {phase, cap_s, at, count} for the most recent phase to exceed its
        # cap — surfaced in /status and in the degraded alert so the cause
        # travels with the symptom. None until one fires.
        self._last_phase_timeout: dict | None = None
        # {skipped, of, cap_s, at} for the most recent batch in which one or
        # more individual analyses exceeded their own cap. Distinct from the
        # phase timeout above: that one says the tick died, this one says a
        # dependency inside it is hanging — which is the thing that KILLS the
        # tick, and the thing an operator can actually act on. None until one
        # fires.
        self._last_analysis_timeout: dict | None = None
        # {phase: {last_s, peak_s, cap_s, at}} for phases that SUCCEEDED —
        # the headroom, not just the breach. Bounded by the phase-name count.
        self._phase_durations: dict[str, dict] = {}
        # {of, done, started} for the analyze batch in flight. Read by the
        # phase-timeout handler, which runs after the batch's frames are gone.
        self._analyze_progress: dict | None = None
        self._analyze_batch_seq: int = 0
        # Effective wall-clock throughput of the LAST analyze batch, and the
        # forecast it produces for the batch in flight. None until measured —
        # a forecast from a guessed rate is a fabricated number, and guessed
        # causes already shipped two wrong fixes for this exact phase.
        self._analyze_throughput: dict | None = None
        self._analyze_capacity: dict | None = None
        # Per-stage seconds summed across the current analyze batch.
        self._stage_totals: dict[str, float] | None = None
        # Reciprocal watch on the proactive monitor loop (attached by the
        # Telegram handler at start_monitor; None when Telegram is off).
        self._proactive_monitor = None
        self._monitor_stale_callback = None
        # None, NOT 0.0 — monotonic's epoch is boot time, so on a freshly
        # booted host `monotonic() - 0.0 < timeout` would silently suppress
        # the first check for a whole window (the documented sentinel trap;
        # caught in CI, whose runners boot seconds before the job).
        self._last_monitor_liveness_check: float | None = None
        # Throttle for the periodic SL/TP self-heal (re-place stops that went
        # missing DURING operation, not just at startup). monotonic seconds.
        self._last_sltp_verify_ts: float = 0.0
        self._SLTP_VERIFY_INTERVAL: float = 300.0  # 5 minutes
        self._LIVE_BALANCE_TTL: float = 30.0  # cache live balance for 30 seconds
        # H-05 FIX: track last known valid prices for WS sanity checks
        self._last_known_prices: dict[str, float] = {}
        # Watchdog: track when the FSM last changed state so _tick() can
        # detect and recover from stuck non-IDLE states.
        self._last_state_change: float = time.time()

        # Cross-asset correlation tracker
        from bot.core.cross_asset import CrossAssetTracker
        self.cross_asset = CrossAssetTracker()
        # Hand the analyzer's dark cross_asset voter its data source (votes
        # only when CROSS_ASSET_VOTER_ENABLED is flipped).
        self.analyzer.cross_asset_tracker = self.cross_asset

        # Slippage tracker. Wire it into the operator executor so realized
        # slippage (intended entry vs actual fill) is actually recorded — the
        # executor's record() call is a no-op until _slippage_tracker is set.
        from bot.core.slippage import SlippageTracker
        self.slippage = SlippageTracker()
        self.live_executor._slippage_tracker = self.slippage

        # Trade journal
        from bot.core.trade_journal import TradeJournal
        self.journal = TradeJournal()

        # Time-of-day edge filter
        self.time_of_day = TimeOfDayEdge()

        # Adaptive limit distance learner
        self.adaptive_limits = AdaptiveLimitDistance()

        # Hold-time analytics tracker
        from bot.core.smart_exits import HoldTimeAnalytics
        self.hold_analytics = HoldTimeAnalytics()

        # VWAP cache for VWAP reversion exits
        self._last_vwap: dict[str, float] = {}

        # Smart scan scheduling
        self._last_scan_time: float = 0.0
        self._current_scan_interval: float = CONFIG.scan_interval_seconds
        self._recent_atr_values: dict[str, float] = {}  # symbol -> latest ATR

        # Black-swan detector. It was written, unit-tested and referenced by two
        # consumers, and NOTHING EVER CONSTRUCTED IT — `engine.black_swan` did
        # not exist, so `api_bridge.py`'s /blackswan answered "not_initialized"
        # forever and `proactive_monitor._check_black_swan`'s
        # `hasattr(self.engine, 'black_swan')` was always False, returning [] on
        # every pass of the alert pipeline it is registered in. Same shape as the
        # rest of this audit: the code works, nothing reaches it.
        #
        # It OBSERVES ONLY. `halt_recommended` is surfaced, never acted on — no
        # caller halts on it today, and wiring an automatic halt would be a new
        # trading behaviour rather than the fix for a dead reference. Fed from
        # _analyze_signal; every call is wrapped so a detector fault can never
        # block a trade decision.
        self.black_swan = BlackSwanDetector()

    # -- State management --

    def _compute_smart_scan_interval(self) -> float:
        """Dynamically adjust scan interval based on market volatility.

        High volatility → scan more frequently (min interval)
        Low volatility → scan less frequently (max interval)
        """
        if not CONFIG.adaptive.smart_scan_enabled:
            return CONFIG.scan_interval_seconds

        if not self._recent_atr_values:
            return CONFIG.scan_interval_seconds

        min_interval = CONFIG.adaptive.smart_scan_min_interval
        max_interval = CONFIG.adaptive.smart_scan_max_interval
        base = CONFIG.scan_interval_seconds

        # Count how many symbols have "hot" ATR (above their recent average)
        hot_symbols = 0
        for symbol, atr_pct in self._recent_atr_values.items():
            if atr_pct > 0.03:  # ATR > 3% of price = high vol
                hot_symbols += 1

        if hot_symbols >= 3:
            # Multiple volatile symbols = market-wide event, scan fast
            interval = min_interval
        elif hot_symbols >= 1:
            # Some volatility, moderate speed
            interval = base * 0.5
        else:
            # Quiet market, slow down
            interval = min(max_interval, base * 1.5)

        interval = max(min_interval, min(max_interval, interval))

        if abs(interval - self._current_scan_interval) > 10:
            audit(system_log,
                  f"Smart scan interval: {self._current_scan_interval:.0f}s \u2192 {interval:.0f}s "
                  f"(hot_symbols={hot_symbols})",
                  action="smart_scan", result="ADJUSTED")

        self._current_scan_interval = interval
        return interval

    async def get_exchange(self, category: str = "Crypto"):
        """Public accessor for the exchange instance (for skills that need OHLCV).

        Args:
            category: Asset category — "Crypto" uses spot, anything else uses futures.
        """
        if category != "Crypto":
            return await self.scanner._get_futures_exchange()
        return await self.scanner._get_exchange()

    async def get_futures_exchange(self):
        """Public accessor for the futures exchange instance."""
        return await self.scanner._get_futures_exchange()

    # -- Live equity cache --

    # _live_balance_cache, _live_balance_cache_ts, and _LIVE_BALANCE_TTL
    # are initialised in __init__() as instance attributes (M-13 fix).

    async def get_live_equity(self) -> Optional[dict]:
        """Fetch real exchange balance in LIVE mode (cached).

        Returns dict with 'equity', 'free', 'used', 'holdings' or None if
        not in live mode or fetch fails.
        """
        if not CONFIG.is_live():
            return None
        now = time.monotonic()
        if (now - self._live_balance_cache_ts) < self._LIVE_BALANCE_TTL and self._live_balance_cache:
            return self._live_balance_cache
        try:
            bal = await self.live_executor.fetch_balance()
            if "error" not in bal or bal.get("total", 0) > 0:
                self._live_balance_cache = bal
                self._live_balance_cache_ts = now
                return bal
        except Exception as exc:
            # C2-55 FIX: log staleness so risk calculation accuracy is visible
            age_s = time.monotonic() - self._live_balance_cache_ts
            if self._live_balance_cache:
                system_log.warning(
                    "Live balance fetch failed (%s) — returning cached value (%.1fs old)",
                    exc, age_s,
                )
                if age_s > 300:
                    system_log.error("Balance cache is >5m stale — risk calculations may be wrong")
            else:
                system_log.debug("Live balance fetch failed (no cache): %s", exc)
        return self._live_balance_cache if self._live_balance_cache else None

    def live_balance_cached(self, max_age_s: float = 900.0) -> Optional[dict]:
        """The cached live balance ONLY when fresh enough to state as current.

        Five call sites read ``_live_balance_cache`` directly, and none of
        them consulted ``_live_balance_cache_ts``. get_live_equity() enforces
        its TTL on the FETCH side, but when fetches keep failing the
        timestamp stops advancing while the dict keeps its last value — and
        every direct reader then presents an hours-old number as the live
        account: the website's equity fallback, the /yield free-margin row,
        the admin card's "Available".

        None means "do not know". Downstream surfaces already render that
        honestly (unavailable / row omitted); a stale dollar figure they
        cannot detect at all. 900s default: generous against the ~60s tick
        cadence that refreshes the cache, far below the hours a dead venue
        connection can leave it dark.
        """
        if not self._live_balance_cache:
            return None
        # ts <= 0 means never stamped. This must be its own check: monotonic's
        # epoch is UNSPECIFIED, and on a freshly booted host it starts near
        # zero — so "monotonic() - 0.0" is NOT enormous by construction, it is
        # the host's uptime, and for the first max_age_s of uptime a
        # never-stamped cache would have read as fresh. CI's just-booted
        # runner caught exactly that.
        if self._live_balance_cache_ts <= 0.0:
            return None
        age = time.monotonic() - self._live_balance_cache_ts
        if age > max_age_s:
            return None
        return self._live_balance_cache

    def _invalidate_live_balance_cache(self) -> None:
        """Force a fresh balance fetch on the next equity check."""
        self._live_balance_cache = {}
        self._live_balance_cache_ts = 0.0
        # Per-user caches too: a close on any account may change that user's
        # equity, so drop them all and let the next check refetch.
        self._user_live_balance_cache.clear()
        self._user_live_balance_cache_ts.clear()

    async def get_user_live_equity(self, user_id: str = "") -> Optional[dict]:
        """LIVE exchange balance for the account THIS user's trade executes on.

        Operator/admin/auto/unattended, or per-user live OFF, or a user with no
        own keys (routed to operator) → the shared operator balance (identical to
        get_live_equity). A regular user under per-user live → THEIR OWN linked
        account's balance, fetched via their executor and cached per-user with the
        same TTL. Fail-safe: returns the last cached value on error, else None, so
        the caller falls back to capped paper-equity sizing rather than the WRONG
        account's equity.
        """
        if not CONFIG.is_live():
            return None
        ex = self._executor_for(user_id)
        # Operator path: per-user off, or operator/admin/auto/unattended, or the
        # user has no own keys (executor fell back to operator).
        if (ex is self.live_executor
                or not getattr(CONFIG, "per_user_live_enabled", False)
                or not user_id or user_id in ("auto", "")
                or self._is_operator_user(user_id)):
            return await self.get_live_equity()
        key = str(user_id)
        now = time.monotonic()
        ts = self._user_live_balance_cache_ts.get(key, 0.0)
        cached = self._user_live_balance_cache.get(key)
        if cached and (now - ts) < self._LIVE_BALANCE_TTL:
            return cached
        try:
            bal = await ex.fetch_balance()
            if "error" not in bal or bal.get("total", 0) > 0:
                self._user_live_balance_cache[key] = bal
                self._user_live_balance_cache_ts[key] = now
                return bal
        except Exception as exc:
            system_log.warning(
                "Per-user live balance fetch failed for %s: %s — using %s",
                user_id, exc, "cached value" if cached else "paper fallback")
        return cached if cached else None

    async def _live_recheck_context(self, user_id: str = "") -> tuple:
        """Return ``(live_equity, live_open_count)`` for the account THIS user's
        confirm will execute on, so the pre-execution risk re-check sizes and
        counts against the RIGHT account.

        Operator/default path is byte-identical to the prior inline logic (shared
        balance cache + operator exchange position count). A regular user under
        per-user live gets their OWN account's balance + open-position count.
        """
        if not CONFIG.is_live():
            return None, None
        ex = self._executor_for(user_id)
        per_user = (
            ex is not self.live_executor
            and getattr(CONFIG, "per_user_live_enabled", False)
            and bool(user_id) and user_id not in ("auto", "")
            and not self._is_operator_user(user_id)
        )
        if not per_user:
            # Operator path — preserves the exact prior behaviour.
            live_eq = self._live_balance_cache.get("total", 0.0) if self._live_balance_cache else None
            try:
                exchange_ct = await get_exchange_position_count(self)
                pending_ct = sum(
                    1 for p in self.live_executor.open_positions
                    if p.status == "pending_fill"
                )
                live_open = exchange_ct + pending_ct
            except Exception:
                live_open = len(self.live_executor.open_positions)
            return live_eq, live_open
        # Per-user regular path — the user's OWN account.
        bal = await self.get_user_live_equity(user_id)
        live_eq = bal.get("total", 0.0) if bal else None
        # open_positions already filters to open + pending_fill for this account.
        live_open = len(ex.open_positions)
        return live_eq, live_open

    def _per_user_margin_cap(self, user_id) -> Optional[float]:
        """Operator-set max margin (USD) for THIS user's live trade, or None.

        Only applies under per-user live to a regular (non-operator) user — the
        operator/admin trade the operator account under the global micro caps.
        Tighten-only: the caller folds it into the existing position cap with a
        min(), so it can only REDUCE the size the risk engine already sized and
        capped. None (no cap set) → no change. Fail-open: a store hiccup → None.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return None
        if not user_id or user_id in ("auto", "") or self._is_operator_user(user_id):
            return None
        store = getattr(self, "_user_store", None)
        if store is None:
            return None
        try:
            cap = store.max_margin(user_id)
        except Exception as exc:
            logger.debug("Per-user margin cap lookup failed for %s: %s", user_id, exc)
            return None
        return cap if (cap is not None and cap > 0) else None

    def _outcome_regime(self, symbol: str) -> str:
        """Best-available market regime to tag a closed-trade outcome with.

        Prefer the analyzer's actual detected regime for this symbol (a real
        value like TREND_UP / RANGE). The risk engine's _current_regime stays
        "UNKNOWN" unless REGIME_SIZING_ENABLED (the regime→sizing bridge is
        gated), so tagging outcomes with it stored "UNKNOWN" for every trade
        while setup-expectancy looks up by the analyzer's real regime — the keys
        never matched and the nudge was permanently zero (deep-audit medium).
        Tolerates symbol-format differences; falls back to _current_regime."""
        try:
            regimes = getattr(getattr(self, "analyzer", None), "_current_regimes", None)
            if regimes:
                reg = regimes.get(symbol)
                if reg is None and symbol:
                    nsym = normalize_symbol(symbol)
                    reg = regimes.get(nsym) or next(
                        (v for k, v in regimes.items() if normalize_symbol(k) == nsym),
                        None)
                if reg is not None:
                    val = getattr(reg, "value", reg)
                    if val:
                        return str(val)
        except Exception:
            pass
        return str(getattr(self.risk, "_current_regime", "") or "")

    def _venue_of_closed_position(self, pos, user_id: str = "") -> str:
        """Which venue did this live close happen on?

        Asked in three places, most specific first, because each later source is
        a weaker claim than the one before it:

        1. the position itself, if the executor stamped it;
        2. the executor that owns this caller — it was constructed with its
           venue and is the thing that placed the order;
        3. the venue the credential store calls this user's ACTIVE one.

        Falls back to the default rather than to empty, because every trade this
        bot has actually placed went to Bitget and an unlabelled record would
        make a real trade look like one from an unknown venue. That is a safe
        answer TODAY and stops being one the moment a second venue can trade —
        at which point this must become a stamp on the position, not a guess
        made after the fact. `docs/MULTI_VENUE_RISK_SPLIT.md` §Phase 2.
        """
        v = str(getattr(pos, "venue", "") or "").strip().lower()
        if v:
            return v
        try:
            ex = self._executor_for(user_id)
            v = str(getattr(ex, "venue", "") or "").strip().lower()
            if v:
                return v
        except Exception:
            pass
        try:
            from bot.core.exchange_credentials import get_credential_store
            v = str(get_credential_store().get_venue(user_id) or "").strip().lower()
            if v:
                return v
        except Exception:
            pass
        return "bitget"

    def _on_live_position_closed(self, pos, user_id: str = "") -> None:
        """Handle live position close: invalidate cache + set SL cooldown.

        ``user_id`` identifies the account that closed the trade (the operator
        executor passes "" — the default — so behaviour is unchanged; per-user
        executors pass their own id). It routes the account-level loss breakers
        to the RIGHT risk engine (audit C1): without it, a per-user live trader's
        realized losses were recorded against the operator engine, so their own
        daily-loss / drawdown / streak breakers never tripped and the operator's
        breakers absorbed every user's losses.
        """
        self._invalidate_live_balance_cache()

        # ── Close the learning loop's WRITE side ──────────────────────────
        # Record the realized outcome as a complete, queryable experience record
        # (symbol + direction + regime + pnl). Done ALWAYS (not gated by the
        # adaptive-confidence flag) so history accumulates and is ready the moment
        # an operator opts in. Cheap append; fail-open.
        try:
            _pnl = getattr(pos, "pnl_usd", None)
            if _pnl is not None:
                self.learning.record_closed_outcome(
                    symbol=getattr(pos, "symbol", ""),
                    direction=str(getattr(pos, "direction", "") or ""),
                    pnl_result=float(_pnl),
                    market_regime=self._outcome_regime(getattr(pos, "symbol", "")),
                    trade_id=getattr(pos, "trade_id", ""),
                )
        except Exception as _lo_exc:
            logger.debug("Learning outcome record skipped: %s", _lo_exc)
        # ── Trade journal (audit CRITICAL, same shape as the breakers below)
        # The journal was written from ONE place: the paper portfolio's
        # check_stops loop. In pure-live mode the paper portfolio is never
        # updated — the exchange is the source of truth — so `closed` there is
        # always empty and the journal never received a single entry. The
        # comment beside that call already said "Live closes already record
        # via _on_live_position_closed", and this function did no such thing:
        # a claim the code did not enforce, sitting next to the code that
        # would have had to enforce it.
        #
        # Live effect: /portfolio showed 104 trades and five recent closes
        # while /journal said "No trades in the last 7 days" — two operator
        # surfaces flatly contradicting each other, on 2026-07-30.
        #
        # Fail-open like every other recorder on this path: a journal write
        # must never cost a close its breaker feed below.
        # Resolved BEFORE the journal write, and guarded separately, because the
        # first version of this called the resolver inline — so anything it
        # raised was swallowed by the fail-open `except` below and the whole
        # trade went unrecorded. A missing LABEL must never cost the RECORD:
        # a journal entry marked with the default venue is a small
        # inaccuracy, and a live close that never reaches the journal is the
        # exact defect tests/test_journal_records_live_closes.py exists to stop.
        try:
            _venue = self._venue_of_closed_position(pos, user_id)
        except Exception:
            _venue = "bitget"
        try:
            _jpnl = getattr(pos, "pnl_usd", None)
            if _jpnl is not None:
                _opened = getattr(pos, "opened_at", None)
                _closed = getattr(pos, "closed_at", None)
                _hold = ((_closed - _opened).total_seconds() / 3600.0
                         if _opened and _closed else 0.0)
                self.journal.record_trade(
                    trade_id=getattr(pos, "trade_id", "") or "",
                    symbol=getattr(pos, "symbol", ""),
                    direction=str(getattr(pos, "direction", "") or ""),
                    strategy_type=getattr(pos, "strategy_type", "") or "",
                    entry_price=float(getattr(pos, "entry_price", 0) or 0),
                    exit_price=float(getattr(pos, "exit_price", 0) or 0),
                    stop_loss=float(getattr(pos, "stop_loss", 0) or 0),
                    take_profit=float(getattr(pos, "take_profit", 0) or 0),
                    pnl=float(_jpnl),
                    regime=self._outcome_regime(getattr(pos, "symbol", "")),
                    holding_hours=_hold,
                    exit_reason=str(getattr(pos, "close_reason", "") or ""),
                    venue=_venue,
                )
        except Exception as _j_exc:
            logger.debug("Journal record skipped for live close: %s", _j_exc)
        # ── Feed the ACCOUNT-LEVEL loss breakers (audit CRITICAL) ──────────
        # In pure-live mode the paper portfolio is never updated, so the
        # consecutive-loss breaker, live-performance governor, equity throttle
        # and the daily-loss / drawdown gates would never see real losses.
        # Route each live realized close into the OWNING account's risk engine
        # (risk_for(user_id)) so those account-level protections engage on the
        # right account: the operator engine for operator/auto closes ("" id),
        # and the user's OWN engine for a per-user live close (audit C1). With
        # PER_USER_LIVE_ENABLED off, risk_for always returns the operator engine
        # — byte-identical to before.
        try:
            _rpnl = getattr(pos, "pnl_usd", None)
            if _rpnl is not None:
                self.risk_for(user_id).record_live_trade_result(float(_rpnl))
        except Exception as _rr_exc:
            logger.debug("Live risk-result record skipped: %s", _rr_exc)
        # ── Guardian Flight Recorder: close the decision→outcome loop ───────
        # Append an OUTCOME event keyed to this position's trade_id (the same id
        # the DecisionRecord was sealed under) so a decision links to its
        # realised fill / PnL / close. Best-effort, fail-open: a recorder error
        # can never affect the close path.
        try:
            from bot.guardian.flight_recorder import outcome_event_payload
            self.audit_chain.append("OUTCOME", outcome_event_payload(pos))
            self._sync_flight_records()
        except Exception as _fo_exc:
            logger.debug("Flight-record outcome append skipped: %s", _fo_exc)
        # Auto-refit the learners every N closed outcomes (gated, fail-open).
        # Keeps calibration/voter/expectancy fresh without a manual /calibration
        # refit. Only updates persisted learner state — never changes a decision
        # unless the learners' own application flags are on.
        try:
            if CONFIG.analyzer.learning_auto_refit_enabled:
                self._auto_refit.note_closed_trade(getattr(self, "analyzer", None))
        except Exception as _ar_exc:
            logger.debug("Learning auto-refit skipped: %s", _ar_exc)
        # If closed adversely (SL / stop / liquidation), set a per-symbol cooldown
        # to prevent immediate re-entry.  A liquidation ("LIQUIDATED") is the most
        # adverse close of all, so it must arm the cooldown too.
        close_reason = getattr(pos, "close_reason", "") or ""
        _cr = close_reason.upper()
        if "SL" in _cr or "STOP" in _cr or "LIQUID" in _cr:
            sym_key = normalize_symbol(getattr(pos, "symbol", ""))
            if sym_key:
                self._symbol_cooldowns[sym_key] = (
                    time.monotonic() + self._symbol_cooldown_seconds
                )
                logger.info(
                    "Symbol cooldown set: %s blocked for %ds after SL hit",
                    sym_key, int(self._symbol_cooldown_seconds))

        # ── Per-symbol loss-streak cooldown ────────────────────────────────
        # The account-wide consecutive-loss streak (RiskEngine) decays on ANY
        # win, so a symbol that keeps losing stays fully eligible as long as
        # OTHER symbols occasionally win. This tracks consecutive losses PER
        # SYMBOL (same decay-on-win / no-change-on-breakeven logic) and, once
        # a symbol hits the threshold, arms a much longer cooldown by reusing
        # _symbol_cooldowns — the SAME dict/check the post-SL cooldown above
        # uses, already wired into _analyze_signal's early pre-analysis guard.
        if CONFIG.risk.symbol_loss_streak_enabled:
            try:
                _streak_sym = normalize_symbol(getattr(pos, "symbol", ""))
                _streak_pnl = getattr(pos, "pnl_usd", None)
                if _streak_sym and _streak_pnl is not None:
                    _streak_pnl = float(_streak_pnl)
                    if _streak_pnl < 0:
                        streak = self._symbol_loss_streaks.get(_streak_sym, 0) + 1
                        self._symbol_loss_streaks[_streak_sym] = streak
                        if streak >= CONFIG.risk.symbol_loss_streak_threshold:
                            _existing_expiry = self._symbol_cooldowns.get(_streak_sym, 0)
                            _new_expiry = (time.monotonic()
                                           + CONFIG.risk.symbol_loss_streak_cooldown_seconds)
                            self._symbol_cooldowns[_streak_sym] = max(_existing_expiry, _new_expiry)
                            # Reset so it takes a fresh run of losses to re-trip
                            # once this cooldown clears, rather than tripping
                            # again on the very next loss.
                            self._symbol_loss_streaks[_streak_sym] = 0
                            audit(system_log,
                                  f"Symbol loss-streak cooldown armed: {_streak_sym} "
                                  f"({streak} consecutive losses) blocked for "
                                  f"{int(CONFIG.risk.symbol_loss_streak_cooldown_seconds)}s",
                                  action="symbol_loss_streak", result="COOLDOWN_ARMED",
                                  data={"symbol": _streak_sym, "streak": streak})
                    elif _streak_pnl > 0:
                        self._symbol_loss_streaks[_streak_sym] = max(
                            0, self._symbol_loss_streaks.get(_streak_sym, 0) - 1)
                    # pnl == 0 (breakeven): no change, matching the account-wide streak.
            except Exception as _sls_exc:
                logger.debug("Symbol loss-streak tracking skipped: %s", _sls_exc)

        # Public mind-stream: operator-account closes only (user_id "" is the
        # operator executor; per-user closes carry that user's id and stay
        # private). Realized PnL is already public on the track-record page.
        try:
            from bot.core.agent_feed import FEED
            if not user_id:
                _fpnl = getattr(pos, "pnl_usd", None)
                _fsym = getattr(pos, "symbol", "")
                if _fpnl is not None and _fsym:
                    _fpnl = float(_fpnl)
                    _freason = str(getattr(pos, "close_reason", "") or "")
                    FEED.emit(
                        "trade_close",
                        f"Closed {_fsym} "
                        f"{'+' if _fpnl >= 0 else '-'}${abs(_fpnl):,.2f}",
                        body=f"Exit: {_freason}" if _freason else "",
                        symbol=_fsym,
                        severity="success" if _fpnl >= 0 else "warning",
                        data={"pnl": round(_fpnl, 2), "reason": _freason})
        except Exception as _feed_exc:
            logger.debug("Agent feed close event skipped: %s", _feed_exc)

        # Push real live state to the website dashboard. The paper portfolio's
        # close callback (_on_trade_close_composite, above) already does this
        # for paper trades; live closes never reached the website at all before
        # this, so the dashboard showed paper/default data for live users.
        try:
            self._sync_live_state_to_website()
        except Exception as _sync_exc:
            logger.debug("Live website sync skipped: %s", _sync_exc)

    def _sync_live_state_to_website(self) -> None:
        """Push the live executor's real open positions, recent closed trades,
        and equity to the website dashboard (fire-and-forget, fail-open).

        Mirrors _on_trade_close_composite's paper sync, but sources from the
        actual LiveExecutor instead of the paper portfolio -- so a live user's
        dashboard reflects their real Bitget account, not simulated state.
        """
        from bot.utils.website_sync import sync_in_background

        executor = self.live_executor

        def _open_dict(pos) -> dict:
            return {
                "asset": pos.symbol,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "commission": pos.commission or 0,
                "pattern": pos.signal_type,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "opened_at": pos.opened_at,
            }

        def _closed_dict(pos) -> dict:
            d = _open_dict(pos)
            d["exit_price"] = pos.close_price or 0
            d["pnl"] = pos.pnl_usd or 0
            d["closed_at"] = pos.closed_at
            return d

        positions = [_open_dict(p) for p in executor.open_positions]
        closed = [_closed_dict(p) for p in executor.closed_positions[-50:]]
        # Truthful equity: in LIVE mode with an empty balance cache this must
        # send None (website renders "unavailable"), never the paper baseline
        # that get_effective_equity() silently falls back to.
        equity, _eq_src = self.resolve_display_equity_sync()
        user_id = getattr(executor, "user_id", None) or 1
        sync_in_background(user_id, equity, positions, closed)

    def _intent_engine_caps(self) -> dict:
        """The authoritative engine caps a compiled policy is clamped against
        (so a policy can only tighten). Missing caps are simply omitted."""
        caps = {
            "max_position_pct": getattr(CONFIG.risk, "max_position_pct", None),
            "max_symbol_exposure_pct": getattr(CONFIG.risk, "max_symbol_exposure_pct", None),
            "max_portfolio_exposure_pct": getattr(CONFIG.risk, "max_portfolio_exposure_pct", None),
            "max_open_positions": getattr(CONFIG.risk, "max_open_positions", None),
            "min_confidence": getattr(CONFIG.risk, "min_confidence", None),
            "min_risk_reward": getattr(CONFIG.risk, "min_risk_reward", None),
            "max_daily_loss_pct": getattr(CONFIG.risk, "max_daily_loss_pct", None),
            "max_drawdown_pct": getattr(CONFIG.risk, "max_drawdown_pct", None),
        }
        return {k: v for k, v in caps.items() if v is not None}

    def _load_intent_policy_onto(self, engine, owner: str = "operator") -> Optional[dict]:
        """Guardian Intent Compiler: compile the on-disk intent policy against the
        live caps and bind it to ``engine``. Fail-open: any error (flag off,
        missing/invalid file, compile fault) leaves the engine with NO policy, so
        this can never block startup or a trade. Returns the compiled policy (or
        None). Path: INTENT_POLICY_PATH env, default config/intent_policy.json.
        """
        try:
            if not getattr(CONFIG.risk, "intent_policy_enabled", False):
                engine.set_intent_policy(None)
                return None
            import json as _json
            import os as _os
            from bot.guardian import intent_policy as _ip
            path = _os.getenv("INTENT_POLICY_PATH", "config/intent_policy.json")
            if not _os.path.exists(path):
                engine.set_intent_policy(None)
                return None
            with open(path, "r", encoding="utf-8") as fh:
                spec = _json.load(fh)
            policy = _ip.compile_policy(spec, self._intent_engine_caps())
            engine.set_intent_policy(policy)
            system_log.info(
                "Guardian intent policy loaded (%s): %s · mode=%s · %d rule(s)%s",
                owner, policy.get("policy_id"), policy.get("mode"),
                len(policy.get("rules", [])),
                (" · warnings: " + "; ".join(policy["warnings"])) if policy.get("warnings") else "")
            return policy
        except Exception as exc:
            logger.debug("Intent policy load skipped (%s): %s", owner, exc)
            try:
                engine.set_intent_policy(None)
            except Exception:
                pass
            return None

    def reload_intent_policy(self) -> Optional[dict]:
        """Re-read + recompile the operator intent policy onto the live engine
        (e.g. after editing the file or flipping shadow→enforce). Fail-open."""
        return self._load_intent_policy_onto(self.risk, owner="operator")

    def _intent_policy_path(self) -> str:
        import os as _os
        return _os.getenv("INTENT_POLICY_PATH", "config/intent_policy.json")

    def write_intent_policy(self, policy: dict) -> Optional[dict]:
        """Guardian Intent authoring: persist a compiled policy to disk and
        reload it onto the live engine. Returns the BOUND policy (what the risk
        gate will actually consult) — ``None`` when nothing bound (e.g. the
        ``INTENT_POLICY_ENABLED`` flag is off, so it is saved-but-dormant until
        the operator enables + restarts). Fail-open: a write/reload fault raises
        so the caller can surface it, but never leaves the engine half-bound.
        """
        path = self._intent_policy_path()
        atomic_write_json(path, policy, indent=2, sort_keys=False)
        return self.reload_intent_policy()

    def set_intent_policy_mode(self, mode: str) -> Optional[dict]:
        """Flip the on-disk policy's mode (off | shadow | enforce) and reload.
        Returns the bound policy (or None). Raises if there is no policy file."""
        import json as _json
        import os as _os
        if mode not in ("off", "shadow", "enforce"):
            raise ValueError(f"invalid mode {mode!r}")
        path = self._intent_policy_path()
        if not _os.path.exists(path):
            raise FileNotFoundError("no intent policy to change")
        with open(path, "r", encoding="utf-8") as fh:
            spec = _json.load(fh)
        spec["mode"] = mode
        return self.write_intent_policy(spec)

    def clear_intent_policy(self) -> bool:
        """Remove the on-disk policy and unbind it from the live engine.
        Returns True if a policy file was removed. Fail-open."""
        import os as _os
        path = self._intent_policy_path()
        removed = False
        try:
            if _os.path.exists(path):
                _os.remove(path)
                removed = True
        finally:
            try:
                self.risk.set_intent_policy(None)
            except Exception:
                pass
        return removed

    def _emit_policy_decision(self, recheck, trade_id: str, symbol: str, user_id: str = "") -> None:
        """Guardian: seal a first-class POLICY_DECISION event (the Policy Decision
        Record) on the tamper-evident chain when a compiled policy was consulted
        for this trade — logged whether the trade executed or was rejected, so the
        policy's verdict is provable independently of the DECISION outcome.
        Best-effort, fail-open — never affects a trade."""
        try:
            pol = getattr(recheck, "intent_policy", None)
            if not pol:
                return
            payload = dict(pol)
            payload["decision_id"] = trade_id
            payload["symbol"] = symbol
            self.audit_chain.append("POLICY_DECISION", payload, actor=str(user_id or "operator"))
        except Exception as exc:
            logger.debug("Policy decision record skipped: %s", exc)

    def firewall_scan(self, text: str, source: str = "chat", user_id: str = "") -> Optional[dict]:
        """Guardian Prompt-Injection & Transaction Firewall: scan inbound
        chat-action text for manipulation shapes and seal a FIREWALL verdict on
        the tamper-evident chain.

        TELEMETRY-FIRST + fail-open. Returns the compact verdict dict (``risk``,
        ``score``, ``categories``, ``excerpt``, …) so the caller can decide whether
        to warn/refuse, or ``None`` when the firewall is disabled or the scan is a
        no-op. The scan itself never raises and never blocks — enforcement is the
        caller's gated choice. Only non-clean verdicts (or hidden-char smuggling)
        are recorded, to keep the chain signal-dense.
        """
        try:
            if not getattr(CONFIG.risk, "guardian_firewall_enabled", False):
                return None
            if not text or not str(text).strip():
                return None
            from bot.guardian import firewall as _fw
            verdict = _fw.verdict_payload(text, source=source, user_id=user_id)
            if verdict.get("risk", "none") != "none" or verdict.get("hidden_chars"):
                try:
                    self.audit_chain.append("FIREWALL", verdict, actor=str(user_id or "chat"))
                except Exception as exc:
                    logger.debug("Firewall verdict record skipped: %s", exc)
            return verdict
        except Exception as exc:
            logger.debug("Firewall scan skipped (%s): %s", source, exc)
            return None

    def _twin_positions(self, user_id: str = "") -> list:
        """Normalise the live book into the plain dicts the pure Digital Twin
        consumes: symbol, direction, entry, qty, cost/margin, leverage, and the
        correlation group (for correlated shocks). Operator book by default; a
        specific user's executor when ``user_id`` is given. Fail-open — a bad
        position is skipped, never fatal."""
        ex = self._user_executors.get(user_id) if user_id else self.live_executor
        if ex is None:
            ex = self.live_executor
        out: list = []
        for pos in (getattr(ex, "open_positions", None) or []):
            try:
                symbol = getattr(pos, "symbol", "?")
                try:
                    group = self.risk._correlation_group(symbol)
                except Exception:
                    group = "*"
                out.append({
                    "symbol": symbol,
                    "direction": getattr(pos, "direction", "LONG"),
                    "entry": getattr(pos, "entry_price", None),
                    "qty": getattr(pos, "quantity", None),
                    "cost_usd": getattr(pos, "cost_usd", None),
                    "leverage": getattr(pos, "leverage", 1),
                    "group": group,
                })
            except Exception:
                continue
        return out

    def run_digital_twin(self, user_id: str = "") -> Optional[dict]:
        """Guardian Portfolio Digital Twin: stress-test the current book against
        parametric price shocks and (when enabled) seal a TWIN verdict on the
        tamper-evident chain.

        Read-only foresight + fail-open: it never proposes, blocks, or alters a
        trade. Returns the full stress report (``scenarios``, ``worst``, ``risk``,
        ``fragile``) so a caller (e.g. the admin ``/twin`` command) can render it,
        or ``None`` when there is nothing to simulate. The TWIN chain event is
        written only when ``GUARDIAN_DIGITAL_TWIN_ENABLED`` is on — the simulation
        itself always runs, since it is pure and touches nothing."""
        try:
            from bot.guardian import digital_twin as _dt
            positions = self._twin_positions(user_id)
            if not positions:
                return None
            try:
                equity = self.get_effective_equity(user_id)
            except Exception:
                equity = 0.0
            report = _dt.run(positions, equity)
            if getattr(CONFIG.risk, "guardian_digital_twin_enabled", False):
                try:
                    payload = _dt.twin_payload(positions, equity)
                    self.audit_chain.append("TWIN", payload, actor=str(user_id or "operator"))
                except Exception as exc:
                    logger.debug("Twin verdict record skipped: %s", exc)
            return report
        except Exception as exc:
            logger.debug("Digital twin skipped: %s", exc)
            return None

    def run_risk_sentinel(self, user_id: str = "") -> Optional[dict]:
        """Guardian Systemic Risk Sentinel: assess intra-book crowding /
        concentration and (when enabled) seal a SENTINEL verdict on the
        tamper-evident chain.

        Read-only telemetry + fail-open: it never proposes, blocks, or alters a
        trade. Returns the crowding assessment (``concerns``, ``risk``,
        ``top_group``, ``net_bias``) so a caller (e.g. the admin ``/sentinel``
        command) can render it, or ``None`` when there is nothing to assess. The
        SENTINEL chain event is written only when ``GUARDIAN_RISK_SENTINEL_ENABLED``
        is on — the assessment itself always runs, since it is pure."""
        try:
            from bot.guardian import risk_sentinel as _rs
            positions = self._twin_positions(user_id)
            if not positions:
                return None
            report = _rs.analyze(positions)
            if getattr(CONFIG.risk, "guardian_risk_sentinel_enabled", False):
                try:
                    payload = _rs.sentinel_payload(positions)
                    self.audit_chain.append("SENTINEL", payload, actor=str(user_id or "operator"))
                except Exception as exc:
                    logger.debug("Sentinel verdict record skipped: %s", exc)
            return report
        except Exception as exc:
            logger.debug("Risk sentinel skipped: %s", exc)
            return None

    def run_escape_agent(self, user_id: str = "") -> Optional[dict]:
        """Guardian Universal Escape Agent: build a safe, ordered emergency-exit
        PLAN for the current book and (when enabled) seal an ESCAPE plan on the
        tamper-evident chain.

        PLAN-ONLY + read-only + fail-open: this never closes anything. It ranks
        the book by escape urgency (liquidation proximity × exposure) so the most
        dangerous positions are unwound first, and names the existing execution
        primitive to use. Returns the plan (``steps``, ``risk``, ``recommended``)
        so a caller (e.g. the admin ``/escape`` command) can render it, or ``None``
        when there is nothing to unwind. The ESCAPE chain event is written only
        when ``GUARDIAN_ESCAPE_ENABLED`` is on — the planning itself always runs,
        since it is pure. Execution stays with ``flatten_all_positions`` /
        ``close_all_positions`` / ``emergency_halt_all``."""
        try:
            from bot.guardian import escape_agent as _ea
            # FLAT AND FAILED WERE BOTH `None`, and the caller rendered None
            # as "no open positions to unwind" — so a planner fault produced an
            # all-clear. An empty book is now the planner's own flat document
            # (ok=True, "nothing to unwind"); None is reserved for the `except`
            # arm below, i.e. "could not tell", which the card renders as a
            # failure rather than as an empty book.
            positions = self._twin_positions(user_id)
            if not positions:
                return _ea.plan([])
            report = _ea.plan(positions)
            if getattr(CONFIG.risk, "guardian_escape_enabled", False):
                try:
                    payload = _ea.escape_payload(positions)
                    self.audit_chain.append("ESCAPE", payload, actor=str(user_id or "operator"))
                except Exception as exc:
                    logger.debug("Escape plan record skipped: %s", exc)
            return report
        except Exception as exc:
            logger.debug("Escape agent skipped: %s", exc)
            return None

    def guardian_status(self, user_id: str = "") -> dict:
        """Guardian console: one read-only snapshot of the whole safety layer —
        the evidence chain's health, the intent policy's state, the firewall's
        arming, and the live book's foresight (Digital Twin), crowding (Risk
        Sentinel) and unwind urgency (Escape Agent), plus which modules are armed.

        PURE READ + fail-open. Crucially it calls the *pure* Guardian modules
        directly (not the ``run_*`` helpers), so opening the console NEVER seals a
        chain event — a status view has no side effects. Every section degrades to
        a safe default on error, so the console can never raise."""
        status: dict = {
            "flags": {
                "intent_policy": bool(getattr(CONFIG.risk, "intent_policy_enabled", False)),
                "firewall": bool(getattr(CONFIG.risk, "guardian_firewall_enabled", False)),
                "firewall_block": bool(getattr(CONFIG.risk, "guardian_firewall_block_high", False)),
                "digital_twin": bool(getattr(CONFIG.risk, "guardian_digital_twin_enabled", False)),
                "risk_sentinel": bool(getattr(CONFIG.risk, "guardian_risk_sentinel_enabled", False)),
                "escape": bool(getattr(CONFIG.risk, "guardian_escape_enabled", False)),
            },
            "chain": {"length": 0, "ok": None, "tip": ""},
            "policy": None,
            # EVERY FAIL-OPEN DEFAULT ON THIS CONSOLE POINTED AT "SAFE".
            # The book assessment below is wrapped in try/except, and
            # `_twin_positions` returns [] for an unreadable book as well as a
            # flat one — so a console that could assess nothing reported
            # `posture: "none"`, the calmest reading it has, about a book it
            # never read. None means unknown here, and stays unknown all the
            # way to `posture` below.
            "twin": {"risk": None, "position_count": None},
            "sentinel": {"risk": None},
            "escape": {"risk": None},
            "posture": None,
        }
        # Evidence chain — length + tip cheaply; verification is best-effort.
        try:
            status["chain"]["length"] = self.audit_chain.get_chain_length()
            entries = self.audit_chain.get_entries(limit=1)
            status["chain"]["tip"] = entries[-1].entry_hash if entries else ""
            try:
                ok, _problems = self.audit_chain.verify(str(self.audit_chain._path))
                status["chain"]["ok"] = bool(ok)
            except Exception:
                pass
        except Exception:
            pass
        # Intent policy summary (what the risk gate would consult).
        try:
            status["policy"] = self._intent_policy_summary()
        except Exception:
            pass
        # Live-book assessments — the PURE modules, no chain writes.
        try:
            positions = self._twin_positions(user_id)
            if positions:
                from bot.guardian import digital_twin as _dt
                from bot.guardian import escape_agent as _ea
                from bot.guardian import risk_sentinel as _rs
                try:
                    equity = self.get_effective_equity(user_id)
                except Exception:
                    equity = 0.0
                twin = _dt.run(positions, equity)
                # `.get(k, "none")` turned an assessor that answered nothing
                # into one that answered "calm". `.get(k)` leaves absent absent.
                status["twin"] = {"risk": twin.get("risk"),
                                  "position_count": twin.get("position_count")}
                status["sentinel"] = {"risk": _rs.analyze(positions).get("risk")}
                status["escape"] = {"risk": _ea.plan(positions).get("risk")}
            else:
                # A genuinely flat book IS assessable and its risk really is
                # "none". That is a different fact from the None defaults
                # above, which survive only when the read or the assessment
                # RAISED — the case that used to report the calmest posture
                # there is about a book nobody managed to look at.
                #
                # `_twin_positions` is itself fail-open and returns [] for an
                # unreadable book too, so this branch is not a perfect
                # discriminator. Probing the executor to close that gap was
                # tried and removed: it coupled this to executor internals to
                # buy a case nothing demonstrated, and the try/except already
                # covers every fault that actually raises.
                status["twin"] = {"risk": "none", "position_count": 0}
                status["sentinel"] = {"risk": "none"}
                status["escape"] = {"risk": "none"}
        except Exception as exc:
            logger.debug("Guardian status book assessment skipped: %s", exc)
        # Overall posture = worst live-book risk across the three assessments.
        try:
            # `order.get(r, 0)` ranked anything unrecognised — including a
            # missing assessment — as the SAFEST input, so `max` would discard
            # it in favour of any real reading. An unknown is not a low risk;
            # it is the absence of a reading, and the posture says so rather
            # than quietly reporting the worst of what happened to work.
            order = {"none": 0, "low": 1, "medium": 2, "high": 3}
            reads = [status["twin"]["risk"], status["sentinel"]["risk"],
                     status["escape"]["risk"]]
            known = [r for r in reads if r in order]
            if not known:
                status["posture"] = None
            else:
                worst = max(known, key=lambda r: order[r])
                # A worst-of over a partial set is a partial answer. Say so
                # rather than presenting it as the whole book's posture.
                status["posture"] = worst
                status["posture_partial"] = len(known) < len(reads)
        except Exception:
            pass
        return status

    def _sync_flight_records(self) -> None:
        """Guardian Flight Recorder: push recent joined decision records + the
        engine-verified chain status to the website (fire-and-forget, fail-open).

        Telemetry only — never blocks, delays, or alters a trade. Reads the
        existing audit chain, joins DECISION↔OUTCOME, runs the authoritative
        ``verify()`` (the engine holds the file and the exact canonical hashing),
        and ships the last N records so the web can render and re-check them.
        """
        try:
            from bot.guardian.flight_recorder import (
                assemble_flight_records, assemble_incident_records)
            from bot.utils.website_sync import sync_flight_records_in_background

            entries = self.audit_chain.get_entries(limit=400)
            records = assemble_flight_records(entries, limit=50)
            incidents = assemble_incident_records(entries, limit=40)
            ok, problems = self.audit_chain.verify(str(self.audit_chain._path))
            tip = entries[-1].entry_hash if entries else ""
            chain = {
                "ok": bool(ok),
                "length": self.audit_chain.get_chain_length(),
                "tip_hash": tip,
                "problems": (problems or [])[:5],
            }
            try:
                gstatus = self.guardian_status()
            except Exception:
                gstatus = None
            sync_flight_records_in_background(
                records, chain, self._intent_policy_summary(), gstatus, incidents)
        except Exception as _fr_exc:
            logger.debug("Flight-record sync skipped: %s", _fr_exc)

    def _intent_policy_summary(self) -> Optional[dict]:
        """Compact, read-only view of the active operator intent policy for the
        website (the enforceable artifact is bot-side). None when no policy is
        bound. Fail-open — never raises into the sync path."""
        try:
            pol = self.risk.get_intent_policy()
            if not pol:
                return None
            return {
                "policy_id": pol.get("policy_id"),
                "label": pol.get("label"),
                "mode": pol.get("mode"),
                "compiled_hash": pol.get("compiled_hash"),
                "rules": pol.get("rules", []),
                "warnings": pol.get("warnings", []),
                "source_text": pol.get("source_text", ""),
                "enabled": bool(getattr(CONFIG.risk, "intent_policy_enabled", False)),
            }
        except Exception:
            return None

    def _push_scan_summary_to_website(self, signals: list) -> None:
        """Push a fresh regime/circuit-breaker/key-call summary every
        autonomous scan cycle (fire-and-forget, fail-open).

        _build_scan_payload's circuit_breaker section (equity, net_pnl,
        win_rate, trades, open positions, rules) only ever reads from
        engine.risk / engine.portfolio / live exchange data -- it does not
        actually need the caller's `results` list to be shaped like the
        manual /scan command's own lightweight scanner output. Calling it
        with an empty list still yields an accurate, real circuit-breaker
        section; only regime/entry_cards/key_call would stay placeholder,
        so this derives a real regime + key_call from BTC's own signal in
        the autonomous scanner's output (a different shape: .symbol/.price/
        .change_pct_24h/.momentum_score, not the manual scanner's .sym/.rsi).
        """
        from bot.skills.scan_skill import _build_scan_payload
        from bot.utils.website_sync import sync_scan_in_background

        payload = _build_scan_payload([], self)
        btc_sig = next(
            (s for s in (signals or []) if normalize_symbol(getattr(s, "symbol", "")).startswith("BTC")),
            None,
        )
        if btc_sig is not None:
            momentum = getattr(btc_sig, "momentum_score", 0.0) or 0.0
            change = getattr(btc_sig, "change_pct_24h", 0.0) or 0.0
            if momentum > 0.15 or change > 1.5:
                label, score = "BULLISH", min(momentum, 1.0)
            elif momentum < -0.15 or change < -1.5:
                label, score = "BEARISH", max(momentum, -1.0)
            else:
                label, score = "NEUTRAL", momentum
            payload["regime"] = {
                "label": label, "score": round(score, 2),
                "gate": getattr(btc_sig, "price", 0.0) or 0.0,
                "long_short": "", "funding": "",
            }
            payload["key_call"] = (
                f"<b>Autonomous scan</b> — {len(signals)} pairs scanned this cycle\n"
                f"BTC 24h change: {change:+.2f}% | momentum {momentum:+.2f}\n"
                f"Scanned at {datetime.now(UTC).strftime('%H:%M UTC')}"
            )
        payload["config"] = self._build_strategy_config_summary()
        sync_scan_in_background(payload)

    def _build_strategy_config_summary(self) -> dict:
        """Real (not fabricated) strategy/risk knobs for the website's
        STRATEGY / LOGIC page -- every value here reads directly from the
        same CONFIG/RUNTIME the engine itself trades against.
        """
        from bot.config import RUNTIME

        st = CONFIG.strategy_types
        return {
            "mode": "LIVE" if CONFIG.is_live() else "PAPER",
            "min_confidence": CONFIG.risk.min_confidence,
            "max_open_positions": CONFIG.risk.max_open_positions,
            "max_daily_loss_pct": CONFIG.risk.max_daily_loss_pct,
            "max_drawdown_pct": CONFIG.risk.max_drawdown_pct,
            "symbol_loss_streak_enabled": CONFIG.risk.symbol_loss_streak_enabled,
            "adaptive_threshold_enabled": CONFIG.adaptive.adaptive_threshold_enabled,
            "auto_confirm_threshold": round(RUNTIME.auto_confirm_threshold, 2),
            "strategy_types": {
                st_name: {
                    "min_confidence": st.get_min_confidence(st_name),
                    "time_close_hours": st.get_time_close_hours(st_name),
                    "time_warn_hours": st.get_time_warn_hours(st_name),
                }
                for st_name in ("scalp", "intraday", "swing", "position")
            },
        }

    def _executor_for(self, user_id: str = ""):
        """Return the LiveExecutor that should place THIS caller's live order.

        Default (PER_USER_LIVE_ENABLED off): ALWAYS the shared operator executor
        — byte-identical to before. When per-user live trading is enabled AND the
        caller is a real human user (not '' / 'auto') who has linked + decryptable
        keys, returns that user's OWN executor (created lazily, cached, rebuilt if
        the user's key changes). If per-user is on but the user has no usable
        credentials, falls back to the operator executor so behaviour never
        silently breaks — eligibility enforcement is a Phase 5 access-policy
        concern layered on top, not here.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return self.live_executor
        # Auto-trade ('auto') and unattended ('') paths run on the operator
        # account, not an individual user's.
        if not user_id or user_id in ("auto", ""):
            return self.live_executor
        try:
            from bot.core.exchange_credentials import get_credential_store
            _store = get_credential_store()
            creds = _store.get(user_id)
            venue = getattr(_store, "get_venue", lambda _u: "bitget")(user_id)
        except Exception as exc:
            logger.warning("Per-user executor: credential lookup failed for %s: %s "
                           "— using operator executor", user_id, exc)
            creds = None
            venue = "bitget"
        if not creds:
            return self.live_executor
        key = str(user_id)
        ex = self._user_executors.get(key)
        # Rebuild if absent or the user's credentials changed (e.g. re-/connect,
        # or switched venue). Full-dict compare is venue-agnostic — Hyperliquid
        # records have no api_key.
        if ex is None or (ex._credentials or {}) != creds:
            ex = LiveExecutor(user_id=user_id, credentials=creds, venue=venue)
            # Capture this user's id in the callback (default-arg avoids the
            # late-binding closure trap) so a per-user live close feeds THAT
            # user's risk engine, not the operator's (audit C1).
            ex.on_position_closed = (
                lambda pos, uid=user_id: self._on_live_position_closed(pos, uid))
            # _risk_engine here only forwards INFRASTRUCTURE warnings (feed
            # staleness etc.), which are shared market-level state — keep it on
            # the operator engine. Per-user ACCOUNT breakers are routed via the
            # close callback above (risk_for(uid)), not this handle.
            ex._risk_engine = self.risk
            # Share the same WS feed as the operator executor (market data is not
            # per-user) so degradation uses real price-staleness, not the shadow clock.
            ex._ws_feed = self.ws_feed
            # Record realized slippage into the shared tracker (no-op until set).
            ex._slippage_tracker = getattr(self, "slippage", None)
            # NB3: apply this user's pinned leverage (reduce-only vs the operator
            # cap; None → operator default). Best-effort — never blocks binding.
            try:
                from bot.core import user_leverage_store as _lev_store
                ex._user_leverage_pref = _lev_store.get(user_id)
            except Exception:
                ex._user_leverage_pref = None
            self._user_executors[key] = ex
            audit(system_log, f"Per-user live executor bound for user {user_id}",
                  action="per_user_executor", result="BOUND", data={"user": key})
        return ex

    def balance_view_executor(self, user_id: str = ""):
        """Return the LiveExecutor for a READ-ONLY balance view of the caller's
        OWN account (used by /livebalance).

        Unlike _executor_for — which gates on PER_USER_LIVE_ENABLED because it
        places orders — viewing your own balance is read-only and must work the
        moment you /connect (it is the very same read-only balance check that
        /connect itself runs to validate the keys). So this resolver ignores the
        live-trading flag:

          - caller has decryptable linked (/connect) credentials -> a per-user
            executor bound to THEIR account (their explicit choice to link it);
          - otherwise -> the shared operator executor (unchanged behaviour for
            the operator/admin, who view the global CONFIG.exchange account).

        These view-only executors are cached in a dedicated dict, NOT in
        _user_executors, so they are never picked up by all_executors() (the
        monitoring / reconciliation / close loops). A view-only account has no
        bot-placed positions to manage while per-user live trading is off.
        """
        if not user_id or user_id in ("auto", ""):
            return self.live_executor
        try:
            from bot.core.exchange_credentials import get_credential_store
            _store = get_credential_store()
            creds = _store.get(str(user_id))
            venue = getattr(_store, "get_venue", lambda _u: "bitget")(str(user_id))
        except Exception as exc:
            logger.warning("balance_view_executor: credential lookup failed for "
                           "%s: %s — using operator executor", user_id, exc)
            creds = None
            venue = "bitget"
        if not creds:
            return self.live_executor
        key = str(user_id)
        ex = self._balance_view_executors.get(key)
        # Rebuild if absent or the user's credentials changed (venue-agnostic
        # full-dict compare — Hyperliquid records have no api_key).
        if ex is None or (ex._credentials or {}) != creds:
            ex = LiveExecutor(user_id=user_id, credentials=creds, venue=venue)
            # Share the operator WS feed (market data is not per-user). No
            # on_position_closed / risk wiring: this executor never trades.
            ex._ws_feed = self.ws_feed
            self._balance_view_executors[key] = ex
        return ex

    def set_live_auth_status(self, ok: bool, detail: str = "",
                             user_id: str = "") -> None:
        """Record whether ACCOUNT ``user_id`` ("" = operator) currently
        authenticates with its venue.

        When an account is marked NOT ok, new live ENTRIES on it are halted
        (``live_auth_healthy`` returns False and the pre-execute gate refuses to
        open) — but open positions keep being monitored and closed. Set by the
        boot credential preflight, the per-user auth sweep, and any live auth
        failure observed during operation. Logs the transition loudly so a
        recovery/regression is visible."""
        key = str(user_id or "")
        prev = self._live_auth_ok.get(key, True)
        self._live_auth_ok[key] = bool(ok)
        self._live_auth_detail[key] = str(detail or "")
        who = key or "operator"
        if prev and not ok:
            logger.critical(
                "Live auth DOWN for account '%s': %s — NEW live entries halted "
                "until authentication is restored (open positions still "
                "monitored).", who, detail)
        elif ok and not prev:
            logger.info("Live auth restored for account '%s' — live entries "
                        "resume.", who)

    def live_auth_healthy(self, user_id: str = "") -> bool:
        """True unless ACCOUNT ``user_id`` has been marked as failing venue auth.
        Unknown accounts default True (allow) so nothing is blocked until a probe
        or a live auth error actually reports a failure — fail-open on detection,
        fail-closed only on a confirmed failure."""
        return self._live_auth_ok.get(str(user_id or ""), True)

    def invalidate_user_executor(self, user_id: str) -> None:
        """Drop any cached per-user executor (e.g. after /connect or /disconnect)
        so the next trade — and the next balance view — rebuilds it from the
        current stored credentials. Safe to call when none exists. Never touches
        the shared operator executor."""
        self._user_executors.pop(str(user_id), None)
        self._balance_view_executors.pop(str(user_id), None)

    async def switch_venue(self, venue_id: str) -> str:
        """Hot-swap the shared operator executor onto another trading venue.

        Called by the admin /venue command AFTER it has preflighted the
        target venue's credentials. Refuses while any live position or
        pending entry is open — position records carry venue-native
        symbols and the monitoring/close paths would route them to the
        wrong exchange. The override is persisted (data/venue_override.json)
        so the choice survives restarts; per-user executors stay Bitget.

        Every consumer reads self.live_executor live (no captured refs —
        verified), so replacing the attribute plus re-running the four
        wiring lines from __init__ is a complete swap. On any failure the
        old executor stays active and the override is rolled back.
        Returns a short human-readable result string.
        """
        from bot.core.venues import get_venue, set_venue_override

        target = get_venue(venue_id)
        old_exec = self.live_executor
        current = old_exec._venue.id
        if target.id == current:
            return f"already trading on {target.display_name}"

        open_positions = [p for p in old_exec.open_positions]
        if open_positions:
            syms = ", ".join(display_symbol(p.symbol) for p in open_positions[:5])
            return (f"REFUSED: {len(open_positions)} open/pending position(s) "
                    f"on {old_exec._venue.display_name} ({syms}). Close them "
                    f"first — venue switch with live positions would orphan "
                    f"their monitoring.")

        prev_override_needed_rollback = False
        try:
            set_venue_override(target.id)
            prev_override_needed_rollback = True
            new_exec = LiveExecutor()  # reads get_venue() -> the new override
            # Re-run the __init__ wiring (see engine startup: on_position_closed,
            # _risk_engine, _ws_feed, _slippage_tracker).
            new_exec.on_position_closed = lambda pos: self._on_live_position_closed(pos)
            new_exec._risk_engine = self.risk
            new_exec._ws_feed = self.ws_feed
            new_exec._slippage_tracker = getattr(self, "slippage", None)
            self.live_executor = new_exec
        except Exception as exc:
            if prev_override_needed_rollback:
                try:
                    set_venue_override(current if current != getattr(
                        CONFIG.exchange, "venue", "bitget") else None)
                except Exception:
                    pass
            audit(system_log, f"Venue switch to {target.id} FAILED: {exc}",
                  action="venue_switch", result="FAIL",
                  data={"from": current, "to": target.id, "error": str(exc)[:200]})
            return f"FAILED: {exc} — still trading on {old_exec._venue.display_name}"

        try:
            await old_exec.close()
        except Exception:
            pass  # old connection cleanup is best-effort
        audit(system_log,
              f"Venue switched: {current} -> {target.id} (operator executor)",
              action="venue_switch", result="OK",
              data={"from": current, "to": target.id})
        return f"switched: {current} → {target.id}"

    def _is_operator_user(self, user_id) -> bool:
        """True if this user trades on the OPERATOR account — i.e. an admin or a
        member of the operator/admin env allowlist. A regular user is NOT an
        operator and, under per-user live trading, must link their own keys.
        """
        uid = str(user_id)
        for raw in (CONFIG.telegram.chat_id, CONFIG.telegram.admin_ids):
            if raw and uid in {s.strip() for s in str(raw).split(",") if s.strip()}:
                return True
        store = getattr(self, "_user_store", None)
        if store is not None:
            try:
                u = store.get(uid)
                if u and u.get("role") == "admin":
                    return True
            except Exception:
                pass
        return False

    def viewer_executor(self, user_id: str = ""):
        """The LiveExecutor whose positions/closed-trades THIS caller may VIEW —
        the single source of truth for every status/portfolio card so they never
        disagree about which account they describe.

        Mirrors ``_executor_for`` but adds the view-layer isolation guard: with
        per-user live ON, a non-operator caller whose resolution only *falls
        back* to the shared operator executor gets ``None`` instead — they must
        never see another account's book. With per-user OFF this is always the
        operator executor (byte-identical to single-account behaviour).
        """
        ex = self._executor_for(user_id)
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return ex
        if ex is self.live_executor and not self._is_operator_user(user_id):
            return None
        return ex

    def risk_for(self, user_id: str = ""):
        """Return the RiskEngine whose stateful safety breakers apply to THIS caller.

        Default (PER_USER_LIVE_ENABLED off): ALWAYS the shared operator engine —
        byte-identical to before. When per-user live trading is on, a real human
        user (not ''/'auto', and not an operator/admin) gets their OWN RiskEngine
        bound to their OWN portfolio and persisted to data/risk_state_{user}.json,
        so one user's loss streak / circuit breaker / daily-loss / drawdown can't
        trip another user's halt.

        Only ACCOUNT-specific state is isolated. MARKET-wide context (regime,
        order-flow signal, rolling price history) is shared from the operator
        engine via _sync_risk_market_context so every user evaluates against
        identical market conditions — the per-user split must never loosen a
        market gate, only separate the account breakers.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return self.risk
        # Auto-trade ('auto') and unattended ('') paths run the operator engine.
        if not user_id or user_id in ("auto", ""):
            return self.risk
        # Operators/admins trade the operator account → operator engine.
        if self._is_operator_user(user_id):
            return self.risk
        key = str(user_id)
        eng = self._user_risk.get(key)
        if eng is None:
            try:
                safe = self.user_portfolios._sanitize(user_id)
            except Exception:
                return self.risk
            eng = RiskEngine(
                self.user_portfolios.get(user_id),
                state_file=f"data/risk_state_{safe}.json",
                macro_calendar=self.macro_calendar,
                macro_provider=self.macro_provider,
            )
            self._user_risk[key] = eng
            audit(system_log, f"Per-user risk engine bound for user {user_id}",
                  action="per_user_risk", result="BOUND", data={"user": key})
        self._sync_risk_market_context(eng)
        return eng

    def _apply_regime_to(self, engine, symbol) -> None:
        """Bridge the analyzer's per-symbol market regime onto ``engine`` so its
        per-regime size multiplier applies during evaluate().

        Gated by REGIME_SIZING_ENABLED (default OFF): when off this is a no-op, so
        the regime stays UNKNOWN and the multiplier is 1.0× — byte-identical to
        before. The analyzer's Regime values are already a subset of the risk
        engine's _REGIME_MULTIPLIERS keys, so regime.value passes straight through;
        volatility overlay is left NORMAL (regime is the lever here). Fail-open:
        any lookup error leaves the engine's regime untouched.
        """
        if not getattr(CONFIG.risk, "regime_sizing_enabled", False):
            return
        try:
            regimes = getattr(self.analyzer, "_current_regimes", None)
            reg = regimes.get(symbol) if regimes else None
            if reg is not None:
                engine.set_regime(reg.value, "NORMAL")
        except Exception as exc:
            logger.debug("Regime bridge skipped for %s: %s", symbol, exc)

    def _sync_risk_market_context(self, eng) -> None:
        """Mirror MARKET-wide (not account-specific) state from the operator
        engine onto a per-user engine so every user's market gates see identical
        conditions. Shares references where the underlying data is global (price
        history, order-flow signal). Fail-open: never block a trade on a sync
        hiccup — the per-user engine just keeps its own (safe-default) context."""
        if eng is self.risk:
            return
        try:
            eng._current_regime = self.risk._current_regime
            eng._current_vol_state = self.risk._current_vol_state
            eng._last_of_signal = self.risk._last_of_signal
            eng._order_flow = self.risk._order_flow
            eng._price_history = self.risk._price_history  # shared global series
        except Exception as exc:
            logger.debug("Per-user risk market-context sync skipped: %s", exc)

    def _route_user_trade_close(self, user_id: str, pnl: float) -> None:
        """Feed a user's closed-trade PnL into the RiskEngine that owns that
        user's streak/breaker state. In default mode risk_for() returns the
        shared engine, so this is identical to self.risk.record_trade_result."""
        try:
            self.risk_for(user_id).record_trade_result(pnl)
        except Exception as exc:
            # Never let streak bookkeeping break a close; fall back to shared.
            logger.warning("Per-user trade-close routing failed for %s: %s — "
                           "recording on shared engine", user_id, exc)
            try:
                self.risk.record_trade_result(pnl)
            except Exception:
                pass

    def per_user_live_eligibility(self, user_id) -> tuple:
        """Whether THIS human user's confirmed live trade may execute, and why.

        Returns ``(ok, reason)``. Only meaningful while PER_USER_LIVE_ENABLED is
        on and for a real human confirm (not 'auto'/''). The rule: an operator/
        admin trades on the operator account (always ok); a regular user must
        have their OWN linked, decryptable keys — otherwise their trade is
        REJECTED rather than silently placed on the operator's account.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return True, "per-user live trading disabled (operator account)"
        if not self._human_confirmed(user_id):
            return True, "operator/auto path"
        if self._is_operator_user(user_id):
            return True, "operator/admin user"
        # A regular user must have their OWN linked, decryptable keys …
        try:
            from bot.core.exchange_credentials import get_credential_store
            if not get_credential_store().get(user_id):
                return False, "no linked Bitget account — use /connect to link one"
        except Exception as exc:
            return False, f"credential lookup failed: {exc}"
        # … AND clear the live-access policy. Two policies, one switch:
        #
        #   LIVE_OPEN_TO_KEY_HOLDERS on  — linked keys ARE the opt-in. Bringing
        #       your own account is the consent, and nothing of the operator's
        #       is at risk because the keys check above already routed this
        #       order to the user's own executor. An explicit revoke still
        #       denies.
        #   off — the staged rollout: an admin must /grant_live first.
        #
        # Fail-CLOSED for real money either way: if the user store is not
        # available to answer, deny rather than assume permission. That matters
        # more under the open policy, not less — "no store" must never read as
        # "nobody is revoked".
        store = getattr(self, "_user_store", None)
        if store is None:
            return False, "live access policy unavailable — denying (fail-closed)"
        if getattr(CONFIG, "live_open_to_key_holders", False):
            try:
                if store.live_trading_revoked(user_id):
                    return False, "live trading revoked for this account"
            except Exception as exc:
                return False, f"live access check failed: {exc}"
            return True, "linked keys (live open to key holders)"
        try:
            if not store.can_trade_live(user_id):
                return False, ("not on the live allowlist — an admin must "
                               "approve you with /grant_live")
        except Exception as exc:
            return False, f"live allowlist check failed: {exc}"
        return True, "linked keys + live-allowlisted"

    def _all_live_executors(self) -> list:
        """The shared operator executor plus every active per-user executor.

        Monitoring/reconciliation loops iterate this so every account's open
        positions get SL/TP enforcement and reconciliation. With per-user live
        trading off (default) ``_user_executors`` is empty, so this is just
        ``[operator]`` and every loop runs exactly as it did before.
        """
        return [self.live_executor, *self._user_executors.values()]

    async def account_risk_overview(self) -> list[dict]:
        """Read-only per-account live risk snapshot for admin observability.

        One row per active account (operator + every per-user executor):
        ``account``, ``user_id``, ``equity_usd``, ``open_positions``,
        ``exposure_usd`` (margin committed), ``circuit_open``,
        ``consecutive_losses``, ``error``. Breaker state is read directly from the
        engine that OWNS that account's safety state — the shared engine for the
        operator, the per-user engine (if one exists yet) for a user — so reading
        the overview never creates state as a side effect. Fail-open per account:
        an error on one account is captured in its ``error`` field instead of
        aborting the sweep. Default (per-user OFF) → just the operator row.
        """
        rows: list[dict] = []
        for ex in self._all_live_executors():
            uid = getattr(ex, "user_id", None)
            row = {
                "account": str(uid or "operator"), "user_id": uid,
                "equity_usd": None, "open_positions": 0, "exposure_usd": 0.0,
                "circuit_open": False, "consecutive_losses": 0,
                "governor": None, "throttle": None, "cap_usd": None, "error": None,
            }
            try:
                positions = list(getattr(ex, "open_positions", []) or [])
                row["open_positions"] = len(positions)
                row["exposure_usd"] = round(
                    sum(float(getattr(p, "cost_usd", 0.0) or 0.0) for p in positions), 2)
                # Operator-set per-trade margin cap (/setcap), for this user only.
                store = getattr(self, "_user_store", None)
                if uid and store is not None:
                    try:
                        row["cap_usd"] = store.max_margin(uid)
                    except Exception:
                        row["cap_usd"] = None
                bal = await self.get_user_live_equity(str(uid)) if uid else await self.get_live_equity()
                if bal:
                    row["equity_usd"] = round(float(bal.get("total", 0.0) or 0.0), 2)
                eng = self._user_risk.get(str(uid)) if uid else self.risk
                if eng is not None:
                    row["circuit_open"] = bool(eng.circuit_breaker_active)
                    row["consecutive_losses"] = int(getattr(eng, "consecutive_losses", 0) or 0)
                    try:
                        row["governor"] = eng.live_performance_state()
                    except Exception:
                        row["governor"] = None
                    try:
                        row["throttle"] = eng.equity_throttle_state()
                    except Exception:
                        row["throttle"] = None
            except Exception as exc:
                row["error"] = str(exc)
                logger.warning("Account overview: %s failed: %s", row["account"], exc)
            rows.append(row)
        return rows

    async def flatten_all_positions(self, reason: str = "manual_closeall") -> list[dict]:
        """Close every open position on EVERY account (operator + per-user).

        ``/closeall`` and the emergency kill-switch both route through here so a
        flatten can never miss a per-user account. Fail-open per account: one
        account's error is captured in its result and never blocks the others.
        Returns ``[{"account": label, "messages": [...]}, ...]``. No-op in paper
        mode. Default (per-user OFF) → just the operator account, as before.
        """
        results: list[dict] = []
        if not CONFIG.is_live():
            return results
        for ex in self._all_live_executors():
            label = getattr(ex, "user_id", None) or "operator"
            try:
                msgs = await ex.close_all_positions(reason=reason)
            except Exception as exc:
                logger.error("Flatten-all: account %s close failed: %s", label, exc)
                msgs = [f"close_all_positions failed: {exc}"]
            results.append({"account": str(label), "messages": list(msgs)})
        return results

    async def emergency_halt_all(self, reason: str = "emergency") -> dict:
        """GLOBAL KILL-SWITCH: stop NEW trades on every account and flatten ALL.

        1. Trip the circuit breaker on the shared operator engine AND every
           per-user RiskEngine, so no new trade can pass risk on any account.
        2. Clear all queued ideas (pending ideas / ATR / pyramid flags).
        3. Flatten open positions on every live executor (operator + per-user).

        Fail-open per step: an error on one engine/account never blocks halting
        the rest. Default (per-user OFF) → shared engine + operator account only,
        i.e. equivalent to the prior operator-only stop plus no-op per-user loops.
        Returns a structured summary for the caller to render.
        """
        engines_halted = 0
        # Raise the hard kill flag FIRST so any confirm already past its risk gate
        # is rejected at the pre-execute re-check before this flatten completes.
        self._halted = True
        try:
            self.risk.emergency_halt(reason)
            engines_halted += 1
        except Exception as exc:
            logger.error("Kill-switch: shared risk engine halt failed: %s", exc)
        for uid, eng in list(self._user_risk.items()):
            try:
                eng.emergency_halt(reason)
                engines_halted += 1
            except Exception as exc:
                logger.error("Kill-switch: user %s risk engine halt failed: %s", uid, exc)
        pending_cleared = len(self._pending_ideas)
        self._pending_ideas.clear()
        self._pending_atr.clear()
        self._pending_timing.clear()
        self._pending_pyramid.clear()
        accounts = await self.flatten_all_positions(reason=reason)
        audit(system_log, f"GLOBAL KILL-SWITCH engaged: {reason}",
              action="emergency_halt_all", result="OK",
              data={"engines_halted": engines_halted,
                    "pending_cleared": pending_cleared,
                    "accounts_flattened": len(accounts)})
        return {
            "reason": reason,
            "engines_halted": engines_halted,
            "pending_cleared": pending_cleared,
            "accounts": accounts,
        }

    def reset_circuit_breaker_all(self) -> int:
        """Resume after a global halt: reset the circuit breaker on the shared
        engine AND every per-user RiskEngine. Returns the number of engines
        reset. Fail-open per engine. Default (per-user OFF) → just the shared
        engine, identical to a bare reset_circuit_breaker()."""
        reset = 0
        self._halted = False  # resume: allow execution again
        try:
            self.risk.reset_circuit_breaker()
            reset += 1
        except Exception as exc:
            logger.error("Resume: shared risk engine reset failed: %s", exc)
        for uid, eng in list(self._user_risk.items()):
            try:
                eng.reset_circuit_breaker()
                reset += 1
            except Exception as exc:
                logger.error("Resume: user %s risk engine reset failed: %s", uid, exc)
        return reset

    def _rehydrate_user_executors(self) -> None:
        """Rebuild per-user executors for all linked users at startup so their
        PERSISTED live positions resume being monitored after a restart (per-user
        executors are otherwise created lazily on the next trade). No-op when
        per-user live trading is off, so the operator path is unchanged.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return
        try:
            from bot.core.exchange_credentials import get_credential_store
            ids = get_credential_store().user_ids()
        except Exception as exc:
            logger.warning("Per-user executor rehydrate skipped: %s", exc)
            return
        for uid in ids:
            try:
                # _executor_for builds, caches, and (via __init__) loads that
                # user's persisted positions; skips users with no usable keys.
                self._executor_for(uid)
            except Exception as exc:
                logger.warning("Rehydrate executor for %s failed: %s", uid, exc)
        if self._user_executors:
            audit(system_log,
                  f"Rehydrated {len(self._user_executors)} per-user executor(s) at startup",
                  action="per_user_rehydrate", result="OK")

    def get_effective_equity(self, user_id: str = "") -> float:
        """Return the equity figure to display/use for sizing.

        In LIVE mode: returns cached live exchange equity (USDT balance).
        In PAPER mode: returns the user's paper portfolio equity.
        """
        if CONFIG.is_live() and self._live_balance_cache:
            return self._live_balance_cache.get("total", 0.0)
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd

    async def get_effective_equity_async(self, user_id: str = "") -> float:
        """Async version that fetches live balance if cache is empty.

        Use this in Telegram command handlers to ensure fresh data.
        """
        if CONFIG.is_live():
            # Balance-mismatch fix: always route through get_live_equity(),
            # which honors the TTL and refreshes an EXPIRED cache. The old
            # `if not cache: refresh` short-circuit served a populated-but-
            # stale cache forever, so the status card could show an equity
            # tens of dollars away from the fresh /portfolio fetch. Fail-open:
            # on fetch failure get_live_equity() returns the cached value.
            bal = await self.get_live_equity()
            if bal:
                return bal.get("total", 0.0)
            if self._live_balance_cache:
                return self._live_balance_cache.get("total", 0.0)
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd

    async def resolve_display_equity(
        self, user_id: str = ""
    ) -> Tuple[Optional[float], str]:
        """Truthful equity for user-facing status cards.

        Returns ``(equity, source)`` where ``source`` is:
          - ``"live"``        real exchange equity for the account this user's
                              trades execute on (the shared operator account by
                              default), possibly a still-valid cached value.
          - ``"paper"``       genuine paper-mode portfolio equity.
          - ``"unavailable"`` LIVE mode, but the balance could not be read and
                              no cache exists.

        The whole point: in LIVE mode a failed balance read returns
        ``(None, "unavailable")`` — callers MUST render "unavailable" and MUST
        NOT substitute the paper $10k baseline (the recurring "bot shows $10,000
        in live mode" bug). A truthful $0.00 for a genuinely empty account is
        preserved because ``get_user_live_equity`` returns a dict (truthy) even
        when its ``total`` is 0.
        """
        if CONFIG.is_live():
            bal = await self.get_user_live_equity(user_id)
            if bal:
                return float(bal.get("total", 0.0) or 0.0), "live"
            return None, "unavailable"
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd, "paper"

    def resolve_display_equity_sync(
        self, user_id: str = ""
    ) -> Tuple[Optional[float], str]:
        """Sync counterpart of :meth:`resolve_display_equity` (cache-only).

        For sync call sites (e.g. building the chat system prompt) that cannot
        await a fresh fetch. In LIVE mode it reads the live-balance cache only;
        an empty cache yields ``(None, "unavailable")`` rather than paper $10k.
        """
        if CONFIG.is_live():
            if self._live_balance_cache:
                return self._live_balance_cache.get("total", 0.0), "live"
            return None, "unavailable"
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd, "paper"

    # -- C2-34: Combined State Persistence --

    def _wire_combined_state_saver(self) -> None:
        """Set up atomic combined state persistence.

        On first boot: if combined_state.json exists, load from it.
        Otherwise, if portfolio already loaded from legacy files, write combined.
        Wire both portfolio and risk_engine to use the combined saver.

        Skips loading when persistence is not active (e.g. in tests where
        portfolio is created fresh with no state file on disk).
        """
        import json as _json
        combined_path = Path(self._combined_state_file)

        # Only load/migrate if persistence is active (production mode) or
        # a combined state file exists from a prior run.
        if not self.portfolio._persistence_active and not combined_path.exists():
            # No persistence — just wire the saver for future use
            self.portfolio._combined_saver = self._save_combined_state
            self.risk._combined_saver = self._save_combined_state
            return

        if combined_path.exists():
            # Load from combined state file
            try:
                with open(combined_path) as f:
                    raw = f.read()
                if raw.strip():
                    combined = _json.loads(raw)
                    if "portfolio" in combined:
                        self.portfolio._load_from_state_dict(combined["portfolio"])
                        self.portfolio._persistence_active = True
                    if "risk" in combined:
                        self.risk._load_from_state_dict(combined["risk"])
                    system_log.info(
                        "C2-34: Loaded combined state (v%s, saved %s)",
                        combined.get("version", "?"),
                        combined.get("written_at", "?"),
                    )
            except Exception as exc:
                # Combined file corrupt — fall back to individual files
                # (which were already loaded by each component's __init__)
                system_log.warning(
                    "C2-34: Combined state corrupt (%s), using individual files",
                    exc,
                )
        else:
            # Legacy migration: individual files were already loaded by
            # portfolio.__init__ and risk_engine.__init__. Write the combined
            # file so subsequent boots use it.
            if self.portfolio._persistence_active:
                try:
                    self._save_combined_state()
                    system_log.info(
                        "C2-34: Migrated legacy state files to combined_state.json"
                    )
                except Exception as exc:
                    system_log.warning(
                        "C2-34: Migration write failed (%s), will retry on next save",
                        exc,
                    )

        # Wire both components to use combined saver
        self.portfolio._combined_saver = self._save_combined_state
        self.risk._combined_saver = self._save_combined_state

    def _save_combined_state(self) -> None:
        """Atomically write the OPERATOR's portfolio + risk state to a single file.
        Called by either portfolio._auto_save() or risk._save_state() whenever
        either component's state changes.

        Scope is the operator account by design (deep-audit #12). Per-user paper
        portfolios and per-user RiskEngines are deliberately NOT folded into this
        file — each persists independently and atomically to its OWN per-user file:
          • per-user portfolio → data/portfolio_{user}.json
            (MultiUserPortfolio.get / _load_existing; PortfolioTracker atomic save)
          • per-user risk      → data/risk_state_{user}.json
            (risk_for(user); RiskEngine._save_state_individual atomic save)
        Both are restored on startup from those files, so no per-user state is lost.
        Snapshotting them here too would create a second source of truth for the
        same account and risk write-skew between the two; keep this file
        operator-only. The test suite guards this intent
        (tests/test_combined_state_per_user_intent.py)."""
        combined = {
            "version": 1,
            "portfolio": self.portfolio._export_state_dict(),
            "risk": self.risk._export_state_dict(),
            "written_at": datetime.now(UTC).isoformat(),
        }
        combined_path = Path(self._combined_state_file)
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep one backup
        if combined_path.exists():
            backup = combined_path.with_suffix(".json.bak")
            try:
                import shutil
                shutil.copy2(str(combined_path), str(backup))
            except Exception:
                pass  # best-effort
        atomic_write_json(str(combined_path), combined, indent=2, default=str)
        # Persist the rename itself (not just the tmp contents) — best-effort.
        fsync_dir(str(combined_path))

    def _transition(self, new_state: AgentState, reason: str = "") -> None:
        """Transition the FSM to a new state. Every transition is audit-logged."""
        old_state = self.state
        transition = StateTransition(
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )
        self._state_history.append(transition)
        # L7: cap state history to prevent unbounded growth
        if len(self._state_history) > 1000:
            self._state_history = self._state_history[-500:]
        self.state = new_state
        self._last_state_change = time.time()
        audit(
            system_log,
            f"State transition: {old_state.value} -> {new_state.value}"
            + (f" ({reason})" if reason else ""),
            action="state_transition",
            data={"from": old_state.value, "to": new_state.value, "reason": reason},
        )

    @property
    def state_history(self) -> list[StateTransition]:
        """Full history of state transitions."""
        return self._state_history

    def set_confirmation_callback(self, cb: Callable) -> None:
        """Register the human-confirmation gate (e.g. Telegram inline keyboard)."""
        self._confirm_callback = cb

    def set_close_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when a trade is closed."""
        self._close_notify_callback = cb

    def set_fill_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when a limit order is filled (opened)."""
        self._fill_notify_callback = cb

    def set_sync_notify_callback(self, cb: Callable) -> None:
        """Register a callback for periodic exchange-sync adoption notices
        ("SYNC: Adopted untracked …") — informational, NOT closes."""
        self._sync_notify_callback = cb

    def set_adopt_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when an exchange position is adopted."""
        self._adopt_notify_callback = cb

    def set_auto_confirm_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify when a trade is auto-confirmed."""
        self._auto_confirm_notify_callback = cb

    # -- Main loop --

    async def run(self) -> None:
        """Start the continuous scan-analyze-monitor loop."""
        self._running = True
        self._transition(AgentState.IDLE, "engine started")
        audit(
            system_log,
            "Engine started",
            action="start",
            data={"simulation": CONFIG.simulation_mode},
        )
        # Start WebSocket feed for real-time price monitoring
        try:
            await self.ws_feed.start()
        except Exception as e:
            system_log.warning("WebSocket feed failed to start: %s", e)
        # Start dashboard pusher — only exists when configured (see __init__).
        if self.dashboard_pusher is not None:
            try:
                await self.dashboard_pusher.start()
            except Exception as e:
                system_log.warning("Dashboard pusher failed to start: %s", e)
        # Subscribe to core symbols so the WS connection stays alive
        # even when no positions are open.  Position-specific symbols
        # are added dynamically in _check_open_positions().
        self.ws_feed.subscribe(["BTC/USDT", "ETH/USDT", "SOL/USDT"])

        # Startup reconciliation: sync local state with exchange before
        # accepting any new signals. Catches positions closed/opened
        # during downtime or crashes.
        if CONFIG.is_live():
            # Rebuild per-user executors so their persisted positions are
            # reconciled/monitored from startup (no-op while per-user is off).
            self._rehydrate_user_executors()
            # Reconcile every account (operator + any per-user). With per-user
            # off this loops once over the operator — identical to before.
            for _ex in self._all_live_executors():
                try:
                    reconciled = await _ex.reconcile_positions()
                    for msg in reconciled:
                        audit(trade_log, f"Startup reconcile: {msg}",
                              action="startup_reconcile", result="CLOSED")
                except Exception as exc:
                    audit(system_log, f"Startup reconciliation error: {exc}",
                          action="startup_reconcile", result="ERROR")

                # Startup position sync: ensure tracked leverage and margin mode
                # match exchange reality. Catches manual changes on exchange or
                # mismatches from dynamic leverage not applying.
                try:
                    await _ex.sync_positions_from_exchange()
                except Exception as exc:
                    audit(system_log, f"Startup position sync error: {exc}",
                          action="startup_position_sync", result="ERROR")

                # Startup SL/TP verification: ensure all open positions have
                # SL/TP orders on exchange. Catches cases where SL/TP placement
                # failed silently (margin mode mismatch, precision errors, etc.)
                try:
                    await _ex.verify_and_fix_sltp()
                except Exception as exc:
                    audit(system_log, f"Startup SL/TP verification error: {exc}",
                          action="startup_sltp_verify", result="ERROR")

            # EXCHANGE = SOURCE OF TRUTH: sync the OPERATOR portfolio with the
            # exchange (per-user portfolios are isolated and not part of this
            # operator-level ghost/orphan sweep).
            try:
                sync_msgs = await sync_portfolio_with_exchange(self)
                for msg in sync_msgs:
                    audit(system_log, f"Exchange sync: {msg}",
                          action="startup_exchange_sync", result="SYNCED")
                # Positions adopted at boot (bot restarted while carrying a
                # live position) must reach the website too — the close-time
                # sync alone leaves them invisible on the dashboard.
                try:
                    self._sync_live_state_to_website()
                except Exception as _sync_exc:
                    logger.debug("Startup live website sync skipped: %s", _sync_exc)
            except Exception as exc:
                audit(system_log, f"Startup exchange sync error: {exc}",
                      action="startup_exchange_sync", result="ERROR")

        # Roadmap P0: exponential backoff on repeated tick failures. Previously a
        # persistent error (exchange outage, auth failure) retried every scan
        # interval forever, hammering the API (ban risk) and masking a degraded
        # state where positions may be unmonitored. Now we back off and escalate.
        _consecutive_failures = 0
        _BACKOFF_CAP_S = 300.0
        while self._running:
            try:
                await self._tick_guarded()
                _consecutive_failures = 0
                self._tick_consecutive_failures = 0
                # Dead-man's switch: ping an external health endpoint (e.g.
                # healthchecks.io) after each successful tick, so a DEAD
                # process — the one failure mode Telegram alerting can never
                # report — raises an alarm at the monitor's grace timeout.
                # Throttled + fail-open; no-op unless HEALTHCHECK_PING_URL set.
                await self._with_maintenance_cap(
                    self._maybe_ping_healthcheck(), "healthcheck ping")
                await self._with_maintenance_cap(
                    self._maybe_check_monitor_liveness(), "monitor liveness")
                # Web wallet (2b): pull any pending exchange-credential requests
                # the website queued and import them into the credential store.
                # Throttled, fail-open, no-op unless WEB_CREDS_KEY is configured.
                await self._with_maintenance_cap(
                    self._maybe_pull_web_credentials(), "web credential pull")
                # Web wallet (3b): process any emergency-stop flatten requests
                # (close the user's live positions via THEIR own executor). Async,
                # fail-open, throttled; guarded so it never touches another
                # account's positions.
                await self._with_maintenance_cap(
                    self._maybe_flatten_web_requests(), "web flatten requests")
                # Nightly LLM self-audit (advisory-only, human merge gate):
                # spawns as a background task at the configured quiet hour,
                # at most once per ~24h; the proactive monitor delivers the
                # report. Fail-open — a broken audit never touches trading.
                try:
                    if getattr(CONFIG, "self_audit_enabled", False):
                        from bot.core.self_audit import SELF_AUDIT
                        SELF_AUDIT.maybe_spawn(self)
                except Exception as _sa_exc:
                    system_log.debug("self-audit spawn skipped: %s", _sa_exc)
                # Continuous Proof-of-PnL publishing: re-derive the operator's
                # verifiable track record from raw fills, seal it, and persist it
                # as the latest publication — so the public /proof feed and the
                # MCP get_proof_of_pnl tool serve live data instead of an empty
                # store. Cadenced by the publisher, fail-open, DEFAULT-OFF
                # (PROOFOFPNL_PUBLISH_ENABLED); never touches trading.
                try:
                    await self._with_maintenance_cap(
                        self._maybe_publish_proofofpnl(), "proof-of-pnl publish")
                except Exception as _pop_exc:
                    system_log.debug("Proof-of-PnL publish tick skipped: %s", _pop_exc)
                # Per-user opt-in leaderboard publishing (community track).
                # Triple-gated default-OFF, throttled, fail-open — see method.
                try:
                    await self._with_maintenance_cap(
                        self._maybe_publish_user_leaderboards(), "user leaderboards")
                except Exception as _ulb_exc:
                    system_log.debug("User leaderboard tick skipped: %s", _ulb_exc)
                # Verifiable seasons: freeze in-window sealed statements into
                # the current season's standings. Local-disk only, fail-open.
                try:
                    self._maybe_snapshot_board_season()
                except Exception as _ssn_exc:
                    system_log.debug("Season snapshot tick skipped: %s", _ssn_exc)
            except Exception as exc:
                _consecutive_failures += 1
                self._tick_consecutive_failures = _consecutive_failures
                audit(
                    system_log,
                    f"Engine tick error (#{_consecutive_failures}): {exc}",
                    action="tick",
                    result="ERROR",
                    data={"consecutive_failures": _consecutive_failures,
                          "phase": (getattr(self, "_last_phase_timeout", None)
                                    or {}).get("phase")},
                )
                # Feed the warning-rate breaker so sustained failures can trip it.
                try:
                    self.risk.record_warning("engine_tick_failure")
                except Exception:
                    pass
                if _consecutive_failures >= 3:
                    audit(
                        system_log,
                        f"Engine tick has failed {_consecutive_failures} times in a row "
                        f"— trading may be degraded/unmonitored",
                        action="tick", result="CRITICAL_CONSECUTIVE_FAILURES",
                        data={"consecutive_failures": _consecutive_failures},
                    )
                # Exponential backoff (2x per failure, capped) instead of a tight retry loop.
                base = self._compute_smart_scan_interval()
                backoff = min(base * (2 ** _consecutive_failures), _BACKOFF_CAP_S)
                self._next_tick_due_ts = time.monotonic() + backoff
                await asyncio.sleep(backoff)
                continue
            _sleep_s = self._compute_smart_scan_interval()
            # Stamp the plan BEFORE parking: the stall watchdog treats time
            # inside a declared sleep as healthy, however long the smart-scan
            # interval legitimately is.
            self._next_tick_due_ts = time.monotonic() + _sleep_s
            await asyncio.sleep(_sleep_s)

    async def _tick_guarded(self) -> None:
        """_tick() under a hard liveness cap — the self-HEAL to the monitor's
        TICK_STALL self-diagnosis. A parked await (a network call whose
        timeout isn't effective) used to hang the loop forever; now it is
        cancelled and re-raised so the run() failure path COUNTS it (backoff,
        degraded alerts) and the loop recovers without a human restart. The
        cap is deliberately far above any legitimate tick (default 900s,
        1.5x the TICK_STALL threshold, so the stall alert with its stack
        diagnosis still fires first). 0 disables the guard entirely."""
        cap = float(getattr(CONFIG.monitoring, "tick_hard_timeout_sec", 0.0) or 0.0)
        if cap <= 0:
            await self._tick()
            return
        try:
            await asyncio.wait_for(self._tick(), timeout=cap)
        except asyncio.TimeoutError:
            audit(
                system_log,
                f"Tick exceeded the {cap:.0f}s hard timeout — cancelled the "
                f"parked await to recover the loop (see the TICK_STALL stack "
                f"diagnosis in the log for where it hung)",
                action="tick", result="HARD_TIMEOUT",
            )
            raise

    #: Stages of one analysis, in the order they run. Bound to the module
    #: constant so there is exactly one list.
    ANALYSIS_STAGES = ANALYSIS_STAGES

    def _stage_enter(self, symbol: str, stage: str) -> None:
        """Mark which stage THIS symbol's analysis is entering.

        _stage_add accumulates COMPLETED stages. A cancelled analysis never
        completes one, so the batch totals said where finished work went and
        nothing about where a hung symbol was stuck — the operator got
        "gave up on FET/USDT after 90s" (#998) with no way to tell an
        exchange fetch from an LLM call without reading logs.

        Measured, not inferred. The alternative — "last completed stage + 1"
        — guesses, and a guess on a diagnosis surface is what this codebase
        keeps removing. Fail-open: instrumentation must never break a tick.
        """
        try:
            inflight = getattr(self, "_stage_inflight", None)
            if inflight is None:
                inflight = self._stage_inflight = {}
            inflight[str(symbol)] = stage
            # Bounded: one entry per in-flight analysis, cleared on exit, but
            # a cancelled task's finally may not run before the next batch.
            if len(inflight) > 500:
                inflight.clear()
        except Exception:
            pass

    def _stage_exit(self, symbol: str) -> None:
        """Analysis finished (any outcome) — it is no longer in a stage."""
        try:
            inflight = getattr(self, "_stage_inflight", None)
            if inflight is not None:
                inflight.pop(str(symbol), None)
        except Exception:
            pass

    def _stage_add(self, stage: str, seconds: float) -> None:
        """Accumulate time spent in one stage of one analysis.

        Summed ACROSS the batch, not per signal. With bounded concurrency the
        sum exceeds wall-clock — twelve analyses each spending 15s in the LLM
        contribute 180s to a phase that ran for 231s. That is the point: the
        SHARE tells you which stage to attack, and wall-clock alone never
        could.

        Safe under asyncio without a lock: `+=` on a float completes between
        awaits, and the loop is single-threaded.

        Fail-open. Instrumentation that can break a tick is not worth having,
        which the phase recorder learned the hard way on the failure path.
        """
        try:
            acc = self._stage_totals
            if acc is not None and stage in acc:
                acc[stage] += float(seconds)
        except Exception:
            pass

    def _forecast_analyze_capacity(self, of: int) -> Optional[dict]:
        """Can `of` signals finish inside the analyze phase cap?

        Returns None when it cannot be known — no measured throughput yet, no
        phase cap configured, or a prior batch that completed zero analyses.
        Omit, never invent: a forecast built from a guessed rate would be a
        fabricated number on an operator surface, and the whole reason this
        exists is that guessed causes shipped two wrong fixes.

        The rate is effective wall-clock throughput (done / elapsed) from the
        previous batch, so it already carries the concurrency in force. That
        matters: dividing per-analysis latency by hand ignores the 12-way
        concurrency and lands ~12x off, which is how a serial-chain theory got
        as far as it did.
        """
        # The cap that kills the batch is the PHASE cap, not
        # analysis_timeout_sec (which bounds one analysis). Getting those two
        # confused is what made "exceeded its 300s" read as a per-symbol
        # limit. Read the same value _phase() enforces, from the same place.
        cap = float(getattr(CONFIG.monitoring, "tick_phase_timeout_sec", 0.0) or 0.0)
        tp = getattr(self, "_analyze_throughput", None)
        if cap <= 0 or of <= 0 or not isinstance(tp, dict):
            return None
        per = float(tp.get("per_signal_s") or 0.0)
        if per <= 0:
            return None
        needed = of * per
        fits = int(cap / per)
        rec = {
            "of": of,
            "per_signal_s": round(per, 2),
            "needed_s": round(needed, 1),
            "cap_s": round(cap, 1),
            "fits": fits,
            # How many will simply never be looked at this tick. Reported as a
            # count, because "57%" was the number that got read as progress
            # rather than as a shortfall.
            "shortfall": max(0, of - fits),
            "measured_from": int(tp.get("done") or 0),
        }
        if rec["shortfall"] > 0:
            audit(scan_log,
                  f"Analyze budget short: {of} signals at "
                  f"{per:.1f}s/signal needs ~{needed:.0f}s against a "
                  f"{cap:.0f}s cap — about {fits} will fit and "
                  f"{rec['shortfall']} will not be analysed this tick. "
                  f"Lower TOP_MOVERS_COUNT or raise "
                  f"SCAN_ANALYSIS_CONCURRENCY.",
                  action="analyze_capacity", result="SHORT", data=rec)
        return rec

    def _record_analyze_throughput(self, done: int, of: int, elapsed: float) -> None:
        """Remember effective throughput so the NEXT batch can forecast.

        Recorded from timed-out batches too: a batch that got through 90 of
        161 measured a real rate for those 90. Discarding it because the phase
        died would throw away the only measurement taken during the exact
        conditions worth forecasting.
        """
        if done <= 0 or elapsed <= 0:
            return
        self._analyze_throughput = {
            "per_signal_s": elapsed / done,
            "done": int(done),
            "of": int(of),
        }

    def _stage_report(self, wall_s: float) -> str:
        """One line: where the batch's work actually went.

        Empty when nothing was recorded — a breakdown of zero is not a
        breakdown, and printing 0s across the board would read as a
        measurement.
        """
        try:
            acc = dict(self._stage_totals or {})
            total = sum(acc.values())
            if total <= 0:
                return ""
            parts = " · ".join(
                f"{k} {acc[k]:.0f}s ({acc[k] / total:.0%})"
                for k in ANALYSIS_STAGES if acc.get(k, 0) > 0)
            _line = (f"{parts} — {total:.0f}s of work across {wall_s:.0f}s "
                     f"wall-clock at {int(CONFIG.scan_analysis_concurrency)} "
                     f"concurrent")
            # This exact line is where "fetch dominating, ~1086s cumulative,
            # 192s wall-clock" came from, and it is where the diagnosis
            # stopped: a percentage says fetch owns the tick and nothing says
            # whether that is 70 symbols at 15s or 3 at 300s. Appended only
            # when fetch actually dominates, so a healthy tick gains nothing.
            # Follow the DOMINANT stage, not a hardcoded one. Within a day
            # of the fetch-stall report the same engine had fetch at ~36-41%
            # and mtf at ~59-64% — a note bolted to `fetch` would already
            # have been describing the wrong stage.
            # getattr-guarded like every other mark on this path. Calling
            # self._stage_shape_note directly made _stage_report return ""
            # for every lightweight stub that has _stage_totals and nothing
            # else — the outer except swallowed the AttributeError and took
            # the WHOLE breakdown with it. Three tests caught it. This file's
            # own _stage_profile_record docstring warns about exactly this,
            # and it was written two hours earlier.
            _top = max(acc.items(), key=lambda kv: kv[1], default=("", 0.0))
            _shape = ""
            try:
                _note = getattr(self, "_stage_shape_note", None)
                if _note is not None and _top[1] >= total * 0.5:
                    _shape = _note(_top[0]) or ""
            except Exception:
                _shape = ""
            # Always appended, not only when a stage dominates: the whole
            # point is that a skipped timeframe is otherwise invisible, and
            # it is most worth seeing on the ticks that now look healthy
            # BECAUSE work is being skipped.
            _supp = ""
            try:
                _sn = getattr(self, "_mtf_suppression_note", None)
                if _sn is not None:
                    _supp = _sn() or ""
            except Exception:
                _supp = ""
            # Per-call fetch attribution. Appended ALWAYS, not only when
            # fetch dominates: the point is to say which of the five calls
            # owns the time, and that is worth knowing on the ticks where
            # fetch is NOT dominant too — that is how you see it change.
            _calls = ""
            try:
                _cn_fn = getattr(self, "_fetch_call_note", None)
                if _cn_fn is not None:
                    _calls = _cn_fn() or ""
            except Exception:
                _calls = ""
            return (_line + (f"; {_shape}" if _shape else "")
                    + (f"; {_calls}" if _calls else "")
                    + (f"; {_supp}" if _supp else ""))
        except Exception:
            return ""

    def _mtf_suppression_note(self) -> str:
        """How many timeframe(s) are currently not being requested, or "".

        mtf_availability.render exists and was TESTED, and nothing called it —
        so the suppression it reports was invisible, which is the precise
        defect its own docstring is written against ("suppression that nobody
        can see is the same defect as the silent fail-open it replaces").

        Third time this session a mechanism landed with no surface:
        live_auth_healthy in #1030, the halt in #1031, and this. Writing the
        renderer is not wiring it.

        One line, because _stage_report's callers embed it in a single-line
        audit string.
        """
        try:
            from bot.core import mtf_availability as _ma
            st = getattr(self, "_mtf_availability", None)
            if not st:
                return ""
            s = _ma.summarize(st)
            if not s:
                return ""
            _top = ", ".join(f"{sym} x{n}" for sym, n in s["top"][:3])
            return (f"mtf skipping {s['pairs']} timeframe(s) on "
                    f"{s['symbols']} symbol(s) after "
                    f"{_ma.CONSECUTIVE_FAILURES} consecutive failures each, "
                    f"re-probed within {int(_ma.SUPPRESS_SECONDS / 60)}min"
                    + (f": {_top}" if _top else ""))
        except Exception:
            return ""

    def _fetch_call_note(self) -> str:
        """Which of the five fetch calls actually owns the time.

        #1045 recorded per-call durations as pseudo-stages (fetch:book,
        fetch:trades, fetch:funding, fetch:oi) and NOTHING READ THEM.
        `_stage_report` picks the dominant stage from `_stage_totals`, which
        holds only the four real ANALYSIS_STAGES, so `_stage_shape_note` was
        never once called with a `fetch:` key. The data was collected every
        tick and printed nowhere.

        Fourth time this session a mechanism landed with no surface —
        live_auth_healthy, the halt, the mtf suppression, and this. The
        previous three were caught by asking "who reads it"; this one was
        caught by writing out the grep command for the operator and checking
        it could match.

            RECORDING IT IS NOT REPORTING IT. THE QUESTION IS ALWAYS WHO
            READS THE FIELD.

        One line, because _stage_report's callers embed it in a single-line
        audit string. Silent when no per-call data exists, so the lightweight
        path (which fires no order-flow calls) adds nothing.
        """
        try:
            from bot.core import stage_profile as _sp
            profs = getattr(self, "_stage_profiles", None) or {}
            parts = []
            for _name in ("book", "trades", "funding", "oi"):
                prof = profs.get(f"fetch:{_name}")
                if not prof:
                    continue
                s = _sp.summarize(prof)
                if not s:
                    continue
                _bit = f"{_name} mean {s['mean_s']}s/max {s['max_s']}s"
                if s.get("worst_symbol"):
                    _bit += f" ({s['worst_symbol']})"
                parts.append(_bit)
            if not parts:
                return ""
            return "fetch calls: " + " · ".join(parts)
        except Exception:
            return ""

    def _stage_shape_note(self, stage: str) -> str:
        """One compact line naming the SHAPE of this run's durations for STAGE.

        Deliberately per-RUN while the breakdown above is per-batch, and
        labelled as such: the question a single slow tick raises is whether it
        keeps happening, which one batch cannot answer. Same choice, and the
        same wording, as the analysis-timeout tally.

        Both call sites of _stage_report embed it in a ONE-LINE audit string,
        so this must never wrap. The full multi-line rendering lives in
        stage_profile.render for surfaces that have the room.

        The per-call ceiling is only meaningful for the stages that are
        EXCHANGE I/O. fetch and mtf are both _cached_ohlcv against the same
        rate-limited ccxt instance, so MARKET_DATA_TIMEOUT_MS bounds their
        calls; analyze is an LLM call bounded by something else entirely, and
        testing it against a market-data timeout would invent a finding.
        """
        try:
            from bot.core import stage_profile as _sp
            prof = (getattr(self, "_stage_profiles", None) or {}).get(stage)
            if not prof:
                return ""
            _ceil = 0.0
            if stage in ("fetch", "mtf"):
                _ceil = float(
                    getattr(CONFIG, "market_data_timeout_ms", 0) or 0) / 1000.0
            s = _sp.summarize(prof, ceiling_s=_ceil)
            if not s or not s.get("shape"):
                return ""
            head = (f"{stage} shape across this run: {s['shape']} "
                    f"({s['count']} sample(s), {s['distinct_symbols']} symbol(s), "
                    f"mean {s['mean_s']}s, max {s['max_s']}s")
            if s["shape"] == "at_ceiling":
                # NAME the worst symbol. This branch is the one that fires
                # under load, and it was the only shape that printed no
                # symbol at all — the operator read "max 74.94s" recurring
                # across three cycles, correctly inferred one symbol was
                # serially responsible, and had to ask which. The profile
                # knew the whole time.
                head += (f", {s['near_ceiling']}/{s['near_ceiling_of']} at or "
                         f"above {round(s['ceiling_s'] * _sp.CEILING_TOLERANCE, 1)}s "
                         f"of a {s['ceiling_s']}s per-call limit"
                         f"; slowest {s.get('worst_symbol')} at "
                         f"{s.get('worst_symbol_max_s')}s")
            elif s["shape"] == "slow_subset":
                head += (f", {s['near_ceiling']}/{s['near_ceiling_of']} near "
                         f"the {s['ceiling_s']}s limit: "
                         + ", ".join(s.get("subset", [])[:5]))
            elif s["shape"] == "concentrated" and s.get("top_symbols"):
                _sym, _tot, _n = s["top_symbols"][0]
                head += f", slowest {_sym} {_tot}s over {_n}"
            return head + ")"
        except Exception:
            return ""

    def _high_conviction_ceiling(self, user_id: str = "") -> Optional[float]:
        """The margin ceilings already binding before this rule runs.

        confirm_trade feeds MICRO_MAX_POSITION_USD and the per-user cap INTO
        the risk recheck, so recheck.position_size_usd already respects them.
        Overwriting that with a flat target would therefore RAISE past a cap
        that had already bound — and the per-user cap is only re-applied
        further down inside the manual-override branch, which the auto path
        never enters. Caught by the ordering test before this shipped.

        So the same ceilings are recomputed here and applied to the target.
        Tighten-only: None means nothing binds.
        """
        ceiling: Optional[float] = None
        try:
            if CONFIG.is_live():
                ceiling = float(CONFIG.execution.max_live_position_usd)
            cap = self._per_user_margin_cap(user_id)
            if cap is not None:
                ceiling = float(cap) if ceiling is None else min(ceiling, float(cap))
        except Exception as exc:
            logger.debug("High-conviction ceiling unresolved: %s", exc)
        return ceiling

    def _high_conviction_margin(self, idea, size_usd: float,
                                user_id: str = "") -> float:
        """Flat margin for an idea at or above the confidence floor.

        Normal sizing is fixed-fractional — risk_budget / stop_distance_pct —
        so a tight stop buys a bigger position and a wide one buys a smaller.
        The operator directive is different: at or above the floor, every
        trade gets the SAME margin regardless of stop distance.

        Returns the target margin, or `size_usd` unchanged when the rule is
        off or the idea does not clear the floor. It is deliberately only a
        TARGET: every reducer downstream still runs — stock-session
        multiplier, exchange free-balance clamp, per-user ceiling,
        MICRO_MAX_POSITION_USD, total-exposure and open-position caps, and the
        drawdown breaker. This can raise a size the risk engine chose; it can
        never raise one past a limit that already bound it.

        Leverage is NOT decided here. It stays with _compute_target_leverage
        (DEFAULT_LEVERAGE, the /leverage override, and volatility
        de-leveraging, which only ever reduces), so there is still one source
        of truth for it and this cannot silently double a notional.

        Fail-safe: any error leaves the risk engine's number alone.
        """
        try:
            cfg = CONFIG.execution
            if not getattr(cfg, "high_conviction_enabled", False):
                return size_usd
            conf = float(getattr(idea, "confidence", 0.0) or 0.0)
            floor = float(cfg.high_conviction_min_confidence)
            if conf < floor:
                return size_usd
            target = float(cfg.high_conviction_margin_usd)
            # Never above a ceiling that was already binding.
            _ceiling = self._high_conviction_ceiling(user_id)
            if _ceiling is not None and target > _ceiling:
                audit(trade_log,
                      f"High-conviction target ${target:.2f} capped to "
                      f"${_ceiling:.2f} (existing margin ceiling)",
                      action="high_conviction_cap", result="CAPPED",
                      data={"target": round(target, 2),
                            "ceiling": round(_ceiling, 2)})
                target = _ceiling
            if target == size_usd:
                return size_usd
            audit(trade_log,
                  f"High-conviction sizing: {getattr(idea, 'asset', '?')} "
                  f"confidence {conf:.0%} >= {floor:.0%} — margin "
                  f"${size_usd:.2f} -> ${target:.2f} (every cap below still "
                  f"applies)",
                  action="high_conviction_size",
                  result="RAISED" if target > size_usd else "REDUCED",
                  data={"asset": getattr(idea, "asset", None),
                        "confidence": round(conf, 4),
                        "floor": floor,
                        "from": round(size_usd, 2),
                        "to": round(target, 2)})
            return target
        except Exception as exc:      # never let sizing policy break a trade
            logger.debug("High-conviction sizing skipped: %s", exc)
            return size_usd

    async def _phase(self, coro, what: str, fatal: bool = True):
        """Bound ONE phase of a tick, naming it if it hangs.

        The whole-tick cap (_tick_guarded) recovers the loop but is silent
        about where: a real 900s hang reported only the outermost await
        ("engine.py:2226 in run"). A per-phase cap fires FIRST, so the audit
        line names the phase — positions / scan / analyze — and the tick
        fails in a third of the time.

        A timeout is re-raised so the existing tick-failure machinery
        (backoff, degraded alerts, the stall counters) handles it exactly as
        it handles any other tick error. This is deliberately NOT the quiet
        treatment `_with_maintenance_cap` gives optional post-tick work: a
        hung phase is a real outage, not a skippable extra. 0 disables.

        `fatal=False` returns None instead of raising, for a phase whose
        failure should not cost the REST of the tick — scanning, whose job is
        finding new entries, versus position monitoring, whose job is
        protecting money already at risk. It is never quiet: the timeout is
        still recorded and still named on every operator surface. Degraded is
        not the same as hidden.
        """
        cap = float(getattr(CONFIG.monitoring, "tick_phase_timeout_sec", 0.0) or 0.0)
        if cap <= 0:
            return await coro
        _started = time.monotonic()
        try:
            _r = await asyncio.wait_for(coro, timeout=cap)
            self._record_phase_duration(what, time.monotonic() - _started, cap)
            return _r
        except asyncio.TimeoutError:
            # How far did it get? "Exceeded its 300s" says the phase died; it
            # does not say whether 4 of 200 symbols finished or 194 did, and
            # those are opposite diagnoses. A cancelled phase that cannot
            # account for itself forces the next step to be a guess — which
            # is how two fixes shipped against a cause that was never
            # confirmed.
            _prog = getattr(self, "_analyze_progress", None) if what == "analyze" else None
            _detail = ""
            _pdata: dict = {"phase": what}
            if isinstance(_prog, dict) and _prog.get("of"):
                _done, _of = int(_prog.get("done") or 0), int(_prog["of"])
                _elapsed = time.monotonic() - float(_prog.get("started") or 0.0)
                _rate = (_done / _elapsed) if _elapsed > 0 else 0.0
                _detail = (f" It had finished {_done} of {_of} signals "
                           f"({_done / _of:.0%}) in {_elapsed:.0f}s"
                           + (f" — {_of / _rate:.0f}s needed at that rate."
                              if _rate > 0 else "."))
                _pdata.update(done=_done, of=_of, elapsed_s=round(_elapsed, 1),
                              needed_s=round(_of / _rate, 1) if _rate > 0 else None)
                # A cancelled batch measured a real rate for the analyses it
                # DID finish, under exactly the conditions worth forecasting.
                # Discarding it would leave the next tick guessing again.
                try:
                    _rt = getattr(self, "_record_analyze_throughput", None)
                    if _rt is not None:
                        _rt(_done, _of, _elapsed)
                except Exception:
                    pass
                # A cancelled batch never reaches its own report, and it is
                # the batch whose breakdown anyone actually wants.
                _stages = self._stage_report(_elapsed)
                if _stages:
                    _detail += f" Work: {_stages}."
                    _pdata["stages"] = {k: round(v, 1) for k, v in
                                        (self._stage_totals or {}).items()}
            audit(system_log,
                  f"Tick phase '{what}' exceeded its {cap:.0f}s cap — cancelled "
                  f"the parked await. This NAMES the hang the whole-tick cap "
                  f"could only report as 'in run'." + _detail,
                  action="tick_phase", result="TIMEOUT", data=_pdata)
            # Remember it. The audit line above lands in a log the operator
            # cannot easily reach, and for one live incident the alerts said
            # only "the main loop has failed 3 times in a row" — the cause was
            # already known to the process and simply never travelled to the
            # surfaces anyone reads. A diagnosis that does not reach the
            # operator is not a diagnosis.
            # The cap is a FLOOR on how long it ran, not a measurement — but
            # a floor of 100% is the entry the headroom line most needs.
            #
            # Guarded because this is the FAILURE path. A phase timeout is a
            # recoverable event the tick machinery knows how to handle; an
            # error raised by a recorder here would replace it with something
            # that machinery has never seen. Instrumentation may not change
            # what happens when things go wrong — that is when it matters most
            # that behaviour is the behaviour that was tested.
            _rec = getattr(self, "_record_phase_duration", None)
            if _rec is not None:
                try:
                    _rec(what, cap, cap, timed_out=True)
                except Exception:
                    pass
            self._last_phase_timeout = {
                "phase": what,
                "cap_s": cap,
                "progress": (dict(_prog) if isinstance(_prog, dict) else None),
                "at": time.monotonic(),
                "count": int((getattr(self, "_last_phase_timeout", None) or {})
                             .get("count", 0)) + 1
                if (getattr(self, "_last_phase_timeout", None) or {}).get("phase") == what
                else 1,
            }
            if not fatal:
                return None
            raise

    def _record_phase_duration(self, what: str, elapsed: float, cap: float,
                               timed_out: bool = False) -> None:
        """Remember how long a phase took — including when it DIED.

        Recording only successes was a hole big enough to hide the very thing
        this instrument exists for. A phase that always times out never
        entered the record, so phase_headroom() reported the slowest of the
        SURVIVING phases. Production, 2026-07-29 10:22 UTC:

            - Tick phase timed out: analyze (exceeded its 300s, x5)
            - Slowest tick phase: scan 2s peak of 300s (1%)

        Read alone — which is how a one-line summary gets read — the second
        line says the loop has 99% of its budget spare while the first says
        it is dying. A phase at 100% of cap is the single most important
        data point here and it was the one being dropped.

        A timeout records `cap` as the duration. That is a FLOOR, not a
        measurement: the phase used at least that long and was cancelled, so
        the true figure is unknowable. `timed_out` carries that distinction
        so nothing downstream presents a floor as a reading.

        _phase used to record only the breach. That made every phase a cliff:
        indistinguishable at 299s of a 300s cap from 30s, then a dead tick at
        301s. The 2026-07-29 incident lived in that blind spot — the analyze
        phase had presumably been creeping toward its cap for some time, and
        the first signal anyone got was thirty-seven consecutive failures and
        a breaker rejecting live trades.

        It is also the only way to VERIFY a latency fix. "The breaches
        stopped" is consistent with the fix working and with the load simply
        being lighter today; peak-vs-cap distinguishes them.

        Peak, not just last: a mean hides the tail, and the tail is what
        trips a cap. Bounded by the number of phase names (three), so this
        cannot grow.
        """
        try:
            prev = self._phase_durations.get(what) or {}
            self._phase_durations[what] = {
                "last_s": float(elapsed),
                "peak_s": max(float(elapsed), float(prev.get("peak_s") or 0.0)),
                "cap_s": float(cap),
                "at": time.monotonic(),
                # Sticky: one timeout in the window is what an operator needs
                # to see, and a later success does not un-happen it.
                "timed_out": bool(timed_out) or bool(prev.get("timed_out")),
            }
        except Exception:  # instrumentation must never break a tick
            pass

    def phase_headroom(self) -> Optional[dict]:
        """The phase running CLOSEST to its cap, or None if none has run.

        Reports the tightest margin rather than every phase: the operator
        needs to know whether anything is near the edge, and one number they
        will actually read beats three they will not.
        """
        worst = None
        for name, rec in (self._phase_durations or {}).items():
            cap = float(rec.get("cap_s") or 0.0)
            peak = float(rec.get("peak_s") or 0.0)
            if cap <= 0:
                continue
            used = peak / cap
            if worst is None or used > worst["used_ratio"]:
                worst = {"phase": name, "peak_s": peak, "cap_s": cap,
                         "last_s": float(rec.get("last_s") or 0.0),
                         "used_ratio": used,
                         "timed_out": bool(rec.get("timed_out"))}
        return worst

    async def _with_maintenance_cap(self, coro, what: str):
        """Bound a post-tick maintenance await. Each is throttled and
        fail-open by design, so a hung one is cancelled QUIETLY (warning log)
        and the loop simply moves on — unlike the tick itself, whose timeout
        must count as a failure. 0 disables the cap."""
        cap = float(getattr(CONFIG.monitoring,
                            "tick_maintenance_timeout_sec", 0.0) or 0.0)
        if cap <= 0:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=cap)
        except asyncio.TimeoutError:
            system_log.warning(
                "Post-tick maintenance %r exceeded %.0fs — cancelled; "
                "the loop continues", what, cap)
            return None

    async def stop(self) -> None:
        self._running = False
        await self.ws_feed.stop()
        await self.scanner.close()
        # The pusher owns an aiohttp session and a background task, and its
        # `stop()` was written and then called from nowhere — so on any boot
        # where it HAD been enabled, both leaked at shutdown. It never bit
        # because nothing deploys its consumer, which is not a reason to leave
        # it: the day someone sets DASHBOARD_API_KEY is the day it starts.
        if getattr(self, "dashboard_pusher", None) is not None:
            try:
                await self.dashboard_pusher.stop()
            except Exception as exc:
                logger.debug("Dashboard pusher stop failed: %s", exc)
        # AUDIT-FIX: Close live executor exchange connection to avoid session leaks
        if hasattr(self, 'live_executor') and self.live_executor:
            await self.live_executor.close()
        # Close any per-user executors' exchange connections too.
        for _ex in list(getattr(self, "_user_executors", {}).values()):
            try:
                await _ex.close()
            except Exception as _close_exc:
                logger.debug("Per-user executor close failed: %s", _close_exc)
        self._transition(AgentState.IDLE, "engine stopped")
        audit(system_log, "Engine stopped", action="stop")

    # -- Pipeline stages --

    async def _maybe_publish_proofofpnl(self) -> None:
        """Cadenced, fail-open Proof-of-PnL publication.

        When enabled (``PROOFOFPNL_PUBLISH_ENABLED``) and the publisher's cadence
        is due, fetch the operator's REAL fills, assemble a public-safe verifiable
        statement, seal it, and persist it as the latest publication — so the
        public ``/proof`` feed and the MCP ``get_proof_of_pnl`` tool serve live
        data instead of an empty store. DEFAULT-OFF. Runs only on the LIVE
        operator path (the only place real, re-derivable fills exist). Balances
        are omitted here, so the epoch reconciles honestly to INCOMPLETE;
        signed-snapshot anchoring is a later slice. Never touches trading and
        never raises past this method."""
        from bot.proofofpnl.scheduler import get_operator_publisher
        publisher = get_operator_publisher()
        if publisher is None:
            return
        now_ts = int(time.time())
        if not publisher.should_publish(now_ts):
            return
        # Proof-of-PnL is about REAL, re-derivable fills — only the live operator
        # account has those. Paper mode has nothing verifiable to publish.
        if not (CONFIG.is_live() and getattr(self, "live_executor", None)):
            return
        try:
            lookback_days = int(os.environ.get("PROOFOFPNL_LOOKBACK_DAYS", "") or 30)
        except (TypeError, ValueError):
            lookback_days = 30
        since_ms = int((now_ts - max(1, lookback_days) * 86400) * 1000)
        try:
            exchange = await self.live_executor._get_exchange()
            trades = await exchange.fetch_my_trades(symbol=None, since=since_ms, limit=200)
        except Exception as exc:
            system_log.debug("Proof-of-PnL fills fetch skipped: %s", exc)
            return
        # publish() is fail-safe internally; envelope omitted for now (optional).
        pub = publisher.publish(
            now_ts, trades or [],
            range_start=int(since_ms / 1000), range_end=now_ts)
        # Public verifiable leaderboard: if the operator has OPTED IN by setting
        # an anonymous handle, register the freshly-sealed statement so it appears
        # on the anonymous, re-verifiable board. Default OFF (no handle => no
        # registration); fail-open; never touches trading.
        if pub is not None:
            handle = str(os.environ.get("PROOFOFPNL_LEADERBOARD_HANDLE", "")).strip()
            if handle:
                try:
                    from bot.proofofpnl.leaderboard import get_leaderboard_registry
                    get_leaderboard_registry().put(handle, pub)
                except Exception as exc:
                    system_log.debug("Leaderboard register skipped: %s", exc)

    async def _maybe_publish_user_leaderboards(self) -> None:
        """Publish each OPTED-IN user's own verifiable statement to the public
        leaderboard under their anonymous handle, and remove handles whose
        owners opted out.

        Consent + privacy invariants (do not weaken):
        - OPT-IN ONLY: a user appears solely because their handle is present in
          the website's desired-state set (users.leaderboard_handle).
        - REVOCABLE: opt-out (handle cleared) reconcile-removes their row on
          the next pull. Only handles THIS loop published are ever removed —
          the operator's own PROOFOFPNL_LEADERBOARD_HANDLE row is untouchable.
        - UNLINKABLE: the sealed statement's ``account_ids`` is the HANDLE,
          never a telegram id / web user id / email — those would be embedded
          verbatim in the public statement (statement.py) and the on-disk
          registry bundle, and is_public_safe does not scan account_ids.
        - ISOLATED: fills come only from an ALREADY-LIVE per-user executor
          (self._user_executors). Never from _executor_for (which falls back
          to the shared operator executor for key-less users) — the operator's
          fills must never publish under a user's handle.

        Triple-gated default-OFF: PER_USER_LIVE_ENABLED and
        PROOFOFPNL_PUBLISH_ENABLED and PROOFOFPNL_USER_LEADERBOARD_ENABLED.
        Throttled, cadenced per user, fail-open; never touches trading.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return
        from bot.proofofpnl.scheduler import (ProofOfPnLPublisher,
                                              feature_enabled)
        if not feature_enabled():
            return
        if str(os.environ.get("PROOFOFPNL_USER_LEADERBOARD_ENABLED", "")
               ).strip().lower() not in ("1", "true", "yes", "on"):
            return
        now_ts = time.time()
        if now_ts - getattr(self, "_last_user_lb_pull_ts", 0.0) < 300.0:
            return
        self._last_user_lb_pull_ts = now_ts
        from bot.utils.leaderboard_pull import fetch_leaderboard_optins
        optins = await asyncio.to_thread(fetch_leaderboard_optins)
        if optins is None:
            return                      # transport failure: leave the board alone
        from bot.proofofpnl.leaderboard import get_leaderboard_registry
        from bot.proofofpnl.publish import PublicationStore
        registry = get_leaderboard_registry()
        try:
            lookback_days = int(os.environ.get("PROOFOFPNL_LOOKBACK_DAYS", "") or 30)
        except (TypeError, ValueError):
            lookback_days = 30
        since_ms = int((now_ts - max(1, lookback_days) * 86400) * 1000)
        desired: dict[str, str] = {}
        for row in optins:
            tg = str(row.get("telegram_id") or "").strip()
            handle = str(row.get("handle") or "").strip()
            if not tg or not handle:
                continue
            ex = self._user_executors.get(tg)
            # ISOLATION GUARD (mirror of the web-flatten guard): only a
            # dedicated per-user executor may be published. The operator
            # account never publishes under a user handle.
            if ex is None or ex is self.live_executor or self._is_operator_user(tg):
                continue
            desired[tg] = handle
            publishers = getattr(self, "_user_lb_publishers", None)
            if publishers is None:
                publishers = self._user_lb_publishers = {}
            pub_er = publishers.get(tg)
            if pub_er is None or pub_er._account_ids != [handle]:   # handle rename
                try:
                    from bot.core.exchange_credentials import get_credential_store
                    venue = getattr(get_credential_store(), "get_venue",
                                    lambda _u: "bitget")(tg) or "bitget"
                except Exception:
                    venue = "bitget"
                # Dedicated SCRATCH store: per-user publications must never
                # overwrite the operator's PublicationStore (the /proof feed).
                # The board itself is fed via registry.put; this file is inert.
                pub_er = ProofOfPnLPublisher(
                    account_ids=[handle], venue=str(venue),
                    store=PublicationStore("data/proofofpnl_user_scratch.json"))
                publishers[tg] = pub_er
            if not pub_er.due(int(now_ts)):
                continue
            try:
                exchange = await ex._get_exchange()
                trades = await exchange.fetch_my_trades(
                    symbol=None, since=since_ms, limit=200)
            except Exception as exc:
                system_log.debug("User leaderboard fills skipped for %s: %s", tg, exc)
                continue
            pub = pub_er.publish(int(now_ts), trades or [],
                                 range_start=int(since_ms / 1000),
                                 range_end=int(now_ts))
            if pub is not None:
                try:
                    registry.put(handle, pub)
                except Exception as exc:
                    system_log.debug("User leaderboard register skipped: %s", exc)
        # Reconcile opt-outs / renames: remove only handles THIS loop set for a
        # telegram id that dropped out or changed handle — never the operator's
        # row or a manually-registered handle.
        prev: dict[str, str] = getattr(self, "_user_board_handles", {})
        for tg, old_handle in list(prev.items()):
            if desired.get(tg) != old_handle:
                try:
                    registry.remove(old_handle)
                except Exception as exc:
                    system_log.debug("User leaderboard remove skipped: %s", exc)
        self._user_board_handles = desired

    def _maybe_snapshot_board_season(self) -> None:
        """Freeze the board's in-window sealed statements into the current
        season (bot/proofofpnl/seasons.py). Pure local-disk bookkeeping over
        already-verified registry entries — no network, no account data —
        so it is gated only on the publish feature being on. Throttled and
        fail-open; past seasons are immutable by construction."""
        from bot.proofofpnl.scheduler import feature_enabled
        if not feature_enabled():
            return
        now_ts = time.time()
        if now_ts - getattr(self, "_last_season_snapshot_ts", 0.0) < 600.0:
            return
        self._last_season_snapshot_ts = now_ts
        from bot.proofofpnl.leaderboard import get_leaderboard_registry
        from bot.proofofpnl.seasons import get_season_store
        get_season_store().record_current(
            get_leaderboard_registry().all_entries(), now_ts)

    @staticmethod
    def _is_monitor_stale(last_loop_ts: "float | None", now: float,
                          timeout: float) -> bool:
        """Pure staleness predicate for the proactive monitor loop. None =
        never ran yet (startup grace) — not stale; timeout <= 0 disables."""
        if last_loop_ts is None or timeout <= 0:
            return False
        return (now - last_loop_ts) > timeout

    async def _maybe_check_monitor_liveness(self) -> None:
        """Reciprocal watchdog: the proactive monitor delivers every internal
        safety alert, yet nothing watched IT — a dead monitor task silently
        ended all alerting while trading continued. Each successful tick now
        checks the monitor's heartbeat; on staleness it audits CRITICAL,
        notifies the operator through a monitor-independent callback, and
        restarts the (same) monitor object's task when it died. Throttled to
        the check interval so a permanently-dead monitor alerts once per
        window, and fail-open — a buggy liveness check must never hurt the
        tick loop it runs in."""
        monitor = getattr(self, "_proactive_monitor", None)
        if monitor is None:
            return
        try:
            timeout = float(getattr(CONFIG.monitoring,
                                    "monitor_liveness_timeout_sec", 300.0))
            if timeout <= 0:
                return
            now = time.monotonic()
            last_check = self._last_monitor_liveness_check
            if last_check is not None and now - last_check < timeout:
                return
            if not self._is_monitor_stale(
                    getattr(monitor, "last_loop_ts", None), now, timeout):
                return
            self._last_monitor_liveness_check = now
            age = now - monitor.last_loop_ts
            audit(system_log,
                  f"Proactive monitor loop STALLED: last ran {age:.0f}s ago — "
                  f"internal alerting is DOWN",
                  action="monitor_liveness", result="CRITICAL",
                  data={"age_s": round(age, 1)})
            cb = getattr(self, "_monitor_stale_callback", None)
            if cb is not None:
                try:
                    await cb(age)
                except Exception as exc:
                    system_log.debug("Monitor-stale callback failed: %s", exc)
        except Exception as exc:
            system_log.debug("Monitor liveness check skipped: %s", exc)

    async def _maybe_ping_healthcheck(self) -> None:
        """Dead-man's-switch ping (ops tip #8). GETs HEALTHCHECK_PING_URL at
        most every HEALTHCHECK_PING_INTERVAL_SEC so an external monitor (e.g.
        healthchecks.io) alarms when the bot process dies or the tick loop
        stalls. Fail-open: a failed ping never affects trading; no-op when the
        URL is unset."""
        url = CONFIG.monitoring.healthcheck_ping_url
        if not url:
            return
        now = time.monotonic()
        if (now - getattr(self, "_last_healthcheck_ping", 0.0)
                < CONFIG.monitoring.healthcheck_ping_interval_sec):
            return
        self._last_healthcheck_ping = now
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
                async with s.get(url) as resp:
                    if resp.status >= 400:
                        system_log.debug("Healthcheck ping HTTP %s", resp.status)
        except Exception as exc:
            system_log.debug("Healthcheck ping failed (non-fatal): %s", exc)

    async def _maybe_pull_web_credentials(self) -> None:
        """Throttled, fail-open pull of website-queued exchange credentials.

        No-op unless the operator has configured WEB_CREDS_KEY (and the website
        sync secret). On a successful connect/disconnect it invalidates the
        affected per-user executor so the next trade rebuilds with the new keys.

        ASYNC ON PURPOSE. This was a plain `def` called straight from the tick
        coroutine, which is the same stall as an async method doing sync work
        and harder to see: nothing about the call site suggested it could block,
        and unlike its sibling `_maybe_flatten_web_requests` it was not even
        wrapped in `_with_maintenance_cap`.

        Being precise about what that cap does now, because overclaiming a
        protection is the defect this file keeps being audited for: with the
        pulls on a worker thread the cap can finally free the LOOP at the
        timeout, which it could never do while the call was inline. It does not
        kill the thread — a 15s urlopen still runs to completion in the pool.
        Freeing the loop is the whole point; interrupting the request was never
        on offer.
        """
        try:
            import time as _time
            now = _time.monotonic()
            last = getattr(self, "_last_cred_pull", 0.0)
            if now - last < 30.0:
                return
            self._last_cred_pull = now
            from bot.utils.credential_pull import pull_and_apply, is_configured
            if not is_configured():
                return
            # OFF THE LOOP. These pulls are synchronous `urlopen(timeout=15)`
            # underneath, and this coroutine shares its loop with SL/TP
            # monitoring, the WS feed and every Telegram handler. Called
            # directly, one slow website froze all of it: driven against a
            # server answering in 2s, a heartbeat wanting to tick every 10ms
            # ticked ZERO times and its longest gap was the full 2.01s. Sync
            # code never yields, so the tick's own `asyncio.wait_for` cap
            # cannot interrupt it either — the timeout that looks like the
            # backstop is not one.
            #
            # This method chains three such pulls and each makes two requests,
            # so the worst case was six × 15s. `telegram_handler.py` already
            # offloads every one of these; the engine's own pumps were the
            # outlier.
            n = await asyncio.to_thread(
                pull_and_apply, on_change=self.invalidate_user_executor)
            if n:
                audit(system_log, f"Applied {n} web credential request(s)",
                      action="web_credentials_pull", result="OK")
            # Web wallet (3a): also pull live-control changes (live on/off, margin
            # cap, pause) and apply them via the UserStore. The allowlist is
            # reported to the UI but still enforced by the live gate — the web
            # cannot grant live access the operator hasn't pre-approved.
            store = getattr(self, "_user_store", None)
            if store is not None:
                from bot.utils.control_pull import pull_and_apply_controls
                # The callbacks run on the worker thread: `UserStore` guards
                # its writes with a `threading.Lock` and both callbacks only
                # pop from plain dicts, so this is safe to offload whole.
                m = await asyncio.to_thread(
                    pull_and_apply_controls,
                    store=store, allowlist_check=self._is_operator_user,
                    on_change=self.invalidate_user_executor)
                if m:
                    audit(system_log, f"Applied {m} web control change(s)",
                          action="web_controls_pull", result="OK")
                # Web parity: admin-queued stance change (global strategy
                # mode). control_pull re-verifies the requester's tier is
                # 'admin' against the bot's own UserStore before applying.
                from bot.utils.control_pull import pull_and_apply_stance
                if await asyncio.to_thread(pull_and_apply_stance, store=store):
                    audit(system_log, "Applied web stance change",
                          action="web_stance_pull", result="OK")
        except Exception as exc:
            try:
                audit(system_log, f"Web credential pull error: {exc}",
                      action="web_credentials_pull", result="ERROR")
            except Exception:
                pass

    async def _maybe_flatten_web_requests(self) -> None:
        """Process website emergency-stop flatten requests: close each requesting
        user's live positions via THEIR OWN executor, then ack.

        Isolation guard (same as the position-UI fix): a request is only honoured
        against the caller's own per-user executor. If ``_executor_for`` would fall
        back to the shared operator executor for a non-operator user, we do NOT
        flatten (there is nothing of theirs to close) — a web request can never
        close the operator's or another user's positions. Throttled, fail-open.
        """
        try:
            import time as _time
            now = _time.monotonic()
            if now - getattr(self, "_last_flatten_pull", 0.0) < 20.0:
                return
            self._last_flatten_pull = now
            from bot.utils.control_pull import fetch_flatten_pending, ack_flatten
            # Off the loop — see _maybe_pull_web_credentials. Worth naming here
            # specifically: this pump exists to CLOSE positions in an
            # emergency, and blocking the loop to ask about it is the one
            # place a stall costs the most.
            rows = await asyncio.to_thread(fetch_flatten_pending)
            if not rows:
                return
            per_user = getattr(CONFIG, "per_user_live_enabled", False)
            acks = []
            for r in rows:
                uid = r.get("user_id")
                tg = str(r.get("telegram_id") or "")
                if uid is None or not tg:
                    continue
                try:
                    ex = self._executor_for(tg)
                    # Never flatten the shared operator account for a non-operator.
                    if per_user and ex is self.live_executor and not self._is_operator_user(tg):
                        acks.append({"user_id": uid, "ok": True, "closed": 0})
                        continue
                    closed = await ex.close_all_positions(reason="web_emergency_stop")
                    audit(system_log,
                          f"Web emergency-stop flatten for user {tg}: {len(closed)} closed",
                          action="web_flatten", result="OK",
                          data={"user": tg, "closed": len(closed)})
                    acks.append({"user_id": uid, "ok": True, "closed": len(closed)})
                except Exception as exc:
                    # Leave the row un-acked (retry next poll) on a close failure.
                    audit(system_log, f"Web flatten failed for user {tg}: {exc}",
                          action="web_flatten", result="ERROR")
            if acks:
                await asyncio.to_thread(ack_flatten, acks)
        except Exception:
            pass

    async def _tick(self) -> None:
        """One full scan-analyze cycle."""
        self._last_tick_started_ts = time.monotonic()
        # ── Watchdog: force-recover if stuck in a non-IDLE state for >2 minutes ──
        # H-09 FIX: Don't interrupt active trade execution — use longer timeout
        if self.state != AgentState.IDLE and time.time() - self._last_state_change > 120:
            if self.state == AgentState.EXECUTING:
                # Allow up to 300s for active trade execution before forcing IDLE
                if time.time() - self._last_state_change <= 300:
                    pass  # Don't interrupt active trade execution
                else:
                    logger.warning(
                        "State timeout watchdog: stuck in %s for >300s, forcing IDLE",
                        self.state.value,
                    )
                    self._transition(AgentState.IDLE, "state timeout watchdog (executing)")
            else:
                logger.warning(
                    "State timeout watchdog: stuck in %s for >120s, forcing IDLE",
                    self.state.value,
                )
                self._transition(AgentState.IDLE, "state timeout watchdog")

        # Refresh live balance cache if in live mode
        if CONFIG.is_live():
            try:
                # Its own intent is "non-fatal: use cached value" — but an
                # except clause cannot catch a HANG, so the quiet cap is what
                # actually delivers that intent.
                await self._with_maintenance_cap(
                    self.get_live_equity(), "live equity refresh")
            except Exception:
                pass  # non-fatal: use cached value

        # Sync WebSocket status to health monitor
        self.health.set_ws_status(self.ws_feed.is_connected())
        # Sync WS heartbeat to live executor so degradation check stays current
        if self.ws_feed.is_connected() and hasattr(self, 'live_executor') and self.live_executor:
            self.live_executor.record_ws_heartbeat()

        # Check circuit breaker — no new scans, but still monitor open positions
        # so SL/TP can fire even while halted (Fix 2: monitoring while halted).
        if self.risk.circuit_breaker_active:
            if self.state != AgentState.HALTED:
                self._transition(AgentState.HALTED, "circuit breaker active")
            await self._phase(self._check_open_positions(), "positions (halted)")
            return
        elif self.state == AgentState.HALTED:
            self._transition(AgentState.IDLE, "circuit breaker cleared")

        # Check cooldown
        if self._cooldown_until and time.monotonic() < self._cooldown_until:
            if self.state != AgentState.COOLING_DOWN:
                self._transition(AgentState.COOLING_DOWN, "post-loss cooldown active")
            # C2-25 FIX: Still monitor open positions during cooldown — they need
            # SL/TP protection even when new scanning is paused.
            await self._phase(self._check_open_positions(), "positions (cooldown)")
            return
        elif self._cooldown_until and time.monotonic() >= self._cooldown_until:
            self._cooldown_until = 0.0

        # TTL: expire stale pending ideas
        now = datetime.now(UTC)
        idea_ttl = CONFIG.pending_idea_ttl
        expired_ids = [
            idea_id
            for idea_id, idea in self._pending_ideas.items()
            if (now - idea.timestamp).total_seconds() > idea_ttl
        ]
        for idea_id in expired_ids:
            expired_idea = self._pending_ideas.pop(idea_id, None)
            self._pending_atr.pop(idea_id, None)  # clean up stored ATR
            self._pending_pyramid.pop(idea_id, None)  # L-02 FIX: clean up pyramid flag
            if expired_idea:
                audit(
                    trade_log,
                    f"Trade idea {idea_id} expired (TTL)",
                    action="ttl_expire",
                    result="EXPIRED",
                    data={"asset": expired_idea.asset, "age_seconds": (now - expired_idea.timestamp).total_seconds()},
                )

        # C2-26 FIX: Skip scanning when ideas are awaiting confirmation.
        # A concurrent confirm_trade call while mid-scan creates a race on
        # shared _pending_ideas state.
        if self._pending_ideas:
            system_log.debug(
                "Skipping scan tick — %d ideas awaiting confirmation",
                len(self._pending_ideas),
            )
            self._transition(AgentState.MONITORING, "checking positions (scan skipped, pending confirms)")
            await self._phase(self._check_open_positions(), "positions (pending confirms)")
            self._transition(AgentState.IDLE, "tick cycle complete (scan skipped)")
            return

        # Don't scan while a Telegram-triggered force_scan holds the scan lock —
        # both mutate _pending_ideas and run auto-confirm. Monitor positions (SL/TP
        # protection unaffected) and bail; the in-flight force_scan produces this
        # cycle's ideas. Same-symbol double orders are separately impossible via
        # the per-symbol entry locks in confirm_trade.
        if self._scan_lock.locked():
            self._transition(AgentState.MONITORING, "checking positions (force_scan in progress)")
            await self._phase(self._check_open_positions(), "positions (scan in progress)")
            self._transition(AgentState.IDLE, "tick cycle complete (scan in progress)")
            return

        self._transition(AgentState.SCANNING, "beginning scan cycle")
        # Non-fatal by default: a slow exchange must not take the tick loop
        # down with it. Positions were checked earlier in this same tick, so a
        # skipped scan costs only the chance of a new entry. The timeout is
        # recorded and surfaced — see CONFIG.monitoring.tick_scan_timeout_fatal.
        signals = await self._phase(
            self.scanner.scan(), "scan",
            fatal=bool(getattr(CONFIG.monitoring, "tick_scan_timeout_fatal", False)))
        # Cache scan results for the proactive monitor (Move 2)
        self._last_scan_signals = signals or []

        # ── Structured scan logging ──
        scan_summary = {
            "cycle_ts": datetime.now(UTC).isoformat(),
            "pairs_scanned": len(self._last_scan_signals),
            "signals_found": len(signals) if signals else 0,
            "top_signals": [
                {
                    "symbol": s.symbol,
                    "price": s.price,
                    "change_24h": round(s.change_pct_24h, 2),
                    "volume_usd": round(s.volume_usd_24h, 0),
                    "volume_spike": s.volume_spike,
                    "momentum": round(s.momentum_score, 3),
                }
                for s in (signals or [])[:5]
            ],
        }
        audit(scan_log, f"Scan cycle: {scan_summary['signals_found']} signals from market",
              action="scan_cycle", result="OK" if signals else "NO_SIGNALS",
              data=scan_summary)

        # Public mind-stream: one compact "the agent just scanned" event per
        # cycle (bounded queue, background flush — see bot/core/agent_feed).
        try:
            from bot.core.agent_feed import FEED
            _feed_top = ", ".join(
                s["symbol"] for s in scan_summary["top_signals"][:3])
            FEED.emit(
                "scan",
                f"Scan complete — {scan_summary['pairs_scanned']} pairs, "
                f"{scan_summary['signals_found']} candidate(s)",
                body=f"Strongest momentum: {_feed_top}" if _feed_top else "",
                data={"pairs": scan_summary["pairs_scanned"],
                      "candidates": scan_summary["signals_found"]})
        except Exception as _feed_exc:
            logger.debug("Agent feed scan event skipped: %s", _feed_exc)

        # Push a fresh regime/circuit-breaker/key-call summary every autonomous
        # cycle. Before this, those dashboard panels only ever refreshed from a
        # manual Telegram /scan (or DeepScanSkill/PlaybookSkill query) -- while
        # trade/signal sync now update automatically (see _on_live_position_closed
        # and _build_signal_sync_payloads), this summary could go stale for
        # hours between manual scans, showing the dashboard as "disconnected"
        # even while the bot was healthy and trading normally.
        try:
            self._push_scan_summary_to_website(signals)
        except Exception as _scan_push_exc:
            logger.debug("Autonomous scan summary push skipped: %s", _scan_push_exc)

        if not signals:
            self._transition(AgentState.IDLE, "no signals found")
            return

        self._transition(AgentState.ANALYZING, "signals detected")

        # Analyze scanner-selected signals with BOUNDED concurrency. The scanner
        # now emits a wide (~200) volume-filtered universe, so an unbounded
        # gather would fan out hundreds of simultaneous OHLCV/order-flow/MTF
        # fetches and hammer the exchange rate limiter. The semaphore caps
        # in-flight analyses at CONFIG.scan_analysis_concurrency.
        results = await self._phase(
            self._analyze_signals_batched(signals), "analyze")
        _synced_ideas = []
        for idea in results:
            if idea:
                # Filter: don't present ideas below min_confidence threshold
                # Prevents user frustration of confirming a trade that gets rejected
                if idea.confidence < CONFIG.risk.min_confidence:
                    audit(scan_log,
                          f"Filtered sub-threshold idea: {idea.asset} conf={idea.confidence:.2f} < {CONFIG.risk.min_confidence}",
                          action="filter_idea", result="BELOW_MIN_CONFIDENCE",
                          data={"asset": idea.asset, "confidence": idea.confidence,
                                "threshold": CONFIG.risk.min_confidence})
                    continue
                # Dedup: if an idea for the same asset already exists, replace it
                existing_id = None
                idea_key = normalize_symbol(idea.asset)
                for eid, eidea in list(self._pending_ideas.items()):
                    if normalize_symbol(eidea.asset) == idea_key:
                        existing_id = eid
                        break
                if existing_id:
                    self._pending_ideas.pop(existing_id)
                    self._pending_atr.pop(existing_id, None)
                    self._pending_pyramid.pop(existing_id, None)  # C2-31 FIX: clean stale pyramid flag
                self._pending_ideas[idea.id] = idea
                _synced_ideas.append(idea)

        # Push these real, engine-generated signals to the website's signal
        # stream. Previously the dashboard's "Signals" feed only updated from
        # manual Telegram /scan (a different, simpler scanner) — it never saw
        # what the autonomous engine actually generates and trades on.
        if _synced_ideas:
            try:
                from bot.utils.website_sync import sync_signals_in_background
                sync_signals_in_background(
                    _build_signal_sync_payloads(_synced_ideas, self._outcome_regime))
            except Exception as _sig_sync_exc:
                logger.debug("Signal stream sync skipped: %s", _sig_sync_exc)
            # Public mind-stream: the thesis behind each fresh idea (capped —
            # a wide cycle shouldn't flood the public feed).
            try:
                from bot.core.agent_feed import FEED
                for _fi in _synced_ideas[:5]:
                    _fdir = str(getattr(_fi.direction, "value", _fi.direction))
                    FEED.emit(
                        "thesis",
                        f"{_fdir} {_fi.asset} — confidence {_fi.confidence:.0%}",
                        body=str(getattr(_fi, "reasoning", "") or "")[:300],
                        symbol=_fi.asset,
                        data={"direction": _fdir,
                              "confidence": round(float(_fi.confidence), 3),
                              "entry": float(_fi.entry_price or 0),
                              "sl": float(_fi.stop_loss or 0),
                              "tp": float(_fi.take_profit or 0)})
            except Exception as _feed_exc:
                logger.debug("Agent feed thesis events skipped: %s", _feed_exc)

        # ── Adaptive Confidence Threshold ──
        # Auto-adjust threshold based on recent win rate
        from bot.config import RUNTIME
        if CONFIG.adaptive.adaptive_threshold_enabled:
            try:
                recent_trades = self.portfolio._history[-CONFIG.adaptive.adaptive_threshold_lookback:]
                if len(recent_trades) >= 5:
                    recent_closed = [t for t in recent_trades if t.closed_at is not None]
                    if len(recent_closed) >= 5:
                        recent_wins = sum(1 for t in recent_closed if t.pnl > 0)
                        recent_wr = recent_wins / len(recent_closed)

                        if recent_wr >= CONFIG.adaptive.adaptive_threshold_high_wr:
                            # Winning streak: lower threshold to capture more
                            new_thresh = max(CONFIG.adaptive.adaptive_threshold_min,
                                           RUNTIME.auto_confirm_threshold - 0.05)
                        elif recent_wr <= CONFIG.adaptive.adaptive_threshold_low_wr:
                            # Losing streak: raise threshold to be selective
                            new_thresh = min(CONFIG.adaptive.adaptive_threshold_max,
                                           RUNTIME.auto_confirm_threshold + 0.05)
                        else:
                            new_thresh = RUNTIME.auto_confirm_threshold

                        if new_thresh != RUNTIME.auto_confirm_threshold:
                            audit(system_log,
                                  f"Adaptive threshold: {RUNTIME.auto_confirm_threshold:.2f} → {new_thresh:.2f} "
                                  f"(WR={recent_wr:.0%} over last {len(recent_closed)} trades)",
                                  action="adaptive_threshold", result="ADJUSTED")
                            RUNTIME.auto_confirm_threshold = new_thresh
            except Exception:
                pass  # fail-open

        # ── Auto-confirmation for high-confidence signals ──
        # If confidence exceeds threshold, bypass human confirmation gate
        # and auto-execute. Notifications still go to Telegram with
        # "[AUTO]" tag so the operator can see what happened.
        # RC-AUD-002: auto-confirm bypasses the human-decision gate. It is
        # disabled by default (threshold 1.0) and, in LIVE mode, refuses to place
        # real-money orders unless AUTO_CONFIRM_LIVE_ENABLED is explicitly set.
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_ideas = [
            (tid, tidea) for tid, tidea in list(self._pending_ideas.items())
            if self._auto_confirm_gate_value(tidea) >= auto_threshold
        ]
        if auto_ideas and CONFIG.is_live() and not CONFIG.auto_confirm_live_enabled:
            for tid, tidea in auto_ideas:
                audit(trade_log,
                      f"Auto-confirm SUPPRESSED in live mode for {tidea.asset} "
                      f"(conf={tidea.confidence:.2f}) — human confirmation required. "
                      f"Set AUTO_CONFIRM_LIVE_ENABLED=true to allow live auto-execution.",
                      action="auto_confirm", result="SUPPRESSED_LIVE",
                      data={"trade_id": tid, "confidence": tidea.confidence,
                            "threshold": auto_threshold})
            auto_ideas = []
        for tid, tidea in auto_ideas:
            # Entry-timing gate (auto path only): DEFER an autonomous entry whose
            # sub-degree hasn't confirmed the turn yet (scoped to
            # ENTRY_TIMING_REGIMES). The idea lapses via its pending-TTL and is
            # re-checked on a later scan. Fail-open: a missing/True verdict runs.
            _et_ok, _et_why = self._pending_timing.get(tid, (True, ""))
            if not _et_ok:
                audit(trade_log,
                      f"Auto-entry DEFERRED for {tidea.asset} — awaiting wave-degree "
                      f"confirmation ({_et_why})",
                      action="entry_timing", result="DEFERRED",
                      data={"trade_id": tid, "reason": _et_why})
                continue
            audit(trade_log,
                  f"Auto-confirming {tidea.asset} (conf={tidea.confidence:.2f} >= {auto_threshold})",
                  action="auto_confirm", result="TRIGGERING",
                  data={"trade_id": tid, "confidence": tidea.confidence,
                        "threshold": auto_threshold})
            try:
                result = await self.confirm_trade(tid, user_id="auto")
                audit(trade_log,
                      f"Auto-confirm result for {tidea.asset}: {result[:120]}",
                      action="auto_confirm", result="DONE",
                      data={"trade_id": tid, "result_preview": result[:200]})
                # Notify via Telegram if callback is set
                if self._auto_confirm_notify_callback:
                    try:
                        await self._auto_confirm_notify_callback(tidea, result)
                    except Exception:
                        pass
            except Exception as exc:
                audit(trade_log,
                      f"Auto-confirm failed for {tidea.asset}: {exc}",
                      action="auto_confirm", result="ERROR",
                      data={"trade_id": tid, "error": str(exc)})

        self._transition(AgentState.MONITORING, "checking open positions")
        # The safety-critical one: this is the SL/TP monitor on every tick.
        # A hang here means stops are not being watched, so it gets the loud
        # cap like the rest — named, and counted as a tick failure.
        await self._phase(self._check_open_positions(), "positions")
        self._transition(AgentState.IDLE, "tick cycle complete")

    async def _cached_ohlcv(self, exchange, symbol, timeframe, limit=100, ttl=120):
        """Fetch OHLCV with a simple TTL cache to avoid refetching within `ttl` seconds.

        The cache key includes ``limit`` (audit): the MTF loop asks for 200
        bars while the primary scan caches 100 — a shared key silently served
        the shorter series and structure math ran on half its window.
        """
        key = f"{symbol}:{timeframe}:{limit}"
        now = time.monotonic()
        if key in self._ohlcv_cache:
            cached_time, cached_data = self._ohlcv_cache[key]
            if now - cached_time < ttl:
                return cached_data
        data = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        self._ohlcv_cache[key] = (now, data)
        # C2-54 FIX: Hard size cap + TTL eviction to prevent unbounded growth.
        # First try TTL-based eviction; if still over limit, evict oldest entries.
        if len(self._ohlcv_cache) > 200:
            cutoff = now - ttl * 2
            self._ohlcv_cache = {k: v for k, v in self._ohlcv_cache.items() if v[0] > cutoff}
        if len(self._ohlcv_cache) > 200:
            # Still over limit — evict oldest entries
            sorted_keys = sorted(self._ohlcv_cache, key=lambda k: self._ohlcv_cache[k][0])
            for old_key in sorted_keys[:len(self._ohlcv_cache) - 200]:
                del self._ohlcv_cache[old_key]
        return data

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Parse a ccxt timeframe to milliseconds (shared util delegate)."""
        from bot.utils.candles import timeframe_to_ms
        return timeframe_to_ms(timeframe)

    def _drop_forming_candle(self, ohlcv, timeframe: str):
        """Drop the in-progress last candle (repaint guard) — shared util
        delegate so the live executor applies the identical policy."""
        from bot.utils.candles import drop_forming_candle
        return drop_forming_candle(ohlcv, timeframe)

    async def _refine_entry_mtf(self, idea: TradeIdea, exchange) -> TradeIdea:
        """Zoom into lower timeframe to find optimal entry within the setup zone.

        After identifying a setup on 1H/4H, check 15m candles for:
        - Better entry near support/resistance within the zone
        - Momentum confirmation on lower timeframe
        - Tighter stop placement based on lower-TF structure

        Returns refined TradeIdea (or original if refinement fails/not applicable).
        """
        try:
            # Only refine for swing/intraday strategies (not scalps)
            if idea.strategy_type == "scalp":
                return idea

            symbol = idea.asset

            # Fetch 15m candles for the last ~12 hours (48 candles).
            # Audit fix #23: drop the still-forming 15m bar like every other
            # analysis path — refinement previously read the in-progress close.
            candles_15m = await self._cached_ohlcv(exchange, symbol, "15m", limit=48, ttl=60)
            candles_15m = self._drop_forming_candle(candles_15m, "15m")
            if not candles_15m or len(candles_15m) < 20:
                return idea

            import numpy as np
            closes = np.array([c[4] for c in candles_15m])
            highs = np.array([c[2] for c in candles_15m])
            lows = np.array([c[3] for c in candles_15m])

            current_price = float(closes[-1])

            # Find recent support/resistance on 15m
            recent_lows = lows[-20:]
            recent_highs = highs[-20:]

            is_long = idea.direction.value == "LONG"

            if is_long:
                # For longs, look for nearest support level below current price
                # as a better entry point
                support_candidates = []
                for i in range(2, len(recent_lows) - 2):
                    if recent_lows[i] <= recent_lows[i-1] and recent_lows[i] <= recent_lows[i-2] and \
                       recent_lows[i] <= recent_lows[i+1] and recent_lows[i] <= recent_lows[i+2]:
                        support_candidates.append(float(recent_lows[i]))

                if support_candidates:
                    # Find the nearest support below current price
                    supports_below = [s for s in support_candidates if s < current_price]
                    if supports_below:
                        best_support = max(supports_below)  # closest support below
                        # Only refine if support is within 1% of current price
                        pct_diff = (current_price - best_support) / current_price
                        if 0.001 < pct_diff < 0.01:
                            # Refined entry is at support + small buffer
                            refined_entry = best_support + (current_price - best_support) * 0.2
                            # Tighter SL based on 15m structure: RAISE the long
                            # stop to just under the recent structure low —
                            # min() here could only ever WIDEN risk — and never
                            # place it at/above the refined entry.
                            min_low = float(np.min(recent_lows[-10:]))
                            sl_candidate = min_low * 0.998
                            refined_sl = idea.stop_loss
                            if idea.stop_loss < sl_candidate < refined_entry:
                                refined_sl = sl_candidate
                            # Preserve R:R ratio for TP. Signed risk: if the
                            # (kept) stop would sit at/above the refined entry
                            # the refinement is geometrically invalid — skip it.
                            original_rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss)
                            new_risk = refined_entry - refined_sl
                            refined_tp = refined_entry + new_risk * original_rr

                            if new_risk > 0:
                                audit(system_log,
                                      f"MTF entry refined for {symbol}: {idea.entry_price:.4f} -> {refined_entry:.4f} "
                                      f"(SL: {idea.stop_loss:.4f} -> {refined_sl:.4f})",
                                      action="mtf_refine", result="REFINED")

                                idea = idea.model_copy(update={
                                    "entry_price": round(refined_entry, 8),
                                    "stop_loss": round(refined_sl, 8),
                                    "take_profit": round(refined_tp, 8),
                                })
            else:
                # For shorts, look for nearest resistance level above current price
                resistance_candidates = []
                for i in range(2, len(recent_highs) - 2):
                    if recent_highs[i] >= recent_highs[i-1] and recent_highs[i] >= recent_highs[i-2] and \
                       recent_highs[i] >= recent_highs[i+1] and recent_highs[i] >= recent_highs[i+2]:
                        resistance_candidates.append(float(recent_highs[i]))

                if resistance_candidates:
                    resistances_above = [r for r in resistance_candidates if r > current_price]
                    if resistances_above:
                        best_resistance = min(resistances_above)  # closest resistance above
                        pct_diff = (best_resistance - current_price) / current_price
                        if 0.001 < pct_diff < 0.01:
                            refined_entry = best_resistance - (best_resistance - current_price) * 0.2
                            # Mirror of the long side: LOWER the short stop to
                            # just above recent structure, never widen, never
                            # at/below the refined entry.
                            max_high = float(np.max(recent_highs[-10:]))
                            sl_candidate = max_high * 1.002
                            refined_sl = idea.stop_loss
                            if refined_entry < sl_candidate < idea.stop_loss:
                                refined_sl = sl_candidate
                            original_rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss)
                            new_risk = refined_sl - refined_entry
                            refined_tp = refined_entry - new_risk * original_rr

                            if new_risk <= 0:
                                return idea  # invalid geometry — keep original

                            audit(system_log,
                                  f"MTF entry refined for {symbol}: {idea.entry_price:.4f} -> {refined_entry:.4f} "
                                  f"(SL: {idea.stop_loss:.4f} -> {refined_sl:.4f})",
                                  action="mtf_refine", result="REFINED")

                            idea = idea.model_copy(update={
                                "entry_price": round(refined_entry, 8),
                                "stop_loss": round(refined_sl, 8),
                                "take_profit": round(refined_tp, 8),
                            })
        except Exception as exc:
            # Fail-open: return original idea if refinement fails
            logger.debug("MTF refinement failed for %s: %s", idea.asset, exc)

        return idea

    async def _analyze_signals_batched(
        self, signals, *, timeframe: str = "1h", lightweight: bool = False,
    ) -> list:
        """Analyze a list of scanner signals with BOUNDED concurrency.

        The scanner emits a wide (~200) volume-filtered universe, so an
        unbounded ``asyncio.gather`` would fan out hundreds of simultaneous
        OHLCV + order-flow + MTF fetches and overwhelm the exchange rate
        limiter. A semaphore caps in-flight analyses at
        ``CONFIG.scan_analysis_concurrency`` (default 12) — the same bounded
        pattern DeepScan already uses. Each analysis is wrapped so one failure
        never sinks the batch, AND bounded by ``CONFIG.analysis_timeout_sec``
        so one that never returns cannot either; the result list preserves
        input order with ``None`` for any signal that failed, timed out or
        produced no idea.
        """
        limit = max(1, int(CONFIG.scan_analysis_concurrency))
        sem = asyncio.Semaphore(limit)

        # The autonomous engine runs under the OPERATOR's identity and keys —
        # admin context — so tier routing can reach the operator's Anthropic
        # key (the non-admin guard otherwise skips it; live incident
        # 2026-07-11: paid key unreachable, bot on rule engine). Flag-gated:
        # ENGINE_ANALYSIS_AS_ADMIN=false restores cheap-tier-only scans.
        _as_admin = bool(getattr(CONFIG.analyzer, "engine_analysis_as_admin", True))

        # A HANG is not an exception. The try/except below has always covered
        # analyses that FAIL; it does nothing for one that never returns, and
        # asyncio.gather waits for every task. One parked analysis parks the
        # batch, the batch parks the phase, and the phase cap kills the tick —
        # production, 2026-07-29: "analyze (exceeded its 300s, x37)", thirty-
        # seven dead ticks feeding the warning-rate breaker until it tripped
        # and started rejecting real trades. Bound each analysis on its own.
        # 0 disables the cap.
        _cap = float(getattr(CONFIG, "analysis_timeout_sec", 0.0) or 0.0)
        _timed_out: list[str] = []
        # Structured twin of the display list above, so the running
        # tally never has to parse "FET/USDT (mtf)" back apart.
        _timed_out_pairs: list[tuple[str, str]] = []
        # Symbols this batch actually put to REST (armed a cooldown for). A
        # subset of _timed_out: resting can be disabled, and a timeout that
        # arms nothing is still a timeout.
        _rested_syms: list[str] = []
        # Progress, so a phase that is CANCELLED can still say what it was
        # doing. Without this the analyze phase died silently: the operator
        # saw "exceeded its 300s" and nothing about whether it had finished 4
        # symbols or 194 — and those call for opposite fixes (a few bad
        # symbols versus a universe too wide for the budget). Kept on the
        # engine because the batch's own frames are gone by the time the
        # timeout handler runs.
        # Tagged with a sequence number, because the slot is engine-level and
        # a CANCELLED batch keeps unwinding after _phase has returned. Its
        # tasks' `finally` blocks then fire while the NEXT tick has already
        # installed a fresh record — and they incremented that one. Production
        # showed the impossible result within minutes of shipping this:
        #
        #     ↳ 47/40 signals analysed before it was cancelled
        #
        # 47 of 40. An instrument that reports a number larger than its own
        # denominator is not reporting anything. A stale batch now cannot
        # write into a record it does not own.
        self._analyze_batch_seq = int(getattr(self, "_analyze_batch_seq", 0)) + 1
        _seq = self._analyze_batch_seq
        # "done" counts finished work of ANY outcome (see the finally block
        # below) — an idea, no idea, a failure, a timeout. That is the right
        # answer to "how far did the batch get" and the WRONG one to "how many
        # symbols were analysed", and once symbols can rest the two diverge:
        # a rested symbol never ran at all. "skipped_resting" is counted
        # separately so no reader has to guess which question it is answering.
        self._analyze_progress = {
            "of": len(signals), "done": 0, "skipped_resting": 0,
            "started": time.monotonic(), "seq": _seq}
        self._stage_totals = {k: 0.0 for k in ANALYSIS_STAGES}
        # Per-symbol duration profiles: reset each batch so a resolved slow
        # episode (e.g. one 77s FIL fetch) does not persist as the reported
        # max in every subsequent cycle. Same epoch discipline as _stage_totals.
        self._stage_profiles: dict = {}
        # Per-symbol in-flight stage, read by the per-analysis timeout handler.
        # Reset per batch so a cancelled batch's leftovers cannot be read as
        # this one's — the same epoch discipline the progress record uses.
        self._stage_inflight: dict[str, str] = {}
        # Will this batch even fit? The phase cap is per-analysis; the PHASE
        # cap that actually kills the batch belongs to _phase(). For 37
        # consecutive ticks the operator saw "analyze exceeded its 300s" and
        # had to work backwards to the cause — a universe that had grown to
        # 161 symbols when the measured throughput only covered ~90 in the
        # budget. Every input to that arithmetic was already on the engine.
        # Say it up front, from the LAST batch's measured rate, so the tick
        # that is about to be truncated says so before it is.
        # Guarded like the other instruments on this path: it runs under
        # lightweight stubs, and a forecast must never be the reason a batch
        # cannot start.
        try:
            _fc = getattr(self, "_forecast_analyze_capacity", None)
            self._analyze_capacity = _fc(len(signals)) if _fc else None
        except Exception:
            self._analyze_capacity = None

        async def _one(sig):
            async with sem:
                try:
                    _coro = self._analyze_signal(
                        sig, timeframe=timeframe, lightweight=lightweight,
                        is_admin=_as_admin)
                    if _cap > 0:
                        _out = await asyncio.wait_for(_coro, timeout=_cap)
                    else:
                        _out = await _coro
                    # A COMPLETED analysis clears the strike count. Escalation
                    # is for a symbol that keeps hanging, not for one that hung
                    # once in a bad exchange minute six hours ago — without
                    # this, a healthy symbol accumulates strikes over a session
                    # and eventually rests for four hours on its first blip.
                    #
                    # Cleared on COMPLETION, never on a skip: a rested symbol
                    # returns before this line, so resting can never reset the
                    # counter that decides how long it rests.
                    try:
                        _st = getattr(self, "_analysis_rest_strikes", None)
                        if _st:
                            _st.pop(normalize_symbol(
                                str(getattr(sig, "symbol", "?"))), None)
                    except Exception:
                        pass
                    return _out
                except asyncio.TimeoutError:
                    # Same outcome as any other failed analysis: this signal
                    # yields no idea. Never fabricate one for a symbol we
                    # could not actually analyse.
                    #
                    # WHERE it was stuck, measured — not the batch totals
                    # (those cover COMPLETED stages only) and not inferred
                    # from the last finished stage. "FET/USDT (mtf)" is a
                    # diagnosis; "FET/USDT" alone sent the operator to the
                    # logs. Absent when unmarked — omit, never guess.
                    _sym = str(getattr(sig, "symbol", "?"))
                    _stg = ""
                    try:
                        _stg = (getattr(self, "_stage_inflight", None) or {}).get(_sym, "")
                    except Exception:
                        _stg = ""
                    _timed_out.append(f"{_sym} ({_stg})" if _stg else _sym)
                    _timed_out_pairs.append((_sym, _stg))
                    # REST IT. Naming a hanging symbol is not the same as
                    # stopping it hanging again: MCD/AMD/HOOD each burned the
                    # full 90s on every sweep, 270s of a 292s analyze phase,
                    # for as long as they stayed unanalysable.
                    #
                    # ARMED HERE, NOT IN THE BATCH TAIL. `_last_analysis_timeout`
                    # is written after `await asyncio.gather(...)` returns — and
                    # in this incident it does NOT return, because the analyze
                    # phase hits its 300s cap and is cancelled first. A fix
                    # placed there is present, greppable, and never reached in
                    # the exact case it was written for.
                    #
                    # Reuses `_symbol_cooldowns`, the SAME dict and the SAME
                    # pre-analysis guard the post-SL and loss-streak cooldowns
                    # use, so a rested symbol is skipped by machinery that
                    # already works rather than by a second parallel mechanism.
                    # The key MUST be normalize_symbol(...) — the guard reads
                    # that spelling, and arming any other writes a cooldown
                    # nothing consults.
                    try:
                        from bot.core.symbol_rest import rest_seconds
                        _key = normalize_symbol(_sym)
                        _strikes = getattr(self, "_analysis_rest_strikes", None)
                        if _strikes is None:
                            _strikes = self._analysis_rest_strikes = {}
                        _n = int(_strikes.get(_key, 0)) + 1
                        # CONFIG root, NOT CONFIG.analyzer — these sit on
                        # AppConfig beside analysis_timeout_sec, which the cap
                        # above reads the same way. The first draft used
                        # CONFIG.analyzer.*, raised AttributeError, and was
                        # swallowed by the except below: the guard was present,
                        # greppable, and armed nothing. Caught by driving the
                        # batch, which no source scan could have done.
                        _rest = rest_seconds(
                            _n,
                            getattr(CONFIG, "analysis_timeout_rest_sec", 0.0),
                            getattr(CONFIG, "analysis_timeout_rest_cap_sec", 0.0))
                        # 0.0 means "do not arm" — disabled, or not a real
                        # strike. Writing monotonic()+0 would expire on the very
                        # next comparison and silently restore the old
                        # behaviour while looking like a working cooldown.
                        if _rest > 0:
                            _strikes[_key] = _n
                            _until = time.monotonic() + _rest
                            self._symbol_cooldowns[_key] = _until
                            # Provenance, so the guard can tell a rest from a
                            # post-SL or loss-streak cooldown. Same expiry, and
                            # popped together — a stale rest marker outliving
                            # its cooldown would relabel the next risk cooldown
                            # on this symbol as an analysis timeout.
                            _ru = getattr(self, "_analysis_rest_until", None)
                            if _ru is None:
                                _ru = self._analysis_rest_until = {}
                            _ru[_key] = _until
                            _rested_syms.append(_sym)
                            audit(scan_log,
                                  f"Symbol resting after analysis timeout: "
                                  f"{_sym} for {int(_rest)}s (strike {_n})",
                                  action="analysis_rest", result="ARMED")
                    except Exception:
                        # Instrumentation-grade guard, same posture as the rest
                        # of this coroutine: several suites drive the batch with
                        # SimpleNamespace stubs, and resting a symbol must never
                        # be the reason an analysis batch cannot finish.
                        pass
                    return None
                except Exception as exc:
                    logger.debug("Signal analysis error for %s: %s",
                                 getattr(sig, "symbol", "?"), exc)
                    return None
                finally:
                    # Counts finished work of ANY outcome — an idea, no idea,
                    # a failure, a timeout. The question it answers is "how
                    # far did the batch get", not "how many succeeded".
                    try:
                        _p = self._analyze_progress
                        if _p is not None and _p.get("seq") == _seq:
                            _p["done"] += 1
                    except Exception:
                        pass
                    # Clear the in-flight stage marker AFTER the timeout
                    # handler above has read it. A stale mark would attribute
                    # this batch's hang to the previous batch's stage.
                    #
                    # getattr-guarded like every other instrument on this
                    # path: five suites drive this batch with SimpleNamespace
                    # stubs, and a diagnostic must never be the reason an
                    # analysis cannot finish. (Caught by those suites — the
                    # third time this exact hazard has bitten instrumentation
                    # added here.)
                    try:
                        _sx = getattr(self, "_stage_exit", None)
                        if _sx is not None:
                            _sx(str(getattr(sig, "symbol", "?")))
                    except Exception:
                        pass

        if not signals:
            return []
        _results = await asyncio.gather(*[_one(s) for s in signals])
        # Measure the rate this batch actually ran at, so the next one can
        # forecast instead of guess. Guarded: the recorder is instrumentation
        # and must not require its caller to grow attributes.
        try:
            _p = self._analyze_progress or {}
            _rec_tp = getattr(self, "_record_analyze_throughput", None)
            if _rec_tp is not None and _p.get("seq") == _seq:
                _rec_tp(int(_p.get("done") or 0), len(signals),
                        time.monotonic() - float(_p.get("started") or 0.0))
        except Exception:
            pass
        # Where the work went. Reported on a batch that FINISHED, so the
        # healthy shape is on record too — a breakdown you only ever see
        # during an incident has nothing to be compared against.
        # Guarded like the phase recorder: instrumentation may not require
        # its caller to grow attributes, and this helper runs on a path
        # exercised with lightweight stubs.
        _rep = ""
        try:
            _fn = getattr(self, "_stage_report", None)
            if _fn is not None:
                _rep = _fn(time.monotonic() - float(
                    (self._analyze_progress or {}).get("started")
                    or time.monotonic()))
        except Exception:
            _rep = ""
        if _rep:
            audit(scan_log,
                  f"Analyze batch complete: {len(signals)} signals — {_rep}",
                  action="analyze_stages", result="OK",
                  data={"of": len(signals),
                        **{k: round(v, 1) for k, v in
                           (self._stage_totals or {}).items()}})
        if _timed_out:
            # NAMED, not silently skipped — a quiet tick hiding a hanging
            # dependency is exactly how this stayed invisible for a day. The
            # audit line lands in a log the operator cannot easily reach, so
            # keep the fact on the engine where /status and the scan-timeout
            # hint can read it too.
            self._last_analysis_timeout = {
                "skipped": len(_timed_out),
                "of": len(signals),
                "cap_s": _cap,
                "at": time.monotonic(),
                # WHICH symbols. The audit line below has carried these all
                # along, but the operator-facing record dropped them — so the
                # hint could say "1 of 70 gave up after 90s" and not name the
                # one symbol that would make the diagnosis instant. A capped
                # few, not all: the record rides /status-adjacent surfaces.
                "symbols": list(_timed_out[:5]),
            }
            # ...and fold this batch into the running distribution. The record
            # above is overwritten every batch, so it answers "what timed out
            # most recently" while the question being asked is "what keeps
            # timing out". Telling three bad symbols from capacity pressure
            # took reading /status by hand across several ticks and noticing
            # the names had rotated -- a real finding reached by a method that
            # gives the opposite answer to anyone who looks only once.
            #
            # getattr-guarded like every instrument on this path: several
            # suites drive this batch with SimpleNamespace stubs, and a
            # diagnostic must never be the reason an analysis cannot finish.
            try:
                from bot.core import analysis_timeout_tally as _att
                _t = getattr(self, "_analysis_timeout_tally", None)
                if _t is None:
                    _t = self._analysis_timeout_tally = _att.new_tally()
                _att.record(_t, _timed_out_pairs)
            except Exception:
                pass
            audit(scan_log,
                  f"Analysis timed out for "
                  f"{len(_timed_out)} of {len(signals)} signals "
                  f"after {_cap:.0f}s each. The tick continues "
                  f"with the rest.",
                  action="analysis_timeout", result="SKIPPED",
                  data={"skipped": len(_timed_out), "of": len(signals),
                        "cap_s": _cap, "symbols": _timed_out[:20]})
        return _results

    async def _analyze_signal(self, signal: MarketSignal, *, timeframe: str = "1h", is_admin: bool = False, user_id=None, user_tier=None, lightweight: bool = False) -> Optional[TradeIdea]:
        """Run full analysis pipeline on a single signal.

        Args:
            signal: Market signal to analyze.
            timeframe: OHLCV timeframe to fetch (e.g. "5m", "15m", "1h", "4h").
            lightweight: skip the order-flow (4 calls) and multi-timeframe (4
                calls) fetches, leaving just the 1 primary OHLCV fetch. Used by
                the INTERACTIVE force-scan so a Telegram tap returns in seconds
                even under exchange throttling. Confidence is computed from
                technicals only; the entry/SL/TP/risk math is unchanged, and the
                background loop still runs the full pipeline for auto-trading.
        """
        # ── Per-symbol cooldown after SL hit OR a loss streak (checked FIRST) ─
        # #32: short-circuit a cooling symbol BEFORE the expensive OHLCV +
        # order-flow fetch and full analysis pipeline, instead of after.
        # Shared by two arming sources (both in _on_live_position_closed): a
        # single post-SL cooldown (short, ~30 min) and a longer loss-streak
        # cooldown once the symbol has lost repeatedly (see
        # CONFIG.risk.symbol_loss_streak_*) — same dict/check either way.
        # (idea.asset == signal.symbol, so the decision is identical, just earlier.)
        symbol_key = normalize_symbol(signal.symbol)
        _sym_cd = self._symbol_cooldowns.get(symbol_key, 0)
        if _sym_cd:
            if time.monotonic() < _sym_cd:
                _remaining = int(_sym_cd - time.monotonic())
                # WHICH KIND of cooldown, because they are different facts.
                # A risk cooldown (post-SL, loss streak) is a decision about
                # the SYMBOL: we can read it and we are choosing not to trade
                # it. A rest is a decision about US: we could not read it in
                # time. Reporting the second as ordinary coverage is how a
                # symbol nobody looked at gets published as analysed-and-clear.
                _resting = False
                try:
                    _rest_until = getattr(self, "_analysis_rest_until", None) or {}
                    _resting = float(_rest_until.get(symbol_key, 0)) >= _sym_cd
                except Exception:
                    _resting = False
                audit(scan_log,
                      f"Signal skipped: {signal.symbol} on "
                      f"{'analysis rest' if _resting else 'symbol cooldown'} "
                      f"({_remaining}s remaining)",
                      action="analysis_rest_skip" if _resting else "symbol_cooldown",
                      result="SKIPPED")
                if _resting:
                    try:
                        _p = self._analyze_progress
                        if _p is not None:
                            _p["skipped_resting"] = int(
                                _p.get("skipped_resting") or 0) + 1
                    except Exception:
                        pass
                return None
            # Cooldown expired → clear it, and the rest provenance with it, so
            # a later risk cooldown on the same symbol is never mistaken for a
            # rest that has long since lapsed.
            self._symbol_cooldowns.pop(symbol_key, None)
            try:
                _ru = getattr(self, "_analysis_rest_until", None)
                if _ru is not None:
                    _ru.pop(symbol_key, None)
            except Exception:
                pass

        try:
            # Use futures exchange for non-Crypto categories (metals,
            # commodities, etc.) AND for perp-only crypto listings, whose
            # futures-form symbol ("X/USDT:USDT") has no spot market to
            # fetch from (futures-first discovery). Venue-native symbols
            # (":USDC", from the non-Bitget venue overlay) only exist on
            # the active venue — route their data there.
            category = getattr(signal, "asset_category", "Crypto") or "Crypto"
            if ":USDC" in signal.symbol:
                exchange = (await self.scanner._get_venue_data_exchange()
                            or await self.scanner._get_futures_exchange())
            elif category != "Crypto" or ":" in signal.symbol:
                exchange = await self.scanner._get_futures_exchange()
            else:
                exchange = await self.scanner._get_exchange()
            # Parallelize OHLCV fetch and order flow analysis. For crypto spot
            # symbols, map to the USDT-M perp so funding rate + open interest
            # actually resolve — this is a FUTURES bot, and without the
            # derivatives symbol both fetches raise on the spot market and the
            # funding/OI voters, cascade + squeeze detectors and OI divergences
            # were permanently neutral. Fail-open: a bad mapping just degrades
            # those components to n/a exactly as before.
            of_deriv = None
            if (category == "Crypto" and ":" not in signal.symbol
                    and signal.symbol.endswith("/USDT")):
                of_deriv = f"{signal.symbol}:USDT"
            ohlcv_task = self._cached_ohlcv(exchange, signal.symbol, timeframe, limit=100)
            if lightweight:
                # Interactive fast path: only the primary OHLCV; skip the 4
                # order-flow fetches (funding/OI/book/trades).
                _t0 = time.monotonic()
                _stage_enter_guarded(self, signal.symbol, "fetch")
                results = list(await asyncio.gather(ohlcv_task, return_exceptions=True))
                _fetch_dt = time.monotonic() - _t0
                self._stage_add("fetch", _fetch_dt)
                _stage_profile_record(self, "fetch", signal.symbol, _fetch_dt)
                results.append(None)
            else:
                of_task = self.order_flow.analyze(exchange, signal.symbol,
                                                  derivatives_symbol=of_deriv)
                _t0 = time.monotonic()
                _stage_enter_guarded(self, signal.symbol, "fetch")
                results = list(await asyncio.gather(ohlcv_task, of_task, return_exceptions=True))
                _fetch_dt = time.monotonic() - _t0
                self._stage_add("fetch", _fetch_dt)
                _stage_profile_record(self, "fetch", signal.symbol, _fetch_dt)
            ohlcv = results[0] if not isinstance(results[0], Exception) else None
            of_signal = results[1] if not isinstance(results[1], Exception) else None

            # Per-CALL attribution for the fetch stage. `_stage_add("fetch")`
            # records ONE duration for five concurrent venue calls, so a
            # symbol at 74s named nothing about WHICH call was slow — and an
            # order book, a trade window, a funding rate and an open-interest
            # read need entirely different fixes. Production, 2026-08-01: the
            # recurring max was correctly traced to a symbol and then stalled
            # there, because the profile could not say more.
            #
            # Recorded as pseudo-stages so the existing per-symbol shape
            # analysis applies unchanged: fetch:book, fetch:trades,
            # fetch:funding, fetch:oi.
            try:
                for _cn, _cv in (getattr(of_signal, "call_seconds", None) or {}).items():
                    _stage_profile_record(self, f"fetch:{_cn}", signal.symbol, _cv)
            except Exception:
                pass

            # Seed the exchange-flow cache with the funding rate this pass just
            # fetched — funding_rate_provider (the macro LIQUIDATION_RISK
            # check) reads that cache, and nothing else fills it. Fail-open.
            if of_signal is not None and not isinstance(of_signal, BaseException) \
                    and getattr(of_signal, "funding_rate", None) is not None:
                try:
                    self.exchange_flow.seed_funding_rate(signal.symbol, of_signal.funding_rate)
                except Exception:
                    pass

            # #17: shadow-record the live order-flow snapshot so the backtest can
            # replay the same microstructure path (gated OF_RECORD_SNAPSHOTS, now
            # default ON). Write-only, best-effort, fail-open — never breaks the
            # scan path. Set OF_RECORD_SNAPSHOTS=0 to disable.
            if of_signal is not None and not isinstance(of_signal, BaseException) and \
                    os.getenv("OF_RECORD_SNAPSHOTS", "1").strip().lower() in ("1", "true", "yes", "on"):
                try:
                    from bot.backtest.recorded_order_flow import record_snapshot
                    record_snapshot(
                        _of_snapshot_path(),
                        of_signal,
                    )
                except Exception:
                    pass
            if isinstance(results[0], Exception):
                audit(
                    system_log,
                    f"OHLCV fetch failed: {results[0]}",
                    action="fetch_candles",
                    result="ERROR",
                )
                return None
            if isinstance(results[1], Exception):
                audit(system_log, f"Order flow analysis failed: {results[1]}",
                      action="order_flow", result="ERROR")
        except Exception as exc:
            audit(
                system_log,
                f"OHLCV fetch failed: {exc}",
                action="fetch_candles",
                result="ERROR",
            )
            return None

        # Repaint fix (gated): drop the still-forming last candle so all TA uses
        # CLOSED bars only. Entry pricing is unaffected — the analyzer prices off
        # the live ticker (signal.price), not the last candle. No-op when off.
        ohlcv = self._drop_forming_candle(ohlcv, timeframe)

        # Timeframe-matched Elliott (gated, default ON): fetch the extra
        # timeframes whose wave degree the analyzer may need for scalp/swing/etc,
        # so it can read the wave structure appropriate to the setup. Cached and
        # fail-open — a fetch failure just omits that timeframe (analyzer no-ops).
        mtf_candles = None
        if not lightweight and (CONFIG.analyzer.elliott_mtf_enabled or CONFIG.analyzer.mtf_confluence_enabled):
            mtf_candles = {}
            # PARALLEL, not sequential. These four fetches are independent —
            # four timeframes of the same symbol — and awaiting them one after
            # another put four socket timeouts END TO END inside a single
            # analysis. With the data client's per-request cap that was the
            # longest serial stretch in the whole pipeline by a wide margin,
            # and it sat under a 300s phase cap. The order-flow fetches beside
            # it were already gathered; this now matches them.
            _tf_specs = (("15m", 200), ("1h", 200), ("4h", 200), ("1d", 200))
            # ttl=180 caches SUCCESSES; a failure caches nothing, so a symbol
            # whose extra timeframes this venue will not serve failed all four
            # and was asked again on the very next tick — every tick, each
            # attempt costing up to the per-call timeout, and fail-open so it
            # never surfaced as an error. That is the mtf stall: mtf overtook
            # fetch (~59-64% vs ~36-41%) with 22-27 signals skipped per cycle.
            #
            # Remember the failures too. Suppression needs CONSECUTIVE
            # failures (a blip cannot latch it), expires on its own, and
            # clears on any success — the cost of a needless fetch is
            # latency, the cost of a wrongly-skipped timeframe is a worse
            # decision on real money.
            _tf_specs = _mtf_filter_specs(self, signal.symbol, _tf_specs)
            _t0 = time.monotonic()
            _stage_enter_guarded(self, signal.symbol, "mtf")
            _tf_results = await asyncio.gather(
                *[self._cached_ohlcv(exchange, signal.symbol, _tf, limit=_lim,
                                     ttl=_mtf_ttl(_tf))
                  for _tf, _lim in _tf_specs],
                return_exceptions=True)
            _mtf_dt = time.monotonic() - _t0
            self._stage_add("mtf", _mtf_dt)
            _stage_profile_record(self, "mtf", signal.symbol, _mtf_dt)
            for (_tf, _lim), _c in zip(_tf_specs, _tf_results):
                # Fail-open per timeframe, exactly as the sequential loop did:
                # one bad timeframe degrades that leg to absent, never the rest.
                if isinstance(_c, BaseException):
                    system_log.debug("Elliott MTF fetch %s failed: %s", _tf, _c)
                    _mtf_note(self, signal.symbol, _tf, ok=False)
                    continue
                if _c:
                    mtf_candles[_tf] = self._drop_forming_candle(_c, _tf)
                    _mtf_note(self, signal.symbol, _tf, ok=True)
                else:
                    # An EMPTY result is not an exception, but it is also not
                    # data. A venue that answers "no candles" instantly is
                    # cheap to re-ask; one that answers it after a timeout is
                    # the exact case this exists for. Counted either way —
                    # the pair still produced nothing usable.
                    _mtf_note(self, signal.symbol, _tf, ok=False)

        _t0 = time.monotonic()
        _stage_enter_guarded(self, signal.symbol, "analyze")
        idea = await self.analyzer.analyze(signal, ohlcv, order_flow=of_signal, is_admin=is_admin, user_id=user_id, user_tier=user_tier, mtf_candles=mtf_candles, timeframe=timeframe)
        _an_dt = time.monotonic() - _t0
        self._stage_add("analyze", _an_dt)
        _stage_profile_record(self, "analyze", signal.symbol, _an_dt)
        if idea is None:
            audit(scan_log, f"Analysis produced no idea for {signal.symbol}",
                  action="analyze_signal", result="NO_IDEA",
                  data={"symbol": signal.symbol, "timeframe": timeframe})
            return None

        # Log trade idea generation
        audit(scan_log, f"Trade idea generated: {idea.direction.value} {idea.asset}",
              action="trade_idea", result="GENERATED",
              data={
                  "id": idea.id,
                  "asset": idea.asset,
                  "direction": idea.direction.value,
                  "confidence": round(idea.confidence, 3),
                  "entry": idea.entry_price,
                  "sl": idea.stop_loss,
                  "tp": idea.take_profit,
                  "rr": round(idea.risk_reward_ratio, 2),
                  "timeframe": timeframe,
              })

        # ── Closed-loop learning nudge (opt-in, default OFF) ──────────────
        # The orchestrator already logs every decision + outcome; here we read
        # that experience back. Down-weight setups (same symbol + direction +
        # regime) that have historically LOST, slightly up-weight winners. The
        # nudge is small, capped, asymmetric, additive — it never overrides the
        # risk engine (every check still runs below); it only shifts confidence,
        # which can push a chronically-losing setup under the entry threshold.
        if CONFIG.learning.adaptive_confidence_enabled:
            try:
                _regime = str(getattr(self.risk, "_current_regime", "") or "")
                # Query on symbol + direction across ALL regimes (empty regime =
                # match any): a live bot accumulates too few same-symbol+direction
                # +regime samples to be useful, and direction already carries the
                # dominant signal (e.g. longs on a symbol chronically losing).
                _lctx = self.learning.get_learning_context(
                    symbol=idea.asset, market_regime="",
                    macro_state="", direction=idea.direction.value)
                _n = _lctx.get("similar_past_setups", 0) or 0
                _avg = _lctx.get("avg_past_pnl")
                if _n >= CONFIG.learning.adaptive_confidence_min_samples and _avg is not None:
                    if _avg < 0:
                        _delta = -CONFIG.learning.adaptive_confidence_max_penalty
                    elif _avg > 0:
                        _delta = CONFIG.learning.adaptive_confidence_max_boost
                    else:
                        _delta = 0.0
                    if _delta:
                        _old = idea.confidence
                        idea.confidence = round(max(0.0, min(1.0, _old + _delta)), 4)
                        audit(scan_log,
                              f"Learning nudge {idea.asset} {idea.direction.value}: "
                              f"conf {_old:.2f} -> {idea.confidence:.2f} "
                              f"(avg_past_pnl=${_avg:.2f} over {_n} setups)",
                              action="learning_confidence_nudge",
                              result="PENALIZED" if _delta < 0 else "BOOSTED",
                              data={"symbol": idea.asset, "direction": idea.direction.value,
                                    "regime": _regime, "delta": _delta,
                                    "avg_past_pnl": round(_avg, 4), "samples": _n,
                                    "old_conf": round(_old, 4), "new_conf": idea.confidence})
            except Exception as _learn_exc:
                # Fail-open: learning must never block or crash trade evaluation.
                logger.debug("Learning nudge skipped for %s: %s", idea.asset, _learn_exc)

        # Compute ATR from candles for the volatility guard (check #16)
        atr_value = None
        if len(ohlcv) >= 15:
            true_ranges = []
            for j in range(1, min(15, len(ohlcv))):
                h = float(ohlcv[-j][2])
                l = float(ohlcv[-j][3])
                pc = float(ohlcv[-j - 1][4])
                tr = max(h - l, abs(h - pc), abs(l - pc))
                true_ranges.append(tr)
            atr_value = sum(true_ranges) / len(true_ranges)

        # Smart scan: track ATR for interval adjustment
        if atr_value is not None and signal.price > 0:
            self._recent_atr_values[signal.symbol] = atr_value / signal.price

        # Feed the black-swan detector. This is the one place in the scan path
        # that already holds all four inputs it needs — symbol, last price,
        # period volume (ohlcv column 5) and ATR — so feeding it anywhere else
        # would mean recomputing them.
        #
        # Fail-open and silent: the detector only OBSERVES (nothing acts on
        # halt_recommended), so an exception here must never cost a trade
        # decision. It is `debug`, not `warning`, because a symbol with too
        # little history is the normal case, not a fault.
        try:
            _bs_vol = float(ohlcv[-1][5]) if ohlcv and len(ohlcv[-1]) > 5 else 0.0
            self.black_swan.update(signal.symbol, price=float(signal.price),
                                   volume=_bs_vol, atr=float(atr_value or 0.0))
        except Exception as _bs_exc:
            logger.debug("black swan update skipped for %s: %s", signal.symbol, _bs_exc)

        # (Signal-stack audit: the old "strategy router" call here computed a
        # 4-profile selection per signal, fed it an absolute-price ATR where
        # ADX was expected, and then DISCARDED the result — dead weight with
        # misleading inputs. Strategy behavior is owned by the analyzer's
        # strategy_type classification + CONFIG.strategy_types.)

        # ── Smart pyramid / duplicate symbol guard ─────────────────
        # Rules: max 2 entries per symbol, same direction adds require
        # 1R profit + 70% confidence. Opposite direction with high
        # confidence triggers a flip (close existing + open new).
        existing_positions = []  # list of (position, is_live, current_price)

        if CONFIG.is_live() and hasattr(self, 'live_executor'):
            for lp in self.live_executor.open_positions:
                lp_key = normalize_symbol(lp.symbol)
                if lp_key == symbol_key:
                    existing_positions.append((lp, True))

        if not existing_positions and hasattr(self, 'portfolio'):
            for pp in self.portfolio.open_positions:
                pp_key = normalize_symbol(pp.asset)
                if pp_key == symbol_key:
                    existing_positions.append((pp, False))

        is_pyramid_add = False
        if existing_positions:
            # Max 2 entries per symbol
            if len(existing_positions) >= 2:
                audit(scan_log, f"Signal skipped: max 2 positions on {idea.asset}",
                      action="pyramid_maxed", result="SKIPPED")
                return None

            pos, is_live = existing_positions[0]
            pos_dir = pos.direction if isinstance(pos.direction, str) else pos.direction.value
            idea_dir = idea.direction.value

            same_direction = (pos_dir.upper() == idea_dir.upper())

            if same_direction:
                # ── Same direction: pyramid add ──
                # Condition 1: confidence >= 70%
                if idea.confidence < 0.70:
                    audit(scan_log, f"Pyramid skipped: confidence {idea.confidence:.0%} < 70% for {idea.asset}",
                          action="pyramid_low_conf", result="SKIPPED")
                    return None

                # Condition 2: existing position is at least 1R in profit
                entry_px = pos.entry_price
                sl_px = pos.stop_loss if hasattr(pos, 'stop_loss') else getattr(pos, 'stop_loss', 0)
                initial_risk = abs(entry_px - sl_px) if sl_px else 0
                current_price = idea.entry_price  # new signal's entry = current price
                if pos_dir.upper() == "LONG":
                    unrealized = current_price - entry_px
                else:
                    unrealized = entry_px - current_price

                r_achieved = unrealized / initial_risk if initial_risk > 0 else 0
                if initial_risk <= 0 or unrealized < initial_risk:
                    audit(scan_log,
                          f"Pyramid skipped: {idea.asset} only {r_achieved:.2f}R in profit (need 1R)",
                          action="pyramid_insufficient_profit", result="SKIPPED")
                    return None

                # All conditions met — flag as pyramid add
                is_pyramid_add = True
                audit(scan_log,
                      f"Pyramid APPROVED: {idea.asset} {r_achieved:.2f}R profit, conf {idea.confidence:.0%}",
                      action="pyramid_approved", result="APPROVED",
                      data={"r_achieved": round(r_achieved, 2), "confidence": idea.confidence})
            else:
                # ── Opposite direction: NEVER auto-flip ──
                # Don't automatically close and reverse positions.
                # Skip the idea — user must manually close first.
                audit(scan_log, f"Flip BLOCKED: {idea.asset} {pos_dir} -> {idea_dir} (auto-flip disabled)",
                      action="flip_blocked", result="SKIPPED",
                      data={"confidence": idea.confidence, "existing": pos_dir, "proposed": idea_dir})
                return None

        # Store pyramid flag for confirm_trade to apply half-size + SL-to-breakeven
        if is_pyramid_add:
            self._pending_pyramid[idea.id] = True

        # Risk gate — pass ATR so all risk-engine checks run
        # LIVE FIX: pass actual exchange equity so sizing is based on real capital
        live_eq = self._live_balance_cache.get("total", 0.0) if (CONFIG.is_live() and self._live_balance_cache) else None
        # Pass micro-test cap so risk evaluates the actual execution size
        from bot.core.live_executor import MICRO_MAX_POSITION_USD
        exec_cap = MICRO_MAX_POSITION_USD if CONFIG.is_live() else None
        # LIVE FIX: pass live open position count so risk check #5 is accurate
        # CRITICAL: count BOTH filled positions AND pending limit orders.
        # Pending limit orders can fill at any time, so they must count
        # toward the max_open_positions limit. Otherwise auto-confirm can
        # place 20+ limit orders that all fill simultaneously.
        live_open = None
        if CONFIG.is_live():
            try:
                exchange_count = await get_exchange_position_count(self)
                # Add pending (unfilled) limit orders — they occupy margin and
                # will become positions when filled
                pending_count = sum(
                    1 for p in self.live_executor.open_positions
                    if p.status == "pending_fill"
                )
                live_open = exchange_count + pending_count
            except Exception:
                # Fallback: use local state (includes both open + pending_fill)
                live_open = len(self.live_executor.open_positions)
        # N-03 FIX: removed _transition(RISK_CHECK) — runs in parallel, parent manages state
        # Wire order flow signal to risk engine so check #23 (bid dominance) runs
        if of_signal is not None:
            self.risk.set_order_flow_signal(of_signal)
        # Regime-aware sizing (gated): set the analyzer's regime for this symbol
        # so the per-regime multiplier applies. No-op when REGIME_SIZING_ENABLED off.
        self._apply_regime_to(self.risk, idea.asset)
        risk_check = self.risk.evaluate(idea, atr=atr_value, live_equity=live_eq, max_position_usd=exec_cap, live_open_count=live_open)

        # Log risk evaluation to scan log
        audit(scan_log, f"Risk evaluation: {risk_check.verdict.value} for {idea.asset}",
              action="risk_evaluation", result=risk_check.verdict.value,
              data={
                  "asset": idea.asset,
                  "direction": idea.direction.value,
                  "checks_passed": risk_check.checks_passed,
                  "checks_failed": risk_check.checks_failed,
                  "reason": risk_check.reason,
                  "position_size_usd": round(risk_check.position_size_usd, 2),
                  "atr_pct": round((atr_value / idea.entry_price) * 100, 2) if atr_value and idea.entry_price else None,
              })

        # Cross-asset confidence adjustment
        try:
            ca_conf_adj, ca_size_mult = self.cross_asset.get_symbol_adjustment(
                signal.symbol, idea.direction.value)
            if ca_conf_adj != 0:
                # Store for risk engine
                pass
        except Exception:
            pass

        # Check #17: liquidity guard from order flow (fail-open if no data)
        if of_signal is not None:
            liq_size = risk_check.position_size_usd if risk_check else 0.0
            liq_reason = self.order_flow.liquidity_guard(
                of_signal,
                position_size_usd=liq_size,
                symbol=signal.symbol,
            )
            if liq_reason:
                audit(trade_log, f"Trade REJECTED by liquidity guard: {liq_reason}",
                      action="liquidity_guard", result="REJECTED")
                audit(scan_log, f"Liquidity guard rejected {idea.asset}: {liq_reason}",
                      action="liquidity_guard", result="REJECTED",
                      data={
                          "asset": idea.asset,
                          "bid_depth": round(of_signal.bid_depth_usd, 0) if of_signal.bid_depth_usd else 0,
                          "ask_depth": round(of_signal.ask_depth_usd, 0) if of_signal.ask_depth_usd else 0,
                          "spread_bps": round(of_signal.spread_bps, 1) if of_signal.spread_bps else 0,
                          "position_size": round(liq_size, 2),
                      })
                # Shadow-price Check #17 (tuning audit): this return sits
                # BEFORE the risk-rejection shadow-book branch, so the only
                # hard book/depth/spread gate was exempt from the
                # counterfactual ledger built to price gates. Recording-only,
                # fail-open — /shadow now answers whether the guard eats edge.
                try:
                    if getattr(CONFIG, "shadow_book_enabled", False):
                        from bot.core.shadow_book import SHADOW_BOOK
                        _lq_regime = ""
                        try:
                            _lq_r = getattr(self.analyzer, "_current_regimes",
                                            {}).get(idea.asset)
                            _lq_regime = getattr(_lq_r, "value", "") or ""
                        except Exception:
                            _lq_regime = ""
                        SHADOW_BOOK.record_rejection(
                            idea, [f"LIQUIDITY: {liq_reason}"], liq_reason,
                            ref_price=float(getattr(signal, "price", 0) or 0),
                            regime=_lq_regime)
                except Exception as _lq_exc:
                    system_log.debug("shadow book liquidity record skipped: %s", _lq_exc)
                return None

        if risk_check.verdict == RiskVerdict.REJECTED:
            # Store rejection for /whynot command
            symbol_key = idea.asset.replace("/USDT", "").upper()
            self._last_rejections[symbol_key] = {
                "symbol": idea.asset,
                "direction": idea.direction.value,
                "confidence": idea.confidence,
                "entry_price": idea.entry_price,
                "stop_loss": idea.stop_loss,
                "take_profit": idea.take_profit,
                "checks_passed": risk_check.checks_passed,
                "checks_failed": risk_check.checks_failed,
                "reason": risk_check.reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            # Cap stored rejections
            if len(self._last_rejections) > 100:
                oldest_keys = list(self._last_rejections.keys())[:-50]
                for k in oldest_keys:
                    self._last_rejections.pop(k, None)
            audit(
                trade_log,
                f"Trade REJECTED by risk: {risk_check.reason}",
                action="risk_gate",
                result="REJECTED",
            )
            # Learning: log rejected trade decision
            decision = self.learning.log_decision(
                symbol=signal.symbol,
                direction=idea.direction.value,
                confidence=idea.confidence,
                # #35: persist the calibrator's apply-target so it trains on the
                # same field (falls back to confidence when unset).
                blended_confidence_raw=getattr(idea, "blended_confidence_raw", None) or 0.0,
                confluence_score=idea.confidence,
                entry_price=idea.entry_price,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
                risk_reward=idea.risk_reward_ratio,
                position_size_usd=risk_check.position_size_usd,
                risk_engine_result="REJECTED",
                checks_passed=risk_check.checks_passed,
                checks_failed=risk_check.checks_failed,
                rejected_reason=risk_check.reason,
                decision="TRADE_REJECTED_FAIL_CLOSED",
                confluence_votes=getattr(idea, "_confluence_votes", []),
            )
            self.learning.review_rejection(decision)
            # Counterfactual shadow book (recording-only): the rejected idea
            # becomes a paper trade so this gate's decision gets a measured
            # outcome instead of a theoretical one. Fail-open, never trades.
            try:
                if getattr(CONFIG, "shadow_book_enabled", False):
                    from bot.core.shadow_book import SHADOW_BOOK
                    _sb_regime = ""
                    try:
                        _sb_r = getattr(self.analyzer, "_current_regimes",
                                        {}).get(idea.asset)
                        _sb_regime = getattr(_sb_r, "value", "") or ""
                    except Exception:
                        _sb_regime = ""
                    SHADOW_BOOK.record_rejection(
                        idea, risk_check.checks_failed, risk_check.reason,
                        ref_price=float(getattr(signal, "price", 0) or 0),
                        regime=_sb_regime)
            except Exception as _sb_exc:
                system_log.debug("shadow book record skipped: %s", _sb_exc)
            return None

        # N-03 FIX: removed _transition(CONFIRMING) — runs in parallel, parent manages state
        audit(
            trade_log,
            f"Trade idea awaiting human confirmation: {idea.id}",
            action="confirmation_gate",
            result="PENDING",
        )
        # H1: store ATR alongside idea for re-check in confirm_trade
        self._pending_atr[idea.id] = atr_value

        # Entry-timing (auto path): pre-compute the sub-degree confirmation on the
        # idea's own candles so the auto-confirm loop can DEFER an unconfirmed
        # autonomous entry (scoped to ENTRY_TIMING_REGIMES; fail-open on any error).
        try:
            from bot.core.entry_timing import auto_entry_allowed
            _reg = self.analyzer._current_regimes.get(idea.asset)
            _regime = getattr(_reg, "value", "") or ""
            _o = [float(r[1]) for r in ohlcv]
            _h = [float(r[2]) for r in ohlcv]
            _l = [float(r[3]) for r in ohlcv]
            _c = [float(r[4]) for r in ohlcv]
            _dir = getattr(idea.direction, "value", "") or str(idea.direction)
            self._pending_timing[idea.id] = auto_entry_allowed(_regime, _dir, _o, _h, _l, _c)
        except Exception:
            self._pending_timing[idea.id] = (True, "fail-safe")
        if len(self._pending_timing) > 500:  # backstop; ids are unique per idea
            self._pending_timing.clear()

        # MTF entry refinement: zoom into 15m for better entry within zone
        _t0 = time.monotonic()
        _stage_enter_guarded(self, signal.symbol, "refine")
        idea = await self._refine_entry_mtf(idea, exchange)
        self._stage_add("refine", time.monotonic() - _t0)

        return idea

    @staticmethod
    def _human_confirmed(user_id: str) -> bool:
        """RC-AUD-025: True only when the confirmation came from a real human.

        Auto-confirm passes ``user_id="auto"`` and some unattended paths pass
        ``""`` — neither represents a deliberate human button press, so the
        "user already confirmed, proceed anyway" rationale must NOT apply to
        them. A real human confirmation carries a non-empty, non-"auto" id.
        """
        return user_id not in ("", "auto")

    @staticmethod
    def _live_execution_vetoed_by_simulation() -> bool:
        """RC-AUD-018: hard veto on live execution when SIMULATION_MODE is True.

        ``CONFIG.simulation_mode`` is an independent, fail-closed kill switch:
        if it is set, the engine must NEVER place a real order, regardless of
        any runtime flag (e.g. ``RUNTIME.live_mode``) that might otherwise arm
        live mode. Returns True when live execution must be vetoed.
        """
        return bool(CONFIG.simulation_mode)

    async def _simulate_paper_fill(
        self, idea, recheck, user_id: str, trade_id: str
    ) -> str:
        """Open a SIMULATED position in the user's paper portfolio. Pure in-memory
        (no exchange interaction whatsoever) — the per-user sim opt-in path. The
        position is then monitored for SL/TP by the existing paper loop
        (``check_stops_all``). Never calls ``live_executor``.
        """
        size_usd = self._high_conviction_margin(idea, recheck.position_size_usd, user_id)
        try:
            leverage = int(CONFIG.exchange.default_leverage)
        except (TypeError, ValueError):
            leverage = 1
        portfolio = self.user_portfolios.get(user_id)
        try:
            trade = portfolio.open_position(idea, size_usd, leverage=leverage)
        except Exception as exc:
            self._pending_ideas.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"paper fill error {trade_id}")
            return f"⚠️ [PAPER] Simulated fill failed: {str(exc)[:160]}"

        self._pending_ideas.pop(trade_id, None)
        # Log a DECISION row for this paper fill (gated, default OFF) so the
        # confidence-calibration / voter-weight learners can JOIN it to the paper
        # outcome (recorded later by the paper loop) via paper_trade_id and train
        # on paper history. Uses trade.trade_id (== idea.id), the SAME key the
        # outcome row carries. Fail-open. Tagged source="paper_decision" so paper-
        # sourced training data stays auditable/distinguishable from live.
        try:
            if CONFIG.learning.learn_calibration_from_paper_enabled:
                self.learning.log_decision(
                    symbol=idea.asset,
                    direction=idea.direction.value,
                    confidence=idea.confidence,
                    blended_confidence_raw=getattr(idea, "blended_confidence_raw", None) or 0.0,
                    confluence_score=idea.confidence,
                    entry_price=idea.entry_price,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    risk_reward=idea.risk_reward_ratio,
                    position_size_usd=size_usd,
                    risk_engine_result="APPROVED",
                    checks_passed=getattr(recheck, "checks_passed", []),
                    checks_failed=[],
                    decision="TRADE_ACCEPTED_PAPER",
                    paper_trade_id=trade.trade_id,
                    confluence_votes=getattr(idea, "_confluence_votes", []),
                    source="paper_decision",
                )
        except Exception as _pd_exc:
            logger.debug("Paper decision-log skipped: %s", _pd_exc)
        audit(trade_log,
              f"PAPER fill: {idea.direction.value} {idea.asset} @ {idea.entry_price} "
              f"size ${size_usd:.2f} (user {user_id})",
              action="paper_fill", result="FILLED",
              data={"trade_id": trade_id, "asset": idea.asset,
                    "direction": idea.direction.value, "size_usd": round(size_usd, 2),
                    "user_id": user_id, "is_paper": True})
        self._transition(AgentState.IDLE, f"paper filled {trade_id}")
        _dir = idea.direction.value
        return (
            f"📝 <b>[PAPER]</b> Simulated {_dir} <b>{idea.asset}</b>\n"
            f"Entry <code>${idea.entry_price:,.4f}</code> | "
            f"SL <code>${idea.stop_loss:,.4f}</code> | "
            f"TP <code>${idea.take_profit:,.4f}</code>\n"
            f"Size <code>${size_usd:,.2f}</code> @ {leverage}x  •  "
            f"<i>practice mode — no real order placed</i>\n"
            f"Trade ID: <code>{trade.trade_id}</code>"
        )

    async def confirm_trade(self, trade_id: str, user_id: str = "") -> str:
        """Serialize execution per symbol so concurrent/overlapping cycles can't
        double-place the same setup, then delegate to the real logic.

        The duplicate-symbol guard runs at ANALYSIS time; two overlapping
        auto-confirm cycles can both clear it before either order lands (TOCTOU),
        producing two live orders for one signal. A per-symbol lock plus a
        re-check of live open/pending orders here — under the lock — closes that
        window: the second confirmation sees the first's order and is suppressed
        (unless it's a deliberately-flagged pyramid add).
        """
        idea = self._pending_ideas.get(trade_id, None)
        if idea is None:
            return "Trade not found or expired."
        key = normalize_symbol(idea.asset)
        lock = self._symbol_entry_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if (CONFIG.is_live() and hasattr(self, "live_executor")
                    and not self._pending_pyramid.get(trade_id)):
                for lp in self.live_executor.open_positions:  # open + pending_fill
                    if normalize_symbol(lp.symbol) == key:
                        self._pending_ideas.pop(trade_id, None)
                        self._pending_atr.pop(trade_id, None)
                        audit(trade_log,
                              f"Duplicate entry suppressed for {idea.asset} — an "
                              f"open/pending order already exists",
                              action="dup_entry", result="SUPPRESSED",
                              data={"trade_id": trade_id, "symbol": idea.asset})
                        return (f"⏭️ Skipped {idea.asset}: already have an "
                                f"open/pending order for it (duplicate suppressed).")
            return await self._confirm_trade_inner(trade_id, user_id)

    async def _pyramid_move_existing_sl_to_breakeven(
            self, executor, asset: str, new_trade_id: str) -> None:
        """Move the EXISTING same-symbol position's SL to breakeven after a
        pyramid add has filled. Called only from the success branch so a blocked
        or failed add never touches the existing winner's stop. Best-effort: a
        local SL move stands even if the exchange update fails (the monitor
        closes at the new level)."""
        symbol_key = normalize_symbol(asset)
        for lp in executor.open_positions:
            if normalize_symbol(lp.symbol) == symbol_key and lp.trade_id != new_trade_id:
                old_sl = lp.stop_loss
                # Move the LOCAL stop to breakeven only after the exchange confirms
                # it — otherwise the winner would display/enforce breakeven while the
                # exchange still holds the looser original stop (silent over-report of
                # protection that bites during any monitor downtime).
                ok = False
                try:
                    exchange = await executor._get_exchange()
                    ok = await executor._update_exchange_sl(exchange, lp, lp.entry_price)
                except Exception as exc:
                    logger.debug("Failed to update exchange SL to BE: %s", exc)
                if ok:
                    lp.stop_loss = lp.entry_price  # breakeven — exchange confirmed
                    executor._save_positions()
                    audit(trade_log,
                          f"Pyramid: moved {lp.symbol} SL to breakeven ${lp.entry_price:.4f} (was ${old_sl:.4f})",
                          action="pyramid_sl_breakeven", result="MOVED")
                else:
                    audit(trade_log,
                          f"Pyramid: exchange SL-to-breakeven FAILED for {lp.symbol} — local "
                          f"stop preserved at ${old_sl:.4f} (no over-report)",
                          action="pyramid_sl_breakeven", result="EXCHANGE_UPDATE_FAILED",
                          level=logging.WARNING)
                break

    async def _confirm_trade_inner(self, trade_id: str, user_id: str = "") -> str:
        """
        Human confirms a pending trade idea.  This is the ONLY path to execution.
        If user_id is provided, the trade is recorded in that user's isolated portfolio.
        """
        idea = self._pending_ideas.get(trade_id, None)
        if idea is None:
            return "Trade not found or expired."

        # Per-user chosen strategy — a tighten-only veto on THIS user's
        # confirms ("your bot, your strategy"). Applies only to explicit
        # user confirms; the operator auto-loop stays governed by the global
        # stance. An armed selection that cannot be evaluated fails CLOSED —
        # unlike a missing preferences file, which simply means "no
        # selection" (see user_strategy_store's header for the split).
        if user_id and user_id != "auto":
            try:
                from bot.core import strategy_gate, user_strategy_store
                _sel = user_strategy_store.get_entry(user_id)
            except Exception:
                _sel = None
            _skey = _sel if isinstance(_sel, str) else (
                _sel.get("slug") if isinstance(_sel, dict) else None)
            if _sel:
                try:
                    if isinstance(_sel, dict):
                        # A community snapshot — armed from the marketplace.
                        _g = strategy_gate.check_custom(
                            _sel, idea.asset, idea.confidence, idea.direction)
                    else:
                        from bot.skills.skill_registry import RunStrategySkill
                        _preset = RunStrategySkill.PRESETS.get(_skey)
                        _g = strategy_gate.check_confirm(
                            _skey, _preset, idea.asset, idea.confidence)
                except Exception as _exc:
                    _g = {"ok": False, "reason": (
                        f"Your chosen strategy '{_skey}' could not be evaluated — "
                        "confirms are refused until it can be (fail closed). "
                        "/mystrategy off disarms it.")}
                    logger.warning("strategy gate error for %s: %s", user_id, _exc)
                if not _g.get("ok"):
                    audit(trade_log,
                          f"Strategy-gate REFUSED {idea.asset} for user {user_id}: "
                          f"{_g.get('reason', '')}",
                          action="user_strategy_gate", result="REFUSED",
                          data={"trade_id": trade_id, "strategy": _skey})
                    return f"\U0001f6e1 {_g.get('reason', 'Refused by your chosen strategy.')}"

        # Store for marketing forwarder access
        self._last_confirmed_idea = idea

        # H1 fix: re-check with stored ATR so volatility guard runs
        stored_atr = self._pending_atr.get(trade_id, None)

        # F-05 FIX: reject if market price has drifted significantly from
        # the idea's entry price. Prevents executing at stale levels.
        # Skip price drift check for manual trades — user specified exact entry
        is_manual = getattr(idea, 'source', '') == 'manual'

        # Synthetic-ATR fallback (generalized 2026-07-21, live "can't open
        # trades" incident). A pending idea sometimes reaches the re-check with
        # stored_atr None — a manual /trade never populates _pending_atr, and a
        # scan idea's cached ATR can expire between the card and the tap. The
        # volatility guard then fail-closes ("VOLATILITY: ATR data unavailable")
        # and REJECTS a trade that carries a perfectly good stop (GOOGL SHORT,
        # 2026-07-21). Derive the ATR from the SL distance — the stop that is
        # ALREADY the risk backstop — so the guard evaluates a real number
        # instead of rejecting. The guard still runs: an insanely wide stop
        # still trips the ATR% ceiling. Applies to any trade with valid levels,
        # not just manual ones. (Mirrors the executor-stage fallback below.)
        if (not stored_atr or stored_atr <= 0):
            _sl = getattr(idea, 'stop_loss', 0) or 0
            if idea.entry_price > 0 and _sl > 0:
                stored_atr = abs(idea.entry_price - _sl)
                audit(trade_log,
                      f"Synthetic ATR={stored_atr:.4f} from SL distance (pre-recheck, "
                      f"{'manual' if is_manual else 'signal'})",
                      action="synthetic_atr_from_sl", result="OK")

        try:
            idea_category = _classify_symbol(idea.asset)
            exchange = await self.get_exchange(idea_category)
            ticker = await exchange.fetch_ticker(idea.asset)
            current_price = float(ticker.get("last") or 0)
            if current_price > 0 and idea.entry_price > 0:
                drift_pct = abs(current_price - idea.entry_price) / idea.entry_price * 100
                max_drift = 2.0  # reject if price moved more than 2%
                is_limit = getattr(idea, 'order_type', '') == 'limit'
                if not is_manual and not is_limit and drift_pct > max_drift:
                    audit(trade_log,
                          f"Price drift {drift_pct:.2f}% exceeds {max_drift}% threshold",
                          action="price_drift", result="REJECTED",
                          data={"trade_id": trade_id, "asset": idea.asset,
                                "idea_entry": idea.entry_price,
                                "current_price": current_price,
                                "drift_pct": round(drift_pct, 2)})
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price drift for {trade_id}")
                    return (f"Trade REJECTED: price drifted {drift_pct:.1f}% since analysis "
                            f"(${idea.entry_price:,.2f} → ${current_price:,.2f}). Re-analyze.")

                # ── Validate price hasn't already blown through SL ──
                # If market price is already past the SL, the trade would be
                # instantly stopped out. Reject before wasting an execution.
                if idea.direction.value == "LONG" and current_price <= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already below "
                            f"SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")
                elif idea.direction.value == "SHORT" and current_price >= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already above "
                            f"SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")

                # ── Validate remaining R:R hasn't deteriorated ──
                # If price has eaten more than 50% of the SL distance, the setup
                # no longer offers a favorable risk:reward. Reject stale signals.
                # Skip for manual trades and limit orders — user chose these exact levels.
                if not is_manual and not is_limit:
                    sl_dist = abs(idea.entry_price - idea.stop_loss)
                    if sl_dist > 0:
                        if idea.direction.value == "LONG":
                            consumed = max(0, idea.entry_price - current_price)
                        else:
                            consumed = max(0, current_price - idea.entry_price)
                        consumed_pct = consumed / sl_dist
                        if consumed_pct > 0.5:
                            self._pending_pyramid.pop(trade_id, None)
                            self._transition(AgentState.IDLE, f"R:R deteriorated for {trade_id}")
                            return (f"Trade REJECTED: price moved {consumed_pct:.0%} toward SL "
                                    f"(${current_price:,.4f} vs entry ${idea.entry_price:,.4f}). "
                                    f"R:R no longer favorable — re-analyze.")
        except Exception as exc:
            # H-08 FIX: fail-closed — reject if exchange is unreachable
            audit(trade_log, f"Price drift check failed (rejecting): {exc}",
                  action="price_drift", result="REJECTED")
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"price drift check failed for {trade_id}")
            return "Trade REJECTED: unable to verify current price. Try again."

        # ── Limit order price recalculation at confirm time ──
        # When order_type is "limit", the idea.entry_price was set at analysis time.
        # Only recalculate if the limit price would cause an instant fill:
        #   - LONG buy limit ABOVE current price fills immediately
        #   - SHORT sell limit BELOW current price fills immediately
        # If the limit price is already on the correct side, keep it.
        if idea.order_type == "limit" and current_price > 0 and stored_atr and stored_atr > 0:
            _needs_recalc = False
            if idea.direction.value == "LONG" and idea.entry_price >= current_price:
                _needs_recalc = True
            elif idea.direction.value != "LONG" and idea.entry_price <= current_price:
                _needs_recalc = True

            if _needs_recalc:
                # Use 0.5*ATR offset (not 0.1) so the limit is far enough from
                # current price to actually rest on the book as a maker order.
                offset = 0.5 * stored_atr
                if idea.direction.value == "LONG":
                    new_limit = round(current_price - offset, 8)
                    # Also update SL/TP relative to new entry
                    sl_dist = abs(idea.entry_price - idea.stop_loss)
                    tp_dist = abs(idea.take_profit - idea.entry_price)
                    new_sl = round(new_limit - sl_dist, 8)
                    new_tp = round(new_limit + tp_dist, 8)
                else:
                    new_limit = round(current_price + offset, 8)
                    sl_dist = abs(idea.stop_loss - idea.entry_price)
                    tp_dist = abs(idea.entry_price - idea.take_profit)
                    new_sl = round(new_limit + sl_dist, 8)
                    new_tp = round(new_limit - tp_dist, 8)

                old_entry = idea.entry_price
                idea = idea.model_copy(update={
                    "entry_price": new_limit,
                    "stop_loss": new_sl,
                    "take_profit": new_tp,
                })
                audit(trade_log,
                      f"Limit price recalculated at confirm: ${old_entry:,.4f} → ${new_limit:,.4f} "
                      f"(market=${current_price:,.4f}, offset={offset:.4f})",
                      action="limit_price_update", result="UPDATED",
                      data={"old_entry": old_entry, "new_entry": new_limit,
                            "current_price": current_price, "offset": offset,
                            "new_sl": new_sl, "new_tp": new_tp})

                # ── RC-AUD-010: re-validate the NEW levels after recalc ──
                # The drift / past-SL / R:R guards above ran against the OLD
                # levels and are skipped for limit orders. Now that the entry
                # was repriced to current ± 0.5*ATR (with SL/TP rederived from
                # the original distances), re-affirm the new SL is sane and
                # that current price has not already blown through the new SL,
                # mirroring the "price past SL" check earlier in this function.
                if idea.stop_loss == idea.entry_price:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"recalc SL==entry for {trade_id}")
                    return (f"Trade REJECTED: recalculated SL ${idea.stop_loss:,.4f} equals "
                            f"entry — cannot compute safe stop distance.")
                if idea.direction.value == "LONG" and current_price <= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past new SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already below "
                            f"recalculated SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")
                elif idea.direction.value == "SHORT" and current_price >= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past new SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already above "
                            f"recalculated SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")

        # STALE_DATA fix: idea.timestamp was stamped when this symbol's analysis
        # COMPLETED during the scan. A wide (~200-symbol) scan plus the human's
        # confirm delay can push that past the staleness window even though we
        # just re-fetched a LIVE price above (the drift / past-SL / R:R guards,
        # which fail-closed and return "unable to verify current price" if the
        # exchange is unreachable). Refresh the timestamp to this confirm-time
        # re-validation so the STALE_DATA guard measures freshness from NOW, not
        # from scan time. Gated on a successful live fetch (current_price > 0) so
        # it can NEVER mask a stale/unreachable exchange — that path already
        # returned above. The guard stays fully active for the autonomous /
        # auto-confirm paths, which do not re-fetch a live price here.
        if current_price > 0:
            idea = idea.model_copy(update={"timestamp": datetime.now(UTC)})

        # Re-check risk (portfolio state may have changed -- new positions, daily PnL, drawdown.
        # HONEST LIMITATION: price drift is now checked above (F-05 fix).
        # Stale-data check #12 guards against time drift (>300s = reject).
        self._transition(AgentState.RISK_CHECK, f"re-checking risk for {trade_id}")
        try:
            from bot.core.live_executor import MICRO_MAX_POSITION_USD
            recheck_cap = MICRO_MAX_POSITION_USD if CONFIG.is_live() else None
            # Per-user margin cap (operator-set, tighten-only): a regular user's
            # live trade is capped at THEIR ceiling, never above the global micro
            # cap. None when unset / operator / per-user off → no change.
            _user_cap = self._per_user_margin_cap(user_id)
            if _user_cap is not None:
                recheck_cap = _user_cap if recheck_cap is None else min(recheck_cap, _user_cap)
            # LIVE FIX: size + count against the account this confirm executes on.
            # Default/operator → the shared operator balance + exchange count
            # (byte-identical). A regular user under per-user live → THEIR OWN
            # account's equity + open-position count, so they are never sized
            # against the operator's (much larger) balance.
            live_eq_recheck, live_open_recheck = await self._live_recheck_context(user_id)
            # Per-user risk isolation: this confirm-time gate runs against the
            # engine that owns THIS user's breaker/streak/daily-loss/drawdown
            # state. Default (per-user OFF) → shared operator engine, unchanged.
            recheck_engine = self.risk_for(user_id)
            # Regime-aware sizing (gated): set regime AFTER risk_for (whose market-
            # context sync may have copied the shared engine's regime) so this
            # idea's symbol regime is authoritative for the executed-size recheck.
            self._apply_regime_to(recheck_engine, idea.asset)
            recheck = recheck_engine.evaluate(idea, atr=stored_atr, live_equity=live_eq_recheck, max_position_usd=recheck_cap, live_open_count=live_open_recheck)
        except Exception as exc:
            # Fix 6: if re-check raises, do NOT silently lose the idea.
            # Log it as a failed re-check and return a clear message.
            audit(
                trade_log,
                f"Risk re-check crashed for {trade_id}: {exc}",
                action="recheck",
                result="ERROR",
                data={"trade_id": trade_id, "asset": idea.asset, "error": str(exc)},
            )
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"re-check error for {trade_id}")
            return f"Trade REJECTED: re-check failed (error logged): {exc}"
        if recheck.verdict == RiskVerdict.REJECTED:
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"re-check rejected {trade_id}")
            # Seal rejection to audit chain (Guardian Flight Recorder: seal the
            # full provenance so a rejection is as explainable as an execution).
            self.audit_chain.seal_decision(DecisionRecord(
                decision_id=trade_id, symbol=idea.asset,
                idea=_flight_idea(idea),
                risk=_flight_risk(recheck),
                outcome="REJECTED_ON_RECHECK", is_paper=not CONFIG.is_live(),
            ))
            self._emit_policy_decision(recheck, trade_id, idea.asset, user_id)
            self._sync_flight_records()
            return f"Trade REJECTED on re-check: {recheck.reason}"

        # Adversarial self-critique gate (fail-open: errors = proceed with warning)
        try:
            from bot.core.critique import TradeCritique
            critique = TradeCritique()
            snapshot = self.user_portfolios.combined_snapshot() if self.user_portfolios.all_portfolios() else self.portfolio.snapshot()
            macro_ctx_for_critique = self.macro_provider.get_context(symbol=idea.asset)
            critique_result = critique.evaluate(idea, recheck, snapshot, macro_ctx_for_critique)

            if critique_result.verdict == "HALT":
                self.audit_chain.append("CRITIQUE_HALT", {
                    "trade_id": trade_id, "asset": idea.asset,
                    "bear_case": critique_result.bear_case,
                    "concerns": critique_result.concerns,
                    "confidence_adjustment": critique_result.confidence_adjustment,
                })
                self._pending_pyramid.pop(trade_id, None)
                self._transition(AgentState.IDLE, f"critique halted {trade_id}")
                return f"Trade HALTED by adversarial review: {critique_result.bear_case}\nConcerns: {'; '.join(critique_result.concerns)}"
            # Apply critique confidence adjustment
            if critique_result.confidence_adjustment != 0:
                idea.confidence = max(0.0, min(1.0, idea.confidence + critique_result.confidence_adjustment))
                audit(trade_log, f"Critique adjusted confidence by {critique_result.confidence_adjustment:+.2f} to {idea.confidence:.3f}",
                      action="critique_adjust", result="ADJUSTED",
                      data={"adjustment": critique_result.confidence_adjustment, "new_confidence": idea.confidence})
                if idea.confidence < CONFIG.risk.min_confidence:
                    # RC-AUD-025: the "user already confirmed, proceed anyway"
                    # rationale only holds when a REAL human pressed Confirm.
                    # Auto-confirm (user_id="auto") and unattended ("") paths
                    # have no deliberate human decision, so a post-critique
                    # sub-min-confidence result must REJECT for them instead of
                    # proceeding.
                    if self._human_confirmed(user_id):
                        # Human made a deliberate decision — warn but proceed.
                        audit(trade_log,
                              f"Post-critique confidence {idea.confidence:.2f} below min {CONFIG.risk.min_confidence} "
                              f"— proceeding anyway (human-confirmed trade via confirm_trade)",
                              action="critique_adjust", result="WARN_OVERRIDE",
                              data={"confidence": idea.confidence, "min": CONFIG.risk.min_confidence,
                                    "user_id": user_id,
                                    "source": getattr(idea, 'source', 'unknown')})
                    else:
                        audit(trade_log,
                              f"Post-critique confidence {idea.confidence:.2f} below min {CONFIG.risk.min_confidence} "
                              f"— REJECTING (not human-confirmed; user_id={user_id!r})",
                              action="critique_adjust", result="REJECT",
                              data={"confidence": idea.confidence, "min": CONFIG.risk.min_confidence,
                                    "user_id": user_id,
                                    "source": getattr(idea, 'source', 'unknown')})
                        self._pending_pyramid.pop(trade_id, None)
                        self._transition(AgentState.IDLE, f"critique sub-min (auto) {trade_id}")
                        return (f"Trade REJECTED: post-critique confidence "
                                f"{idea.confidence:.2f} below minimum "
                                f"{CONFIG.risk.min_confidence} (auto-confirm not permitted "
                                f"to override).")

            if critique_result.verdict == "WARN":
                audit(trade_log, f"Critique WARNING for {trade_id}: {critique_result.bear_case}",
                      action="critique", result="WARN",
                      data={"concerns": critique_result.concerns})
        except Exception as exc:
            # Audit F-13: the critique is the strongest discretionary brake. In
            # paper mode a crash can fail-open (advisory). In LIVE mode a crash
            # must fail CLOSED — a malformed idea/snapshot that crashes the
            # bear-case review should not silently disable it before a real order.
            if CONFIG.is_live():
                audit(trade_log, f"Critique gate error (fail-CLOSED in LIVE): {exc}",
                      action="critique", result="ERROR_FAILCLOSED",
                      data={"trade_id": trade_id, "error": str(exc)[:200]})
                self._pending_pyramid.pop(trade_id, None)
                self._transition(AgentState.IDLE, f"critique error (live) {trade_id}")
                return ("Trade REJECTED: adversarial critique could not complete "
                        "and live mode fails closed on critique errors.")
            audit(trade_log, f"Critique gate error (fail-open): {exc}",
                  action="critique", result="ERROR")

        # Compliance gate: authorize before execution
        action = Permission.LIVE_TRADE if CONFIG.is_live() else Permission.PAPER_TRADE
        macro_ctx = self.macro_provider.get_context(symbol=idea.asset)
        macro_ok = macro_ctx.risk_state != "BLOCK_NEW_ENTRIES"

        # Issue a human-approval token for live-mode compliance (Lock 5).
        # The Telegram /confirm flow is the human approval gate — reaching
        # this point means the operator already tapped "Confirm".
        approval_token = None
        if CONFIG.is_live():
            human = self._human_confirmed(user_id)
            # Audit F-8: only mint the Lock 5 human-approval token for a REAL
            # human confirmation. For non-human callers (user_id "" / "auto" —
            # e.g. auto-confirm or a skill dispatch) require the explicit
            # AUTO_CONFIRM_LIVE_ENABLED opt-in; otherwise leave the token
            # unminted so compliance Lock 5 fails CLOSED and the live trade is
            # denied rather than executed with no human approval at all.
            if human or CONFIG.auto_confirm_live_enabled:
                approval_token = self.compliance.issue_approval_token(
                    trade_id, self.compliance_profile.subject_id,
                )
                if not human:
                    # RC-AUD-018: unattended live execution explicitly opted in.
                    system_log.warning(
                        "AUTO-MINT APPROVAL TOKEN (RC-AUD-018): engine minted the "
                        "Lock 5 token for UNATTENDED trade %s (user_id=%r) under "
                        "AUTO_CONFIRM_LIVE_ENABLED — no human callback occurred.",
                        trade_id, user_id,
                    )
            else:
                system_log.warning(
                    "Lock 5 NOT minted for non-human confirm of %s (user_id=%r) "
                    "and AUTO_CONFIRM_LIVE_ENABLED is off — live execution will be "
                    "denied (audit F-8).", trade_id, user_id,
                )

        compliance_decision = self.compliance.authorize(
            action=action,
            profile=self.compliance_profile,
            live_mode=CONFIG.is_live(),
            risk_passed=(recheck.verdict == RiskVerdict.APPROVED),
            macro_ok=macro_ok,
            notional_usd=recheck.position_size_usd,
            trade_id=trade_id,
            approval_token=approval_token,
        )
        if not compliance_decision.granted:
            self.audit_chain.append("AUTH_DENIED", {
                "trade_id": trade_id, "asset": idea.asset,
                "reasons": compliance_decision.reasons,
                "locks_failed": compliance_decision.locks_failed,
            }, actor=self.compliance_profile.subject_id)
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"compliance denied {trade_id}")
            return f"Execution denied: {compliance_decision.reasons[-1] if compliance_decision.reasons else 'compliance check failed'}"

        # ── Per-user PAPER (sim) opt-in ──────────────────────────────────────
        # A user who has opted into practice mode (and the feature is enabled)
        # has THEIR confirmed trade SIMULATED into their paper portfolio instead
        # of sent to the exchange. This branch runs BEFORE the EXECUTING
        # transition, live_executor.execute(), and the post-fill pyramid SL move
        # (which mutates an exchange stop) — so a paper trade can NEVER place or
        # modify a real order. Default OFF and per-user, so live users unaffected.
        if (CONFIG.paper_sim_opt_in_enabled and user_id
                and self._user_store is not None
                and self._user_store.sim_opt_in(user_id)):
            self._pending_pyramid.pop(trade_id, None)
            return await self._simulate_paper_fill(idea, recheck, user_id, trade_id)

        # LIVE-ONLY: this bot only executes live trades. Paper mode is disabled.
        if not CONFIG.is_live():
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, "paper mode disabled")
            return "⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY."

        # ── RC-AUD-018 / Audit F-14: SIMULATION_MODE hard veto ──
        # Final, independent fail-closed gate. It must run BEFORE the EXECUTING
        # transition and before any exchange-mutating side-effect (execute() and
        # the post-fill pyramid SL→breakeven move on a *different* live position).
        # Previously the veto sat just before execute(), so a vetoed confirm could
        # still have modified another position's stop on the exchange. This guard
        # never enables execution — it only ever blocks it.
        if self._live_execution_vetoed_by_simulation():
            self._pending_pyramid.pop(trade_id, None)
            audit(trade_log,
                  f"Live execution VETOED by SIMULATION_MODE for {trade_id}",
                  action="confirm", result="VETO_SIMULATION",
                  data={"trade_id": trade_id, "asset": idea.asset})
            self._transition(AgentState.IDLE, f"simulation hard veto {trade_id}")
            return ("Trade REJECTED: SIMULATION_MODE=true — live execution "
                    "vetoed (hard safety switch).")

        # ── Per-user eligibility gate ────────────────────────────────────────
        # When per-user live trading is ON, a regular (non-operator) human user
        # may only place a live order on THEIR OWN linked account. If they have
        # not linked keys, REJECT here — never silently route their trade to the
        # operator account. No-op while the flag is off, and operator/admin/auto
        # paths always pass, so the operator path is unchanged.
        _elig_ok, _elig_reason = self.per_user_live_eligibility(user_id)
        if not _elig_ok:
            self._pending_pyramid.pop(trade_id, None)
            audit(trade_log,
                  f"Live execution blocked — per-user eligibility: {_elig_reason}",
                  action="confirm", result="REJECT_NOT_ELIGIBLE",
                  data={"trade_id": trade_id, "user_id": user_id, "reason": _elig_reason})
            self._transition(AgentState.IDLE, f"not eligible {trade_id}")
            return (f"Trade REJECTED: {_elig_reason}. Your live trades execute on "
                    "your OWN Bitget account — link it with /connect first.")

        # Live mode — execute via LiveExecutor with micro-test safety limits
        self._transition(AgentState.EXECUTING, f"executing LIVE trade {trade_id}")
        # Flat margin for high-conviction ideas. A TARGET only: every
        # reducer below still applies and can only lower it.
        size_usd = self._high_conviction_margin(idea, recheck.position_size_usd, user_id)

        # Resolve WHICH executor places this order. With PER_USER_LIVE_ENABLED off
        # (default) this is always the shared operator executor, so everything
        # below is byte-identical to before. With it on, a human user's confirmed
        # trade routes to THEIR own linked account.
        executor = self._executor_for(user_id)

        # ── Pyramid add: half size now; the existing winner's SL is moved to
        # breakeven ONLY after this add actually fills (see the success branch
        # below). Moving it here — BEFORE execute() — left the existing position
        # damaged at breakeven whenever the add was blocked (the executor's
        # duplicate-symbol preflight blocks a same-symbol add) with no rollback.
        _is_pyramid_add = False
        _pending_pyramid = getattr(self, '_pending_pyramid', {})
        if _pending_pyramid.pop(trade_id, False):
            _is_pyramid_add = True
            original_size = size_usd
            size_usd = size_usd * 0.5
            audit(trade_log,
                  f"Pyramid add: half size ${original_size:.2f} -> ${size_usd:.2f}",
                  action="pyramid_half_size", result="APPLIED")

        # Universe expansion: session-aware sizing for US-equity perps.
        # Tokenized stock/ETF/pre-IPO perps trade 24/7 on the venue but track
        # an underlying that doesn't — off-hours books are thin and weekend/
        # overnight gaps blow through stops. StockTradingConfig defined these
        # controls but get_stock_risk_params was never imported anywhere;
        # this wires the session multiplier into the money path.
        try:
            from bot.core.market_scanner import category_for_symbol
            _cat = category_for_symbol(idea.asset)
        except Exception:
            _cat = "Crypto"
        if _cat in ("Stock", "ETF", "Pre-IPO"):
            from bot.core.stock_trading import get_market_session
            _sess = get_market_session()
            if _sess.size_multiplier <= 0.0:
                audit(trade_log,
                      f"BLOCKED: {idea.asset} — US markets {_sess.session_name} "
                      f"(opens in {_sess.hours_until_open:.1f}h) and "
                      f"STOCK_BLOCK_OUTSIDE_HOURS is on",
                      action="stock_session_gate", result="BLOCKED",
                      data={"symbol": idea.asset, "session": _sess.session_name})
                self._pending_pyramid.pop(trade_id, None)
                self._transition(AgentState.IDLE, f"stock session gate {trade_id}")
                return (f"Trade REJECTED: US markets are {_sess.session_name} "
                        f"(open in {_sess.hours_until_open:.1f}h) — equity perp "
                        f"entries are blocked outside regular hours.")
            if _sess.size_multiplier < 1.0:
                _orig = size_usd
                size_usd = round(size_usd * _sess.size_multiplier, 2)
                audit(trade_log,
                      f"Stock session sizing: {idea.asset} {_sess.session_name} "
                      f"x{_sess.size_multiplier:.2f} — ${_orig:.2f} -> ${size_usd:.2f}",
                      action="stock_session_sizing", result="REDUCED",
                      data={"symbol": idea.asset, "session": _sess.session_name,
                            "multiplier": _sess.size_multiplier,
                            "from": round(_orig, 2), "to": round(size_usd, 2)})

        # LIVE FIX: Cap position size at actual exchange equity to prevent
        # InsufficientFunds errors.  The risk engine sizes based on paper
        # portfolio equity; in LIVE mode the real account may be smaller.
        #
        # C2 FIX (HIGH): clamp against the EXECUTING account's free balance, not
        # the operator's. This used self._live_balance_cache — always the shared
        # operator account — so under per-user live a user's order was sized
        # against the OPERATOR's margin (too loose → InsufficientFunds on their
        # smaller account, or too tight). get_user_live_equity resolves to the
        # operator balance for the operator/non-per-user paths (byte-identical)
        # and to the user's OWN linked account otherwise. Fail-safe: returns None
        # on fetch failure, so the clamp is simply skipped (as before an empty
        # cache), never sized against the wrong account.
        live_bal = await self.get_user_live_equity(user_id)
        if live_bal:
            available = live_bal.get("free", 0.0)
            if size_usd > available:
                audit(trade_log,
                      f"Live size clamped: ${size_usd:.2f} -> ${available:.2f} (exchange available)",
                      action="live_size_clamp", result="CLAMPED",
                      data={"requested": round(size_usd, 2), "available": round(available, 2),
                            "user_id": user_id})
                size_usd = available

        # Manual margin override: if user specified a fixed margin via /trade command.
        # Audit V7 follow-up (double-leverage fix): size_usd is MARGIN everywhere —
        # the live executor multiplies it by leverage to get notional
        # (quantity = size_usd * leverage / price). The old code pre-multiplied
        # here (size_usd = margin * leverage) and the executor multiplied AGAIN,
        # placing margin * leverage**2 notional (e.g. /trade margin 250 at 5x put
        # on $6,250 instead of $1,250). Pass the margin itself so manual trades
        # match the auto path and the user's stated margin.
        if hasattr(self, '_manual_margin_override') and idea.id in self._manual_margin_override:
            manual_margin = self._manual_margin_override.pop(idea.id)
            leverage = CONFIG.exchange.default_leverage
            size_usd = manual_margin  # margin; executor applies leverage for notional
            audit(system_log,
                  f"Manual margin override: ${manual_margin:.2f} margin "
                  f"(≈${manual_margin * leverage:.2f} notional at {leverage}x)",
                  action="manual_margin_override", result="APPLIED",
                  data={"margin": round(manual_margin, 2), "leverage": leverage,
                        "approx_notional": round(manual_margin * leverage, 2)})
            # C5 FIX: the manual override was applied AFTER the per-user cap and the
            # free-margin clamp above, so a manual /trade could oversize past a
            # user's ceiling or their account's available margin. Re-apply both
            # guards to the manual value (tighten-only; never raises size).
            _cap = self._per_user_margin_cap(user_id)
            if _cap is not None and size_usd > _cap:
                audit(trade_log,
                      f"Manual margin capped to per-user ceiling: "
                      f"${size_usd:.2f} -> ${_cap:.2f}",
                      action="manual_margin_cap", result="CAPPED",
                      data={"requested": round(size_usd, 2), "cap": round(_cap, 2),
                            "user_id": user_id})
                size_usd = _cap
            if live_bal:
                _avail = live_bal.get("free", 0.0)
                if size_usd > _avail:
                    audit(trade_log,
                          f"Manual margin clamped to free balance: "
                          f"${size_usd:.2f} -> ${_avail:.2f}",
                          action="manual_margin_clamp", result="CLAMPED",
                          data={"requested": round(size_usd, 2),
                                "available": round(_avail, 2), "user_id": user_id})
                    size_usd = _avail

        # C2-53 FIX: Reject trade when ATR is missing or zero.
        # A zero ATR produces SL at entry price = immediate stop-out.
        # Skip ATR check for manual trades — user provided explicit SL/TP levels.
        if getattr(idea, 'source', '') == 'manual':
            if not stored_atr or stored_atr <= 0:
                # Use a synthetic ATR based on SL distance so executor can function
                stored_atr = abs(idea.entry_price - idea.stop_loss)
                audit(trade_log,
                      f"Manual trade: synthetic ATR={stored_atr:.4f} from SL distance",
                      action="manual_atr_synthetic", result="OK")
        elif not stored_atr or stored_atr <= 0:
            audit(trade_log,
                  f"No valid ATR for {idea.asset} — aborting to avoid SL-at-entry",
                  action="confirm", result="REJECT",
                  data={"trade_id": trade_id, "stored_atr": stored_atr})
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"no ATR for {trade_id}")
            return "Trade REJECTED: no valid ATR available — cannot compute safe SL distance"

        # Kill-switch fail-closed re-check. With concurrent updates, /halt
        # (emergency_halt_all) or a circuit-breaker trip can land AFTER this
        # confirm passed its risk gate above but BEFORE the order is placed.
        # Re-check here — under the per-symbol entry lock — so no position can
        # survive the kill switch. (This is the whole reason concurrent_updates
        # is safe on the money path.)
        #
        # C4: the last-mile re-check must consult the EXECUTING account's engine,
        # not only the shared operator's. A per-user breaker/kill that trips in
        # this race window (e.g. that user hit their own daily-loss limit) only
        # opens THEIR engine's breaker; checking self.risk alone would miss it and
        # place the order. risk_for(user_id) is the shared engine for the
        # operator/default path (byte-identical) and the user's own engine
        # otherwise.
        _user_breaker = False
        try:
            _user_breaker = self.risk_for(user_id).circuit_breaker_active
        except Exception:
            _user_breaker = self.risk.circuit_breaker_active  # fail-closed to shared
        if self._halted or self.risk.circuit_breaker_active or _user_breaker:
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"halted before execute {trade_id}")
            return "Trade REJECTED: engine halted (kill-switch) before execution."

        # Auth fail-closed: never OPEN a live position on an account whose venue
        # authentication is known-broken (missing passphrase, Bitget 40006/40012)
        # — it could not place the protective stop, exactly the naked-position
        # failure mode. This blocks NEW entries only; open positions keep being
        # monitored and closed. Cleared automatically when the next preflight /
        # auth probe confirms auth is healthy again.
        if CONFIG.is_live() and not self.live_auth_healthy(user_id):
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"auth-halt before execute {trade_id}")
            _detail = self._live_auth_detail.get(str(user_id or ""), "")
            return ("Trade REJECTED: exchange authentication is failing"
                    + (f" ({_detail})" if _detail else "")
                    + " — new live entries are halted until the credentials are "
                    "fixed. Open positions are still monitored.")

        result = await executor.execute(
            idea, size_usd,
            order_type=idea.order_type,
            atr_value=stored_atr,
        )

        # Only record the trade if a LIVE position actually resulted.
        # Audit F-1: classification is centralized in live_executor next to the
        # return strings so the two cannot drift. The old prefix list here
        # missed "REFUSED:" / "EXECUTION BLOCKED:" / "Live execution blocked:"
        # and could not match emoji/HTML-prefixed strings, so blocked trades
        # were sealed to the audit chain as phantom live fills.
        from bot.core.live_executor import execution_indicates_failure
        live_failed = execution_indicates_failure(result)

        if not live_failed:
            # Exchange is single source of truth — no paper duplicate.
            # Position count comes from get_exchange_position_count().
            invalidate_position_count_cache()
            # Re-entry cooldown: stamp the real fill on the SAME engine that
            # evaluates this user's next trade (risk_for routes per-user; shared
            # operator engine in default mode). No-op when the flag is off.
            try:
                self.risk_for(user_id).note_symbol_entry(idea.asset)
            except Exception:
                pass
            # C-05 FIX: only remove idea and ATR after successful execution
            self._pending_ideas.pop(trade_id, None)
            self._pending_atr.pop(trade_id, None)
            # Public mind-stream: operator-account opens only (per-user
            # executors are private). No sizes on the public feed.
            try:
                from bot.core.agent_feed import FEED
                if executor is getattr(self, "live_executor", None):
                    _fdir = idea.direction.value
                    FEED.emit(
                        "trade_open",
                        f"Opened {_fdir} {idea.asset}",
                        body=(f"Entry ${idea.entry_price:,.4f} · "
                              f"SL ${idea.stop_loss:,.4f} · "
                              f"TP ${idea.take_profit:,.4f}"),
                        symbol=idea.asset, severity="success",
                        data={"direction": _fdir,
                              "confidence": round(float(idea.confidence), 3)})
            except Exception as _feed_exc:
                logger.debug("Agent feed open event skipped: %s", _feed_exc)
            # Push the new live position to the website immediately. Before
            # this, live state only synced on CLOSE — an open position sat
            # invisible on the web dashboard (while Telegram showed it) until
            # the trade finished.
            try:
                if executor is getattr(self, "live_executor", None):
                    self._sync_live_state_to_website()
            except Exception as _sync_exc:
                logger.debug("Live website sync skipped: %s", _sync_exc)
            # Pyramid add filled — NOW move the existing position's SL to breakeven
            # (deferred from before execute() so a blocked/failed add never leaves
            # the existing winner sitting at breakeven with no rollback).
            if _is_pyramid_add:
                await self._pyramid_move_existing_sl_to_breakeven(
                    executor, idea.asset, trade_id)
            # Cache VWAP at entry for VWAP reversion exit monitoring
            if hasattr(idea, 'signal_type') and idea.signal_type == "vwap_reversion":
                # Extract VWAP from the idea's signals_used/indicators (stored at analysis time)
                entry_vwap = getattr(idea, '_entry_vwap', None) or idea.entry_price
                self._last_vwap[idea.asset] = entry_vwap

        # Seal decision to tamper-evident audit chain (Guardian Flight Recorder:
        # provenance-complete idea/risk — votes, model/prompt version, and the
        # explainability slice — so every executed decision is fully auditable).
        self.audit_chain.seal_decision(DecisionRecord(
            decision_id=trade_id, symbol=idea.asset,
            idea=_flight_idea(idea),
            risk=_flight_risk(recheck, size_usd=size_usd),
            macro={"risk_state": macro_ctx.risk_state, "multiplier": macro_ctx.size_multiplier},
            compliance={"granted": True, "locks_passed": compliance_decision.locks_passed},
            outcome="EXECUTED_LIVE", is_paper=False,
        ))
        self._emit_policy_decision(recheck, trade_id, idea.asset, user_id)
        self._sync_flight_records()
        # Learning: log accepted trade decision
        self.learning.log_decision(
            symbol=idea.asset,
            direction=idea.direction.value,
            confidence=idea.confidence,
            # #35: persist the calibrator's apply-target so it trains on the same
            # field (falls back to confidence when unset).
            blended_confidence_raw=getattr(idea, "blended_confidence_raw", None) or 0.0,
            confluence_score=idea.confidence,
            entry_price=idea.entry_price,
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            risk_reward=idea.risk_reward_ratio,
            position_size_usd=size_usd,
            risk_engine_result="APPROVED",
            checks_passed=recheck.checks_passed,
            checks_failed=[],
            decision="TRADE_ACCEPTED_LIVE",
            paper_trade_id=trade_id,
            confluence_votes=getattr(idea, "_confluence_votes", []),
        )
        self._transition(AgentState.IDLE, "live trade executed")
        return result

    def reject_trade(self, trade_id: str) -> str:
        """Human explicitly rejects a pending idea."""
        idea = self._pending_ideas.pop(trade_id, None)
        self._pending_atr.pop(trade_id, None)  # clean up stored ATR
        self._pending_pyramid.pop(trade_id, None)  # C2-30 FIX: clean up pyramid flag
        if idea:
            audit(
                trade_log,
                f"Trade manually rejected: {trade_id}",
                action="human_reject",
                result="REJECTED",
            )
            return "Trade REJECTED."
        return "Trade not found."

    async def force_scan(self, max_symbols: int | None = None, lightweight: bool = False) -> dict:
        """Single-flight guard around a force-scan cycle.

        ``max_symbols`` caps how many scanned signals get ANALYZED this cycle.
        Interactive callers (the "Latest Signal" button) pass a small cap so the
        heavy per-symbol analysis (OHLCV + order-flow + MTF fetches, serialized
        by the exchange rate limiter) can't hang the Telegram handler for
        minutes on the wide ~200-symbol universe. None = analyze all (the
        background/operator deep scan).

        With PTB concurrent_updates ON, two Telegram updates can enter here at
        once (two 'Latest Signal' taps, or a tap + /forcescan). Both would clear
        and repopulate _pending_ideas and double the scan/LLM/exchange load, so
        the second caller returns immediately. The .locked() check and the
        ``async with`` acquire have no await between them, so the guard is atomic
        on single-threaded asyncio.
        """
        if self._scan_lock.locked():
            audit(system_log, "Force scan skipped — scan already in progress",
                  action="force_scan", result="SKIPPED_INFLIGHT")
            return {"signals": 0, "ideas": 0, "auto_confirmed": 0,
                    "pending": len(self._pending_ideas), "cleared_pending": 0,
                    "skipped": "scan_already_running"}
        async with self._scan_lock:
            return await self._force_scan_locked(max_symbols=max_symbols, lightweight=lightweight)

    async def _force_scan_locked(self, max_symbols: int | None = None, lightweight: bool = False) -> dict:
        """Force an immediate scan cycle, bypassing cooldown and pending gates.

        Called by /forcescan command. Clears pending ideas, resets cooldown,
        and runs a full scan-analyze cycle. Returns a summary dict. ``max_symbols``
        caps the number of signals analyzed (interactive callers pass a small
        value to stay responsive on the wide universe).
        """
        audit(system_log, "Force scan triggered", action="force_scan", result="START")

        # Clear gates that would block a normal tick
        old_pending = len(self._pending_ideas)
        self._pending_ideas.clear()
        self._pending_atr.clear()
        self._pending_timing.clear()
        self._pending_pyramid.clear()
        self._cooldown_until = 0.0

        # Run scan
        self._transition(AgentState.SCANNING, "force scan")
        try:
            signals = await self.scanner.scan()
            self._last_scan_signals = signals or []
        except Exception as exc:
            self._transition(AgentState.IDLE, "force scan error")
            return {"error": str(exc), "signals": 0, "ideas": 0}

        if not signals:
            self._transition(AgentState.IDLE, "force scan: no signals")
            return {"signals": 0, "ideas": 0, "cleared_pending": old_pending}

        # Interactive cap: analyze only the top-N (scanner already ranked by
        # allocation), keeping the button responsive. The full sweep still runs
        # in the autonomous loop.
        if max_symbols and max_symbols > 0 and len(signals) > max_symbols:
            signals = signals[:max_symbols]

        # Analyze (bounded concurrency, same as the autonomous tick)
        self._transition(AgentState.ANALYZING, "force scan analyzing")
        results = await self._analyze_signals_batched(signals, lightweight=lightweight)

        ideas_found = 0
        for idea in results:
            if idea and idea.confidence >= CONFIG.risk.min_confidence:
                idea_key = normalize_symbol(idea.asset)
                for eid, eidea in list(self._pending_ideas.items()):
                    if normalize_symbol(eidea.asset) == idea_key:
                        self._pending_ideas.pop(eid)
                        self._pending_atr.pop(eid, None)
                        break
                self._pending_ideas[idea.id] = idea
                ideas_found += 1

        # Auto-confirm high-confidence ideas (same as normal tick)
        from bot.config import RUNTIME
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_confirmed = 0
        for tid, tidea in list(self._pending_ideas.items()):
            if self._auto_confirm_gate_value(tidea) >= auto_threshold:
                _et_ok, _et_why = self._pending_timing.get(tid, (True, ""))
                if not _et_ok:
                    audit(trade_log,
                          f"Auto-entry DEFERRED for {tidea.asset} — awaiting wave-degree "
                          f"confirmation ({_et_why})",
                          action="entry_timing", result="DEFERRED",
                          data={"trade_id": tid, "reason": _et_why})
                    continue
                try:
                    result = await self.confirm_trade(tid, user_id="auto")
                    auto_confirmed += 1
                    if self._auto_confirm_notify_callback:
                        try:
                            await self._auto_confirm_notify_callback(tidea, result)
                        except Exception:
                            pass
                except Exception:
                    pass

        self._transition(AgentState.IDLE, "force scan complete")

        summary = {
            "signals": len(signals),
            "ideas": ideas_found,
            "auto_confirmed": auto_confirmed,
            "pending": len(self._pending_ideas),
            "cleared_pending": old_pending,
        }
        audit(system_log, f"Force scan complete: {summary}",
              action="force_scan", result="OK", data=summary)
        return summary

    async def _fetch_prices_by_category(self, positions) -> dict[str, float]:
        """Fetch ticker prices using the correct exchange per asset category.

        Splits positions into spot (Crypto) and futures (Metal/Commodity/ETF/etc.)
        groups and fetches from the appropriate exchange in parallel.
        """
        spot_syms = []
        futures_syms = []
        for p in positions:
            sym = p.asset if hasattr(p, "asset") else p.symbol
            cat = _classify_symbol(sym)
            if cat != "Crypto":
                futures_syms.append(sym)
            else:
                spot_syms.append(sym)

        prices: dict[str, float] = {}

        async def _fetch_spot():
            if not spot_syms:
                return {}
            ex = await self.scanner._get_exchange()
            tickers = await ex.fetch_tickers(spot_syms)
            return {s: float(t.get("last", 0)) for s, t in tickers.items()}

        async def _fetch_futures():
            if not futures_syms:
                return {}
            ex = await self.scanner._get_futures_exchange()
            tickers = await ex.fetch_tickers(futures_syms)
            return {s: float(t.get("last", 0)) for s, t in tickers.items()}

        results = await asyncio.gather(
            _fetch_spot(), _fetch_futures(), return_exceptions=True
        )
        for r in results:
            if isinstance(r, dict):
                prices.update(r)
            elif isinstance(r, Exception):
                logger.debug("Price fetch error: %s", r)

        return prices

    async def _evaluate_live_smart_exits(self, executor) -> None:
        """Gated (default OFF): auto-close LIVE positions whose thesis has
        invalidated, instead of letting them ride to the exchange stop-loss.

        Runs the SAME smart-exit checks the paper path already applies in
        ``_check_paper_positions`` — time stop, signal-hold limit, VWAP-reversion
        done/failed, volume-signal decay — against the executor's open positions,
        and closes a fired position at market via ``executor.close_position``.

        Gated behind ``CONFIG.time_stop.enabled`` AND
        ``CONFIG.time_stop.live_auto_close_enabled`` (both must be true; the
        latter defaults False), so live behaviour is byte-identical until an
        operator opts in. Fail-open throughout: any error is swallowed so this
        can never disrupt the SL/TP monitoring that runs alongside it. Never
        bypasses the risk engine — it only ever CLOSES an existing position.
        """
        cfg = CONFIG.time_stop
        if not (cfg.enabled and getattr(cfg, "live_auto_close_enabled", False)):
            return
        try:
            from bot.core.smart_exits import (
                should_time_exit,
                check_signal_hold_limit,
                check_vwap_reversion_exit,
                should_volume_decay_exit,
            )
            prices: dict = {}
            try:
                if self.ws_feed.is_connected():
                    prices = self.ws_feed.get_prices(
                        max_age_sec=CONFIG.execution.ws_max_tick_age_sec) or {}
            except Exception:
                prices = {}

            for pos in list(getattr(executor, "_positions", {}).values()):
                if getattr(pos, "status", "") != "open":
                    continue
                price = prices.get(pos.symbol) or 0
                if price <= 0 or pos.entry_price <= 0:
                    continue

                hold_h = (datetime.now(UTC) - pos.opened_at).total_seconds() / 3600.0
                candles_held = int(hold_h)  # 1H candles
                if pos.direction == "LONG":
                    pnl_raw = price - pos.entry_price
                else:
                    pnl_raw = pos.entry_price - price
                # R-multiple denominator is the INITIAL risk taken at entry, not
                # the live ratcheted stop: a winner whose stop has trailed to
                # breakeven has entry-minus-stop ≈ 0, which read as R=0 and made
                # the time/hold exits below force-close a real runner.
                from bot.core.position_telemetry import r_denominator
                risk = r_denominator(pos)
                r_mult = pnl_raw / risk if risk > 0 else 0.0

                sig = getattr(pos, "signal_type", "momentum_confluence")
                stype = getattr(pos, "strategy_type", "swing")

                should_exit, reason = should_time_exit(stype, candles_held, r_mult)
                if not should_exit:
                    should_exit, reason = check_signal_hold_limit(sig, hold_h, r_mult)
                if not should_exit:
                    should_exit, reason = should_volume_decay_exit(sig, candles_held, r_mult)
                if not should_exit:
                    vwap = self._last_vwap.get(pos.symbol, 0)
                    if vwap > 0:
                        should_exit, reason = check_vwap_reversion_exit(
                            sig, price, vwap, pos.direction)

                if not should_exit:
                    continue

                audit(trade_log, f"Live smart-exit auto-close: {pos.symbol} — {reason}",
                      action="live_smart_exit", result="CLOSED",
                      data={"symbol": pos.symbol, "r_multiple": round(r_mult, 2),
                            "hold_hours": round(hold_h, 1), "signal_type": sig,
                            "strategy_type": stype})
                try:
                    await executor.close_position(
                        pos.trade_id, reason=f"smart_exit:{reason[:48]}")
                    if self._close_notify_callback:
                        try:
                            await self._close_notify_callback(
                                f"Smart-exit closed {pos.symbol}: {reason}")
                        except Exception as nexc:
                            logger.debug("Smart-exit notify failed: %s", nexc)
                except Exception as cexc:
                    audit(system_log,
                          f"Live smart-exit close failed for {pos.symbol}: {cexc}",
                          action="live_smart_exit", result="ERROR")
        except Exception as exc:
            system_log.debug("Live smart-exit evaluation failed: %s", exc)

    def _auto_confirm_gate_value(self, idea) -> float:
        """The confidence value the auto-confirm threshold is tested against.

        With CONFIG.auto_confirm_use_calibrated ON and a fitted calibrator
        available, return min(raw, calibrated) confidence — a real-money
        auto-trade then requires BOTH the raw blend AND the measured (calibrated)
        win-rate to clear the bar. This can only TIGHTEN auto-confirm, never
        loosen it: with no calibration data the calibrator is identity, so it is
        a no-op until evidence shows the raw confidence is over-optimistic.
        Fail-open: any error returns the raw confidence (gate never breaks)."""
        raw = float(getattr(idea, "confidence", 0.0) or 0.0)
        try:
            if not getattr(CONFIG, "auto_confirm_use_calibrated", False):
                return raw
            cal = self.analyzer._get_calibrator() if getattr(self, "analyzer", None) else None
            if not cal or not cal.is_ready():
                return raw
            calibrated = float(cal.calibrate(raw))
            return min(raw, calibrated)
        except Exception:
            return raw

    @staticmethod
    def _is_fill_message(msg: str) -> bool:
        """True when an executor position-monitor message is a FILL/OPEN — a
        filled limit order OR a limit→market fallback (the limit converted to a
        market fill) — rather than an actual CLOSE. Fills are notified as
        "TRADE OPENED"; everything else as a close. The fallback message was
        previously misrouted to the close path and shown as "❌ Trade Closed"."""
        first = (msg or "").split("\n", 1)[0]
        return first.startswith("LIMIT FILLED:") or "MARKET FALLBACK:" in first

    @staticmethod
    def _is_sync_message(msg: str) -> bool:
        """True for the periodic exchange-sync adoption notices the executor
        emits through the same monitor-message channel ("SYNC: Adopted
        untracked position/limit order …"). These are informational — the
        position is now TRACKED, nothing closed — and were previously
        misrouted to the close path and shown as "❌ Closed" (live incident:
        'Closed — SYNC: Adopted untracked position B from exchange')."""
        return (msg or "").split("\n", 1)[0].startswith("SYNC:")

    async def _check_open_positions(self) -> None:
        """Monitor open positions for SL/TP hits."""
        positions = self.portfolio.open_positions
        # Run paper monitoring when the shared portfolio OR any per-user (sim
        # opt-in) portfolio has open positions, so opted-in paper trades get
        # SL/TP monitoring even when the shared paper portfolio is empty.
        _user_paper_open = any(
            pf.open_positions for pf in self.user_portfolios.all_portfolios().values())
        if positions or _user_paper_open:
            await self._check_paper_positions(positions)
        # Live positions are checked independently below — do NOT return early
        # when paper portfolio is empty, or live SL/TP monitoring is skipped entirely.

        # Also check live positions if in live mode
        if CONFIG.is_live():
            # SL/TP self-heal: re-place any stop that went missing DURING
            # operation (adopted-unprotected, cancelled SL, deferred-then-filled).
            # verify_and_fix_sltp is idempotent; throttled so it isn't run every
            # tick. Previously this ran ONLY at startup, so a position that became
            # naked mid-session stayed naked until the next restart.
            _now = time.monotonic()
            if (_now - self._last_sltp_verify_ts) >= self._SLTP_VERIFY_INTERVAL:
                self._last_sltp_verify_ts = _now
                for _ex in self._all_live_executors():
                    try:
                        await _ex.verify_and_fix_sltp()
                    except Exception as _vexc:
                        audit(system_log, f"Periodic SL/TP self-heal error: {_vexc}",
                              action="periodic_sltp_verify", result="ERROR")
                    # Leverage self-heal: propagate the exchange's ACTUAL applied
                    # leverage onto tracked positions while they are still OPEN.
                    # Previously this ran ONLY at startup, so a position whose
                    # exchange leverage differed from the bot's intent (e.g. a
                    # manual 20x vs a tracked 10x) recorded the stale value at
                    # close and under-counted margin risk the whole time it was
                    # live (incident TI-a4ba8a82). Same 5-min throttle.
                    try:
                        await _ex.sync_positions_from_exchange()
                    except Exception as _lexc:
                        audit(system_log, f"Periodic leverage sync error: {_lexc}",
                              action="periodic_leverage_sync", result="ERROR")

            # Monitor every account (operator + any per-user). With per-user off
            # this loops once over the operator — identical to before.
            for _ex in self._all_live_executors():
                try:
                    live_closed = await _ex.check_positions()
                    for msg in live_closed:
                        # Distinguish limit fills from actual closes. A
                        # "LIMIT → MARKET FALLBACK:" message is a position OPEN
                        # (the limit converted to a market fill), not a close —
                        # route it to the fill ("TRADE OPENED") path so it isn't
                        # mislabeled as "❌ Trade Closed".
                        is_fill = self._is_fill_message(msg)
                        if is_fill:
                            audit(trade_log, f"Limit order filled: {msg}",
                                  action="limit_fill_notify", result="FILLED")
                            if self._fill_notify_callback:
                                try:
                                    await self._fill_notify_callback(msg)
                                except Exception as exc:
                                    logger.debug("Fill notify failed: %s", exc)
                            continue

                        # Periodic-sync adoption notices are informational —
                        # the position is now tracked, nothing closed. Route
                        # them to the sync callback (info card), never the
                        # close card, and skip the loss-cooldown scan below.
                        if self._is_sync_message(msg):
                            audit(trade_log, f"Exchange sync: {msg}",
                                  action="exchange_sync_notify", result="ADOPTED")
                            if self._sync_notify_callback:
                                try:
                                    await self._sync_notify_callback(msg)
                                except Exception as exc:
                                    logger.debug("Sync notify failed: %s", exc)
                            continue

                        audit(trade_log, f"Live position auto-closed: {msg}",
                              action="live_auto_close", result="CLOSED")
                        if self._close_notify_callback:
                            try:
                                await self._close_notify_callback(msg)
                            except Exception as exc:
                                logger.debug("Close notify failed: %s", exc)
                    # C-08 FIX: trigger cooldown on live losses. Read the losses
                    # from the executor's closed-trade ledger for THIS tick
                    # instead of _last_close_data per message — that shared slot
                    # is last-write-wins, so with 2+ closes in one sweep every
                    # message saw only the final close (a winning last close
                    # masked an earlier loss, and the cooldown reason named the
                    # wrong symbol). Only scanned when this tick closed something.
                    if any(not self._is_fill_message(m)
                           and not self._is_sync_message(m) for m in live_closed):
                        try:
                            _now_utc = datetime.now(UTC)
                            _tick_losses = [
                                t for t in getattr(_ex, '_closed_trades', [])
                                if (t.pnl_usd or 0) < 0 and t.closed_at is not None
                                and (_now_utc - (t.closed_at if t.closed_at.tzinfo
                                                 else t.closed_at.replace(tzinfo=UTC))
                                     ).total_seconds() <= 120
                            ]
                            if _tick_losses:
                                _worst = min(_tick_losses, key=lambda t: t.pnl_usd or 0)
                                self._cooldown_until = (
                                    time.monotonic() + CONFIG.risk.cooldown_after_loss_seconds
                                )
                                self._transition(
                                    AgentState.COOLING_DOWN,
                                    f"live loss on {_worst.symbol} "
                                    f"(PnL=${_worst.pnl_usd}), "
                                    f"cooling down {CONFIG.risk.cooldown_after_loss_seconds}s",
                                )
                        except Exception as exc:
                            logger.debug("Loss-cooldown scan failed: %s", exc)
                except Exception as exc:
                    audit(system_log, f"Live position monitor error: {exc}",
                          action="live_monitor", result="ERROR")

                # F-14 FIX: Reconcile tracked positions with exchange
                # Detects positions closed by exchange-side SL/TP triggers
                try:
                    reconciled = await _ex.reconcile_positions()
                    for msg in reconciled:
                        audit(trade_log, f"Position reconciled: {msg}",
                              action="reconcile", result="CLOSED")
                        if self._close_notify_callback:
                            try:
                                await self._close_notify_callback(msg)
                            except Exception as exc:
                                logger.debug("Close notify (reconcile) failed: %s", exc)
                except Exception as exc:
                    audit(system_log, f"Reconciliation error: {exc}",
                          action="reconcile", result="ERROR")

                # Gated (default OFF): auto-close live positions whose thesis has
                # invalidated (time stop / signal-hold limit / VWAP reversion /
                # volume decay) instead of letting them ride to the exchange SL.
                await self._evaluate_live_smart_exits(_ex)

            # Periodic orphan adoption: catch positions opened on exchange
            # but not tracked locally (e.g., after bot restart, manual trades,
            # or failed adoption on startup).  Runs every tick alongside
            # reconciliation to keep local state in sync.
            try:
                sync_msgs = await sync_portfolio_with_exchange(self)
                for msg in sync_msgs:
                    if "Adopted" in msg or "Ghost" in msg or "Orphan" in msg:
                        audit(system_log, f"Periodic sync: {msg}",
                              action="periodic_exchange_sync", result="SYNCED")
                # _adopt_notify_callback expects a list[str] (it renders ONE
                # consolidated "Found N position(s)..." notification) -- this
                # used to call it once PER message with a single string, so
                # len()/iteration over that string produced a garbled
                # character-by-character bullet list (e.g. "Found 41
                # position(s)" where 41 was the CHARACTER COUNT of one
                # message, each letter its own bullet). Collect all "Adopted"
                # messages first and notify once with the real list.
                # SYMBOLS, not the prose messages — the callback renders a
                # per-position SL/TP outcome and needs something it can match
                # against the executor's positions.
                adopted_msgs = adopted_symbols_from_messages(sync_msgs)
                if adopted_msgs and self._adopt_notify_callback:
                    try:
                        await self._adopt_notify_callback(adopted_msgs)
                    except Exception as exc:
                        logger.debug("Adopt notify failed: %s", exc)
                # Adopted/ghost/orphan changes alter the live position set —
                # mirror them to the website (only when something changed;
                # this branch runs every tick).
                if sync_msgs:
                    try:
                        self._sync_live_state_to_website()
                    except Exception as _sync_exc:
                        logger.debug("Periodic live website sync skipped: %s", _sync_exc)
            except Exception as exc:
                audit(system_log, f"Periodic exchange sync error: {exc}",
                      action="periodic_exchange_sync", result="ERROR")

    async def _check_paper_positions(self, positions) -> None:
        """Monitor paper portfolio positions for SL/TP hits."""
        try:
            # Price every paper symbol: the shared portfolio's positions PLUS all
            # per-user (sim opt-in) portfolios, so opted-in paper positions are
            # priced and SL/TP-monitored (via check_stops_all below) even when the
            # shared portfolio is empty. The exit loops below still iterate only
            # the shared portfolio, so this never closes a user position in the
            # wrong book.
            _priced_positions = list(positions) + [
                p for pf in self.user_portfolios.all_portfolios().values()
                for p in pf.open_positions]

            # Prefer WebSocket prices (sub-second) over REST (polling), but only
            # FRESH ticks — a stale-but-connected feed must not drive stop logic.
            # When all WS ticks are stale, ws_prices is empty → fall back to REST.
            if self.ws_feed.is_connected():
                ws_prices = self.ws_feed.get_prices(
                    max_age_sec=CONFIG.execution.ws_max_tick_age_sec)
                if ws_prices:
                    prices = ws_prices
                else:
                    prices = await self._fetch_prices_by_category(_priced_positions)
            else:
                prices = await self._fetch_prices_by_category(_priced_positions)

            # H-05 FIX: validate prices before use — reject any price that
            # deviates more than 50% from the last known good price for that symbol.
            validated_prices: dict[str, float] = {}
            for sym, px in prices.items():
                if px <= 0:
                    continue
                last_px = self._last_known_prices.get(sym)
                if last_px is not None and last_px > 0:
                    deviation = abs(px - last_px) / last_px
                    if deviation > 0.50:
                        logger.warning(
                            "Price validation: %s price %.6f deviates %.1f%% from last known %.6f — skipped",
                            sym, px, deviation * 100, last_px,
                        )
                        continue
                # Price is valid — update last known and include in validated set
                self._last_known_prices[sym] = px
                validated_prices[sym] = px
            prices = validated_prices

            # Feed prices to cross-asset tracker
            for _ca_sym, _ca_px in prices.items():
                try:
                    self.cross_asset.feed_price(_ca_sym, _ca_px)
                except Exception:
                    pass

            # Feed prices to risk engine for correlation v2. #49: stamp every
            # symbol fed this tick with ONE shared timestamp so the VaR path can
            # align cross-asset returns on a common grid (a symbol missing from
            # this tick simply lacks that timestamp, instead of drifting).
            _tick_ts = time.time()
            for _rp_sym, _rp_px in prices.items():
                try:
                    self.risk.update_price_history(_rp_sym, _rp_px, ts=_tick_ts)
                except Exception:
                    pass

            # Subscribe open position symbols to WS feed for future ticks
            pos_symbols = [p.asset for p in _priced_positions]
            self.ws_feed.subscribe(pos_symbols)
            # Mark-to-market: feed current prices so snapshot() reflects unrealized PnL
            self.portfolio.mark_to_market(prices)
            # Also update all per-user portfolios
            self.user_portfolios.mark_to_market_all(prices)

            # ── Time-based exit: close dead trades with no R progress ──
            try:
                from bot.core.smart_exits import should_time_exit
                for pos in list(positions):
                    # Calculate candles held (1H candles)
                    hold_secs = (datetime.now(UTC) - pos.opened_at).total_seconds()
                    candles_held = int(hold_secs / 3600)  # 1H candles

                    # Calculate current R-multiple
                    current_price = prices.get(pos.asset)
                    if current_price and pos.entry_price > 0:
                        if pos.direction.value == "LONG":
                            risk = pos.entry_price - pos.stop_loss
                            pnl_raw = current_price - pos.entry_price
                        else:
                            risk = pos.stop_loss - pos.entry_price
                            pnl_raw = pos.entry_price - current_price
                        r_multiple = pnl_raw / risk if risk > 0 else 0.0

                        should_exit, reason = should_time_exit(
                            strategy_type=pos.strategy_type,
                            candles_held=candles_held,
                            current_r_multiple=r_multiple,
                        )

                        if should_exit:
                            audit(trade_log, f"Time exit triggered: {pos.asset} — {reason}",
                                  action="time_exit", result="CLOSED",
                                  data={"symbol": pos.asset, "candles": candles_held,
                                        "r_multiple": round(r_multiple, 2),
                                        "strategy_type": pos.strategy_type})
                            # Close the position at current price
                            self.portfolio.close_position(
                                pos.trade_id, current_price
                            )
            except Exception as exc:
                system_log.debug("Time-based exit check failed: %s", exc)

            # ── Signal-type hold limit check ──
            try:
                from bot.core.smart_exits import check_signal_hold_limit, check_vwap_reversion_exit
                for pos in list(self.portfolio.open_positions):
                    current_price = prices.get(pos.asset)
                    if not current_price or current_price <= 0:
                        continue

                    hold_secs = (datetime.now(UTC) - pos.opened_at).total_seconds()
                    holding_hours = hold_secs / 3600

                    # R-multiple calculation
                    if pos.direction.value == "LONG":
                        risk = pos.entry_price - pos.stop_loss
                        pnl_raw = current_price - pos.entry_price
                    else:
                        risk = pos.stop_loss - pos.entry_price
                        pnl_raw = pos.entry_price - current_price
                    r_multiple = pnl_raw / risk if risk > 0 else 0.0

                    signal_type = getattr(pos, 'signal_type', 'momentum_confluence')

                    # Signal hold limit
                    should_exit, reason = check_signal_hold_limit(
                        signal_type=signal_type,
                        holding_hours=holding_hours,
                        current_r_multiple=r_multiple,
                    )
                    if should_exit:
                        audit(trade_log, f"Signal hold exit: {pos.asset} — {reason}",
                              action="signal_hold_exit", result="CLOSED")
                        self.portfolio.close_position(pos.trade_id, current_price)
                        continue

                    # VWAP reversion exit (best-effort: skip if no VWAP available)
                    vwap = self._last_vwap.get(pos.asset, 0)
                    if vwap > 0:
                        should_exit, reason = check_vwap_reversion_exit(
                            signal_type=signal_type,
                            current_price=current_price,
                            vwap=vwap,
                            direction=pos.direction.value,
                        )
                        if should_exit:
                            audit(trade_log, f"VWAP exit: {pos.asset} — {reason}",
                                  action="vwap_exit", result="CLOSED")
                            self.portfolio.close_position(pos.trade_id, current_price)
            except Exception as exc:
                system_log.debug("Signal hold check failed: %s", exc)

            closed = self.portfolio.check_stops(prices)
            # Check stops for per-user portfolios too
            user_closed = self.user_portfolios.check_stops_all(prices)
            # Merge user-closed trades into the main notification flow
            for uid, user_trades in user_closed.items():
                closed.extend(user_trades)
            for c in closed:
                audit(
                    trade_log,
                    f"Position auto-closed: {c.asset} PnL=${c.pnl}",
                    action="auto_close",
                    result="CLOSED",
                )
                # Enter cooldown after a loss
                if c.pnl < 0:
                    self._cooldown_until = (
                        time.monotonic() + CONFIG.risk.cooldown_after_loss_seconds
                    )
                    self._transition(
                        AgentState.COOLING_DOWN,
                        f"loss on {c.asset} (PnL=${c.pnl}), "
                        f"cooling down {CONFIG.risk.cooldown_after_loss_seconds}s",
                    )
                # Record to trade journal
                try:
                    self.journal.record_trade(
                        trade_id=getattr(c, 'trade_id', '') or '',
                        symbol=c.asset,
                        direction=c.direction.value if hasattr(c.direction, 'value') else str(c.direction),
                        strategy_type=getattr(c, 'strategy_type', ''),
                        entry_price=c.entry_price,
                        exit_price=getattr(c, 'exit_price', None) or 0,
                        stop_loss=c.stop_loss,
                        take_profit=c.take_profit,
                        pnl=c.pnl,
                        confidence=getattr(c, '_confidence', 0),
                        signals_used=getattr(c, '_signals_used', []),
                        regime=getattr(self.risk, '_current_regime', ''),
                        holding_hours=((c.closed_at - c.opened_at).total_seconds() / 3600) if getattr(c, 'closed_at', None) and getattr(c, 'opened_at', None) else 0,
                    )
                except Exception:
                    pass
                # Feed the paper/sim close into the learning loop's WRITE side
                # (opt-in, default OFF). Live closes record via
                # _on_live_position_closed — which now also journals them;
                # for a long time that claim was made here and nowhere
                # enforced, leaving /journal empty in pure-live mode. this lets the simulation-first
                # majority of closes accumulate learning history, tagged
                # source="paper_outcome" so live evidence stays distinguishable.
                try:
                    if CONFIG.learning.learn_from_paper_closes_enabled:
                        self.learning.record_closed_outcome(
                            symbol=c.asset,
                            direction=c.direction.value if hasattr(c.direction, 'value') else str(c.direction),
                            pnl_result=float(c.pnl),
                            market_regime=self._outcome_regime(c.asset),
                            trade_id=getattr(c, 'trade_id', '') or '',
                            source="paper_outcome",
                        )
                except Exception as _lp_exc:
                    logger.debug("Paper-close learning record skipped: %s", _lp_exc)
                # Advance the auto-refit cadence on paper closes too (gated,
                # fail-open). note_closed_trade is what turns accumulated outcomes
                # into refitted learner state; it was wired into live closes only,
                # so in simulation-first operation the learners never refit despite
                # the paper history just recorded above. Gate on the paper-write
                # flag so the counter only advances when a paper outcome was
                # actually recorded.
                try:
                    if (CONFIG.learning.learn_from_paper_closes_enabled
                            and CONFIG.analyzer.learning_auto_refit_enabled):
                        self._auto_refit.note_closed_trade(getattr(self, "analyzer", None))
                except Exception as _ar_paper_exc:
                    logger.debug("Learning auto-refit skipped (paper): %s", _ar_paper_exc)
                # Record time-of-day outcome
                try:
                    from datetime import datetime as _dt
                    hour_utc = _dt.now(UTC).hour
                    is_win = c.pnl > 0
                    self.time_of_day.record(c.asset, hour_utc, is_win)
                except Exception:
                    pass
                # Record hold-time analytics
                try:
                    hold_h = ((c.closed_at - c.opened_at).total_seconds() / 3600) if getattr(c, 'closed_at', None) and getattr(c, 'opened_at', None) else 0
                    if hold_h > 0 and c.entry_price > 0:
                        if c.direction.value == "LONG":
                            risk = c.entry_price - c.stop_loss
                        else:
                            risk = c.stop_loss - c.entry_price
                        final_r = c.pnl / (risk * c.quantity) if risk > 0 and c.quantity > 0 else 0
                        self.hold_analytics.record(
                            strategy_type=c.strategy_type,
                            holding_hours=hold_h,
                            r_multiple=final_r,
                            is_win=c.pnl > 0,
                        )
                except Exception:
                    pass
        except Exception as exc:
            audit(
                system_log,
                f"Position monitor error: {exc}",
                action="monitor",
                result="ERROR",
            )
            self._transition(AgentState.IDLE, f"monitor error: {exc}")

    @property
    def pending_ideas(self) -> list[TradeIdea]:
        return list(self._pending_ideas.values())

    # -- Portfolio Heat / Auto-Rebalance --

    def check_portfolio_heat(self) -> dict:
        """Compute portfolio exposure and determine if rebalancing is needed.

        Returns dict with:
          - total_exposure_pct: sum of position values / equity
          - max_single_exposure_pct: largest single position / equity
          - needs_rebalance: True if total > 60% or single > 30%
          - rebalance_actions: list of suggested reduction actions
        """
        snap = self.portfolio.snapshot()
        equity = snap.equity_usd
        if equity <= 0:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        positions = self.portfolio.open_positions
        if not positions:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        # Compute per-position exposure
        exposures: dict[str, float] = {}
        total_exposure = 0.0
        for pos in positions:
            pos_value = self.portfolio.get_position_value(pos.asset)
            exposure_pct = (pos_value / equity) * 100
            exposures[pos.asset] = exposure_pct
            total_exposure += exposure_pct

        max_single = max(exposures.values()) if exposures else 0.0

        # Determine rebalance need
        needs_rebalance = total_exposure > 60.0 or max_single > 30.0

        # Generate suggested actions
        actions: list[str] = []
        if needs_rebalance:
            for asset, exp_pct in sorted(exposures.items(), key=lambda x: -x[1]):
                if exp_pct > 30.0:
                    # Suggest reducing to 25%
                    reduce_pct = round((1 - 25.0 / exp_pct) * 100)
                    actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")
                elif total_exposure > 60.0 and exp_pct > 15.0:
                    # Suggest reducing larger positions proportionally
                    target = exp_pct * (55.0 / total_exposure)
                    reduce_pct = round((1 - target / exp_pct) * 100)
                    if reduce_pct > 5:
                        actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")

        return {
            "total_exposure_pct": round(total_exposure, 2),
            "max_single_exposure_pct": round(max_single, 2),
            "needs_rebalance": needs_rebalance,
            "rebalance_actions": actions,
        }

    def get_rebalance_signals(self) -> list[dict]:
        """Return rebalance signals for the War Room display.

        Respects a minimum 4-hour interval between checks to avoid
        excessive computation. Returns empty list if checked too recently.
        """
        now = time.monotonic()
        if self._last_rebalance_check and (now - self._last_rebalance_check) < self._rebalance_interval:
            return []

        self._last_rebalance_check = now
        heat = self.check_portfolio_heat()

        if not heat["needs_rebalance"]:
            return []

        signals = []
        for action in heat["rebalance_actions"]:
            signals.append({
                "type": "REBALANCE",
                "action": action,
                "total_exposure_pct": heat["total_exposure_pct"],
                "max_single_exposure_pct": heat["max_single_exposure_pct"],
                "timestamp": datetime.now(UTC).isoformat(),
            })

        if signals:
            audit(
                system_log,
                f"Rebalance needed: total={heat['total_exposure_pct']:.1f}%, "
                f"max_single={heat['max_single_exposure_pct']:.1f}%",
                action="rebalance_check",
                result="REBALANCE_NEEDED",
                data=heat,
            )

        return signals
