"""
System Health Monitor for RUNECLAW.

Tracks API latency, error rates, exchange connectivity, and uptime.
Provides a health snapshot for risk engine and Telegram status display.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# An em dash for a figure nobody measured. `f"{None:.0f}"` raises, and the
# nearest `except` would have swallowed the whole card -- so being honest
# upstream would have deleted the display rather than corrected it.
_DASH = "\u2014"


def _ms(v: Optional[float]) -> str:
    return _DASH if v is None else f"{v:.0f}ms"


def _rate(v: Optional[float]) -> str:
    return _DASH if v is None else f"{v:.1f}%"


@dataclass
class HealthSnapshot:
    """Point-in-time system health report."""
    uptime_seconds: float = 0.0
    # RC-2026-014. These were `0.0`, and every status threshold below is a
    # comparison against them, so a monitor nothing had ever reported to
    # graded itself HEALTHY off three fabricated zeros. None means NO SAMPLE:
    # `0.0` is a real reading (calls were made and none failed) and the two
    # must not share a value.
    api_latency_ms: Optional[float] = None       # rolling average; None = no samples
    api_latency_p99_ms: Optional[float] = None   # 99th percentile
    error_rate_pct: Optional[float] = None       # errors/calls in window; None = no calls
    total_api_calls: int = 0
    total_errors: int = 0
    last_successful_scan: Optional[str] = None
    last_error: Optional[str] = None
    # Was `True` -- an initialiser value no caller ever wrote, published as a
    # connectivity check that had never run.
    exchange_connected: Optional[bool] = None
    ws_connected: bool = False
    # UNKNOWN is not a passing grade. It is the correct answer before anything
    # has reported, and it is this monitor's steady state until something does.
    status: str = "UNKNOWN"              # UNKNOWN | HEALTHY | DEGRADED | CRITICAL
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SystemHealthMonitor:
    """Tracks API performance and system health metrics.

    Thread-safe. Call record_api_call() after each exchange/LLM API call.
    Call snapshot() to get current health status.
    """

    def __init__(self, window_seconds: float = 300.0, max_samples: int = 500):
        self._start_time = time.monotonic()
        self._lock = threading.Lock()
        self._window = window_seconds

        # Rolling latency samples: (timestamp, latency_ms, success)
        self._samples: deque = deque(maxlen=max_samples)
        self._total_calls = 0
        self._total_errors = 0
        self._last_success_time: Optional[str] = None
        self._last_error_msg: Optional[str] = None
        # None = nobody has reported either way. `set_exchange_status` has no
        # caller in the tree, so this stays None in practice -- which is the
        # fact the snapshot now publishes instead of hiding.
        self._exchange_ok: Optional[bool] = None
        self._ws_ok = False

    def record_api_call(
        self, latency_ms: float, success: bool = True, error_msg: str = ""
    ) -> None:
        """Record an API call result."""
        with self._lock:
            now = time.monotonic()
            self._samples.append((now, latency_ms, success))
            self._total_calls += 1
            if success:
                self._last_success_time = datetime.now(timezone.utc).isoformat()
            else:
                self._total_errors += 1
                self._last_error_msg = error_msg or "Unknown error"

    def record_scan(self) -> None:
        """Record a successful market scan completion."""
        with self._lock:
            self._last_success_time = datetime.now(timezone.utc).isoformat()

    def set_exchange_status(self, connected: bool) -> None:
        with self._lock:
            self._exchange_ok = connected

    def set_ws_status(self, connected: bool) -> None:
        with self._lock:
            self._ws_ok = connected

    def snapshot(self) -> HealthSnapshot:
        """Get current health status."""
        with self._lock:
            now = time.monotonic()
            uptime = now - self._start_time
            cutoff = now - self._window

            # Filter to recent samples
            recent = [(t, lat, ok) for t, lat, ok in self._samples if t > cutoff]

            if recent:
                latencies = [lat for _, lat, _ in recent]
                avg_lat = sum(latencies) / len(latencies)
                sorted_lat = sorted(latencies)
                p99_idx = min(int(len(sorted_lat) * 0.99), len(sorted_lat) - 1)
                p99_lat = sorted_lat[p99_idx]
                errors = sum(1 for _, _, ok in recent if not ok)
                err_rate = (errors / len(recent)) * 100
            else:
                avg_lat = None
                p99_lat = None
                err_rate = None

            # Determine status. Four outcomes, not three -- "nothing has
            # reported" is not a grade, and it used to be spelled HEALTHY.
            if self._exchange_ok is False:
                # Reported down. This is a measurement, and it is the bad one.
                status = "CRITICAL"
            elif err_rate is None and self._exchange_ok is None:
                # No samples and no connectivity report: nothing to grade.
                status = "UNKNOWN"
            elif err_rate is not None and err_rate > 50:
                status = "CRITICAL"
            elif (err_rate is not None and err_rate > 10) or \
                 (avg_lat is not None and avg_lat > 5000):
                status = "DEGRADED"
            else:
                status = "HEALTHY"

            return HealthSnapshot(
                uptime_seconds=round(uptime, 1),
                api_latency_ms=None if avg_lat is None else round(avg_lat, 1),
                api_latency_p99_ms=None if p99_lat is None else round(p99_lat, 1),
                error_rate_pct=None if err_rate is None else round(err_rate, 2),
                total_api_calls=self._total_calls,
                total_errors=self._total_errors,
                last_successful_scan=self._last_success_time,
                last_error=self._last_error_msg,
                exchange_connected=self._exchange_ok,
                ws_connected=self._ws_ok,
                status=status,
            )

    def format_telegram(self) -> str:
        """Format health snapshot for Telegram display."""
        s = self.snapshot()
        uptime_h = s.uptime_seconds / 3600
        uptime_d = int(uptime_h // 24)
        uptime_rem_h = uptime_h % 24

        status_icon = {
            "HEALTHY": "\u2705",
            "DEGRADED": "\u26a0\ufe0f",
            "CRITICAL": "\U0001f6a8",
            "UNKNOWN": "\u2b1c",
        }.get(s.status, "\u2753")

        # Three states. `if s.exchange_connected` collapsed None onto the
        # DISCONNECTED branch, which is the opposite error but still a claim:
        # nobody has checked, so neither red nor green is honest.
        if s.exchange_connected is None:
            exchange_icon, exchange_str = "\u26aa", "not reported"
        elif s.exchange_connected:
            exchange_icon, exchange_str = "\U0001f7e2", "Connected"
        else:
            exchange_icon, exchange_str = "\U0001f534", "DISCONNECTED"
        ws_icon = "\U0001f7e2" if s.ws_connected else "\u26aa"
        ws_str = "Connected" if s.ws_connected else "Disconnected"

        if uptime_d > 0:
            uptime_str = f"{uptime_d}d {uptime_rem_h:.1f}h"
        else:
            uptime_str = f"{uptime_h:.1f}h"

        lines = [
            f"{status_icon} <b>SYSTEM HEALTH: {s.status}</b>",
            "────────────────",
            f"- Uptime: <code>{uptime_str}</code>",
            f"- API Latency: <code>{_ms(s.api_latency_ms)}</code>"
            f" (p99: <code>{_ms(s.api_latency_p99_ms)}</code>)",
            f"- Error Rate: <code>{_rate(s.error_rate_pct)}</code>"
            f" ({s.total_errors}/{s.total_api_calls})",
            f"- Exchange: {exchange_icon} {exchange_str}",
            f"- WebSocket: {ws_icon} {ws_str}",
        ]
        if s.last_successful_scan:
            lines.append(f"- Last Scan: <code>{s.last_successful_scan[:19]}</code>")
        if s.last_error:
            lines.append(f"- Last Error: <code>{s.last_error[:60]}</code>")
        lines.append("────────────────")
        lines.append("\U0001f449 /status — engine overview")
        lines.append("\U0001f449 /watch on — enable proactive alerts")
        return "\n".join(lines)
