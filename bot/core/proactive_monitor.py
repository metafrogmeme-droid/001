"""
RUNECLAW Proactive Alert Monitor.

Runs as a background coroutine alongside the engine, pushing unsolicited
alerts to the operator when thresholds are crossed:
  - Volume spikes on watched assets
  - Regime flips (TREND → CHOP, etc.)
  - Black-swan detector triggers
  - Circuit breaker state changes
  - Trade SL/TP proximity warnings
  - Macro event approaching

Gated behind /watch on|off toggle per chat. Only sends to authorized
admin users in the allow-list (F-04 compliant).

Safety: the monitor is read-only. It observes engine state and emits
alerts. It never creates trades, modifies risk limits, or bypasses
any gate. Proposal alerts may ATTACH inline action buttons, but those
buttons route to already-guarded handlers (admin re-check + live-amount
recompute happen there) — the monitor itself still moves nothing.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Any, Callable, Optional, Set

from bot.config import CONFIG
from bot.utils.logger import audit, system_log

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import state_path

logger = logging.getLogger(__name__)


def _host_of(url: str) -> str:
    """Just the hostname from a URL — never the path, never a query.

    A probe result is a message to a person and a URL is the one field in this
    module most likely to carry a credential (a signed tunnel link, a token in
    a query string). The host is what identifies the origin; the rest is not
    needed to act on the alert.
    """
    try:
        from urllib.parse import urlparse
        # Always a usable label. An empty return renders as
        # "Host: <code></code>", which tells the reader nothing and looks like
        # a bug in the alert rather than a fact about the configuration.
        return urlparse(url).hostname or (url[:60] if url else "") \
            or "the configured URL"
    except Exception:
        return "the configured URL"


# ── Alert types ───────────────────────────────────────────────────────

@dataclass
class Alert:
    """A single proactive alert to send to the operator."""
    alert_type: str       # VOLUME_SPIKE, REGIME_FLIP, BLACK_SWAN, CIRCUIT_BREAKER, etc.
    severity: str         # INFO, WARNING, CRITICAL
    title: str            # Short title for the alert
    body: str             # Full message (HTML formatted for Telegram)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    dedup_key: str = ""   # For deduplication (same key = don't re-alert within cooldown)
    idea: Any = None      # optional TradeIdea — enables an attached setup chart
    # Optional inline action buttons as (label, callback_data) pairs. Kept as
    # plain tuples so this core module never imports telegram — the handler's
    # send_fn converts them to an InlineKeyboardMarkup. Callback data must be
    # an already-guarded route (e.g. "yld:s:USDT" re-checks admin + amounts).
    buttons: list = field(default_factory=list)


# ── Alert severity icons ──────────────────────────────────────────────

_SEVERITY_ICON = {
    "INFO": "\U0001f535",       # Blue circle
    "WARNING": "\U0001f7e0",    # Orange circle
    "CRITICAL": "\U0001f534",   # Red circle
}


# ── Proactive Monitor ─────────────────────────────────────────────────

def select_severe_cards(groups: dict, cap: int) -> tuple:
    """(cards to send, symbols only named in the overflow line).

    Pure, and module-level, so a test can drive it. The first version of this
    lived inline and its only test was a source scan — which duly passed
    against a mutation that made the overflow branch unreachable, because the
    string it grepped for was still sitting in the dead code.

    Most severe first: the cap must never be able to drop the worst condition
    of the pass.
    """
    ordered = sorted(groups.items(),
                     key=lambda kv: -max(float(a.severity) for a in kv[1]))
    shown, hidden = ordered[:cap], ordered[cap:]
    spill = sorted({str(a.symbol) for _k, g in hidden for a in g})
    return shown, spill


class ProactiveMonitor:
    """Background monitor that generates alerts from engine state.

    Usage:
        monitor = ProactiveMonitor(engine)
        asyncio.create_task(monitor.run(send_fn))

    The send_fn is an async callable(chat_id: str, text: str) -> None
    that sends a Telegram message. The monitor calls it for each alert.
    """

    # How often to check (seconds)
    CHECK_INTERVAL = 30

    # Deduplication cooldown (don't re-alert same event within this window)
    DEDUP_COOLDOWN = 300  # 5 minutes

    # ANOMALIES ARE STANDING CONDITIONS, NOT EVENTS, and the 5-minute cooldown
    # treats them as events. A spread that stays widened for an hour re-pages
    # twelve times PER SYMBOL under DEDUP_COOLDOWN, saying nothing new each
    # time. Observed live alongside the missing type-clustering: the two
    # compound, so a single market-wide liquidity event produced dozens of
    # near-identical messages.
    #
    # An operator who is being told the same thing every five minutes stops
    # reading, and the next alert — the one that IS new — arrives into that
    # habit. Suppression here is not about noise, it is about keeping the
    # channel worth reading.
    #
    # Escalation still pages immediately: the suppression is keyed on the
    # severity TIER, so 0.2 -> 0.9 breaks through at once. Only a condition
    # that is both persisting AND unchanged waits.
    BLACK_SWAN_REPEAT = 1800  # 30 minutes for an unchanged, ongoing anomaly

    # SEVERE USED TO BE EXEMPT, AND "EXEMPT" TURNED OUT TO MEAN "METRONOME".
    # `_bs_is_news` returned True unconditionally at tier 2, on the reasoning
    # that the one page which must always arrive must never be held. It does
    # always arrive — and then arrives again on every DEDUP_COOLDOWN for as
    # long as the condition lasts. Observed live: a spread stuck at 12.9x
    # baseline re-paging beside a second one at 7.9x, thirty-three seconds
    # apart, saying exactly what the previous pair said.
    #
    # A standing condition is not news the ninth time either, whatever its
    # severity, and the operator who stops reading a severe alert is the exact
    # failure the exemption was meant to prevent. The FIRST sighting is still
    # immediate and unconditional, and so is any escalation into this tier;
    # only the unchanged repeat waits, and it waits half as long as a mild one.
    BLACK_SWAN_SEVERE_REPEAT = 900  # 15 minutes for an unchanged severe anomaly

    #: Severe anomaly CARDS per tick. Each is a distinct first-sighting and is
    #: individually correct; the flood is their number. The rest are named in
    #: one trailing line rather than dropped in silence.
    _SEVERE_CARDS_PER_TICK = 3

    def __init__(self, engine) -> None:
        self.engine = engine
        self._enabled_chats: Set[str] = set()   # Chat IDs with /watch on
        # NOTE: the persisted watch list is loaded (and the operator auto-
        # enrolled) by hydrate(), called from start_monitor — NOT here — so a
        # bare ProactiveMonitor(engine) in tests stays empty and deterministic.
        self._running = False
        # Own-loop heartbeat for the engine's reciprocal liveness watch.
        # None (NOT 0.0) sentinel — see the monotonic-epoch trap note below:
        # a fresh process has monotonic near zero, so a 0.0 "never ran" would
        # read as recent and suppress the very alarm this exists to raise.
        self.last_loop_ts: float | None = None
        # Edge-trigger state for the engine-tick stall watch.
        self._tick_stale_alerted = False
        # The _last_tick_started_ts a stall was alerted for. Re-arming keys on
        # this MOVING, not on the predicate momentarily reading healthy.
        self._tick_stale_alerted_for = None
        self._dedup_cache: dict[str, float] = {}  # dedup_key -> last_alert_time
        # dedup_key -> (last_alert_time, severity_tier) for anomalies only, so
        # a standing condition is not re-announced while it is unchanged.
        self._bs_last: dict[str, tuple[float, int]] = {}

        # State tracking for change detection
        self._last_regime: dict[str, str] = {}    # symbol -> last known regime
        self._last_cb_state: bool = False          # last circuit breaker state
        self._last_state: str = ""                 # last engine FSM state
        self._alerted_signals: set = set()         # signal IDs already alerted
        # News stand-down PUSH: each fresh high-impact headline on a held asset
        # alerts EXACTLY once (the headline stays "fresh" for ~1h, so the generic
        # 5-min dedup would re-fire ~12×; this set is the once-only guard).
        self._news_alerted: set = set()
        # Early-warning state (Tier 1a hardening): track the highest drawdown
        # tier already alerted (re-arms only after recovery), WS/health/balance
        # and warning-rate breaker last-states so each transition alerts once.
        self._last_dd_tier: int = 0                # 0=none, 50/75/85 = pct-of-limit tier
        self._last_ws_ok: bool = True              # last WS-connected state (live)
        self._ws_down_since: float = 0.0           # monotonic ts WS first seen down
        self._last_warn_rate: bool = False         # last warning-rate-breaker state
        self._last_tick_degraded: bool = False     # last tick-failure alert state
        self._scan_timeout_alerted_at: int | None = None
        # Last result of the public-gateway probe: the path the WEBSITE uses
        # to reach this bot. None until the first probe runs.
        self._gw_probe: dict | None = None
        self._gw_probe_at: float = 0.0
        # Same shape for the self-hosted LLM origin — see _probe_llm_endpoint.
        self._llm_probe: dict | None = None
        self._llm_probe_at: float = 0.0
        # The STATE last paged, not a bool: unreachable -> forbidden is a
        # different fault with a different fix, and must re-page rather
        # than be swallowed as 'already told them'. Same as _gw_alerted_state.
        self._llm_alerted_state: str = ""
        self._gw_alerted_state: str | None = None
        self._last_llm_degraded: bool = False       # last LLM-brain-offline state
        # Strangle watchdog: rolling (wall_ts, evaluated, approved, fails_by_gate)
        # snapshots of the risk engine's cumulative counters, plus our own
        # last-alert time (the condition PERSISTS, so the generic 5-min dedup
        # would spam — this re-alerts at most once per window).
        self._strangle_snaps: deque = deque()
        self._last_strangle_alert: float = 0.0
        # Learning readiness: last known per-component state, so a component
        # BECOMING ready alerts exactly once (not every tick it stays ready).
        self._readiness_states: dict[str, str] = {}
        self._readiness_next_check: float = 0.0
        # Idle-cash nudge: when free margin sits stakeable for hours, propose
        # /stake with a confirm button (once per cooldown, re-arms on spend).
        self._idle_since: float = 0.0        # monotonic ts idle threshold first met
        self._last_idle_nudge: float = 0.0   # monotonic ts of the last nudge sent
        # Daily digest: morning plan + evening wrap, sent once per UTC day each.
        self._digest_sent: dict[str, str] = {}   # kind -> "YYYY-MM-DD" last sent
        # Funding-arb paper tracker: hourly background snapshot + per-coin
        # per-day big-spread alert dedup. The send_fn is captured by run()
        # so the background task can dispatch outside the check cycle.
        self._last_arb_snapshot: float = 0.0
        self._arb_alerted: set = set()
        self._arb_send_fn = None
        # Web reports push: hourly funding/arb/parity/yield payload to the
        # website so the dashboard reaches parity with the Telegram reports.
        self._last_reports_push: float = 0.0
        # Optional async callback(chat_id, idea) -> None to push a setup chart
        # alongside a signal alert. Set via set_chart_fn(); never required.
        self._chart_fn: Optional[Callable] = None

    def set_chart_fn(self, chart_fn) -> None:
        """Register an async callback(chat_id, idea) that pushes a setup chart
        for signal alerts. Optional — alerts work fine without it."""
        self._chart_fn = chart_fn

    def enable_chat(self, chat_id: str) -> None:
        """Enable proactive alerts for a chat."""
        self._enabled_chats.add(str(chat_id))
        self._save_enabled_chats()
        audit(system_log, f"Proactive alerts enabled for chat {chat_id}",
              action="watch_on", data={"chat_id": chat_id})

    def disable_chat(self, chat_id: str) -> None:
        """Disable proactive alerts for a chat."""
        self._enabled_chats.discard(str(chat_id))
        self._save_enabled_chats()
        audit(system_log, f"Proactive alerts disabled for chat {chat_id}",
              action="watch_off", data={"chat_id": chat_id})

    # ── Watch-list persistence + admin auto-enroll ────────────────
    # The watch list was in-memory only, so every restart silenced CRITICAL
    # safety alerts until someone re-ran /watch on. Persist it and, on a fresh
    # deploy with an empty list, auto-enroll the operator so alerts flow by
    # default. All best-effort / fail-open — a persistence hiccup must never
    # break the monitor.

    def hydrate(self) -> None:
        """Load the persisted watch list and auto-enroll the operator if empty.

        Called once at startup (start_monitor). Kept out of __init__ so a bare
        monitor constructed in tests is deterministically empty.
        """
        self._load_enabled_chats()
        self._maybe_auto_enroll_admin()

    def _watch_state_path(self) -> str:
        try:
            from bot.config import CONFIG
            return str(state_path(CONFIG.proactive_watch_state_file))
        except Exception:
            return str(state_path("data/proactive_watch.json"))

    def _load_enabled_chats(self) -> None:
        import json
        import os
        path = self._watch_state_path()
        # Whether a state file already exists distinguishes a FRESH deploy (no
        # file -> auto-enroll the operator) from an operator who explicitly
        # emptied the list (file present but empty -> respect their choice, do
        # NOT re-enroll on every restart).
        self._watch_state_existed = os.path.exists(path)
        try:
            if not self._watch_state_existed:
                return
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            chats = data.get("enabled_chats", []) if isinstance(data, dict) else data
            if isinstance(chats, list):
                self._enabled_chats = {str(c) for c in chats if c not in (None, "")}
        except Exception as exc:
            logger.debug("proactive watch-list load skipped: %s", exc)

    def _save_enabled_chats(self) -> None:
        path = self._watch_state_path()
        try:
            atomic_write_json(
                path, {"enabled_chats": sorted(self._enabled_chats)},
                indent=None)
        except Exception as exc:
            logger.debug("proactive watch-list save skipped: %s", exc)

    def _maybe_auto_enroll_admin(self) -> None:
        """When nobody is watching on a FRESH deploy, enroll the operator chat
        so CRITICAL safety alerts still reach them. Bounded: only fires when no
        state file existed yet (never when the operator explicitly emptied the
        list) and only if TELEGRAM_CHAT_ID is configured."""
        if self._enabled_chats:
            return
        # Operator has interacted before (file present) — respect their empty
        # list instead of re-enrolling every restart.
        if getattr(self, "_watch_state_existed", False):
            return
        try:
            from bot.config import CONFIG
            if not CONFIG.proactive_auto_enroll_admin:
                return
            admin = str(CONFIG.telegram.chat_id or "").strip()
            if admin:
                self._enabled_chats.add(admin)
                self._save_enabled_chats()
                audit(system_log,
                      f"Proactive alerts auto-enrolled operator chat {admin} "
                      f"(empty watch list on startup)",
                      action="watch_auto_enroll", data={"chat_id": admin})
        except Exception as exc:
            logger.debug("proactive admin auto-enroll skipped: %s", exc)

    def is_enabled(self, chat_id: str) -> bool:
        return chat_id in self._enabled_chats

    @property
    def enabled_chat_count(self) -> int:
        return len(self._enabled_chats)

    async def run(self, send_fn) -> None:
        """Main monitor loop. Runs until stopped."""
        self._running = True
        self._arb_send_fn = send_fn
        logger.info("Proactive monitor started")

        while self._running:
            self.last_loop_ts = time.monotonic()
            try:
                # Keep the shared news radar fresh so the stand-down PUSH check
                # (below, in _check_all) sees current headlines. Throttled + never
                # raises; the only network I/O in the loop.
                await self._refresh_news_radar()
                await self._probe_public_gateway()
                await self._probe_llm_endpoint()
                alerts = self._check_all()
                for alert in alerts:
                    if self._should_send(alert):
                        await self._dispatch(alert, send_fn)
                        self._mark_sent(alert)
            except Exception as exc:
                logger.debug("Monitor check error: %s", exc)

            await asyncio.sleep(self.CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ── Alert generation ──────────────────────────────────────────

    def _check_all(self) -> list[Alert]:
        """Run all alert checks and return any triggered alerts."""
        alerts: list[Alert] = []
        alerts.extend(self._check_circuit_breaker())
        alerts.extend(self._check_drawdown_tiers())
        alerts.extend(self._check_tick_failures())
        alerts.extend(self._check_scan_timeouts())
        alerts.extend(self._check_public_gateway())
        alerts.extend(self._check_llm_endpoint())
        alerts.extend(self._check_engine_tick_stale())
        alerts.extend(self._check_warning_rate_breaker())
        alerts.extend(self._check_llm_degraded())
        alerts.extend(self._check_ws_health())
        alerts.extend(self._check_stale_balance())
        alerts.extend(self._check_macro_calendar_stale())
        alerts.extend(self._check_news_standdown())
        alerts.extend(self._check_unprotected_positions())
        alerts.extend(self._check_slippage())
        alerts.extend(self._check_volume_spikes())
        alerts.extend(self._check_black_swan())
        alerts.extend(self._check_state_changes())
        alerts.extend(self._check_trade_signals())
        alerts.extend(self._check_sl_tp_proximity())
        alerts.extend(self._check_time_stops())
        alerts.extend(self._check_signal_strangle())
        alerts.extend(self._check_learning_readiness())
        alerts.extend(self._check_new_listings())
        alerts.extend(self._check_self_audit())
        alerts.extend(self._check_idle_cash())
        alerts.extend(self._check_daily_digest())
        alerts.extend(self._check_parity_digest())
        alerts.extend(self._check_arb_tracker())
        self._check_reports_push()
        return alerts

    # ── Web reports push: hourly Telegram↔web parity payload ──────

    def _check_reports_push(self) -> None:
        """Once an hour, build the web-reports payload (funding scan, arb
        paper tracker, parity headline, yield radar) in a worker thread and
        push it to the website's reports cache. Read-only everywhere; a dead
        website or venue just skips this cycle."""
        if os.environ.get("WEB_REPORTS_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return
        try:
            now = time.monotonic()
            interval_s = self._env_f("WEB_REPORTS_MIN", 60.0) * 60
            # 0.0 = never ran — fire immediately (same boot-pacing rule as
            # the arb tracker above; monotonic() starts near zero).
            if self._last_reports_push and now - self._last_reports_push < interval_s:
                return
            self._last_reports_push = now
            import asyncio as _aio

            engine = self.engine

            async def _build_and_push() -> None:
                try:
                    from bot.core.web_reports import build_reports_payload
                    from bot.utils.website_sync import sync_reports
                    payload = await _aio.to_thread(build_reports_payload, engine)
                    await _aio.to_thread(sync_reports, payload)
                except Exception as exc:
                    logger.debug("web reports push failed: %s", exc)

            _aio.get_running_loop().create_task(_build_and_push())
        except Exception as exc:
            logger.debug("web reports check skipped: %s", exc)

    # ── Funding-arb paper tracker: hourly snapshot + big-spread alert ─

    def _check_arb_tracker(self) -> list[Alert]:
        """Once an hour, snapshot cross-venue funding spreads (background
        thread — three public HTTP calls must never block the monitor loop)
        and alert when a coin's spread crosses the alert threshold. The
        tracker is 100% paper: it records and reports, never trades."""
        if os.environ.get("ARB_TRACKER_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        alerts: list[Alert] = []
        try:
            now = time.monotonic()
            interval_s = self._env_f("ARB_SNAPSHOT_MIN", 60.0) * 60
            # 0.0 means "never ran" — fire immediately. (monotonic() starts
            # near zero at boot, so `now - 0 < interval` would wrongly pace
            # out the first snapshot for up to an hour after every restart.)
            if self._last_arb_snapshot and now - self._last_arb_snapshot < interval_s:
                return []
            self._last_arb_snapshot = now
            import asyncio as _aio

            async def _snap_and_alert() -> None:
                try:
                    from bot.core.arb_tracker import (load_snapshots,
                                                      snapshot_opportunities)
                    wrote = await _aio.to_thread(snapshot_opportunities)
                    if not wrote:
                        return
                    threshold = self._env_f("ARB_ALERT_SPREAD_APR", 10.0)
                    snaps = (await _aio.to_thread(load_snapshots))[-wrote:]
                    today = datetime.now(UTC).strftime("%Y-%m-%d")
                    for s in snaps:
                        spread = float(s.get("spread_apr", 0) or 0)
                        key = f"arb_{s.get('base')}_{today}"
                        if spread < threshold or key in self._arb_alerted:
                            continue
                        self._arb_alerted.add(key)
                        alert = Alert(
                            alert_type="FUNDING_ARB", severity="INFO",
                            title="Wide funding spread",
                            body=(f"⚖️ <b>Wide funding spread: "
                                  f"{s.get('base')}</b>\n\n"
                                  f"<code>{spread:.1f}%/yr</code> — long "
                                  f"{s.get('long_venue')} / short "
                                  f"{s.get('short_venue')}.\n"
                                  "<i>Info only — /arb shows the paper "
                                  "tracker; nothing is traded.</i>"),
                            dedup_key=key)
                        if self._should_send(alert) and self._arb_send_fn:
                            await self._dispatch(alert, self._arb_send_fn)
                            self._mark_sent(alert)
                except Exception as exc:
                    logger.debug("arb tracker snapshot failed: %s", exc)

            _aio.get_running_loop().create_task(_snap_and_alert())
        except Exception as exc:
            logger.debug("arb tracker check skipped: %s", exc)
        return alerts

    # ── Weekly live↔backtest parity digest ────────────────────────

    def _check_parity_digest(self) -> list[Alert]:
        """Once a week, surface whether live execution still matches the
        model: realized PF, fee drag vs the modeled commission, win rate.
        Drift here is the earliest sign the backtest no longer describes
        reality. Local file read only — no network, no orders."""
        if os.environ.get("PARITY_DIGEST_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        try:
            now = datetime.now(UTC)
            dow = int(self._env_f("PARITY_DIGEST_DOW", 0))       # 0 = Monday
            hour = int(self._env_f("PARITY_DIGEST_HOUR_UTC", 7))
            week = now.strftime("%G-W%V")
            if now.weekday() != dow or now.hour < hour \
                    or self._digest_sent.get("parity") == week:
                return []
            self._digest_sent["parity"] = week
            from bot.backtest.parity import load_closed_trades, parity_summary
            path = getattr(getattr(self.engine, "live_executor", None),
                           "_closed_trades_file", None)
            if not path:
                return []
            trades = load_closed_trades(path)
            if not trades:
                return []
            s = parity_summary(trades, CONFIG.risk.commission_pct)
            fee_x = s.get("fee_vs_model", 0.0)
            drift = " ⚠️ fees running above model — /parity for the breakdown" \
                if fee_x > 1.5 else ""
            body = (
                "📏 <b>Weekly parity — live vs model</b>\n\n"
                f"Filled trades: <b>{s['trades']}</b> · win rate "
                f"<code>{s['win_rate'] * 100:.0f}%</code> · PF "
                f"<code>{s['pf']:.2f}</code>\n"
                f"Net <code>${s['net_pnl']:+,.2f}</code> · fees "
                f"<code>${s['total_fees']:,.2f}</code> "
                f"(<code>{fee_x:.1f}×</code> the modeled rate)"
                f"{drift}\n\n<i>/parity for the full bucketed report.</i>")
            return [Alert(alert_type="PARITY_DIGEST", severity="INFO",
                          title="Weekly parity digest", body=body,
                          dedup_key=f"parity_{week}")]
        except Exception as exc:
            logger.debug("parity digest skipped: %s", exc)
            return []

    # ── Proactive proposals: idle cash → stake nudge ──────────────

    @staticmethod
    def _env_f(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    def _check_idle_cash(self) -> list[Alert]:
        """Propose staking when free margin has sat idle for hours.

        Read-only: the alert only carries a button to the yld:s route, which
        re-checks admin and recomputes/clamps the amount from live balances
        at press time. Re-arms after the cooldown or once the cash is used.
        """
        if os.environ.get("IDLE_CASH_NUDGE_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        try:
            from bot.core.yield_radar import MARGIN_RESERVE_PCT
            cache = getattr(self.engine, "_live_balance_cache", None) or {}
            free = float(cache.get("free", 0) or 0)
            stakeable = free * (1 - MARGIN_RESERVE_PCT)
            threshold = self._env_f("IDLE_CASH_NUDGE_USD", 25.0)
            now = time.monotonic()
            if stakeable < threshold:
                self._idle_since = 0.0   # cash got used — re-arm the timer
                return []
            if not self._idle_since:
                self._idle_since = now
            idle_hours = self._env_f("IDLE_CASH_NUDGE_HOURS", 6.0)
            cooldown_h = self._env_f("IDLE_CASH_NUDGE_COOLDOWN_H", 24.0)
            if (now - self._idle_since) < idle_hours * 3600:
                return []
            if self._last_idle_nudge and \
                    (now - self._last_idle_nudge) < cooldown_h * 3600:
                return []
            self._last_idle_nudge = now
            return [Alert(
                alert_type="IDLE_CASH",
                severity="INFO",
                title="Idle cash could be earning",
                body=(
                    "💤 <b>Idle cash could be earning</b>\n\n"
                    f"≈<code>${stakeable:,.2f}</code> of free margin has sat "
                    f"unused for {idle_hours:.0f}h+ (after the "
                    f"{MARGIN_RESERVE_PCT:.0%} reserve the engine keeps).\n"
                    "Flexible Earn redeems instantly, so it stays recallable.\n\n"
                    "<i>The button recomputes the exact amount from live "
                    "balances — /yield shows current rates, /unstake redeems.</i>"),
                dedup_key="idle_cash_nudge",
                buttons=[("✅ Stake idle USDT", "yld:s:USDT"),
                         ("Not now", "yld:x")],
            )]
        except Exception as exc:
            logger.debug("idle-cash check skipped: %s", exc)
            return []

    # ── Proactive digests: morning plan + evening wrap ────────────

    def _check_daily_digest(self) -> list[Alert]:
        """Send a morning plan and an evening wrap once per UTC day each."""
        if os.environ.get("DAILY_DIGEST_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        alerts: list[Alert] = []
        try:
            now = datetime.now(UTC)
            today = now.strftime("%Y-%m-%d")
            schedule = {
                "brief": int(self._env_f("DAILY_BRIEF_HOUR_UTC", 6)),
                "wrap": int(self._env_f("DAILY_WRAP_HOUR_UTC", 20)),
            }
            for kind, hour in schedule.items():
                if now.hour >= hour and self._digest_sent.get(kind) != today:
                    self._digest_sent[kind] = today
                    body = self._digest_body(kind)
                    if body:
                        alerts.append(Alert(
                            alert_type=f"DAILY_{kind.upper()}",
                            severity="INFO",
                            title=f"Daily {kind}",
                            body=body,
                            dedup_key=f"digest_{kind}_{today}"))
        except Exception as exc:
            logger.debug("daily digest check skipped: %s", exc)
        return alerts

    def _digest_body(self, kind: str) -> str:
        """Compact, truthful engine digest. Everything best-effort — a field
        we can't read is omitted, never invented."""
        e = self.engine
        lines: list[str] = []
        try:
            mode = "LIVE" if CONFIG.is_live() else "PAPER"
        except Exception:
            mode = "?"
        state = str(getattr(e, "state", "") or "").replace("EngineState.", "")

        # Open positions (operator book; live executor first).
        positions = []
        try:
            ex = getattr(e, "live_executor", None)
            if ex is not None and getattr(ex, "open_positions", None):
                positions = list(ex.open_positions)
            elif getattr(e, "portfolio", None) is not None:
                positions = list(e.portfolio.open_positions)
        except Exception:
            pass
        pos_bits = []
        for p in positions[:6]:
            sym = str(getattr(p, "symbol", getattr(p, "asset", "?")))
            sym = sym.replace("/USDT", "").replace(":USDT", "")
            side = str(getattr(p, "direction", getattr(p, "side", "")))[:5].upper()
            pos_bits.append(f"{sym} {side}")

        # Free margin / equity from the venue-aware cache (may be absent).
        equity_bit = ""
        try:
            cache = getattr(e, "_live_balance_cache", None) or {}
            eq = float(cache.get("equity", 0) or 0)
            free = float(cache.get("free", 0) or 0)
            if eq > 0:
                equity_bit = (f"Equity <code>${eq:,.2f}</code> · free margin "
                              f"<code>${free:,.2f}</code>")
        except Exception:
            pass

        if kind == "brief":
            lines.append("🌅 <b>Morning brief — today's plan</b>")
            lines.append(f"Mode <b>{mode}</b>" + (f" · engine <code>{_html.escape(state)}</code>" if state else ""))
            if equity_bit:
                lines.append(equity_bit)
            lines.append(
                f"Carrying <b>{len(positions)}</b> open position(s)"
                + (f": {_html.escape(', '.join(pos_bits))}" if pos_bits else "")
                + " — managing SL/TP and scanning the universe for setups "
                  "at or above the auto-trade confidence gate.")
            lines.append("<i>/status for detail · /whynot SYMBOL to see why "
                         "something isn't being traded.</i>")
        else:
            lines.append("🌙 <b>Evening wrap</b>")
            lines.append(f"Mode <b>{mode}</b>" + (f" · engine <code>{_html.escape(state)}</code>" if state else ""))
            if equity_bit:
                lines.append(equity_bit)
            # Recent closed trades (live book) — count + net, best/worst.
            try:
                ex = getattr(e, "live_executor", None)
                closed = list(getattr(ex, "closed_positions", []) or [])[-20:]
                if closed:
                    # `getattr(t, "pnl_usd", 0) or 0` over LivePosition, whose
                    # pnl_usd is Optional[float] = None: an unpriced close was
                    # summed as break-even AND counted as a non-win in a
                    # full-set denominator. bot/utils/win_rate.py exists to be
                    # the single answer to both and six other sites already
                    # use it; this digest went its own way.
                    from bot.utils.win_rate import pnl_stats, win_stats
                    ws = win_stats(closed)
                    ps = pnl_stats(closed)
                    net_bit = ("net <code>—</code>" if ps["total"] is None
                               else f"net <code>${ps['total']:+,.2f}</code>")
                    unpriced = (f" · {ws['unscored']} unpriced"
                                if ws["unscored"] else "")
                    lines.append(
                        f"Recent closes: <b>{len(closed)}</b> "
                        f"(<b>{ws['wins']}</b> wins of {ws['scored']} priced) · "
                        f"{net_bit}{unpriced}")
            except Exception:
                pass
            lines.append(
                f"Still open: <b>{len(positions)}</b>"
                + (f" — {_html.escape(', '.join(pos_bits))}" if pos_bits else ""))
            lines.append("<i>/daily_report for the full report · "
                         "/yield checks what idle cash could earn.</i>")
        return "\n\n".join(lines)

    def _check_learning_readiness(self) -> list[Alert]:
        """Alert when a learner BECOMES validated-and-ready — the moment the
        operator can act on the learning loop instead of remembering to poll
        /readiness. Assessment reads the decision store, so it runs on a slow
        cadence (hourly), not every 30s tick."""
        alerts: list[Alert] = []
        if not CONFIG.analyzer.learning_readiness_alert_enabled:
            return alerts
        now = time.time()
        if now < self._readiness_next_check:
            return alerts
        self._readiness_next_check = now + 3600.0
        try:
            from bot.learning.readiness import assess_readiness, render_report
            assessment = assess_readiness()
        except Exception as exc:
            logger.debug("readiness check failed: %s", exc)
            return alerts
        for name, comp in assessment.get("components", {}).items():
            state = comp.get("state", "?")
            prev = self._readiness_states.get(name)
            self._readiness_states[name] = state
            # First observation seeds the baseline silently; only a genuine
            # transition INTO READY (while not yet applied) alerts.
            if prev is None or state != "READY" or prev == "READY":
                continue
            if comp.get("applied") is True:
                continue
            alerts.append(Alert(
                alert_type="LEARNING_READY",
                severity="INFO",
                title=f"Learning component ready: {name}",
                body=("\U0001f9e0 <b>LEARNING COMPONENT VALIDATED</b>\n"
                      "────────────────\n"
                      f"<b>{name}</b> now clears its evidence bar and is "
                      "ready to apply.\n\n" + render_report(assessment) +
                      "\n\n\U0001f449 /readiness — full report"),
                dedup_key=f"learning_ready_{name}",
            ))
        return alerts

    _CLASS_ICON = {
        "Crypto": "\U0001fa99", "Stock": "\U0001f4c8", "ETF": "\U0001f4ca",
        "Commodity": "\U0001f6e2", "Metal": "⚙️",
        "Pre-IPO": "\U0001f680", "Forex": "\U0001f4b1",
    }

    def _check_new_listings(self) -> list[Alert]:
        """Surface new exchange listings the catalog watch queued during
        scans. New crypto / *STOCK perps already trade automatically; the
        point here is telling the operator the catalog changed — above all
        for bare-ticker TradFi listings that the classifier can only call
        Crypto until a config entry names them."""
        alerts: list[Alert] = []
        try:
            watch = getattr(getattr(self.engine, "scanner", None),
                            "_catalog_watch", None)
            if watch is None:
                return alerts
            events = watch.drain_pending()
            if not events:
                return alerts
            lines = []
            for ev in events[:25]:
                sym = str(ev.get("symbol", "?"))
                cat = str(ev.get("category", "Crypto"))
                icon = self._CLASS_ICON.get(cat, "\U0001fa99")
                vol = float(ev.get("vol_usd", 0.0) or 0.0)
                vol_s = f" · ${vol/1e6:.1f}M/day" if vol > 0 else ""
                lines.append(f"{icon} <code>{sym}</code> — {cat}{vol_s}")
            more = len(events) - 25
            if more > 0:
                lines.append(f"…and {more} more")
            syms = sorted(str(ev.get("symbol", "")) for ev in events)
            alerts.append(Alert(
                alert_type="NEW_LISTINGS",
                severity="INFO",
                title=f"{len(events)} new exchange listing(s)",
                body=("\U0001f195 <b>NEW EXCHANGE LISTINGS</b>\n"
                      "────────────────\n"
                      + "\n".join(lines) +
                      "\n────────────────\n"
                      "New crypto and *STOCK perps join the scan universe "
                      "automatically. If a name above is really a stock/"
                      "commodity/ETF but shows as Crypto, it needs a config "
                      "entry to get the right volume floor and session "
                      "sizing — say the word and I'll add it."),
                dedup_key="new_listings_" + ",".join(syms)[:120],
            ))
        except Exception as exc:
            logger.debug("new-listings check failed: %s", exc)
        return alerts

    def _check_self_audit(self) -> list[Alert]:
        """Deliver the nightly self-audit report the moment a run finishes.
        The audit itself runs in the engine (background task); this check
        only drains its queue — same pattern as the new-listings watch."""
        alerts: list[Alert] = []
        try:
            from bot.core.self_audit import SELF_AUDIT
            for item in SELF_AUDIT.drain_pending():
                report = str(item.get("report", "")).strip()
                if not report:
                    continue
                alerts.append(Alert(
                    alert_type="SELF_AUDIT",
                    severity="INFO",
                    title="Nightly self-audit report",
                    body=report + "\n\n\U0001f449 /audit — re-show this report",
                    dedup_key=f"self_audit_{int(item.get('ts', 0))}",
                ))
        except Exception as exc:
            logger.debug("self-audit check failed: %s", exc)
        return alerts

    def _check_signal_strangle(self) -> list[Alert]:
        """Silent-strangle watchdog: ideas keep flowing but NOTHING has been
        approved for a whole window — the failure shape of a silently latched
        gate. (The soft loss-streak latch ran a production backtest dry for
        ~8 months with zero operator-visible signal: the bot scans, generates
        ideas, and rejects every one.) Names the top rejecting gate so the
        operator knows WHERE the flow died, not just that it died."""
        alerts: list[Alert] = []
        window_s = CONFIG.risk.strangle_alert_hours * 3600.0
        if window_s <= 0:
            return alerts
        try:
            stats = self.engine.risk.eval_stats()
            fails = {k: v.get("failed", 0)
                     for k, v in self.engine.risk.gate_stats().items()}
        except Exception:
            return alerts

        now = time.time()
        snaps = self._strangle_snaps
        snaps.append((now, stats["evaluated"], stats["approved"], fails))
        while snaps and now - snaps[0][0] > 2 * window_s:
            snaps.popleft()

        # Baseline = the newest snapshot that is at least one window old.
        base = None
        for s in snaps:
            if now - s[0] >= window_s:
                base = s
            else:
                break
        if base is None:
            return alerts

        evals_d = stats["evaluated"] - base[1]
        approved_d = stats["approved"] - base[2]
        if evals_d < CONFIG.risk.strangle_min_ideas or approved_d > 0:
            return alerts
        if now - self._last_strangle_alert < window_s:
            return alerts   # persists — re-alert once per window, not per tick

        gate_deltas = {k: v - base[3].get(k, 0) for k, v in fails.items()}
        top_gate, top_fails = max(gate_deltas.items(),
                                  key=lambda kv: kv[1], default=("?", 0))
        streak = {}
        try:
            streak = self.engine.risk.streak_state()
        except Exception:
            pass
        probe_line = ""
        if streak.get("latched"):
            p = streak.get("probe_in_seconds")
            probe_line = (
                f"- Loss streak: <code>{streak.get('consecutive_losses')}"
                f"/{streak.get('soft_limit')} soft</code>"
                + (f" — probe trade in <code>{p / 3600.0:.1f}h</code>\n"
                   if p is not None and p > 0 else
                   " — probe trade ALLOWED now\n" if p is not None else
                   " — probing disabled\n"))

        hours = CONFIG.risk.strangle_alert_hours
        self._last_strangle_alert = now
        alerts.append(Alert(
            alert_type="SIGNAL_STRANGLE",
            severity="WARNING",
            title="Signal flow strangled",
            body=(
                "⚠️ <b>SIGNAL FLOW STRANGLED</b>\n"
                "────────────────\n"
                f"<code>{evals_d}</code> ideas evaluated in the last "
                f"<code>{hours:.0f}h</code> — <b>zero approved</b>.\n\n"
                f"- Top rejecting gate: <code>{top_gate}</code> "
                f"(<code>{top_fails}</code> rejections)\n"
                + probe_line +
                "\nThe bot is scanning but cannot trade. If this is not "
                "intentional (breaker/streak protection doing its job), a "
                "gate may be latched or misconfigured.\n"
                "────────────────\n"
                "\U0001f449 /gates — per-gate pass/fail counters\n"
                "\U0001f449 /status — engine + breaker state\n"
                "\U0001f449 /whynot — why the last idea was rejected"
            ),
            dedup_key="signal_strangle",
        ))
        return alerts

    def _check_circuit_breaker(self) -> list[Alert]:
        """Alert on circuit breaker state changes."""
        alerts = []
        cb_active = self.engine.risk.circuit_breaker_active

        if cb_active and not self._last_cb_state:
            # Gather live context for the alert. Read the REAL trip cause and the
            # live accumulators — the old code read non-existent attrs
            # (risk.current_drawdown_pct / risk.daily_pnl) and the empty PAPER
            # portfolios, so a live trip always showed "Drawdown N/A, Daily P&L
            # N/A, Open Positions 0" even with real positions (operator report:
            # "message is not correct").
            cause = getattr(self.engine.risk, 'circuit_trip_cause', '') or 'unknown'
            _dl = getattr(self.engine.risk, 'last_known_daily_loss_pct', None)
            daily_pnl_str = f"-{_dl:.2f}% (of equity)" if _dl else "N/A"
            # Drawdown reason is shown via the cause line; the exact live % isn't
            # separately retained, so present it only when it IS the cause.
            drawdown_str = "see cause" if cause == "drawdown" else "N/A"
            # Live open-position count (operator account), not the paper books.
            positions_count = 0
            try:
                ex = getattr(self.engine, 'live_executor', None)
                if ex is not None:
                    positions_count = len(getattr(ex, 'open_positions', []) or [])
            except Exception:
                pass
            ts = datetime.now(UTC).strftime("%H:%M:%S UTC")

            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="CRITICAL",
                title="Circuit Breaker TRIPPED",
                body=(
                    "\U0001f6a8 <b>CIRCUIT BREAKER TRIPPED</b>\n"
                    "────────────────\n"
                    "The risk engine has <b>halted all new entries</b>.\n\n"
                    f"- Reason: <code>{cause}</code>\n"
                    f"- Drawdown: <code>{drawdown_str}</code>\n"
                    f"- Daily loss: <code>{daily_pnl_str}</code>\n"
                    f"- Open Positions: <code>{positions_count}</code>\n"
                    f"- Triggered At: <code>{ts}</code>\n\n"
                    "If the reason looks wrong (e.g. a stale drawdown after an "
                    "auth blip), <code>/resume</code> re-seeds the high-water "
                    "mark and clears it.\n\n"
                    "\U0001f6e1 Open positions are still monitored for SL/TP.\n"
                    "────────────────\n"
                    "\U0001f449 /status — review engine state\n"
                    "\U0001f449 /positions — inspect open trades\n"
                    "\U0001f449 /reset — clear after review"
                ),
                dedup_key="cb_tripped",
            ))
        elif not cb_active and self._last_cb_state:
            ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="INFO",
                title="Circuit Breaker Cleared",
                body=(
                    "\u2705 <b>CIRCUIT BREAKER CLEARED</b>\n"
                    "────────────────\n"
                    "Risk limits are back within tolerance.\n"
                    "Trading operations have <b>resumed</b>.\n\n"
                    f"- Cleared At: <code>{ts}</code>\n\n"
                    "\U0001f680 The engine will begin scanning on the next cycle.\n"
                    "────────────────\n"
                    "\U0001f449 /status — confirm engine state\n"
                    "\U0001f449 /health — check system vitals"
                ),
                dedup_key="cb_cleared",
            ))

        self._last_cb_state = cb_active
        return alerts

    def _check_drawdown_tiers(self) -> list[Alert]:
        """Early-warning alerts as drawdown approaches the circuit-breaker limit.

        Fires once at 50%, 75%, 85% of MAX_DRAWDOWN_PCT so the operator can act
        BEFORE the breaker halts trading. Re-arms only after drawdown recovers to
        a lower tier (tracked via _last_dd_tier), so it doesn't spam."""
        alerts: list[Alert] = []
        try:
            dd = getattr(self.engine.risk, "current_drawdown_pct", None)
            limit = float(getattr(CONFIG.risk, "max_drawdown_pct", 0) or 0)
            if dd is None or limit <= 0:
                return alerts
            frac = float(dd) / limit
            tier = 85 if frac >= 0.85 else (75 if frac >= 0.75 else (50 if frac >= 0.50 else 0))
            if tier > self._last_dd_tier and tier > 0:
                sev = "CRITICAL" if tier >= 85 else "WARNING"
                alerts.append(Alert(
                    alert_type="DRAWDOWN_TIER", severity=sev,
                    title=f"Drawdown {tier}% of limit",
                    body=(
                        f"⚠️ <b>DRAWDOWN AT {tier}% OF LIMIT</b>\n"
                        "────────────────\n"
                        f"- Current drawdown: <code>{float(dd):.2f}%</code>\n"
                        f"- Circuit-breaker limit: <code>{limit:.2f}%</code>\n\n"
                        "The risk engine halts all entries at 100% of the limit.\n"
                        "Consider reducing size or reviewing open risk now.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine state\n"
                        "\U0001f449 /positions — inspect open trades"),
                    dedup_key=f"dd_tier_{tier}"))
            # Re-arm tiers once we drop below them (frac fell).
            self._last_dd_tier = tier
        except Exception as exc:
            system_log.debug("drawdown-tier check failed: %s", exc)
        return alerts

    def _check_tick_failures(self) -> list[Alert]:
        """Alert when the engine's main loop has failed repeatedly — positions
        may be silently unmonitored (SL/TP not firing)."""
        alerts: list[Alert] = []
        try:
            fails = int(getattr(self.engine, "_tick_consecutive_failures", 0) or 0)
            degraded = fails >= 3
            if degraded and not self._last_tick_degraded:
                # Carry the CAUSE with the symptom. The engine already knows
                # which phase blew its cap; without this the operator reads
                # "failed 3 times in a row" and has to go find a log they may
                # have no way to reach.
                cause = ""
                pt = getattr(self.engine, "_last_phase_timeout", None)
                if isinstance(pt, dict) and pt.get("phase"):
                    cause = (f"Cause: phase <b>{pt['phase']}</b> exceeded its "
                             f"{float(pt.get('cap_s') or 0):.0f}s cap.\n")
                alerts.append(Alert(
                    alert_type="TICK_FAILURE", severity="CRITICAL",
                    title="Engine loop degraded",
                    body=(
                        "\U0001f6a8 <b>ENGINE LOOP DEGRADED</b>\n"
                        "────────────────\n"
                        f"The main loop has failed <b>{fails}</b> times in a row.\n"
                        f"{cause}"
                        "Scanning and position monitoring may be impaired — "
                        "open positions could be <b>unmonitored</b>.\n"
                        "────────────────\n"
                        "\U0001f449 /status — check engine state\n"
                        "\U0001f449 /positions — verify SL/TP are in place"),
                    dedup_key="tick_degraded"))
            self._last_tick_degraded = degraded
        except Exception as exc:
            system_log.debug("tick-failure check failed: %s", exc)
        return alerts

    # The one other piece of network I/O in this loop, same contract as the
    # news radar above: throttled, bounded, and it never raises.
    async def _probe_public_gateway(self) -> None:
        """Prove the WEBSITE's path to this bot still works, from this side.

        The website reaches the bot over a public URL. When that broke there
        was nothing to notice it: the website has no alert channel, and the
        bot never knew it was being reached until it wasn't — so the first
        report came from a person opening the page. An ephemeral tunnel URL
        rotating on restart breaks it silently and looks exactly like a
        firewall.

        Probing from HERE closes that, because this process is the one that
        can page an operator. The result distinguishes the two faults, which
        have different remedies:

          unreachable  the URL is wrong/expired or the path is blocked
          forbidden    reachable, but the shared secret no longer matches

        Never raises and never blocks the loop for long: one bounded request
        on a slow interval.
        """
        url = str(getattr(CONFIG.monitoring, "public_gateway_url", "") or "").strip()
        if not url:
            return
        every = float(getattr(CONFIG.monitoring, "public_gateway_probe_interval_s", 300.0) or 300.0)
        now = time.monotonic()
        if self._gw_probe_at and (now - self._gw_probe_at) < every:
            return
        self._gw_probe_at = now

        secret = (os.environ.get("WEB_GATEWAY_SECRET") or "").strip()
        result: dict = {"state": "unreachable", "status": None}
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"X-Gateway-Secret": secret} if secret else {}
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url.rstrip("/") + "/gateway/health",
                                    headers=headers) as resp:
                    result["status"] = resp.status
                    if resp.status == 200:
                        result["state"] = "ok"
                    elif resp.status in (401, 403):
                        result["state"] = "forbidden"
                    else:
                        result["state"] = "error"
        except Exception as exc:
            result["state"] = "unreachable"
            system_log.debug("public gateway probe failed: %s", exc)

        prev = self._gw_probe or {}
        fails = int(prev.get("consecutive_failures") or 0)
        result["consecutive_failures"] = 0 if result["state"] == "ok" else fails + 1
        self._gw_probe = result

    # Consecutive probe failures before paging. One is a blip; two in a row on
    # a 5-minute interval is a real outage of the website's path to the bot.
    GATEWAY_PROBE_ALERT_AT = 2

    # ── Self-hosted LLM origin ──────────────────────────────────────
    #
    # THE GATEWAY PROBE'S DOCSTRING DESCRIBES THIS FAILURE EXACTLY, one service
    # over: "an ephemeral tunnel URL rotating on restart breaks it silently and
    # looks exactly like a firewall." The in-house model is served from a
    # machine the operator controls, reached over a tunnel, and when that URL
    # rotates every call to it fails — but the LLM fallback chain catches them,
    # so nothing surfaces. The symptom is not an error. It is the in-house
    # model quietly never being used again, and slightly slower analysis.
    #
    # Observed 2026-08-19: the configured URL had been dead long enough that
    # the tunnel it named no longer existed, with all three routed tiers
    # (chat, scan, thesis) pointing at it, and the logs showed nothing.

    #: Two consecutive failures before paging, matching the gateway probe: one
    #: is a blip, two on a five-minute interval is an outage.
    LLM_PROBE_ALERT_AT = 2
    #: The tiers an operator can pin to a provider, and the providers that mean
    #: "something you host yourself" — the only ones with an origin that can
    #: vanish. A hosted API going down is Anthropic's problem, not a tunnel.
    _LLM_TIERS = ("SCAN", "THESIS", "LEARNING", "CHAT")
    _SELF_HOSTED = ("runeclaw", "ollama")

    def _llm_origin(self) -> tuple[str, str, str]:
        """(base_url, api_key, tier_name) for a self-hosted tier, else ("","","").

        Cheap and network-free: reads the same env the resolver reads. Returns
        the FIRST self-hosted tier found — one probe covers them all, since in
        practice they share one endpoint.
        """
        for t in self._LLM_TIERS:
            prov = (os.environ.get(f"LLM_TIER_{t}_PROVIDER") or "").strip().lower()
            if prov in self._SELF_HOSTED:
                env = "RUNECLAW_LLM" if prov == "runeclaw" else "OLLAMA"
                url = (os.environ.get(f"{env}_BASE_URL") or "").strip()
                key = (os.environ.get(f"{env}_API_KEY") or "").strip()
                return url, key, t.lower()
        return "", "", ""

    async def _probe_llm_endpoint(self) -> None:
        """Ask the self-hosted model endpoint whether it is still there.

        PROBED WITH THE CREDENTIAL THE BOT WOULD SEND, which is the whole
        lesson of the gateway incident: an unauthenticated check returns the
        same 401 for a healthy endpoint and for one behind an Access policy the
        bot can never pass, so it cannot fail and proves nothing. Sending the
        key separates them.

        Four outcomes, because they have four different remedies:

          ok            reachable, authenticated, serving the configured model
          model_missing reachable and authenticated, but the model named by
                        LLM_TIER_*_MODEL is not on this server — every call
                        404s, and the endpoint looks perfectly healthy
          forbidden     reachable, key rejected (wrong key, or an Access policy
                        in front, in which case no key will ever work)
          unreachable   the URL is dead — the usual case, a rotated tunnel

        Never raises, never blocks the loop for long: one bounded request on a
        slow interval, and only when a tier is actually pinned to a self-hosted
        provider — operators on hosted APIs get no probe and no noise.
        """
        url, key, tier = self._llm_origin()
        if not url:
            self._llm_probe = None      # nothing to claim about
            return
        every = float(getattr(CONFIG.monitoring, "llm_probe_interval_s", 300.0) or 300.0)
        now = time.monotonic()
        if self._llm_probe_at and (now - self._llm_probe_at) < every:
            return
        self._llm_probe_at = now

        want = (os.environ.get(f"LLM_TIER_{tier.upper()}_MODEL") or "").strip()
        result: dict = {"state": "unreachable", "status": None, "tier": tier,
                        "model": want, "host": _host_of(url)}
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url.rstrip("/") + "/models", headers=headers) as resp:
                    result["status"] = resp.status
                    if resp.status in (401, 403):
                        result["state"] = "forbidden"
                    elif resp.status == 200:
                        result["state"] = "ok"
                        if want:
                            try:
                                body = await resp.json()
                                served = {str(m.get("id", "")) for m in (body.get("data") or [])}
                                if served and want not in served:
                                    result["state"] = "model_missing"
                                    result["served"] = sorted(served)[:6]
                            except Exception:
                                pass   # unreadable list is not evidence of absence
                    else:
                        result["state"] = "error"
        except Exception as exc:
            result["state"] = "unreachable"
            logger.debug("llm endpoint probe failed: %s", exc)

        prev = self._llm_probe or {}
        fails = int(prev.get("consecutive_failures") or 0)
        result["consecutive_failures"] = 0 if result["state"] == "ok" else fails + 1
        self._llm_probe = result

    def _check_llm_endpoint(self) -> list[Alert]:
        """Page when the self-hosted model has been unreachable twice running.

        The host is named, never the key — a probe result is a message to a
        person, and /readyz answers with a coarse reason for the same reason.
        """
        p = self._llm_probe
        if not p:
            return []
        fails = int(p.get("consecutive_failures") or 0)
        state = str(p.get("state") or "")
        tier = str(p.get("tier") or "?")
        host = str(p.get("host") or "the configured URL")

        if state == "ok":
            if self._llm_alerted_state and self._llm_alerted_state != "ok":
                self._llm_alerted_state = "ok"
                return [Alert(
                    alert_type="STATE_CHANGE", severity="INFO",
                    title="Self-hosted model reachable again",
                    body=("\u2705 <b>IN-HOUSE MODEL BACK</b>\n"
                          f"The self-hosted endpoint at <code>{host}</code> is "
                          "answering again. Routed tiers are using it once more."),
                    dedup_key="llm_endpoint_recovered")]
            return []

        if fails < self.LLM_PROBE_ALERT_AT:
            return []
        if self._llm_alerted_state == state:
            return []          # same fault, already said; dedup handles repeats
        self._llm_alerted_state = state

        if state == "model_missing":
            served = ", ".join(p.get("served") or []) or "none listed"
            why = (f"The endpoint answers and the key works, but the model "
                   f"<code>{p.get('model')}</code> is not served there.\n"
                   f"It offers: <code>{served}</code>.\n"
                   "Every call to this tier will 404 while the endpoint looks healthy.")
        elif state == "forbidden":
            why = ("The endpoint is reachable and REFUSED the key "
                   f"(HTTP {p.get('status')}).\n"
                   "Either the key is wrong, or an access policy sits in front "
                   "of the tunnel — in which case no API key will ever pass.")
        else:
            why = ("No answer from the self-hosted model endpoint.\n"
                   "A quick tunnel URL is bound to the cloudflared PROCESS and "
                   "changes whenever it restarts; a named tunnel's hostname does "
                   "not. See scripts/cloudflared/README.md.")

        # `sep` as a NAME, never "..." "\u2500" * 16 inline. Adjacent string
        # literals concatenate before `*` applies, so the line above gets
        # repeated sixteen times with it — which is exactly how the severe
        # anomaly card printed its header sixteen deep, in this file, and the
        # guard written for that was scoped to that one card.
        sep = "\u2500" * 16
        return [Alert(
            alert_type="STATE_CHANGE", severity="WARNING",
            title="Self-hosted model unreachable",
            body=("\u26a0\ufe0f <b>IN-HOUSE MODEL UNREACHABLE</b>\n"
                  f"{sep}\n"
                  f"- Host: <code>{host}</code>\n"
                  f"- Routed tier: <code>{tier}</code>\n"
                  f"- Failed checks: <code>{fails}</code>\n\n"
                  f"<i>{why}</i>\n"
                  f"{sep}\n"
                  "\u26a0\ufe0f Trading is UNAFFECTED — the LLM fallback chain "
                  "is answering these tiers from another provider, which is "
                  "why nothing else looks wrong. The in-house model is simply "
                  "not being used."),
            dedup_key="llm_endpoint_down")]

    def _check_public_gateway(self) -> list[Alert]:
        """Page when the WEBSITE can no longer reach this bot, and say which
        fault it is. Edge-triggered on the STATE, so a recovery re-arms it and
        a change of fault (unreachable -> forbidden) pages again."""
        alerts: list[Alert] = []
        try:
            p = self._gw_probe
            if not isinstance(p, dict):
                return alerts
            state = str(p.get("state") or "")
            if state == "ok":
                if self._gw_alerted_state and self._gw_alerted_state != "ok":
                    self._gw_alerted_state = "ok"
                    sep = "\u2500" * 16
                    alerts.append(Alert(
                        alert_type="GATEWAY_OK", severity="INFO",
                        title="Website can reach the bot again",
                        body=("\u2705 <b>WEB GATEWAY RECOVERED</b>\n"
                              f"{sep}\n"
                              "The website's path to this bot is working again — "
                              "chat and web trade are back."),
                        dedup_key="gateway_recovered"))
                else:
                    self._gw_alerted_state = "ok"
                return alerts
            if int(p.get("consecutive_failures") or 0) < self.GATEWAY_PROBE_ALERT_AT:
                return alerts
            if self._gw_alerted_state == state:
                return alerts
            self._gw_alerted_state = state
            sep = "\u2500" * 16
            if state == "forbidden":
                why = ("The bot answered but REJECTED the shared secret.\n"
                       "WEB_GATEWAY_SECRET differs between the website and this bot.")
            elif state == "unreachable":
                why = ("No answer at the public gateway URL.\n"
                       "The URL has changed or expired (an ephemeral tunnel does this "
                       "on every restart), or the path is blocked.")
            else:
                why = f"The gateway answered with HTTP {p.get('status')}."
            alerts.append(Alert(
                alert_type="GATEWAY_DOWN", severity="WARNING",
                title="Website cannot reach the bot",
                body=("\U0001f7e0 <b>WEB GATEWAY UNREACHABLE</b>\n"
                      f"{sep}\n"
                      f"{why}\n"
                      "Web chat and web trade are down. Trading is unaffected — "
                      "this is the website's path to me, not the exchange.\n"
                      f"{sep}"),
                dedup_key=f"gateway_down_{state}"))
        except Exception as exc:
            system_log.debug("public-gateway check failed: %s", exc)
        return alerts

    def _check_scan_timeouts(self) -> list[Alert]:
        """WARNING when scans keep exceeding their cap.

        A scan timeout is deliberately non-fatal — a slow exchange must not
        take the tick loop down with it, because monitoring money already at
        risk matters more than finding new entries. But non-fatal must never
        mean invisible: the bot would look perfectly healthy while silently
        finding nothing, which is exactly the quiet degradation this codebase
        refuses to ship. Edge-triggered on the repeat count, so one slow scan
        says nothing and a PATTERN pages once.
        """
        alerts: list[Alert] = []
        try:
            pt = getattr(self.engine, "_last_phase_timeout", None)
            if not isinstance(pt, dict) or pt.get("phase") != "scan":
                self._scan_timeout_alerted_at = None
                return alerts
            count = int(pt.get("count") or 0)
            if count < self.SCAN_TIMEOUT_ALERT_AT:
                return alerts
            if self._scan_timeout_alerted_at == count:
                return alerts
            self._scan_timeout_alerted_at = count
            cap = float(pt.get("cap_s") or 0)
            sep = "\u2500" * 16
            alerts.append(Alert(
                alert_type="SCAN_TIMEOUT", severity="WARNING",
                title="Scans are timing out",
                body=(
                    "\U0001f7e0 <b>SCANS TIMING OUT</b>\n"
                    f"{sep}\n"
                    f"The market scan has exceeded its {cap:.0f}s cap "
                    f"<b>{count}</b> times in a row.\n"
                    "New entries are not being found. Open positions are "
                    "still monitored \u2014 the tick loop is intact.\n"
                    "Usually exchange/data latency rather than the bot.\n"
                    f"{sep}\n"
                    "\U0001f449 /status \u2014 engine health\n"
                    "\U0001f449 /fullscan \u2014 force a deep sweep"),
                dedup_key="scan_timeout"))
        except Exception as exc:
            system_log.debug("scan-timeout check failed: %s", exc)
        return alerts

    # A hung tick (a blocked await that never raises) increments no failure
    # counter and is invisible to _check_tick_failures above. This watches the
    # tick loop's START stamp instead. Threshold must clear the worst
    # LEGITIMATE gap — the tick loop backs off up to 300s between failed
    # ticks. The other legitimate gap — the smart-scan quiet-market sleep,
    # whose max (600s default, env-tunable higher) can meet or EXCEED this
    # threshold — is handled precisely instead of by inflating the constant:
    # the engine stamps _next_tick_due_ts before every inter-tick sleep, and
    # time inside a declared sleep (+ grace) never counts as a stall.
    # Root-caused from a production false alarm (15s tick + 600s planned
    # sleep = 615s "stall").
    # Consecutive scan timeouts before paging. One slow scan is weather;
    # three in a row is a condition.
    SCAN_TIMEOUT_ALERT_AT = 3
    TICK_STALL_THRESHOLD_S = 600.0
    TICK_DUE_GRACE_S = 120.0

    @staticmethod
    def _is_tick_stalled(last_started: "float | None", now: float,
                         threshold: float,
                         next_due: "float | None" = None,
                         grace: float = TICK_DUE_GRACE_S) -> bool:
        """Pure staleness predicate. None last_started = engine not started
        yet — never stale (the documented monotonic None-sentinel rule).
        next_due = the engine's declared wake-up time: being inside a planned
        sleep (+ grace) is healthy, not a stall; None falls back to the
        threshold-only rule."""
        if last_started is None or threshold <= 0:
            return False
        if (now - last_started) <= threshold:
            return False
        if next_due is not None and now <= next_due + grace:
            return False
        return True

    @staticmethod
    def _frame_summaries(frames: "list[Any]") -> "list[str]":
        """['engine.py:2182 in run', …] oldest → newest — the LAST line is
        the await the coroutine is parked on."""
        out: list[str] = []
        for f in frames:
            try:
                code = f.f_code
                fname = str(code.co_filename).replace("\\", "/").rsplit("/", 1)[-1]
                out.append(f"{fname}:{f.f_lineno} in {code.co_name}")
            except Exception:
                continue
        return out

    @staticmethod
    def _await_chain_frames(task: "Any", max_depth: int = 60) -> "list[Any]":
        """Every frame from the task's outermost coroutine down to the await
        it is actually parked on.

        WHY THIS EXISTS (production incident, 2026-07-28): the diagnosis used
        Task.get_stack() alone and reported a ONE-FRAME stack —
        "engine.py:2226 in run" — for a real 900s hang. That is the line
        `await self._tick_guarded()`: the outermost suspension point, and
        nothing about where the tick actually parked. get_stack() does NOT
        descend into nested awaited coroutines; a self-diagnosing alert that
        cannot name the culprit is a stall alert with the diagnosis missing.

        The chain has to be walked explicitly: a suspended coroutine exposes
        what it awaits as `cr_await` (async generators: `ag_await`, legacy
        generators: `gi_yieldfrom`). Following that link yields the real
        innermost frame. Cycle-guarded and depth-capped; a Future/Task at the
        end of the chain simply terminates the walk (it has no frame).
        """
        frames: list[Any] = []
        seen: set[int] = set()
        try:
            obj = task.get_coro()
        except Exception:
            return frames
        depth = 0
        while obj is not None and depth < max_depth and id(obj) not in seen:
            seen.add(id(obj))
            depth += 1
            fr = (getattr(obj, "cr_frame", None)
                  or getattr(obj, "gi_frame", None)
                  or getattr(obj, "ag_frame", None))
            if fr is not None:
                frames.append(fr)
            nxt = (getattr(obj, "cr_await", None)
                   or getattr(obj, "ag_await", None)
                   or getattr(obj, "gi_yieldfrom", None))
            if nxt is None:
                # A Task/Future ends the coroutine chain — but a Task wraps
                # its own coroutine, so step into it rather than stopping at
                # a name-less boundary.
                inner = getattr(obj, "get_coro", None)
                nxt = inner() if callable(inner) else None
            obj = nxt
        return frames

    def _stall_diagnosis(self) -> "list[str]":
        """Best-effort stack of the engine's hung run/tick task.

        The monitor shares the engine's event loop, so when TICK_STALL fires
        the loop is demonstrably alive and the hang is a parked await — whose
        suspended frames are readable in place. The engine task is the one
        whose OUTERMOST frame is Engine.run (a stuck post-tick maintenance
        await still matches; '_tick' alone would not).

        Task.get_stack() identifies the task but reports only that outermost
        frame, so the DEEP chain comes from _await_chain_frames — the line
        that actually names the parked call. Falls back to the shallow stack
        if the walk yields nothing. Fail-open: any error returns [].
        """
        try:
            for task in asyncio.all_tasks():
                try:
                    frames = task.get_stack()
                except Exception:
                    continue
                if not frames:
                    continue
                code = frames[0].f_code
                if code.co_name == "run" and str(code.co_filename).endswith("engine.py"):
                    deep = self._await_chain_frames(task)
                    return self._frame_summaries(deep or frames)
        except Exception:
            pass
        return []

    def _check_engine_tick_stale(self) -> list[Alert]:
        """CRITICAL when the engine tick loop has not STARTED a tick for far
        longer than any legitimate backoff — a hang, not a crash (crashes are
        counted and caught by _check_tick_failures). Edge-triggered: fires once
        per stall, re-arms when the loop moves again."""
        alerts: list[Alert] = []
        last = getattr(self.engine, "_last_tick_started_ts", None)
        stalled = self._is_tick_stalled(
            last, time.monotonic(), self.TICK_STALL_THRESHOLD_S,
            next_due=getattr(self.engine, "_next_tick_due_ts", None))
        # One hang, one page. The alert is edge-triggered, but re-arming on
        # "predicate reads healthy" let a moving _next_tick_due_ts re-arm it
        # DURING a hang: the operator got repeated CRITICAL pages 13s apart
        # for one stall (2026-07-28). Re-arm only when the tick has actually
        # MOVED — a changed _last_tick_started_ts is the only proof of that.
        if stalled and self._tick_stale_alerted_for != last:
            self._tick_stale_alerted_for = last
            self._tick_stale_alerted = True
            age = time.monotonic() - last
            # Self-diagnosing alert: capture WHERE the loop is parked. Full
            # chain to the system log; the innermost await into the alert so
            # the operator (and the next debugging session) see the culprit
            # line without shelling into the host.
            stack = self._stall_diagnosis()
            if stack:
                system_log.error(
                    "TICK_STALL stack (oldest → newest): %s", " <- ".join(stack))
            hung_at = (f"\n\nHung awaiting: {stack[-1]}" if stack else "")
            alerts.append(Alert(
                alert_type="TICK_STALL",
                severity="CRITICAL",
                title="Engine loop stalled",
                body=(f"The engine tick loop has not started a cycle for "
                      f"{age:.0f}s (threshold {self.TICK_STALL_THRESHOLD_S:.0f}s). "
                      f"This looks like a HANG, not a crash: positions are NOT "
                      f"being monitored. Check the process and consider a restart."
                      f"{hung_at}"),
                dedup_key="tick_stall",
            ))
        elif not stalled and last != self._tick_stale_alerted_for:
            self._tick_stale_alerted = False
            self._tick_stale_alerted_for = None
        return alerts

    def _check_warning_rate_breaker(self) -> list[Alert]:
        """Alert when the infrastructure warning-rate breaker trips — signal
        generation is being suppressed by repeated errors (API/auth/WS)."""
        alerts: list[Alert] = []
        try:
            tripped = bool(getattr(self.engine.risk, "warning_rate_breaker_active", False))
            if tripped and not self._last_warn_rate:
                key = getattr(self.engine.risk, "_warning_rate_trip_key", "")
                alerts.append(Alert(
                    alert_type="WARNING_RATE", severity="WARNING",
                    title="Warning-rate breaker tripped",
                    body=(
                        "\U0001f7e0 <b>WARNING-RATE BREAKER TRIPPED</b>\n"
                        "────────────────\n"
                        "Repeated infrastructure warnings have <b>suppressed new "
                        "entries</b> (existing positions are still monitored).\n"
                        f"- Trigger: <code>{key or 'n/a'}</code>\n\n"
                        "Usually transient (exchange API / WS). It clears as the "
                        "error rate falls.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine health"),
                    dedup_key="warn_rate_tripped"))
            self._last_warn_rate = tripped
        except Exception as exc:
            system_log.debug("warning-rate check failed: %s", exc)
        return alerts

    def _check_llm_degraded(self) -> list[Alert]:
        """Alert when the LLM brain has gone offline — every provider failed for
        N consecutive theses and the analyzer is running on the rule engine. This
        is the live "free-tier quota exhausted" signature that was previously
        silent: the bot keeps trading, but blind, on the rule engine only. Fires
        once when the streak crosses the threshold, and once more (INFO) when a
        live provider answers again. Rule-engine-by-design never trips it."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.analyzer.llm_degraded_alert_enabled:
                return alerts
            analyzer = getattr(self.engine, "analyzer", None)
            if analyzer is None or not hasattr(analyzer, "llm_health"):
                return alerts
            health = analyzer.llm_health()
            streak = int(health.get("degraded_streak", 0) or 0)
            min_streak = int(getattr(
                CONFIG.analyzer, "llm_degraded_alert_min_streak", 3) or 3)
            degraded = streak >= min_streak
            if degraded and not self._last_llm_degraded:
                mins = float(health.get("degraded_seconds", 0.0) or 0.0) / 60.0
                alerts.append(Alert(
                    alert_type="LLM_DEGRADED", severity="CRITICAL",
                    title="LLM brain offline",
                    body=(
                        "\U0001f6a8 <b>LLM BRAIN OFFLINE — RUNNING ON RULES</b>\n"
                        "────────────────\n"
                        f"Every LLM provider has failed for <b>{streak}</b> "
                        "analyses in a row"
                        + (f" (~{mins:.0f} min)" if mins >= 1 else "") + ".\n"
                        "The bot is still scanning and trading, but on the "
                        "<b>rule engine only</b> — no AI thesis, weaker signals.\n\n"
                        + (("Last error: <code>"
                            + _html.escape(str(health.get("last_error", ""))[:160])
                            + "</code>\n")
                           if health.get("last_error") else
                           "Usual cause: free-tier API quota exhausted (429 / "
                           "RESOURCE_EXHAUSTED) across every provider.\n")
                        + "────────────────\n"
                        "\U0001f449 Add or rotate an LLM API key (paid tier "
                        "avoids the daily quota wall).\n"
                        "\U0001f449 /llmstatus — current provider + key"),
                    dedup_key="llm_degraded"))
            elif not degraded and self._last_llm_degraded:
                alerts.append(Alert(
                    alert_type="LLM_RESTORED", severity="INFO",
                    title="LLM brain restored",
                    body="✅ <b>LLM brain restored</b> — a provider answered "
                         "again. AI theses are back online.",
                    dedup_key="llm_restored"))
            self._last_llm_degraded = degraded
        except Exception as exc:
            system_log.debug("llm-degraded check failed: %s", exc)
        return alerts

    def _check_ws_health(self) -> list[Alert]:
        """Alert when the price WebSocket has been disconnected for a sustained
        window in live mode (SL/TP monitoring falls back to slower REST polling)."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            ws = getattr(self.engine, "ws_feed", None)
            if ws is None:
                return alerts
            connected = bool(ws.is_connected())
            now = time.monotonic()
            if not connected:
                if self._ws_down_since == 0.0:
                    self._ws_down_since = now
                # Alert once it's been down for > 5 minutes.
                if (now - self._ws_down_since) > 300 and self._last_ws_ok:
                    self._last_ws_ok = False
                    alerts.append(Alert(
                        alert_type="WS_DOWN", severity="WARNING",
                        title="Price feed disconnected",
                        body=(
                            "\U0001f7e0 <b>PRICE WEBSOCKET DISCONNECTED</b>\n"
                            "────────────────\n"
                            "The real-time price feed has been down for "
                            "&gt;5 minutes. SL/TP monitoring is on slower REST "
                            "polling until it reconnects.\n"
                            "────────────────\n"
                            "\U0001f449 /health — check system vitals"),
                        dedup_key="ws_down"))
            else:
                if not self._last_ws_ok:
                    alerts.append(Alert(
                        alert_type="WS_UP", severity="INFO",
                        title="Price feed reconnected",
                        body="✅ <b>Price WebSocket reconnected</b> — "
                             "real-time monitoring restored.",
                        dedup_key="ws_up"))
                self._ws_down_since = 0.0
                self._last_ws_ok = True
        except Exception as exc:
            system_log.debug("ws-health check failed: %s", exc)
        return alerts

    @staticmethod
    def _stale_balance_threshold_s() -> float:
        """Alert only past the WORST legitimate refresh gap. The engine
        refreshes the cache once per tick, and the smart scan legitimately
        sleeps up to smart_scan_max_interval (default 600s) in quiet markets
        — plus the tick's own scan time on top. The previous fixed 300s
        threshold was HALF the configured quiet-market sleep, so every calm
        stretch re-fired a false 'stale balance' alarm (live incident,
        2026-07-20: two spurious alerts ~30min apart on a healthy bot). Same
        headroom reasoning as TICK_STALL_THRESHOLD_S (2x its cap)."""
        try:
            max_interval = float(getattr(
                CONFIG.adaptive, "smart_scan_max_interval", 600) or 600)
        except Exception:
            max_interval = 600.0
        return max(900.0, 1.5 * max_interval)

    def _check_stale_balance(self) -> list[Alert]:
        """Alert when the live balance cache is very stale — position sizing may
        be based on out-of-date equity."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            ts = float(getattr(self.engine, "_live_balance_cache_ts", 0.0) or 0.0)
            if ts <= 0:
                return alerts
            age = time.monotonic() - ts
            if age > self._stale_balance_threshold_s():
                alerts.append(Alert(
                    alert_type="STALE_BALANCE", severity="WARNING",
                    title="Live balance stale",
                    body=(
                        "\U0001f7e0 <b>LIVE BALANCE CACHE STALE</b>\n"
                        "────────────────\n"
                        f"Exchange equity hasn't refreshed in <code>{age/60:.0f} min</code>. "
                        "Position sizing may use out-of-date equity.\n"
                        "────────────────\n"
                        "\U0001f449 /livebalance — force a refresh"),
                    dedup_key="stale_balance"))
        except Exception as exc:
            system_log.debug("stale-balance check failed: %s", exc)
        return alerts

    def _check_unprotected_positions(self) -> list[Alert]:
        """CRITICAL alert (live only) when an open position has NO exchange
        stop-loss after the grace window — i.e. SL placement / self-heal FAILED
        and the position is live with no venue-side protection. A naked leveraged
        perp is account-threatening and was otherwise only logged. Independent of
        the executor's check_positions message flow, so it can't be mislabeled or
        missed. Covers every executor (operator + per-user)."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            grace = float(getattr(CONFIG.execution, "unprotected_alert_grace_seconds", 120.0))
            executors = []
            try:
                executors = list(self.engine._all_live_executors())
            except Exception:
                ex = getattr(self.engine, "live_executor", None)
                if ex is not None:
                    executors = [ex]
            now = datetime.now(UTC)
            for ex in executors:
                for pos in (getattr(ex, "open_positions", []) or []):
                    if getattr(pos, "status", "") != "open":
                        continue
                    opened_at = getattr(pos, "opened_at", None)
                    age = (now - opened_at).total_seconds() if opened_at else 1e9
                    has_sl = bool(getattr(pos, "sl_order_id", None))
                    marked = bool(getattr(pos, "unprotected", False))
                    # Unprotected = no exchange stop (or explicitly flagged) AND
                    # past the placement grace, so self-heal has had its chance.
                    if age < grace or (has_sl and not marked):
                        continue
                    sym = getattr(pos, "symbol", "?")
                    tid = getattr(pos, "trade_id", sym)
                    sl = getattr(pos, "stop_loss", 0.0) or 0.0
                    direction = getattr(pos, "direction", "")
                    # Surface the LAST venue rejection reason for this symbol so
                    # the operator can tell a transient retry apart from a hard
                    # rejection (min-size / wrong-symbol / bad tick) that needs a
                    # different manual fix — not just "it's naked". Best-effort.
                    reason = ""
                    try:
                        _r = ex._last_sltp_reason(sym)
                        if _r:
                            reason = (f"- Venue rejected the stop: "
                                      f"<code>{_html.escape(str(_r)[:160])}</code>\n")
                    except Exception:
                        reason = ""
                    alerts.append(Alert(
                        alert_type="POSITION_UNPROTECTED", severity="CRITICAL",
                        title=f"Unprotected: {sym}",
                        body=(
                            "\U0001f6a8 <b>POSITION UNPROTECTED — NO EXCHANGE STOP</b>\n"
                            "────────────────\n"
                            f"- {sym} <b>{direction}</b> "
                            f"open <code>{age/60:.0f} min</code> with NO venue stop-loss.\n"
                            f"- Intended stop: <code>${sl:,.4f}</code>\n"
                            + reason +
                            "- Self-heal keeps retrying and the local price check is the "
                            "only backstop — a gap/outage could run it unbounded.\n"
                            "────────────────\n"
                            "\U0001f449 Place a stop on Bitget manually now.\n"
                            "\U0001f449 /livepositions — review · /health — vitals"),
                        dedup_key=f"unprotected_{tid}"))
        except Exception as exc:
            system_log.debug("unprotected-position check failed: %s", exc)
        return alerts

    def _check_slippage(self) -> list[Alert]:
        """Alert (live only) when a symbol's mean absolute slippage drifts above
        the configured threshold, once it has enough recorded fills. Execution
        quality silently drains equity over many trades — surfacing it lets the
        operator switch to limit orders, trim size, or drop the symbol."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            tracker = getattr(self.engine, "slippage", None)
            if tracker is None:
                return alerts
            _exec = getattr(CONFIG, "execution", None)
            if _exec is None:
                return alerts
            thresh = float(getattr(_exec, "slippage_alert_mean_pct", 0.20))
            min_trades = int(getattr(_exec, "slippage_alert_min_trades", 10))
            for symbol, stats in (tracker.get_all_stats() or {}).items():
                if stats.total_trades < min_trades:
                    continue
                if stats.mean_slippage_pct <= thresh:
                    continue
                alerts.append(Alert(
                    alert_type="SLIPPAGE_HIGH", severity="WARNING",
                    title=f"High slippage: {symbol}",
                    body=(
                        "\U0001f7e0 <b>EXECUTION SLIPPAGE ELEVATED</b>\n"
                        "────────────────\n"
                        f"- Symbol: <code>{symbol}</code>\n"
                        f"- Mean slippage: <code>{stats.mean_slippage_pct:.3f}%</code> "
                        f"(&gt; {thresh:.3f}% limit)\n"
                        f"- p95: <code>{stats.p95_slippage_pct:.3f}%</code>\n"
                        f"- Fills: <code>{stats.total_trades}</code>, "
                        f"adverse <code>{stats.adverse_count}</code>\n"
                        f"- Est. lost: <code>${stats.total_slippage_usd:,.2f}</code>\n"
                        "────────────────\n"
                        "\U0001f449 Consider limit orders, smaller size, or dropping "
                        "this symbol.\n"
                        "\U0001f449 /slippage — full execution-quality report"),
                    # Dedup per symbol; the 5-min cooldown prevents repeat spam.
                    dedup_key=f"slippage_high_{symbol}"))
        except Exception as exc:
            system_log.debug("slippage check failed: %s", exc)
        return alerts

    def _check_macro_calendar_stale(self) -> list[Alert]:
        """Alert when the macro calendar is EXHAUSTED — the hardcoded schedule has
        aged out, so all macro event protection (FOMC/CPI lockdowns) has silently
        disappeared. With the fail-safe ON the risk engine is now blocking new
        entries (BLACKOUT); either way the operator must refresh the schedule."""
        alerts: list[Alert] = []
        try:
            cal = getattr(self.engine, "macro_calendar", None)
            if cal is None or not hasattr(cal, "is_exhausted"):
                return alerts
            if not cal.is_exhausted():
                return alerts
            fail_closed = bool(getattr(
                CONFIG.risk, "macro_calendar_fail_closed_when_stale", True))
            posture = (
                "New entries are <b>blocked</b> (BLACKOUT) until refreshed."
                if fail_closed else
                "Event protection is <b>OFF</b> (fail-closed disabled) — trades "
                "are running with no macro lockdown."
            )
            alerts.append(Alert(
                alert_type="MACRO_CALENDAR_STALE", severity="CRITICAL",
                title="Macro calendar exhausted",
                body=(
                    "\U0001f7e0 <b>MACRO CALENDAR EXHAUSTED</b>\n"
                    "────────────────\n"
                    "Every scheduled macro event is now in the past — there are "
                    "no future FOMC/CPI/PCE/NFP events to gate against. The "
                    "hardcoded schedule needs regenerating.\n"
                    f"{posture}\n"
                    "────────────────\n"
                    "\U0001f449 Refresh the macro schedule (extend the calendar "
                    "or wire a live feed)."),
                dedup_key="macro_calendar_stale"))
        except Exception as exc:
            system_log.debug("macro-calendar-stale check failed: %s", exc)
        return alerts

    def _held_base_assets(self) -> list[str]:
        """Symbols currently held across all portfolios (shared + per-user), as the
        analyzer sees them (e.g. 'BTC/USDT'). Reused by the news refresh + check."""
        out: list[str] = []
        try:
            up = getattr(self.engine, "user_portfolios", None)
            if up is not None and up.all_portfolios():
                for uid in up.all_portfolios():
                    for pos in (up.get(uid).open_positions or []):
                        if getattr(pos, "asset", None):
                            out.append(pos.asset)
            else:
                pf = getattr(self.engine, "portfolio", None)
                for pos in (getattr(pf, "open_positions", []) or []):
                    if getattr(pos, "asset", None):
                        out.append(pos.asset)
        except Exception:
            pass
        return out

    async def _refresh_news_radar(self) -> None:
        """Throttled, best-effort refresh of the shared news radar so the
        stand-down PUSH check has current headlines. The radar self-throttles
        (NEWS_REFRESH_MIN_SEC) and never raises; pulls only PUBLIC RSS (§4)."""
        try:
            from bot.core.news import NewsRadar
            if not NewsRadar.enabled():
                return
            radar = getattr(self.engine, "_news_radar", None)
            if radar is None:
                radar = self.engine._news_radar = NewsRadar()
            # Bias the symbol match toward what we hold; fall back to majors so
            # the radar still populates when the book is flat.
            held = self._held_base_assets() or ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
            await radar.refresh(symbols=held)
        except Exception as exc:
            system_log.debug("news refresh failed: %s", exc)

    def _check_news_standdown(self) -> list[Alert]:
        """Advisory PUSH: when a FRESH (≤1h) HIGH-impact headline names an asset
        the book holds, nudge the operator to review it. Makes the existing
        pull-only stand-down proactive. Each headline alerts EXACTLY once.

        News stays ADVISORY — this NEVER blocks, sizes, or moves an order (that
        remains the macro gate's job). Gate: NEWS_STANDDOWN_ALERTS (default ON)."""
        alerts: list[Alert] = []
        try:
            from bot.core.news import NewsRadar
            if not NewsRadar.enabled():
                return alerts
            if str(os.getenv("NEWS_STANDDOWN_ALERTS", "1")).strip().lower() in (
                    "0", "false", "no", "off"):
                return alerts
            radar = getattr(self.engine, "_news_radar", None)
            if radar is None:
                return alerts
            held = self._held_base_assets()
            if not held:
                return alerts
            recs = radar.standdown(held, time.time())
            for rec in recs[:5]:                     # cap per cycle — no burst spam
                url = rec.get("url", "") or ""
                sym = rec.get("symbol", "?")
                key = f"{url}|{sym}"
                if key in self._news_alerted:
                    continue
                self._news_alerted.add(key)
                headline = _html.escape(rec.get("headline", "") or "")
                src = _html.escape(rec.get("source", "") or "")
                age_min = max(1, int(rec.get("age_sec", 0)) // 60)
                link = f'\n\U0001f517 {_html.escape(url)}' if url else ""
                alerts.append(Alert(
                    alert_type="NEWS_STANDDOWN", severity="WARNING",
                    title=f"High-impact news · {sym}",
                    body=(
                        f"\U0001f4f0 <b>NEWS ON A POSITION YOU HOLD</b> — {_html.escape(sym)}\n"
                        "────────────────\n"
                        f"<b>{headline}</b>\n"
                        f"<i>{src} · {age_min}m ago</i>{link}\n"
                        "────────────────\n"
                        "\U0001f449 Review it — consider tightening the stop or "
                        "trimming. <b>Advisory only</b>; nothing was changed or "
                        "moved.\n"
                        "\U0001f449 /news — full radar"),
                    # Once-only via _news_alerted; dedup_key adds the 5-min floor.
                    dedup_key=f"news_standdown_{key}"))
            # Keep the once-only set bounded across a long-running process.
            if len(self._news_alerted) > 500:
                self._news_alerted = set(list(self._news_alerted)[-250:])
        except Exception as exc:
            system_log.debug("news-standdown check failed: %s", exc)
        return alerts

    def _check_volume_spikes(self) -> list[Alert]:
        """Alert when the scanner detects volume spikes."""
        alerts = []
        try:
            # Check last scan results from the scanner cache
            if hasattr(self.engine, '_last_scan_signals'):
                for sig in self.engine._last_scan_signals:
                    if sig.volume_spike:
                        key = f"vol_spike_{sig.symbol}"
                        if key not in self._alerted_signals:
                            chg = f"{sig.change_pct_24h:+.1f}%" if sig.change_pct_24h else "N/A"
                            vol_m = sig.volume_usd_24h / 1_000_000 if sig.volume_usd_24h else 0
                            base = sig.symbol.split('/')[0] if '/' in sig.symbol else sig.symbol

                            # Direction hint from 24h change
                            if sig.change_pct_24h and sig.change_pct_24h > 0:
                                direction = "\U0001f7e2 Bullish momentum"
                            elif sig.change_pct_24h and sig.change_pct_24h < 0:
                                direction = "\U0001f534 Bearish pressure"
                            else:
                                direction = "\u26aa Neutral"

                            # Optional RSI
                            rsi = getattr(sig, 'rsi', None)
                            rsi_str = f"<code>{rsi:.1f}</code>" if rsi is not None else "—"

                            # Optional VWAP distance
                            vwap = getattr(sig, 'vwap', None)
                            if vwap and sig.price:
                                vwap_dist = ((sig.price - vwap) / vwap) * 100
                                vwap_str = f"<code>{vwap_dist:+.2f}%</code>"
                            else:
                                vwap_str = "—"

                            alerts.append(Alert(
                                alert_type="VOLUME_SPIKE",
                                severity="WARNING",
                                title=f"Volume Spike: {sig.symbol}",
                                body=(
                                    f"\U0001f4a5 <b>VOLUME SPIKE — {sig.symbol}</b>\n"
                                    "────────────────\n"
                                    f"- Price: <code>${sig.price:,.2f}</code> ({chg})\n"
                                    f"- 24h Volume: <code>${vol_m:,.1f}M</code>\n"
                                    f"- RSI: {rsi_str}\n"
                                    f"- vs VWAP: {vwap_str}\n"
                                    f"- Bias: {direction}\n"
                                    "────────────────\n"
                                    f"\U0001f449 Say \"analyze {base}\" for full technical breakdown\n"
                                    f"\U0001f449 Say \"chart {base}\" to view price chart"
                                ),
                                dedup_key=key,
                            ))
                            self._alerted_signals.add(key)
        except Exception as exc:
            logger.debug("_check_volume_spikes error: %s", exc)
        return alerts

    def _check_black_swan(self) -> list[Alert]:
        """Alert on black-swan detector triggers.

        This ran on every pass of the alert pipeline and returned [] every
        time, because nothing ever set `engine.black_swan` — `hasattr` was
        always False. Now that the detector is constructed in Engine.__init__,
        this code executes for the first time, and it had a bug that could only
        have been found by executing it: `AnomalyAlert.severity` is a FLOAT in
        [0, 1] (`Field(ge=0.0, le=1.0)`), not the string "SEVERE". The
        comparison was always False, so a genuine flash-crash alert would have
        rendered as an orange WARNING. Compared against `_HALT_SEVERITY` — the
        same 0.8 the detector itself uses to decide `halt_recommended` — so the
        alert's colour and the detector's own escalation cannot disagree.
        """
        alerts: list[Alert] = []
        try:
            from bot.core.black_swan import _HALT_SEVERITY
            if not hasattr(self.engine, "black_swan"):
                return []
            raw_alerts = list(self.engine.black_swan.active_alerts)
            if not raw_alerts:
                return []

            # SPLIT ON THE ONLY LINE THAT CHANGES WHAT AN OPERATOR SHOULD DO.
            # At/above _HALT_SEVERITY the detector advises HALT_NEW_TRADES and
            # the alert is worth interrupting someone for. Below it, every
            # anomaly says MONITOR or REDUCE_POSITION_SIZE — advisory, and the
            # engine acts on none of it.
            severe, mild = [], []
            for a in raw_alerts:
                (severe if float(a.severity) >= _HALT_SEVERITY else mild).append(a)

            # ── severe: one page per condition, immediately ──────────────
            # De-duplicated by symbol+type first. `active_alerts` holds one
            # row per DETECTION, not per condition, so the same symbol appears
            # several times in one pass and would otherwise page several times.
            # ONE CONDITION, ONE PAGE — and for a correlation breakdown the
            # condition is the HUB, not the symbol. A single asset decorrelating
            # from five others is five rows here and one market event; keying
            # those on the peer keeps the severe card prominent (never digested)
            # while still refusing to send it five times.
            groups: dict = {}
            for a in severe:
                kind = getattr(a.anomaly_type, "value", a.anomaly_type)
                peer = getattr(a, "peer", None)
                key = (f"bs_CORR_HUB_{peer}"
                       if kind == "CORRELATION_BREAKDOWN" and peer
                       else f"bs_{kind}_{a.symbol}")
                groups.setdefault(key, []).append(a)
            # A CAP, BECAUSE BREADTH IS ITS OWN FLOOD. The suppression above is
            # per-key and per-repeat: every group here is a FIRST sighting of a
            # distinct condition, so it pages immediately and correctly — and
            # six of them in one tick is still six messages thirty seconds
            # apart. Observed live on 2026-08-21.
            #
            # Ordered most-severe first and truncated, NOT digested: the
            # decision above to keep a severe card prominent is right, and the
            # loudest conditions are the ones worth a card each. What is
            # dropped is stated rather than silently omitted — a bounded list
            # published as though it were the whole one is the mistake this
            # repo keeps finding, and here it would read as "these are all the
            # anomalies", which is the opposite of true.
            shown, _more = select_severe_cards(
                groups, self._SEVERE_CARDS_PER_TICK)
            for key, group in shown:
                alert_obj = max(group, key=lambda a: float(a.severity))
                kind = getattr(alert_obj.anomaly_type, "value", alert_obj.anomaly_type)
                syms = sorted({str(a.symbol) for a in group})
                clustered = (
                    f"- Clustered: <code>{len(syms)}</code> symbols on one event"
                    f" — <code>{', '.join(syms)}</code>\n" if len(syms) > 1 else "")
                ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
                alerts.append(Alert(
                    alert_type="BLACK_SWAN",
                    severity="CRITICAL",
                    title=f"Anomaly: {kind}",
                    body=(
                        # The `+` is load-bearing. Without it the adjacent
                        # literals concatenate FIRST and `* 16` repeats the
                        # header sixteen times \u2014 rendered, caught, pinned.
                        "\U0001f6a8 <b>ANOMALY DETECTED</b>\n"
                        + "\u2500" * 16 + "\n"
                        f"- Type: <code>{kind}</code>\n"
                        f"- Symbol: <code>{alert_obj.symbol}</code>\n"
                        + clustered
                        + f"- Severity: \U0001f534 <code>{float(alert_obj.severity):.2f}</code>"
                        f" (advises {getattr(alert_obj, 'recommended_action', 'MONITOR')})\n"
                        f"- Detected At: <code>{ts}</code>\n\n"
                        f"<i>{alert_obj.description}</i>\n"
                        + "\u2500" * 16 + "\n"
                        # WAS: "Engine may auto-halt if severity is SEVERE."
                        # It does not, and never did — nothing reads
                        # `halt_recommended`. Telling an operator the bot may
                        # protect itself when it will not is the worst form of
                        # this defect class: they stand down waiting for an
                        # action nobody implemented.
                        "\u26a0\ufe0f This is an OBSERVATION, not an action. The engine "
                        "does NOT auto-halt on anomalies \u2014 use /halt to stop trading.\n"
                        "\U0001f449 /status — check engine state\n"
                        "\U0001f449 Say \"positions\" to review exposure"
                    ),
                    dedup_key=key,
                ))

            if _more:
                alerts.append(Alert(
                    alert_type="BLACK_SWAN",
                    severity="CRITICAL",
                    title="Anomaly: more severe conditions this pass",
                    body=(
                        "\U0001f6a8 <b>+" + str(len(_more)) + " more severe "
                        "anomal" + ("y" if len(_more) == 1 else "ies")
                        + " this pass</b>\n"
                        + "\u2500" * 16 + "\n"
                        "<i>The " + str(self._SEVERE_CARDS_PER_TICK) + " loudest "
                        "are carded above. Also flagged: <code>"
                        + ", ".join(_more[:12]) + "</code>"
                        + ("\u2026" if len(_more) > 12 else "") + "</i>\n"
                        + "\u2500" * 16 + "\n"
                        "\u26a0\ufe0f Still an OBSERVATION. The engine does NOT "
                        "auto-halt \u2014 use /halt to stop trading."
                    ),
                    dedup_key="bs_overflow",
                ))

            # ── everything else: ONE digest, not one message per type ────
            #
            # WHAT WAS HERE BEFORE, AND WHY IT FAILED IN PRODUCTION. Mild
            # anomalies were clustered per type and keyed
            # `bs_CLUSTER_{type}_{count}_{names}` — deliberately, so a new
            # symbol joining an event would re-page. During a market-wide
            # liquidity event the membership changes on EVERY 30-second pass,
            # so the key changed every pass, every cluster read as a first
            # sighting, and BLACK_SWAN_REPEAT never applied to anything.
            # Observed live: `PRICE_ACCELERATION x 31` at 16:27:47 and
            # `x 32` at 16:28:20, thirty-three seconds apart, plus a separate
            # SPREAD_WIDENING cluster and two severe singles in the same burst.
            #
            # A suppression key must be stable across exactly the churn the
            # event produces, or it suppresses nothing. Keyed on nothing but
            # the digest now: one message, then quiet for BLACK_SWAN_REPEAT,
            # with escalation and every severe alert still breaking through.
            if mild:
                alerts.append(self._anomaly_digest(mild))
        except Exception as exc:
            logger.debug("_check_black_swan error: %s", exc)
        return [a for a in alerts if self._bs_is_news(a)]

    @staticmethod
    def _anomaly_digest(mild: list) -> Alert:
        """One advisory message for every non-severe anomaly in this pass.

        Per TYPE it reports DISTINCT SYMBOLS, not detector rows. The old
        cluster printed `x 31` from a list holding `OPEN/USDT:USDT` eight
        times and `INJ/USDT` five — a count of detections presented as a count
        of symbols, which overstates the breadth of the event on the one line
        an operator actually reads.
        """
        by_kind: dict = {}
        for a in mild:
            kind = getattr(a.anomaly_type, "value", a.anomaly_type)
            by_kind.setdefault(kind, []).append(a)

        rows, all_syms = [], set()
        top_overall = max(mild, key=lambda a: float(a.severity))
        for kind, group in sorted(by_kind.items(),
                                  key=lambda kv: -max(float(a.severity) for a in kv[1])):
            syms = sorted({str(a.symbol) for a in group})
            all_syms.update(syms)
            top = max(group, key=lambda a: float(a.severity))
            sev = float(top.severity)
            icon = "\U0001f7e0" if sev >= 0.3 else "\U0001f7e1"
            shown = ", ".join(syms[:6]) + (f" +{len(syms) - 6} more" if len(syms) > 6 else "")
            rows.append(f"- <code>{kind}</code> — {len(syms)} symbol"
                        f"{'' if len(syms) == 1 else 's'}, worst {icon} "
                        f"<code>{sev:.2f}</code> ({top.symbol})\n"
                        f"  <i>{shown}</i>")

        worst_sev = float(top_overall.severity)
        head_icon = "\U0001f7e0" if worst_sev >= 0.3 else "\U0001f7e1"
        ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
        return Alert(
            alert_type="BLACK_SWAN",
            severity="WARNING",
            title=f"Anomaly digest: {len(all_syms)} symbols",
            body=(
                f"{head_icon} \U0001f440 <b>ANOMALY DIGEST</b>\n"
                + "\u2500" * 16 + "\n"
                f"- Symbols affected: <code>{len(all_syms)}</code> across "
                f"<code>{len(by_kind)}</code> type"
                f"{'' if len(by_kind) == 1 else 's'}\n"
                f"- As of: <code>{ts}</code>\n\n"
                + "\n".join(rows) + "\n\n"
                f"<i>{top_overall.description}</i>\n"
                + "\u2500" * 16 + "\n"
                "\u26a0\ufe0f OBSERVATIONS, not actions — nothing was traded, moved or "
                "halted. Advisory anomalies are collected into one message; "
                "anything at severity 0.80+ is sent separately and immediately, "
                "so a quiet channel is not a claim that the market is calm.\n"
                "\U0001f449 /status — check engine state"
            ),
            # STABLE BY CONSTRUCTION. The membership is deliberately NOT in the
            # key: it is the thing that churns, and putting it here is what
            # defeated the previous attempt at this.
            dedup_key="bs_DIGEST",
        )


    @staticmethod
    def _severity_tier(alert: Alert) -> int:
        """The three tiers the message itself already shows, as a number.

        Tiers rather than the raw float on purpose: a severity drifting
        0.31 -> 0.32 is not news, and keying suppression on the exact value
        would re-page on every jitter, which is the behaviour being fixed.
        """
        body = alert.body or ""
        if alert.severity == "CRITICAL":
            return 2
        return 1 if "\U0001f7e0" in body else 0

    def _bs_is_news(self, alert: Alert) -> bool:
        """Is this anomaly alert saying something the operator does not know?

        Returns True the first time a condition appears, whenever it escalates
        a tier, and once per repeat window while it persists unchanged —
        BLACK_SWAN_SEVERE_REPEAT at tier 2, BLACK_SWAN_REPEAT below it.

        THE WINDOW IS ONLY HALF OF THIS. The other half is that the dedup_key
        must stay the same while the condition does. A key carrying the
        membership of a cluster changes on every pass of a live market-wide
        event, so every pass reads as a first sighting and the window never
        applies — which is exactly what shipped and had to be corrected. This
        function cannot detect that: to it, a churning key is a stream of
        genuinely new conditions. Whoever mints the key owns the suppression.

        FAIL-OPEN, AND THE FIRST DRAFT ONLY SAID SO.

        It read `self._bs_last` directly. `test_anomaly_credibility` builds a
        monitor with `ProactiveMonitor.__new__(...)` — a deliberate, legitimate
        way to exercise `_check_black_swan` without an engine — so the attribute
        did not exist and this raised AttributeError.

        The raise is the serious part, not the test. This is called on
        `_check_black_swan`'s RETURN line, outside that method's try/except, so
        the error propagates into `_check_all()`, whose caller in the monitor
        loop swallows exceptions at debug level. A crash in the NOISE FILTER
        would therefore have silently taken out EVERY alert — halts, gateway
        outages, black swans — leaving a channel that looks calm because
        nothing can reach it. A suppression bug that suppresses everything is
        indistinguishable from a quiet market.

        So it is fail-open by construction now: unknown state is created on
        demand, and any unforeseen error sends the alert rather than eating it.
        The operator cannot see what they were not told.
        """
        try:
            if alert.alert_type != "BLACK_SWAN" or not alert.dedup_key:
                return True
            tier = self._severity_tier(alert)
            # Severe waits half as long as mild, and never waits on a first
            # sighting or on an escalation into this tier — see
            # BLACK_SWAN_SEVERE_REPEAT for why "never waits at all" was wrong.
            window = (self.BLACK_SWAN_SEVERE_REPEAT if tier >= 2
                      else self.BLACK_SWAN_REPEAT)
            seen = getattr(self, "_bs_last", None)
            if seen is None:
                seen = {}
                self._bs_last = seen
            prev = seen.get(alert.dedup_key)
            now = time.time()
            if prev is None or tier > prev[1] or (now - prev[0]) >= window:
                seen[alert.dedup_key] = (now, tier)
                return True
            return False
        except Exception as exc:
            logger.debug("anomaly repeat filter failed open: %s", exc)
            return True

    def _check_state_changes(self) -> list[Alert]:
        """Alert on significant FSM state changes."""
        alerts = []
        current_state = self.engine.state.value if hasattr(self.engine.state, 'value') else str(self.engine.state)

        if current_state != self._last_state:
            # Only alert on interesting transitions
            if current_state == "HALTED" and self._last_state != "HALTED":
                ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="CRITICAL",
                    title="Engine HALTED",
                    body=(
                        "\u26d4 <b>ENGINE HALTED</b>\n"
                        "────────────────\n"
                        f"- Previous State: <code>{self._last_state or 'UNKNOWN'}</code>\n"
                        f"- Halted At: <code>{ts}</code>\n\n"
                        "No new scans or analyses will run.\n"
                        "All automated trading is paused.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine details\n"
                        "\U0001f449 /health — check system vitals\n"
                        "\U0001f449 /reset — resume after review"
                    ),
                    dedup_key="state_halted",
                ))
            elif current_state == "COOLING_DOWN" and self._last_state != "COOLING_DOWN":
                cooldown_sec = CONFIG.risk.cooldown_after_loss_seconds
                cooldown_min = cooldown_sec / 60
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="WARNING",
                    title="Cooling Down",
                    body=(
                        f"\u23f8 <b>COOLDOWN ACTIVE</b>\n"
                        "────────────────\n"
                        f"- Duration: <code>{cooldown_min:.0f} min</code> ({cooldown_sec}s)\n"
                        f"- Previous State: <code>{self._last_state or 'UNKNOWN'}</code>\n\n"
                        "Post-loss cooldown period activated.\n"
                        "The engine will resume scanning automatically.\n"
                        "────────────────\n"
                        "\U0001f449 /status — check countdown\n"
                        "\U0001f449 /positions — review open trades"
                    ),
                    dedup_key="state_cooldown",
                ))

            self._last_state = current_state
        return alerts

    def _check_trade_signals(self) -> list[Alert]:
        """Alert when a new trade idea is generated and pending confirmation.

        Only higher-conviction ideas ping Telegram: confidence must clear
        ``risk.signal_display_min_confidence`` (default 0.70). Lower-conviction
        ideas (0.60-0.70) still queue and trade normally — they just don't
        message the operator, cutting notification noise. (Auto-execution is a
        separate, stricter gate: ``auto_confirm_threshold``, default 0.85.)
        """
        alerts = []
        try:
            min_alert_conf = CONFIG.risk.signal_display_min_confidence
            for idea_id, idea in list(self.engine._pending_ideas.items()):
                key = f"signal_{idea_id}"
                if key in self._alerted_signals:
                    continue
                # Mark seen once so a sub-threshold idea isn't re-evaluated each tick.
                self._alerted_signals.add(key)
                # Only higher-conviction ideas message the operator; lower ones
                # (0.60-0.70) still queue and trade, they just don't ping Telegram.
                if float(getattr(idea, "confidence", 0.0) or 0.0) >= min_alert_conf:
                    d = "\U0001f7e2 LONG" if idea.direction.value == "LONG" else "\U0001f534 SHORT"
                    risk_amt = abs(idea.entry_price - idea.stop_loss)
                    reward_amt = abs(idea.take_profit - idea.entry_price)
                    rr_ratio = reward_amt / risk_amt if risk_amt > 0 else 0
                    base = idea.asset.split('/')[0] if '/' in idea.asset else idea.asset
                    alerts.append(Alert(
                        alert_type="TRADE_SIGNAL",
                        severity="INFO",
                        title=f"Signal: {idea.asset}",
                        body=(
                            f"\U0001f514 <b>NEW SIGNAL — {idea.asset}</b>\n"
                            "────────────────\n"
                            f"- Direction: {d}\n"
                            f"- Confidence: <code>{idea.confidence:.0%}</code>\n"
                            f"- Entry: <code>${idea.entry_price:,.2f}</code>\n"
                            f"- Stop Loss: <code>${idea.stop_loss:,.2f}</code>\n"
                            f"- Take Profit: <code>${idea.take_profit:,.2f}</code>\n"
                            f"- R:R Ratio: <code>{rr_ratio:.1f}</code>\n"
                            "────────────────\n"
                            "\u23f3 Awaiting operator confirmation.\n"
                            f"\U0001f449 Say \"analyze {base}\" to review analysis\n"
                            f"\U0001f449 Say \"confirm\" to approve this trade"
                        ),
                        dedup_key=key,
                        idea=idea,
                    ))
        except Exception as exc:
            logger.debug("_check_trade_signals error: %s", exc)
        return alerts

    def _check_sl_tp_proximity(self) -> list[Alert]:
        """Alert when open positions approach their SL or TP levels."""
        alerts = []
        proximity_threshold = 0.015  # 1.5%
        try:
            # Collect positions from all user portfolios and the shared portfolio
            all_positions = []
            if self.engine.user_portfolios.all_portfolios():
                for uid in self.engine.user_portfolios.all_portfolios():
                    portfolio = self.engine.user_portfolios.get(uid)
                    all_positions.extend(portfolio.open_positions)
            else:
                all_positions.extend(self.engine.portfolio.open_positions)

            if not all_positions:
                return alerts

            # Get current prices from WS feed. Apply the same staleness bound the
            # SL/TP monitor uses so a silently-stalled feed can't drive a
            # proactive alert off a frozen price (0 = no filter).
            ws_prices = {}
            if self.engine.ws_feed.is_connected():
                ws_prices = self.engine.ws_feed.get_prices(
                    max_age_sec=getattr(CONFIG.execution, "ws_max_tick_age_sec", 0)) or {}

            for pos in all_positions:
                current_price = ws_prices.get(pos.asset)
                if not current_price or current_price <= 0:
                    continue
                if not pos.stop_loss or not pos.take_profit or pos.entry_price <= 0:
                    continue

                # Check SL proximity
                sl_distance_pct = abs(current_price - pos.stop_loss) / current_price
                if sl_distance_pct <= proximity_threshold:
                    key = f"sl_prox_{pos.asset}_{pos.trade_id}"
                    base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset
                    alerts.append(Alert(
                        alert_type="SL_PROXIMITY",
                        severity="WARNING",
                        title=f"SL Proximity: {pos.asset}",
                        body=(
                            f"\u26a0\ufe0f <b>STOP LOSS APPROACHING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Current Price: <code>${current_price:,.4f}</code>\n"
                            f"- Stop Loss: <code>${pos.stop_loss:,.4f}</code>\n"
                            f"- Distance: <code>{sl_distance_pct:.2%}</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            "────────────────\n"
                            f"\U0001f449 /positions — review open trades\n"
                            f"\U0001f449 Say \"analyze {base}\" for updated analysis"
                        ),
                        dedup_key=key,
                    ))

                # Check TP proximity
                tp_distance_pct = abs(current_price - pos.take_profit) / current_price
                if tp_distance_pct <= proximity_threshold:
                    key = f"tp_prox_{pos.asset}_{pos.trade_id}"
                    base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset
                    alerts.append(Alert(
                        alert_type="TP_PROXIMITY",
                        severity="INFO",
                        title=f"TP Proximity: {pos.asset}",
                        body=(
                            f"\U0001f3af <b>TAKE PROFIT APPROACHING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Current Price: <code>${current_price:,.4f}</code>\n"
                            f"- Take Profit: <code>${pos.take_profit:,.4f}</code>\n"
                            f"- Distance: <code>{tp_distance_pct:.2%}</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            "────────────────\n"
                            f"\U0001f449 /positions — review open trades\n"
                            f"\U0001f449 Say \"analyze {base}\" for updated analysis"
                        ),
                        dedup_key=key,
                    ))
        except Exception as exc:
            logger.debug("_check_sl_tp_proximity error: %s", exc)
        return alerts

    # ── Deduplication ─────────────────────────────────────────────

    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent (dedup + has enabled chats)."""
        if not self._enabled_chats:
            return False
        if alert.dedup_key:
            # Roadmap P0-1: the sentinel must be None, not 0. time.monotonic()'s
            # epoch is arbitrary (process uptime, often < DEDUP_COOLDOWN seconds),
            # so a 0 sentinel made `monotonic() - 0 < COOLDOWN` true on a fresh
            # process — silently suppressing the FIRST alert for any key during
            # the first ~5 minutes of uptime, exactly when a freshly-deployed bot
            # is most fragile (circuit-breaker trips, SL proximity).
            last_sent = self._dedup_cache.get(alert.dedup_key)
            if last_sent is not None and time.monotonic() - last_sent < self.DEDUP_COOLDOWN:
                return False
        return True

    def _mark_sent(self, alert: Alert) -> None:
        """Record that alert was sent for dedup tracking."""
        if alert.dedup_key:
            self._dedup_cache[alert.dedup_key] = time.monotonic()

        # Prune old dedup entries (keep last 200)
        if len(self._dedup_cache) > 200:
            sorted_keys = sorted(self._dedup_cache, key=self._dedup_cache.get)
            for k in sorted_keys[:100]:
                del self._dedup_cache[k]

        # Prune alerted signals set
        if len(self._alerted_signals) > 500:
            # Evict oldest half instead of clearing all
            to_remove = list(self._alerted_signals)[:250]
            self._alerted_signals -= set(to_remove)

    # ── Dispatch ──────────────────────────────────────────────────

    async def _dispatch(self, alert: Alert, send_fn) -> None:
        """Send alert to all enabled chats."""
        icon = _SEVERITY_ICON.get(alert.severity, "\u2139\ufe0f")
        full_msg = f"{icon} {alert.body}"

        # Public mind-stream: title + type only \u2014 alert BODIES can carry
        # operator-account detail (drawdown amounts, idle-cash balances) that
        # must not reach the public feed.
        try:
            from bot.core.agent_feed import FEED
            _sev = {"INFO": "info", "WARNING": "warning",
                    "CRITICAL": "critical"}.get(alert.severity, "info")
            FEED.emit("alert", alert.title, severity=_sev,
                      data={"type": alert.alert_type})
        except Exception as _feed_exc:
            logger.debug("Agent feed alert event skipped: %s", _feed_exc)

        async def _send_to_chat(chat_id: str) -> None:
            try:
                if alert.buttons:
                    # 3-arg form only when needed, so existing 2-arg send_fns
                    # (tests, custom integrations) keep working untouched.
                    await send_fn(chat_id, full_msg, alert.buttons)
                else:
                    await send_fn(chat_id, full_msg)
                if alert.idea is not None and self._chart_fn is not None:
                    try:
                        await self._chart_fn(chat_id, alert.idea)
                    except Exception as cexc:  # noqa: BLE001 — charts are best-effort
                        logger.warning("proactive chart send failed: %s", cexc, exc_info=True)
                audit(system_log,
                      f"Proactive alert sent: {alert.alert_type}",
                      action="proactive_alert",
                      data={"type": alert.alert_type, "chat_id": chat_id,
                            "severity": alert.severity})
            except Exception as exc:
                logger.debug("Failed to send alert to %s: %s", chat_id, exc)

        await asyncio.gather(*[_send_to_chat(cid) for cid in list(self._enabled_chats)])

    # ── Time Stops (Rules 6/17) ──────────────────────────────────

    def _check_time_stops(self) -> list[Alert]:
        """Alert when positions exceed time limits without profit."""
        alerts = []
        if not CONFIG.time_stop.enabled:
            return alerts

        try:
            all_positions = []
            if self.engine.user_portfolios.all_portfolios():
                for uid in self.engine.user_portfolios.all_portfolios():
                    portfolio = self.engine.user_portfolios.get(uid)
                    all_positions.extend(portfolio.open_positions)
            else:
                all_positions.extend(self.engine.portfolio.open_positions)

            if not all_positions:
                return alerts

            now = datetime.now(UTC)
            cfg = CONFIG.time_stop

            # Get current prices (staleness-bounded, as in the SL/TP monitor) so
            # a frozen WS price can't trigger a time-stop/SL-proximity alert.
            ws_prices = {}
            if self.engine.ws_feed.is_connected():
                ws_prices = self.engine.ws_feed.get_prices(
                    max_age_sec=getattr(CONFIG.execution, "ws_max_tick_age_sec", 0)) or {}

            for pos in all_positions:
                opened_at = getattr(pos, 'opened_at', None)
                if not opened_at:
                    continue

                # Calculate age in hours
                age_hours = (now - opened_at).total_seconds() / 3600.0

                # Determine trade type from SL distance: tight SL = intraday, wide = swing
                # Heuristic: if SL distance < 2% = intraday, else swing
                sl_pct = abs(pos.entry_price - pos.stop_loss) / pos.entry_price if pos.entry_price > 0 and pos.stop_loss > 0 else 0
                is_intraday = sl_pct < 0.02
                warn_hours = cfg.intraday_warn_hours if is_intraday else cfg.swing_warn_hours
                close_hours = cfg.intraday_close_hours if is_intraday else cfg.swing_close_hours
                trade_type = "intraday" if is_intraday else "swing"

                # Check if position is in profit
                current_price = ws_prices.get(pos.asset) or 0
                if current_price <= 0:
                    continue
                if pos.direction.value == "LONG":
                    in_profit = current_price > pos.entry_price
                else:
                    in_profit = current_price < pos.entry_price

                if in_profit:
                    continue  # Time stops only apply to positions NOT in profit

                base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset

                # Force close check
                if age_hours >= close_hours:
                    key = f"time_close_{pos.trade_id}"
                    alerts.append(Alert(
                        alert_type="TIME_STOP_CLOSE",
                        severity="CRITICAL",
                        title=f"Time Stop: {pos.asset}",
                        body=(
                            f"\u23f0 <b>TIME STOP — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Type: <code>{trade_type}</code>\n"
                            f"- Open: <code>{age_hours:.1f}h</code> (limit: {close_hours:.0f}h)\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            f"- Current: <code>${current_price:,.4f}</code>\n"
                            f"- Status: <b>NOT in profit — AUTO-CLOSE recommended</b>\n"
                            "────────────────\n"
                            f"\U0001f449 /close {base} — close position manually\n"
                            "\U0001f449 /positions — review all open trades"
                        ),
                        dedup_key=key,
                    ))
                # Warning check
                elif age_hours >= warn_hours:
                    key = f"time_warn_{pos.trade_id}"
                    remaining = close_hours - age_hours
                    alerts.append(Alert(
                        alert_type="TIME_STOP_WARN",
                        severity="WARNING",
                        title=f"Time Warning: {pos.asset}",
                        body=(
                            f"\u23f3 <b>TIME WARNING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Type: <code>{trade_type}</code>\n"
                            f"- Open: <code>{age_hours:.1f}h</code>\n"
                            f"- Auto-close in: <code>{remaining:.1f}h</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            f"- Current: <code>${current_price:,.4f}</code>\n"
                            f"- Status: NOT in profit\n"
                            "────────────────\n"
                            f"Position will be flagged for close at {close_hours:.0f}h if not profitable."
                        ),
                        dedup_key=key,
                    ))
        except Exception as exc:
            logger.debug("_check_time_stops error: %s", exc)
        return alerts
